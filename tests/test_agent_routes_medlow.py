"""Regression tests for agent session lifecycle fixes."""


def test_corrupt_incognito_record_is_preserved(tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as routes

    path = tmp_path / '_incognito.json'
    path.write_text('{', encoding='utf-8')
    monkeypatch.setattr(routes, 'DATA_DIR', tmp_path)
    monkeypatch.setitem(routes.state.CONFIG, 'auto_workspace_base', str(tmp_path))
    logs = []
    monkeypatch.setattr(routes, '_log', logs.append)
    assert routes._ensure_incognito_project()['_is_incognito_project']
    assert path.read_text(encoding='utf-8') == '{'
    assert any('project record read failed' in line for line in logs)


def test_taskkill_refusal_reports_failure(monkeypatch):
    from types import SimpleNamespace
    from mc.blueprints import agent_routes as routes

    monkeypatch.setattr(routes, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(routes.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1))
    assert routes._kill_pid(123, tree=True) is False


def test_disabled_memory_push_does_not_load_project(monkeypatch):
    from mc.blueprints import agent_routes as routes

    loads = []
    monkeypatch.setattr(routes, 'load_project', loads.append)
    monkeypatch.setattr(routes._memory_push, 'enabled', lambda: False)
    monkeypatch.setattr(routes._memory_push, 'extract_result_query', lambda text: text)
    session = {'project_id': 'sample'}
    routes._observe_memory_push_input(session, 'Read', {'file_path': 'sample.py'})
    routes._observe_memory_push_result(session, 'Read', 'file error')
    assert loads == []
