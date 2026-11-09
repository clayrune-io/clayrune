"""MC-1168 r6: an old failed delivery cannot rewrite a successor's run.

Ports Fenn's real Run Now/revival/pending-row probes, also covering cadence,
same-turn replacements, and takeover after rollback. Processes are doubles.
"""
from copy import deepcopy
import threading
from types import SimpleNamespace

import pytest

import mc.agent_runtime as art
from mc.delivery_attempt import DeliveryAttempt, pending_fields
from mc.schedule_delivery_failure import record_failure
from test_runtime_delivery_truth import app_env, revive_env, _Proc, _Stdin  # noqa: F401
from test_runtime_delivery_truth_r4 import scheduled  # noqa: F401


@pytest.mark.parametrize('path', ['run-now', 'cadence'])
@pytest.mark.parametrize('owner', ['proc', 'registry'])
@pytest.mark.parametrize('advance_turn', [False, True])
def test_old_schedule_failure_preserves_replacement(scheduled, monkeypatch, path, owner, advance_turn):
    env = scheduled
    monkeypatch.setattr(env['ar'], '_UsageBreakdownStore', lambda *a: SimpleNamespace(
        record_session_checkpoint=lambda **kw: True, mark_session_running=lambda *a: None))
    entered, release = threading.Event(), threading.Event()
    outcome = []

    class PendingFailure(_Stdin):
        def write(self, text):
            entered.set()
            assert release.wait(5)
            raise BrokenPipeError('old scheduled prompt rejected')

    old_proc = _Proc(PendingFailure(), pid=515159)
    new_proc = _Proc(_Stdin(), pid=515160)
    env['spawned']['proc'] = old_proc

    def request():
        if path == 'cadence':
            env['sr']._scheduler_loop()
            outcome.append('failed cadence')
        else:
            with env['client'] as client:
                response = client.post('/api/schedule/s1/run-now')
                outcome.append((response.status_code, response.get_json()))

    caller = threading.Thread(target=request)
    caller.start()
    try:
        assert entered.wait(3)
        mgr = env['ar'].get_manager('p1')
        # Acquiring this lock also proves the blocked write does not hold it.
        with mgr.lock:
            old_session = env['sessions'][env['sid']]
            replacement = old_session if owner == 'proc' else dict(old_session)
            replacement.update(proc=new_proc, status='running', process_alive=True,
                               task='new task',
                               _delegation_turn=old_session['_delegation_turn'] + int(advance_turn))
            env['sessions'][env['sid']] = replacement
            env['ar']._log_agent_dispatch_pending(replacement, strict=True, write_turn_start=False)
            expected = deepcopy(env['history'])
            assert expected[0]['status'] == 'in_progress' and expected[0]['task'] == 'new task'
        release.set()
        caller.join(5)
        assert not caller.is_alive()
        if path == 'run-now':
            status, body = outcome[0]
            assert status == 502 and body['ok'] is False and body['delivered'] is False
            assert body['session_id'] == env['sid']
        else:
            assert outcome == ['failed cadence']
            assert env['schedule']['next_run'] != '2020-01-01T00:00:00Z'
        assert env['sessions'][env['sid']] is replacement
        assert replacement['proc'] is new_proc and replacement['status'] == 'running'
        assert env['killed'] == [old_proc.pid]
        assert env['dispatches'] == []
        assert 'old scheduled prompt rejected' in env['schedule']['last_error']
        assert env['schedule']['last_error_at'] and env['saved']
        assert env['history'] == expected
    finally:
        release.set()
        caller.join(5)


def test_takeover_between_rollback_and_recording_preserves_row(scheduled, monkeypatch):
    env = scheduled
    env['spawned']['proc'] = _Proc(_Stdin(fail_on='write'))
    successor = {'proc': _Proc(_Stdin()), 'status': 'running', '_delegation_turn': 2}
    expected = []

    def replace_then_record(*args):
        with env['ar'].get_manager('p1').lock:
            assert env['sid'] not in env['sessions']  # Old rollback completed.
            env['sessions'][env['sid']] = successor
            env['history'][0].update(delegation_turn=2, status='in_progress', task='new task',
                                     **pending_fields(successor))
            expected.extend(deepcopy(env['history']))
        record_failure(*args)

    monkeypatch.setattr(env['sr']._delivery_failure, 'record_failure', replace_then_record)
    response = env['client'].post('/api/schedule/s1/run-now')
    assert response.status_code == 502
    assert env['history'] == expected
    assert env['sessions'][env['sid']] is successor


@pytest.mark.parametrize('owner', ['absent', 'disowned', 'restored'])
def test_newer_durable_turn_is_preserved_even_after_owner_cleanup(app_env, monkeypatch, owner):
    env = app_env
    prior = {'proc': _Proc(_Stdin()), '_delegation_turn': 1}
    session = {'proc': _Proc(_Stdin()), '_delegation_turn': 2}
    attempt = DeliveryAttempt.capture(session)
    if owner == 'disowned':
        session['proc'] = None
        env['sessions']['addressed'] = session
    elif owner == 'restored':
        env['sessions']['addressed'] = prior
    rows = [{'session_id': 'addressed', 'trigger_type': 'schedule', 'trigger_id': 's1',
             'delegation_turn': 3, 'status': 'in_progress', 'task': 'new task', 'summary': ''}]
    expected = deepcopy(rows)
    monkeypatch.setattr(env['ar'], '_update_agent_log', lambda pid, mutate: mutate(rows))
    record_failure('p1', 's1', 'addressed', 'old task', attempt.bind(art.PromptDeliveryError('old failure')))
    assert rows == expected


def test_missing_attempt_identity_never_mutates_history(app_env, monkeypatch):
    def forbidden(*a):
        pytest.fail('unidentified failure attempted a log transaction')
    monkeypatch.setattr(app_env['ar'], '_update_agent_log', forbidden)
    record_failure('p1', 's1', 'addressed', 'old task', art.PromptDeliveryError('no identity'))


def test_codex_acknowledgment_keeps_captured_attempt_after_process_replacement(app_env, monkeypatch):
    old_proc, replacement = _Proc(_Stdin(), pid=737373), _Proc(_Stdin(), pid=737374)
    session = {'proc': replacement, '_delegation_turn': 2, 'status': 'running'}
    attempt = DeliveryAttempt.capture(session)
    app_env['sessions']['addressed'] = session
    rows = [{'session_id': 'addressed', 'trigger_type': 'schedule', 'trigger_id': 's1',
             'delegation_turn': 2, 'status': 'in_progress', 'task': 'new task',
             **pending_fields(session)}]
    expected = deepcopy(rows)
    killed = []
    monkeypatch.setattr(art, '_kill_pid', lambda pid: killed.append(pid))
    monkeypatch.setattr(app_env['ar'], '_update_agent_log', lambda pid, mutate: mutate(rows))
    delivery = art._PromptDelivery()
    delivery.why = 'old write rejected'
    delivery.done.set()
    acknowledge = art._pending_prompt(delivery, session, old_proc, 'codex', attempt)
    with pytest.raises(art.PromptDeliveryError) as caught:
        acknowledge()
    assert getattr(caught.value, 'delivery_attempt') is attempt
    record_failure('p1', 's1', 'addressed', 'old task', caught.value)
    assert rows == expected
    assert session['proc'] is replacement and session['status'] == 'running'
    assert killed == [old_proc.pid]


def test_restored_predecessor_allows_original_failure_but_not_reused_predecessor(app_env, monkeypatch):
    env = app_env
    prior = {'proc': _Proc(_Stdin()), '_delegation_turn': 1}
    session = {'proc': _Proc(_Stdin()), '_delegation_turn': 2}
    attempt = DeliveryAttempt.capture(session)
    env['sessions']['addressed'] = prior
    rows = [{'session_id': 'addressed', 'trigger_type': 'schedule', 'trigger_id': 's1',
             'delegation_turn': 1, 'status': 'completed', 'task': 'old',
             'delivery_attempt_id': attempt.attempt_id}]
    monkeypatch.setattr(env['ar'], '_update_agent_log', lambda pid, mutate: mutate(rows))
    error = attempt.bind(art.PromptDeliveryError('failed'))
    record_failure('p1', 's1', 'addressed', 'attempt task', error)
    assert rows[0]['status'] == 'error' and rows[0]['task'] == 'attempt task'
    rows[0].update(status='in_progress', task='new task')
    rows[0].pop('delivery_attempt_id')
    prior['_delegation_turn'] += 1
    expected = deepcopy(rows)
    record_failure('p1', 's1', 'addressed', 'attempt task', error)
    assert rows == expected
