"""MC-1071: stateless weekly policy and dispatch integration without spawning."""

from datetime import datetime, timedelta, timezone

import pytest

from mc import engine_pace as pace
from mc import engine_pace_dispatch as adapter
from tests.test_revive_notify_carry import ar, _project  # noqa: F401


NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
CHARACTER = {'scope': 'global', 'name': 'builder', 'engine': {'provider': 'claude'}}
CONFIG = {'engine_pace_alternates': {'global:builder': 'global:alternate'}}


def usage(percent, remaining_days=3.5):
    return {'claude': {'utilization': percent,
                       'resets_at': (NOW + timedelta(days=remaining_days)).isoformat()}}


@pytest.mark.parametrize('percent,remaining,reroute', [
    (45, 3.5, False),  # on pace
    (55, 3.5, False),  # exactly at margin
    (55.1, 3.5, True), # ahead by more than margin
    (85, 0.1, False),  # exactly at ceiling, behind pace
    (85.1, 0.1, True), # over ceiling while behind pace
    (100, 0, False),   # reset invalidates old reading
    (100, -1, False),
    (100, 8, False),   # impossible seven-day window
    (5, 7, False),
    (6, 7, True),
])
def test_pace_boundaries(percent, remaining, reroute):
    selected, reason = pace.decide(CHARACTER, usage(percent, remaining), NOW, CONFIG)
    assert selected == ('global:alternate' if reroute else 'global:builder')
    assert bool(reason) is reroute


def test_reasons_and_custom_thresholds():
    assert 'ceiling' in pace.decide(CHARACTER, usage(90, 0.1), NOW, CONFIG)[1]
    assert 'elapsed' in pace.decide(CHARACTER, usage(60), NOW, CONFIG)[1]
    config = dict(CONFIG, engine_pace_margin_points=20, engine_pace_ceiling_percent=95)
    assert pace.decide(CHARACTER, usage(60), NOW, config) == ('global:builder', None)


@pytest.mark.parametrize('config', [
    {}, {'engine_pace_alternates': {}},
    dict(CONFIG, engine_pace_enabled=False),
    {'engine_pace_alternates': {'global:builder': 'global:builder'}},
    {'engine_pace_alternates': {'global:builder': 'typo'}},
    {'engine_pace_alternates': []},
    dict(CONFIG, engine_pace_margin_points='invalid'),
    dict(CONFIG, engine_pace_ceiling_percent=-1),
])
def test_no_alternate_or_disabled_or_invalid_config(config):
    assert pace.decide(CHARACTER, usage(99), NOW, config) == ('global:builder', None)


@pytest.mark.parametrize('reading', [
    {}, {'codex': {'utilization': 99}}, {'claude': None},
    {'claude': {'utilization': 99}},
    {'claude': {'utilization': 99, 'resets_at': 'bad'}},
    {'claude': {'utilization': 99, 'resets_at': '2026-10-08T12:00:00'}},
    *[usage(p) for p in (None, '99', True, float('nan'), float('inf'), -1, 101)],
])
def test_missing_or_invalid_usage_fails_open(reading):
    assert pace.decide(CHARACTER, reading, NOW, CONFIG) == ('global:builder', None)


def test_no_sticky_routing_state():
    assert pace.decide(CHARACTER, usage(90), NOW, CONFIG)[0] == 'global:alternate'
    assert pace.decide(CHARACTER, usage(40), NOW, CONFIG)[0] == 'global:builder'
    assert pace.decide(CHARACTER, usage(90, 0), NOW, CONFIG)[0] == 'global:builder'


def prep(monkeypatch, reading, **kwargs):
    monkeypatch.setattr(adapter, 'provider_usage', lambda provider: reading)
    logs = []
    monkeypatch.setattr(adapter, '_log', lambda message, **kw: logs.append(message))
    meta = dict(CHARACTER)
    resolve = lambda pp, ref, **kw: (meta, 'persona') if ref != 'global:alternate' else (None, '')
    options = dict(resume_id='', provider_override='', model_override='', effort_override=None,
                   config=CONFIG, load_project=lambda _: {'project_path': 'test'},
                   resolve_character=resolve)
    options.update(kwargs)
    result = adapter.prepare('p1', 'global:builder', **options)
    return result, logs


def test_missing_usage_is_logged(monkeypatch):
    result, logs = prep(monkeypatch, {})
    assert result == ('global:builder', {})
    assert 'weekly usage missing' in logs[0]


def test_deleted_alternate_keeps_original(monkeypatch):
    future = {'claude': {'utilization': 99, 'resets_at':
                        (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}}
    result, logs = prep(monkeypatch, future)
    assert result == ('global:builder', {})
    assert 'alternate' in logs[0] and 'unavailable' in logs[0]


@pytest.mark.parametrize('pin', [
    {'resume_id': 'original'}, {'provider_override': 'claude'},
    {'model_override': 'opus'}, {'effort_override': 'high'},
    {'config': dict(CONFIG, engine_pace_enabled=False)}, {'config': {}},
])
def test_resume_and_explicit_engine_choices_do_not_read_usage(monkeypatch, pin):
    def no_read(_):
        pytest.fail('usage should not be read')
    monkeypatch.setattr(adapter, 'provider_usage', no_read)
    options = dict(resume_id='', provider_override='', model_override='', effort_override=None,
                   config=CONFIG, load_project=lambda _: {}, resolve_character=lambda *a, **k: None)
    options.update(pin)
    assert adapter.prepare('p1', 'global:builder', **options) == ('global:builder', {})


def test_reader_exception_is_logged_and_keeps_original(monkeypatch):
    def fail(_):
        raise OSError('usage read failed')
    options = dict(resume_id='', provider_override='', model_override='', effort_override=None,
                   config=CONFIG, load_project=lambda _: {},
                   resolve_character=lambda *a, **k: (CHARACTER, 'persona'))
    logs = []
    monkeypatch.setattr(adapter, 'provider_usage', fail)
    monkeypatch.setattr(adapter, '_log', lambda message, **kw: logs.append(message))
    assert adapter.prepare('p1', 'global:builder', **options) == ('global:builder', {})
    assert 'usage read failed' in logs[0]


def test_dispatch_route_uses_alternate_engine_and_discloses(ar, tmp_path, monkeypatch):
    import server
    from mc import characters
    project = _project(tmp_path)
    monkeypatch.setattr(characters, 'GLOBAL_AGENTS_DIR', tmp_path / 'characters')
    characters.write_character('global', 'builder', 'Build', 'Original persona',
                               engine={'provider': 'claude', 'model': 'opus'})
    characters.write_character('global', 'alternate', 'Build', 'Alternate persona',
                               engine={'provider': 'codex', 'model': 'gpt-5.4'})
    monkeypatch.setattr(ar, 'load_project', lambda _: project)
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_maybe_isolate_worktree', lambda *a: (project['project_path'], False))
    monkeypatch.setitem(ar.state.CONFIG, 'engine_pace_alternates', CONFIG['engine_pace_alternates'])
    monkeypatch.setitem(ar.state.CONFIG, 'engine_pace_enabled', True)
    monkeypatch.setattr(adapter, 'provider_usage', lambda _: {
        'claude': {'utilization': 90, 'resets_at':
                   (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()}})
    calls = []
    def spawn(p, task, **kw):
        calls.append((p, task, kw))
        return 'pace-session'
    monkeypatch.setattr(ar, '_dispatch_via_runtime', spawn)
    with server.app.test_client() as client:
        response = client.post('/api/project/p1/agent/dispatch', json={
            'task': 'Build the ticket', 'character': 'global:builder',
            'notify_session': 'parent-session', 'source': 'agent'})
    assert response.status_code == 200, response.get_json()
    payload = response.get_json()
    assert payload['rerouted_from'] == 'global:builder'
    assert 'ceiling' in payload['reroute_reason']
    assert payload['session_id'] == 'pace-session'
    assert len(calls) == 1
    p, task, kw = calls[0]
    assert p['provider'] == kw['provider_name'] == 'codex'
    assert kw['model_override'] == 'gpt-5.4'
    assert kw['character_meta']['name'] == 'alternate'
    assert 'Alternate persona' in kw['character_body']
    assert kw['notify_session'] == 'parent-session'
    assert kw['trigger_type'] == 'dispatch'
    assert task == 'Build the ticket'
    assert kw['display_task'].startswith('[Pace routing: global:builder -> global:alternate;')
    assert payload['reroute_reason'] in kw['display_task']


def test_dispatch_without_mapping_omits_reroute_fields(ar, monkeypatch):
    import server
    calls = []
    monkeypatch.setitem(ar.state.CONFIG, 'engine_pace_alternates', {})
    monkeypatch.setattr(ar, '_dispatch_agent_internal',
                        lambda *args, **kw: calls.append(kw) or 'original-session')
    with server.app.test_client() as client:
        response = client.post('/api/project/p1/agent/dispatch', json={
            'task': 'Build', 'character': 'global:builder'})
    assert response.status_code == 200
    assert response.get_json() == {'ok': True, 'session_id': 'original-session'}
    assert calls[0]['character'] == 'global:builder'
    assert calls[0]['display_task'] == 'Build'


@pytest.mark.parametrize('provider', ['claude', 'codex', 'gemini'])
def test_adapter_uses_existing_weekly_sources(monkeypatch, provider):
    from mc.blueprints import system_routes
    weekly = usage(70)['claude']
    monkeypatch.setattr(system_routes, '_fetch_oauth_usage_limits',
                        lambda: {'seven_day': weekly})
    monkeypatch.setattr(system_routes, '_fetch_codex_weekly_usage', lambda: weekly)
    assert adapter.provider_usage(provider) == ({provider: weekly} if provider != 'gemini' else {})
