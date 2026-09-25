"""mc/agent_jobs.py -- the pure job-running engine (MC-958 follow-up,
backlog b49cf71f). No Flask, no session store: just "run this command,
watch it, report back," which is the piece Codex/Gemini/Qwen sessions have
no equivalent of on their own (see mc/agent_runtime.py's CodexRuntime
docstring -- `codex exec --json` is one process per turn, no
run_in_background tool).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import mc.agent_jobs as aj


def _wait_for(predicate, timeout=8.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _py(code: str) -> str:
    # sys.executable is portable across the Windows cmd.exe shell=True path
    # and a POSIX shell -- unlike `sleep`/`echo`, it behaves identically.
    return f'"{sys.executable}" -c "{code}"'


def test_job_runs_to_completion_and_records_result(tmp_path):
    done = threading.Event()
    captured = {}

    def _on_complete(result):
        captured.update(result)
        done.set()

    job = aj.start_job(
        project_id='p1', session_id='s1', command=_py("print('hello-job')"),
        cwd=str(tmp_path), timeout_minutes=1, log_dir=tmp_path / 'logs',
        on_complete=_on_complete)
    assert job['status'] == 'running'
    assert job['pid'] == job['proc'].pid

    assert _wait_for(done.is_set), 'on_complete never fired'
    assert captured['exit_code'] == 0
    assert captured['timed_out'] is False
    assert 'hello-job' in captured['tail']
    assert captured['job_id'] == job['job_id']

    stored = aj.get_job(job['job_id'])
    assert stored['status'] == 'completed'
    assert stored['exit_code'] == 0
    assert Path(stored['log_path']).is_file()


def test_job_timeout_kills_the_process(tmp_path):
    done = threading.Event()
    captured = {}

    def _on_complete(result):
        captured.update(result)
        done.set()

    job = aj.start_job(
        project_id='p1', session_id='s1',
        command=_py('import time; time.sleep(30)'),
        cwd=str(tmp_path), timeout_minutes=0.02,  # 1.2s
        log_dir=tmp_path / 'logs', on_complete=_on_complete)

    assert _wait_for(done.is_set, timeout=15.0), 'timeout watcher never fired'
    assert captured['timed_out'] is True
    assert aj.get_job(job['job_id'])['status'] == 'timeout'
    # The process must actually be dead, not just marked as such.
    assert job['proc'].poll() is not None


def test_job_timeout_uses_the_supplied_kill_fn(tmp_path):
    """The route wires a tree-kill (_kill_pid) instead of bare proc.kill() --
    prove the hook is actually invoked, not just accepted."""
    killed = []
    done = threading.Event()

    def _kill_fn(proc):
        killed.append(proc.pid)
        proc.kill()

    job = aj.start_job(
        project_id='p1', session_id='s1',
        command=_py('import time; time.sleep(30)'),
        cwd=str(tmp_path), timeout_minutes=0.02,
        log_dir=tmp_path / 'logs', kill_fn=_kill_fn,
        on_complete=lambda result: done.set())

    assert _wait_for(done.is_set, timeout=15.0)
    assert killed == [job['pid']]


def test_on_complete_exception_does_not_orphan_the_watcher_thread(tmp_path):
    """A broken callback must not prevent the job from being marked finished --
    exception-swallowing policy: log it, don't let it corrupt state."""
    def _boom(result):
        raise RuntimeError('caller bug')

    job = aj.start_job(
        project_id='p1', session_id='s1', command=_py("print('x')"),
        cwd=str(tmp_path), timeout_minutes=1, log_dir=tmp_path / 'logs',
        on_complete=_boom)

    assert _wait_for(lambda: aj.get_job(job['job_id'])['status'] != 'running')
    assert aj.get_job(job['job_id'])['status'] == 'completed'


def test_public_job_excludes_the_live_popen_handle(tmp_path):
    job = aj.start_job(
        project_id='p1', session_id='s1', command=_py("print('x')"),
        cwd=str(tmp_path), timeout_minutes=1, log_dir=tmp_path / 'logs')
    assert _wait_for(lambda: aj.get_job(job['job_id'])['status'] != 'running')

    full = aj.get_job(job['job_id'])
    public = aj.public_job(job['job_id'])
    assert 'proc' in full
    assert 'proc' not in public
    assert public['job_id'] == job['job_id']


def test_get_job_unknown_id_returns_none():
    assert aj.get_job('does-not-exist') is None
    assert aj.public_job('does-not-exist') is None


def test_tail_returns_last_n_bytes(tmp_path):
    p = tmp_path / 'big.log'
    p.write_bytes(b'x' * 5000 + b'END')
    tail = aj._tail(p, n=10)
    assert tail == 'xxxxxxxEND'


def test_tail_missing_file_is_empty_not_an_error(tmp_path):
    assert aj._tail(tmp_path / 'nope.log') == ''


# ── shell resolution (MC-958 follow-up, backlog b49cf71f: `shell=True` was
# always cmd.exe on Windows, which doesn't know POSIX `sleep` -- b49cf71f) ──

def test_resolve_shell_defaults_to_bash():
    resolved = aj.resolve_shell(None)
    assert resolved['shell'] == 'bash'


def test_resolve_shell_unknown_value_raises():
    with pytest.raises(aj.ShellResolutionError):
        aj.resolve_shell('fish')


@pytest.mark.skipif(sys.platform != 'win32', reason="WSL-launcher rejection is Windows-only")
def test_resolve_shell_rejects_the_wsl_launcher(monkeypatch):
    """shutil.which('bash') commonly resolves to the WSL launcher Windows
    drops at System32\\bash.exe -- it execs into a Linux VM, not a shell that
    can see this box's cwd. Force `which` to return that exact path (it's a
    real file on any Windows box with WSL installed) and prove resolution
    never hands it back, falling through to Git Bash or powershell instead."""
    wsl_path = str(Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'bash.exe')
    real_which = shutil.which
    monkeypatch.setattr(aj.shutil, 'which',
                        lambda name: wsl_path if name == 'bash' else real_which(name))
    resolved = aj.resolve_shell('bash')
    assert resolved['path'] != wsl_path
    assert resolved['shell'] in ('bash', 'powershell')


@pytest.mark.skipif(sys.platform != 'win32', reason="fallback-to-powershell is Windows-only")
def test_resolve_shell_falls_back_to_powershell_when_no_bash_anywhere(monkeypatch):
    monkeypatch.setattr(aj.shutil, 'which', lambda name: None)
    resolved = aj.resolve_shell('bash')
    assert resolved['shell'] == 'powershell'
    assert resolved['note'] is not None and 'falling back to powershell' in resolved['note']


def test_build_argv_uses_dash_c_for_bash():
    assert aj.build_argv('bash', '/bin/bash', 'echo hi') == ['/bin/bash', '-c', 'echo hi']


def test_build_argv_uses_command_flag_for_powershell():
    assert aj.build_argv('powershell', 'powershell', 'echo hi') == \
        ['powershell', '-NoProfile', '-Command', 'echo hi']


def test_default_shell_runs_a_real_posix_sleep_command(tmp_path):
    """The exact command shape from the live-test bug report (b49cf71f):
    `sleep 90; echo BG-OK` failed in 0.03s under the old shell=True (cmd.exe
    on Windows doesn't know `sleep`). Under the new default (bash, argv-
    invoked) it must actually run."""
    done = threading.Event()
    captured = {}

    def _on_complete(result):
        captured.update(result)
        done.set()

    job = aj.start_job(
        project_id='p1', session_id='s1', command='sleep 1; echo OK',
        cwd=str(tmp_path), timeout_minutes=1, log_dir=tmp_path / 'logs',
        on_complete=_on_complete)
    assert job['shell'] == 'bash'

    assert _wait_for(done.is_set, timeout=15.0), 'sleep-based job never completed'
    assert captured['exit_code'] == 0
    assert captured['timed_out'] is False
    assert 'OK' in captured['tail']
