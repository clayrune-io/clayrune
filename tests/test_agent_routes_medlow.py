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
