"""Opt-in native Codex MCP discovery for tests that fake only model turns."""
import json
from pathlib import Path
import subprocess
try:
    import tomllib
except ImportError:
    import tomli as tomllib

import pytest


@pytest.fixture
def codex_mcp_cli(tmp_path, monkeypatch):
    """Keep the real bridge/profile checks, with no operator config or CLI calls."""
    from mc import mcp
    monkeypatch.setattr(mcp, '_read_global_servers', lambda: {
        'fixture-stdio': {'command': 'fixture-mcp', 'args': ['test']},
        'fixture-http': {'type': 'http', 'url': 'https://fixture.invalid/mcp'}})
    monkeypatch.setattr(mcp, '_read_project_servers', lambda path: {})
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex-home'))
    real_run = subprocess.run
    profiles = []

    def run(cmd, **kwargs):
        if 'mcp' not in cmd or cmd[cmd.index('mcp'):] not in (
                ['mcp', 'list', '--json', '-c', 'features.plugins=false'],
                ['mcp', 'list', '--json']):
            return real_run(cmd, **kwargs)
        if '--profile' not in cmd:
            rows = [{'name': 'native-private', 'enabled': True}]
        else:
            name = cmd[cmd.index('--profile') + 1]
            path = Path(kwargs['env']['CODEX_HOME']) / (name + '.config.toml')
            data = tomllib.loads(path.read_text(encoding='utf-8'))
            assert data['features']['plugins'] is False
            servers = data['mcp_servers']
            assert servers['native-private'] == {'enabled': False}
            profiles.append(servers)
            rows = [{'name': key, 'enabled': spec['enabled'], 'transport': spec}
                    for key, spec in servers.items()]
        return subprocess.CompletedProcess(cmd, 0, json.dumps(rows), '')

    monkeypatch.setattr(subprocess, 'run', run)
    return profiles
