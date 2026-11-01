import json
import subprocess
from pathlib import Path

import pytest

from mc import mcp_bridge_kiro as bridge


def test_agent_selects_exact_set_without_native_inheritance():
    cfg = bridge.translate({'local': {'command': 'fixture', 'args': ['a b'], 'env': {'A': 'B'}},
                            'web': {'type': 'http', 'url': 'https://example/mcp',
                                    'headers': {'X-Test': 'value'}}})
    assert cfg['includeMcpJson'] is False
    assert cfg['mcpServers']['local']['env'] == {'A': 'B'}
    assert cfg['mcpServers']['web']['headers'] == {'X-Test': 'value'}
    assert bridge.translate({})['mcpServers'] == {}


def test_exclusive_named_file_keeps_user_agents_and_profile(tmp_path):
    agents = tmp_path / 'agents'
    agents.mkdir()
    original = agents / 'mine.json'
    original.write_text('user-agent')
    first = bridge.launch_args({}, {'KIRO_HOME': str(tmp_path)})
    second = bridge.launch_args({'clayrune': {'command': 'fixture'}}, {'KIRO_HOME': str(tmp_path)})
    assert first != second and first[0] == '--agent'
    cfg = json.loads((agents / (second[1] + '.json')).read_text())
    assert cfg['name'] == second[1] and set(cfg['mcpServers']) == {'clayrune'}
    assert original.read_text() == 'user-agent'


def test_unsupported_cwd_is_not_silently_discarded():
    with pytest.raises(ValueError, match='cwd'):
        bridge.translate({'local': {'command': 'fixture', 'cwd': '/work'}})


def test_native_agent_validation_error_is_visible(monkeypatch, tmp_path):
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **k:
                        subprocess.CompletedProcess(a, 1))
    with pytest.raises(ValueError, match='agent; launch refused'):
        bridge.launch_args({}, {'KIRO_HOME': str(tmp_path)}, prefix=['kiro-cli'], cwd='.')
