"""MC-975 follow-up (2026-09-25): hooks in a FROZEN (PyInstaller) build.

There `sys.executable` is the Clayrune app binary and the hook scripts are
bytecode inside the bundle, so `<app> <.../steward/fence.py> --armed
--self-test` booted a second copy of the app instead of running the fence.
codex_fence_self_test() failed and every unattended Codex launch was refused.

The frozen build now calls itself with `--clayrune-hook <name>`, which
app.py hands to mc/hook_entry.py before anything else starts. These tests
simulate the frozen build with `sys.frozen = True` and `sys.executable`
pointing at a stand-in for the app binary that runs the REAL app.py with
the argv it is given, the same way the bundled executable runs app.py as
__main__. Not covered here: PyInstaller actually bundling steward.fence and
mc.process_guard (it should, the imports in mc/hook_entry.py are static),
bootloader start-up time, and macOS launching an app-bundle binary directly.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mc import guardrail_hooks as gh
from mc import hook_entry

REPO = Path(__file__).resolve().parent.parent
PUSH = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'git push origin master'}})
ECHO = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'echo hi'}})
# A payload, never executed: the guard's image-name rule on a decoy name.
KILL = json.dumps({'tool_name': 'Bash',
                   'tool_input': {'command': 'task' + 'kill /IM clayrune-decoy.exe /F'}})
PS = shutil.which('powershell.exe') if os.name == 'nt' else None
HAS_SHELL = bool(PS) or (os.name != 'nt' and bool(shutil.which('sh')))
needs_shell = pytest.mark.skipif(not HAS_SHELL, reason='no hook shell on this box')


def _clean_env():
    return {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_CODE')}


def _fake_app(tmp_path):
    """A stand-in for the frozen app binary: runs app.py with its argv."""
    py, app = sys.executable, str(REPO / 'app.py')
    if os.name == 'nt':
        p = tmp_path / 'Clayrune.cmd'
        p.write_text(f'@"{py}" "{app}" %*\r\n', encoding='utf-8')
    else:
        p = tmp_path / 'Clayrune'
        p.write_text(f'#!/bin/sh\nexec "{py}" "{app}" "$@"\n', encoding='utf-8')
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return str(p)


@pytest.fixture
def frozen(monkeypatch, tmp_path):
    app = _fake_app(tmp_path)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', app)
    return app


def _run(command, payload):
    argv = gh._codex_hook_shell_argv(command)
    return subprocess.run(argv, input=payload, capture_output=True, text=True,
                          env=_clean_env(), timeout=120)


def test_frozen_commands_call_the_app_entry_not_a_script(frozen):
    fence_cmd = gh.codex_fence_hook_command(armed=True)
    guard_cmd = gh.guard_shell_command()
    app = gh._shell_neutral_path(frozen)
    assert fence_cmd.startswith(f'{app} --clayrune-hook fence --armed')
    assert guard_cmd.startswith(f'{app} --clayrune-hook process-guard')
    assert 'fence.py' not in fence_cmd and 'process_guard.py' not in guard_cmd


def test_explicit_script_or_python_keeps_the_source_form(frozen):
    """install_hooks.py and the tests pass real paths; those mean python+script."""
    cmd = gh.codex_fence_hook_command(armed=True, python_exe='py3')
    assert cmd.startswith('py3 ') and 'fence.py' in cmd and '--clayrune-hook' not in cmd
    cmd = gh.guard_shell_command(guard_script=Path('/x/process_guard.py'))
    assert 'process_guard.py' in cmd and '--clayrune-hook' not in cmd


def test_source_install_unchanged():
    assert not getattr(sys, 'frozen', False)
    assert '--clayrune-hook' not in gh.codex_fence_hook_command(armed=True)
    assert '--clayrune-hook' not in gh.guard_shell_command()


@needs_shell
def test_frozen_self_test_passes_through_the_app_entry(frozen):
    ok, detail = gh.codex_fence_self_test()
    assert ok, detail


@needs_shell
def test_frozen_armed_fence_blocks_and_allows(frozen):
    cmd = gh.codex_fence_hook_command(armed=True)
    r = _run(cmd, PUSH)
    assert r.returncode == 2 and 'STEWARD FENCE blocked' in r.stderr, (r.returncode, r.stderr)
    r = _run(cmd, ECHO)
    assert r.returncode == 0, (r.returncode, r.stderr)


@needs_shell
def test_frozen_process_guard_blocks_image_name_kill(frozen):
    r = _run(gh.guard_shell_command(), KILL)
    assert r.returncode == 2 and r.stderr.strip(), (r.returncode, r.stderr)
    r = _run(gh.guard_shell_command(), ECHO)
    assert r.returncode == 0, (r.returncode, r.stderr)


def test_unknown_hook_fails_closed(capsys):
    assert hook_entry.run_hook(['nope']) == 2
    assert 'unknown hook' in capsys.readouterr().err
    assert hook_entry.run_hook([]) == 2


def test_run_hook_dispatches_fence_self_test(capsys):
    assert hook_entry.run_hook(['fence', '--self-test']) == 2
    assert gh.FENCE_SELF_TEST_TOKEN in capsys.readouterr().err


def test_app_py_handles_the_flag_before_anything_else():
    """The dispatch must precede the pythonnet set-up and every side effect."""
    src = (REPO / 'app.py').read_text(encoding='utf-8')
    flag_at = src.index("sys.argv[1] == '--clayrune-hook'")
    assert flag_at < src.index('PYTHONNET_RUNTIME')
    assert flag_at < src.index('def _ensure_data_dirs')
    assert hook_entry.HOOK_FLAG == gh.HOOK_ENTRY_FLAG
    assert (hook_entry.FENCE, hook_entry.PROCESS_GUARD) == (gh.FENCE_HOOK, gh.PROCESS_GUARD_HOOK)
