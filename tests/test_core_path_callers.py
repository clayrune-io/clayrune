"""MC-1158: mixed Windows namespaces must still trigger inside-path guards."""
import json
from pathlib import PureWindowsPath
from types import SimpleNamespace

import pytest
from flask import Flask

from mc import core, state


@pytest.fixture
def mixed_namespaces(monkeypatch):
    def apply():
        resolved = iter((PureWindowsPath(r'\\?\C:\store\child'), PureWindowsPath(r'C:\store')))
        monkeypatch.setattr(core, 'Path', lambda _: SimpleNamespace(resolve=lambda: next(resolved)))
        monkeypatch.setattr(core, 'os', SimpleNamespace(name='nt', sep='\\'))
    return apply


def test_project_assignment_still_refuses_inside_install(monkeypatch, mixed_namespaces, tmp_path):
    from mc.blueprints import project_routes

    monkeypatch.setattr(project_routes, '_APP_DIR', tmp_path)
    monkeypatch.setitem(state.CONFIG, 'allow_project_in_install_dir', False)
    mixed_namespaces()
    with Flask(__name__).app_context():
        response, status = project_routes._refuse_project_path_in_install_dir(str(tmp_path / 'child'), None)
        assert status == 400
        assert 'install directory' in response.get_json()['error']


def test_update_warning_still_lists_inside_project(monkeypatch, mixed_namespaces, tmp_path):
    from mc.blueprints import system_routes

    project = {'id': 'inside', 'name': 'Inside', 'project_path': str(tmp_path / 'child')}
    monkeypatch.setattr(system_routes, 'load_projects', lambda: [project])
    monkeypatch.setitem(state.CONFIG, 'allow_project_in_install_dir', False)
    mixed_namespaces()
    assert system_routes._projects_pointing_at_install_dir(tmp_path) == [{'id': 'inside', 'name': 'Inside'}]


def test_store_still_refuses_data_dir_overlap(mixed_namespaces, tmp_path):
    from mc.desk_agent_store import AgentChannelStore

    mixed_namespaces()
    with pytest.raises(ValueError, match='must not overlap DATA_DIR'):
        AgentChannelStore(tmp_path / 'child', data_dir=tmp_path)


def test_scanner_still_excludes_install_dir(mixed_namespaces, tmp_path):
    from mc import claude_projects_scan

    install = tmp_path / 'install'
    install.mkdir()
    transcript_dir = tmp_path / 'claude-home' / 'session'
    transcript_dir.mkdir(parents=True)
    (transcript_dir / 's.jsonl').write_text(json.dumps({'cwd': str(install)}), encoding='utf-8')
    mixed_namespaces()
    result = claude_projects_scan.scan(claude_home=tmp_path / 'claude-home',
                                      home=tmp_path / 'home', app_dir=install)
    assert result['candidates'] == []
    assert result['skipped']['install_dir'] == 1
