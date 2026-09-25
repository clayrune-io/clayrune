"""MC-975 (2026-09-25): the steward fence never gated a Codex run.

Measured on codex-cli 0.155.1 (docs/_journal/MC-975-codex-unattended-sandbox.md):
Codex runs hooks as `powershell.exe -NoProfile -Command "<command>"`, blocks on
exit 2 WITH stderr or a deny JSON, and on anything else (parse error, other
exit code, timeout, exit 2 with empty stderr) logs `PreToolUse Failed` and
RUNS THE TOOL. The only fence Codex ever saw was its own migrated copy of the
Claude hook, `"<py>" "<fence.py>"`, which PowerShell cannot parse. And the
fence's two arming signals (CLAUDE_CODE_SESSION_ID, a Claude transcript) do
not exist for a Codex hook.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from mc import agent_runtime as ar
from mc import guardrail_hooks as gh

REPO = Path(__file__).resolve().parent.parent
FENCE = REPO / 'steward' / 'fence.py'
sys.path.insert(0, str(REPO / 'steward'))
import fence  # noqa: E402

BLOCK = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'echo shutdown-probe > p.txt'}})
ALLOW = json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'echo hi'}})
PS = shutil.which('powershell.exe') if os.name == 'nt' else None
needs_ps = pytest.mark.skipif(not PS, reason='Codex hook shell is PowerShell on Windows only')


def _clean_env():
    # The fence's own env-based arming must not leak in from a Claude-hosted
    # test run; --armed has to be the only thing that arms it here.
    return {k: v for k, v in os.environ.items() if not k.startswith('CLAUDE_CODE')}


def _run_hook(command, payload):
    assert PS
    return subprocess.run([PS, '-NoProfile', '-Command', command], input=payload,
                          capture_output=True, text=True, env=_clean_env(), timeout=60)


def _inline_fence_command(args):
    """The fence handler's command, TOML-unescaped, from a -c hooks value."""
    value = args[-1]
    tail = value.split('name="clayrune-process-guard"},', 1)[1]
    raw = tail.split('command="', 1)[1].rsplit('",timeout=', 1)[0]
    return raw.replace('\\\\', '\\').replace('\\"', '"'), value


@needs_ps
def test_migrated_quoted_shape_is_a_powershell_parse_error():
    """The root cause, pinned: the command Codex migrated into
    <project>/.codex/hooks.json never starts python under Codex's hook shell."""
    r = _run_hook(f'"{sys.executable}" "{FENCE.as_posix()}"', BLOCK)
    assert r.returncode == 1
    assert 'UnexpectedToken' in r.stderr


@needs_ps
def test_armed_fence_blocks_and_allows_through_codex_shell():
    cmd = gh.codex_fence_hook_command(armed=True)
    blocked, allowed = _run_hook(cmd, BLOCK), _run_hook(cmd, ALLOW)
    assert blocked.returncode == 2, blocked.stderr
    assert 'STEWARD FENCE blocked' in blocked.stderr
    assert allowed.returncode == 0, allowed.stderr


@needs_ps
def test_unarmed_fence_does_not_arm_itself_for_codex():
    assert _run_hook(gh.codex_fence_hook_command(armed=False), BLOCK).returncode == 0


@needs_ps
@pytest.mark.parametrize('py', ['C:/nope/python.exe', 'C:/Program Files/nope/python.exe'])
def test_armed_fence_that_cannot_start_python_blocks_with_a_reason(py):
    """Codex treats exit 2 with EMPTY stderr as a failure (fail open), so the
    wrapper must print a reason as well as exit 2."""
    r = _run_hook(gh.codex_fence_hook_command(armed=True, python_exe=py), ALLOW)
    assert r.returncode == 2
    assert 'blocking this tool call fail-closed' in r.stderr


@needs_ps
def test_self_test_passes_only_on_a_real_fence_round_trip():
    ok, detail = gh.codex_fence_self_test()
    assert ok, detail
    ok, detail = gh.codex_fence_self_test(python_exe='C:/nope/python.exe')
    assert not ok and 'exit 2' in detail  # wrapper's exit 2 carries no token


@needs_ps
def test_armed_fence_runs_far_inside_the_fail_open_timeout():
    """Codex lets the tool run when a hook times out. Measured max 0.36 s
    under 8-way load against a 30 s timeout; a fifth of it is the alarm."""
    import time
    cmd = gh.codex_fence_hook_command(armed=True)
    worst = 0.0
    for _ in range(3):
        t = time.perf_counter()
        assert _run_hook(cmd, BLOCK).returncode == 2
        worst = max(worst, time.perf_counter() - t)
    assert worst < gh.CODEX_FENCE_TIMEOUT_SEC / 5, worst


@pytest.mark.skipif(os.name != 'nt', reason='codex.CMD shim is Windows-only')
@pytest.mark.parametrize('armed', [True, False])
def test_injected_value_has_no_cmd_metacharacters(armed):
    """npm's codex.CMD re-parses its `%*` line with cmd.exe. An `&` in the
    fence command split the -c value and codex died at config load
    ("invalid type: string ..., expected a sequence in `hooks`")."""
    value = gh.codex_hook_config_args(fence_armed=armed)[-1]
    for ch in '&|<>^%':
        assert ch not in value, (ch, value)


def test_inline_value_carries_both_handlers_and_seconds_timeout():
    cmd, value = _inline_fence_command(gh.codex_hook_config_args(
        Path('C:/repo/mc/process_guard.py'), python_exe='C:/py/python.exe',
        fence_armed=True, fence_script=Path('C:/repo/steward/fence.py')))
    assert value.startswith('hooks.PreToolUse=[{hooks=[{type="command"')
    assert value.endswith(f'timeout={gh.CODEX_FENCE_TIMEOUT_SEC},name="clayrune-steward-fence"}}]}}]')
    assert 'matcher=' not in value
    assert cmd.startswith('C:/py/python.exe C:/repo/steward/fence.py --armed')
    assert gh.CODEX_FENCE_TIMEOUT_SEC < 120  # seconds, measured; 10000 was ~2.8 h


def test_guard_only_shape_unchanged_when_fence_not_requested():
    assert 'clayrune-steward-fence' not in gh.codex_hook_config_args()[-1]


def test_build_command_injects_fence_on_both_branches():
    rt = ar.CodexRuntime()
    rt._bin_cache = 'codex'
    for rid in ('', 'abc'):
        armed = ' '.join(rt.build_command(fence_armed=True, resume_id=rid))
        unarmed = ' '.join(rt.build_command(fence_armed=False, resume_id=rid))
        assert 'clayrune-steward-fence' in armed and '--armed' in armed
        assert 'clayrune-steward-fence' in unarmed and '--armed' not in unarmed


def test_fence_main_self_test_and_armed_flag(monkeypatch, capsys):
    assert fence.main(['--self-test']) == 2
    assert gh.FENCE_SELF_TEST_TOKEN in capsys.readouterr().err
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    import io
    monkeypatch.setattr(sys, 'stdin', io.StringIO(BLOCK))
    assert fence.main([]) == 0
    monkeypatch.setattr(sys, 'stdin', io.StringIO(BLOCK))
    assert fence.main(['--armed']) == 2


@pytest.mark.parametrize('session,enabled,expected', [
    ({'trigger_type': 'manual'}, True, False),
    ({'trigger_type': 'schedule'}, True, True),
    ({'trigger_type': 'dispatch'}, True, True),
    ({}, True, True),
    (None, True, True),
    ({'trigger_type': 'schedule'}, False, False),
])
def test_armed_decision(session, enabled, expected):
    assert ar.codex_fence_armed_decision(session, enabled) is expected


def _dispatch(monkeypatch, session, self_test):
    rt = ar.CodexRuntime()
    rt._bin_cache = 'codex'
    monkeypatch.setattr(ar, '_codex_fence_self_test', self_test)
    captured = {}
    monkeypatch.setattr(ar, '_mode_a_dispatch', lambda _rt, cmd, *a, **k: captured.setdefault('cmd', cmd))
    rt.dispatch(project_path=str(REPO), task='t', session_dict=session)
    return captured.get('cmd')


def test_unattended_dispatch_refused_when_self_test_fails(monkeypatch):
    session = {'trigger_type': 'schedule'}
    with pytest.raises(ar.CodexFenceSelfTestError):
        _dispatch(monkeypatch, session, lambda: (False, 'exit -1073741502'))


def test_unattended_dispatch_armed_after_self_test_passes(monkeypatch):
    session = {'trigger_type': 'schedule'}
    cmd = _dispatch(monkeypatch, session, lambda: (True, 'ok'))
    assert '--armed' in ' '.join(cmd)
    assert session['_codex_fence_armed'] is True


def test_manual_dispatch_skips_self_test_and_is_unarmed(monkeypatch):
    def boom():
        raise AssertionError('self-test must not run for a manual session')
    session = {'trigger_type': 'manual'}
    cmd = _dispatch(monkeypatch, session, boom)
    assert '--armed' not in ' '.join(cmd)
    assert session['_codex_fence_armed'] is False
