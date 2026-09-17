"""Actual SQLite producer -> protocol1 projection -> structured Scribe input."""
from pathlib import Path

import pytest

from mc.conversation_projection import ProjectionError, project_conversation, scribe_input
from mc.conversation_store import ConversationStore
from mc.execution_lifecycle import AttemptStatus as S, EvidenceDisposition


ENGINE = {'provider': 'fake', 'model': 'requested', 'effort': 'high'}


def create(tmp_path: Path):
    store = ConversationStore(tmp_path / 'journal.sqlite3')
    state = store.create_lifecycle_conversation('project', 'chat', engine=ENGINE, event_id='created')
    assert state is not None
    state = store.accept_request('project', 'chat', request_id='request-a',
        user_message={'text': 'Original request', 'attachments': [{'id': 'asset-1', 'name': 'input.png'}]},
        engine=ENGINE, provenance={'origin': 'interactive', 'memory_owner': 'agent-a'},
        expected_revision=state.revision, event_id='accepted-a')
    state, owner = store.claim_owner('project', 'chat', owner_id='server-a',
                                     expected_revision=state.revision, event_id='owner-a')
    state, token = store.claim_attempt(owner, request_id='request-a', attempt_id='attempt-a',
                                       expected_revision=state.revision, event_id='claim-a')
    transition(store, token, S.SPAWNING, 'spawning-a')
    transition(store, token, S.RUNNING, 'running-a')
    return store, owner, token


def transition(store, token, outcome, event_id):
    state = store.lifecycle_state('project', 'chat')
    attempt = next(a for a in state.attempts if a.attempt_id == token.attempt_id)
    return store.transition_attempt(token, outcome, expected_attempt_revision=attempt.revision,
                                     event_id=event_id)


def test_actual_queued_input_outcomes_late_receipt_coverage_and_reopen(tmp_path):
    store, owner, old = create(tmp_path)
    store.append_evidence(old, event_id='call-a', kind='tool_call',
                          payload={'call_id': '1', 'name': 'shell', 'input': {'command': 'work'}})
    store.append_evidence(old, event_id='partial-a', kind='assistant_message',
                          payload={'message_id': 'm', 'block_id': 'b', 'text': 'Tentative answer',
                                   'completeness': 'partial'})
    state = store.lifecycle_state('project', 'chat')
    state = store.accept_request('project', 'chat', request_id='request-b',
        user_message={'text': 'Queued input must survive while A is running'}, engine=ENGINE,
        provenance={'origin': 'unattended', 'memory_owner': 'agent-b', 'trigger': 'schedule'},
        expected_revision=state.revision, event_id='accepted-b')
    assert state.active_attempt == old.attempt_id
    transition(store, old, S.FAILED, 'failed-a')
    state = store.lifecycle_state('project', 'chat')
    state, new = store.claim_attempt(owner, request_id='request-b', attempt_id='attempt-b',
                                     expected_revision=state.revision, event_id='claim-b')
    transition(store, new, S.SPAWNING, 'spawning-b')
    transition(store, new, S.RUNNING, 'running-b')
    store.append_evidence(new, event_id='call-b', kind='tool_call',
                          payload={'call_id': '1', 'name': 'shell', 'input': {}})
    store.append_evidence(new, event_id='result-b', kind='tool_result',
                          payload={'call_id': '1', 'output': 'B receipt', 'is_error': False})
    store.append_evidence(new, event_id='final-b', kind='assistant_message',
                          payload={'message_id': 'm', 'block_id': 'b', 'text': 'Final B answer',
                                   'completeness': 'final'})
    transition(store, new, S.COMPLETED, 'completed-b')
    late, disposition = store.append_evidence(old, event_id='late-result-a', kind='tool_result',
                          payload={'call_id': '1', 'output': 'A late receipt', 'is_error': True})
    assert disposition == EvidenceDisposition.LATE
    state = store.lifecycle_state('project', 'chat')
    store.record_coverage(owner, high_water=state.high_water, complete=True,
                          source_reference='fixture-complete-capture', expected_revision=state.revision,
                          event_id='coverage')
    # Reopen the actual database: no session-dict/raw parser shortcuts.
    reopened = ConversationStore(store.db_path)
    snap = reopened.snapshot('project', 'chat')
    events = []
    cursor = snap.after
    while True:
        page = reopened.read_snapshot(snap, after=cursor, limit=3)
        if not page:
            break
        events.extend(page)
        cursor = page[-1].sequence
    result = project_conversation(events, after_sequence=snap.after)
    memory = scribe_input(result)
    assert result.through_sequence == snap.high_water
    assert any(e.disposition == 'control' for e in result.evidence)
    assert next(e for e in result.evidence if e.event_id == 'late-result-a').sequence == late.sequence
    inputs = [b.text for b in memory.blocks if b.kind == 'user_message']
    assert inputs == ['Original request', 'Queued input must survive while A is running']
    assert [(a.attempt_id, a.request_id, a.status, a.origin) for a in memory.attempts] == [
        ('attempt-a', 'request-a', 'failed', 'interactive'),
        ('attempt-b', 'request-b', 'completed', 'unattended')]
    receipts = [b for b in memory.blocks if b.kind == 'tool_result']
    assert [(b.attempt_id, b.call_id, b.disposition) for b in receipts] == [
        ('attempt-b', '1', 'authoritative'), ('attempt-a', '1', 'late')]
    assert not memory.complete  # full capture does not finalize A's partial message
    assert {i.code for i in memory.issues} == {'unfinalized_message'}
    full = project_conversation(reopened.read_events('project', 'chat', limit=1000))
    assert full.blocks[-1].kind == 'lifecycle.coverage_recorded'


def test_store_takeover_projects_uncertain_not_stale_running_or_cancelled(tmp_path):
    store, _, token = create(tmp_path)
    state = store.lifecycle_state('project', 'chat')
    store.claim_owner('project', 'chat', owner_id='server-b',
                       expected_revision=state.revision, event_id='takeover')
    stored = store.lifecycle_state('project', 'chat')
    result = project_conversation(store.read_events('project', 'chat'))
    assert stored.attempts[0].status == S.UNCERTAIN
    assert result.attempts[0].status == 'uncertain'
    assert result.attempts[0].attempt_id == token.attempt_id


def test_control_cannot_be_used_for_provider_content():
    with pytest.raises(ProjectionError, match='reserved'):
        project_conversation([dict(protocol_version=1, sequence=1, attempt_id='a',
            disposition='control', kind='assistant_message', payload={
                'message_id': 'm', 'block_id': 'b', 'text': 'x', 'completeness': 'final'})])
