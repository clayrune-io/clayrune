"""Fail-closed steward fence for UNATTENDED Claude launches (2026-10-09, Quill).

Claude Code runs a PreToolUse command hook that cannot start, times out or
exits with an unexpected code as a non-blocking error, so the tool call runs.
Claude Code 2.1.295 added `onFailure: "block"`. This file covers:

  (a) the pre-spawn fence self-test, run before every UNATTENDED Claude spawn,
      refusing the launch when it fails (Codex parity);
  (b) the `onFailure: "block"` copy of the per-launch `--settings` file, used
      only for unattended launches on CLI >= 2.1.295, with a cached version
      probe; attended launches get back the very same cmd list;
  (c) the fence hook `timeout` is in SECONDS (it was 10000, ~2 h 47 min). That
      fix is in steward/core.py, which is human-owned, so it ships as
      docs/patches/fence-timeout-seconds.patch; its tests are xfail(strict)
      keyed on whether core.py has the constant, and a second test applies the
      patch to a throwaway copy and runs the result.

Measurement behind the merge/dedupe claims: docs/FENCE_FAIL_CLOSED_MEASUREMENT.md.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / 'tools' / 'guards'))

import install_hooks  # noqa: E402
from mc import fence_fail_closed as ffc  # noqa: E402
from mc import guardrail_hooks as gh  # noqa: E402
from mc import launch_marker  # noqa: E402
from steward import core as steward_core  # noqa: E402

# tests/conftest.py stubs the self-test for every other file; this one runs the real thing.
REAL_FENCE_SELF_TEST = True

UNATTENDED = ['dispatch', 'schedule', 'workflow', 'hivemind_orchestrator', 'hivemind_worker']
PATCH = PROJECT_ROOT / 'docs' / 'patches' / 'fence-timeout-seconds.patch'


def _quote(p) -> str:
    return '"' + str(p).replace('\\', '/') + '"'


@pytest.fixture
def fence_cmd(monkeypatch):
    """Point steward.core._fence_command at a stand-in script. The script
    answers --self-test like fence.py (exit 2 + token); `mode` picks what it
    does otherwise."""
    def make(tmp_path, *, selftest='ok', interpreter=None):
        script = tmp_path / 'fake_fence.py'
        token = gh.FENCE_SELF_TEST_TOKEN
        body = {
            'ok': f"print({token!r}, file=sys.stderr); sys.exit(2)",
            'crash': "sys.exit(1)",
            'exit2_no_token': "print('blocked', file=sys.stderr); sys.exit(2)",
            'exit0': "sys.exit(0)",
        }[selftest]
        script.write_text(f"import sys\nif '--self-test' in sys.argv:\n    {body}\n"
                          "sys.exit(0)\n", encoding='utf-8')
        cmd = f'{_quote(interpreter or sys.executable)} {_quote(script)}'
        monkeypatch.setattr(steward_core, '_fence_command', lambda: cmd)
        return cmd
    return make


@pytest.fixture(autouse=True)
def _fence_on(monkeypatch):
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', True)


# ── (a) self-test ───────────────────────────────────────────────────────────

def test_real_fence_command_passes_the_self_test():
    ok, detail = ffc.claude_fence_self_test()
    assert ok, detail


def test_self_test_passes_for_a_fence_that_answers_the_token(tmp_path, fence_cmd):
    fence_cmd(tmp_path)
    assert ffc.claude_fence_self_test()[0] is True


@pytest.mark.parametrize('mode', ['crash', 'exit2_no_token', 'exit0'])
def test_self_test_fails_for_a_fence_that_does_not_answer_the_token(tmp_path, fence_cmd, mode):
    fence_cmd(tmp_path, selftest=mode)
    ok, detail = ffc.claude_fence_self_test()
    assert ok is False
    assert detail


def test_self_test_fails_when_the_interpreter_is_missing(tmp_path, fence_cmd):
    fence_cmd(tmp_path, interpreter=tmp_path / 'no-such' / 'python.exe')
    ok, detail = ffc.claude_fence_self_test()
    assert ok is False
    assert 'could not run the fence' in detail


def test_self_test_never_raises_when_the_command_cannot_be_built(monkeypatch):
    def boom():
        raise RuntimeError('no fence script')
    monkeypatch.setattr(steward_core, '_fence_command', boom)
    ok, detail = ffc.claude_fence_self_test()
    assert ok is False and 'no fence script' in detail


def test_codex_self_test_still_uses_the_shared_runner(monkeypatch):
    seen = {}

    def fake(argv, *, timeout, what='the hook shell'):
        seen.update(argv=argv, timeout=timeout, what=what)
        return True, 'ok'
    monkeypatch.setattr(gh, 'run_fence_self_test', fake)
    assert gh.codex_fence_self_test() == (True, 'ok')
    assert seen['argv'][-1].endswith(gh.FENCE_SELF_TEST_ARG) or \
        gh.FENCE_SELF_TEST_ARG in seen['argv'][-1]
    assert seen['what'] == 'the hook shell'


@pytest.mark.parametrize('trigger_type', UNATTENDED)
def test_unattended_launch_is_refused_when_the_self_test_fails(tmp_path, fence_cmd, trigger_type):
    fence_cmd(tmp_path, selftest='crash')
    cmd = ['claude', '--print']
    with pytest.raises(ffc.ClaudeFenceSelfTestError) as ei:
        launch_marker.launch_cmd(cmd, trigger_type)
    assert 'refusing unattended Claude launch' in str(ei.value)
    assert isinstance(ei.value, RuntimeError)


@pytest.mark.parametrize('trigger_type', ['manual', '', None, 'some_future_type'])
def test_attended_launch_is_untouched_and_never_self_tested(monkeypatch, trigger_type):
    def boom(**k):
        raise AssertionError('self-test must not run for an attended launch')
    monkeypatch.setattr(ffc, 'claude_fence_self_test', boom)
    cmd = ['claude', '--print', '--settings', 'x.json']
    assert launch_marker.launch_cmd(cmd, trigger_type) is cmd


def test_fence_switched_off_skips_everything(monkeypatch):
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', False)
    monkeypatch.setattr(ffc, 'claude_fence_self_test',
                        lambda **k: pytest.fail('must not run with the fence off'))
    cmd = ['claude', '--settings', 'x.json']
    assert launch_marker.launch_cmd(cmd, 'dispatch') is cmd


# ── (b) version probe, cached ───────────────────────────────────────────────

@pytest.fixture
def fake_cli(tmp_path, monkeypatch):
    """A claude 'binary' whose `--version` output is controlled, counting probes."""
    ffc._version_cache.clear()
    binary = tmp_path / 'claude.cmd'
    binary.write_text('x', encoding='utf-8')
    state = {'out': '2.1.295 (Claude Code)', 'calls': 0, 'raise': None}

    real_run = subprocess.run

    def fake_run(argv, **kw):
        if list(argv)[-1:] != ['--version']:
            return real_run(argv, **kw)  # a self-test of a fence: run it for real
        state['calls'] += 1
        assert list(argv) == [str(binary), '--version']
        if state['raise']:
            raise state['raise']
        return subprocess.CompletedProcess(argv, 0, stdout=state['out'] + '\n', stderr='')
    monkeypatch.setattr(ffc.subprocess, 'run', fake_run)
    yield binary, state
    ffc._version_cache.clear()


@pytest.mark.parametrize('out,expected', [
    ('2.1.295 (Claude Code)', (2, 1, 295)),
    ('2.1.293 (Claude Code)', (2, 1, 293)),
    ('2.2.0', (2, 2, 0)),
])
def test_version_is_parsed(fake_cli, out, expected):
    binary, state = fake_cli
    state['out'] = out
    assert ffc.claude_cli_version(str(binary)) == expected


def test_version_probe_runs_once_per_binary_not_per_spawn(fake_cli):
    binary, state = fake_cli
    for _ in range(25):
        assert ffc.supports_on_failure(str(binary)) is True
    assert state['calls'] == 1


def test_version_probe_reruns_when_the_binary_or_package_changes(fake_cli, tmp_path):
    binary, state = fake_cli
    assert ffc.claude_cli_version(str(binary)) == (2, 1, 295)
    state['out'] = '2.1.296 (Claude Code)'
    pkg = tmp_path / 'node_modules' / '@anthropic-ai' / 'claude-code'
    pkg.mkdir(parents=True)
    (pkg / 'package.json').write_text('{"version": "2.1.296"}', encoding='utf-8')
    assert ffc.claude_cli_version(str(binary)) == (2, 1, 296)
    assert state['calls'] == 2


def test_unreadable_version_is_not_supported_and_retried_later(fake_cli, monkeypatch):
    binary, state = fake_cli
    state['raise'] = OSError('boom')
    assert ffc.supports_on_failure(str(binary)) is False
    assert ffc.supports_on_failure(str(binary)) is False
    assert state['calls'] == 1  # not re-probed on every spawn
    state['raise'] = None
    monkeypatch.setattr(ffc, '_VERSION_RETRY_AFTER_SEC', 0.0)
    assert ffc.supports_on_failure(str(binary)) is True
    assert state['calls'] == 2


def test_293_is_not_supported(fake_cli):
    binary, state = fake_cli
    state['out'] = '2.1.293 (Claude Code)'
    assert ffc.supports_on_failure(str(binary)) is False


# ── (b) the fail-closed settings copy ───────────────────────────────────────

def _launch_file(tmp_path) -> Path:
    home = tmp_path / '.clayrune'
    install_hooks.generate('claude', apply=True, clayrune_home=home)
    return gh.launch_file_path('claude', home)


def _by_name(data):
    return {h['name']: h for g in data['hooks']['PreToolUse'] for h in g['hooks']}


def test_generated_launch_file_stays_plain_for_attended_use(tmp_path):
    fence = _by_name(json.loads(_launch_file(tmp_path).read_text(encoding='utf-8')))[
        install_hooks.FENCE_HOOK_NAME]
    assert 'onFailure' not in fence
    assert 'timeout' not in fence


def test_copy_marks_only_the_fence_entry(tmp_path):
    src = _launch_file(tmp_path)
    out = ffc.fail_closed_settings_path(src, tmp_path / 'out')
    assert out is not None and out != src
    before = _by_name(json.loads(src.read_text(encoding='utf-8')))
    after = _by_name(json.loads(out.read_text(encoding='utf-8')))
    fence = after[install_hooks.FENCE_HOOK_NAME]
    assert fence['onFailure'] == 'block'
    assert fence['timeout'] == ffc.FAIL_CLOSED_TIMEOUT_SEC == 30  # seconds
    assert after[install_hooks.HOOK_NAME] == before[install_hooks.HOOK_NAME]
    # Same command string as before: that is what lets Claude Code collapse it
    # with the project-settings entry into one run (measurement doc).
    assert fence['command'] == before[install_hooks.FENCE_HOOK_NAME]['command']
    assert fence['command'] == steward_core._fence_command()
    # The source file is untouched.
    assert 'onFailure' not in before[install_hooks.FENCE_HOOK_NAME]


def test_copy_is_content_addressed_and_keeps_other_settings(tmp_path):
    src = _launch_file(tmp_path)
    data = json.loads(src.read_text(encoding='utf-8'))
    data['skillOverrides'] = {'some-skill': 'name-only'}
    scoped = tmp_path / 'scoped.json'
    scoped.write_text(json.dumps(data), encoding='utf-8')
    a = ffc.fail_closed_settings_path(scoped, tmp_path / 'out')
    b = ffc.fail_closed_settings_path(scoped, tmp_path / 'out')
    assert a == b
    assert json.loads(a.read_text(encoding='utf-8'))['skillOverrides'] == {'some-skill': 'name-only'}
    assert len(list((tmp_path / 'out').glob('*.json'))) == 1


def test_no_fence_entry_or_bad_file_means_no_copy(tmp_path):
    no_fence = tmp_path / 'a.json'
    no_fence.write_text(json.dumps({'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [
        {'type': 'command', 'command': 'x', 'name': 'other'}]}]}}), encoding='utf-8')
    assert ffc.fail_closed_settings_path(no_fence, tmp_path / 'out') is None
    bad = tmp_path / 'b.json'
    bad.write_text('{not json', encoding='utf-8')
    assert ffc.fail_closed_settings_path(bad, tmp_path / 'out') is None
    assert ffc.fail_closed_settings_path(tmp_path / 'missing.json', tmp_path / 'out') is None


def _cmd(binary, settings):
    return [str(binary), '--print', '--model', 'x', '--settings', str(settings), '-p', 't']


@pytest.mark.parametrize('trigger_type', UNATTENDED)
def test_unattended_launch_on_a_supporting_cli_swaps_the_settings_file(
        tmp_path, fake_cli, monkeypatch, trigger_type):
    binary, state = fake_cli
    src = _launch_file(tmp_path)
    monkeypatch.setattr(gh, 'hooks_dir', lambda *a, **k: tmp_path / 'hooks')
    monkeypatch.setattr(ffc, 'claude_fence_self_test', lambda **k: (True, 'ok'))
    cmd = _cmd(binary, src)
    out = launch_marker.launch_cmd(cmd, trigger_type)
    assert out is not cmd and cmd == _cmd(binary, src), 'input cmd must not be mutated'
    i = out.index('--settings')
    assert out[:i] == cmd[:i] and out[i + 2:] == cmd[i + 2:]
    assert Path(out[i + 1]) != src
    fence = _by_name(json.loads(Path(out[i + 1]).read_text(encoding='utf-8')))[
        install_hooks.FENCE_HOOK_NAME]
    assert fence['onFailure'] == 'block' and fence['timeout'] == 30


def test_unattended_launch_on_an_old_or_unknown_cli_keeps_the_plain_file(
        tmp_path, fake_cli, monkeypatch):
    binary, state = fake_cli
    src = _launch_file(tmp_path)
    monkeypatch.setattr(ffc, 'claude_fence_self_test', lambda **k: (True, 'ok'))
    state['out'] = '2.1.293 (Claude Code)'
    assert launch_marker.launch_cmd(_cmd(binary, src), 'dispatch') == _cmd(binary, src)
    ffc._version_cache.clear()
    state['out'] = 'garbage'
    assert launch_marker.launch_cmd(_cmd(binary, src), 'dispatch') == _cmd(binary, src)


def test_unattended_launch_without_a_settings_flag_is_passed_through(
        tmp_path, fake_cli, monkeypatch):
    binary, state = fake_cli
    monkeypatch.setattr(ffc, 'claude_fence_self_test', lambda **k: (True, 'ok'))
    cmd = [str(binary), '--print']
    assert launch_marker.launch_cmd(cmd, 'schedule') == cmd


def test_attended_launch_on_a_supporting_cli_gets_the_same_list(tmp_path, fake_cli):
    binary, state = fake_cli
    cmd = _cmd(binary, _launch_file(tmp_path))
    assert launch_marker.launch_cmd(cmd, 'manual') is cmd
    assert state['calls'] == 0, 'attended spawns must not pay for a version probe'


def test_copy_failure_degrades_to_the_plain_file(tmp_path, fake_cli, monkeypatch):
    binary, state = fake_cli
    src = _launch_file(tmp_path)
    monkeypatch.setattr(ffc, 'claude_fence_self_test', lambda **k: (True, 'ok'))
    monkeypatch.setattr(ffc, 'fail_closed_settings_path', lambda *a, **k: None)
    assert launch_marker.launch_cmd(_cmd(binary, src), 'dispatch') == _cmd(binary, src)


# ── (c) hook timeout is seconds ─────────────────────────────────────────────

_HAS_FIX = hasattr(steward_core, 'FENCE_HOOK_TIMEOUT_SEC')
_needs_patch = pytest.mark.xfail(
    not _HAS_FIX, strict=True,
    reason='steward/core.py is human-owned: apply docs/patches/fence-timeout-seconds.patch')


@_needs_patch
def test_project_fence_entry_timeout_is_seconds_not_ms():
    hook = steward_core._fence_settings_content()['hooks']['PreToolUse'][0]['hooks'][0]
    assert hook['timeout'] == 10
    assert hook['timeout'] < 600  # Claude's own default for a command hook is 600 s


def test_patch_applies_to_a_throwaway_copy_and_fixes_the_timeout(tmp_path):
    base = tmp_path / 'base'
    base.mkdir()
    # master's core.py, whatever the working copy holds: the patch is built
    # against the blob before the fix, so skip when the fix is already in.
    text = (PROJECT_ROOT / 'steward' / 'core.py').read_text(encoding='utf-8')
    if _HAS_FIX:
        pytest.skip('steward/core.py already carries the fix')
    (base / 'core.py').write_text(text, encoding='utf-8')
    r = subprocess.run(['git', '-C', str(tmp_path), 'apply', '-p2', '--directory=base',
                        str(PATCH)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    patched = (base / 'core.py').read_text(encoding='utf-8')
    assert '"timeout": 10000' not in patched
    assert 'FENCE_HOOK_TIMEOUT_SEC = 10' in patched
    # Run the patched module (no steward/ folder in the path on purpose).
    import importlib.util
    spec = importlib.util.spec_from_file_location('steward.core_patched_probe', base / 'core.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    hook = mod._fence_settings_content()['hooks']['PreToolUse'][0]['hooks'][0]
    assert hook['timeout'] == 10


# ── (b2) a project fence entry with a DIFFERENT command is shadowed ─────────
# On this machine the project entry was written by the system python and the
# launch file's by the venv python: different strings, so they do not collapse,
# both run, and Claude Code waits for all of them before it applies a block
# (P5, docs/FENCE_FAIL_CLOSED_MEASUREMENT.md). The copy therefore also carries
# a twin of the project command with the same keys.

def _write_project_settings(cwd: Path, *commands, timeout=10000, matcher='Bash|Write'):
    d = cwd / '.claude'
    d.mkdir(parents=True, exist_ok=True)
    (d / 'settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [
        {'matcher': matcher, 'hooks': [{'type': 'command', 'command': c, 'timeout': timeout}
                                       for c in commands]}]}}), encoding='utf-8')


def _project_cmd(fence_command: str) -> str:
    return fence_command + ' --project'


def test_project_entry_with_a_different_command_is_found(tmp_path, fence_cmd):
    own = fence_cmd(tmp_path)
    proj = tmp_path / 'proj'
    _write_project_settings(proj, _project_cmd(own))
    assert ffc.project_fence_entries(str(proj)) == [('Bash|Write', _project_cmd(own))]


def test_project_entry_with_our_own_command_adds_nothing(tmp_path, fence_cmd):
    own = fence_cmd(tmp_path)
    proj = tmp_path / 'proj'
    _write_project_settings(proj, own)
    assert ffc.project_fence_entries(str(proj)) == []


@pytest.mark.parametrize('cwd_kind', ['none', 'no_file', 'bad_json', 'no_fence_hook'])
def test_nothing_to_shadow(tmp_path, fence_cmd, cwd_kind):
    fence_cmd(tmp_path)
    proj = tmp_path / 'proj'
    proj.mkdir()
    if cwd_kind == 'bad_json':
        (proj / '.claude').mkdir()
        (proj / '.claude' / 'settings.json').write_text('{nope', encoding='utf-8')
    elif cwd_kind == 'no_fence_hook':
        _write_project_settings(proj, 'echo hello')
    assert ffc.project_fence_entries(None if cwd_kind == 'none' else str(proj)) == []


def test_project_entry_that_fails_its_self_test_is_left_alone(tmp_path, fence_cmd):
    fence_cmd(tmp_path)
    proj = tmp_path / 'proj'
    stale = f'{_quote(tmp_path / "gone" / "python.exe")} {_quote(tmp_path / "fence.py")}'
    _write_project_settings(proj, stale)
    assert ffc.project_fence_entries(str(proj)) == []


def test_copy_adds_one_shadow_per_project_command_and_no_third_run(tmp_path):
    src = _launch_file(tmp_path)
    own = steward_core._fence_command()
    shadow = [('Bash|Write', own + ' --project')]
    out = ffc.fail_closed_settings_path(src, tmp_path / 'out', project_fences=shadow)
    groups = json.loads(out.read_text(encoding='utf-8'))['hooks']['PreToolUse']
    hooks = [h for g in groups for h in g['hooks']]
    fences = [h for h in hooks if h.get('name') in (install_hooks.FENCE_HOOK_NAME,
                                                    ffc.PROJECT_COPY_NAME)]
    assert [h['command'] for h in fences] == [own, own + ' --project']
    for h in fences:
        assert h['onFailure'] == 'block' and h['timeout'] == 30
    # Exactly the entries that already existed plus the one shadow: nothing else.
    assert len(hooks) == len(json.loads(src.read_text(encoding='utf-8'))['hooks']['PreToolUse']) + 1
    # The shadow keeps the project entry's matcher.
    assert groups[-1]['matcher'] == 'Bash|Write'


def test_launch_cmd_shadows_the_project_entry_of_the_launch_cwd(tmp_path, fake_cli, fence_cmd,
                                                                 monkeypatch):
    binary, state = fake_cli
    own = fence_cmd(tmp_path)
    src = _launch_file(tmp_path)
    proj = tmp_path / 'proj'
    _write_project_settings(proj, _project_cmd(own))
    monkeypatch.setattr(gh, 'hooks_dir', lambda *a, **k: tmp_path / 'hooks')
    out = launch_marker.launch_cmd(_cmd(binary, src), 'dispatch', str(proj))
    hooks = [h for g in json.loads(Path(out[out.index('--settings') + 1]).read_text(
        encoding='utf-8'))['hooks']['PreToolUse'] for h in g['hooks']]
    by_name = {h.get('name'): h for h in hooks}
    assert by_name[ffc.PROJECT_COPY_NAME]['command'] == _project_cmd(own)
    assert by_name[ffc.PROJECT_COPY_NAME]['onFailure'] == 'block'
    # The project file itself is not touched.
    stored = json.loads((proj / '.claude' / 'settings.json').read_text(encoding='utf-8'))
    assert stored['hooks']['PreToolUse'][0]['hooks'][0]['timeout'] == 10000
    assert 'onFailure' not in stored['hooks']['PreToolUse'][0]['hooks'][0]


def test_launch_cmd_without_a_cwd_or_on_an_old_cli_adds_no_shadow(tmp_path, fake_cli, fence_cmd,
                                                                    monkeypatch):
    binary, state = fake_cli
    own = fence_cmd(tmp_path)
    src = _launch_file(tmp_path)
    proj = tmp_path / 'proj'
    _write_project_settings(proj, _project_cmd(own))
    monkeypatch.setattr(gh, 'hooks_dir', lambda *a, **k: tmp_path / 'hooks')
    out = launch_marker.launch_cmd(_cmd(binary, src), 'dispatch')
    assert ffc.PROJECT_COPY_NAME not in Path(out[out.index('--settings') + 1]).read_text(
        encoding='utf-8')
    state['out'] = '2.1.293 (Claude Code)'
    ffc._version_cache.clear()
    assert launch_marker.launch_cmd(_cmd(binary, src), 'dispatch', str(proj)) == _cmd(binary, src)


def test_attended_launch_never_reads_the_project_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ffc, 'project_fence_entries',
                        lambda cwd: pytest.fail('attended spawn must not read project settings'))
    cmd = ['claude', '--settings', 'x.json']
    assert launch_marker.launch_cmd(cmd, 'manual', str(tmp_path)) is cmd
