"""The generated guard command must block under EVERY shell a vendor CLI may
run hooks through — not just the one the author's prompt happened to use.

Qwen live pass run 3 (2026-09-19, docs/_journal/provider-live/qwen/
guardrail.md): a Qwen agent executed `taskkill /IM <decoy> /F` and the decoy
died. qwen-code 0.23.4 runs hooks via `bash -c` whenever MSYSTEM is MINGW*/
MSYS* (a Clayrune started from git-bash), bash ate the backslashes in
`C:\\...\\python.exe`, the hook exited 127, and qwen maps every exit other
than 0/2 to "allow". These tests spawn the command exactly as qwen does
(`<shell> <prefix> <command>`, no extra quoting) and require exit code 2.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mc import guardrail_hooks as gh

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows shell parsing')

PAYLOAD = json.dumps({'tool_name': 'run_shell_command',
                      'tool_input': {'command': 'taskkill /IM clayrune_no_such_image.exe /F'}})


def _git_bash():
    for base in (os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)')):
        if base:
            p = Path(base) / 'Git' / 'bin' / 'bash.exe'
            if p.is_file():
                return str(p)
    return None


def _run(argv):
    return subprocess.run(argv, input=PAYLOAD, capture_output=True, text=True, timeout=60)


def test_guard_command_has_no_backslash_on_windows():
    cmd = gh.guard_shell_command(python_exe=sys.executable)
    assert '\\' not in cmd


def test_guard_blocks_when_qwen_runs_hooks_through_git_bash():
    bash = _git_bash()
    if not bash:
        pytest.skip('git-bash not installed')
    r = _run([bash, '-c', gh.guard_shell_command(python_exe=sys.executable)])
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert 'image-name termination is blocked' in r.stderr


def test_guard_blocks_when_qwen_runs_hooks_through_cmd():
    comspec = os.environ.get('ComSpec') or 'cmd.exe'
    r = _run([comspec, '/d', '/s', '/c', gh.guard_shell_command(python_exe=sys.executable)])
    assert r.returncode == 2, (r.returncode, r.stderr)


def test_guard_blocks_when_gemini_runs_hooks_through_powershell():
    ps = shutil.which('powershell.exe') or shutil.which('pwsh.exe')
    if not ps:
        pytest.skip('no PowerShell')
    # gemini-cli 0.59.0 appends this itself so the hook's exit code survives.
    cmd = gh.guard_shell_command(python_exe=sys.executable) + \
        '; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }'
    r = _run([ps, '-NoProfile', '-NonInteractive', '-Command', cmd])
    assert r.returncode == 2, (r.returncode, r.stderr)


# ── Qwen's own shell tool: pinned to cmd.exe ────────────────────────────────
# The same MSYSTEM switch also moves qwen's run_shell_command onto git-bash,
# where MSYS path conversion turned `taskkill /PID <n> /F` into
# `taskkill 'C:/Program Files/Git/PID' ...` (live replay, 2026-09-19).

from mc import agent_runtime as art  # noqa: E402
from mc.agent_runtime import QwenRuntime, SessionHandle  # noqa: E402


def test_pin_blanks_git_bash_markers(monkeypatch):
    monkeypatch.setenv('MSYSTEM', 'MINGW64')
    monkeypatch.setenv('TERM', 'cygwin')
    env = art._pin_qwen_windows_shell({})
    assert env == {'MSYSTEM': '', 'TERM': ''}


def test_pin_leaves_a_plain_env_alone(monkeypatch):
    monkeypatch.delenv('MSYSTEM', raising=False)
    monkeypatch.setenv('TERM', 'xterm-256color')
    assert art._pin_qwen_windows_shell({}) == {}


def test_qwen_dispatch_and_followup_carry_the_pin(monkeypatch, tmp_path):
    monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('MSYSTEM', 'MINGW64')
    captured = {}

    def _fake(self, cmd, full_prompt, project_path, project_id, task, mc_sid,
              session_dict, incognito, env, callbacks, register_process, **kw):
        captured['dispatch'] = env
        return SessionHandle(mc_session_id=mc_sid, provider='qwen', mode='A',
                             project_path=project_path, project_id=project_id,
                             session_dict=session_dict if session_dict is not None else {})

    class _Stdin:
        def write(self, s): pass
        def close(self): pass

    class _Proc:
        stdin = _Stdin()
        stdout = iter([])
        pid = 1
        def wait(self): return 0
        def poll(self): return None

    def _popen(*a, **k):
        captured['followup'] = k.get('env')
        return _Proc()

    monkeypatch.setattr(art, '_mode_a_dispatch', _fake)
    monkeypatch.setattr(art.subprocess, 'Popen', _popen)
    rt = QwenRuntime()
    rt._bin_cache = 'qwen'
    rt.dispatch(project_path='/p', task='x', session_dict={})
    rt.write_followup(SessionHandle(mc_session_id='s', provider='qwen', mode='A',
                                    project_path='/p', project_id='p',
                                    session_dict={'log_lines': [], 'proc': None}), 'hi')
    assert captured['dispatch']['MSYSTEM'] == ''
    assert captured['followup']['MSYSTEM'] == ''
