"""MC-1163 persistence, policy, and real lifecycle integration seams."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from threading import Event, Thread as RealThread

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
    assert env.store.get('child')['review_required'] is True
    assert env.sessions['child']['status'] == 'waiting_for_reset'
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


def test_human_takeover_allowance_409_clears_reset_status(integration, monkeypatch):
    env = integration
    env.store.park(exhausted(session()))
    dispatch.restore_waits(env.ar)
    monkeypatch.setattr(env.ar, '_update_agent_log', lambda *a, **k: None)
    monkeypatch.setattr(env.ar, '_load_agent_log', lambda *a: [])
    monkeypatch.setattr(env.ar, '_observe_habit_cue', lambda *a, **k: None)
    monkeypatch.setattr(env.ar, '_allowance_refusal', lambda *a, **k: 'still exhausted')
    with env.app.test_request_context(json={'session_id': 'child', 'message': 'take over'},
                                     headers={'Origin': 'http://localhost:5199'}):
        response = env.app.make_response(env.ar.agent_followup('p'))
    assert response.status_code == 409
    live = env.sessions['child']
    assert live['status'] == 'idle'
    assert live['last_status_change_time'] > 0
    assert live['_turn_owner'] == 'human'
    assert env.store.get('child')['phase'] == 'cancelled'
    assert lr.view(live) is None
    assert not lr.hold_completion(live, {})
    dispatch.tick(DUE)
    assert live['status'] == 'idle'


@pytest.fixture
def claude_followup(integration, monkeypatch):
    """Exercise the real handler while holding only its async launch threads."""
    env = integration
    ar = env.ar
    for name in ('_observe_habit_cue', '_allocate_delegation_turn',
                 '_log_agent_dispatch_pending', '_write_usage_breakdown_turn_start_checkpoint',
                 '_log_agent_activity', '_sysprompt_cleanup', '_register_process'):
        monkeypatch.setattr(ar, name, lambda *a, **k: None)
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_auto_fresh_trigger', lambda *a, **k: (None, None))
    monkeypatch.setattr(ar, '_resume_unresumable', lambda *a: False)
    monkeypatch.setattr(ar, '_fragile_resume_target', lambda *a: False)
    monkeypatch.setattr(ar, '_refuse_duplicate_spawn', lambda *a: None)
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(ar, '_respawn_sysprompt_args', lambda *a: ([], None))
    monkeypatch.setattr(ar, '_continuation_model', lambda *a: None)
    monkeypatch.setattr(ar, '_continuation_effort', lambda *a: None)
    monkeypatch.setattr(ar, '_session_skills', lambda *a: [])
    monkeypatch.setattr(ar, '_clock_wrap', lambda p, s, msg, *a: msg)
    monkeypatch.setattr(ar._memory_turn, 'reset_conversation_state', lambda *a: None)
    monkeypatch.setattr(ar._behavior_tail, 'reset_conversation_state', lambda *a: None)
    pending = []
    class DeferredThread:
        def __init__(self, target, args=(), **kwargs):
            self.target, self.args = target, args
        def start(self):
            pending.append(self)
    monkeypatch.setattr(ar.threading, 'Thread', DeferredThread)
    env.pending = pending
    return env


@pytest.mark.parametrize('mode', ['A', 'B'])
def test_claude_queued_launch_crash_restores_review_without_replay(claude_followup, monkeypatch, mode):
    env = claude_followup
    s = exhausted(session())
    s.update(mode=mode, proc=None, process_alive=False, _turn_owner='spawner')
    env.sessions['child'] = s
    assert env.store.park(s)
    dispatch.tick(DUE)
    assert any(t.target.__name__ in ('_do_respawn_b', '_start_followup') for t in env.pending)
    assert env.store.get('child')['phase'] == 'submitting'
    assert env.store.get('child')['attempts'] == 1
    # Status alone is not launch evidence (an old reader may report idle).
    s['status'] = 'idle'
    lr.hold_completion(s, {})
    assert env.store.get('child')['phase'] == 'submitting'
    # Simulate server death before the queued launch thread runs.
    env.sessions.clear()
    monkeypatch.setattr(lr, '_store', lr.ResumeStore(env.store.path))
    dispatch.tick(DUE)
    live = env.sessions['child']
    assert live['status'] == 'waiting_for_reset'
    assert lr.view(live)['phase'] == 'submitting'
    assert any('continue manually' in line for line in live['log_lines'])
    assert env.store.claim('child', DUE) is None
    assert sum(t.target.__name__ in ('_do_respawn_b', '_start_followup')
               for t in env.pending) == 1


@pytest.mark.parametrize('mode', ['A', 'B'])
def test_claude_launch_ack_activates_only_after_delivery(claude_followup, monkeypatch, mode):
    env = claude_followup
    s = exhausted(session())
    s.update(mode=mode, proc=None, process_alive=False, _turn_owner='spawner')
    env.sessions['child'] = s
    env.store.park(s)
    def launch(*a, **k):
        assert env.store.get('child')['phase'] == 'submitting'
        return SimpleNamespace(pid=12345)
    monkeypatch.setattr(env.ar, 'spawn_engine_proc', launch)
    writes = []
    def write(*a, **k):
        assert env.store.get('child')['phase'] == 'submitting'
        assert k['wait_for_ack'] is not None
        writes.append(a)
        return {'ack': 'written'}
    monkeypatch.setattr(env.ar, '_bounded_stdin_write', write)
    dispatch.tick(DUE)
    launch_thread = next(t for t in env.pending
                         if t.target.__name__ in ('_do_respawn_b', '_start_followup'))
    launch_thread.target(*launch_thread.args)
    assert env.store.get('child')['phase'] == 'active', s['log_lines']
    assert env.store.get('child')['attempts'] == 1
    assert len(writes) == (mode == 'B')


@pytest.mark.parametrize('ack', ['failed', 'unknown'])
def test_claude_respawn_unacknowledged_write_stays_recoverable(claude_followup, monkeypatch, ack):
    env = claude_followup
    s = exhausted(session())
    s.update(proc=None, process_alive=False, _turn_owner='spawner')
    env.sessions['child'] = s
    env.store.park(s)
    monkeypatch.setattr(env.ar, 'spawn_engine_proc', lambda *a, **k: SimpleNamespace(pid=12345))
    monkeypatch.setattr(env.ar, '_bounded_stdin_write', lambda *a, **k: {'ack': ack})
    dispatch.tick(DUE)
    launch_thread = next(t for t in env.pending if t.target.__name__ == '_do_respawn_b')
    launch_thread.target(*launch_thread.args)
    assert env.store.get('child')['phase'] == 'submitting'
    env.sessions.clear()
    dispatch.tick(DUE)
    assert lr.view(env.sessions['child'])['phase'] == 'submitting'
    assert env.store.claim('child', DUE) is None


@pytest.mark.parametrize('order', [
    'before_ack_live', 'before_ack_exit', 'after_ack', 'exhaustion_before_ack', 'success_unknown_ack'])
def test_mode_b_result_and_submission_ack_order(claude_followup, monkeypatch, tmp_path, order):
    """Run the real respawn worker and reader across the stdin-ack boundary."""
    from mc import workflows
    from tests.test_stream_reader_malformed import _FakeProc
    env = claude_followup
    s = exhausted(session())
    s.update(proc=None, process_alive=False, _turn_owner='spawner', _notify_session='parent')
    env.sessions['child'] = s
    env.store.park(s)
    monkeypatch.setattr(workflows.state, 'agent_sessions', env.sessions)
    monkeypatch.setattr(workflows, '_load_agent_log', lambda pid: [])
    monkeypatch.setattr(workflows, '_session_summary', lambda live: 'finished task')
    monkeypatch.setattr(workflows, 'WORKFLOWS_PATH', tmp_path / 'workflows.json')
    monkeypatch.setattr(workflows, 'WORKFLOW_RUNS_DIR', tmp_path / 'workflow_runs')
    monkeypatch.setattr(workflows, '_dispatch_agent_internal', lambda *a, **k: 'child')
    workflow = workflows.create_workflow({'name': 'resume-success', 'nodes': [
        {'type': 'agent', 'name': 'first', 'project_id': 'p', 'prompt': 'original task'},
    ]})
    run = workflows.start_run(workflow['id'])
    calls = []
    monkeypatch.setattr(env.ar, '_capture_system_init', lambda *a: None)
    monkeypatch.setattr(env.ar, '_write_usage_breakdown_turn_checkpoint', lambda *a: None)
    monkeypatch.setattr(env.ar, '_maybe_checkpoint', lambda *a: None)
    monkeypatch.setattr(env.ar, '_run_runtime_callbacks', lambda *a: None)
    monkeypatch.setattr(env.ar, '_log_agent_completion_body', lambda live: calls.append('exit'))
    monkeypatch.setattr(env.ar, '_notify_agent_spawner', lambda *a: calls.append('parent'))
    monkeypatch.setattr(env.ar._agent_shim_completion, 'begin', lambda *a: calls.append(a[1]))
    monkeypatch.setattr(env.ar._agent_shim_completion, 'ack', lambda *a, **k: None)
    reached_result, exit_reader = Event(), Event()
    proc = _FakeProc([])
    proc.poll = lambda: None
    def output():
        if order == 'exhaustion_before_ack':
            yield json.dumps({'type': 'rate_limit_event', 'rate_limit_info': {
                'status': 'rejected', 'resetsAt': RESET.timestamp()}}) + '\n'
        yield json.dumps({'type': 'result', 'is_error': order == 'exhaustion_before_ack',
                          'num_turns': 1}) + '\n'
        reached_result.set()
        if order == 'before_ack_live':
            assert exit_reader.wait(5), 'test did not release reader'
    proc.stdout = output()
    monkeypatch.setattr(env.ar, 'spawn_engine_proc', lambda *a, **k: proc)
    reader_thread = None
    def write(*a, **k):
        nonlocal reader_thread
        reader = next(t for t in env.pending if t.target.__name__ == '_read_agent_stream_b')
        if order == 'before_ack_live':
            reader_thread = RealThread(target=reader.target, args=reader.args)
            reader_thread.start()
            assert reached_result.wait(5), s['log_lines']
        elif order != 'after_ack':
            reader.target(*reader.args)
        if order != 'after_ack':
            assert s['status'] == 'waiting_for_reset'
            assert calls == []
            assert workflows._step_session_verdict(
                {'session_id': 'child', 'project_id': 'p'}, use_live=True)[0] == 'in_flight'
        return {'ack': 'unknown' if order == 'success_unknown_ack' else 'written'}
    monkeypatch.setattr(env.ar, '_bounded_stdin_write', write)
    dispatch.tick(DUE)
    worker = next(t for t in env.pending if t.target.__name__ == '_do_respawn_b')
    try:
        worker.target(*worker.args)
        if order == 'success_unknown_ack':
            assert env.store.get('child')['phase'] == 'submitting'
            assert env.store.get('child')['review_required'] is True
            assert s['status'] == 'waiting_for_reset'
            assert calls == []
            dispatch.confirm_submission(s, 1)  # late ack cannot release manual review
            dispatch.tick(DUE)
            assert s['status'] == 'waiting_for_reset'
            assert env.store.get('child')['attempts'] == 1
            assert calls == []
            return
        if order == 'exhaustion_before_ack':
            assert env.store.get('child')['phase'] == 'waiting'
            assert s['status'] == 'waiting_for_reset'
            assert calls == []
            assert env.store.get('child')['attempts'] == 1
            return
        assert env.store.get('child')['phase'] == 'active'
        if order == 'after_ack':
            reader = next(t for t in env.pending if t.target.__name__ == '_read_agent_stream_b')
            reader.target(*reader.args)
        assert s['status'] == ('idle' if order == 'before_ack_live' else 'completed'), s['log_lines']
        assert calls.count('parent') == 1
        assert workflows._step_session_verdict(
            {'session_id': 'child', 'project_id': 'p'}, use_live=True)[0] == 'ended'
        assert workflows.reconcile_running_steps() == {'advanced': 1, 'stalled': 0}
        assert workflows.get_run(run['id'])['status'] == 'completed'
    finally:
        exit_reader.set()
        if reader_thread:
            reader_thread.join(5)
            assert not reader_thread.is_alive()
    assert s['status'] == 'completed'
    assert calls.count('parent') == 1
    assert calls.count('exit') >= 1


@pytest.mark.parametrize('failure', ['launch', 'failed', 'unknown'])
def test_failed_mode_b_continuation_is_visible_and_reconciles(claude_followup, monkeypatch, tmp_path, failure):
    from mc import workflows
    from mc.blueprints import floor_routes as floor
    env = claude_followup
    s = exhausted(session())
    s.update(proc=None, process_alive=False, _turn_owner='spawner')
    env.sessions['child'] = s
    env.store.park(s)
    monkeypatch.setattr(workflows, 'WORKFLOWS_PATH', tmp_path / 'workflows.json')
    monkeypatch.setattr(workflows, 'WORKFLOW_RUNS_DIR', tmp_path / 'workflow_runs')
    monkeypatch.setattr(workflows, '_dispatch_agent_internal', lambda *a, **k: 'child')
    workflow = workflows.create_workflow({'name': 'resume-review', 'nodes': [
        {'type': 'agent', 'name': 'first', 'project_id': 'p', 'prompt': 'original task'},
        {'type': 'agent', 'name': 'next', 'project_id': 'p', 'prompt': 'never run on failure'},
    ], 'edges': [{'from': 'first', 'to': 'next'}]})
    run = workflows.start_run(workflow['id'])
    def launch(*a, **k):
        if failure == 'launch':
            raise OSError('synthetic continuation Popen failure')
        return SimpleNamespace(pid=12345)
    monkeypatch.setattr(env.ar, 'spawn_engine_proc', launch)
    real_write = env.ar._bounded_stdin_write
    monkeypatch.setattr(env.ar, '_bounded_stdin_write', lambda *a, **k: {'ack': failure})
    dispatch.tick(DUE)
    worker = next(t for t in env.pending if t.target.__name__ == '_do_respawn_b')
    assert s['_limit_resume_attempt'] == env.store.get('child')['attempts'] == 1
    worker.target(*worker.args)
    assert env.store.get('child')['phase'] == 'submitting'
    assert s['status'] == 'waiting_for_reset', (s['status'], s['log_lines'])
    if failure == 'unknown':
        # A timed-out writer may fail after the worker already established the
        # review hold; execute the real writer's delayed failure as well.
        def broken_write(payload):
            raise BrokenPipeError('synthetic late pipe failure')
        proc = SimpleNamespace(pid=12345, stdin=SimpleNamespace(write=broken_write))
        real_write(proc, 'continuation', session=s)
        writer = next(t for t in env.pending if t.target.__name__ == '_writer')
        writer.target(*writer.args)
        assert s['status'] == 'waiting_for_reset'
    # Completion/recovery cannot erase the hold or replay the failed claim.
    env.ar._log_agent_completion(s)
    dispatch.tick(DUE)
    assert s['status'] == 'waiting_for_reset'
    assert env.store.get('child')['attempts'] == 1
    monkeypatch.setattr(floor, 'agent_sessions', env.sessions)
    assert floor._live_sessions({}, {}, {})['p'][0]['limit_resume']['phase'] == 'submitting'
    for use_live in (False, True):
        verdict, status, summary = workflows._step_session_verdict(
            {'session_id': 'child', 'project_id': 'p'}, use_live=use_live)
        assert (verdict, status) == ('ended', 'error')
        assert 'manual review' in summary
    # The same verdict and visible hold survive losing the live session.
    env.sessions.clear()
    monkeypatch.setattr(lr, '_store', lr.ResumeStore(env.store.path))
    dispatch.restore_waits(env.ar)
    assert env.sessions['child']['status'] == 'waiting_for_reset'
    assert workflows._step_session_verdict(
        {'session_id': 'child', 'project_id': 'p'}, use_live=False)[:2] == ('ended', 'error')
    assert workflows.reconcile_running_steps() == {'advanced': 1, 'stalled': 0}
    after = workflows.get_run(run['id'])
    assert after['status'] == 'failed'
    assert after['steps']['first']['status'] == 'failed'
    assert 'manual review' in after['steps']['first']['error']
    assert after['steps']['next']['status'] == 'pending'
    assert workflows.reconcile_running_steps() == {'advanced': 0, 'stalled': 0}
    monkeypatch.setattr(env.ar, '_log_agent_completion', lambda s: None)
    response = env.app.test_client().post('/api/project/p/agent/child/limit-resume/cancel')
    assert response.status_code == 200
    assert env.sessions['child']['status'] == 'stopped'


def test_launch_ack_cannot_activate_cancelled_or_different_attempt(store):
    s = exhausted(session())
    store.park(s)
    store.claim('child', DUE)
    lr.confirm_submission('child', 2)
    assert store.get('child')['phase'] == 'submitting'
    store.cancel('child', 'p')
    lr.confirm_submission('child', 1)
    assert store.get('child')['phase'] == 'cancelled'


def test_submission_review_rejects_late_ack_and_stale_or_cancelled_failure(store):
    s = exhausted(session())
    store.park(s)
    store.claim('child', DUE)
    lr.new_turn(s, internal=True)
    s['status'] = 'running'
    lr.submission_failed(s, 2)
    assert s['status'] == 'running'
    lr.submission_failed(s, 1)
    assert s['status'] == 'waiting_for_reset'
    lr.confirm_submission('child', 1)
    assert store.get('child')['phase'] == 'submitting'
    assert store.cancel('child', 'p')
    s['status'] = 'idle'
    lr.submission_failed(s, 1)
    assert s['status'] == 'idle'
    assert store.get('child')['phase'] == 'cancelled'


@pytest.mark.parametrize('intervention', ['none', 'cancel', 'human', 'review', 'stale_ack', 'stale_turn'])
def test_deferred_completion_release_respects_attempt_and_project_lock(integration, monkeypatch, intervention):
    env = integration
    s = exhausted(session())
    env.sessions['child'] = s
    env.store.park(s)
    env.store.claim('child', DUE)
    s['_limit_resume_attempt'] = 1
    lr.new_turn(s, internal=True)
    s['status'] = 'idle'
    assert lr.hold_completion(s, {}, completion_kind='turn')
    lock = env.ar.get_manager('p').lock
    release_started, release_finished = Event(), Event()
    calls = []
    def complete(live):
        assert lock._is_owned()
        calls.append(live['status'])
    monkeypatch.setattr(env.ar, '_complete_agent_turn', complete)
    monkeypatch.setattr(env.ar, '_log_agent_completion', lambda live: None)
    monkeypatch.setattr(env.ar, '_update_agent_log', lambda *a, **k: None)
    def acknowledge():
        release_started.set()
        dispatch.confirm_submission(s, 2 if intervention == 'stale_ack' else 1)
        release_finished.set()
    with lock:
        worker = RealThread(target=acknowledge)
        worker.start()
        assert release_started.wait(5)
        assert not release_finished.is_set()
        if intervention == 'cancel':
            response = env.app.test_client().post('/api/project/p/agent/child/limit-resume/cancel')
            assert response.status_code == 200
        elif intervention == 'human':
            with env.app.test_request_context(headers={'Origin': 'http://localhost:5199'}):
                env.ar._mark_human_joined('p', 'child')
        elif intervention == 'review':
            lr.submission_failed(s, 1)
        elif intervention == 'stale_turn':
            lr.new_turn(s)
            s['status'] = 'running'
    worker.join(5)
    assert not worker.is_alive()
    assert release_finished.is_set()
    if intervention == 'none':
        assert calls == ['idle']
        assert '_limit_resume_completion' not in s
        dispatch.confirm_submission(s, 1)  # duplicate ack never repeats completion
        assert calls == ['idle']
    else:
        assert calls == []
        assert s['status'] == {
            'cancel': 'stopped', 'human': 'idle', 'review': 'waiting_for_reset',
            'stale_ack': 'waiting_for_reset', 'stale_turn': 'running',
        }[intervention]
        if intervention in ('review', 'stale_ack'):
            assert env.store.get('child')['phase'] == 'submitting'
            assert env.store.claim('child', DUE) is None


def test_pending_claim_holds_without_exhaustion_but_can_park_fresh_limit(store):
    s = exhausted(session())
    store.park(s)
    claim = store.claim('child', DUE)
    s['_limit_resume_attempt'] = claim['attempts']
    lr.new_turn(s, internal=True)
    s['status'] = 'error'
    assert lr.hold_completion(s, {})
    assert s['status'] == 'waiting_for_reset'
    assert store.get('child')['phase'] == 'submitting'
    exhausted(s)  # new evidence from the continuation before its launch ack
    s['status'] = 'error'
    assert lr.hold_completion(s, {})
    assert store.get('child')['phase'] == 'waiting'
    lr.confirm_submission('child', claim['attempts'])
    assert store.get('child')['phase'] == 'waiting'
    assert store.claim('child', DUE)['attempts'] == 2


@pytest.mark.parametrize('month,offset', [(1, -8), (7, -7)])
def test_codex_local_reset_parks_with_utc_deadline(store, monkeypatch, month, offset):
    from mc import allowance_state as allowance
    # Simulate a host with DST, independently of the test runner's timezone.
    class LocalDatetime(datetime):
        def astimezone(self, tz=None):
            assert self.tzinfo is None  # vendor emits a local wall-clock time
            local = datetime(self.year, self.month, self.day, self.hour, self.minute,
                             tzinfo=timezone(timedelta(hours=offset)))
            return local.astimezone(tz)
    monkeypatch.setattr(allowance, 'datetime', LocalDatetime)
    text = f"You've hit your usage limit. try again at {'Jan' if month == 1 else 'Jul'} 1st, 2030 7:58 AM."
    hit = allowance.detect('codex', {'type': 'error', 'message': text})
    s = session()
    s.update(provider='codex', mode='A')
    lr.observe(s, hit)
    assert store.park(s)
    expected = datetime(2030, month, 1, 7 - offset, 58, tzinfo=timezone.utc)
    row = store.get('child')
    assert row['resets_at'] == expected.isoformat()
    assert lr.reset_time(row['resume_at']) == expected + timedelta(seconds=120)


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
