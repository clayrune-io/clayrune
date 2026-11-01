"""Four vendor runtime boundaries: selection, continuation, retry and fence."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
try:
    import tomllib
except ImportError:  # pytest provides tomli on supported Python 3.10 installs.
    import tomli as tomllib

import pytest

from mc import agent_runtime as ar, session_mcp_launch as launch, state
from mc.mcp_launch_config import servers_from_json

VENDORS = ('codex', 'opencode', 'goose', 'kiro')
MCP = json.dumps({'mcpServers': {'clayrune': {'command': 'fixture', 'args': ['a b'],
                                           'env': {'BINDING': 'private-fixture'}}}})


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    monkeypatch.setenv('KIRO_HOME', str(tmp_path / 'kiro'))
    monkeypatch.setattr(ar, '_guardrail_codex_hook_args', lambda **kw: [])
    monkeypatch.setattr(ar, '_codex_fence_self_test', lambda: (True, 'stub'))
    monkeypatch.setattr(ar.CodexRuntime, '_gate_model', lambda self, m: (m, ''))
    def run(cmd, **kw):
        if '--profile' in cmd:
            profile = cmd[cmd.index('--profile') + 1]
            data = tomllib.loads((Path(kw['env']['CODEX_HOME']) / (profile + '.config.toml')).read_text())
            data = [{'name': name, 'enabled': spec['enabled'], 'transport': spec}
                    for name, spec in data['mcp_servers'].items()]
        elif 'debug' in cmd:
            content = kw['env'].get('OPENCODE_CONFIG_CONTENT')
            data = json.loads(content) if content else {'mcp': {'native': {'type': 'local'}}}
        else:
            data = [{'name': 'native'}]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(data))
    monkeypatch.setattr(subprocess, 'run', run)
    class Proc:
        pid = 1234567
        stdin = None
        stdout = iter(())
        def poll(self): return 0
    def popen(cmd, **kw):
        calls.append((list(cmd), kw))
        return Proc()
    monkeypatch.setattr(subprocess, 'Popen', popen)
    class Thread:
        def __init__(self, *args, **kw): pass
        def start(self): pass
    monkeypatch.setattr(ar.threading, 'Thread', Thread)
    def runtime(vendor):
        rt = type(ar.get_runtime(vendor))()
        monkeypatch.setattr(rt, 'resolve_binary', lambda: Path(vendor))
        return rt
    return runtime, calls


def selected(vendor, cmd, env):
    if vendor == 'codex':
        profile = cmd[cmd.index('--profile') + 1]
        data = tomllib.loads((Path(env['CODEX_HOME']) / (profile + '.config.toml')).read_text())
        return {name for name, cfg in data['mcp_servers'].items() if cfg['enabled']}
    if vendor == 'opencode':
        return {name for name, cfg in json.loads(env['OPENCODE_CONFIG_CONTENT'])['mcp'].items()
                if cfg['enabled']}
    if vendor == 'goose':
        return {cfg['name'] for cfg in json.loads(Path(cmd[cmd.index('--recipe') + 1]).read_text())['extensions']}
    name = cmd[cmd.index('--agent') + 1]
    return set(json.loads((Path(env['KIRO_HOME']) / 'agents' / (name + '.json')).read_text())['mcpServers'])


@pytest.mark.parametrize('vendor', VENDORS)
@pytest.mark.parametrize('config,expected', [(MCP, {'clayrune'}), ('{"mcpServers":{}}', set())])
def test_dispatch_and_followup_keep_selection_and_env(boundary, vendor, config, expected):
    runtime, calls = boundary
    rt = runtime(vendor)
    session = {'trigger_type': 'manual', 'log_lines': []}
    handle = rt.dispatch(project_path='.', project_id='p', task='task {{keep}}',
                         system_prompt='system', session_dict=session, mcp_config_json=config,
                         env_extra={'MCP_EXTRA': 'keep'})
    assert selected(vendor, calls[0][0], calls[0][1]['env']) == expected
    assert calls[0][1]['env']['MCP_EXTRA'] == 'keep'
    assert 'private-fixture' not in str(calls[0][0])
    assert session['_mcp_config_json'] == config
    session['provider_session_id'] = 'native-id'
    rt.write_followup(handle, 'next task')
    assert selected(vendor, calls[1][0], calls[1][1]['env']) == expected
    assert calls[1][1]['env']['MCP_EXTRA'] == 'keep'
    if vendor == 'codex':
        assert 'resume' in calls[1][0] and 'native-id' in calls[1][0]
    if vendor == 'goose':
        assert '--instructions' not in calls[0][0] and '--system' not in calls[0][0]
        assert calls[0][1]['stdin'] == subprocess.DEVNULL
        text = json.loads(Path(calls[1][0][-1]).read_text())['prompt']
        assert 'next task' in text and 'system' in text


def test_codex_model_refusal_retry_keeps_mcp(boundary):
    runtime, calls = boundary
    rt = runtime('codex')
    handle = rt.dispatch(project_path='.', task='task', mcp_config_json=MCP,
                         session_dict={'trigger_type': 'manual', 'log_lines': []})
    handle.session_dict['_codex_model_refused'] = 'rejected-model'
    rt._installed_cli_version = lambda: 'fixture'
    assert rt.retry_without_model(handle, None)
    assert selected('codex', calls[-1][0], calls[-1][1]['env']) == {'clayrune'}


@pytest.mark.parametrize('vendor', ['opencode', 'goose', 'kiro'])
def test_unattended_refusal_precedes_bridge_and_spawn(boundary, monkeypatch, vendor):
    runtime, calls = boundary
    monkeypatch.setitem(state.CONFIG, 'fence_unattended_enabled', True)
    monkeypatch.setattr(launch, 'prepare', lambda *a, **kw: pytest.fail('bridge ran before fence'))
    rt = runtime(vendor)
    with pytest.raises(ar.vendor_fence.VendorFenceError):
        rt.dispatch(project_path='.', task='task', mcp_config_json=MCP,
                    session_dict={'trigger_type': 'schedule', 'log_lines': []})
    handle = ar.SessionHandle(mc_session_id='s', provider=vendor, mode='A', project_path='.',
                              project_id='p', session_dict={'trigger_type': 'schedule', 'log_lines': []})
    with pytest.raises(ar.vendor_fence.VendorFenceError):
        rt.write_followup(handle, 'next')
    assert calls == []


@pytest.mark.parametrize('vendor', VENDORS)
def test_incognito_withholds_even_supplied_servers(boundary, vendor):
    runtime, calls = boundary
    runtime(vendor).dispatch(project_path='.', task='task', mcp_config_json=MCP, incognito=True,
                             session_dict={'trigger_type': 'manual'})
    assert selected(vendor, calls[0][0], calls[0][1]['env']) == set()


def test_resolution_canonical_precedence_and_exclusions(monkeypatch):
    from mc import mcp
    monkeypatch.setattr(mcp, '_read_global_servers', lambda: {'same': {'command': 'global'}})
    monkeypatch.setattr(mcp, '_read_project_servers', lambda p: {'same': {'command': 'project'},
                        'web': {'type': 'http', 'url': 'https://example/mcp'}})
    project = {'id': 'p', 'project_path': '/p'}
    cfg = json.loads(launch.resolve(project, None, {}))['mcpServers']
    assert cfg['same']['command'] == 'project' and cfg['web']['type'] == 'http'
    assert 'clayrune' in cfg
    assert 'clayrune' not in json.loads(launch.resolve(project, None,
                                                      {'clayrune_mcp_enabled': False}))['mcpServers']
    assert json.loads(launch.resolve(project, '{"mcpServers":{}}', {})) == {'mcpServers': {}}
    with pytest.raises(ValueError, match='explicit MCP selection'):
        launch.resolve({**project, 'enabled_mcp_servers': []}, None, {})
    assert json.loads(launch.resolve({**project, '_is_incognito_project': True}, MCP, {})) == {'mcpServers': {}}
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert json.loads(launch.resolve(project, MCP, {})) == {'mcpServers': {}}


@pytest.mark.parametrize('value', ['null', '{"mcpServers":[]}', '{"mcpServers":{"x":{}}}',
                                  '{"mcpServers":{"x":{"command":"x","args":"bad"}}}'])
def test_bad_selection_never_becomes_native_discovery(value):
    with pytest.raises(ValueError, match='Invalid resolved MCP'):
        servers_from_json(value)


def test_launch_artifacts_never_land_under_a_repository(tmp_path):
    from mc.mcp_launch_config import private_json, private_text
    (tmp_path / '.git').mkdir()
    with pytest.raises(ValueError, match='inside a repository'):
        private_json({'fixture': 'value'}, directory=tmp_path / 'agents')
    with pytest.raises(ValueError, match='inside a repository'):
        private_text('fixture', tmp_path, '.toml')
