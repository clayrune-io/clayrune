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


def test_session_scan_survives_a_dispatch_mid_iteration(monkeypatch):
    from mc.blueprints import agent_routes as routes

    class DispatchingSession(dict):
        def get(self, key, default=None):
            sessions.setdefault('new-dispatch', {})
            return super().get(key, default)

    sessions = {
        'first': DispatchingSession(project_id='other'),
        'second': {'project_id': 'sample', 'claude_session_id': 'chat', 'context_tokens': 12},
    }
    monkeypatch.setattr(routes, 'agent_sessions', sessions)
    assert routes._live_context_tokens('sample', 'chat') == 12


def test_handoff_ignores_finished_children_but_keeps_pending_background_work(monkeypatch):
    from mc.blueprints import agent_routes as routes

    sessions = {}
    for status in ('running', 'error', 'stopped', 'completed', 'idle', 'idle-background'):
        sessions[status] = {
            'project_id': 'sample', '_notify_session': 'parent',
            'session_id': status, 'status': status.removesuffix('-background'),
        }
    monkeypatch.setattr(routes, 'agent_sessions', sessions)
    monkeypatch.setattr(routes._bg_tasks, 'held_tasks', lambda s: s['session_id'] == 'idle-background')
    children = routes._in_flight_children('sample', 'parent')
    assert [child['session_id'] for child in children] == ['running', 'idle-background']


def test_guardian_compares_only_fresh_samples_from_same_process(monkeypatch):
    import psutil
    from types import SimpleNamespace
    from mc.blueprints import agent_routes as routes

    totals = {1: 100, 2: 3}

    def process(pid):
        return SimpleNamespace(
            cpu_times=lambda: SimpleNamespace(user=totals[pid], system=0),
            children=lambda **kwargs: [],
        )

    monkeypatch.setattr(psutil, 'Process', process)
    first, second = SimpleNamespace(pid=1), SimpleNamespace(pid=2)
    session = {}
    assert not routes._proc_is_cpu_idle(session, first, 0)
    assert not routes._proc_is_cpu_idle(session, second, 10)
    assert routes._proc_is_cpu_idle(session, second, 20)
    assert not routes._proc_is_cpu_idle(session, second, 100)
    totals[2] = 1
    assert not routes._proc_is_cpu_idle(session, second, 110)


def test_floor_identity_uses_configured_user_name(tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as routes

    monkeypatch.setattr(routes, 'DATA_DIR', tmp_path / 'projects')
    monkeypatch.setattr(routes, 'SHARED_RULES_PATH', tmp_path / 'rules')
    monkeypatch.setattr(routes, '_get_memory_path', lambda p: tmp_path / 'memory')
    monkeypatch.setattr(routes, '_get_archive_path', lambda p: tmp_path / 'archive')
    monkeypatch.setattr(routes, '_load_agent_log', lambda p: [])
    monkeypatch.setattr(routes, '_log', lambda *a, **k: None)
    monkeypatch.setitem(routes.state.CONFIG, 'user_name', 'Example')
    prompt = routes._build_agent_context(
        {'id': 'sample'}, session_id='example-session', source='agent')
    assert 'would tell Example at a glance' in prompt


def test_claude_context_uses_native_skill_discovery_on_codex_project(tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as routes

    monkeypatch.setattr(routes, 'DATA_DIR', tmp_path / 'projects')
    monkeypatch.setattr(routes, 'SHARED_RULES_PATH', tmp_path / 'rules')
    monkeypatch.setattr(routes, '_get_memory_path', lambda p: tmp_path / 'memory')
    monkeypatch.setattr(routes, '_get_archive_path', lambda p: tmp_path / 'archive')
    monkeypatch.setattr(routes, '_load_agent_log', lambda p: [])
    monkeypatch.setattr(routes, '_log', lambda *a, **k: None)
    catalog_reads = []
    monkeypatch.setattr(routes._skills, 'list_skills', lambda *a: catalog_reads.append(a) or [])
    routes._build_agent_context({'id': 'sample', 'provider': 'codex'}, provider='claude')
    assert catalog_reads == []



def test_stop_session_hands_off_stdin_close(monkeypatch):
    from types import SimpleNamespace
    from mc.blueprints import agent_routes as routes

    monkeypatch.setattr(routes, '_unregister_process', lambda pid: None)
    calls = []
    monkeypatch.setattr(routes, '_bounded_stdin_close', lambda proc, **kwargs: calls.append(proc))
    proc = SimpleNamespace(pid=123)
    session = {'proc': proc, 'mode': 'B', 'log_lines': [], 'process_alive': True}
    routes._stop_session(session, 'example')
    assert calls == [proc]
    assert session['status'] == 'stopped'


def test_prior_provider_finds_claude_lineage(monkeypatch):
    from mc.blueprints import agent_routes as routes

    monkeypatch.setattr(routes, 'agent_sessions', {})
    monkeypatch.setattr(routes, '_load_agent_log', lambda pid: [{
        'provider': 'claude', 'claude_session_id': 'current-chat',
        'claude_session_ids': ['earlier-chat', 'current-chat'],
    }])
    assert routes._prior_conversation_provider('sample', 'earlier-chat') == 'claude'


def test_superseded_mode_a_reader_leaves_resume_recovery_to_owner(monkeypatch):
    from types import SimpleNamespace
    from mc.blueprints import agent_routes as routes

    recoveries = []
    monkeypatch.setattr(routes, '_unregister_process', lambda pid: None)
    monkeypatch.setattr(routes._credits_session, 'replay_after_exit', lambda *a: False)
    monkeypatch.setattr(routes, '_auto_recover_failed_resume', recoveries.append)
    proc = SimpleNamespace(pid=123, stdout=(), wait=lambda: 1)
    session = {
        'project_id': 'sample', 'status': 'error', 'proc': object(),
        '_resume_id': 'prior', '_dispatch_time': routes._time.time(),
    }
    routes._read_agent_stream(proc, session)
    assert recoveries == []
