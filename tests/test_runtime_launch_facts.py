import pytest

from mc.conversation_store import ConversationStore, ConversationUnavailable, EventConflict
from mc.runtime_attempt_owner import RuntimeAttemptOwner
from tests.test_runtime_attempt_owner import ENGINE, _prepared, _owner
from mc.execution_lifecycle import AttemptStatus, LifecycleConflict


def test_facts_are_immutable_and_conflicts_rejected(tmp_path):
    prepared = _prepared(tmp_path)
    assert prepared is not None
    store = _owner(tmp_path).store
    facts = store.read_runtime_launch_facts('p', 'c', prepared.attempt.attempt_id)
    assert facts['requested_engine_json'] == prepared.state.requested_engine_key
    with pytest.raises(TypeError):
        facts['source_id'] = 'changed'
    with pytest.raises(EventConflict):
        store.save_runtime_launch_facts(prepared.attempt, {
            'provider': 'codex', 'project_path': str(tmp_path), 'mc_session_id': 'c',
            'requested_engine_json': prepared.state.requested_engine_key, 'incognito': False,
            'source_id': 'other', 'source_incarnation': 'one',
            'format_version': 'codex-rollout-jsonl-0.153'})


def test_facts_reject_unknown_native_and_provider_mismatch(tmp_path):
    prepared = _prepared(tmp_path)
    assert prepared is not None
    store = _owner(tmp_path).store
    base = dict(store.read_runtime_launch_facts('p', 'c', prepared.attempt.attempt_id))
    base.pop('native_session_id', None); base.pop('transcript_path', None)
    base['native_session_id'] = 'too-early'
    with pytest.raises(ValueError):
        store.save_runtime_launch_facts(prepared.attempt, base)


def test_claim_and_facts_rollback_together_on_insert_fault(tmp_path, monkeypatch):
    owner = _owner(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError('injected')
    monkeypatch.setattr(owner.store, '_validate_launch_facts', staticmethod(fail))
    with pytest.raises(RuntimeError):
        owner.prepare(engine=ENGINE, user_message={'text': 'x'}, request_id='r',
                      provenance={'origin': 'interactive'}, launch_facts={})
    monkeypatch.undo()
    state = owner.store.lifecycle_state('p', 'c')
    assert state.active_attempt is None and not state.attempts
    with pytest.raises(ConversationUnavailable):
        owner.store.read_runtime_launch_facts('p', 'c', 'missing')


def test_bind_native_and_source_are_both_visible_after_reopen(tmp_path):
    prepared = _prepared(tmp_path)
    owner = _owner(tmp_path)
    owner.launch(prepared, authorize=lambda: None, spawn=lambda: 'p')
    state, facts = owner.bind_native(prepared, 'native', transcript_path='rollout.jsonl', expected_attempt_revision=2)
    reopened = ConversationStore(owner.store.db_path)
    got = reopened.read_runtime_launch_facts('p', 'c', prepared.attempt.attempt_id)
    assert state.attempts[0].native_handle == got['native_session_id'] == 'native'
    assert got['transcript_path'] == 'rollout.jsonl'


def test_bind_native_rejects_missing_facts_and_wrong_status(tmp_path):
    prepared = _prepared(tmp_path)
    owner = _owner(tmp_path)
    with pytest.raises(LifecycleConflict):
        owner.bind_native(prepared, 'native', expected_attempt_revision=0)
    owner.launch(prepared, authorize=lambda: None, spawn=lambda: 'p')
    owner.bind_native(prepared, 'native', expected_attempt_revision=2)
    owner.finish(prepared, AttemptStatus.COMPLETED, expected_attempt_revision=3)
    with pytest.raises(LifecycleConflict):
        owner.bind_native(prepared, 'native', expected_attempt_revision=3)
