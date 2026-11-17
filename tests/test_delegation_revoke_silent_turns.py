"""MC-1196: tools/delegation_revoke_silent_turns.py refuses the old completions
that never owed a wake, and only those."""
import importlib.util
import json
import sqlite3
from pathlib import Path

from mc.delegation_delivery import DeliveryStore, callback_payload, event_id_for_turn

_TOOL = Path(__file__).resolve().parent.parent / 'tools' / 'delegation_revoke_silent_turns.py'
_spec = importlib.util.spec_from_file_location('delegation_revoke_silent_turns', _TOOL)
assert _spec is not None and _spec.loader is not None
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)


def _exact(child, turn):
    return callback_payload({'session_id': child, 'status': 'completed', 'task': 't'},
                            'r', event_id_for_turn(child, turn))


def _row(child, turn):
    return {'session_id': child, 'spawned_by_session_id': 'parent', 'status': 'completed',
            'delegation_turn': turn, 'delegation_completion': _exact(child, turn)}


def _suppressed(child, turn):
    return (f'[notify-spawner] callback suppressed for {child}: spawner latch '
            f'_notify_session_sent already set for turn {turn}\n')


def _world(tmp_path):
    """silent: suppressed, never stored.  lost: absent, no log evidence.
    sent: suppressed on the exit call AFTER a committed wake.  src: only a
    completion_sources row.  Returns (data_dir, store, rows)."""
    data = tmp_path / 'data'
    (data / 'projects').mkdir(parents=True)
    (data / 'logs').mkdir()
    store = DeliveryStore(data / 'delegation_delivery.sqlite3')
    rows = [_row('silent', 2), _row('lost', 2), _row('sent', 1), _row('src', 2)]
    sent = _exact('sent', 1)
    store.enqueue(sent['event_id'], 'p', 'parent', sent)
    src = _exact('src', 2)
    store.record_completion_source(src['event_id'], 'p', 'parent', src)
    (data / 'projects' / 'p_agent_log.json').write_text(json.dumps(rows), encoding='utf-8')
    (data / 'logs' / 'server.log').write_text(
        'noise\n' + _suppressed('silent', 2) + _suppressed('sent', 1) +
        _suppressed('src', 2) + _suppressed('lost', 1) + 'more noise\n', encoding='utf-8')
    return data, store, rows


def _find(data):
    return tool.find_silent_turns(data / 'projects', data / 'delegation_delivery.sqlite3',
                                  sorted((data / 'logs').glob('*.log')))


def test_selects_only_the_turn_with_evidence_and_no_stored_event(tmp_path):
    data, _store, _rows = _world(tmp_path)
    provable, unproven = _find(data)
    assert [p['event_id'] for p in provable] == ['silent:turn:2']
    assert provable[0]['child'] == 'silent' and provable[0]['parent'] == 'parent'
    assert provable[0]['turn'] == 2 and provable[0]['project'] == 'p'
    # `lost` turn 2 has a suppression line only for turn 1: not the same turn.
    assert [u['event_id'] for u in unproven] == ['lost:turn:2']


def test_dry_run_writes_nothing(tmp_path, capsys):
    data, _store, _rows = _world(tmp_path)
    db_path = data / 'delegation_delivery.sqlite3'
    before = db_path.read_bytes()
    assert tool.main(['--data-dir', str(data)]) == 0
    assert db_path.read_bytes() == before
    out = capsys.readouterr().out
    assert 'silent:turn:2' in out and 'child=silent' in out and 'parent=parent' in out
    assert 'turn=2' in out and 'UNPROVEN' in out and 'dry run' in out


def test_apply_refuses_the_silent_turn_and_recovery_never_queues_it(tmp_path):
    data, store, rows = _world(tmp_path)
    assert tool.main(['--data-dir', str(data), '--apply']) == 0
    db = sqlite3.connect(data / 'delegation_delivery.sqlite3')
    revoked = {r[0] for r in db.execute('SELECT event_id FROM revocations')}
    db.close()
    assert revoked == {'silent:turn:2'}
    # Recovery: the silent turn is refused; `lost` (no evidence) and `src` (an
    # enqueue that left only its completion source) are still recovered.
    assert store.recover_outbox(rows, 'p') == 2
    assert store.status('outbox', 'silent:turn:2', 'p') is None
    assert store.status('outbox', 'lost:turn:2', 'p') is not None


def test_apply_never_touches_events_that_are_in_the_store(tmp_path):
    data, store, _rows = _world(tmp_path)
    before = {t: store_rows for t, store_rows in _dump(data).items()}
    tool.main(['--data-dir', str(data), '--apply'])
    after = _dump(data)
    for table in ('outbox', 'inbox', 'completion_sources'):
        assert after[table] == before[table]


def test_rerun_selects_nothing_more(tmp_path):
    data, _store, _rows = _world(tmp_path)
    tool.main(['--data-dir', str(data), '--apply'])
    provable, unproven = _find(data)
    assert provable == []
    assert [u['event_id'] for u in unproven] == ['lost:turn:2']


def _dump(data):
    db = sqlite3.connect(data / 'delegation_delivery.sqlite3')
    try:
        return {t: db.execute(f'SELECT * FROM {t} ORDER BY event_id').fetchall()
                for t in ('outbox', 'inbox', 'completion_sources')}
    finally:
        db.close()
