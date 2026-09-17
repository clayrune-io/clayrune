"""Protocol-1 snapshot projection, never real CLI/model calls or publication."""
from copy import deepcopy

import pytest

from mc.conversation_projection import ProjectionError, project_conversation, scribe_input
from mc.conversation_store import ConversationEvent


def event(sequence, kind, payload, attempt='a', disposition='authoritative', version=1):
    return dict(sequence=sequence, protocol_version=version, attempt_id=attempt,
                kind=kind, payload=payload, disposition=disposition)


def message(text, completeness='final', message_id='m', block_id='b'):
    return dict(message_id=message_id, block_id=block_id, text=text, completeness=completeness)


def delta(index, text):
    return dict(message_id='m', block_id='b', delta_index=index, text=text)


def prefix():
    return [event(1, 'lifecycle.request_accepted', {
        'request_id': 'r', 'user_message': {'text': 'User original\n🧱'},
        'engine': {'model': 'requested'},
        'provenance': {'origin': 'unattended', 'memory_owner': 'agent-1', 'trigger': 'schedule'},
    }, attempt=''), event(2, 'lifecycle.attempt_claimed', {'request_id': 'r', 'attempt_id': 'a'})]


def test_partial_deltas_are_retained_without_inventing_role_or_final():
    result = project_conversation([event(1, 'message_delta', delta(0, 'Hello ')),
                                   event(2, 'message_delta', delta(1, 'world'))])
    block = result.blocks[0]
    assert block.text == 'Hello world'
    assert block.kind == 'message_unknown_role'
    assert block.completeness == 'partial'
    assert (block.attempt_id, block.message_id, block.block_id) == ('a', 'm', 'b')
    assert block.source_sequences == (1, 2)
    assert not result.complete
    assert result.issues[-1].code == 'unfinalized_message'
    assert len(result.evidence) == 2


def test_final_snapshot_consolidates_deltas_preserving_all_source_evidence():
    source = prefix() + [event(3, 'message_delta', delta(0, 'Hello ')),
                         event(4, 'message_delta', delta(1, 'world')),
                         event(5, 'assistant_message', message('Hello world!')),
                         event(6, 'lifecycle.attempt_transitioned',
                               {'attempt_id': 'a', 'status': 'completed', 'attempt_revision': 2})]
    before = deepcopy(source)
    result = project_conversation(source)
    assert result.complete
    block = next(b for b in result.blocks if b.kind == 'assistant_message')
    assert block.text == 'Hello world!' and block.completeness == 'final'
    assert block.source_sequences == (3, 4, 5)
    memory = scribe_input(result)
    assert memory.attempts[0].origin == 'unattended'
    assert memory.attempts[0].status == 'completed'
    assert memory.attempts[0].request_id == 'r'
    assert 'memory_owner' in memory.attempts[0].provenance_json
    source[4]['payload']['text'] = 'mutated after projection'
    assert result.evidence[4].payload['text'] == before[4]['payload']['text']
    decoded = result.evidence[0].payload
    decoded['user_message']['text'] = 'mutated decode'
    assert result.evidence[0].payload['user_message']['text'] == 'User original\n🧱'


@pytest.mark.parametrize('second', ['same', 'different'])
def test_repeated_delta_indices_raise_explicitly(second):
    with pytest.raises(ProjectionError, match='Repeated delta index'):
        project_conversation([event(1, 'message_delta', delta(0, 'same')),
                              event(2, 'message_delta', delta(0, second))])


def test_missing_delta_and_conflicting_final_are_incomplete_not_hidden():
    result = project_conversation([event(1, 'message_delta', delta(0, 'old ')),
                                   event(2, 'message_delta', delta(2, 'text')),
                                   event(3, 'assistant_message', message('different answer'))])
    assert {'delta_gap', 'final_delta_conflict'} <= {i.code for i in result.issues}
    assert result.blocks[0].text == 'different answer'
    assert result.evidence[0].payload['text'] == 'old '
    assert not scribe_input(result).complete


def test_repeated_finalization_raises_and_postfinal_delta_flags_incomplete():
    with pytest.raises(ProjectionError, match='Repeated message finalization'):
        project_conversation([event(1, 'assistant_message', message('x')),
                              event(2, 'assistant_message', message('x'))])
    result = project_conversation([event(1, 'assistant_message', message('x')),
                                   event(2, 'message_delta', delta(0, 'x'))])
    assert any(i.code == 'content_after_final' for i in result.issues)


def test_tool_ids_are_attempt_scoped_and_failed_partial_history_survives_retry():
    source = prefix() + [
        event(3, 'tool_call', {'call_id': '1', 'name': 'shell', 'input': {'command': 'first'}}),
        event(4, 'tool_result', {'call_id': '1', 'output': 'failed receipt', 'is_error': True}),
        event(5, 'assistant_message', message('Tentative proposal', 'partial')),
        event(6, 'lifecycle.attempt_transitioned', {'attempt_id': 'a', 'status': 'failed'}),
        event(7, 'lifecycle.attempt_claimed', {'request_id': 'r', 'attempt_id': 'b'}, attempt='b'),
        event(8, 'tool_call', {'call_id': '1', 'name': 'shell', 'input': {'command': 'retry'}}, attempt='b'),
        event(9, 'tool_result', {'call_id': '1', 'output': 'success receipt', 'is_error': False}, attempt='b'),
        event(10, 'assistant_message', message('Corrected answer'), attempt='b'),
        event(11, 'lifecycle.attempt_transitioned', {'attempt_id': 'b', 'status': 'completed'}, attempt='b'),
    ]
    result = project_conversation(source)
    results = [b for b in result.blocks if b.kind == 'tool_result']
    assert [(b.attempt_id, b.call_id) for b in results] == [('a', '1'), ('b', '1')]
    assert [(a.attempt_id, a.status) for a in result.attempts] == [('a', 'failed'), ('b', 'completed')]
    assert next(b for b in result.blocks if b.kind == 'assistant_message').completeness == 'partial'
    assert not result.complete


def test_late_lifecycle_does_not_replace_authoritative_outcome():
    result = project_conversation(prefix() + [
        event(3, 'lifecycle.attempt_transitioned', {'attempt_id': 'a', 'status': 'failed'}),
        event(4, 'tool_call', {'call_id': '1', 'name': 'shell', 'input': {}}, disposition='late'),
        event(5, 'tool_result', {'call_id': '1', 'output': 'late receipt', 'is_error': False}, disposition='late'),
        event(6, 'lifecycle.attempt_transitioned', {'attempt_id': 'a', 'status': 'completed'}, disposition='late'),
    ])
    assert result.attempts[0].status == 'failed'
    assert result.blocks[-2].disposition == 'late'
    assert any(i.code == 'late_lifecycle' for i in result.issues)


@pytest.mark.parametrize('kind,version', [('surprise', 1), ('assistant_message', 0),
                                        ('assistant_message', 2), ('lifecycle.surprise', 1)])
def test_unknown_protocol_or_kind_never_silently_disappears(kind, version):
    with pytest.raises(ProjectionError):
        project_conversation([event(1, kind, message('x'), version=version)])


def test_capture_and_sequence_gaps_remain_explicit():
    result = project_conversation([event(2, 'capture_gap', {'reason': 'unknown native event',
                                                         'source_reference': 'native:42'})])
    assert {i.code for i in result.issues} == {'capture_gap', 'sequence_gap'}
    assert result.blocks[0].kind == 'capture_gap'
    with pytest.raises(ProjectionError):
        project_conversation([event(1, 'assistant_message', message('x')),
                              event(1, 'assistant_message', message('y'))])


def test_provider_observation_stays_history_not_conversation_claim():
    result = project_conversation([event(1, 'provider_observation',
                                        {'name': 'observed_model', 'value': 'reported-model'})])
    assert len(result.evidence) == len(result.blocks) == 1
    assert scribe_input(result).blocks == ()
    assert result.attempts[0].origin == 'unknown'


def test_actual_store_event_metadata_supported_without_schema1_fallback():
    raw = ConversationEvent(1, 'event-1', 'a', 'assistant_message', '2026-09-17',
                            '{"message_id":"m","block_id":"b","text":"x","completeness":"final"}',
                            protocol_version=1, disposition='late')
    result = project_conversation([raw])
    assert result.evidence[0].timestamp == '2026-09-17'
    assert result.evidence[0].event_id == 'event-1'
    assert result.blocks[0].disposition == 'late'


def test_pending_calls_and_cross_attempt_orphan_results_are_incomplete():
    result = project_conversation([
        event(1, 'tool_call', {'call_id': '1', 'name': 'shell', 'input': {}}),
        event(2, 'tool_result', {'call_id': '1', 'output': '', 'is_error': True}, attempt='b')])
    assert {i.code for i in result.issues} == {'orphan_tool_result', 'tool_result_missing'}
