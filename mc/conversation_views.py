"""Provider-neutral views over the canonical conversation journal.

These projections never open vendor transcripts or mutate stored events. The
Scribe view is deliberately smaller than the history; it is NOT a backup or a
replacement for that history. Runtime wiring is a separate migration gate.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Any

from mc.conversation_contract import validate_content

if TYPE_CHECKING:
    from mc.conversation_store import ConversationEvent, ConversationStore


def history_events(
    store: ConversationStore,
    project_id: str,
    conversation_id: str,
    *,
    after: int = 0,
    batch_size: int = 100,
) -> Iterator[ConversationEvent]:
    """Read ordered complete events in pages, without a total-history cap.

    Each page rechecks deletion through the store; do not cache a deleted
    conversation's remaining pages. This is a live cursor, not a snapshot:
    callers needing an export snapshot must separately freeze its high-water
    mark. Empty pages end the current read, not the conversation's lifetime.
    """
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= 1000:
        raise ValueError('batch_size must be between 1 and 1000')
    cursor = after
    while True:
        page = store.read_events(
            project_id, conversation_id, after=cursor, limit=batch_size)
        if not page:
            return
        yield from page
        cursor = page[-1].sequence


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    left = limit // 2
    right = limit - left
    return f'{text[:left]}\n...[{len(text) - limit} chars elided]...\n{text[-right:]}'


def scribe_lines(
    events: Iterable[ConversationEvent], *, detail_limit: int = 2000,
) -> Iterator[str]:
    """Produce Scribe's existing text format from canonical content events.

    No provider branches, role inference from display strings, injected system
    prompt, or raw event fallback. Lifecycle/usage events stay in history but
    do not become conversational claims. Tool/attachment strings are untrusted
    data for the existing isolated summarizer, never instructions to execute.
    Messages are not shortened; the consumer owns its total context budget.
    """
    if isinstance(detail_limit, bool) or not isinstance(detail_limit, int) or detail_limit < 2:
        raise ValueError('detail_limit must be an integer >= 2')
    for event in events:
        payload = event.payload
        validate_content(event.kind, payload)
        if not isinstance(payload, dict):
            continue
        if event.kind == 'attempt_started':
            payload = payload.get('user_message')
            if payload is None:  # Retry references the accepted request.
                continue
            validate_content('user_message', payload)
        if event.kind in ('attempt_started', 'user_message', 'assistant_message'):
            role = 'ASSISTANT' if event.kind == 'assistant_message' else 'USER'
            text = payload.get('text')
            if isinstance(text, str) and text:
                yield f'{role}: {text}'
            # References, not base64 blobs or absolute local file contents.
            attachments = payload.get('attachments', [])
            if not isinstance(attachments, list):
                attachments = []
            for attachment in attachments:
                if isinstance(attachment, dict):
                    label = attachment.get('name') or attachment.get('id')
                    if label:
                        yield f'ATTACHMENT: {_shorten(_text(label), detail_limit)}'
        elif event.kind == 'tool_call':
            yield (f"ACTION {payload['name']} [call {payload['call_id']}]: "
                   f"{_shorten(_text(payload.get('input', {})), detail_limit)}")
        elif event.kind == 'tool_result':
            status = ' error' if payload.get('is_error') else ''
            yield (f"RESULT [call {payload['call_id']}{status}]: "
                   f"{_shorten(_text(payload['output']), detail_limit)}")
        elif event.kind == 'thinking':
            text = payload.get('text')
            if isinstance(text, str) and text:
                yield f'THINKING: {_shorten(text, detail_limit)}'
