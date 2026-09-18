"""Bounded decoder for the repository's recorded Codex rollout envelope.

This is deliberately distinct from :mod:`mc.codex_capture`: that decoder
handles ``codex exec --json`` fixture frames, while this module handles the
documented ``session_meta``/``response_item`` rollout envelope.  It is an
offline fixture decoder, not a CLI-version or native coverage certification.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from mc.conversation_contract import validate_protocol_event


# One format per surveyed CLI minor. 0.154 was checked offline against 245
# real rollouts (2026-09-17): every record shape shared with 0.153 carries
# identical payload keys; 0.154 adds response_item `agent_message` and an
# `inter_agent_communication_metadata` record (sub-agent traffic), which this
# decoder reports as explicit capture gaps. A new minor is refused until it
# gets the same survey.
SUPPORTED_FORMATS = frozenset({'codex-rollout-jsonl-0.153', 'codex-rollout-jsonl-0.154'})


class RolloutSourceConflict(ValueError):
    pass


@dataclass(frozen=True)
class CaptureEvent:
    kind: str
    payload_json: str
    source_reference: str
    source_sequence: int
    event_index: int
    protocol_version: int = 1

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


class CodexRolloutCapture:
    """Decode only evidence-backed rollout record types.

    ``session_meta`` and ``event_msg`` are bookkeeping and do not become
    conversation events.  Repeated response envelopes with the same native
    item/call identity and identical content are ignored; changed content is
    a source conflict rather than a second assistant/tool event.
    """

    def __init__(self, *, format_version: str = 'codex-rollout-jsonl-0.153'):
        if format_version not in SUPPORTED_FORMATS:
            raise ValueError('unsupported Codex rollout format')
        self.format_version = format_version
        self._seen: dict[str, tuple[int, str]] = {}
        self._semantic_seen: dict[str, str] = {}
        self._last_sequence = -1
        self._terminal = False
        self._finished = False

    def feed(self, *, source_reference: str, sequence: int,
             frame_json: str) -> tuple[CaptureEvent, ...]:
        if not isinstance(source_reference, str) or not source_reference.strip() \
                or type(sequence) is not int or sequence < 0:
            raise ValueError('source_reference and nonnegative sequence required')
        if not isinstance(frame_json, str):
            raise ValueError('frame_json must be original rollout text')
        digest = hashlib.sha256(('frame\0' + frame_json).encode()).hexdigest()
        previous = self._seen.get(source_reference)
        if previous is not None:
            if previous == (sequence, digest):
                return ()
            raise RolloutSourceConflict('source reference reused with different frame')
        if self._finished or sequence <= self._last_sequence:
            raise RolloutSourceConflict('rollout source is finished or out of sequence')
        try:
            record = json.loads(frame_json)
            if not isinstance(record, dict):
                raise ValueError('rollout record must be an object')
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise RolloutSourceConflict('malformed rollout record') from exc
        events: list[tuple[str, dict[str, Any]]] = []

        def emit(kind: str, payload: dict[str, Any]) -> None:
            validate_protocol_event(kind, payload, version=1)
            events.append((kind, payload))

        record_type = record.get('type')
        if record_type == 'session_meta':
            pass
        elif record_type == 'event_msg':
            if isinstance(record.get('payload'), dict) and record['payload'].get('type') == 'task_complete':
                self._terminal = True
        elif record_type != 'response_item' or not isinstance(record.get('payload'), dict):
            emit('capture_gap', {'reason': 'unsupported_rollout_record',
                                 'source_reference': source_reference})
        else:
            semantic_before = dict(self._semantic_seen)
            try:
                self._decode_response(record['payload'], source_reference, emit)
            except BaseException:
                self._semantic_seen = semantic_before
                raise
        self._seen[source_reference] = (sequence, digest)
        self._last_sequence = sequence
        return tuple(CaptureEvent(kind, _json(payload), source_reference, sequence, index)
                     for index, (kind, payload) in enumerate(events))

    def _decode_response(self, payload: dict[str, Any], source_reference: str,
                         emit: Any) -> None:
        kind = payload.get('type')
        native_id = payload.get('id')
        semantic_key = None
        if kind in {'function_call', 'function_call_output',
                    'custom_tool_call', 'custom_tool_call_output'}:
            call_id = payload.get('call_id')
            if isinstance(call_id, str) and call_id.strip():
                category = 'output' if kind.endswith('_output') else 'call'
                semantic_key = f'tool-{category}:{call_id}'
        elif isinstance(native_id, str) and native_id.strip():
            semantic_key = f'{kind}:{native_id}'
        content_digest = hashlib.sha256(_json(payload).encode()).hexdigest()
        if semantic_key:
            old = self._semantic_seen.get(semantic_key)
            if old is not None:
                if old != content_digest:
                    raise RolloutSourceConflict('mirrored native item changed content')
                return
            self._semantic_seen[semantic_key] = content_digest

        if kind == 'message':
            role = payload.get('role')
            content = payload.get('content')
            if role not in {'assistant', 'user'} or not isinstance(content, list):
                emit('capture_gap', {'reason': 'unsupported_rollout_message',
                                     'source_reference': source_reference})
                return
            if not isinstance(native_id, str) or not native_id.strip():
                emit('capture_gap', {'reason': 'message_missing_native_id',
                                     'source_reference': source_reference})
                return
            for index, block in enumerate(content):
                if not isinstance(block, dict) or not isinstance(block.get('text'), str):
                    emit('capture_gap', {'reason': 'message_block_missing_text',
                                         'source_reference': source_reference})
                    continue
                if role == 'assistant' and block.get('type') in {'output_text', 'text'}:
                    emit('assistant_message', {'message_id': native_id,
                        'block_id': f'{native_id}/content/{index}', 'text': block['text'],
                        'completeness': 'final'})
                elif role == 'user' and block.get('type') in {'input_text', 'text'}:
                    emit('provider_observation', {'name': 'native_user_message',
                        'value': {'text': block['text'], 'native_message_id': native_id,
                                  'source_role': 'user'}})
                else:
                    emit('capture_gap', {'reason': 'unsupported_message_block',
                                         'source_reference': source_reference})
            if role == 'assistant':
                self._terminal = False
            return
        if kind == 'reasoning':
            if not isinstance(native_id, str) or not native_id.strip():
                emit('capture_gap', {'reason': 'reasoning_missing_native_id',
                                     'source_reference': source_reference})
                return
            summary = payload.get('summary')
            if not isinstance(summary, list):
                emit('capture_gap', {'reason': 'reasoning_summary_missing',
                                     'source_reference': source_reference})
                return
            for index, block in enumerate(summary):
                if isinstance(block, dict) and isinstance(block.get('text'), str):
                    emit('thinking', {'message_id': native_id,
                        'block_id': f'{native_id}/summary/{index}', 'text': block['text'],
                        'completeness': 'final'})
                else:
                    emit('capture_gap', {'reason': 'reasoning_block_missing_text',
                                         'source_reference': source_reference})
            return
        if kind in {'function_call', 'custom_tool_call'}:
            name = payload.get('name')
            raw_input = payload.get('arguments' if kind == 'function_call' else 'input')
            call_id = payload.get('call_id')
            if not all(isinstance(value, str) and value.strip() for value in (name, call_id)):
                emit('capture_gap', {'reason': 'tool_call_missing_native_identity',
                                     'source_reference': source_reference})
                return
            if kind == 'function_call':
                if isinstance(raw_input, str):
                    try:
                        tool_input = json.loads(raw_input)
                    except (TypeError, ValueError):
                        tool_input = None
                else:
                    tool_input = None
            else:
                # The repository evidence records custom-tool input as a raw
                # string (apply_patch's diff format). The protocol permits the
                # value without requiring a JSON object, so retain it exactly.
                tool_input = raw_input if isinstance(raw_input, str) else None
            if tool_input is None:
                emit('capture_gap', {'reason': 'tool_call_input_missing_or_unsupported',
                                     'source_reference': source_reference})
                return
            emit('tool_call', {'call_id': call_id, 'name': name, 'input': tool_input})
            return
        if kind in {'function_call_output', 'custom_tool_call_output'}:
            call_id = payload.get('call_id')
            raw_output = payload.get('output')
            if not isinstance(call_id, str) or not call_id.strip() or not isinstance(raw_output, str):
                emit('capture_gap', {'reason': 'tool_result_missing_native_identity',
                                     'source_reference': source_reference})
                return
            try:
                output: Any = json.loads(raw_output)
            except (TypeError, ValueError):
                output = None
            exit_code = None
            if isinstance(output, dict) and isinstance(output.get('metadata'), dict):
                candidate = output['metadata'].get('exit_code')
                if type(candidate) is int:
                    exit_code = candidate
            if exit_code is None:
                emit('provider_observation', {'name': 'codex.tool_output',
                    'value': {'call_id': call_id, 'raw_output': raw_output,
                              'is_error': 'unknown'}})
                emit('capture_gap', {'reason': 'tool_result_status_unknown',
                                     'source_reference': source_reference})
            else:
                emit('tool_result', {'call_id': call_id, 'output': output,
                                     'is_error': exit_code != 0})
            return
        emit('capture_gap', {'reason': 'unsupported_response_item',
                             'source_reference': source_reference})

    def finish(self, *, source_reference: str, sequence: int,
               exit_status: int | None) -> tuple[CaptureEvent, ...]:
        if not isinstance(source_reference, str) or not source_reference.strip() \
                or type(sequence) is not int or sequence < 0 \
                or (exit_status is not None and type(exit_status) is not int):
            raise ValueError('invalid rollout EOF')
        digest = hashlib.sha256(('eof\0' + json.dumps(exit_status)).encode()).hexdigest()
        previous = self._seen.get(source_reference)
        if previous is not None:
            if previous == (sequence, digest):
                return ()
            raise RolloutSourceConflict('EOF source identity conflict')
        if self._finished or sequence <= self._last_sequence:
            raise RolloutSourceConflict('rollout EOF is out of sequence')
        payload = {'name': 'transport_eof', 'value': {
            'exit_status': exit_status, 'native_terminal_observed': self._terminal,
            'coverage_claim': False}}
        self._finished = True
        self._last_sequence = sequence
        self._seen[source_reference] = (sequence, digest)
        events = [('provider_observation', payload)]
        if not self._terminal:
            events.append(('capture_gap', {'reason': 'missing_native_terminal_result',
                                           'source_reference': source_reference}))
        return tuple(CaptureEvent(kind, _json(value), source_reference, sequence, index)
                     for index, (kind, value) in enumerate(events))
