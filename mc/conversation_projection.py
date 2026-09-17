"""Offline protocol-1 history and structured Scribe projection.

No database, vendor parser, model call, or publication occurs here. Full evidence
is retained independently from consolidated message blocks. An incomplete view
is explicitly labelled, not silently promoted to a complete memory source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Iterable, Mapping

from mc.conversation_contract import validate_content, validate_protocol_event
from mc.conversation_store import ConversationEvent


class ProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class Evidence:
    sequence: int
    attempt_id: str
    kind: str
    payload_json: str
    disposition: str
    event_id: str = ''
    protocol_version: int = 1
    timestamp: str = ''

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)


@dataclass(frozen=True)
class ProjectionIssue:
    code: str
    sequence: int
    detail: str


@dataclass(frozen=True)
class HistoryBlock:
    kind: str
    attempt_id: str
    text: str
    completeness: str
    disposition: str
    source_sequences: tuple[int, ...]
    message_id: str = ''
    block_id: str = ''
    call_id: str = ''
    payload_json: str = '{}'


@dataclass(frozen=True)
class AttemptContext:
    attempt_id: str
    request_id: str
    status: str
    origin: str
    provenance_json: str


@dataclass(frozen=True)
class ProjectedHistory:
    evidence: tuple[Evidence, ...]
    blocks: tuple[HistoryBlock, ...]
    attempts: tuple[AttemptContext, ...]
    issues: tuple[ProjectionIssue, ...]
    after_sequence: int
    through_sequence: int

    @property
    def complete(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class ScribeInput:
    """Structured content/provenance, not permission to publish a summary.

    Callers must still enforce source-coverage, privacy-generation, origin,
    token budgets, memory-owner and consumer checkpoint policies. Unknown origin
    is never relabelled interactive. All strings remain untrusted input.
    """
    blocks: tuple[HistoryBlock, ...]
    attempts: tuple[AttemptContext, ...]
    issues: tuple[ProjectionIssue, ...]
    after_sequence: int
    through_sequence: int
    complete: bool


def _json(value: Any) -> str:
    def validate(item: Any) -> None:
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                raise ProjectionError('Object keys must be strings; coercion would lose identity')
            for child in item.values():
                validate(child)
        elif isinstance(item, list):
            for child in item:
                validate(child)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ProjectionError('Payload contains non-JSON values')
    validate(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _evidence(value: Mapping[str, Any] | ConversationEvent) -> Evidence:
    if isinstance(value, ConversationEvent):
        raw = {'sequence': value.sequence, 'attempt_id': value.attempt_id,
               'kind': value.kind, 'payload': value.payload,
               'protocol_version': value.protocol_version,
               'disposition': value.disposition, 'event_id': value.event_id,
               'timestamp': value.timestamp}
    else:
        raw = dict(value)
    if type(raw.get('protocol_version')) is not int or raw['protocol_version'] != 1:
        raise ProjectionError('Unsupported protocol version; no projection produced')
    if type(raw.get('sequence')) is not int or raw['sequence'] < 1:
        raise ProjectionError('Event requires a positive integer sequence')
    if not isinstance(raw.get('attempt_id'), str):
        raise ProjectionError('Event requires attempt_id (empty only for conversation lifecycle)')
    if not isinstance(raw.get('disposition'), str) or raw['disposition'] not in {'authoritative', 'late', 'control'}:
        raise ProjectionError('Unknown evidence disposition')
    if not isinstance(raw.get('kind'), str) or not isinstance(raw.get('payload'), dict):
        raise ProjectionError('Invalid event kind/payload')
    if raw['disposition'] == 'control' and not raw['kind'].startswith('lifecycle.'):
        raise ProjectionError('Control disposition is reserved for store lifecycle events')
    if not isinstance(raw.get('event_id', ''), str) or not isinstance(raw.get('timestamp', ''), str):
        raise ProjectionError('Event identity/timestamp must be strings')
    return Evidence(raw['sequence'], raw['attempt_id'], raw['kind'],
                    _json(raw['payload']), raw['disposition'], raw.get('event_id', ''),
                    timestamp=raw.get('timestamp', ''))


_LIFECYCLE = {
    'lifecycle.created', 'lifecycle.request_accepted', 'lifecycle.owner_claimed',
    'lifecycle.attempt_claimed', 'lifecycle.attempt_transitioned',
    'lifecycle.native_bound', 'lifecycle.attempt_reconciled',
    'lifecycle.engine_changed', 'lifecycle.privacy_changed',
    'lifecycle.coverage_recorded',
}
_STATUSES = {'launch_intent', 'spawning', 'running', 'cancel_requested', 'uncertain',
             'completed', 'failed', 'failed_before_launch', 'blocked', 'cancelled'}


@dataclass
class _Message:
    attempt_id: str
    message_id: str
    block_id: str
    kind: str = 'message_unknown_role'
    deltas: dict[int, str] = field(default_factory=dict)
    samples: list[Evidence] = field(default_factory=list)
    final: Evidence | None = None
    partial: Evidence | None = None


def project_conversation(events: Iterable[Mapping[str, Any] | ConversationEvent], *,
                         after_sequence: int = 0) -> ProjectedHistory:
    """Project an ordered snapshot; retain raw evidence for every accepted event.

    Delta indices are zero-based and unique within (attempt,message,block).
    Final text must extend observed deltas/partial snapshots; conflicting forms
    remain in evidence and flag the result incomplete. No final is invented.
    """
    if type(after_sequence) is not int or after_sequence < 0:
        raise ValueError('Invalid starting sequence')
    evidence: list[Evidence] = []
    blocks: list[HistoryBlock] = []
    issues: list[ProjectionIssue] = []
    messages: dict[tuple[str, str, str], _Message] = {}
    requests: dict[str, dict[str, Any]] = {}
    attempt_requests: dict[str, str] = {}
    statuses: dict[str, str] = {}
    calls: dict[tuple[str, str], Evidence] = {}
    results: set[tuple[str, str]] = set()
    previous = after_sequence

    def issue(code: str, event: Evidence, detail: str) -> None:
        issues.append(ProjectionIssue(code, event.sequence, detail))

    for value in events:
        event = _evidence(value)
        if event.sequence <= previous:
            raise ProjectionError('Repeated or out-of-order source sequence')
        if event.sequence != previous + 1:
            issue('sequence_gap', event, f'Missing source sequences {previous + 1}..{event.sequence - 1}')
        previous = event.sequence
        evidence.append(event)
        payload = event.payload
        if event.kind.startswith('lifecycle.'):
            if event.kind not in _LIFECYCLE:
                raise ProjectionError(f'Unknown lifecycle kind: {event.kind}')
            if event.disposition == 'late':
                issue('late_lifecycle', event, 'Late evidence cannot establish lifecycle authority')
            elif event.kind == 'lifecycle.request_accepted':
                request_id = payload.get('request_id')
                user = payload.get('user_message')
                if not isinstance(request_id, str) or not request_id:
                    raise ProjectionError('Accepted request requires request_id')
                validate_content('user_message', user)
                assert isinstance(user, dict)  # validated above; narrows for typed projection
                if request_id in requests:
                    raise ProjectionError('Duplicate request acceptance')
                provenance = payload.get('provenance')
                if not isinstance(provenance, dict):
                    raise ProjectionError('Accepted request requires structured provenance')
                requests[request_id] = provenance
                blocks.append(HistoryBlock('user_message', '', user['text'], 'final',
                    event.disposition, (event.sequence,), message_id=request_id,
                    payload_json=_json({'request_id': request_id, 'user_message': user,
                                        'provenance': provenance})))
            elif event.kind == 'lifecycle.attempt_claimed':
                aid, rid = payload.get('attempt_id'), payload.get('request_id')
                if not isinstance(aid, str) or not aid or not isinstance(rid, str) or not rid:
                    raise ProjectionError('Attempt claim requires attempt_id/request_id')
                if event.attempt_id and event.attempt_id != aid:
                    raise ProjectionError('Attempt claim envelope/payload disagree')
                if aid in attempt_requests:
                    raise ProjectionError('Duplicate attempt claim')
                attempt_requests[aid] = rid
                statuses[aid] = 'launch_intent'
            elif event.kind in {'lifecycle.attempt_transitioned', 'lifecycle.attempt_reconciled'}:
                aid, status = payload.get('attempt_id'), payload.get('status')
                if (not isinstance(aid, str) or not aid or not isinstance(status, str)
                        or status not in _STATUSES):
                    raise ProjectionError('Lifecycle outcome lacks valid attempt/status')
                if event.attempt_id and event.attempt_id != aid:
                    raise ProjectionError('Lifecycle envelope/payload disagree')
                statuses[aid] = status
            elif event.kind in {'lifecycle.owner_claimed', 'lifecycle.privacy_changed'}:
                # The store's reducer marks unresolved execution uncertain on
                # takeover/privacy revocation without emitting a second transition
                # event. Reflect that documented control transition, never infer
                # process death or successful cancellation from it.
                for aid, status in tuple(statuses.items()):
                    if status in {'launch_intent', 'spawning', 'running', 'cancel_requested'}:
                        statuses[aid] = 'uncertain'
            blocks.append(HistoryBlock(event.kind, event.attempt_id, '', 'lifecycle',
                                       event.disposition, (event.sequence,), payload_json=event.payload_json))
            continue
        try:
            validate_protocol_event(event.kind, payload, event.protocol_version)
        except ValueError as exc:
            raise ProjectionError(str(exc)) from exc
        if not event.attempt_id:
            raise ProjectionError('Provider evidence requires attributable attempt_id')
        if event.kind in {'message_delta', 'user_message', 'assistant_message', 'thinking'}:
            key = (event.attempt_id, payload['message_id'], payload['block_id'])
            message = messages.setdefault(key, _Message(*key))
            message.samples.append(event)
            if message.final:
                issue('content_after_final', event, 'Additional evidence after message finalization')
            if event.kind == 'message_delta':
                index = payload['delta_index']
                if index in message.deltas:
                    raise ProjectionError(f'Repeated delta index {index} for {key}')
                if index != len(message.deltas):
                    issue('delta_gap', event, f'Expected delta index {len(message.deltas)}, got {index}')
                message.deltas[index] = payload['text']
            else:
                if message.kind != 'message_unknown_role' and message.kind != event.kind:
                    issue('message_role_conflict', event, 'Message changed role/kind')
                message.kind = event.kind
                if payload['completeness'] == 'final':
                    if message.final is not None:
                        raise ProjectionError('Repeated message finalization')
                    message.final = event
                else:
                    if message.partial and not payload['text'].startswith(message.partial.payload['text']):
                        issue('partial_conflict', event, 'Partial snapshots disagree')
                    message.partial = event
            continue
        if event.kind == 'capture_gap':
            issue('capture_gap', event, payload['reason'])
        if event.kind in {'tool_call', 'tool_result'}:
            key = (event.attempt_id, payload['call_id'])
            if event.kind == 'tool_call':
                if key in calls:
                    raise ProjectionError('Repeated tool call identity within attempt')
                calls[key] = event
            else:
                if key in results:
                    raise ProjectionError('Repeated tool result identity within attempt')
                if key not in calls:
                    issue('orphan_tool_result', event, 'No preceding call in this attempt/snapshot')
                results.add(key)
        blocks.append(HistoryBlock(event.kind, event.attempt_id, '', 'evidence',
                                   event.disposition, (event.sequence,),
                                   call_id=payload.get('call_id', ''), payload_json=event.payload_json))

    for message in messages.values():
        delta_text = ''.join(message.deltas[i] for i in sorted(message.deltas))
        chosen = message.final or message.partial
        text = chosen.payload['text'] if chosen else delta_text
        if chosen and message.deltas and not text.startswith(delta_text):
            issue('final_delta_conflict', chosen, 'Snapshot/final text conflicts with captured deltas')
        if message.final and message.partial and not text.startswith(message.partial.payload['text']):
            issue('final_partial_conflict', message.final, 'Final text conflicts with partial snapshot')
        if not message.final:
            issue('unfinalized_message', message.samples[-1], 'Partial content retained without claiming finality')
        dispositions = {sample.disposition for sample in message.samples}
        blocks.append(HistoryBlock(message.kind, message.attempt_id, text,
            'final' if message.final else 'partial',
            next(iter(dispositions)) if len(dispositions) == 1 else 'mixed',
            tuple(sample.sequence for sample in message.samples), message.message_id, message.block_id,
            payload_json=chosen.payload_json if chosen else '{}'))
    for key, call in calls.items():
        if key not in results:
            issue('tool_result_missing', call, 'Call has no result in selected snapshot')
    aids = sorted({e.attempt_id for e in evidence if e.attempt_id} | set(attempt_requests) | set(statuses))
    contexts: list[AttemptContext] = []
    for aid in aids:
        rid = attempt_requests.get(aid, '')
        provenance = requests.get(rid, {})
        origin = provenance.get('origin', 'unknown')
        if not isinstance(origin, str) or origin not in {'interactive', 'unattended', 'unknown'}:
            origin = 'unknown'
        contexts.append(AttemptContext(aid, rid, statuses.get(aid, 'unknown'), origin, _json(provenance)))
    return ProjectedHistory(tuple(evidence), tuple(sorted(blocks, key=lambda b: b.source_sequences[0])),
                            tuple(contexts), tuple(issues), after_sequence, previous)


def scribe_input(history: ProjectedHistory) -> ScribeInput:
    """Preserve content identities, late/partial status and attempt provenance.

    Explicitly classified provider observations are diagnostics, not assertions
    by the user/assistant. The full history still retains them without changes.
    """
    blocks = tuple(b for b in history.blocks if b.kind != 'provider_observation')
    return ScribeInput(blocks, history.attempts, history.issues, history.after_sequence,
                       history.through_sequence, history.complete)
