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


def test_coverage_reads_fresh_and_scheduled_seeds(monkeypatch):
    from mc.blueprints import agent_routes as routes

    monkeypatch.setitem(routes.state.CONFIG, 'user_name', 'Example')
    for seed in ('> Example: open sample.py', '> [scheduled run]: open sample.py'):
        assert routes._coverage_last_user_message({'log_lines': [seed]}) == 'open sample.py'


def test_auth_probe_timeout_clears_stale_success(monkeypatch):
    from mc.blueprints import agent_routes as routes

    def timeout(*args, **kwargs):
        raise routes.subprocess.TimeoutExpired('fixture', 20)

    monkeypatch.setattr(routes, '_claude_auth_state', {'ok': True})
    monkeypatch.setattr(routes, '_merge_registry_path', lambda: None)
    monkeypatch.setattr(routes, '_resolve_claude', lambda: 'fixture')
    monkeypatch.setattr(routes, '_auth_probe_cwd', lambda: None)
    monkeypatch.setattr(routes, '_prune_auth_probe_transcripts', lambda: None)
    monkeypatch.setattr(routes.subprocess, 'run', timeout)
    result = routes._run_claude_auth_probe()
    assert result['ok'] is False
    assert result['reason'] == 'unknown'
    assert result['last_probe_at'] > 0
