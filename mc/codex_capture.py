"""Native Codex exec JSONL -> immutable protocol-1 evidence (not a runner).

The two explicit format labels name repository fixture families, NOT CLI safety
certification or compatibility with every release: test_provider_runtimes.py's
0.133 content[] messages and 0.151 command_execution/agent_message records, plus
test_mode_a_tool_call_parity.py. Rollout/native transcript format is NOT accepted.

One decoder belongs to one source stream/attempt. source_reference identifies a
single original frame, sequence orders it. Identical replay returns no events;
conflicting identity reuse raises. Store durable replay keys as reference plus
event_index; do not deduplicate equal text or item snapshots. No raw INIT/config
or unknown fields are retained. Unknown/malformed content becomes capture_gap.

message_id is the native item ID. block_id is a canonical structural address
anchored to that ID, not a claim the provider emitted a block ID. Tool call IDs
are native block/call IDs (shell uses its native command item ID). No native
identity is synthesized for an item lacking one. Native turn IDs are not exposed
by the fixture turn-boundary events; observations retain their source boundary.

Partial text is a snapshot, NOT an append delta. Partial shell output is an
explicit provider_observation because protocol1 tool_result requires a known
is_error boolean; unknown terminal exit status likewise stays an observation
plus gap, never invented success. Usage is the native report, with event basis,
not an inferred/summed total. Exposed reasoning text only; no hidden reasoning.

No DB, runtime, process, network, prompts, configuration reads or UI dependencies.
The composition root must authorize capture and supply approved source frames;
this decoder does not implement retention, attachment storage or certification.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from mc.conversation_contract import PROTOCOL_VERSION, validate_protocol_event


SUPPORTED_FORMATS = frozenset({'codex-exec-jsonl-0.133', 'codex-exec-jsonl-0.151'})


class CaptureSourceConflict(ValueError):
    pass


class UnsupportedCaptureFormat(ValueError):
    pass


@dataclass(frozen=True)
class CaptureEvent:
    kind: str
    payload_json: str
    source_reference: str
    source_sequence: int
    event_index: int
    protocol_version: int = PROTOCOL_VERSION

    @property
    def payload(self) -> dict[str, Any]:
        """Fresh decoded copy: mutation cannot alter the immutable event."""
        return json.loads(self.payload_json)


def _id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError('non-finite JSON constant')


class CodexCapture:
    def __init__(self, *, format_version: str):
        if format_version not in SUPPORTED_FORMATS:
            raise UnsupportedCaptureFormat('unsupported Codex exec fixture format')
        self.format_version = format_version
        self._seen: dict[str, tuple[int, str]] = {}
        self._last_sequence: int | None = None
        self._terminal_seen = False
        self._open_items: set[str] = set()
        self._had_gap = False
        self._finished = False

    def feed(self, *, source_reference: str, sequence: int,
             frame_json: str) -> tuple[CaptureEvent, ...]:
        if not _id(source_reference) or type(sequence) is not int or sequence < 0:
            raise ValueError('source_reference and nonnegative integer sequence required')
        if not isinstance(frame_json, str):
            raise ValueError('frame_json must be the original JSONL frame text')
        digest = hashlib.sha256(('frame\0' + frame_json).encode('utf-8')).hexdigest()
        prior = self._seen.get(source_reference)
        if prior is not None:
            if prior != (sequence, digest):
                raise CaptureSourceConflict('source reference reused with different frame/sequence')
            return ()
        if self._finished:
            raise CaptureSourceConflict('new source frame after transport EOF')
        if self._last_sequence is not None and sequence <= self._last_sequence:
            raise CaptureSourceConflict('new source frame is out of sequence')
        raw_events: list[tuple[str, dict[str, Any]]] = []

        def emit(kind: str, payload: dict[str, Any]) -> None:
            validate_protocol_event(kind, payload)
            raw_events.append((kind, payload))

        def gap(reason: str) -> None:
            emit('capture_gap', {'reason': reason, 'source_reference': source_reference})

        expected = 0 if self._last_sequence is None else self._last_sequence + 1
        if sequence != expected:
            gap(f'source sequence gap: expected {expected}, received {sequence}')
        try:
            frame = json.loads(frame_json, object_pairs_hook=_strict_object,
                               parse_constant=_reject_constant)
            # parse_constant rejects NaN/Infinity literals, but NOT 1e999.
            # Check the whole tree before any capture/state mutation; this also
            # refuses trees too deep to serialize into protocol evidence.
            json.dumps(frame, allow_nan=False)
        except (ValueError, RecursionError):
            gap('malformed JSON frame; source bytes were not interpreted as assistant text')
        else:
            if not isinstance(frame, dict):
                gap('native frame must be a JSON object')
            else:
                self._decode(frame, emit, gap)
                etype = frame.get('type')
                if etype == 'turn.started':
                    self._terminal_seen = False
                elif etype in ('turn.completed', 'thread.completed', 'turn.failed'):
                    self._terminal_seen = any(kind == 'provider_observation' for kind, _ in raw_events)
                elif etype in ('item.started', 'item.updated', 'item.completed'):
                    self._terminal_seen = False
                    item = frame.get('item')
                    if isinstance(item, dict) and _id(item.get('id')):
                        if etype == 'item.completed' and not any(kind == 'capture_gap' for kind, _ in raw_events):
                            self._open_items.discard(item['id'])
                        else:
                            self._open_items.add(item['id'])
        events = tuple(CaptureEvent(kind, json.dumps(payload, ensure_ascii=False, allow_nan=False),
                                    source_reference, sequence, index)
                       for index, (kind, payload) in enumerate(raw_events))
        self._seen[source_reference] = (sequence, digest)
        self._last_sequence = sequence
        self._had_gap |= any(event.kind == 'capture_gap' for event in events)
        return events

    def finish(self, *, source_reference: str, sequence: int,
               exit_status: int | None) -> tuple[CaptureEvent, ...]:
        """Record transport EOF, not inferred native success/completeness.

        Exact replay is idempotent. New input after EOF requires a new decoder.
        None means exit status unavailable; no terminal tool result is invented.
        """
        if not _id(source_reference) or type(sequence) is not int or sequence < 0:
            raise ValueError('source_reference and nonnegative integer sequence required')
        if exit_status is not None and type(exit_status) is not int:
            raise ValueError('exit_status must be an integer or None')
        digest = hashlib.sha256(('eof\0' + json.dumps(exit_status)).encode('utf-8')).hexdigest()
        prior = self._seen.get(source_reference)
        if prior is not None:
            if prior != (sequence, digest):
                raise CaptureSourceConflict('source reference reused for conflicting EOF')
            return ()
        if self._finished or self._last_sequence is not None and sequence <= self._last_sequence:
            raise CaptureSourceConflict('new EOF after completion or out of sequence')
        rows: list[tuple[str, dict[str, Any]]] = [('provider_observation', {
            'name': 'transport_eof', 'value': {'exit_status': exit_status,
                'native_terminal_observed': self._terminal_seen,
                'incomplete_item_ids': sorted(self._open_items),
                'prior_capture_gap': self._had_gap}})]
        reasons = []
        expected = 0 if self._last_sequence is None else self._last_sequence + 1
        if sequence != expected:
            reasons.append(f'source sequence gap at EOF: expected {expected}, received {sequence}')
        if not self._terminal_seen:
            reasons.append('transport EOF without observed native terminal turn result')
        if self._open_items:
            reasons.append('transport EOF with open or incompletely captured native items')
        if self._had_gap:
            reasons.append('transport EOF cannot establish complete capture after earlier gaps')
        rows.extend(('capture_gap', {'reason': reason, 'source_reference': source_reference}) for reason in reasons)
        events = []
        for index, (kind, payload) in enumerate(rows):
            validate_protocol_event(kind, payload)
            events.append(CaptureEvent(kind, json.dumps(payload, ensure_ascii=False, allow_nan=False),
                                       source_reference, sequence, index))
        self._seen[source_reference] = (sequence, digest)
        self._last_sequence = sequence
        self._finished = True
        return tuple(events)

    def _decode(self, frame: dict[str, Any], emit: Any, gap: Any) -> None:
        etype = frame.get('type')

        def observe(name: str, value: Any) -> None:
            emit('provider_observation', {'name': name, 'value': value})

        if etype == 'thread.started':
            if not _id(frame.get('thread_id')):
                gap('thread.started missing native thread_id')
            else:
                observe('codex.thread.started', {'thread_id': frame['thread_id']})
            if set(frame) - {'type', 'thread_id'}:
                gap('thread.started has unsupported metadata; raw INIT/config not captured')
            return
        if etype == 'turn.started':
            observe('codex.turn.started', {'basis': 'source_frame_boundary'})
            if set(frame) - {'type'}:
                gap('turn.started contains unsupported fields')
            return
        if etype in ('turn.completed', 'thread.completed'):
            observe(f'codex.{etype}', {'basis': 'source_frame_boundary'})
            if 'usage' in frame:
                usage = frame['usage']
                allowed = {'input_tokens', 'output_tokens', 'cached_input_tokens'}
                if (not isinstance(usage, dict) or set(usage) - allowed
                        or any(type(v) is not int or v < 0 for v in usage.values())):
                    gap('unsupported usage report shape; no token totals inferred')
                else:
                    observe('codex.usage', {'basis': etype, 'aggregation': 'native_report_not_summed',
                                           'usage': usage})
            if set(frame) - {'type', 'usage'}:
                gap('completion contains unsupported fields')
            return
        if etype in ('error', 'turn.failed'):
            error = frame.get('error')
            message = frame.get('message') if etype == 'error' else (
                error.get('message') if isinstance(error, dict) else None)
            if not isinstance(message, str):
                gap('error event missing supported message shape')
            else:
                observe('codex.error', {'native_type': etype, 'message': message})
            if (set(frame) - {'type', 'error', 'message'}
                    or isinstance(error, dict) and set(error) - {'message'}):
                gap('error event contains unsupported fields')
            return
        if etype not in ('item.started', 'item.updated', 'item.completed'):
            gap('unknown native event type')
            return
        item = frame.get('item')
        if not isinstance(item, dict) or not _id(item.get('id')):
            gap('item missing native item ID or object shape')
            return
        if set(frame) - {'type', 'item'}:
            gap('item envelope contains unsupported metadata')
        item_id = item['id']
        item_type = item.get('type')
        if not _id(item_type):
            gap('item missing native type string')
            return
        final = etype == 'item.completed'
        observe('codex.item.state', {'item_id': item_id, 'native_type': item_type,
                                    'phase': etype})
        if item_type in ('agent_message', 'reasoning') and self.format_version.endswith('0.151'):
            if isinstance(item.get('text'), str):
                emit('thinking' if item_type == 'reasoning' else 'assistant_message', {
                    'message_id': item_id, 'block_id': f'{item_id}/text', 'text': item['text'],
                    'completeness': 'final' if final else 'partial'})
            elif etype != 'item.started':
                gap('message/reasoning item missing text snapshot')
            if set(item) - {'id', 'type', 'text'}:
                gap('message/reasoning item contains unsupported fields')
            return
        if item_type == 'message' and self.format_version.endswith('0.133'):
            if 'role' in item and item['role'] != 'assistant':
                gap('legacy message has unsupported role; not attributed to assistant')
                return
            content = item.get('content')
            if not isinstance(content, list):
                gap('legacy message requires content array')
                return
            for index, block in enumerate(content):
                if not isinstance(block, dict):
                    gap('legacy message block is not an object')
                    continue
                if block.get('type') in ('output_text', 'text') and isinstance(block.get('text'), str):
                    emit('assistant_message', {'message_id': item_id,
                        'block_id': f'{item_id}/content/{index}', 'text': block['text'],
                        'completeness': 'final' if final else 'partial'})
                    if set(block) - {'type', 'text'}:
                        gap('legacy text block contains unsupported fields')
                elif block.get('type') == 'tool_use':
                    if not _id(block.get('id')) or not _id(block.get('name')) or 'input' not in block:
                        gap('legacy tool block missing native call ID/name/input')
                    else:
                        emit('tool_call', {'call_id': block['id'], 'name': block['name'], 'input': block['input']})
                    if set(block) - {'id', 'type', 'name', 'input'}:
                        gap('legacy tool block contains unsupported fields')
                else:
                    gap('unsupported legacy content block')
            if set(item) - {'id', 'type', 'content', 'role'}:
                gap('legacy message contains unsupported fields')
            return
        if item_type == 'command_execution' and self.format_version.endswith('0.151'):
            if not isinstance(item.get('command'), str):
                gap('shell item missing command string')
            else:
                emit('tool_call', {'call_id': item_id, 'name': 'shell', 'input': {'command': item['command']}})
            if 'status' in item:
                if isinstance(item['status'], str):
                    observe('codex.shell.status', {'call_id': item_id, 'status': item['status']})
                else:
                    gap('shell status is not a string')
            output = item.get('aggregated_output')
            exit_code = item.get('exit_code')
            if 'exit_code' in item:
                if exit_code is None or type(exit_code) is int:
                    observe('codex.shell.exit', {'call_id': item_id, 'exit_code': exit_code,
                                               'terminal': final})
                else:
                    gap('shell exit status has unsupported shape')
            if isinstance(output, str):
                if final and type(exit_code) is int:
                    emit('tool_result', {'call_id': item_id, 'output': output, 'is_error': exit_code != 0})
                else:
                    observe('codex.shell.output_snapshot', {'call_id': item_id, 'output': output,
                        'completeness': 'final' if final else 'partial', 'exit_status_known': False})
                    if final:
                        gap('terminal shell result has no supported exit status; success not assumed')
            elif final or 'aggregated_output' in item:
                gap('shell output absent or unsupported; result not fabricated')
            if set(item) - {'id', 'type', 'command', 'aggregated_output', 'exit_code', 'status'}:
                gap('shell item contains unsupported fields')
            return
        gap('unsupported native item type for selected fixture format')
