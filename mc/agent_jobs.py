"""Engine-agnostic background command jobs (MC-958 follow-up, backlog b49cf71f).

Claude Mode B self-wakes on `task_notification` (mc/background_tasks.py) because
the CLI stays alive between turns and reports on its own background jobs.
Codex/Gemini/Qwen run one process per turn (`codex exec --json` etc.) that
exits at turn end -- there is no `run_in_background` tool, and a shell `&`
job would be orphaned with nothing to wake the session. This module is the
substitute: Clayrune runs the command itself as a real subprocess it owns,
and calls back on exit so the caller can deliver a follow-up turn.

Leaf module -- no Flask import, no reach into agent_routes' private state
(session store, delivery store). Same shape as mc.workflows: the caller
(mc/blueprints/agent_routes.py) supplies the log directory, Popen flags, and
completion callback; this module only knows how to run a command and report
back on it.
"""
from __future__ import annotations

import subprocess
import threading
import time as _time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from mc.core import _log

# Matches background_tasks.DEFAULT_WAIT_MAX_MINUTES -- the existing cap for
# "how long may a spawner wait on a background job before Clayrune reports
# in anyway" (state.CONFIG['background_wait_max_minutes']).
DEFAULT_TIMEOUT_MINUTES = 120
TAIL_BYTES = 4096

_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def _tail(path: Path, n: int = TAIL_BYTES) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ''
    return data[-n:].decode('utf-8', errors='replace')


def start_job(*, project_id: str, session_id: str, command: str, cwd: str,
              timeout_minutes: float, log_dir: Path,
              popen_flags: int = 0, startupinfo: Any = None,
              kill_fn: Optional[Callable[[subprocess.Popen], Any]] = None,
              on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
              popen: Callable[..., subprocess.Popen] = subprocess.Popen,
              ) -> Dict[str, Any]:
    """Start `command` as a subprocess, log its output, watch for exit.

    Returns immediately with the job record (includes `job_id`, `pid`,
    `proc` -- the live Popen, for the caller to register with the process
    tracker). `on_complete(result)` fires from a background thread once the
    process exits or the timeout kills it; `result` is a plain dict (job_id,
    project_id, session_id, pid, command, cwd, exit_code, timed_out,
    duration_s, log_path, tail) so the callback never needs to reach back
    into this module's own state.
    """
    job_id = uuid.uuid4().hex
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f'{job_id}.log'
    log_file = open(log_path, 'wb')
    started = _time.time()
    try:
        # stdin=DEVNULL, not inherited: a background job must never block
        # waiting on interactive input, and on Windows inheriting the
        # parent's stdin handle raises WinError 6 whenever that handle isn't
        # itself valid for inheritance (headless/service-style parents hit
        # this even outside tests).
        proc = popen(command, shell=True, cwd=cwd or None, stdin=subprocess.DEVNULL,
                     stdout=log_file, stderr=subprocess.STDOUT,
                     creationflags=popen_flags, startupinfo=startupinfo)
    except Exception:
        log_file.close()
        raise
    job: Dict[str, Any] = {
        'job_id': job_id, 'project_id': project_id, 'session_id': session_id,
        'command': command, 'cwd': cwd, 'pid': proc.pid, 'proc': proc,
        'log_path': str(log_path), 'status': 'running', 'started_at': started,
        'timeout_minutes': timeout_minutes, 'exit_code': None,
        'timed_out': False, 'finished_at': None,
    }
    with _lock:
        _jobs[job_id] = job

    def _watch():
        timeout_s = max(0.0, float(timeout_minutes) * 60)
        timed_out = False
        try:
            proc.wait(timeout=timeout_s if timeout_s > 0 else None)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                if kill_fn is not None:
                    kill_fn(proc)
                else:
                    proc.kill()
            except Exception as e:
                _log(f"[agent-jobs] kill failed for job {job_id[:8]} (pid {proc.pid}): {e}")
            try:
                proc.wait(timeout=10)
            except Exception as e:
                _log(f"[agent-jobs] job {job_id[:8]} did not exit after kill: {e}")
        finally:
            try:
                log_file.close()
            except Exception as e:
                _log(f"[agent-jobs] log close failed for job {job_id[:8]}: {e}")
        finished = _time.time()
        exit_code = proc.poll()
        with _lock:
            j = _jobs.get(job_id)
            if j is not None:
                j['status'] = 'timeout' if timed_out else 'completed'
                j['exit_code'] = exit_code
                j['timed_out'] = timed_out
                j['finished_at'] = finished
        result = {
            'job_id': job_id, 'project_id': project_id, 'session_id': session_id,
            'pid': proc.pid, 'command': command, 'cwd': cwd,
            'exit_code': exit_code, 'timed_out': timed_out,
            'duration_s': finished - started, 'log_path': str(log_path),
            'tail': _tail(log_path),
        }
        if on_complete is not None:
            try:
                on_complete(result)
            except Exception as e:
                _log(f"[agent-jobs] on_complete failed for job {job_id[:8]}: {e}")

    threading.Thread(target=_watch, daemon=True, name=f'agent-job-{job_id[:8]}').start()
    return job


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job is not None else None


def public_job(job_id: str) -> Optional[Dict[str, Any]]:
    """`get_job` minus the live Popen handle -- safe to JSON-serialize."""
    job = get_job(job_id)
    if job is None:
        return None
    job.pop('proc', None)
    return job
