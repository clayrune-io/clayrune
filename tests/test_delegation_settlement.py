"""Absence must be evidence, not an idle/age/unchanged-file heuristic."""
import json
import sqlite3
import threading

import pytest
from flask import Flask

from mc import delegation_settlement as ds
from mc import delegation_waiting as dw
from mc.blueprints.delegation_report_routes import register_routes
from mc.delegation_delivery import DeliveryStore
from mc.delegation_transcript_reconcile import TranscriptReconciler

HEADER = '[dispatched agent finished] Sol_Tobin (session child) ended with status=completed.'
EVENT = 'child:turn:1'


def test_old_database_upgrades_without_inventing_historical_evidence(tmp_path):
    path = tmp_path / 'old.db'
    payload = json.dumps({'payload': {'message': HEADER}})
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE inbox (event_id TEXT PRIMARY KEY,project_id TEXT NOT NULL,"
                   "parent_session_id TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'pending',"
                   "attempts INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL DEFAULT 0,"
                   "lease_until REAL NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '',"
                   "created_at REAL NOT NULL,accepted_at REAL NOT NULL)")
        db.execute("INSERT INTO inbox(event_id,project_id,parent_session_id,payload,state,created_at,accepted_at) "
                   "VALUES(?,?,?,?,'uncertain',1,1)", (EVENT, 'p', 'parent', payload))
    store = DeliveryStore(path)
    existing = store.status('inbox', EVENT, 'p')
    assert existing['payload'] == payload and existing['state'] == 'uncertain'
    assert existing['handoff_boundary'] == existing['turn_completion'] == ''
    assert existing['auto_resends'] == existing['check_requested'] == 0
    assert ds.evaluate(existing, None) == ('no_boundary', None)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    now = [1000.0]
    store = DeliveryStore(tmp_path / 'delivery.db', clock=lambda: now[0])
    path = tmp_path / 'native.jsonl'
    parent = {'session_id': 'parent', 'project_id': 'p', 'provider': 'claude',
              'claude_session_id': 'native', '_agent_cwd': str(tmp_path), 'status': 'idle'}
    baseline = {'type': 'user', 'sessionId': 'native', 'cwd': str(tmp_path),
                'timestamp': '1970-01-01T00:16:39Z', 'message': {'content': 'original task'}}
    path.write_text(json.dumps(baseline) + '\n', encoding='utf-8')
    monkeypatch.setattr(ds, '_locate', lambda s: (str(path), ''))
    payload = {'message': HEADER + '\nreport', 'who': 'Sol_Tobin', 'child_session_id': 'child'}
    store.enqueue(EVENT, 'p', 'parent', payload)
    store.accept(EVENT, 'p', 'parent', payload)
    claim = store.claim_inbox()
    store.record_handoff(claim, parent)
    store.record_handoff_response(claim, {'session_id': 'parent', 'stdin_write_ack': 'written'}, needs_ack=True)
    store.finish_inbox(EVENT, claim['fence_token'], 'acknowledgment lost', state='uncertain')
    now[0] = 1002.0
    dw.configure(store.path)
    yield store, parent, path, now
    dw.configure(None)


def row(setup):
    return setup[0].status('inbox', EVENT, 'p')


def append(setup, *, delivered=False, terminal=True):
    _, parent, path, _ = setup
    records = []
    if delivered:
        records.append({'type': 'user', 'timestamp': '1970-01-01T00:16:41Z',
                        'message': {'content': HEADER + '\nreport'}})
    if terminal:
        records.append({'type': 'assistant', 'timestamp': '1970-01-01T00:16:42Z',
                        'message': {'stop_reason': 'end_turn', 'content': []}})
    with path.open('a', encoding='utf-8') as stream:
        for record in records:
            stream.write(json.dumps(record) + '\n')


def tick(setup, rec=None):
    store, parent, _, _ = setup
    rec = rec or TranscriptReconciler(projects_home=setup[2].parent / 'empty')
    return rec.tick(store, {'parent': parent}, lambda p: {}, lambda p: [])


def test_proven_delivered_clears_both_transport_copies_without_resend(setup):
    append(setup, delivered=True)
    assert tick(setup) == 1
    assert row(setup)['state'] == 'confirmed_in_transcript'
    assert setup[0].status('outbox', EVENT, 'p')['state'] == 'confirmed_in_transcript'
    assert row(setup)['auto_resends'] == 0
    assert dw.waiting_by_parent('p') == {}


def test_positive_receipt_does_not_need_idle_or_a_completed_turn(setup):
    setup[1]['status'] = 'running'
    append(setup, delivered=True, terminal=False)
    assert tick(setup) == 1
    assert row(setup)['state'] == 'confirmed_in_transcript'


@pytest.mark.parametrize('stamp', ['', '1970-01-01T00:16:40Z', '1970-01-01T00:16:39Z'])
def test_unusable_matching_receipt_timestamp_cannot_become_absence(setup, stamp):
    store, parent, path, _ = setup
    append(setup)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'type': 'user', 'timestamp': stamp, 'message': {'content': HEADER}}) + '\n')
    store.record_turn_completion(parent)
    assert tick(setup) == 0
    assert row(setup)['auto_resends'] == 0 and row(setup)['check_status'] == 'ambiguous_receipt'


def test_semantically_malformed_user_record_is_incomplete_not_absence(setup):
    store, parent, path, _ = setup
    append(setup)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'type': 'user', 'message': {'content': [{'type': 'text', 'text': 42}]}}) + '\n')
    store.record_turn_completion(parent)
    assert tick(setup) == 0
    assert row(setup)['auto_resends'] == 0 and row(setup)['check_status'] == 'partial'


def test_absent_after_completed_turn_uses_existing_retry_once(setup):
    store, parent, _, now = setup
    append(setup)
    store.record_turn_completion(parent)
    assert tick(setup) == 1
    assert row(setup)['state'] == 'pending'
    assert row(setup)['auto_resends'] == 1
    second = store.claim_inbox()
    now[0] = 1003
    store.record_handoff(second, parent)
    store.record_handoff_response(second, {'session_id': 'parent', 'stdin_write_ack': 'written'}, needs_ack=True)
    store.finish_inbox(EVENT, second['fence_token'], 'second acknowledgment lost', state='uncertain')
    now[0] = 1005
    with setup[2].open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'type': 'assistant', 'timestamp': '1970-01-01T00:16:45Z',
                                 'message': {'stop_reason': 'end_turn'}}) + '\n')
    store.record_turn_completion(parent)
    assert tick(setup) == 0
    assert row(setup)['state'] == 'recovery_required'
    assert row(setup)['check_status'] == 'not_delivered'
    report = dw.waiting_by_parent('p')['parent'][0]
    assert report['stage'] == 'failed' and report['label'] == 'report from Sol_Tobin was not delivered'
    # Human retry works, but does not restore the automatic budget.
    assert store.retry('inbox', EVENT, 'p', reviewed=True)
    assert row(setup)['auto_resends'] == 1
    assert dw.waiting_by_parent('p')['parent'][0]['stage'] == 'waiting'


@pytest.mark.parametrize('fault,expected', [('missing', 'missing'), ('unreadable', 'unreadable'),
                                          ('partial', 'partial'), ('malformed', 'partial')])
def test_failed_reads_are_not_absence_and_offer_check_only_retry(setup, monkeypatch, fault, expected):
    store, parent, path, _ = setup
    append(setup)
    store.record_turn_completion(parent)
    if fault == 'missing':
        path.unlink()
    elif fault == 'unreadable':
        original = ds.Path.open
        def refused(p, *args, **kwargs):
            if p == path:
                raise PermissionError('refused')
            return original(p, *args, **kwargs)
        monkeypatch.setattr(ds.Path, 'open', refused)
    else:
        with path.open('ab') as stream:
            stream.write(b'{"type":' if fault == 'partial' else b'not json\n')
    assert tick(setup) == 0
    assert row(setup)['state'] == 'uncertain' and row(setup)['auto_resends'] == 0
    assert row(setup)['check_status'] == expected
    report = dw.waiting_by_parent('p')['parent'][0]
    assert report['stage'] == 'check_failed' and report['retry_action'] == 'check'
    assert ds.CHECK_ERRORS[expected] in report['label']
    assert 'may not have arrived' not in report['label']


@pytest.mark.parametrize('flag', ['waiting_for_question', 'pending_questions'])
def test_question_wait_is_neither_completion_nor_absence(setup, flag):
    store, parent, _, _ = setup
    append(setup)
    parent[flag] = True
    store.record_turn_completion(parent)
    assert row(setup)['turn_completion'] == ''
    assert tick(setup) == 0
    assert row(setup)['check_status'] == 'confirming'
    assert dw.waiting_by_parent('p')['parent'][0]['stage'] == 'delivering'


@pytest.mark.parametrize('case', ['no_provider_end', 'no_native_end', 'old_native_end', 'future_native_end',
                                  'running', 'writer_pending', 'queued', 'unowned', 'rewritten', 'rollover', 'unfinished_handoff'])
def test_each_required_evidence_gate_refuses_absence(setup, case):
    store, parent, path, _ = setup
    append(setup, terminal=case not in ('no_native_end', 'old_native_end'))
    if case != 'no_provider_end':
        store.record_turn_completion(parent)
    if case == 'old_native_end':
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'type': 'assistant', 'timestamp': '1970-01-01T00:16:39Z',
                                     'message': {'stop_reason': 'end_turn'}}) + '\n')
    elif case == 'running':
        parent['status'] = 'running'
    elif case == 'writer_pending':
        parent['_stdin_write_uncertain'] = True
    elif case == 'queued':
        with sqlite3.connect(store.path) as db:
            db.execute("UPDATE inbox SET last_error='parent submission entered an in-memory queue'")
    elif case in ('unowned', 'rewritten'):
        text = path.read_text(encoding='utf-8').replace('native' if case == 'unowned' else 'original task',
                                                      'foreign' if case == 'unowned' else 'rewritten task')
        path.write_text(text, encoding='utf-8')
    elif case == 'rollover':
        completion = json.loads(row(setup)['turn_completion'])
        completion['native'] = 'new-provider-id'
        with sqlite3.connect(store.path) as db:
            db.execute('UPDATE inbox SET turn_completion=?', (json.dumps(completion),))
    elif case == 'future_native_end':
        completion = json.loads(row(setup)['turn_completion'])
        completion['at'] = 1001
        with sqlite3.connect(store.path) as db:
            db.execute('UPDATE inbox SET turn_completion=?', (json.dumps(completion),))
    elif case == 'unfinished_handoff':
        boundary = json.loads(row(setup)['handoff_boundary'])
        boundary['submission_settled'] = False
        with sqlite3.connect(store.path) as db:
            db.execute('UPDATE inbox SET handoff_boundary=?', (json.dumps(boundary),))
    assert tick(setup) == 0
    assert row(setup)['state'] == 'uncertain' and row(setup)['auto_resends'] == 0


def test_restart_mid_resend_does_not_grant_another_resend(setup):
    store, parent, _, now = setup
    append(setup)
    store.record_turn_completion(parent)
    assert tick(setup) == 1
    claim = store.claim_inbox()
    now[0] = 1003
    store.record_handoff(claim, parent)
    # Crash before submit/ack. Its expired claim becomes uncertain on reopen.
    now[0] = 1100
    reopened = DeliveryStore(store.path, clock=lambda: now[0])
    reopened.reconcile()
    assert reopened.status('inbox', EVENT, 'p')['auto_resends'] == 1
    rec = TranscriptReconciler(projects_home=setup[2].parent / 'empty')
    assert rec.tick(reopened, {}, lambda p: {}, lambda p: []) == 0
    assert reopened.claim_inbox() is None
    assert reopened.status('inbox', EVENT, 'p')['state'] == 'uncertain'


def test_completed_evidence_survives_restart_but_resend_budget_is_atomic(setup):
    store, parent, _, now = setup
    append(setup)
    store.record_turn_completion(parent)
    proof = ds.evaluate(row(setup), parent)[1]
    reopened = DeliveryStore(store.path, clock=lambda: now[0])
    barrier = threading.Barrier(2)
    outcomes = []
    def resend(s):
        barrier.wait()
        outcomes.append(s.retry('inbox', EVENT, 'p', absence_proof=proof))
    threads = [threading.Thread(target=resend, args=(s,)) for s in (store, reopened)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(3)
    assert sorted(outcomes) == [False, True]
    assert row(setup)['auto_resends'] == 1


@pytest.mark.parametrize('race', ['receipt_added', 'handled', 'claim_changed', 'revoked'])
def test_store_revalidates_proof_before_resending(setup, race):
    store, parent, _, _ = setup
    append(setup)
    store.record_turn_completion(parent)
    proof = ds.evaluate(row(setup), parent)[1]
    if race == 'receipt_added':
        append(setup, delivered=True)
    elif race == 'handled':
        store.mark_handled(EVENT, 'p')
    elif race == 'revoked':
        store.revoke_event('p', EVENT)
    else:
        with sqlite3.connect(store.path) as db:
            db.execute("UPDATE inbox SET fence_token='new-claim'")
    assert not store.retry('inbox', EVENT, 'p', absence_proof=proof)
    if race != 'revoked':
        assert row(setup)['auto_resends'] == 0


def test_prompt_reconcile_bypasses_full_sweep_gate_for_new_rows_and_completions(setup):
    store, parent, _, _ = setup
    rec = TranscriptReconciler(projects_home=setup[2].parent / 'empty')
    # First scan starts the five-minute backstop gate; idle is no receipt.
    assert tick(setup, rec) == 0
    assert rec._next_check == 1302
    append(setup)
    store.record_turn_completion(parent)
    assert tick(setup, rec) == 1  # same clock, new durable completion
    assert row(setup)['auto_resends'] == 1


def test_new_uncertain_row_is_checked_on_next_tick_not_full_gate(setup):
    store, _, _, _ = setup
    rec = TranscriptReconciler(projects_home=setup[2].parent / 'empty')
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE inbox SET state='pending'")
    assert tick(setup, rec) == 0
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE inbox SET state='uncertain'")
    append(setup, delivered=True)
    assert tick(setup, rec) == 1


def test_partial_log_repaired_is_checked_promptly_without_timer_heuristic(setup):
    store, parent, path, _ = setup
    append(setup)
    store.record_turn_completion(parent)
    good = path.read_bytes()
    path.write_bytes(good + b'{')
    rec = TranscriptReconciler(projects_home=path.parent / 'empty')
    assert tick(setup, rec) == 0
    assert row(setup)['check_status'] == 'partial'
    path.write_bytes(good)
    assert tick(setup, rec) == 1


def test_retry_check_is_scoped_and_never_changes_submission_or_budget(setup):
    store, _, _, _ = setup
    app = Flask(__name__)
    register_routes(app, store_fn=lambda: store)
    client = app.test_client()
    url = '/api/project/p/agent/delegation/check'
    for body in ({}, [], {'event_id': 42}, {'event_id': ' '}):
        assert client.post(url, json=body).status_code == 400
    assert client.post(url.replace('/p/', '/q/'), json={'event_id': EVENT}).status_code == 404
    before = row(setup)
    assert client.post(url, json={'event_id': EVENT}).status_code == 200
    after = row(setup)
    assert after['check_requested'] == before['check_requested'] + 1
    for field in ('state', 'auto_resends', 'handoff_boundary', 'turn_completion', 'attempts'):
        assert after[field] == before[field]


def test_native_codex_transcript_uses_its_own_identity_and_user_text(setup):
    store, parent, path, _ = setup
    parent.update(provider='codex', provider_session_id='codex-native')
    path.write_text(json.dumps({'type': 'session_meta', 'payload': {
        'id': 'codex-native', 'cwd': parent['_agent_cwd']}}) + '\n', encoding='utf-8')
    store.retry('inbox', EVENT, 'p', reviewed=True)
    claim = store.claim_inbox()
    store.record_handoff(claim, parent)
    store.finish_inbox(EVENT, claim['fence_token'], 'lost ack', state='uncertain')
    store.record_handoff_response(claim, {'session_id': 'parent'}, needs_ack=False)
    setup[3][0] = 1004
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'type': 'event_msg', 'timestamp': '1970-01-01T00:16:44Z',
                                 'payload': {'type': 'task_complete'}}) + '\n')
    store.record_turn_completion(parent)
    assert ds.evaluate(row(setup), parent)[0] == 'absent'
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'type': 'response_item', 'timestamp': '1970-01-01T00:16:43Z',
                                 'payload': {'role': 'user', 'content': [
                                     {'type': 'input_text', 'text': HEADER}]}}) + '\n')
    assert ds.evaluate(row(setup), parent)[0] == 'delivered'


def test_expired_boundary_claim_cannot_start_a_handoff(setup):
    store, parent, _, now = setup
    store.retry('inbox', EVENT, 'p', reviewed=True)
    claim = store.claim_inbox()
    now[0] += 31
    with pytest.raises(RuntimeError, match='lost its claim'):
        store.record_handoff(claim, parent)
    assert row(setup)['handoff_boundary'] == ''


def test_runtime_completion_hook_ignores_question_wait_after_provider_end(setup):
    store, parent, _, _ = setup
    called = []
    callbacks = ds.runtime_callbacks({'on_process_exit': lambda e, s: called.append('exit')})
    callbacks['on_turn_end'](None, parent)
    parent['waiting_for_question'] = True
    ds.note_runtime_exit(parent, store)
    callbacks['on_process_exit'](None, parent)
    assert called == ['exit'] and row(setup)['turn_completion'] == ''


def test_runtime_exit_without_provider_turn_end_does_not_settle(setup):
    store, parent, _, _ = setup
    append(setup)
    ds.note_runtime_exit(parent, store)
    assert row(setup)['turn_completion'] == ''
    callbacks = ds.runtime_callbacks({'on_process_exit': lambda e, s: None})
    callbacks['on_turn_end'](None, parent)
    ds.note_runtime_exit(parent, store)
    assert row(setup)['turn_completion']


def test_claude_result_hook_preserves_existing_callback_and_excludes_error_results(setup):
    store, parent, _, _ = setup
    called = []
    ds.note_claude_turn(parent, {'is_error': True}, store, lambda s: called.append(s))
    assert called == [parent] and row(setup)['turn_completion'] == ''
    ds.note_claude_turn(parent, {'is_error': False}, store, lambda s: called.append(s))
    assert row(setup)['turn_completion']


def test_non_report_notification_keeps_its_existing_submission_semantics(setup):
    store, parent, _, _ = setup
    payload = {'message': '[background job completed] job-1', 'child_session_id': 'job-1'}
    store.enqueue('job-1', 'p', 'parent', payload)
    store.accept('job-1', 'p', 'parent', payload)
    claim = store.claim_inbox()
    store.record_handoff(claim, parent)
    assert store.status('inbox', 'job-1', 'p')['handoff_boundary'] == ''
    assert parent.get('_report_attempt') is None
    store.submit_inbox('job-1', claim['fence_token'], {'stdin_write_ack': 'written'})
    assert all(r['event_id'] != 'job-1' for r in dw.waiting_by_parent('p')['parent'])


def test_real_parent_handoff_records_boundary_before_call_and_claude_result_completes_it(setup, monkeypatch):
    from types import SimpleNamespace
    from mc.blueprints import agent_routes as ar
    store, parent, _, now = setup
    store.retry('inbox', EVENT, 'p', reviewed=True)
    claim = store.claim_inbox()
    parent['mode'] = 'B'
    monkeypatch.setattr(ar, '_delivery_store', store)
    monkeypatch.setattr(ar, 'agent_sessions', {'parent': parent})
    monkeypatch.setattr(ar, 'get_manager', lambda p: SimpleNamespace(lock=threading.RLock()))
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a: '')
    monkeypatch.setattr(ar, '_delegation_app', Flask('settlement-wiring'))
    def followup(pid):
        assert row(setup)['handoff_boundary'] and not row(setup)['turn_completion']
        with setup[2].open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'type': 'assistant', 'timestamp': '1970-01-01T00:16:43Z',
                                     'message': {'stop_reason': 'end_turn'}}) + '\n')
        now[0] = 1004
        ds.note_claude_turn(parent, {}, store, lambda s: None)
        return {'session_id': 'parent', 'stdin_write_ack': 'written'}
    monkeypatch.setattr(ar, 'agent_followup', followup)
    evidence = ar._process_inbox(claim)
    store.submit_inbox(EVENT, claim['fence_token'], evidence)
    assert row(setup)['turn_completion']
    assert tick(setup) == 1
    assert row(setup)['auto_resends'] == 1


def test_settlement_holds_the_same_parent_guard_during_store_retry(setup):
    store, parent, _, _ = setup
    append(setup)
    store.record_turn_completion(parent)
    locked = []
    class Guard:
        def __enter__(self):
            locked.append(True)
        def __exit__(self, *args):
            locked.pop()
    original = store.retry
    def retry(*args, **kwargs):
        assert locked == [True]
        return original(*args, **kwargs)
    store.retry = retry
    rec = TranscriptReconciler(parent_guard=lambda p: Guard())
    assert tick(setup, rec) == 1
