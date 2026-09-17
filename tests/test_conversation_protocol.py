"""Offline protocol boundaries: never silently accept unmapped provider content."""
import pytest

from mc.conversation_contract import validate_content, validate_protocol_event


@pytest.mark.parametrize('kind,payload', [
    ('assistant_message', dict(message_id='m', block_id='b', text='', completeness='final')),
    ('user_message', dict(message_id='m', block_id='b', text='hi', completeness='final', attachments=[{'id': 'a'}])),
    ('thinking', dict(message_id='m', block_id='b', text='partial', completeness='partial')),
    ('message_delta', dict(message_id='m', block_id='b', text='x', delta_index=0)),
    ('tool_call', dict(call_id='c', name='read', input={'path': 'x'})),
    ('tool_result', dict(call_id='c', output=None, is_error=True)),
    ('capture_gap', dict(reason='unknown native event', source_reference='offset:4')),
    ('provider_observation', dict(name='model', value='observed-alias')),
])
def test_supported_evidence(kind, payload):
    validate_protocol_event(kind, payload)


@pytest.mark.parametrize('kind,payload', [
    ('assistant_message', {'text': 'legacy unbound text'}),
    ('message_delta', dict(message_id='m', block_id='b', text='x', delta_index=True)),
    ('message_delta', dict(message_id='m', block_id='b', text='x', delta_index=-1)),
    ('assistant_message', dict(message_id='m', block_id='b', text='x', completeness='unknown')),
    ('assistant_message', dict(message_id='m', block_id=' ', text='x', completeness='final')),
    ('tool_result', dict(call_id='c', output='x')),
    ('tool_result', dict(call_id='c', output='x', is_error=False, ignored_text='lost')),
    ('capture_gap', dict(reason='unknown')),
    ('provider_observation', dict(name='model')),
    ('native.unmapped', {'text': 'would disappear'}),
    ('lifecycle.attempt_transitioned', {'status': 'completed'}),
])
def test_invalid_evidence_rejected(kind, payload):
    with pytest.raises(ValueError):
        validate_protocol_event(kind, payload)


@pytest.mark.parametrize('version', [True, 0, 2, '1', None])
def test_unknown_versions_rejected(version):
    with pytest.raises(ValueError):
        validate_protocol_event('capture_gap', {'reason': 'x', 'source_reference': 'y'}, version)


def test_legacy_reader_contract_unchanged():
    validate_content('assistant_message', {'text': 'schema one'})
    validate_content('historical_native_kind', {'anything': True})
