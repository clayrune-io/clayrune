"""Crash-safe source cursor tests using only temporary fixture files/SQLite."""
import hashlib
import json
import sqlite3

import pytest

from mc.capture_replay import SourceRecoveryError, replay_file_prefix
from mc.conversation_store import ConversationStore, EventConflict
from mc.codex_capture import CodexCapture


@pytest.fixture
def claimed(tmp_path):
    store = ConversationStore(tmp_path / 'capture.sqlite')
    engine = {'provider': 'codex', 'model': 'fixture', 'effort': 'high'}
    store.create_lifecycle_conversation('p', 'c', engine=engine, event_id='create')
    state = store.accept_request('p', 'c', request_id='r', user_message={'text': 'x'},
        engine=engine, provenance={'origin': 'interactive'}, expected_revision=0, event_id='accept')
    state, owner = store.claim_owner('p', 'c', owner_id='o', expected_revision=state.revision, event_id='owner')
    _, token = store.claim_attempt(owner, request_id='r', attempt_id='a', expected_revision=state.revision, event_id='claim')
    return store, token, tmp_path / 'native.fixture'


def write_frames(path, frames):
    path.write_bytes(b''.join((json.dumps(frame, ensure_ascii=False) + '\n').encode('utf-8') for frame in frames))


def replay(store, token, path):
    return replay_file_prefix(path=path, store=store, token=token, provider='codex',
        format_version='codex-exec-jsonl-0.151', source_id='fixture-source', incarnation='v1',
        decoder_factory=lambda: CodexCapture(format_version='codex-exec-jsonl-0.151'))


def test_restart_rebuilds_decoder_state_and_replays_idempotently(claimed):
    store, token, path = claimed
    write_frames(path, [
        {'type': 'item.started', 'item': {'id': 'shell', 'type': 'command_execution', 'command': 'x'}},
        {'type': 'item.completed', 'item': {'id': 'shell', 'type': 'command_execution', 'command': 'x', 'aggregated_output': 'out', 'exit_code': 0}},
        {'type': 'item.completed', 'item': {'id': 'answer', 'type': 'agent_message', 'text': 'done'}},
    ])
    first = replay(store, token, path)
    reopened = ConversationStore(store.db_path)
    second = replay(reopened, token, path)
    assert first.cursor_sequence == second.cursor_sequence == 2
    assert len(reopened.read_events('p', 'c')) == 12
    assert sum(e.kind == 'tool_result' for e in reopened.read_events('p', 'c')) == 1


def test_atomic_multievent_fault_leaves_staged_span_for_retry(claimed, monkeypatch):
    store, token, path = claimed
    write_frames(path, [{'type': 'item.completed', 'item': {'id': 'a', 'type': 'agent_message', 'text': 'x'}}])
    original = store._lifecycle_event
    monkeypatch.setattr(store, '_lifecycle_event', lambda *a, **k: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError):
        replay(store, token, path)
    assert store.read_capture_cursor(token).cursor_sequence == -1
    staged = store.read_capture_span(token, source_sequence=0)
    assert staged is not None and staged[2] is False
    monkeypatch.setattr(store, '_lifecycle_event', original)
    assert replay(store, token, path).cursor_sequence == 0


def test_empty_span_advances_cursor_and_hash_conflict_never_resets(claimed):
    store, token, path = claimed
    write_frames(path, [{'type': 'turn.started'}])
    assert replay(store, token, path).cursor_sequence == 0
    digest = hashlib.sha256(b'changed').hexdigest()
    with pytest.raises(EventConflict):
        store.stage_capture_span(token, source_sequence=0, frame_json='changed', frame_digest=digest)
    assert store.read_capture_cursor(token).cursor_sequence == 0


def test_rotation_or_truncation_is_explicit_failure(claimed):
    store, token, path = claimed
    write_frames(path, [{'type': 'turn.started'}, {'type': 'turn.completed'}])
    replay(store, token, path)
    path.write_bytes(path.read_bytes()[:10])
    with pytest.raises(SourceRecoveryError):
        replay(store, token, path)


def test_delete_revokes_source_and_staged_span(claimed):
    store, token, path = claimed
    write_frames(path, [{'type': 'turn.started'}])
    replay(store, token, path)
    state = store.lifecycle_state('p', 'c')
    store.set_lifecycle_deleted('p', 'c', True, expected_revision=state.revision, event_id='delete')
    with pytest.raises(Exception):
        replay(store, token, path)


def test_stale_owner_cannot_advance_bound_cursor(claimed):
    store, token, path = claimed
    write_frames(path, [{'type': 'turn.started'}])
    replay(store, token, path)
    state = store.lifecycle_state('p', 'c')
    _, new_owner = store.claim_owner('p', 'c', owner_id='new-owner',
                                     expected_revision=state.revision, event_id='takeover')
    with pytest.raises(Exception):
        store.commit_capture_span(token, source_sequence=1, frame_digest='x', events=[])
    with pytest.raises(Exception):
        store.read_capture_cursor(token)


def test_staged_span_cannot_commit_after_takeover(claimed):
    store, token, path = claimed
    write_frames(path, [{'type': 'turn.started'}])
    store.bind_capture_source(token, provider='codex', format_version='codex-exec-jsonl-0.151', source_id='fixture-source', incarnation='v1')
    raw = path.read_bytes().splitlines()[0].decode('utf-8')
    store.stage_capture_span(token, source_sequence=0, frame_json=raw)
    state = store.lifecycle_state('p', 'c')
    store.claim_owner('p', 'c', owner_id='takeover', expected_revision=state.revision, event_id='takeover')
    with pytest.raises(Exception):
        store.commit_capture_span(token, source_sequence=0,
                                  frame_digest=hashlib.sha256(raw.encode()).hexdigest(), events=[])


def test_duplicate_cursor_validates_normalized_batch_payload(claimed):
    store, token, path = claimed
    write_frames(path, [{'type': 'turn.started'}])
    replay(store, token, path)
    with pytest.raises(Exception):
        store.commit_capture_span(token, source_sequence=0,
            frame_digest=store.read_capture_cursor(token).cursor_digest,
            events=[('fixture-source/v1/0:0', 'provider_observation',
                     {'name': 'codex.turn.started', 'value': {'basis': 'changed'}})])


def test_partial_trailing_frame_is_not_consumed_or_committed(claimed):
    store, token, path = claimed
    path.write_bytes(b'{"type":"turn.started"}\n{"type":')
    with pytest.raises(SourceRecoveryError):
        replay(store, token, path)
    assert store.read_capture_cursor(token).cursor_sequence == 0
    path.write_bytes(b'{"type":"turn.started"}\n{"type":"turn.completed"}\n')
    assert replay(store, token, path).cursor_sequence == 1


def test_incognito_replay_bypasses_path_store_and_decoder_io():
    class NoIO:
        def __getattribute__(self, name):
            raise AssertionError('incognito performed I/O')
    assert replay_file_prefix(path=NoIO(), store=NoIO(), token=NoIO(), provider='x',
        format_version='x', source_id='x', incarnation='x', decoder_factory=NoIO,
        incognito=True) is None


def test_schema2_migration_uses_backup_and_keeps_backup_unchanged(tmp_path):
    store = ConversationStore(tmp_path / 'schema2.sqlite')
    engine = {'provider': 'codex'}
    store.create_lifecycle_conversation('p', 'c', engine=engine, event_id='create')
    with store._connection(write=True) as db:
        db.execute('DROP TABLE capture_spans')
        db.execute('DROP TABLE capture_sources')
        db.execute('DROP TABLE runtime_launch_facts')
        db.execute('PRAGMA user_version=2')
    backup = tmp_path / 'schema2-backup.sqlite'
    store.migrate_schema2(backup_path=backup)
    with __import__('sqlite3').connect(backup) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
    with __import__('sqlite3').connect(store.db_path) as db:
            assert db.execute('PRAGMA user_version').fetchone()[0] == 4


def test_schema2_legacy_guard_and_protocol_metadata_are_preserved(tmp_path):
    store = ConversationStore(tmp_path / 'legacy2.sqlite')
    store.create_lifecycle_conversation('p', 'c', engine={'provider': 'codex'}, event_id='create')
    state = store.lifecycle_state('p', 'c')
    store._lifecycle_apply('p', 'c', event_id='note', kind='provider_observation',
                           payload={'name': 'fixture.note', 'value': {'ok': True}},
                           reduce=lambda value: value)
    with sqlite3.connect(store.db_path) as db:
        db.execute('DROP TABLE capture_spans')
        db.execute('DROP TABLE capture_sources')
        db.execute('DROP TABLE runtime_launch_facts')
        db.execute('PRAGMA user_version=2')
    events = store.read_events('p', 'c')
    assert events[-1].protocol_version == 1
    assert events[-1].disposition == 'control'
    with pytest.raises(Exception, match='Legacy writes'):
        store.begin_attempt('p', 'c', user_message={'text': 'x'},
                            engine={'provider': 'codex'}, event_id='legacy-write')


def test_schema2_migration_rejects_unexpected_shape_without_backup_mutation(tmp_path):
    store = ConversationStore(tmp_path / 'bad2.sqlite')
    store.create_lifecycle_conversation('p', 'c', engine={'provider': 'codex'}, event_id='create')
    with sqlite3.connect(store.db_path) as db:
        db.execute('DROP TABLE capture_spans')
        db.execute('DROP TABLE capture_sources')
        db.execute('DROP TABLE runtime_launch_facts')
        db.execute('ALTER TABLE requests ADD COLUMN unexpected TEXT')
        db.execute('PRAGMA user_version=2')
    before = store.db_path.read_bytes()
    backup = tmp_path / 'bad2-backup.sqlite'
    with pytest.raises(Exception, match='Unexpected schema 2 column shape'):
        store.migrate_schema2(backup_path=backup)
    assert store.db_path.read_bytes() == before
    assert not backup.exists()


def test_direct_duplicate_commit_is_idempotent_and_conflicts_are_event_conflicts(claimed):
    store, token, path = claimed
    write_frames(path, [{'type': 'turn.started'}])
    cursor = store.bind_capture_source(token, provider='codex', format_version='codex-exec-jsonl-0.151', source_id='s', incarnation='i')
    raw = path.read_bytes().splitlines()[0].decode()
    digest = hashlib.sha256(raw.encode()).hexdigest()
    store.stage_capture_span(token, source_sequence=0, frame_json=raw)
    events = [('s/i/0:0', 'provider_observation', {'name': 'codex.turn.started', 'value': {'basis': 'source_frame_boundary'}})]
    assert store.commit_capture_span(token, source_sequence=0, frame_digest=digest, events=events).cursor_sequence == 0
    assert store.commit_capture_span(token, source_sequence=0, frame_digest=digest, events=events).cursor_sequence == 0
    with pytest.raises(EventConflict):
        store.commit_capture_span(token, source_sequence=0, frame_digest=digest,
            events=[('s/i/0:0', 'provider_observation', {'name': 'codex.turn.started', 'value': {'basis': 'changed'}})])
    with pytest.raises(EventConflict):
        store.commit_capture_span(token, source_sequence=0, frame_digest=digest, events=[])


def test_replay_can_seal_eof_without_claiming_coverage(claimed):
    store, token, path = claimed
    write_frames(path, [{'type': 'turn.started'}])
    cursor = replay(store, token, path)
    sealed = store.seal_capture_source(token, eof_sequence=cursor.cursor_sequence + 1,
        exit_status=0, event_id='eof')
    assert sealed.sealed is True
    assert sealed.eof_sequence == 1
    assert store.lifecycle_state('p', 'c').coverage_complete is False


def test_sealed_source_rejects_new_stage_commit_and_appended_replay(claimed):
    store, token, path = claimed
    write_frames(path, [{'type': 'turn.started'}])
    replay(store, token, path)
    raw = '{"type":"turn.completed"}'
    digest = hashlib.sha256(raw.encode()).hexdigest()
    store.stage_capture_span(token, source_sequence=1, frame_json=raw)
    store.seal_capture_source(token, eof_sequence=1, exit_status=0, event_id='eof')
    with pytest.raises(EventConflict, match='new source spans'):
        store.stage_capture_span(token, source_sequence=2, frame_json='{"type":"turn.completed"}')
    # The pre-staged sequence is still not allowed to advance after sealing.
    with pytest.raises(EventConflict, match='advancing commit'):
        store.commit_capture_span(token, source_sequence=1, frame_digest=digest, events=[])
    # An appended frame is observed but cannot be committed after EOF.
    path.write_bytes(path.read_bytes() + b'{"type":"turn.completed"}\n')
    with pytest.raises(EventConflict, match='sealed'):
        replay(store, token, path)


def test_sealed_source_allows_exact_prior_replay_but_rejects_conflicts(claimed):
    store, token, path = claimed
    write_frames(path, [{'type': 'turn.started'}])
    replay(store, token, path)
    sealed = store.seal_capture_source(token, eof_sequence=1, exit_status=0, event_id='eof')
    replayed = replay(store, token, path)
    assert replayed.cursor_sequence == sealed.cursor_sequence
    with pytest.raises(EventConflict, match='EOF event identity'):
        store.seal_capture_source(token, eof_sequence=1, exit_status=0, event_id='different-eof')
    with pytest.raises(EventConflict, match='Conflicting source EOF sequence'):
        store.seal_capture_source(token, eof_sequence=2, exit_status=0, event_id='eof')
