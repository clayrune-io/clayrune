"""MC-1163 persistence, policy, and real lifecycle integration seams."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask, request

from mc import limit_resume as lr
from mc import limit_resume_dispatch as dispatch

RESET = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
DUE = RESET + timedelta(seconds=120)


def session(**overrides):
    return dict(session_id='child', project_id='p', source='agent',
                trigger_type='dispatch', started_at='start', status='error',
                task='original task', provider='claude', mode='B',
                claude_session_id='native', log_lines=[], **overrides)


def exhausted(s):
    lr.observe(s, {'resets_at': RESET.isoformat()})
    return s


@pytest.fixture
def store(tmp_path, monkeypatch):
    result = lr.ResumeStore(tmp_path / 'data' / 'limit_resume.json')
    monkeypatch.setattr(lr, '_store', result)
    return result


def test_persistence_restart_and_grace(store):
    s = exhausted(session())
    assert store.park(s)
    assert s['status'] == 'waiting_for_reset'
    reloaded = lr.ResumeStore(store.path)
    assert reloaded.get('child')['resets_at'] == RESET.isoformat()
    assert reloaded.get('child')['snapshot']['task'] == 'original task'
    assert reloaded.claim('child', DUE - timedelta(seconds=1)) is None
    claim = reloaded.claim('child', DUE)
    assert claim['attempts'] == 1
    assert reloaded.claim('child', DUE) is None
    assert lr.ResumeStore(store.path).get('child')['phase'] == 'submitting'
    assert store.path.parent.name != 'projects'


def test_overlapping_stores_claim_only_once(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    store.park(exhausted(session()))
    other = lr.ResumeStore(store.path)  # both start with the same waiting row
    barrier = Barrier(2)
    def claim(candidate):
        barrier.wait(timeout=5)
        return candidate.claim('child', DUE)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [store, other]))
    assert sum(result is not None for result in results) == 1
    assert lr.ResumeStore(store.path).get('child')['attempts'] == 1


def test_stale_store_cannot_overwrite_cancellation(store):
    store.park(exhausted(session()))
    other = lr.ResumeStore(store.path)
    assert store.cancel('child', 'p')
    assert other.claim('child', DUE) is None
    assert not other.park(exhausted(session()))


def test_two_resumes_survive_restarts(store, monkeypatch):
    s = exhausted(session())
    assert store.park(s)
    for count in (1, 2):
        assert store.claim('child', DUE)['attempts'] == count
        lr.new_turn(s, internal=True)
        store.submitted('child')
        store = lr.ResumeStore(store.path)
        monkeypatch.setattr(lr, '_store', store)
        s['status'] = 'error'
        exhausted(s)
        assert store.park(s) is (count < 2)
    assert store.claim('child', DUE) is None


def test_successful_intermediate_turn_does_not_replenish_budget(store):
    s = exhausted(session())
    store.park(s)
    store.claim('child', DUE)
    lr.new_turn(s, internal=True)
    store.submitted('child')
    s['status'] = 'idle'
    lr.observe_claude(s, {'type': 'result', 'is_error': False})
    assert not lr.hold_completion(s, {})
    assert store.get('child')['attempts'] == 1
    s['status'] = 'error'
    assert store.park(exhausted(s))
    assert store.claim('child', DUE)['attempts'] == 2


@pytest.mark.parametrize('fields', [
    {'source': 'ui', 'trigger_type': 'manual'},
    {'_turn_owner': 'human'}, {'incognito': True},
    {'waiting_for_question': True}, {'waiting_for_plan_approval': True},
    {'status': 'stopped'}, {'_limit_resume_cancelled': True},
])
def test_attended_and_gated_exclusions(store, fields):
    s = session()
    s.update(fields)
    assert not store.park(exhausted(s))
    assert not store.path.exists()


@pytest.mark.parametrize('source,trigger', [
    ('agent', 'dispatch'), ('schedule', 'schedule'), ('', 'schedule'), ('', 'steward')])
def test_unattended_sources(store, source, trigger):
    s = session()
    s.update(source=source, trigger_type=trigger)
    assert store.park(exhausted(s))


@pytest.mark.parametrize('reset', [None, '', 'tomorrow', '2030-01-01T12:00:00', True, float('inf')])
def test_missing_or_invalid_reset(store, reset):
    s = session()
    lr.observe(s, {'resets_at': reset})
    assert not store.park(s)


def test_global_allowance_alone_does_not_park(store):
    assert not store.park(session())
    assert not store.park(exhausted(session()), enabled=False)


def test_claude_real_rejected_event_only(store):
    s = session()
    msg = {'type': 'rate_limit_event', 'rate_limit_info': {
        'status': 'allowed', 'resetsAt': RESET.timestamp()}}
    lr.observe_claude(s, msg)
    assert not store.park(s)
    msg['rate_limit_info']['status'] = 'rejected'
    lr.observe_claude(s, msg)
    assert store.park(s)


def test_success_clears_advisory_exhaustion(store):
    s = exhausted(session())
    lr.observe_claude(s, {'type': 'result', 'is_error': False})
    assert not store.park(s)


def test_failed_write_does_not_publish_wait(store, monkeypatch):
    monkeypatch.setattr(lr, 'write_json_atomic', lambda *a, **k: (_ for _ in ()).throw(OSError('disk full')))
    s = exhausted(session())
    assert not lr.hold_completion(s, {})
    assert s['status'] == 'error'
    assert store.get('child') is None


def test_existing_wait_stays_held_when_state_read_fails(store, monkeypatch):
    s = exhausted(session())
    store.park(s)
    monkeypatch.setattr(store, 'get', lambda sid: (_ for _ in ()).throw(OSError('locked state')))
    assert lr.hold_completion(s, {})
    assert s['status'] == 'waiting_for_reset'


def test_corrupt_store_fails_closed(store, monkeypatch):
    store.path.parent.mkdir(parents=True)
    store.path.write_text('{bad', encoding='utf-8')
    lr.wire(store.path)
    assert lr.store() is None
    assert store.path.read_text(encoding='utf-8') == '{bad'


def test_cancel_is_durable_and_scoped(store):
    s = exhausted(session())
    assert store.park(s)
    assert not store.cancel('child', 'other-project')
    assert store.cancel('child', 'p')
    assert lr.ResumeStore(store.path).claim('child', DUE) is None
    assert not store.park(s)
    lr.new_turn(s)
    s['status'] = 'error'
    assert store.park(exhausted(s))


@pytest.fixture
def integration(store, monkeypatch, tmp_path):
    import server
    from mc.blueprints import agent_routes as ar
    # Importing server wires production singletons; restore our isolated store.
    monkeypatch.setattr(lr, '_store', store)
    sessions = {}
    monkeypatch.setattr(ar, 'agent_sessions', sessions)
    monkeypatch.setattr(ar.state, 'CONFIG', {'limit_resume_enabled': True})
    monkeypatch.setattr(ar, 'load_project', lambda pid: {'id': 'p', 'project_path': str(tmp_path)} if pid == 'p' else None)
    app = Flask('reset-test')
    app.register_blueprint(dispatch.bp)
    monkeypatch.setattr(ar, '_delegation_app', app)
    monkeypatch.setattr(ar.get_manager('p'), 'ensure_guardian', lambda: None)
    return SimpleNamespace(ar=ar, app=app, sessions=sessions, store=store)


def test_completion_holds_merge_and_parent_notification(integration, monkeypatch):
    ar = integration.ar
    s = exhausted(session())
    calls = []
    monkeypatch.setattr(ar, '_log_agent_completion_body', lambda s: calls.append('body/merge'))
    monkeypatch.setattr(ar, '_notify_agent_spawner', lambda *a: calls.append('parent'))
    s['_notify_session'] = 'parent'
    ar._maybe_notify_spawner(s, 'limit error')
    ar._log_agent_completion(s)
    assert calls == []
    assert s['status'] == 'waiting_for_reset'


def test_restart_tick_same_session_and_context(integration, monkeypatch):
    env = integration
    s = exhausted(session())
    s.update(_agent_cwd='isolated-worktree', _notify_session='parent',
             character={'name': 'wren', 'scope': 'global'}, _worktree_isolated=True, housekeeping=True)
    env.store.park(s)
    monkeypatch.setattr(lr, '_store', lr.ResumeStore(env.store.path))
    calls = []
    def followup(pid):
        data = request.get_json()
        calls.append((pid, data, request.environ.get('clayrune.limit_resume')))
        live = env.sessions[data['session_id']]
        assert live['_agent_cwd'] == 'isolated-worktree'
        assert live['_notify_session'] == 'parent'
        assert live['claude_session_id'] == 'native'
        assert live['character']['name'] == 'wren'
        assert live['housekeeping'] is True
        lr.new_turn(live, internal=True)
        live['status'] = 'running'
        return {'ok': True, 'session_id': data['session_id']}
    monkeypatch.setattr(env.ar, 'agent_followup', followup)
    dispatch.tick(DUE - timedelta(seconds=1))
    assert env.sessions['child']['status'] == 'waiting_for_reset'
    assert calls == []
    dispatch.tick(DUE)
    dispatch.tick(DUE)
    assert len(calls) == 1
    assert calls[0][0] == 'p' and calls[0][2] is True
    assert calls[0][1]['message'] == lr.CONTINUATION
    assert lr.store().get('child')['attempts'] == 1


@pytest.mark.parametrize('flag', ['limit_resume_enabled', 'scheduler_paused'])
def test_tick_obeys_config_and_scheduler_pause(integration, monkeypatch, flag):
    env = integration
    env.store.park(exhausted(session()))
    env.ar.state.CONFIG[flag] = flag == 'scheduler_paused'
    monkeypatch.setattr(env.ar, 'agent_followup', lambda pid: pytest.fail('must not submit'))
    dispatch.tick(DUE)
    assert env.store.get('child')['attempts'] == 0


def test_uncertain_submit_is_not_replayed_after_restart(integration, monkeypatch):
    env = integration
    env.store.park(exhausted(session()))
    def fail(pid):
        raise RuntimeError('pipe write outcome unknown')
    monkeypatch.setattr(env.ar, 'agent_followup', fail)
    dispatch.tick(DUE)
    env.sessions.clear()
    monkeypatch.setattr(lr, '_store', lr.ResumeStore(env.store.path))
    monkeypatch.setattr(env.ar, 'agent_followup', lambda pid: pytest.fail('no duplicate'))
    dispatch.tick(DUE)
    assert lr.view(env.sessions['child'])['phase'] == 'submitting'
    # An exit replay of the pre-submit snapshot must not re-arm its old event.
    restored = env.sessions['child']
    restored['status'] = 'error'
    assert lr.hold_completion(restored, {})
    assert lr.store().get('child')['phase'] == 'submitting'
    assert lr.store().claim('child', DUE) is None


def test_cancel_route_and_floor_projection(integration, monkeypatch):
    from mc.blueprints import floor_routes as floor
    env = integration
    env.store.park(exhausted(session()))
    dispatch.restore_waits(env.ar)
    live = env.sessions['child']
    # The real room filter retains the otherwise-terminal paused session.
    monkeypatch.setattr(floor, 'agent_sessions', env.sessions)
    rooms = floor._live_sessions({}, {}, {})
    assert rooms['p'][0]['limit_resume']['resume_at'] == DUE.isoformat()
    monkeypatch.setattr(env.ar, '_log_agent_completion', lambda s: None)
    client = env.app.test_client()
    assert client.post('/api/project/other/agent/child/limit-resume/cancel').status_code == 404
    assert client.post('/api/project/p/agent/child/limit-resume/cancel').status_code == 200
    assert live['status'] == 'stopped'
    assert env.store.claim('child', DUE) is None


def test_human_message_cancels_even_if_allowance_refuses(integration, monkeypatch):
    env = integration
    env.store.park(exhausted(session()))
    dispatch.restore_waits(env.ar)
    monkeypatch.setattr(env.ar, '_update_agent_log', lambda *a, **k: None)
    with env.app.test_request_context(headers={'Origin': 'http://localhost:5199'}):
        env.ar._mark_human_joined('p', 'child')
    assert not lr.unattended(env.sessions['child'])
    assert env.store.get('child')['phase'] == 'cancelled'


def test_workflow_boot_and_reconcile_keep_wait_in_flight(integration):
    from mc import workflows
    integration.store.park(exhausted(session()))
    assert workflows._step_session_verdict(
        {'session_id': 'child', 'project_id': 'p'}, use_live=False) == (
            'in_flight', 'waiting_for_reset', '')


def test_attempt_cap_releases_normal_completion(integration, monkeypatch):
    env = integration
    s = exhausted(session())
    env.store.park(s)
    for _ in range(2):
        env.store.claim('child', DUE)
        env.store.submitted('child')
        s['status'] = 'error'
        env.store.park(s)
    calls = []
    monkeypatch.setattr(env.ar, '_log_agent_completion_body', lambda s: calls.append('completed'))
    monkeypatch.setattr(env.ar, '_notify_spawner_unless_jobs_open', lambda *a: None)
    monkeypatch.setattr(env.ar._agent_shim_completion, 'begin', lambda *a: None)
    monkeypatch.setattr(env.ar._agent_shim_completion, 'ack', lambda *a, **k: None)
    env.ar._log_agent_completion(s)
    assert calls == ['completed']


def test_real_native_followup_preserves_owner_and_attempt_count(integration, monkeypatch):
    env = integration
    s = exhausted(session())
    s.update(provider='codex', mode='A', provider_session_id='native-codex',
             _turn_owner='spawner', proc=None)
    env.sessions['child'] = s
    env.store.park(s)
    ar = env.ar
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_observe_habit_cue', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_allocate_delegation_turn', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_write_usage_breakdown_turn_start_checkpoint', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_mode_a_token_rollover', lambda *args: args[-1])
    monkeypatch.setattr(ar, '_clock_wrap', lambda p, s, msg, *a: msg)
    monkeypatch.setattr(ar, '_bridge_images_for_blind_model', lambda msg, *a, **k: msg)
    monkeypatch.setattr(ar._native_followup_context, 'refresh_stash', lambda *a, **k: None)
    monkeypatch.setattr(ar._native_followup_context, 'message_for_turn', lambda p, s, msg: msg)
    calls = []
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda p: SimpleNamespace(
        write_followup=lambda handle, msg: calls.append((handle, msg))))
    logs = []
    monkeypatch.setattr(dispatch, '_log', lambda msg, **k: logs.append(msg))
    dispatch.tick(DUE)
    assert len(calls) == 1, (logs, s['log_lines'], env.store.get('child'))
    assert calls[0][0].mc_session_id == 'child'
    assert calls[0][0].session_dict['provider_session_id'] == 'native-codex'
    assert s['_turn_owner'] == 'spawner'
    assert env.store.get('child')['attempts'] == 1
    assert env.store.get('child')['phase'] == 'active'
    assert '_limit_resume_exhaustion' not in s


def test_normal_stop_handles_restored_wait_without_process(integration, monkeypatch):
    env = integration
    env.store.park(exhausted(session()))
    dispatch.restore_waits(env.ar)
    monkeypatch.setattr(env.ar, '_log_agent_completion', lambda s: None)
    with env.app.test_request_context(json={'session_id': 'child'}):
        response = env.ar.agent_stop('p')
    assert response.get_json()['ok']
    assert env.sessions['child']['status'] == 'stopped'
    assert env.store.claim('child', DUE) is None


def test_close_tab_cancels_restored_wait(integration, monkeypatch):
    env = integration
    env.store.park(exhausted(session()))
    dispatch.restore_waits(env.ar)
    monkeypatch.setattr(env.ar, '_log_agent_completion', lambda s: None)
    with env.app.test_request_context(json={'session_id': 'child'}):
        response = env.ar.agent_session_delete('p')
    assert response.get_json()['ok']
    assert 'child' not in env.sessions
    dispatch.tick(DUE)
    assert 'child' not in env.sessions


@pytest.mark.parametrize('mode', ['A', 'B'])
def test_actual_claude_reader_parks_at_error_result(integration, monkeypatch, mode):
    from tests.test_stream_reader_malformed import _FakeProc
    env = integration
    s = session()
    s.update(mode=mode, status='running')
    proc = _FakeProc([
        json.dumps({'type': 'rate_limit_event', 'rate_limit_info': {
            'status': 'rejected', 'resetsAt': RESET.timestamp()}}),
        json.dumps({'type': 'result', 'is_error': True, 'num_turns': 1})])
    proc._rc = 1
    s['proc'] = proc
    env.sessions['child'] = s
    monkeypatch.setattr(env.ar, '_capture_system_init', lambda msg: None)
    monkeypatch.setattr(env.ar, '_write_usage_breakdown_turn_checkpoint', lambda *a: None)
    monkeypatch.setattr(env.ar, '_maybe_checkpoint', lambda *a: None)
    monkeypatch.setattr(env.ar, '_log_agent_completion_body', lambda s: pytest.fail('no terminal completion'))
    monkeypatch.setattr(env.ar._agent_shim_completion, 'begin', lambda *a: pytest.fail('no completion claim'))
    reader = env.ar._read_agent_stream_b if mode == 'B' else env.ar._read_agent_stream
    reader(proc, s)
    assert not any('[stream error:' in line for line in s['log_lines']), s['log_lines']
    assert s['status'] == 'waiting_for_reset'
    assert env.store.get('child')['attempts'] == 0
