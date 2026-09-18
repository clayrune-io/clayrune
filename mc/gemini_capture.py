"""Offline Gemini stream-json capture BEFORE its lossy UI reader.

Evidence is repository fixture schema, not CLI-version or safety certification:
test_claude_runtime.py Gemini tool_id/tool_name/parameters/output/status fixtures
and test_provider_runtimes.py content/result/error fixtures. They do NOT provide
a complete native stream or message IDs/delta-final markers. Consequently exact
text is retained as an unclassified provider observation plus explicit gap,
never assigned invented native IDs/finality. User echoes are not original input.
Tool results require native tool_id, actual output and explicit known status.

CaptureEvent/feed/finish match the other offline capture decoders. One instance
owns one source stream/attempt; source_reference identifies a single frame.
Only exact source replay deduplicates. Conflicting sources are rejected, source
acknowledgment follows successful batch serialization, and state rolls back on
unexpected failure. No arbitrary INIT/config/unknown envelopes are persisted.
No runtime, DB, filesystem, subprocess, network or actual adapter activation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from mc.conversation_contract import validate_protocol_event


FORMAT_VERSION = 'gemini-stream-json-repository-fixtures-v1'


class CaptureSourceConflict(ValueError):
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


def _id(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, content in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = content
    return value


class GeminiCapture:
    def __init__(self, *, format_version: str):
        if format_version != FORMAT_VERSION:
            raise ValueError('unsupported Gemini fixture format; CLI versions are not certified')
        self._sources: dict[str, tuple[int, str]] = {}
        self._last_sequence = -1
        self._open_calls: set[str] = set()
        self._terminal = False
        self._had_gap = False
        self._finished = False

    def _source(self, reference: str, sequence: int, fingerprint: str) -> bool:
        if not _id(reference) or type(sequence) is not int or sequence < 0:
            raise ValueError('explicit source reference and nonnegative integer sequence required')
        prior = self._sources.get(reference)
        if prior is not None:
            if prior == (sequence, fingerprint):
                return False
            raise CaptureSourceConflict('source reference reused with different content or sequence')
        if self._finished or sequence <= self._last_sequence:
            raise CaptureSourceConflict('unseen source after EOF or out of sequence')
        return True

    def _events(self, reference: str, sequence: int,
                rows: list[tuple[str, dict[str, Any]]]) -> tuple[CaptureEvent, ...]:
        events = []
        for index, (kind, payload) in enumerate(rows):
            validate_protocol_event(kind, payload)
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            events.append(CaptureEvent(kind, encoded, reference, sequence, index))
        return tuple(events)

    def feed(self, *, source_reference: str, sequence: int,
             frame_json: str) -> tuple[CaptureEvent, ...]:
        if not isinstance(frame_json, str):
            raise ValueError('original JSONL text required')
        fingerprint = hashlib.sha256(('frame\0' + frame_json).encode('utf-8')).hexdigest()
        if not self._source(source_reference, sequence, fingerprint):
            return ()
        old = (set(self._open_calls), self._terminal, self._had_gap)
        rows: list[tuple[str, dict[str, Any]]] = []

        def gap(reason: str) -> None:
            rows.append(('capture_gap', {'reason': reason, 'source_reference': source_reference}))

        try:
            if sequence != self._last_sequence + 1:
                gap('source_sequence_gap')
            try:
                frame = json.loads(frame_json, object_pairs_hook=_unique)
                # Reject NaN/Infinity, exponent overflow and excessive depth
                # throughout the tree BEFORE any semantic state changes.
                json.dumps(frame, allow_nan=False)
            except (ValueError, RecursionError):
                gap('malformed_json_frame')
            else:
                if not isinstance(frame, dict):
                    gap('unsupported_nonobject_frame')
                else:
                    self._decode(frame, rows, gap)
            events = self._events(source_reference, sequence, rows)
            self._had_gap |= any(e.kind == 'capture_gap' for e in events)
        except BaseException:
            self._open_calls, self._terminal, self._had_gap = old
            raise
        self._sources[source_reference] = (sequence, fingerprint)
        self._last_sequence = sequence
        return events

    def _decode(self, frame: dict[str, Any], rows: list[tuple[str, dict[str, Any]]], gap: Any) -> None:
        def observe(name: str, value: Any) -> None:
            rows.append(('provider_observation', {'name': name, 'value': value}))

        def extras(allowed: set[str]) -> None:
            if set(frame) - allowed:
                gap('unsupported_native_fields')

        kind = frame.get('type')
        if kind != 'init' and 'session_id' in frame:
            if _id(frame['session_id']):
                observe('gemini.session', {'session_id': frame['session_id']})
            else:
                gap('invalid_native_session_id')
        if kind == 'init':
            if _id(frame.get('session_id')):
                observe('gemini.session', {'session_id': frame['session_id']})
            else:
                gap('missing_native_session_id')
            if 'model' in frame:
                if isinstance(frame['model'], str):
                    observe('gemini.observed_model', frame['model'])
                else:
                    gap('unsupported_observed_model')
            extras({'type', 'session_id', 'model'})
            return
        if kind in ('message', 'content', 'assistant', 'text'):
            self._terminal = False
            text = frame.get('content') if 'content' in frame else frame.get('text')
            role = frame.get('role')
            if kind == 'message' and role not in ('user', 'assistant'):
                gap('unsupported_message_role')
            elif isinstance(text, str):
                # No fixture demonstrates native message/block IDs or whether
                # these chunks append/replace/finalize. Preserve evidence only.
                observe('gemini.unclassified_text', {'text': text,
                    'role': role if kind == 'message' else 'assistant',
                    'provenance': 'provider_echo' if role == 'user' else 'provider_output',
                    'completeness': 'unverified'})
                gap('message_identity_and_delta_final_semantics_unverified')
            else:
                gap('unsupported_message_text_shape')
            if 'content' in frame and 'text' in frame:
                gap('ambiguous_duplicate_text_fields')
            extras({'type', 'session_id', 'role', 'content', 'text'})
            return
        if kind == 'tool_use':
            self._terminal = False
            call_id = frame.get('tool_id')
            name = frame.get('tool_name') if 'tool_name' in frame else frame.get('name')
            input_key = 'parameters' if 'parameters' in frame else 'input'
            if not _id(call_id) or not _id(name) or input_key not in frame:
                gap('missing_native_tool_id_name_or_arguments')
            else:
                assert isinstance(call_id, str) and isinstance(name, str)
                rows.append(('tool_call', {'call_id': call_id, 'name': name, 'input': frame[input_key]}))
                self._open_calls.add(call_id)
            if ('parameters' in frame and 'input' in frame) or ('tool_name' in frame and 'name' in frame):
                gap('ambiguous_tool_alias_fields')
            extras({'type', 'session_id', 'tool_id', 'tool_name', 'name', 'parameters', 'input'})
            return
        if kind == 'tool_result':
            self._terminal = False
            call_id = frame.get('tool_id')
            status = frame.get('status')
            output = frame.get('output')
            if not _id(call_id):
                gap('missing_native_tool_result_id')
            else:
                assert isinstance(call_id, str)
                if isinstance(status, str):
                    observe('gemini.tool_status', {'call_id': call_id, 'status': status})
                if not isinstance(output, str):
                    gap('missing_or_unsupported_tool_output')
                elif status not in ('success', 'error'):
                    observe('gemini.unclassified_tool_output', {'call_id': call_id, 'output': output})
                    gap('tool_result_status_unknown_no_success_inferred')
                else:
                    rows.append(('tool_result', {'call_id': call_id, 'output': output, 'is_error': status == 'error'}))
                    if call_id not in self._open_calls:
                        gap('tool_result_without_observed_call')
                    self._open_calls.discard(call_id)
            extras({'type', 'session_id', 'tool_id', 'status', 'output'})
            return
        if kind in ('result', 'turn_end', 'done'):
            self._terminal = True  # observed boundary, never inferred success
            observe('gemini.native_terminal', {'native_type': kind, 'outcome': 'unverified'})
            if 'status' in frame:
                if isinstance(frame['status'], str):
                    observe('gemini.result_status', frame['status'])
                else:
                    gap('unsupported_result_status')
            else:
                gap('native_terminal_outcome_missing')
            if frame.get('status') == 'error':
                error = frame.get('error')
                if isinstance(error, dict) and isinstance(error.get('message'), str):
                    observe('gemini.error', {'message': error['message']})
                    if set(error) - {'type', 'message'}:
                        gap('unsupported_error_fields')
                else:
                    gap('missing_structured_error_message')
            elif 'error' in frame:
                gap('unclassified_error_without_error_status')
            for field in ('usage', 'stats'):
                if field not in frame:
                    continue
                report = frame[field]
                if (isinstance(report, dict) and not set(report) - {'tokens', 'total_tokens', 'input_tokens', 'output_tokens'}
                        and all(type(n) is int and n >= 0 for n in report.values())):
                    observe('gemini.usage', {'basis': f'{kind}.{field}', 'aggregation': 'native_report_not_summed',
                                             'report': report})
                else:
                    gap('unsupported_usage_report')
            extras({'type', 'session_id', 'status', 'error', 'usage', 'stats'})
            return
        gap('unknown_native_event_type')

    def finish(self, *, source_reference: str, sequence: int,
               exit_status: int | None) -> tuple[CaptureEvent, ...]:
        if exit_status is not None and type(exit_status) is not int:
            raise ValueError('exit_status must be integer or None')
        fingerprint = hashlib.sha256(('finish\0' + json.dumps(exit_status)).encode('utf-8')).hexdigest()
        if not self._source(source_reference, sequence, fingerprint):
            return ()
        rows: list[tuple[str, dict[str, Any]]] = [('provider_observation', {
            'name': 'transport_eof', 'value': {'exit_status': exit_status}})]
        reasons = []
        if sequence != self._last_sequence + 1:
            reasons.append('source_sequence_gap')
        if not self._terminal:
            reasons.append('missing_native_terminal_result')
        if self._open_calls:
            reasons.append('unfinished_native_tools')
        if self._had_gap:
            reasons.append('earlier_capture_gaps')
        rows.extend(('capture_gap', {'reason': reason, 'source_reference': source_reference}) for reason in reasons)
        events = self._events(source_reference, sequence, rows)
        self._sources[source_reference] = (sequence, fingerprint)
        self._last_sequence = sequence
        self._finished = True
        return events
