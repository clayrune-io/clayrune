"""MC-1216 follow-ups, both decided by Dave on 2026-11-17.

Q1 (ed1eb670258f): a report refused BEFORE any hand-off (parent durable status
'error' at revive time, nothing written) was filed 'uncertain', which is never
retried, and then hidden because it had no handoff boundary. It is now a
bounded retry, and once the retries run out it is shown as not delivered.

Q2 (8e65fec33d4e:turn:3): the receipt check matched the header line, which
carried no turn number, so turn:4's delivery confirmed turn:3, which was never
written. The header now ends with the event id and the check matches on it; a
stored header without the id is unverified, never delivered.
"""
import json
import sqlite3
from contextlib import nullcontext

import pytest

from mc import delegation_receipt_header as rh
from mc import delegation_settlement as ds
from mc import delegation_waiting as dw
from mc.delegation_delivery import (DeliveryNotHandedOff, DeliveryStore,
                                    callback_payload, drain_once, uncertain_cause)
from mc.delegation_transcript_reconcile import TranscriptReconciler

CHILD = {'session_id': 'childchildchild', 'character': {'agent_name': 'Sol_Tobin'},
         'status': 'completed', 'task': 't', 'project_id': 'p'}


def _payload(event):
    return callback_payload(CHILD, f'report for {event}', event)


# ---------------------------------------------------------------- Q1

def _revive_with_durable_status(monkeypatch, status):
    from mc.blueprints import agent_routes as ar
    entry = {'session_id': 'parent', 'status': status, 'provider': 'claude',
             'incognito': False, 'ts': '1'}
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid, **kw: [entry])
    monkeypatch.setattr(ar, '_assert_runtime_project_generation', lambda *a, **k: None)
    return lambda: ar._revive_parent_for_delegation('p', 'parent', 'msg', {})


@pytest.mark.parametrize('status', ['error', 'stopped', 'running', 'in_progress'])
def test_refusal_before_any_handoff_is_a_not_handed_off_failure(monkeypatch, status):
    revive = _revive_with_durable_status(monkeypatch, status)
    with pytest.raises(DeliveryNotHandedOff, match='parent durable outcome is ambiguous'):
        revive()


def test_that_cause_is_known_to_be_safe_to_retry():
    code, never_handed = uncertain_cause('parent durable outcome is ambiguous: error')
    assert (code, never_handed) == ('parent_state_ambiguous', True)


def _drain_refused(store, revive):
    def process(row):
        return revive()
    return drain_once(store, send_outbox=lambda r: None, process_inbox=process)


def _settle_unchecked(store, event):
    """What the reconciler's pass does to a parked row with no boundary."""
    row = store.status('inbox', event, 'p')
    ds.settle(store, row, {}, lambda pid: nullcontext())


def test_a_refused_report_is_retried_then_surfaces_as_failed(monkeypatch, tmp_path):
    now = [1000.0]
    store = DeliveryStore(tmp_path / 'd.db', max_attempts=3, clock=lambda: now[0])
    dw.configure(store.path)
    try:
        event = 'ed1eb670258f:turn:3'
        store.enqueue(event, 'p', 'parent', _payload(event))
        store.accept(event, 'p', 'parent', _payload(event))
        revive = _revive_with_durable_status(monkeypatch, 'error')

        _drain_refused(store, revive)
        row = store.status('inbox', event, 'p')
        assert (row['state'], row['attempts']) == ('pending', 1)    # retried, not parked
        assert row['next_attempt'] > now[0]                         # with backoff

        for expected in (2, 3):
            now[0] += 1000.0
            _drain_refused(store, revive)
            assert store.status('inbox', event, 'p')['attempts'] == expected
        row = store.status('inbox', event, 'p')
        assert row['state'] == 'recovery_required'                  # bounded: it stops

        _settle_unchecked(store, event)
        assert store.status('inbox', event, 'p')['check_status'] == 'no_boundary'
        (report,) = dw.waiting_by_parent('p')['parent']
        assert report['stage'] == 'failed'
        assert 'was not delivered' in report['label']
        assert report['retry_action'] == 'retry'
        assert 'cannot duplicate' in report['detail']
    finally:
        dw.configure(None)


def test_an_old_uncertain_row_with_that_cause_is_no_longer_hidden(tmp_path):
    """ed1eb670258f itself: state uncertain, check_status no_boundary."""
    store = DeliveryStore(tmp_path / 'd.db')
    dw.configure(store.path)
    try:
        event = 'ed1eb670258f:turn:3'
        store.enqueue(event, 'p', 'parent', _payload(event))
        store.accept(event, 'p', 'parent', _payload(event))
        with sqlite3.connect(store.path) as db:
            db.execute("UPDATE inbox SET state='uncertain',recovery_required=1,check_status='no_boundary',"
                       "last_error='parent durable outcome is ambiguous: error'")
        (report,) = dw.waiting_by_parent('p')['parent']
        assert report['stage'] == 'failed' and report['event_id'] == event
    finally:
        dw.configure(None)


# ---------------------------------------------------------------- Q2

def test_each_turns_header_names_its_own_event():
    first = _payload('kid:turn:3')['message'].split('\n', 1)[0]
    other = _payload('kid:turn:4')['message'].split('\n', 1)[0]
    assert first != other
    assert first.endswith(' [event kid:turn:3]')
    assert first.startswith('[dispatched agent finished] Sol_Tobin (session childchildch')


def test_header_helper_only_accepts_its_own_id():
    h = rh.with_event_id('[dispatched agent finished] x', 'kid:turn:3')
    assert rh.with_event_id(h, 'kid:turn:3') == h                    # idempotent
    assert rh.header_carries(h, 'kid:turn:3')
    assert not rh.header_carries(h, 'kid:turn:4')
    assert not rh.header_carries(h, '')
    assert not rh.header_carries('[dispatched agent finished] x', 'kid:turn:3')


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A parent transcript with a baseline record and a store whose clock we own."""
    now = [1000.0]
    store = DeliveryStore(tmp_path / 'delivery.db', clock=lambda: now[0])
    path = tmp_path / 'native.jsonl'
    parent = {'session_id': 'parent', 'project_id': 'p', 'provider': 'claude',
              'claude_session_id': 'native', '_agent_cwd': str(tmp_path), 'status': 'idle'}
    path.write_text(json.dumps({'type': 'user', 'sessionId': 'native', 'cwd': str(tmp_path),
                                'timestamp': '1970-01-01T00:16:39Z',
                                'message': {'content': 'original task'}}) + '\n', encoding='utf-8')
    monkeypatch.setattr(ds, '_locate', lambda s: (str(path), ''))
    dw.configure(store.path)
    yield store, parent, path, now, tmp_path
    dw.configure(None)


def _queue_and_hand_off(env, event, message):
    store, parent, _path, now, _tmp = env
    payload = {'message': message, 'who': 'Sol_Tobin', 'child_session_id': 'kid'}
    store.enqueue(event, 'p', 'parent', payload)
    store.accept(event, 'p', 'parent', payload)
    claim = store.claim_inbox()
    store.record_handoff(claim, parent)
    store.record_handoff_response(claim, {'session_id': 'parent', 'stdin_write_ack': 'written'}, needs_ack=True)
    store.finish_inbox(event, claim['fence_token'], 'acknowledgment lost', state='uncertain')
    now[0] = 1002.0


def _transcript_gets(env, text, *, terminal=True):
    _store, _parent, path, _now, _tmp = env
    records = [{'type': 'user', 'timestamp': '1970-01-01T00:16:41Z', 'message': {'content': text}}]
    if terminal:
        records.append({'type': 'assistant', 'timestamp': '1970-01-01T00:16:42Z',
                        'message': {'stop_reason': 'end_turn', 'content': []}})
    with path.open('a', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record) + '\n')


def _tick(env):
    store, parent, path, _now, _tmp = env
    return TranscriptReconciler(projects_home=path.parent / 'none').tick(
        store, {'parent': parent}, lambda p: {}, lambda p: [])


def test_a_sibling_turns_delivery_does_not_confirm_this_turn(env):
    """8e65fec33d4e:turn:3 was never written; turn:4's delivery confirmed it."""
    store = env[0]
    _queue_and_hand_off(env, 'kid:turn:3', _payload('kid:turn:3')['message'])
    _transcript_gets(env, _payload('kid:turn:4')['message'])
    assert _tick(env) == 0
    row = store.status('inbox', 'kid:turn:3', 'p')
    assert row['state'] != 'confirmed_in_transcript'


def test_its_own_delivery_still_confirms(env):
    store = env[0]
    _queue_and_hand_off(env, 'kid:turn:3', _payload('kid:turn:3')['message'])
    _transcript_gets(env, _payload('kid:turn:3')['message'])
    assert _tick(env) == 1
    assert store.status('inbox', 'kid:turn:3', 'p')['state'] == 'confirmed_in_transcript'


def test_a_header_without_an_id_reads_unverified_never_delivered(env):
    """A row queued before this change. The same line sits in the transcript
    (it may be a sibling turn's delivery): that must not read as delivered."""
    store = env[0]
    old = '[dispatched agent finished] Sol_Tobin (session childchildch) ended with status=completed.'
    _queue_and_hand_off(env, 'kid:turn:3', old + '\n\nreport')
    _transcript_gets(env, old + '\n\nreport')
    assert _tick(env) == 0
    row = store.status('inbox', 'kid:turn:3', 'p')
    assert row['state'] != 'confirmed_in_transcript'
    assert row['check_status'] == 'unverified_header'
    (report,) = dw.waiting_by_parent('p')['parent']
    assert report['stage'] == 'check_failed'
    assert 'no event id' in report['label']
