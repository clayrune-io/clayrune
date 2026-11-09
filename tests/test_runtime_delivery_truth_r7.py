"""MC-1168 r7: durable attempt tokens survive completion and tab removal."""
from copy import deepcopy
import threading
from types import SimpleNamespace
from uuid import UUID

import pytest

import mc.agent_runtime as art
from mc.delivery_attempt import DeliveryAttempt, pending_fields
from mc.schedule_delivery_failure import record_failure
from test_runtime_delivery_truth import app_env, revive_env, codex, _Proc, _Stdin  # noqa: F401
from test_runtime_delivery_truth_r4 import scheduled, runtime_start, _start  # noqa: F401
from test_runtime_completion_log import env, _dispatch, _log_rows  # noqa: F401


def test_real_pending_capture_completion_and_reload_keep_successor_token(env):
    sid, handle = _dispatch(env, trigger_type='schedule', trigger_id='s1')
    ar, session = env['ar'], handle.session_dict
    first = DeliveryAttempt.capture(session)
    assert _log_rows(env)[0]['delivery_attempt_id'] == first.attempt_id
    second = DeliveryAttempt.capture(session)
    assert UUID(first.attempt_id) != UUID(second.attempt_id)
    assert session.get('_delegation_turn', 1) == 1
    env['runtime'].run_turn(handle, ['successor answer'])
    expected = _log_rows(env)  # Read actual serialized JSON, not an in-memory projection.
    assert len(expected) == 1
    assert expected[0]['status'] == 'completed'
    assert expected[0]['delivery_attempt_id'] == second.attempt_id
    env['sessions'].pop(sid)
    record_failure('proj1', 's1', sid, 'old task', first.bind(art.PromptDeliveryError('old failure')))
    assert _log_rows(env) == expected


def test_identity_backfill_retains_token_but_same_turn_takeover_changes_it(env):
    sid, handle = _dispatch(env)
    session, ar = handle.session_dict, env['ar']
    first = _log_rows(env)[0]['delivery_attempt_id']
    session['provider_session_id'] = 'native-thread'
    ar._log_agent_dispatch_pending(session, identity_only=True)
    assert _log_rows(env)[0]['delivery_attempt_id'] == first
    ar._advance_delegation_turn(session)
    row = _log_rows(env)[0]
    assert row['delegation_turn'] == session['_delegation_turn'] == 1
    assert UUID(row['delivery_attempt_id']) != UUID(first)


@pytest.mark.parametrize('prior_live', [False, True])
def test_ordinary_revival_rollback_records_its_claimed_attempt(scheduled, prior_live):
    env = scheduled
    prior = {'session_id': env['sid'], 'project_id': 'p1', 'status': 'completed'}
    if prior_live:
        env['sessions'][env['sid']] = prior
    env['spawned']['proc'] = _Proc(_Stdin(fail_on='write'))
    response = env['client'].post('/api/schedule/s1/run-now')
    assert response.status_code == 502
    assert response.get_json()['delivered'] is False
    row = env['history'][0]
    assert row['status'] == 'error' and 'BrokenPipeError' in row['summary']
    assert UUID(row['delivery_attempt_id'])
    assert row['cost_usd'] == 1.25
    assert env['sessions'].get(env['sid']) is (prior if prior_live else None)
    assert env['dispatches'] == []


@pytest.mark.parametrize('legacy', [False, True])
@pytest.mark.parametrize('live_owner', ['absent', 'restored'])
def test_only_owned_row_records_failure_without_registry_or_turn_inference(app_env, monkeypatch, legacy, live_owner):
    attempt = DeliveryAttempt.capture(None)
    rows = [{'session_id': 'addressed', 'trigger_type': 'schedule', 'trigger_id': 's1',
             'delegation_turn': 99, 'status': 'completed', 'cost_usd': 1.25}]
    if not legacy:
        rows[0]['delivery_attempt_id'] = attempt.attempt_id
    if live_owner == 'restored':
        app_env['sessions']['addressed'] = {'_delegation_turn': 456, 'proc': object()}
    monkeypatch.setattr(app_env['ar'], '_update_agent_log', lambda pid, mutate: mutate(rows))
    expected = deepcopy(rows)
    record_failure('p1', 's1', 'addressed', 'failed task', attempt.bind(art.PromptDeliveryError('rejected')))
    if legacy:
        assert rows == expected
        return
    assert rows[0]['status'] == 'error' and rows[0]['task'] == 'failed task'
    assert rows[0]['delivery_attempt_id'] == attempt.attempt_id
    assert rows[0]['cost_usd'] == 1.25


def test_token_is_compared_inside_log_transaction_even_if_trigger_changed(app_env, monkeypatch):
    attempt = DeliveryAttempt.capture(None)
    rows = [{'session_id': 'addressed', 'trigger_type': 'schedule', 'trigger_id': 's1',
             'delivery_attempt_id': attempt.attempt_id, 'status': 'in_progress'}]
    expected = []

    def update(pid, mutate):
        # This takeover lands at the leaf before the failure mutator, after
        # any stale pre-transaction read would already have happened.
        rows[0].update(trigger_type='manual', trigger_id='', status='completed',
                       task='successor', summary='answer', **pending_fields({}))
        expected.extend(deepcopy(rows))
        mutate(rows)

    monkeypatch.setattr(app_env['ar'], '_update_agent_log', update)
    record_failure('p1', 's1', 'addressed', 'old task', attempt.bind(art.PromptDeliveryError('old')))
    assert rows == expected  # No duplicate row inserted under the schedule's old trigger.


def test_claude_claim_storage_failure_precedes_any_spawn(scheduled, monkeypatch):
    def broken(*a):
        raise OSError('claim storage unavailable')
    monkeypatch.setattr(scheduled['ar'], '_update_agent_log', broken)
    monkeypatch.setattr(scheduled['ar'], 'spawn_engine_proc',
                        lambda *a, **kw: pytest.fail('spawned before durable ownership'))
    with pytest.raises(OSError, match='claim storage unavailable'):
        scheduled['sr']._scheduled_continue(scheduled['project'], 'p1', scheduled['sid'], 'task')
    assert scheduled['dispatches'] == []


def test_codex_claim_storage_failure_precedes_any_spawn(runtime_start, monkeypatch):
    def broken(*a):
        raise OSError('claim storage unavailable')
    monkeypatch.setattr(runtime_start['ar'], '_update_agent_log', broken)
    monkeypatch.setattr(art, 'spawn_engine_proc',
                        lambda *a, **kw: pytest.fail('spawned before durable ownership'))
    with pytest.raises(OSError, match='claim storage unavailable'):
        _start(runtime_start)


@pytest.mark.parametrize('streaming', [False, True])
def test_both_claude_revival_modes_claim_durable_ownership(scheduled, monkeypatch, streaming):
    env = scheduled
    env['project']['use_streaming_agent'] = streaming
    env['spawned']['proc'] = _Proc(_Stdin())
    monkeypatch.setattr(env['ar'], '_read_agent_stream', lambda *a: None)
    session = env['ar']._revive_from_agent_log('p1', env['sid'], 'new task', env['project'])
    assert session is not None
    assert UUID(session['_delivery_attempt_id'])
    assert env['history'][0]['delivery_attempt_id'] == session['_delivery_attempt_id']


@pytest.mark.parametrize('launch', ['first', 'followup'])
def test_real_codex_rejection_preserves_closed_same_turn_successor(scheduled, codex, monkeypatch, launch):
    env, rt = scheduled, codex['rt']
    ar = env['ar']
    monkeypatch.setattr(ar, '_UsageBreakdownStore', lambda *a: SimpleNamespace(
        record_session_checkpoint=lambda **kw: True, mark_session_running=lambda *a: None))
    monkeypatch.setattr(ar._limit_resume_dispatch, 'close_session', lambda *a: None)
    entered, release = threading.Event(), threading.Event()
    published = threading.Event()
    monkeypatch.setattr(art, '_mode_a_reader', lambda *a: published.set())
    errors = []

    class PendingFailure(_Stdin):
        def write(self, text):
            entered.set()
            assert release.wait(10)
            raise BrokenPipeError('old codex write rejected')

    old_proc = _Proc(PendingFailure())
    codex['holder']['proc'] = old_proc
    session = {'session_id': env['sid'], 'project_id': 'p1', 'provider': 'codex',
               'task': 'old task', 'trigger_type': 'schedule', 'trigger_id': 's1',
               '_delegation_turn': 1}
    env['sessions'][env['sid']] = session

    def deliver():
        try:
            if launch == 'first':
                art._mode_a_dispatch(rt, ['codex', 'exec'], 'old task', env['project']['project_path'],
                    'p1', 'old task', env['sid'], session, False, None, None, None)
            else:
                rt._spawn_turn(art.SessionHandle(mc_session_id=env['sid'], provider='codex', mode='A',
                    project_id='p1', project_path=env['project']['project_path'], session_dict=session),
                    ['codex', 'exec'], 'old task', 'native-thread')
        except Exception as exc:
            errors.append(exc)
            record_failure('p1', 's1', env['sid'], 'old task', exc)

    caller = threading.Thread(target=deliver)
    caller.start()
    try:
        assert entered.wait(3)
        assert published.wait(3)  # Publication and terminal gate precede the reader.
        with ar.get_manager('p1').lock:
            assert session['proc'] is old_proc
            old_id = env['history'][0]['delivery_attempt_id']
            successor = dict(session, proc=_Proc(_Stdin(), pid=737374), task='new successful task')
            env['sessions'][env['sid']] = successor
            ar._advance_delegation_turn(successor)
            assert successor['_delegation_turn'] == 1
            assert env['history'][0]['delivery_attempt_id'] != old_id
            successor['status'] = 'completed'
            env['history'][0].update(status='completed', summary='successor answer')
            expected = deepcopy(env['history'])
        with env['client'].application.test_client() as client:
            assert client.delete('/api/project/p1/agent/session', json={'session_id': env['sid']}).status_code == 200
        release.set()
        caller.join(5)
        assert not caller.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], art.PromptDeliveryError)
        assert getattr(errors[0], 'delivery_attempt').attempt_id == old_id
        assert env['history'] == expected
        assert codex['killed'] == [old_proc.pid]
        assert env['dispatches'] == []
    finally:
        release.set()
        caller.join(5)
