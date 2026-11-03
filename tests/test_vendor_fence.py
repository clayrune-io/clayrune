"""Real runtime dispatch/follow-up boundaries; mock only process/thread edges."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mc import agent_runtime as ar, guardrail_hooks as gh, state
from mc import vendor_fence as vf, vendor_fence_hook as hook, vendor_fence_probe as probe

ENGINES = ('gemini', 'qwen', 'opencode', 'goose', 'aider', 'kiro')
TRIGGERS = tuple(sorted(vf.lm.UNATTENDED_TRIGGER_TYPES))


@pytest.mark.parametrize('trigger', TRIGGERS)
def test_aider_opt_in_wraps_both_dispatch_and_followup(boundary, monkeypatch, trigger):
    from mc import aider_launch
    runtime, calls = boundary
    monkeypatch.setitem(state.CONFIG, aider_launch.FLAG, True)
    prepared = []

    def prepare(cmd, cwd, env):
        prepared.append((cmd, cwd, env))
        return ['restricted-aider-runner'] + cmd[1:]

    monkeypatch.setattr(aider_launch, 'prepare', prepare)
    rt = runtime('aider')
    session = {'trigger_type': trigger, 'log_lines': [], 'agent_model': 'test'}
    handle = rt.dispatch(project_path='/project', task='hello', session_dict=session)
    rt.write_followup(handle, 'again')
    assert len(prepared) == len(calls) == 2
    assert all(args[0][0] == 'restricted-aider-runner' for args, _ in calls)
    monkeypatch.setitem(state.CONFIG, aider_launch.FLAG, False)
    with pytest.raises(vf.VendorFenceError):
        rt.write_followup(handle, 'must refuse')
    assert len(calls) == 2


@pytest.mark.parametrize('followup', [False, True])
def test_aider_opt_in_drift_refuses_without_provider_spawn(boundary, monkeypatch, followup):
    from mc import aider_launch
    runtime, calls = boundary
    monkeypatch.setitem(state.CONFIG, aider_launch.FLAG, True)
    monkeypatch.setattr(aider_launch, 'prepare',
                        lambda *a: (_ for _ in ()).throw(RuntimeError('Aider source drift')))
    rt = runtime('aider')
    session = {'trigger_type': 'schedule', 'log_lines': [], 'agent_model': 'test'}
    with pytest.raises(vf.VendorFenceError, match='source drift'):
        if followup:
            rt.write_followup(ar.SessionHandle(
                mc_session_id='test', provider='aider', mode='A',
                project_path='/project', project_id='project', session_dict=session), 'again')
        else:
            rt.dispatch(project_path='/project', task='hello', session_dict=session)
    assert not calls and session['status'] == 'error'


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', True)
    monkeypatch.setattr(gh, 'hooks_dir', lambda: tmp_path / 'hooks')
    monkeypatch.setattr(ar, '_guardrail_hooks_dir', lambda: tmp_path / 'hooks')
    (tmp_path / 'hooks').mkdir()
    source = tmp_path / 'hooks' / 'settings.json'
    source.write_text(json.dumps({'mcpServers': {'existing': {'command': 'echo'}},
                                 'hooks': {'Stop': [{'hooks': []}]}}))
    monkeypatch.setattr(ar, '_guardrail_launch_file', lambda vendor: source)
    monkeypatch.setattr(gh, 'run_fence_self_test', lambda *a, **kw: (True, 'tested'))
    monkeypatch.setattr(probe, 'self_test_argv', lambda vendor, binary, cwd, path, cmd:
                        ['node', 'native-probe', vendor, str(path), cmd])

    class Thread:
        def __init__(self, *a, **kw): pass
        def start(self): pass

    class Proc:
        pid = 999999
        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = iter([])
        def poll(self): return 0
        def wait(self): return 0

    def popen(*args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    monkeypatch.setattr(ar.subprocess, 'Popen', popen)
    monkeypatch.setattr(ar.threading, 'Thread', Thread)

    def runtime(vendor):
        rt = type(ar.get_runtime(vendor))()
        monkeypatch.setattr(rt, 'resolve_binary', lambda: Path('fake-cli'))
        monkeypatch.setattr(rt, 'build_command', lambda **kw: ['fake-cli'])
        if vendor == 'qwen':
            monkeypatch.setattr(rt, '_settings_auth_env', lambda: {})
        return rt
    return runtime, calls


@pytest.mark.parametrize('vendor', ENGINES)
@pytest.mark.parametrize('trigger', TRIGGERS)
def test_refuse_dispatch_and_followup(boundary, monkeypatch, vendor, trigger):
    runtime, calls = boundary
    monkeypatch.setattr(gh, 'run_fence_self_test', lambda *a, **kw: (False, 'cannot load'))
    rt = runtime(vendor)
    session = {'trigger_type': trigger, 'log_lines': []}
    with pytest.raises(vf.VendorFenceError, match='refused while fencing is enabled'):
        rt.dispatch(project_path='/project', task='hello', session_dict=session)
    assert not calls
    assert session['status'] == 'error'
    assert session['process_alive'] is False
    assert 'refused' in session['log_lines'][-1]
    handle = ar.SessionHandle(mc_session_id='test', provider=vendor, mode='A',
                              project_path='/project', project_id='project', session_dict=session)
    with pytest.raises(vf.VendorFenceError):
        rt.write_followup(handle, 'again')
    assert not calls
    assert 'refused' in session['log_lines'][-1]


@pytest.mark.parametrize('vendor', ENGINES)
@pytest.mark.parametrize('trigger,enabled', [('manual', True), (None, True), ('schedule', False)])
def test_allowed_dispatch_and_followup_unchanged(boundary, monkeypatch, vendor, trigger, enabled):
    runtime, calls = boundary
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', enabled)
    def no_selftest(*a, **kw):
        pytest.fail('attended/disabled launch must not self-test')
    monkeypatch.setattr(gh, 'run_fence_self_test', no_selftest)
    rt = runtime(vendor)
    session = {'trigger_type': trigger, 'log_lines': [], 'agent_model': 'test'}
    handle = rt.dispatch(project_path='/project', task='hello', session_dict=session)
    rt.write_followup(handle, 'again')
    assert len(calls) == 2
    assert session['status'] == 'running'
    for _, kwargs in calls:
        path = (kwargs.get('env') or {}).get(vf.SETTINGS_ENV.get(vendor, ''))
        assert not path or '-fence-' not in path


@pytest.mark.parametrize('vendor', ('gemini', 'qwen'))
def test_armed_dispatch_and_followup_keep_mcp_and_regenerate(boundary, monkeypatch, vendor):
    runtime, calls = boundary
    checks = []
    monkeypatch.setattr(gh, 'run_fence_self_test',
                        lambda argv, **kw: (checks.append((argv, kw)) or True, 'ok'))
    rt = runtime(vendor)
    session = {'trigger_type': 'workflow', 'log_lines': [], 'agent_model': 'test'}
    handle = rt.dispatch(project_path='/project', task='hello', session_dict=session,
                         mcp_config_json=json.dumps({'mcpServers': {'existing': {'command': 'echo'}}}))
    rt.write_followup(handle, 'again')
    assert len(calls) == len(checks) == 2
    for _, kwargs in calls:
        settings = json.loads(Path(kwargs['env'][vf.SETTINGS_ENV[vendor]]).read_text())
        assert 'existing' in settings['mcpServers']
        assert 'Stop' in settings['hooks']
        event = 'BeforeTool' if vendor == 'gemini' else 'PreToolUse'
        fence = settings['hooks'][event][-1]
        assert fence['matcher'] == ''
        assert fence['hooks'][0]['name'] == gh.FENCE_HOOK_NAME
        assert 'vendor_fence_hook.py' in fence['hooks'][0]['command']
        assert settings['disableAllHooks'] is False
        if vendor == 'gemini':
            assert settings['hooksConfig']['enabled'] is True
    assert all('--self-test' in check[0][-1] for check in checks)


@pytest.mark.parametrize('vendor', ENGINES)
def test_switch_on_between_turns_refuses_existing_chat(boundary, monkeypatch, vendor):
    runtime, calls = boundary
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', False)
    rt = runtime(vendor)
    session = {'trigger_type': 'dispatch', 'log_lines': [], 'agent_model': 'test'}
    handle = rt.dispatch(project_path='/project', task='hello', session_dict=session)
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', True)
    monkeypatch.setattr(gh, 'run_fence_self_test', lambda *a, **kw: (False, 'broken guard'))
    with pytest.raises(vf.VendorFenceError):
        rt.write_followup(handle, 'again')
    assert len(calls) == 1


@pytest.mark.parametrize('vendor', ('gemini', 'qwen'))
@pytest.mark.parametrize('contents', ['{broken', '[]', '{"hooks": []}'])
def test_bad_settings_refuse(boundary, monkeypatch, tmp_path, vendor, contents):
    runtime, calls = boundary
    source = tmp_path / 'hooks' / 'bad.json'
    source.write_text(contents)
    monkeypatch.setattr(ar, '_guardrail_launch_file', lambda vendor: source)
    rt = runtime(vendor)
    with pytest.raises(vf.VendorFenceError):
        rt.dispatch(project_path='/project', task='hello',
                    session_dict={'trigger_type': 'schedule'})
    assert not calls


@pytest.mark.parametrize('vendor', ('gemini', 'qwen'))
def test_settings_write_failure_refuses(boundary, monkeypatch, tmp_path, vendor):
    runtime, calls = boundary
    blocker = tmp_path / 'not-directory'
    blocker.write_text('block')
    monkeypatch.setattr(gh, 'hooks_dir', lambda: blocker)
    monkeypatch.setattr(ar, '_guardrail_launch_file', lambda vendor: None)
    with pytest.raises(vf.VendorFenceError):
        runtime(vendor).dispatch(project_path='/project', task='hello',
                                  session_dict={'trigger_type': 'schedule'})
    assert not calls


@pytest.mark.parametrize('vendor', ('gemini', 'qwen'))
def test_inherited_user_settings_are_never_copied(boundary, monkeypatch, tmp_path, vendor):
    runtime, calls = boundary
    user_path = tmp_path / 'user-settings.json'
    user_path.write_text('{"private": "user owned"}')
    monkeypatch.setattr(ar, '_guardrail_launch_file', lambda vendor: None)
    with pytest.raises(vf.VendorFenceError, match='cannot copy user configuration'):
        runtime(vendor).dispatch(project_path='/project', task='hello',
                                  env_extra={vf.SETTINGS_ENV[vendor]: str(user_path)},
                                  session_dict={'trigger_type': 'schedule'})
    assert user_path.read_text() == '{"private": "user owned"}'
    assert not calls


@pytest.mark.parametrize('vendor', ('gemini', 'qwen'))
def test_repeated_preparation_does_not_duplicate_fence(boundary, vendor):
    runtime, calls = boundary
    session = {'trigger_type': 'schedule'}
    env = {}
    vf.prepare_env(vendor, session, env, binary=Path('fake-cli'))
    first = env[vf.SETTINGS_ENV[vendor]]
    vf.prepare_env(vendor, session, env, binary=Path('fake-cli'))
    assert first == env[vf.SETTINGS_ENV[vendor]]
    data = json.loads(Path(first).read_text())
    event = 'BeforeTool' if vendor == 'gemini' else 'PreToolUse'
    assert len(data['hooks'][event]) == 1


@pytest.mark.parametrize('vendor', tuple(vf.UNVERIFIED))
def test_refusal_precedes_binary_resolution(boundary, monkeypatch, vendor):
    runtime, calls = boundary
    rt = runtime(vendor)
    monkeypatch.setattr(rt, 'resolve_binary', lambda: pytest.fail('must refuse first'))
    with pytest.raises(vf.VendorFenceError, match=vf.UNVERIFIED[vendor]):
        rt.dispatch(project_path='/project', task='hello',
                    session_dict={'trigger_type': 'dispatch'})


@pytest.mark.parametrize('native,canonical', [('run_shell_command', 'Bash'),
    ('write_file', 'Write'), ('replace', 'Edit'), ('read_file', 'Read'),
    ('glob', 'Glob'), ('grep_search', 'Grep'), ('Bash', 'Bash'), ('Edit', 'Edit')])
def test_payload_keeps_identity(native, canonical):
    payload = {'tool_name': native, 'tool_input': {'file_path': '/p/file'},
               'cwd': '/p', 'tool_use_id': 'call', 'session_id': 'session'}
    result = hook.normalize_payload(payload)
    assert result['tool_name'] == canonical
    assert result['tool_use_id'] == 'call'
    assert result['cwd'] == '/p'
    assert payload['tool_name'] == native


@pytest.mark.parametrize('payload', [[], {}, {'tool_name': 'Bash', 'tool_input': []},
    {'tool_name': 'read_many_files', 'tool_input': {'paths': ['/p/a', '/p/b']}}])
def test_malformed_payload_blocks(monkeypatch, capsys, payload):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    assert hook.main() == 2
    assert 'blocking fail-closed' in capsys.readouterr().err


def test_frozen_hook_entry(monkeypatch):
    from mc import hook_entry
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(hook, 'main', lambda args: 2 if args == ['--self-test'] else 0)
    assert hook_entry.run_hook(['vendor-fence', '--self-test']) == 2
    assert '--clayrune-hook' in vf.hook_command('powershell.exe')
    assert 'vendor-fence' in vf.hook_command('powershell.exe')


@pytest.mark.parametrize('mode', ('crash', 'unexpected_exit'))
def test_fence_failure_itself_blocks(monkeypatch, capsys, mode):
    from steward import fence
    def broken(args):
        if mode == 'crash':
            raise RuntimeError('guard load failure')
        return 1
    monkeypatch.setattr(fence, 'main', broken)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(
        {'tool_name': 'run_shell_command', 'tool_input': {'command': 'echo okay'}})))
    assert hook.main() == 2
    assert 'blocking fail-closed' in capsys.readouterr().err


def test_powershell_tool_mapping():
    result = hook.normalize_payload({'tool_name': 'run_shell_command',
                                      'tool_input': {'command': 'Write-Output okay'}},
                                     powershell=True)
    assert result['tool_name'] == 'PowerShell'


@pytest.mark.parametrize('vendor', ('gemini', 'qwen'))
def test_native_probe_not_given_wrong_environment_or_cwd(boundary, monkeypatch, vendor):
    runtime, calls = boundary
    captured = []
    def check(argv, **kwargs):
        captured.append(kwargs)
        return True, 'native pass'
    monkeypatch.setattr(gh, 'run_fence_self_test', check)
    runtime(vendor).dispatch(project_path='/project', task='hello',
                             env_extra={'FENCE_TEST_ENV': 'same child'},
                             session_dict={'trigger_type': 'schedule'})
    assert captured[0]['cwd'] == '/project'
    assert captured[0]['env']['FENCE_TEST_ENV'] == 'same child'
    assert calls[0][1]['env']['FENCE_TEST_ENV'] == 'same child'


def test_real_translated_write_blocks_global_config(tmp_path):
    payload = {'tool_name': 'write_file',
               'tool_input': {'file_path': str(Path.home() / '.claude' / 'settings.json'),
                              'content': 'never written; classification only'},
               'cwd': str(tmp_path)}
    result = subprocess.run([sys.executable, str(Path(hook.__file__))],
                            input=json.dumps(payload), text=True, capture_output=True,
                            timeout=10)
    assert result.returncode == 2
    assert 'STEWARD FENCE blocked' in result.stderr


@pytest.mark.parametrize('command,expected', [('echo okay', 0), ('git push', 2)])
def test_real_adapter_fence_verdict(command, expected, tmp_path):
    payload = {'tool_name': 'run_shell_command', 'tool_input': {'command': command},
               'cwd': str(tmp_path)}
    result = subprocess.run([sys.executable, str(Path(hook.__file__))],
                            input=json.dumps(payload), text=True, capture_output=True,
                            timeout=10)
    assert result.returncode == expected, result.stderr
    if expected:
        assert 'STEWARD FENCE blocked' in result.stderr


@pytest.mark.parametrize('vendor', ('gemini', 'qwen'))
def test_real_shared_selftest(vendor):
    shell = vf._shell(vendor, dict(os.environ))
    ok, detail = gh.run_fence_self_test(
        vf._shell_argv(shell, vf.hook_command(shell, self_test=True)), timeout=10)
    assert ok, detail
