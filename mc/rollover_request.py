"""Identify the exact native request that produced a Claude context reading."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mc.core import _log

_TAIL_BYTES = 1024 * 1024
_EFFORTS = frozenset({'low', 'medium', 'high', 'xhigh', 'max'})


def effort_name(value: object) -> str | None:
    if isinstance(value, str) and value.strip().lower() in _EFFORTS:
        return value.strip().lower()
    return None


@dataclass
class Call:
    native: str
    message_id: str
    tokens: int
    model: str | None
    cwd: str | None
    effort: str | None = None
    checked: bool = False
    # The effort last read for an earlier call of this session: the fallback
    # while this call's own row is not on disk yet (2026-11-25 review, F9).
    last_effort: str | None = None


def note_call(
    session: dict[str, Any], event: dict[str, Any], cwd: str | None, tokens: int,
) -> None:
    native = event.get('session_id')
    if (not isinstance(native, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', native)
            or native != session.get('claude_session_id') or event.get('parent_tool_use_id')):
        return
    message = event['message']
    message_id = message.get('id')
    model = message.get('model')
    if isinstance(model, str) and model.startswith('<'):
        return
    previous = session.get('_rollover_call')
    if (isinstance(previous, Call) and previous.native == native
            and previous.message_id == message_id and previous.tokens == tokens):
        return
    session['_rollover_call'] = Call(
        native, message_id if isinstance(message_id, str) else '', tokens,
        model if isinstance(model, str) and model else None, cwd,
        last_effort=_known_effort(previous),
    )


def _known_effort(call: object) -> str | None:
    return (call.effort or call.last_effort) if isinstance(call, Call) else None


def _request_effort(path: Path, call: Call) -> tuple[str | None, bool]:
    """Join the assistant block to its own api-request in a bounded file tail."""
    try:
        with path.open('rb') as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - _TAIL_BYTES))
            if size > _TAIL_BYTES:
                stream.readline(_TAIL_BYTES)
            lines = stream.read(_TAIL_BYTES).splitlines()
    except FileNotFoundError:
        return None, False
    except OSError as error:
        _log(f'[rollover-request] could not read native transcript: {error}')
        return None, True
    request_ref = None
    assistant_effort = None
    matched = False
    for line in reversed(lines):
        # Skip the large tool results without decoding their content.
        if not matched and call.message_id.encode() not in line:
            continue
        if matched and request_ref and request_ref.encode() not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get('sessionId') != call.native:
            continue
        if matched:
            if row.get('type') == 'api-request' and row.get('id') == request_ref:
                params = row.get('params')
                output = params.get('output_config') if isinstance(params, dict) else None
                explicit = effort_name(output.get('effort')) if isinstance(output, dict) else None
                return explicit or assistant_effort, True
            continue
        message = row.get('message')
        if (row.get('type') == 'assistant' and not row.get('isSidechain')
                and not row.get('parentToolUseID') and isinstance(message, dict)
                and message.get('id') == call.message_id):
            assistant_effort = effort_name(row.get('perTurnEffort')) or effort_name(row.get('effort'))
            request_ref = row.get('requestRef')
            if not isinstance(request_ref, str) or not request_ref:
                return assistant_effort, True
            matched = True
    return assistant_effort, matched


def call_facts(source: dict[str, Any], context_tokens: object) -> tuple[str | None, str | None]:
    call = source.get('_rollover_call')
    effort = effort_name(source.get('effective_effort'))
    if (not isinstance(call, Call) or call.native != source.get('claude_session_id')
            or call.tokens != context_tokens):
        return None, effort
    if not call.checked and call.message_id and not source.get('incognito'):
        from mc.agent_runtime import _find_claude_transcript

        path = _find_claude_transcript(call.cwd, call.native)
        if path is not None:
            call.effort, call.checked = _request_effort(path, call)
    if call.checked:
        return call.model, call.effort or effort    # the row is on disk: it is the answer
    # stdout runs ahead of the transcript. Without the row the effort was
    # None and the limit moved a grid step (opus over an 89k floor: 220,000,
    # against 200,000 once the row said `high`).
    return call.model, call.effort or effort or call.last_effort


def main_context_tokens(source: Any, native: str, tokens: Any) -> Any:
    """Ignore a newer subagent reading while a main-conversation reading exists."""
    call = source.get('_rollover_call') if isinstance(source, dict) else None
    if tokens is not None and isinstance(call, Call) and call.native == native:
        return call.tokens
    return tokens
