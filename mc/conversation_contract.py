"""Content contract shared by journal writers and read projections.

Vendor adapters must convert their payloads into these shapes. Unknown event
kinds can be retained for forward-compatible lifecycle diagnostics, but a known
content kind must never quietly lose text because its shape is unsupported.
"""
from __future__ import annotations

from typing import Any


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
