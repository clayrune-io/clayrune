"""Deadline-only Popen path: kill a still-owned live tree, bound pipe cleanup."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from typing import Any

from mc import proc_kill
from mc.core import _log

CLEANUP_SECONDS = 0.5


def _stop_owned(proc: subprocess.Popen) -> None:
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
    """Readers stay daemonized if an inherited pipe never closes after cleanup.

    communicate runs only in the reader thread. In particular, do not repeat
    communicate without a timeout on the calling thread (Windows run's trap).
    """
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, **kwargs,
                            **proc_kill.POPEN_NEW_SESSION)
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
    reader.start()
    limit = min(deadline, time.monotonic() + timeout)
    if not done.wait(max(0, limit - time.monotonic())):
        cleanup_end = time.monotonic() + CLEANUP_SECONDS
        cleanup = threading.Thread(target=_stop_owned, args=(proc,),
                                   name='transform-cleanup', daemon=True)
        cleanup.start()
        cleanup.join(timeout=max(0, cleanup_end - time.monotonic()))
        reader.join(timeout=max(0, cleanup_end - time.monotonic()))
        if proc.poll() is None:
            _log(f'[transform-process] cleanup incomplete for owned PID {proc.pid}', flush=True)
        raise subprocess.TimeoutExpired(cmd, timeout)
    if errors:
        _stop_owned(proc)
        raise errors[0]
    stdout, stderr = output[0]
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
