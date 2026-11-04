import json
import subprocess

import pytest

from mc import mcp_bridge_opencode as bridge


def test_translation_and_explicit_native_disables():
    config = bridge.translate({'local': {'command': 'python', 'args': ['a b'],
                                         'env': {'A': 'B'}, 'cwd': '/work'},
                               'web': {'type': 'http', 'url': 'https://example/mcp',
                                       'headers': {'X-Test': 'value'}}}, {'other': {}})
    assert config['other'] == {'enabled': False}
    assert config['local'] == {'type': 'local', 'command': ['python', 'a b'],
                               'environment': {'A': 'B'}, 'enabled': True, 'cwd': '/work'}
    assert config['web']['headers'] == {'X-Test': 'value'}
    assert bridge.translate({}, {'native': {}}) == {'native': {'enabled': False}}


def test_deep_merge_transport_conflict_is_visible():
    with pytest.raises(ValueError, match='conflicting transport'):
        bridge.translate({'same': {'command': 'fixture'}}, {'same': {'type': 'remote'}})


def test_runtime_env_preserves_other_inline_settings_and_uses_effective_names(monkeypatch):
    calls = []
    def run(cmd, **kw):
        calls.append((cmd, kw))
        if len(calls) == 2:
            return subprocess.CompletedProcess(cmd, 0, kw['env']['OPENCODE_CONFIG_CONTENT'])
        return subprocess.CompletedProcess(cmd, 0, json.dumps({'mcp': {'native': {'type': 'local'}}}))
    monkeypatch.setattr(bridge.subprocess, 'run', run)
    env = {'OPENCODE_CONFIG_CONTENT': json.dumps({'model': 'keep', 'permission': {'bash': 'ask'}})}
    output = json.loads(bridge.launch_env({'clayrune': {'command': 'fixture'}}, ['opencode'], env,
                                        '/work')['OPENCODE_CONFIG_CONTENT'])
    assert output['model'] == 'keep' and output['permission'] == {'bash': 'ask'}
    assert output['mcp']['native'] == {'enabled': False}
    assert output['mcp']['clayrune']['command'] == ['fixture']
    assert calls[0][0] == ['opencode', 'debug', 'config']
    assert calls[0][1]['env'] == env


@pytest.mark.parametrize('stdout,rc', [('invalid', 0), ('{"mcp":[]}', 0), ('{}', 1)])
def test_invalid_discovery_does_not_inherit_native_fleet(monkeypatch, stdout, rc):
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a, rc, stdout))
    with pytest.raises(ValueError, match='launch refused'):
        bridge.launch_env({}, ['opencode'], {}, '/work')


@pytest.mark.parametrize('native', [{'other': {'type': 'local'}},
                                   {'selected': {'type': 'local', 'command': ['fixture'],
                                                 'environment': {'UNSELECTED': 'value'}}}])
def test_native_merge_or_managed_override_mismatch_refuses(monkeypatch, native):
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, json.dumps({'mcp': native})))
    with pytest.raises(ValueError, match='exact per-launch MCP selection'):
        bridge.launch_env({'selected': {'command': 'fixture'}}, ['opencode'], {}, '/work')
