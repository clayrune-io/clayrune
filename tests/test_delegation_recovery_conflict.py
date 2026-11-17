"""MC-1196: boot-time log recovery must not stop at the first conflicting row,
and a log row must not disagree with the completion already committed for it."""
import json

import pytest

from mc.delegation_delivery import (ConflictingEventIdentity, DeliveryStore,
                                    callback_payload, event_id_for_turn)


def _exact(child, turn, status='completed', summary='the result'):
    child_row = {'session_id': child, 'status': status, 'task': 't',
                 'provider': 'claude', 'character': {'agent_name': 'Tobin'}}
    return callback_payload(child_row, summary, event_id_for_turn(child, turn))


def _row(child, turn, exact):
    return {'session_id': child, 'spawned_by_session_id': 'parent',
            'status': 'completed', 'delegation_turn': turn,
            'delegation_completion': exact}


def test_recovery_skips_a_conflicting_row_and_queues_the_rest(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    # The store already holds turn 1 of `a` as it was sent at the turn boundary.
    sent = _exact('a', 1, status='idle')
    assert store.enqueue(sent['event_id'], 'p', 'parent', sent) is True
    # The log says the same event, rewritten at process exit as `completed`.
    log = [_row('a', 1, _exact('a', 1, status='completed')),   # conflicts
           _row('b', 1, _exact('b', 1))]                       # never queued
    assert store.recover_outbox(log, 'p') == 1
    assert store.status('outbox', 'b:turn:1', 'p') is not None
    # The conflicting row was neither queued again nor overwritten.
    stored = json.loads(store.status('outbox', 'a:turn:1', 'p')['payload'])['payload']
    assert stored['status'] == 'idle'


def test_conflict_is_logged_once_with_event_id_and_session(tmp_path, monkeypatch):
    import mc.delegation_delivery as dd
    lines = []
    monkeypatch.setattr(dd, '_log', lambda msg, **kw: lines.append(msg))
    store = DeliveryStore(tmp_path / 'delivery.db')
    sent = _exact('a', 1, status='idle')
    store.enqueue(sent['event_id'], 'p', 'parent', sent)
    store.recover_outbox([_row('a', 1, _exact('a', 1))], 'p')
    hits = [m for m in lines if 'a:turn:1' in m]
    assert len(hits) == 1 and 'session a' in hits[0] and 'conflicting' in hits[0]


def test_enqueue_still_raises_on_conflict(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    sent = _exact('a', 1, status='idle')
    store.enqueue(sent['event_id'], 'p', 'parent', sent)
    with pytest.raises(ConflictingEventIdentity):
        store.enqueue(sent['event_id'], 'p', 'parent', _exact('a', 1))
    with pytest.raises(ValueError):  # the old contract: still a ValueError
        store.enqueue(sent['event_id'], 'p', 'parent', _exact('a', 1))


def test_non_conflict_failure_still_aborts_recovery(tmp_path, monkeypatch):
    store = DeliveryStore(tmp_path / 'delivery.db')

    def boom(*a, **k):
        raise RuntimeError('disk gone')
    monkeypatch.setattr(store, 'enqueue', boom)
    with pytest.raises(RuntimeError):
        store.recover_outbox([_row('b', 1, _exact('b', 1))], 'p')


def test_log_row_is_pinned_to_the_committed_completion(tmp_path):
    """3b: Mode B enqueues at the turn boundary (status `idle`), then the exit
    path rebuilds the log row (status `completed`). The row must carry what was
    committed, so recovery sees the same event, not a conflicting one."""
    from mc.delegation_completion_pin import completion_for_row
    store = DeliveryStore(tmp_path / 'delivery.db')
    child = {'session_id': 'a', 'status': 'idle', 'task': 't',
             'provider': 'claude', 'character': {'agent_name': 'Tobin'},
             '_notify_session': 'parent', '_delegation_turn': 1}
    sent = callback_payload(child, 'the result', event_id_for_turn('a', 1))
    assert store.enqueue(sent['event_id'], 'p', 'parent', sent) is True
    child['status'] = 'completed'                       # process exit
    row = completion_for_row(store, 'p', child, 'the result')
    assert row == sent
    assert store.recover_outbox([_row('a', 1, row)], 'p') == 0   # no conflict, nothing new


def test_pin_falls_back_to_fresh_payload_when_nothing_committed(tmp_path):
    from mc.delegation_completion_pin import completion_for_row
    store = DeliveryStore(tmp_path / 'delivery.db')
    child = {'session_id': 'a', 'status': 'completed', 'task': 't',
             '_notify_session': 'parent', '_delegation_turn': 2}
    row = completion_for_row(store, 'p', child, 'r')
    assert row == callback_payload(child, 'r', event_id_for_turn('a', 2))
    assert completion_for_row(None, 'p', child, 'r') == row


def test_pin_leaves_a_different_parent_alone_and_skips_non_spawned(tmp_path):
    from mc.delegation_completion_pin import completion_for_row
    store = DeliveryStore(tmp_path / 'delivery.db')
    child = {'session_id': 'a', 'status': 'idle', 'task': 't',
             '_notify_session': 'other', '_delegation_turn': 1}
    old = callback_payload(child, 'r', event_id_for_turn('a', 1))
    store.enqueue(old['event_id'], 'p', 'other', old)
    child['_notify_session'] = 'parent'; child['status'] = 'completed'
    fresh = completion_for_row(store, 'p', child, 'r')
    assert fresh['status'] == 'completed'               # not pinned: real conflict
    assert completion_for_row(store, 'p', {'session_id': 'a'}, 'r') is None
    assert completion_for_row(store, 'p', {'session_id': 'a', '_notify_session': 'a'}, 'r') is None


def test_mode_b_boundary_then_exit_leaves_log_row_matching_the_store(monkeypatch, tmp_path):
    """The real sequence behind MC-1196: the wake is committed at the turn
    boundary (`idle`), the process then exits (`completed`) and rewrites the
    agent-log row. The row must agree with the store, so boot recovery has
    nothing to conflict with."""
    from mc.blueprints import agent_routes as ar
    store = DeliveryStore(tmp_path / 'delivery.db')
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(ar, '_delivery_store', store)
    monkeypatch.setattr(ar, '_write_session_memory', lambda *args, **kwargs: True)
    monkeypatch.setattr(ar, '_extract_transcript_telemetry', lambda *args: {})
    session = {'project_id': 'p', 'session_id': 'child',
               '_notify_session': 'parent', '_delegation_turn': 1,
               'status': 'idle', 'task': 'task', 'provider': 'claude',
               'log_lines': ['the exact result']}
    ar._maybe_notify_spawner(session, 'the exact result')          # turn boundary
    assert session['_notify_session_sent'] is True
    session['status'] = 'completed'                                 # process exit
    ar._log_agent_completion_body(session)
    rows = json.loads((tmp_path / 'p_agent_log.json').read_text(encoding='utf-8'))
    log_row = next(r for r in rows if r.get('session_id') == 'child')
    committed = json.loads(store.status('outbox', 'child:turn:1', 'p')['payload'])['payload']
    assert committed['status'] == 'idle'
    assert log_row['delegation_completion'] == committed
    assert store.recover_outbox(rows, 'p') == 0     # nothing conflicts, nothing new
