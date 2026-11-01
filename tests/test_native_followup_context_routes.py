"""BUILTINS-2: real route/build/fingerprint/runtime delivery across turns."""
import threading
from types import SimpleNamespace

import pytest

from test_builtins_resume_context import spawns  # noqa: F401 -- shared pipe recorder


@pytest.fixture(params=['codex', 'gemini', 'qwen'])
def engine(request):
    return request.param


@pytest.fixture(params=['followup', 'interrupt'])
def endpoint(request):
    return request.param


@pytest.fixture
def route_env(tmp_path, monkeypatch, spawns, engine, endpoint):
    import server
    from mc import characters, memory, skills, state
    from mc.blueprints import agent_routes as routes, local_auth

    project_path = tmp_path / 'project'
    agents = project_path / '.claude' / 'agents'
    agents.mkdir(parents=True)
    persona = agents / 'sol.md'
    persona.write_text('---\nname: sol\nagent_name: Sol\n---\nPERSONA_ALPHA', encoding='utf8')
    rules = project_path / 'AGENT_RULES.md'
    rules.write_text('RULES_ALPHA', encoding='utf8')
    vault = tmp_path / 'memory'
    vault.mkdir()
    memory_path = vault / 'MEMORY.md'
    memory_path.write_text(memory._mem_compose('# Index', [], []), encoding='utf8')
    shared_rules = tmp_path / 'SHARED_RULES.md'
    shared_rules.write_text('COMMON_CONDUCT', encoding='utf8')
    project = {'id': 'stable-native', 'name': 'Native context test',
               'project_path': str(project_path), 'provider': engine,
               'activity_log': [{'ts': 'before', 'msg': 'initial activity'}]}
    session = {'session_id': 'native-test', 'project_id': project['id'],
               'provider': engine, 'provider_session_id': 'native-thread',
               'mode': 'A', 'status': 'idle', 'task': 'initial unrelated task',
               'trigger_type': 'manual', 'log_lines': [], 'proc': None,
               'character': {'name': 'sol', 'scope': 'project'}}

    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    monkeypatch.setattr(memory, '_get_memory_path', lambda p: memory_path)
    monkeypatch.setattr(memory, '_get_archive_path', lambda p: vault / 'MEMORY_ARCHIVE.md')
    monkeypatch.setattr(routes, '_get_memory_path', lambda p: memory_path)
    monkeypatch.setattr(routes, '_memory_search', memory._memory_search)
    monkeypatch.setattr(routes, 'SHARED_RULES_PATH', shared_rules)
    monkeypatch.setattr(skills, 'GLOBAL_SKILLS_DIR', tmp_path / 'global-skills')
    monkeypatch.setattr(characters, 'GLOBAL_AGENTS_DIR', tmp_path / 'global-agents')
    monkeypatch.setattr(routes, 'load_project', lambda pid: project)
    manager = SimpleNamespace(lock=threading.RLock(), session_ids=set(), ensure_guardian=lambda: None)
    monkeypatch.setattr(routes, 'get_manager', lambda pid: manager)
    monkeypatch.setattr(routes, '_allowance_refusal', lambda *a, **k: '')
    monkeypatch.setattr(routes, '_allowance_conflict_block', lambda *a, **k: '')
    monkeypatch.setattr(routes, '_mark_human_joined', lambda *a: None)
    monkeypatch.setattr(routes, '_observe_habit_cue', lambda *a: None)
    monkeypatch.setattr(routes, '_write_usage_breakdown_turn_start_checkpoint', lambda *a: None)
    monkeypatch.setattr(routes, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(routes, '_bridge_images_for_blind_model', lambda message, *a, **k: message)
    monkeypatch.setattr(routes._agent_runtime.get_runtime(engine), 'resolve_binary', lambda: engine)
    for key, value in {
        'memory_turn_refresh_enabled': True, 'memory_turn_cold_probe_enabled': False,
        'negation_ledger_enabled': False, 'exploration_readback_enabled': False,
        'coordination_enabled': False, 'read_floor_topk': 6, 'read_floor_link_expand': 0,
        'context_profile_overrides': {'*': 'full'}, 'delivery_telemetry_enabled': True,
        'vendor_context_sync_enabled': False,
    }.items():
        monkeypatch.setitem(state.CONFIG, key, value)

    # Live activity is deliberately volatile, just like production. Its text
    # changes the real builder's output without changing project input files.
    def log_activity(pid, text):
        project['activity_log'].insert(0, {'ts': 'later', 'msg': text})

    monkeypatch.setattr(routes, '_log_agent_activity', log_activity)
    real_fresh = routes._fresh_context_for
    rebuilds = []

    def fresh(*args, **kwargs):
        rebuilds.append(kwargs.get('task', ''))
        return real_fresh(*args, **kwargs)

    monkeypatch.setattr(routes, '_fresh_context_for', fresh)
    snapshot = dict(state.agent_sessions)
    state.agent_sessions.clear()
    state.agent_sessions[session['session_id']] = session
    monkeypatch.setitem(server.app.config, 'TESTING', True)
    client = server.app.test_client()

    def send(message):
        response = client.post(f'/api/project/{project["id"]}/agent/{endpoint}',
                               json={'session_id': session['session_id'], 'message': message})
        assert response.status_code == 200, response.get_data(as_text=True)
        active = state.agent_sessions[session['session_id']]
        assert active['status'] != 'error', active['log_lines']
        cmd, proc = spawns[-1]
        assert 'native-thread' in cmd
        return proc.stdin.getvalue()

    try:
        yield SimpleNamespace(send=send, session=session, project=project, rules=rules,
                              persona=persona, vault=vault, routes=routes, engine=engine,
                              rebuilds=rebuilds, fresh=real_fresh)
    finally:
        state.agent_sessions.clear()
        state.agent_sessions.update(snapshot)


def test_different_messages_reuse_real_context_and_send_bare_second_turn(route_env):
    e = route_env
    first = e.send('alpha request')
    assert 'CURRENT CLAYRUNE CONTEXT' in first
    assert 'PERSONA_ALPHA' in first and 'RULES_ALPHA' in first
    stash = e.session['_system_prompt']
    fp = e.session['_system_prompt_fp']
    # Demonstrate that the old unconditional rebuild WOULD change the context.
    assert e.fresh(e.project, e.session, task='beta request') != stash
    assert e.routes._context_fingerprint(e.project) == fp
    assert e.send('beta request') == 'beta request'
    assert e.session['_system_prompt'] == stash
    assert e.rebuilds == ['alpha request']


def test_runtime_dispatch_primes_stash_for_first_route_followup(route_env, spawns):
    e = route_env
    meta, body = e.routes._resolve_character(
        e.project['project_path'], 'project:sol', e.project)
    sid = e.routes._dispatch_via_runtime(
        e.project, 'initial task', provider_name=e.engine,
        planned_session_id=e.session['session_id'], resume_id='native-thread',
        character_meta=meta, character_body=body)
    active = e.routes.agent_sessions[sid]
    assert active['_system_prompt_fp'] == e.routes._context_fingerprint(e.project)
    assert 'PERSONA_ALPHA' in spawns[-1][1].stdin.getvalue()
    assert e.send('different next request') == 'different next request'
    assert not e.rebuilds


def test_project_rules_edit_refreshes_once_then_bare(route_env):
    e = route_env
    e.send('alpha request')
    fp = e.session['_system_prompt_fp']
    e.rules.write_text('RULES_BRAVO with a revised constraint', encoding='utf8')
    assert e.routes._context_fingerprint(e.project) != fp
    changed = e.send('beta request')
    assert 'CURRENT CLAYRUNE CONTEXT' in changed and 'RULES_BRAVO' in changed
    assert 'RULES_ALPHA' not in changed
    assert e.session['_system_prompt_fp'] == e.routes._context_fingerprint(e.project)
    assert e.send('gamma request') == 'gamma request'
    assert e.rebuilds == ['alpha request', 'beta request']


def test_persona_body_edit_refreshes_once_then_bare(route_env):
    e = route_env
    e.send('alpha request')
    fp = e.session['_system_prompt_fp']
    e.persona.write_text('---\nname: sol\nagent_name: Sol\n---\nPERSONA_BRAVO revised voice',
                         encoding='utf8')
    assert e.routes._context_fingerprint(e.project) != fp
    changed = e.send('beta request')
    assert 'CURRENT CLAYRUNE CONTEXT' in changed and 'PERSONA_BRAVO' in changed
    assert 'PERSONA_ALPHA' not in changed
    assert e.send('gamma request') == 'gamma request'
    assert e.rebuilds == ['alpha request', 'beta request']


def test_per_message_memory_uses_real_retrieval_without_rebuilding_setup(route_env):
    e = route_env
    for name, text in [('quartz', 'quartz calibration QUARTZ_NOTE'),
                       ('zircon', 'zircon alignment ZIRCON_NOTE')]:
        (e.vault / f'{name}.md').write_text(text, encoding='utf8')
    first = e.send('quartz calibration')
    assert 'QUARTZ_NOTE' in first and 'ZIRCON_NOTE' not in first
    stash = e.session['_system_prompt']
    second = e.send('zircon alignment')
    assert 'ZIRCON_NOTE' in second and second.endswith('zircon alignment')
    assert 'CURRENT CLAYRUNE CONTEXT' not in second
    assert 'PERSONA_ALPHA' not in second and 'RULES_ALPHA' not in second
    assert e.session['_system_prompt'] == stash
    assert e.send('continue zircon alignment') == 'continue zircon alignment'
    assert e.rebuilds == ['quartz calibration']


def test_incognito_never_refreshes_stash_or_retrieves_memory(route_env, monkeypatch, codex_mcp_cli):
    e = route_env
    e.session['incognito'] = True

    def unexpected(*a, **k):
        pytest.fail('incognito must not read project context or per-turn memory')

    monkeypatch.setattr(e.routes, '_fresh_context_for', unexpected)
    monkeypatch.setattr(e.routes._memory_turn, 'refresh_for_turn', unexpected)
    assert e.send('private alpha').endswith('private alpha')
    assert e.send('private beta') == 'private beta'
    assert not e.rebuilds
    if e.engine == 'codex':
        assert len(codex_mcp_cli) == 2
        assert all(not any(spec['enabled'] for spec in servers.values())
                   for servers in codex_mcp_cli)


def test_fingerprint_error_reuses_stash_without_losing_message(route_env, monkeypatch):
    e = route_env
    e.send('alpha request')
    fp = e.session['_system_prompt_fp']

    def broken(project):
        raise OSError('stat unavailable')

    monkeypatch.setattr(e.routes, '_context_fingerprint', broken)
    assert e.send('beta request') == 'beta request'
    assert e.session['_system_prompt_fp'] == fp
    assert e.rebuilds == ['alpha request']


def test_failed_stale_rebuild_preserves_fingerprint_and_retries(route_env, monkeypatch):
    e = route_env
    e.send('alpha request')
    fp = e.session['_system_prompt_fp']
    e.rules.write_text('RULES_BRAVO revised constraint', encoding='utf8')
    rebuild = e.routes._fresh_context_for

    def broken(*a, **k):
        raise OSError('context unavailable')

    monkeypatch.setattr(e.routes, '_fresh_context_for', broken)
    assert e.send('beta request') == 'beta request'
    assert e.session['_system_prompt_fp'] == fp
    monkeypatch.setattr(e.routes, '_fresh_context_for', rebuild)
    assert 'RULES_BRAVO' in e.send('gamma request')
    assert e.send('delta request') == 'delta request'
