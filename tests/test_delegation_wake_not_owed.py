"""MC-1196: a finished turn that owed its spawner no wake must say so in its
agent-log row, so boot recovery never rebuilds a wake nobody was owed. A wake
that WAS owed and was lost in a crash must still be recovered."""
import json

from mc.delegation_completion_pin import delegation_row_fields
from mc.delegation_delivery import (WAKE_NOT_OWED_KEY, DeliveryStore,
                                    callback_payload, event_id_for_turn)


def _child(turn, **extra):
    child = {'session_id': 'kid', 'status': 'completed', 'task': 't',
             'provider': 'claude', 'character': {'agent_name': 'Tobin'},
             '_notify_session': 'parent', '_delegation_turn': turn}
    child.update(extra)
    return child


def _log_row(child, fields):
    row = {'session_id': child['session_id'], 'spawned_by_session_id': 'parent',
           'status': 'completed', 'delegation_turn': child['_delegation_turn']}
    row.update(fields)
    return row


def test_spent_latch_with_nothing_committed_marks_the_row_not_owed(tmp_path):
    """Turn 2 of a child a human followed up: the latch stayed spent from turn 1
    (MC-970), so no event exists for turn 2 and none was owed."""
    store = DeliveryStore(tmp_path / 'delivery.db')
    fields = delegation_row_fields(
        store, 'p', _child(2, _notify_session_sent=True), 'the answer')
    assert fields[WAKE_NOT_OWED_KEY] is True
    row = _log_row(_child(2), fields)
    assert store.recover_outbox([row], 'p') == 0          # this boot
    assert store.recover_outbox([row], 'p') == 0          # and the next
    assert store.status('outbox', event_id_for_turn('kid', 2), 'p') is None


def test_owed_and_lost_wake_is_still_recovered(tmp_path):
    """The latch is unset (the enqueue never committed: a crash or a fault), so
    the wake was owed. The row carries no mark and recovery queues it."""
    store = DeliveryStore(tmp_path / 'delivery.db')
    fields = delegation_row_fields(store, 'p', _child(2), 'the answer')
    assert WAKE_NOT_OWED_KEY not in fields
    assert store.recover_outbox([_log_row(_child(2), fields)], 'p') == 1
    assert store.status('outbox', event_id_for_turn('kid', 2), 'p') is not None


def test_committed_turn_is_not_marked_and_stays_pinned(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    sent = callback_payload(_child(1, status='idle'), 'r', event_id_for_turn('kid', 1))
    store.enqueue(sent['event_id'], 'p', 'parent', sent)
    fields = delegation_row_fields(store, 'p', _child(1, _notify_session_sent=True), 'r')
    assert WAKE_NOT_OWED_KEY not in fields
    assert fields['delegation_completion'] == sent


def test_unreadable_or_missing_store_never_marks_a_wake_as_not_owed():
    class Broken:
        def committed_payload(self, *a, **k):
            raise RuntimeError('db locked')
    for store in (None, Broken()):
        fields = delegation_row_fields(store, 'p', _child(2, _notify_session_sent=True), 'r')
        assert WAKE_NOT_OWED_KEY not in fields
        assert isinstance(fields['delegation_completion'], dict)


def test_a_session_nobody_spawned_gets_no_completion_and_no_mark(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    child = _child(1, _notify_session_sent=True)
    child['_notify_session'] = ''
    assert delegation_row_fields(store, 'p', child, 'r') == {'delegation_completion': None}


def _session(turn):
    return {'project_id': 'p', 'session_id': 'child', '_notify_session': 'parent',
            '_delegation_turn': turn, 'status': 'idle', 'task': 'task',
            'provider': 'claude', 'log_lines': ['the exact result']}


def _wire(monkeypatch, tmp_path):
    from mc.blueprints import agent_routes as ar
    store = DeliveryStore(tmp_path / 'delivery.db')
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(ar, '_delivery_store', store)
    monkeypatch.setattr(ar, '_write_session_memory', lambda *args, **kwargs: True)
    monkeypatch.setattr(ar, '_extract_transcript_telemetry', lambda *args: {})
    return ar, store


def _rows(tmp_path):
    return json.loads((tmp_path / 'p_agent_log.json').read_text(encoding='utf-8'))


def test_human_followup_turn_leaves_a_not_owed_row_through_the_real_path(monkeypatch, tmp_path):
    """Turn 1 reports. A human then follows up (`_advance_delegation_turn`
    leaves the latch spent), turn 2 finishes: `_maybe_notify_spawner` suppresses,
    and the row written for turn 2 must be marked."""
    ar, store = _wire(monkeypatch, tmp_path)
    session = _session(1)
    ar._maybe_notify_spawner(session, 'the exact result')
    session['status'] = 'completed'
    ar._log_agent_completion_body(session)
    session['_delegation_turn'] = 2                       # the human's follow-up turn
    session['log_lines'].append('second answer')
    ar._maybe_notify_spawner(session, 'second answer')    # suppressed: latch spent
    ar._log_agent_completion_body(session)
    rows = [r for r in _rows(tmp_path) if r.get('session_id') == 'child']
    turn2 = next(r for r in rows if r.get('delegation_turn') == 2)
    assert turn2[WAKE_NOT_OWED_KEY] is True
    assert store.status('outbox', 'child:turn:2', 'p') is None
    assert store.recover_outbox(_rows(tmp_path), 'p') == 0
    assert store.recover_outbox(_rows(tmp_path), 'p') == 0
    assert store.status('outbox', 'child:turn:2', 'p') is None


def test_enqueue_fault_leaves_an_unmarked_row_that_recovery_queues(monkeypatch, tmp_path):
    """The wake was owed (spawner armed, latch unset) and the enqueue faulted, so
    only the log row carries it. No mark; the next boot queues it."""
    ar, store = _wire(monkeypatch, tmp_path)
    real = store.enqueue_outcome

    def fault(*a, **k):
        raise RuntimeError('disk gone')
    monkeypatch.setattr(store, 'enqueue_outcome', fault)
    session = _session(1)
    ar._maybe_notify_spawner(session, 'the exact result')
    assert not session.get('_notify_session_sent')
    session['status'] = 'completed'
    ar._log_agent_completion_body(session)
    monkeypatch.setattr(store, 'enqueue_outcome', real)
    rows = [r for r in _rows(tmp_path) if r.get('session_id') == 'child']
    assert WAKE_NOT_OWED_KEY not in rows[-1]
    assert store.recover_outbox(_rows(tmp_path), 'p') == 1
    assert store.status('outbox', 'child:turn:1', 'p') is not None
