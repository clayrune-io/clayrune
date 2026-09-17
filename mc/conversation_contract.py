"""Content contract shared by journal writers and read projections.

Vendor adapters must convert their payloads into these shapes. Unknown event
kinds can be retained for forward-compatible lifecycle diagnostics, but a known
content kind must never quietly lose text because its shape is unsupported.
"""
from __future__ import annotations

from typing import Any


PROTOCOL_VERSION = 1


def validate_protocol_event(kind: str, payload: Any, version: int = PROTOCOL_VERSION) -> None:
    """Validate incoming canonical evidence, not store-owned lifecycle events.

    Schema-1 readers retain ``validate_content`` compatibility. Protocol-1
    evidence is deliberately closed: adapters must explicitly represent gaps
    instead of passing unrecognized native payloads off as complete capture.
    Message identities are scoped by the enclosing attempt; deltas are not
    final messages and consumers must not silently discard them.
    """
    if type(version) is not int or version != PROTOCOL_VERSION:
        raise ValueError('unsupported conversation protocol version')
    if not isinstance(kind, str) or kind.startswith('lifecycle.'):
        raise ValueError('lifecycle events are reserved for transactional state operations')
    if not isinstance(payload, dict):
        raise ValueError('protocol event payload must be an object')

    fields = {
        'user_message': {'message_id', 'block_id', 'text', 'completeness', 'attachments'},
        'assistant_message': {'message_id', 'block_id', 'text', 'completeness', 'attachments'},
        'thinking': {'message_id', 'block_id', 'text', 'completeness'},
        'message_delta': {'message_id', 'block_id', 'delta_index', 'text'},
        'tool_call': {'call_id', 'name', 'input'},
        'tool_result': {'call_id', 'output', 'is_error'},
        'capture_gap': {'reason', 'source_reference'},
        'provider_observation': {'name', 'value'},
    }
    if kind not in fields:
        raise ValueError('unknown protocol event kind; record a capture_gap')
    if set(payload) - fields[kind]:
        raise ValueError(f'{kind} contains unsupported fields')

    def require_text(key: str, *, empty: bool = False) -> None:
        value = payload.get(key)
        if not isinstance(value, str) or (not empty and not value.strip()):
            raise ValueError(f'{kind} requires {key}')

    if kind in {'user_message', 'assistant_message', 'thinking', 'message_delta'}:
        require_text('message_id')
        require_text('block_id')
        require_text('text', empty=True)
        if kind == 'message_delta':
            index = payload.get('delta_index')
            if type(index) is not int or index < 0:
                raise ValueError('message_delta requires nonnegative integer delta_index')
        elif payload.get('completeness') not in ('final', 'partial'):
            raise ValueError('message requires final or partial completeness')
        else:
            validate_content(kind, payload)
    elif kind in {'tool_call', 'tool_result'}:
        validate_content(kind, payload)
        require_text('call_id')
        if kind == 'tool_call':
            require_text('name')
        elif type(payload.get('is_error')) is not bool:
            raise ValueError('tool_result requires explicit boolean is_error')
    elif kind == 'capture_gap':
        require_text('reason')
        require_text('source_reference')
    else:
        require_text('name')
        if 'value' not in payload:
            raise ValueError('provider_observation requires value')


def validate_content(kind: str, payload: Any) -> None:
    if kind not in {'user_message', 'assistant_message', 'tool_call', 'tool_result', 'thinking'}:
        return
    if not isinstance(payload, dict):
        raise ValueError(f'{kind} payload must be an object')
    if kind in {'user_message', 'assistant_message', 'thinking'}:
        if not isinstance(payload.get('text'), str):
            raise ValueError(f'{kind} requires text')
        attachments = payload.get('attachments', [])
        if not isinstance(attachments, list):
            raise ValueError('attachments must be a list of references')
        for attachment in attachments:
            if not isinstance(attachment, dict) or not isinstance(attachment.get('id'), str) or not attachment['id']:
                raise ValueError('attachment requires a nonempty id')
    else:
        if not isinstance(payload.get('call_id'), str) or not payload['call_id']:
            raise ValueError(f'{kind} requires a call_id')
        if kind == 'tool_call':
            if not isinstance(payload.get('name'), str) or not payload['name'] or 'input' not in payload:
                raise ValueError('tool_call requires name and input')
        elif 'output' not in payload:
            raise ValueError('tool_result requires output')
        if 'is_error' in payload and not isinstance(payload['is_error'], bool):
            raise ValueError('is_error must be boolean')
