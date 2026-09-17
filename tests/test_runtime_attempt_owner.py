"""Store-backed runtime ownership tests; callbacks are local fakes only."""
import pytest

from mc.conversation_store import ConversationStore, EventConflict, LaunchUncertain
from mc.execution_lifecycle import AttemptStatus, LifecycleConflict
from mc.runtime_attempt_owner import RuntimeAttemptOwner


ENGINE = {'provider': 'codex', 'model': '', 'effort': 'high',
          'settings': {'nested': {'keep': [1, 'two']}}}


def _owner(tmp_path, conversation='c', owner='o'):
    return RuntimeAttemptOwner(store=ConversationStore(tmp_path / 'state.sqlite'),
        project_id='p', conversation_id=conversation, owner_id=owner)


def _prepared(tmp_path, conversation='c', owner='o'):
    return _owner(tmp_path, conversation, owner).prepare(
        engine=ENGINE, user_message={'text': 'hello'}, request_id='request',
        provenance={'origin': 'interactive'}, event_prefix=f'{conversation}-{owner}',
        launch_facts={'provider': 'codex', 'project_path': str(tmp_path),
                      'mc_session_id': conversation, 'requested_engine_json':
                      '{"effort":"high","model":"","provider":"codex","settings":{"nested":{"keep":[1,"two"]}}}',
                      'incognito': False, 'source_id': f'{conversation}-source',
                      'source_incarnation': 'one', 'format_version': 'codex-rollout-jsonl-0.153'})


def test_prepare_uses_real_store_and_preserves_exact_engine(tmp_path):
    prepared = _prepared(tmp_path)
    assert prepared is not None
    assert prepared.state.requested_engine_key == '{"effort":"high","model":"","provider":"codex","settings":{"nested":{"keep":[1,"two"]}}}'
    state = prepared.state
    assert state.active_attempt == prepared.attempt.attempt_id


def test_two_conversations_are_isolated_and_projection_is_irrelevant(tmp_path):
    a = _prepared(tmp_path, 'a', 'owner-a')
    b = _prepared(tmp_path, 'b', 'owner-b')
    assert a is not None and b is not None
    assert a.attempt.attempt_id != b.attempt.attempt_id
    assert a.attempt.conversation_id != b.attempt.conversation_id
    projection = {'status': 'completed'}
    projection['status'] = 'failed'
    assert a.state.active_attempt == a.attempt.attempt_id


def test_launch_durably_marks_uncertain_on_spawn_failure_and_does_not_retry(tmp_path):
    prepared = _prepared(tmp_path)
    assert prepared is not None
    with pytest.raises(LaunchUncertain):
        _owner(tmp_path).launch(prepared, authorize=lambda: None,
                                spawn=lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    state = _owner(tmp_path).store.lifecycle_state('p', 'c')
    attempt = next(iter(state.attempts))
    assert attempt.status is AttemptStatus.UNCERTAIN
    with pytest.raises(EventConflict, match='already committed'):
        _owner(tmp_path).launch(prepared, authorize=lambda: None, spawn=lambda: 'never')


def test_native_bind_is_idempotent_and_conflicts_are_rejected(tmp_path):
    prepared = _prepared(tmp_path)
    assert prepared is not None
    owner = _owner(tmp_path)
    owner.launch(prepared, authorize=lambda: None, spawn=lambda: 'proc')
    bound, facts = owner.bind_native(prepared, 'native', expected_attempt_revision=2)
    assert bound.attempts[0].native_handle == 'native'
    owner.bind_native(prepared, 'native', expected_attempt_revision=3)
    with pytest.raises(EventConflict, match='cannot change'):
        owner.bind_native(prepared, 'other', expected_attempt_revision=3)


def test_finish_and_reopen_reconstruct_exact_durable_state(tmp_path):
    prepared = _prepared(tmp_path)
    assert prepared is not None
    owner = _owner(tmp_path)
    owner.launch(prepared, authorize=lambda: None, spawn=lambda: 'proc')
    owner.bind_native(prepared, 'native', expected_attempt_revision=2)
    owner.finish(prepared, AttemptStatus.COMPLETED, expected_attempt_revision=3)
    state = owner.store.lifecycle_state('p', 'c')
    assert state.attempts[0].native_handle == 'native'
    assert state.attempts[0].status is AttemptStatus.COMPLETED
    reconstructed = owner.reconstruct(attempt_id=prepared.attempt.attempt_id)
    assert reconstructed.token.attempt_id == prepared.attempt.attempt_id
    assert reconstructed.native_handle == 'native'
    assert reconstructed.engine['model'] == ''
    assert reconstructed.launch_facts['source_id'] == 'c-source'


def test_stale_owner_and_incognito_are_fenced(tmp_path):
    store = ConversationStore(tmp_path / 'state.sqlite')
    assert RuntimeAttemptOwner(store=store, project_id='p', conversation_id='c', owner_id='o').prepare(
        engine=ENGINE, user_message={'text': 'x'}, request_id='r', provenance={'origin': 'interactive'},
        incognito=True) is None
    assert not (tmp_path / 'state.sqlite').exists()
    prepared = _prepared(tmp_path)
    assert prepared is not None
    state = store.lifecycle_state('p', 'c')
    store.claim_owner('p', 'c', owner_id='new', expected_revision=state.revision,
                      event_id='takeover')
    with pytest.raises(LifecycleConflict):
        _owner(tmp_path, owner='new').launch(prepared, authorize=lambda: None, spawn=lambda: 'no')
