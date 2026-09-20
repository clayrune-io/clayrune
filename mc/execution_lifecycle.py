"""Pure lifecycle reducer for the dormant canonical execution boundary.

No persistence, process launch, authorization, or clock is provided here.
Schema-2 storage must load, validate, and save each reduction in ONE transaction;
applying two reductions to the same old value is not concurrency enforcement.
Tokens fence authority, not the possession of sensitive data or OS processes.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class LifecycleConflict(ValueError):
    """Stale authority/revision or an invalid lifecycle operation."""


class AttemptStatus(str, Enum):
    LAUNCH_INTENT = 'launch_intent'
    SPAWNING = 'spawning'
    RUNNING = 'running'
    CANCEL_REQUESTED = 'cancel_requested'
    UNCERTAIN = 'uncertain'
    COMPLETED = 'completed'
    FAILED = 'failed'
    FAILED_BEFORE_LAUNCH = 'failed_before_launch'
    BLOCKED = 'blocked'
    CANCELLED = 'cancelled'


TERMINAL = frozenset({AttemptStatus.COMPLETED, AttemptStatus.FAILED,
                      AttemptStatus.FAILED_BEFORE_LAUNCH, AttemptStatus.BLOCKED,
                      AttemptStatus.CANCELLED})
_TRANSITIONS = {
    AttemptStatus.LAUNCH_INTENT: frozenset({AttemptStatus.RUNNING,
        AttemptStatus.SPAWNING, AttemptStatus.FAILED_BEFORE_LAUNCH, AttemptStatus.UNCERTAIN}),
    AttemptStatus.SPAWNING: frozenset({AttemptStatus.RUNNING,
        AttemptStatus.FAILED_BEFORE_LAUNCH, AttemptStatus.UNCERTAIN}),
    AttemptStatus.RUNNING: frozenset({AttemptStatus.CANCEL_REQUESTED,
        AttemptStatus.COMPLETED, AttemptStatus.FAILED, AttemptStatus.BLOCKED,
        AttemptStatus.UNCERTAIN}),
    AttemptStatus.CANCEL_REQUESTED: frozenset({AttemptStatus.CANCELLED,
        AttemptStatus.COMPLETED, AttemptStatus.FAILED, AttemptStatus.UNCERTAIN}),
}


class EvidenceDisposition(str, Enum):
    AUTHORITATIVE = 'authoritative'
    LATE = 'late'


@dataclass(frozen=True)
class Request:
    request_id: str
    # Equality key for the immutable input + requested engine + provenance.
    # The caller supplies its canonical representation, not provider output.
    content_key: str
    origin: str = 'unknown'
    engine_key: str = ''
    settings_revision: int = 0


@dataclass(frozen=True)
class EngineChange:
    requested_engine_key: str
    settings_revision: int
    consent_reference: str


@dataclass(frozen=True)
class Attempt:
    attempt_id: str
    request_id: str
    owner_epoch: int
    privacy_generation: int
    engine_key: str = ''
    settings_revision: int = 0
    status: AttemptStatus = AttemptStatus.LAUNCH_INTENT
    revision: int = 0
    native_handle: str | None = None


@dataclass(frozen=True)
class ConversationState:
    conversation_id: str
    project_id: str
    revision: int = 0
    # Opaque canonical requested provider/model/effort/account tuple. Empty is
    # an explicit native-default choice, never an observed model identity.
    requested_engine_key: str = ''
    settings_revision: int = 0
    engine_changes: tuple[EngineChange, ...] = ()
    owner_id: str = ''
    owner_epoch: int = 0
    privacy_generation: int = 0
    deleted: bool = False
    requests: tuple[Request, ...] = ()
    attempts: tuple[Attempt, ...] = ()
    active_attempt: str | None = None
    high_water: int = 0
    covered_through: int = 0
    coverage_revision: int = 0
    coverage_complete: bool = False


@dataclass(frozen=True)
class OwnerToken:
    conversation_id: str
    owner_id: str
    owner_epoch: int
    privacy_generation: int
    project_id: str


@dataclass(frozen=True)
class AttemptToken:
    conversation_id: str
    attempt_id: str
    owner_epoch: int
    privacy_generation: int
    project_id: str


@dataclass(frozen=True)
class SnapshotToken:
    conversation_id: str
    privacy_generation: int
    high_water: int
    coverage_revision: int
    after: int
    project_id: str


def _name(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Identity must be a nonempty string')


def _revision(state: ConversationState, expected: int) -> None:
    if type(expected) is not int or state.revision != expected:
        raise LifecycleConflict('Stale conversation revision')


def _visible(state: ConversationState) -> None:
    if state.deleted:
        raise LifecycleConflict('Conversation is deleted')


def _owner(state: ConversationState, token: OwnerToken) -> None:
    _visible(state)
    if (token.project_id != state.project_id or token.conversation_id != state.conversation_id
            or not state.owner_id or token.owner_id != state.owner_id
            or token.owner_epoch != state.owner_epoch
            or token.privacy_generation != state.privacy_generation):
        raise LifecycleConflict('Stale owner token')


def _attempt(state: ConversationState, attempt_id: str) -> Attempt:
    for attempt in state.attempts:
        if attempt.attempt_id == attempt_id:
            return attempt
    raise LifecycleConflict('Unknown attempt')


def _replace_attempt(state: ConversationState, attempt: Attempt) -> ConversationState:
    return replace(state, revision=state.revision + 1,
                   attempts=tuple(attempt if a.attempt_id == attempt.attempt_id else a
                                  for a in state.attempts))


def claim_owner(state: ConversationState, owner_id: str, *, expected_revision: int
                ) -> tuple[ConversationState, OwnerToken]:
    """Explicit restart/takeover. Never infer that the previous process stopped."""
    _revision(state, expected_revision)
    _visible(state)
    _name(owner_id)
    attempts = state.attempts
    if state.active_attempt:
        active = _attempt(state, state.active_attempt)
        if active.status not in TERMINAL:
            uncertain = replace(active, status=AttemptStatus.UNCERTAIN,
                                revision=active.revision + 1)
            attempts = tuple(uncertain if a.attempt_id == active.attempt_id else a
                             for a in attempts)
    result = replace(state, revision=state.revision + 1, owner_id=owner_id,
                     owner_epoch=state.owner_epoch + 1, attempts=attempts)
    return result, OwnerToken(result.conversation_id, owner_id, result.owner_epoch,
                              result.privacy_generation, result.project_id)


def accept_request(state: ConversationState, request: Request, *, expected_revision: int
                   ) -> ConversationState:
    """Durable acceptance/queueing is independent from execution ownership."""
    _revision(state, expected_revision)
    _visible(state)
    _name(request.request_id)
    _name(request.content_key)
    if request.origin not in {'interactive', 'unattended', 'unknown'}:
        raise ValueError('Unknown request origin')
    previous = next((r for r in state.requests if r.request_id == request.request_id), None)
    if previous:
        if previous != request:
            raise LifecycleConflict('Request identity already has different content')
        return state
    if (not isinstance(request.engine_key, str)
            or request.engine_key != state.requested_engine_key
            or type(request.settings_revision) is not int
            or request.settings_revision != state.settings_revision):
        raise LifecycleConflict('Request must use current explicitly selected engine/settings')
    return replace(state, revision=state.revision + 1, requests=state.requests + (request,))


def change_engine(state: ConversationState, requested_engine_key: str, *,
                  consent_reference: str, expected_revision: int) -> ConversationState:
    """Explicit user settings transition; never called from model observation.

    Accepted requests and active/queued attempts retain their frozen choice.
    Caller must authenticate consent and persist this change atomically; a
    nonempty reference is an audit requirement, not proof of authorization.
    """
    _revision(state, expected_revision)
    _visible(state)
    _name(consent_reference)
    if not isinstance(requested_engine_key, str):
        raise ValueError('Engine key must be a canonical string')
    if requested_engine_key == state.requested_engine_key:
        return state
    change = EngineChange(requested_engine_key, state.settings_revision + 1, consent_reference)
    return replace(state, revision=state.revision + 1,
                   requested_engine_key=requested_engine_key,
                   settings_revision=change.settings_revision,
                   engine_changes=state.engine_changes + (change,))


def claim_attempt(state: ConversationState, owner: OwnerToken, request_id: str,
                  attempt_id: str, *, expected_revision: int
                  ) -> tuple[ConversationState, AttemptToken]:
    """Record launch intent. Caller still needs policy approval for retry/launch."""
    _revision(state, expected_revision)
    _owner(state, owner)
    _name(attempt_id)
    request = next((r for r in state.requests if r.request_id == request_id), None)
    if request is None:
        raise LifecycleConflict('Request was not accepted')
    if state.active_attempt is not None:
        raise LifecycleConflict('Existing execution must be resolved before claim')
    if any(a.attempt_id == attempt_id for a in state.attempts):
        raise LifecycleConflict('Attempt identity already exists')
    attempt = Attempt(attempt_id, request_id, owner.owner_epoch, state.privacy_generation,
                      engine_key=request.engine_key, settings_revision=request.settings_revision)
    result = replace(state, revision=state.revision + 1,
                     active_attempt=attempt_id, attempts=state.attempts + (attempt,))
    return result, AttemptToken(state.conversation_id, attempt_id, owner.owner_epoch,
                                state.privacy_generation, state.project_id)


def evidence_disposition(state: ConversationState, token: AttemptToken) -> EvidenceDisposition:
    """Retain attributable stale receipts without terminal/notification authority.

    Does not itself append anything. After deletion/restoration, old evidence is
    refused entirely. Import/reconciliation needs a separately authorized path.
    """
    _visible(state)
    if (token.project_id != state.project_id or token.conversation_id != state.conversation_id
            or token.privacy_generation != state.privacy_generation):
        raise LifecycleConflict('Stale evidence privacy generation')
    attempt = _attempt(state, token.attempt_id)
    if (token.owner_epoch != attempt.owner_epoch
            or token.privacy_generation != attempt.privacy_generation):
        raise LifecycleConflict('Token does not identify this attempt producer')
    if (token.owner_epoch != state.owner_epoch
            or state.active_attempt != attempt.attempt_id
            or attempt.status in TERMINAL or attempt.status == AttemptStatus.UNCERTAIN):
        return EvidenceDisposition.LATE
    return EvidenceDisposition.AUTHORITATIVE


def record_evidence(state: ConversationState, token: AttemptToken, *, sequence: int
                    ) -> tuple[ConversationState, EvidenceDisposition]:
    """Advance a committed evidence sequence without mutating execution outcome.

    Deduplication/payload persistence belongs to the same future DB transaction.
    Source-coverage changes use record_coverage; ordinary appends do not revoke a
    fixed earlier snapshot. Late evidence gets the same lossless sequence space.
    """
    disposition = evidence_disposition(state, token)
    if type(sequence) is not int or sequence != state.high_water + 1:
        raise LifecycleConflict('Evidence sequence must be contiguous')
    return replace(state, revision=state.revision + 1, high_water=sequence), disposition


def transition_attempt(state: ConversationState, token: AttemptToken,
                       new_status: AttemptStatus, *, expected_attempt_revision: int
                       ) -> ConversationState:
    """Only authoritative execution can transition; terminal outcomes are final.

    COMPLETED denotes execution completion, never accepted domain success.
    CANCEL_REQUESTED is not cancellation acknowledgement. A terminal outcome is
    supplied by the controller with evidence; this reducer cannot prove exit.
    """
    if evidence_disposition(state, token) != EvidenceDisposition.AUTHORITATIVE:
        raise LifecycleConflict('Late evidence has no transition authority')
    attempt = _attempt(state, token.attempt_id)
    if type(expected_attempt_revision) is not int or attempt.revision != expected_attempt_revision:
        raise LifecycleConflict('Stale attempt revision')
    if not isinstance(new_status, AttemptStatus) or new_status not in _TRANSITIONS.get(attempt.status, ()):
        raise LifecycleConflict('Invalid attempt transition')
    result = _replace_attempt(state, replace(attempt, status=new_status,
                                            revision=attempt.revision + 1))
    return replace(result, active_attempt=None) if new_status in TERMINAL else result


def bind_native_handle(state: ConversationState, token: AttemptToken, handle: str,
                       *, expected_attempt_revision: int) -> ConversationState:
    _name(handle)
    if evidence_disposition(state, token) != EvidenceDisposition.AUTHORITATIVE:
        raise LifecycleConflict('Late evidence cannot bind a native handle')
    attempt = _attempt(state, token.attempt_id)
    if type(expected_attempt_revision) is not int or attempt.revision != expected_attempt_revision:
        raise LifecycleConflict('Stale attempt revision')
    if attempt.native_handle == handle:
        return state
    if attempt.native_handle is not None:
        raise LifecycleConflict('Native handle cannot change within an attempt')
    return _replace_attempt(state, replace(attempt, native_handle=handle,
                                           revision=attempt.revision + 1))


def reconcile_attempt(state: ConversationState, owner: OwnerToken, attempt_id: str,
                      outcome: AttemptStatus, *, expected_attempt_revision: int,
                      resolution: str) -> ConversationState:
    """Current owner resolves uncertain execution using explicit external evidence.

    No blind adoption/relaunch: resolving RUNNING requires a future transport
    reattachment contract, so this minimal reducer accepts terminal resolutions
    only. A nonempty evidence reference is required, but is not verified here.
    """
    _owner(state, owner)
    _name(resolution)
    attempt = _attempt(state, attempt_id)
    if (state.active_attempt != attempt_id or attempt.status != AttemptStatus.UNCERTAIN
            or type(expected_attempt_revision) is not int
            or attempt.revision != expected_attempt_revision
            or not isinstance(outcome, AttemptStatus) or outcome not in TERMINAL):
        raise LifecycleConflict('Invalid reconciliation')
    result = _replace_attempt(state, replace(attempt, status=outcome,
                                            revision=attempt.revision + 1))
    return replace(result, active_attempt=None)


def set_deleted(state: ConversationState, deleted: bool, *, expected_revision: int
                ) -> ConversationState:
    """Revoke capture and snapshots, not OS processes or already-derived memory."""
    _revision(state, expected_revision)
    if type(deleted) is not bool:
        raise ValueError('deleted must be boolean')
    if deleted == state.deleted:
        return state
    attempts = state.attempts
    if state.active_attempt:
        active = _attempt(state, state.active_attempt)
        attempts = tuple(replace(a, status=AttemptStatus.UNCERTAIN, revision=a.revision + 1)
                         if a.attempt_id == active.attempt_id and a.status not in TERMINAL else a
                         for a in attempts)
    # Keep unresolved execution as active: restore cannot silently launch over it.
    return replace(state, revision=state.revision + 1, deleted=deleted,
                   privacy_generation=state.privacy_generation + 1,
                   owner_id='', owner_epoch=state.owner_epoch + 1, attempts=attempts)


def record_coverage(state: ConversationState, owner: OwnerToken, *, high_water: int,
                    complete: bool, expected_revision: int) -> ConversationState:
    """Attest source coverage, not merely that some events were successfully saved.

    Storage must validate actual sequences/source evidence before calling this.
    Changing the source attestation revokes previous derivation snapshots.
    """
    _revision(state, expected_revision)
    _owner(state, owner)
    if (type(high_water) is not int or not state.covered_through <= high_water <= state.high_water
            or type(complete) is not bool):
        raise ValueError('Coverage must be monotonic and cannot exceed captured evidence')
    return replace(state, revision=state.revision + 1, covered_through=high_water,
                   coverage_revision=state.coverage_revision + 1, coverage_complete=complete)


def snapshot(state: ConversationState, *, after: int = 0) -> SnapshotToken:
    _visible(state)
    if not state.coverage_complete:
        raise LifecycleConflict('Incomplete canonical source coverage')
    if type(after) is not int or not 0 <= after <= state.covered_through:
        raise ValueError('Invalid snapshot cursor')
    return SnapshotToken(state.conversation_id, state.privacy_generation,
                         state.covered_through, state.coverage_revision, after, state.project_id)


def validate_snapshot(state: ConversationState, token: SnapshotToken) -> None:
    """Check before each page AND atomically with derivation publication.

    This pure check does not close a check-then-MEMORY.md-write race. The future
    publication/deletion composition needs a shared transactional/locking guard.
    """
    _visible(state)
    if (token.project_id != state.project_id or token.conversation_id != state.conversation_id
            or token.privacy_generation != state.privacy_generation
            or token.coverage_revision != state.coverage_revision
            or not state.coverage_complete or token.high_water > state.covered_through
            or type(token.after) is not int or type(token.high_water) is not int
            or not 0 <= token.after <= token.high_water):
        raise LifecycleConflict('Snapshot revoked or coverage changed')


def validate_derivation(state: ConversationState, token: SnapshotToken, *,
                        current_cursor: int, processed_through: int,
                        complete: bool) -> None:
    """No operational/partial-processing failure may acknowledge a source span."""
    validate_snapshot(state, token)
    if (type(current_cursor) is not int or current_cursor != token.after
            or type(processed_through) is not int or processed_through != token.high_water
            or complete is not True):
        raise LifecycleConflict('Incomplete processing or competing derivation cursor')
