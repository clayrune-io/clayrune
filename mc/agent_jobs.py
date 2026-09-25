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

import os
import shutil
import subprocess
import sys
import threading
import time as _time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from mc.core import _log

# Matches background_tasks.DEFAULT_WAIT_MAX_MINUTES -- the existing cap for
# "how long may a spawner wait on a background job before Clayrune reports
# in anyway" (state.CONFIG['background_wait_max_minutes']).
DEFAULT_TIMEOUT_MINUTES = 120
TAIL_BYTES = 4096

VALID_SHELLS = ('bash', 'sh', 'powershell', 'cmd')

_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


class ShellResolutionError(ValueError):
    """Raised for a `shell` request value outside VALID_SHELLS."""


def _tail(path: Path, n: int = TAIL_BYTES) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ''
    return data[-n:].decode('utf-8', errors='replace')


def _is_wsl_launcher(path: str, name: str) -> bool:
    """True when `path` is the WSL launcher Windows drops at
    System32\\<name>.exe -- it execs into a Linux VM, not a shell that can
    see this box's cwd or run a Windows-side command; a job invoked through
    it hangs or fails opaquely rather than running."""
    try:
        wsl_path = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / f'{name}.exe'
        return Path(path).resolve() == wsl_path.resolve()
    except OSError:
        return False


def _resolve_git_bash_root(name: str) -> Optional[str]:
    """Git for Windows ships `bash.exe`/`sh.exe` at <git root>/bin/ -- derive
    the root from `git`'s own resolved location (found via PATH) rather than
    assuming the default 'C:\\Program Files\\Git', since installs vary."""
    git_path = shutil.which('git')
    if not git_path:
        return None
    git_dir = Path(git_path).resolve().parent  # .../Git/cmd or .../Git/mingw64/bin
    for root in (git_dir.parent, git_dir.parent.parent):
        candidate = root / 'bin' / f'{name}.exe'
        if candidate.is_file():
            return str(candidate)
    return None


def resolve_shell(requested: Optional[str]) -> Dict[str, Any]:
    """Resolve a job's `shell` request field to an executable.

    Returns `{'shell': <name actually used>, 'path': <resolved executable>,
    'note': <str|None>}`. Raises `ShellResolutionError` for a value outside
    VALID_SHELLS.

    Default is 'bash' on every OS -- agents write POSIX commands
    (`sleep 90; echo done`), and the old `shell=True` on Windows ran them
    through cmd.exe, which doesn't know `sleep` (backlog b49cf71f). On
    Windows, `shutil.which('bash')` commonly resolves to the WSL launcher at
    System32\\bash.exe; that hit is rejected in favour of Git Bash. If no
    bash/sh exists anywhere on a Windows host, falls back to powershell and
    says so in `note` rather than failing the job outright.
    """
    name = (requested or 'bash').strip().lower()
    if name not in VALID_SHELLS:
        raise ShellResolutionError(
            f"unknown shell '{requested}' -- must be one of {', '.join(VALID_SHELLS)}")

    if name in ('bash', 'sh'):
        path = shutil.which(name)
        if path and sys.platform == 'win32' and _is_wsl_launcher(path, name):
            path = None
        if not path and sys.platform == 'win32':
            path = _resolve_git_bash_root(name)
        if not path:
            if sys.platform == 'win32':
                ps_path = shutil.which('powershell') or 'powershell'
                note = (f"no usable '{name}' found on this Windows host "
                        "(PATH resolved to the WSL launcher or nothing, and no "
                        "Git for Windows install was found) -- falling back to "
                        "powershell")
                return {'shell': 'powershell', 'path': ps_path, 'note': note}
            path = name  # non-Windows: trust PATH resolution at exec time
        return {'shell': name, 'path': path, 'note': None}

    if name == 'powershell':
        return {'shell': name, 'path': shutil.which('powershell') or 'powershell', 'note': None}

    # cmd
    return {'shell': name, 'path': shutil.which('cmd') or 'cmd', 'note': None}


def build_argv(shell: str, path: str, command: str) -> List[str]:
    """argv for `shell` running `command` as a single statement -- replaces
    the old `shell=True` string invocation (which on Windows is always
    cmd.exe regardless of what the command was written for)."""
    if shell in ('bash', 'sh'):
        return [path, '-c', command]
    if shell == 'powershell':
        return [path, '-NoProfile', '-Command', command]
    if shell == 'cmd':
        return [path, '/c', command]
    raise ShellResolutionError(f"unknown shell '{shell}'")


def open_job_count(session_id: str) -> int:
    """Jobs still `running` for this session -- lets the caller hold a
    turn-end callback until a job-finished wake turn actually happens
    (MC-958 follow-up, b49cf71f second bug)."""
    with _lock:
        return sum(1 for j in _jobs.values()
                   if j.get('session_id') == session_id and j.get('status') == 'running')


def describe_open_jobs(session_id: str, limit: int = 3) -> str:
    """Short human label for a session's open jobs, mirroring
    background_tasks.describe for the CLI-native facility."""
    with _lock:
        items = [j for j in _jobs.values()
                if j.get('session_id') == session_id and j.get('status') == 'running']
    parts = [(j.get('command') or j.get('job_id') or '')[:80] for j in items[:limit]]
    if len(items) > limit:
        parts.append(f'+{len(items) - limit} more')
    return '; '.join(parts)


def start_job(*, project_id: str, session_id: str, command: str, cwd: str,
              timeout_minutes: float, log_dir: Path, shell: Optional[str] = None,
              popen_flags: int = 0, startupinfo: Any = None,
              kill_fn: Optional[Callable[[subprocess.Popen], Any]] = None,
              on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
              popen: Callable[..., subprocess.Popen] = subprocess.Popen,
              ) -> Dict[str, Any]:
    """Start `command` as a subprocess, log its output, watch for exit.

    `shell` picks the interpreter (`resolve_shell` -- default 'bash' on
    every OS, argv-invoked via `build_argv` rather than the old
    `shell=True` string form, which on Windows is always cmd.exe no matter
    what the command was written for). Raises `ShellResolutionError` for an
    unrecognized `shell` value -- raised before any process is spawned.

    Returns immediately with the job record (includes `job_id`, `pid`,
    `proc` -- the live Popen, for the caller to register with the process
    tracker; `shell`/`shell_note` -- what was actually resolved and used).
    `on_complete(result)` fires from a background thread once the process
    exits or the timeout kills it; `result` is a plain dict (job_id,
    project_id, session_id, pid, command, cwd, shell, shell_note, exit_code,
    timed_out, duration_s, log_path, tail) so the callback never needs to
    reach back into this module's own state.
    """
    resolved = resolve_shell(shell)
    argv = build_argv(resolved['shell'], resolved['path'], command)

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
        proc = popen(argv, shell=False, cwd=cwd or None, stdin=subprocess.DEVNULL,
                     stdout=log_file, stderr=subprocess.STDOUT,
                     creationflags=popen_flags, startupinfo=startupinfo)
    except Exception:
        log_file.close()
        raise
    job: Dict[str, Any] = {
        'job_id': job_id, 'project_id': project_id, 'session_id': session_id,
        'command': command, 'cwd': cwd, 'pid': proc.pid, 'proc': proc,
        'shell': resolved['shell'], 'shell_note': resolved['note'],
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
            'shell': resolved['shell'], 'shell_note': resolved['note'],
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
