"""R51 (MC-1179, docs/_review/2026-11-16_02_agent_routes.md): `drain_once` ran
`recover_sources()` every 5 s, and it opened two SQLite connections and took the
write lock for EVERY completion source ever recorded, to find the ones whose
outbox row is missing -- normally none. Measured 4-6 s per call on a 2,198-row
install; cost linear in all completions ever recorded.

The cost is pinned by connection count, not wall time, so it cannot flake.
Behaviour is pinned beside it: a source that really is missing from the outbox
is still rebuilt, a revoked one is not, and a delivered row is left alone.
"""
from mc.delegation_delivery import DeliveryStore, callback_payload, drain_once


def _record(store, i, *, enqueue=True, parent='parent'):
    eid = f'child{i}:turn:1'
    payload = callback_payload(
        {'session_id': f'child{i}', 'status': 'completed', 'task': 't'}, f'result {i}', eid)
    store.record_completion_source(eid, 'p', parent, payload)
    if enqueue:
        assert store.enqueue(eid, 'p', parent, payload)
    return eid


def _count_connects(store, monkeypatch):
    opened = []
    real = store._connect
    monkeypatch.setattr(store, '_connect', lambda: opened.append(1) or real())
    return opened


def test_recover_sources_cost_does_not_grow_with_recorded_completions(tmp_path, monkeypatch):
    store = DeliveryStore(tmp_path / 'delivery.db')
    for i in range(200):
        _record(store, i)
    opened = _count_connects(store, monkeypatch)

    assert store.recover_sources() == 0

    # Before: 1 + 2 per source = 401. Constant now, whatever the history.
    assert len(opened) <= 2, f'{len(opened)} connections for 200 settled sources'


def test_drain_once_cost_does_not_grow_with_recorded_completions(tmp_path, monkeypatch):
    store = DeliveryStore(tmp_path / 'delivery.db')
    for i in range(200):
        _record(store, i)
    for _ in range(200):
        row = store.claim_outbox()
        store.ack_outbox(row['event_id'], row['fence_token'])
    opened = _count_connects(store, monkeypatch)

    assert drain_once(store, send_outbox=lambda r: None, process_inbox=lambda r: None) == 0

    # reconcile + recover_sources + claim_outbox + claim_inbox: a handful, not 400+.
    assert len(opened) <= 8, f'{len(opened)} connections for one idle drain'


def test_a_source_missing_from_the_outbox_is_still_rebuilt(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    for i in range(5):
        _record(store, i)
    lost = _record(store, 99, enqueue=False)
    assert store.status('outbox', lost, 'p') is None

    assert store.recover_sources() == 1

    assert store.status('outbox', lost, 'p')['state'] == 'pending'
    assert store.recover_sources() == 0


def test_a_revoked_source_is_not_rebuilt_and_a_delivered_row_is_left_alone(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    done = _record(store, 1)
    row = store.claim_outbox()
    store.ack_outbox(row['event_id'], row['fence_token'])
    gone = _record(store, 2, enqueue=False, parent='doomed')
    store.revoke_session('p', 'doomed')

    assert store.recover_sources() == 0

    assert store.status('outbox', gone, 'p') is None
    assert store.status('outbox', done, 'p')['state'] == 'delivered'
