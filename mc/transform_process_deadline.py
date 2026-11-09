"""Deadline-only Popen path: persistent tree ownership and bounded pipe cleanup."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Any

from mc import proc_kill
from mc import windows_process_job
from mc.core import _log

CLEANUP_SECONDS = 0.5


def _stop_owned(proc: subprocess.Popen, *, job: windows_process_job.OwnedJob | None = None,
                pgid: int | None = None, reader: threading.Thread | None = None) -> None:
    # Kernel-container ownership survives root exit; never resolve its stale PID.
    if job is not None:
        try:
            job.stop()
        except OSError as exc:
            _log(f'[transform-process] job cleanup failed: {type(exc).__name__}', flush=True)
        return
    if pgid is not None and reader is not None:
        proc_kill.safe_killpg_recorded(pgid, holder_alive=reader.is_alive())
        # A finished reader no longer witnesses group ownership, but the live
        # root's Popen handle is still safe to use for reader-error cleanup.
        if not reader.is_alive() and proc.poll() is None:
            try:
                proc.kill()
            except OSError as exc:
                _log(f'[transform-process] root cleanup failed: {type(exc).__name__}', flush=True)
        return
    # Never pass a reaped/exited root to taskkill: its PID can be reused.
    if proc.poll() is not None:
        return
    if sys.platform == 'win32':
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)],
                           capture_output=True, timeout=CLEANUP_SECONDS,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as exc:
            _log(f'[transform-process] tree cleanup failed: {type(exc).__name__}', flush=True)
    else:
        proc_kill.kill_tree(proc.pid)
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError as exc:
            _log(f'[transform-process] root cleanup failed: {type(exc).__name__}', flush=True)


def run(cmd: list[str], *, input: str, timeout: float, deadline: float,
        **kwargs: Any) -> subprocess.CompletedProcess:
    """A job/group remains owned even if the wrapper exits before cancellation.

    communicate runs only in the reader thread. In particular, do not repeat
    communicate without a timeout on the calling thread (Windows run's trap).
    The cleanup reserve is part of the entry budget, including process startup.
    """
    limit = min(deadline, time.monotonic() + timeout)
    compute_end = limit - CLEANUP_SECONDS
    if sys.platform == 'win32':
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | windows_process_job.CREATE_SUSPENDED
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, **kwargs,
                            **proc_kill.POPEN_NEW_SESSION)
    job = None
    pgid = proc.pid if sys.platform != 'win32' else None
    if sys.platform == 'win32':
        # The root cannot execute or spawn descendants before job assignment.
        try:
            handle = windows_process_job.create_for(proc.pid, kill_on_close=True)
            if not handle:
                raise OSError('transform process ownership could not be established')
            job = windows_process_job.OwnedJob(handle)
            windows_process_job.resume_suspended(proc.pid)
        except Exception:
            try:
                windows_process_job.terminate_suspended(
                    proc, timeout=max(0, min(CLEANUP_SECONDS, limit - time.monotonic())))
            finally:
                if job is not None:
                    job.close()
                for stream in (proc.stdin, proc.stdout, proc.stderr):
                    if stream is not None:
                        stream.close()
            raise
    done = threading.Event()
    output: list[tuple[Any, Any]] = []
    errors: list[Exception] = []

    def communicate() -> None:
        try:
            output.append(proc.communicate(input))
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    reader = threading.Thread(target=communicate, name='transform-pipes', daemon=True)
    try:
        reader.start()
        if not done.wait(max(0, compute_end - time.monotonic())):
            cleanup_end = min(limit, time.monotonic() + CLEANUP_SECONDS)
            cleanup = threading.Thread(target=_stop_owned, args=(proc,),
                                       kwargs=dict(job=job, pgid=pgid, reader=reader),
                                       name='transform-cleanup', daemon=True)
            cleanup.start()
            cleanup.join(timeout=max(0, cleanup_end - time.monotonic()))
            reader.join(timeout=max(0, cleanup_end - time.monotonic()))
            if proc.poll() is None or reader.is_alive():
                _log(f'[transform-process] cleanup incomplete for owned PID {proc.pid}', flush=True)
            raise subprocess.TimeoutExpired(cmd, timeout)
        if errors:
            _stop_owned(proc, job=job, pgid=pgid, reader=reader)
            raise errors[0]
        stdout, stderr = output[0]
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    finally:
        if job is not None:
            job.close()
