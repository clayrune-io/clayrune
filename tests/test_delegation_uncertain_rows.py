"""Rows `reconcile()` marks 'uncertain' after a restart stay uncertain (Dave's
call, 2026-10-05: no auto-retry, a handoff may have reached the parent). What
this pins instead: each one logs why when it is marked, and the status list
carries enough to settle it by hand."""
from __future__ import annotations

import json

from flask import Flask

import mc.delegation_delivery as dd
from mc.blueprints import agent_routes as ar
from mc.delegation_delivery import DeliveryStore, uncertain_cause

PAYLOAD = {'message': 'DO NOT LEAK', 'child_session_id': 'kid', 'summary': 'DO NOT LEAK'}


def _in_flight(tmp_path, event_id='kid:turn:2', project='p'):
    """An inbox row claimed (dispatch_intent) and then abandoned by a restart."""
    now = [100.0]
    store = DeliveryStore(tmp_path / 'd.db', clock=lambda: now[0])
    assert store.enqueue(event_id, project, 'dave', PAYLOAD)
    row = store.claim_outbox()
    store.ack_outbox(row['event_id'], row['fence_token'])
    assert store.accept(event_id, project, 'dave', PAYLOAD)
    assert store.claim_inbox()['state'] == 'dispatch_intent'
    now[0] += 10_000                                    # the lease is long gone
    return store


def test_reconcile_logs_why_for_each_row_it_marks(tmp_path, monkeypatch):
    logged = []
    monkeypatch.setattr(dd, '_log', lambda *a, **k: logged.append(' '.join(str(x) for x in a)))
    store = _in_flight(tmp_path)
    assert store.reconcile() == 1
    assert store.status('inbox', 'kid:turn:2', 'p')['state'] == 'uncertain'
    lines = [l for l in logged if 'kid:turn:2' in l]
    assert len(lines) == 1
    assert 'marked uncertain' in lines[0] and 'dave' in lines[0]
    assert 'in flight when the server stopped' in lines[0]
    assert '/api/project/p/agent/delegation/status-list' in lines[0]
    assert store.reconcile() == 0                       # already marked: no second line
    assert len([l for l in logged if 'kid:turn:2' in l]) == 1


def test_status_list_says_what_an_uncertain_row_needs(tmp_path, monkeypatch):
    store = _in_flight(tmp_path)
    store.reconcile()
    app = Flask('uncertain-rows')
    app.register_blueprint(ar.bp)
    monkeypatch.setattr(ar, '_delivery_store', store)
    body = app.test_client().get('/api/project/p/agent/delegation/status-list').get_json()
    (item,) = body['items']
    assert item['state'] == 'uncertain' and item['recovery_required'] is True
    assert item['cause_code'] == 'restart_during_handoff'
    assert item['child_session_id'] == 'kid'
    assert item['parent_session_id'] == 'dave'
    assert '/api/project/p/agent/delegation/retry' in item['action']
    assert '"event_id": "kid:turn:2"' in item['action'] and '"table": "inbox"' in item['action']
    assert 'reviewed_resolution' in item['action'] and 'never retried automatically' in item['action']
    assert 'DO NOT LEAK' not in json.dumps(body)


def test_pre_handoff_cause_says_a_retry_is_safe(tmp_path):
    store = _in_flight(tmp_path)
    store.reconcile()
    # A pre-handoff row filed uncertain before this fix (live DB has 5).
    with store._db() as db:
        db.execute("UPDATE inbox SET last_error='inbox claim expired while waiting for parent lock'")
    (item,) = store.list_recovery_status('p')['items']
    assert item['cause_code'] == 'claim_expired_before_handoff'
    assert 'cannot duplicate' in item['action']


def test_non_uncertain_rows_carry_no_cause_or_action(tmp_path):
    store = DeliveryStore(tmp_path / 'd.db')
    store.enqueue('e1', 'p', 'dave', PAYLOAD)
    (item,) = store.list_recovery_status('p')['items']
    assert item['state'] == 'pending'
    assert (item['cause_code'], item['action']) == ('', '')


def test_every_known_uncertain_error_has_a_cause():
    for text, code in (
            ('parent stdin write acknowledgment is unknown', 'parent_stdin_ack_unknown'),
            ('parent durable outcome is ambiguous: stopped', 'parent_state_ambiguous'),
            ('guarded parent submission returned HTTP 503', 'parent_submission_http_error'),
            ('something nobody wrote down', 'unrecorded'),
            ('', 'unrecorded')):
        assert uncertain_cause(text)[0] == code
