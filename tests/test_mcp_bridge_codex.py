"""Codex selection translation and offline CLI discovery contract."""
import json
from pathlib import Path
import subprocess

import pytest

from mc import mcp_bridge_codex as bridge


def test_stdio_http_and_explicit_empty():
    chosen = bridge.translate({'local': {'command': 'a b', 'args': ['x y'],
                                        'env': {'KEY': 'value'}, 'cwd': '/work'},
                               'web': {'type': 'http', 'url': 'https://example/mcp',
                                       'headers': {'X-Test': 'value'}}}, ['other'])
    assert chosen['other'] == {'enabled': False}
    assert chosen['local'] == {'enabled': True, 'command': 'a b', 'args': ['x y'],
                               'env': {'KEY': 'value'}, 'cwd': '/work'}
    assert chosen['web']['http_headers'] == {'X-Test': 'value'}
    assert bridge.translate({}, ['other']) == {'other': {'enabled': False}}


def test_sse_refuses_instead_of_changing_transport():
    with pytest.raises(ValueError, match='SSE'):
        bridge.translate({'remote': {'type': 'sse', 'url': 'https://example/sse'}}, [])


def test_profile_keeps_values_off_argv_and_does_not_modify_user_file(tmp_path, monkeypatch):
    config = tmp_path / 'config.toml'
    config.write_text('user-file')
    seen = []
    def run(cmd, **kwargs):
        seen.append((cmd, kwargs))
        if '--profile' in cmd:
            return subprocess.CompletedProcess(cmd, 0, json.dumps([
                {'name': 'other', 'enabled': False},
                {'name': 'test.name', 'enabled': True, 'transport': {
                    'command': 'fixture', 'args': ['opaque value'], 'env': {'KEY': 'opaque value'}}}]))
        return subprocess.CompletedProcess(cmd, 0, json.dumps([{'name': 'other'}]))
    monkeypatch.setattr(bridge.subprocess, 'run', run)
    args = bridge.launch_args({'test.name': {'command': 'fixture', 'args': ['opaque value'],
                                            'env': {'KEY': 'opaque value'}}},
                              ['npx', '--yes', '@openai/codex'], {'CODEX_HOME': str(tmp_path)}, '/work')
    assert 'opaque value' not in str(args)
    assert args[0] == '--profile'
    text = (tmp_path / (args[1] + '.config.toml')).read_text()
    assert '"other" = {"enabled" = false}' in text
    assert '"KEY" = "opaque value"' in text
    assert 'plugins = false' in text
    assert config.read_text() == 'user-file'
    assert seen[0][0][-5:] == ['mcp', 'list', '--json', '-c', 'features.plugins=false']
    assert seen[0][1]['cwd'] == '/work'


@pytest.mark.parametrize('stdout,rc', [('invalid', 0), ('{}', 0), ('[]', 1)])
def test_discovery_fails_closed_and_does_not_echo_output(monkeypatch, stdout, rc):
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a, rc, stdout, 'private-error'))
    with pytest.raises(ValueError, match='launch refused') as error:
        bridge.launch_args({}, ['codex'], {}, '/work')
    assert 'private-error' not in str(error.value)


def test_native_profile_ignored_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, json.dumps([
                            {'name': 'unexpected', 'enabled': True, 'transport': {}}])))
    with pytest.raises(ValueError, match='exact per-launch MCP profile'):
        bridge.launch_args({}, ['codex'], {'CODEX_HOME': str(tmp_path)}, '/work')
