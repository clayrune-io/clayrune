"""Offline authorization and replay tests for the explicit Codex adapter."""
import json
import io
import sqlite3

import pytest

from mc.capture_ingress import CaptureProvenance
from mc.capture_replay import SourceRecoveryError, replay_file_prefix
from mc.codex_rollout_capture import CodexRolloutCapture, SUPPORTED_FORMATS
from mc.codex_rollout_adapter import CodexRolloutAdapterError, replay_authorized_codex_rollout
from mc.conversation_store import ConversationStore, EventConflict, StaleAttempt
from mc import execution_lifecycle as lifecycle


FORMAT = next(iter(SUPPORTED_FORMATS))


def _setup(tmp_path):
    project_path = tmp_path / 'project'
    project_path.mkdir(parents=True)
    store = ConversationStore(tmp_path / 'delivery.sqlite')
    engine = {'provider': 'codex', 'model': 'rollout-model', 'effort': 'high',
              'settings': {'nested': {'flag': True}}}
    store.create_lifecycle_conversation('project', 'conversation', engine=engine, event_id='create')
    state = store.accept_request('project', 'conversation', request_id='request',
        user_message={'text': 'input'}, engine=engine,
        provenance={'origin': 'interactive'}, expected_revision=0, event_id='accept')
    state, owner = store.claim_owner('project', 'conversation', owner_id='owner',
        expected_revision=state.revision, event_id='owner')
    _, token = store.claim_attempt(owner, request_id='request', attempt_id='attempt',
        expected_revision=state.revision, event_id='attempt')
    native_id = 'native-rollout-session'
    path = tmp_path / 'rollout.jsonl'
    frames = [
        {'type': 'session_meta', 'payload': {'id': native_id, 'session_id': native_id,
                                             'cwd': str(project_path), 'cli_version': '0.153.4'}},
        {'type': 'response_item', 'payload': {'type': 'message', 'id': 'message-1',
            'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'done'}]}},
        {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'turn-1'}},
    ]
    path.write_text(''.join(json.dumps(frame) + '\n' for frame in frames), encoding='utf-8')
    provenance = CaptureProvenance(provider='codex', native_session_id=native_id,
        mc_session_id='mc-session', requested_engine=engine, privacy_generation=token.privacy_generation)
    return store, token, provenance, project_path, native_id, path, engine


def _replay(store, token, provenance, project_path, native_id, path, *, source_id='rollout', incarnation='one'):
    return replay_authorized_codex_rollout(path=path, store=store, token=token,
        provenance=provenance, project_path=str(project_path), native_session_id=native_id,
        source_id=source_id, incarnation=incarnation, format_version=FORMAT)


def test_authorized_replay_validates_meta_and_reopens_idempotently(tmp_path):
    store, token, provenance, project_path, native_id, path, _ = _setup(tmp_path)
    first = _replay(store, token, provenance, project_path, native_id, path)
    reopened = ConversationStore(store.db_path)
    second = _replay(reopened, token, provenance, project_path, native_id, path)
    assert first.cursor_sequence == second.cursor_sequence == 2
    events = reopened.read_events('project', 'conversation')
    assert sum(event.kind == 'assistant_message' for event in events) == 1
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('SELECT native_handle FROM lifecycle_attempts').fetchone()[0] is None


@pytest.mark.parametrize('mutator, message', [
    (lambda path, project: path.write_text(path.read_text().replace('native-rollout-session', 'other'), encoding='utf-8'), 'native session identity'),
    (lambda path, project: path.write_text(path.read_text().replace('"cwd": "', '"cwd": "C:/wrong-project/'), encoding='utf-8'), 'project cwd'),
])
def test_metadata_mismatch_fails_before_source_binding(tmp_path, mutator, message):
    store, token, provenance, project_path, native_id, path, _ = _setup(tmp_path)
    mutator(path, project_path)
    with pytest.raises(CodexRolloutAdapterError, match=message):
        _replay(store, token, provenance, project_path, native_id, path)
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM capture_sources').fetchone()[0] == 0


def test_conflicting_meta_ids_and_cli_version_fail_without_delivery_state(tmp_path):
    store, token, provenance, project_path, native_id, path, _ = _setup(tmp_path)
    path.write_text(path.read_text(encoding='utf-8').replace(
        '"session_id": "native-rollout-session"', '"session_id": "different-session"'), encoding='utf-8')
    with pytest.raises(CodexRolloutAdapterError, match='identities conflict'):
        _replay(store, token, provenance, project_path, native_id, path)
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM capture_sources').fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM events WHERE kind NOT LIKE 'lifecycle.%'").fetchone()[0] == 0

    store, token, provenance, project_path, native_id, path, _ = _setup(tmp_path / 'version')
    path.write_text(path.read_text(encoding='utf-8').replace('0.153.4', '0.154.0'), encoding='utf-8')
    with pytest.raises(CodexRolloutAdapterError, match='CLI version'):
        _replay(store, token, provenance, project_path, native_id, path)
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM capture_sources').fetchone()[0] == 0


def test_missing_partial_rotation_and_unsupported_profile_fail_closed(tmp_path):
    store, token, provenance, project_path, native_id, path, _ = _setup(tmp_path)
    path.unlink()
    with pytest.raises(CodexRolloutAdapterError, match='missing'):
        _replay(store, token, provenance, project_path, native_id, path)
    store, token, provenance, project_path, native_id, path, _ = _setup(tmp_path / 'partial')
    path.write_bytes(path.read_bytes() + b'{"type":')
    with pytest.raises(SourceRecoveryError, match='incomplete'):
        _replay(store, token, provenance, project_path, native_id, path)
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('SELECT cursor_sequence FROM capture_sources').fetchone()[0] == 2
    with pytest.raises(CodexRolloutAdapterError, match='unsupported'):
        replay_authorized_codex_rollout(path=path, store=store, token=token,
            provenance=provenance, project_path=str(project_path), native_session_id=native_id,
            source_id='other', incarnation='one', format_version='unknown')


def test_stale_owner_privacy_and_incognito_are_fail_closed(tmp_path):
    store, token, provenance, project_path, native_id, path, _ = _setup(tmp_path)
    state = store.lifecycle_state('project', 'conversation')
    store.claim_owner('project', 'conversation', owner_id='new',
        expected_revision=state.revision, event_id='takeover')
    with pytest.raises(StaleAttempt):
        _replay(store, token, provenance, project_path, native_id, path)
    class NoIO:
        def __getattribute__(self, name):
            raise AssertionError('incognito touched dependency')
    assert replay_authorized_codex_rollout(path=NoIO(), store=NoIO(), token=NoIO(),
        provenance=NoIO(), project_path=NoIO(), native_session_id=NoIO(),
        source_id=NoIO(), incarnation=NoIO(), format_version=NoIO(), incognito=True) is None


def test_provider_engine_mismatch_and_real_privacy_generation_refusal(tmp_path):
    store, token, provenance, project_path, native_id, path, engine = _setup(tmp_path)
    wrong = CaptureProvenance(provider='codex', native_session_id=native_id,
        mc_session_id='mc-session', requested_engine={**engine, 'provider': 'claude'},
        privacy_generation=token.privacy_generation)
    with pytest.raises(CodexRolloutAdapterError, match='provenance identity'):
        _replay(store, token, wrong, project_path, native_id, path)
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('SELECT COUNT(*) FROM capture_sources').fetchone()[0] == 0
    _replay(store, token, provenance, project_path, native_id, path)
    state = store.lifecycle_state('project', 'conversation')
    store.set_lifecycle_deleted('project', 'conversation', True,
        expected_revision=state.revision, event_id='delete')
    with pytest.raises(lifecycle.LifecycleConflict, match='deleted'):
        _replay(store, token, provenance, project_path, native_id, path)


def test_rotation_changes_prefix_without_advancing_and_new_incarnation_conflicts(tmp_path):
    store, token, provenance, project_path, native_id, path, _ = _setup(tmp_path)
    _replay(store, token, provenance, project_path, native_id, path)
    with sqlite3.connect(store.db_path) as db:
        before = db.execute('SELECT cursor_sequence FROM capture_sources').fetchone()[0]
    path.write_text(path.read_text(encoding='utf-8').replace('done', 'changed'), encoding='utf-8')
    with pytest.raises(SourceRecoveryError, match='prefix changed'):
        _replay(store, token, provenance, project_path, native_id, path)
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('SELECT cursor_sequence FROM capture_sources').fetchone()[0] == before
    with pytest.raises(EventConflict, match='binding conflicts'):
        _replay(store, token, provenance, project_path, native_id, path, incarnation='replacement')


def test_invalid_incognito_value_does_not_bypass_authorization(tmp_path):
    store, token, provenance, project_path, native_id, path, _ = _setup(tmp_path)
    with pytest.raises(CodexRolloutAdapterError, match='incognito'):
        replay_authorized_codex_rollout(path=path, store=store, token=token,
            provenance=provenance, project_path=str(project_path), native_session_id=native_id,
            source_id='s', incarnation='i', format_version=FORMAT, incognito=1)


def test_cli_version_requires_numeric_patch(tmp_path):
    store, token, provenance, project_path, native_id, path, _ = _setup(tmp_path)
    path.write_text(path.read_text(encoding='utf-8').replace('0.153.4', '0.153.preview'), encoding='utf-8')
    with pytest.raises(CodexRolloutAdapterError, match='CLI version'):
        _replay(store, token, provenance, project_path, native_id, path)


def test_stream_replay_does_not_close_caller_and_matches_path(tmp_path):
    store_a, token_a, _, _, _, path_a, _ = _setup(tmp_path / 'path')
    replay_file_prefix(path=path_a, store=store_a, token=token_a, provider='codex',
        format_version=FORMAT, source_id='source', incarnation='one',
        decoder_factory=lambda: CodexRolloutCapture(format_version=FORMAT))
    store_b, token_b, _, _, _, path_b, _ = _setup(tmp_path / 'stream')
    payload = path_b.read_bytes()
    stream = io.BytesIO(payload)
    replay_file_prefix(source=stream, path=None, store=store_b, token=token_b,
        provider='codex', format_version=FORMAT, source_id='source', incarnation='one',
        decoder_factory=lambda: CodexRolloutCapture(format_version=FORMAT))
    assert stream.closed is False
    assert stream.tell() >= 0
    def evidence(store):
        return [(event.kind, event.payload_json) for event in store.read_events('project', 'conversation')
                if not event.kind.startswith('lifecycle.')]
    assert evidence(store_a) == evidence(store_b)
