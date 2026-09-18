"""Store-backed execution ownership foundation.

This module is deliberately independent of routes, runtimes, and processes.
`ConversationStore` is the sole authority; callers may project returned state
into session dictionaries, but must never use that projection for decisions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import json
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Optional
from uuid import uuid4

from mc import execution_lifecycle as lifecycle
from mc.conversation_store import ConversationStore, ConversationUnavailable


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise ValueError('mapping keys must be strings')
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    return value


@dataclass(frozen=True)
class DispatchFacts:
    """Immutable, caller-supplied facts at the generic runtime boundary."""
    project_id: str
    project_path: str
    mc_session_id: str
    provider: str
    model: str
    effort: Optional[str]
    resume_id: str
    task: str
    incognito: bool
    dispatch_id: str
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ('project_id', 'project_path', 'mc_session_id', 'provider',
                     'dispatch_id'):
            value = getattr(self, name)
            if type(value) is not str or not value or any(ord(c) < 32 for c in value):
                raise ValueError(f'{name} must be a non-empty string')
        if type(self.model) is not str or type(self.resume_id) is not str or type(self.task) is not str:
            raise ValueError('model, resume_id, and task must be strings')
        if self.effort is not None and type(self.effort) is not str:
            raise ValueError('effort must be a string or None')
        if type(self.incognito) is not bool:
            raise ValueError('incognito must be boolean')
        if not isinstance(self.provenance, Mapping) or any(type(k) is not str for k in self.provenance):
            raise ValueError('provenance keys must be strings')
        object.__setattr__(self, 'provenance', _freeze(dict(self.provenance)))


class RuntimeLifecycleBridge(Protocol):
    """Optional authority seam; implementations own persistence and fences."""
    def prepare(self, facts: DispatchFacts) -> None: ...
    def launch(self, spawn: Callable[[], Any]) -> Any: ...
    def on_init(self, event: Any, session: dict) -> None: ...
    def on_exit(self, event: Any, session: dict, native_source: Optional[Path]) -> None: ...


class AuthorizedRuntimeLifecycleBridge:
    """Composition object joining runtime callbacks to one injected owner.

    The caller supplies both the owner and the pre-authorized launch facts.
    This class owns no store, path, configuration, or provider defaults.
    """
    def __init__(self, *, owner: 'RuntimeAttemptOwner', facts: DispatchFacts,
                 launch_facts: dict, authorize: Callable[[], None]) -> None:
        if not callable(authorize):
            raise ValueError('fresh launch authorization callback is required')
        self.owner = owner
        self.facts = facts
        self.launch_facts = launch_facts
        self.authorize = authorize
        self.prepared: PreparedAttempt | None = None
        self._attempt_revision: int | None = None
        self._bound = False
        self._finished = False
        self._callback_lock = RLock()
        self._launch_pending = False
        self._pending_callbacks: list[tuple[str, Any, dict, Optional[Path]]] = []

    def prepare(self, facts: DispatchFacts) -> None:
        if facts != self.facts:
            raise ValueError('dispatch facts changed before preparation')
        self.prepared = self.owner.prepare(
            engine={'provider': facts.provider, 'model': facts.model,
                    'effort': facts.effort, 'resume_id': facts.resume_id,
                    'settings': dict(facts.provenance)},
            user_message={'text': facts.task}, request_id=f'{facts.dispatch_id}:request',
            provenance=dict(facts.provenance), launch_facts=self.launch_facts,
            incognito=facts.incognito)
        if self.prepared is None and not facts.incognito:
            raise RuntimeError('lifecycle preparation returned no attempt')

    def launch(self, spawn: Callable[[], Any]) -> Any:
        if self.prepared is None:
            if self.facts.incognito:
                return spawn()
            raise RuntimeError('launch before lifecycle preparation')
        launched: list[Any] = []

        def _owned_spawn() -> str:
            handle = spawn()
            launched.append(handle)
            process_reference = getattr(handle, 'mc_session_id', None)
            if not isinstance(process_reference, str) or not process_reference:
                raise TypeError('runtime launch must return an identified session handle')
            return process_reference

        with self._callback_lock:
            self._launch_pending = True
        try:
            state = self.owner.launch(self.prepared, authorize=self.authorize,
                                      spawn=_owned_spawn)
        except BaseException:
            with self._callback_lock:
                self._launch_pending = False
                self._pending_callbacks.clear()
            raise
        with self._callback_lock:
            attempt = next(a for a in state.attempts
                           if a.attempt_id == self.prepared.attempt.attempt_id)
            self._attempt_revision = attempt.revision
            self._launch_pending = False
            pending, self._pending_callbacks = self._pending_callbacks, []
            for kind, event, session, source in pending:
                try:
                    if kind == 'init':
                        self._apply_init(event, {'_lifecycle_native_source': source})
                    else:
                        self._apply_exit(event, session, source)
                except Exception as exc:
                    # Match route callback isolation; never turn an already
                    # launched process into a reported spawn failure.
                    session.setdefault('_lifecycle_errors', []).append(str(exc))
        return launched[0]

    def on_init(self, event: Any, session: dict) -> None:
        with self._callback_lock:
            if self._launch_pending:
                self._pending_callbacks.append(('init', SimpleNamespace(
                    payload=deepcopy(getattr(event, 'payload', {}))), session,
                    session.get('_lifecycle_native_source')))
                return
            self._apply_init(event, session)

    def _apply_init(self, event: Any, session: dict) -> None:
        if self.prepared is None or self.facts.incognito:
            return
        payload = getattr(event, 'payload', {}) or {}
        native = payload.get('session_id') or payload.get('thread_id')
        if not isinstance(native, str) or not native:
            raise RuntimeError('native identity missing from init')
        if self._attempt_revision is None:
            raise RuntimeError('native init arrived before launch ownership committed')
        native_source = session.get('_lifecycle_native_source')
        if not isinstance(native_source, Path):
            raise RuntimeError('authoritative native source missing from init')
        state, _facts = self.owner.bind_native(
            self.prepared, native, transcript_path=str(native_source),
            expected_attempt_revision=self._attempt_revision,
            event_id=f'{self.prepared.attempt.attempt_id}:native')
        attempt = next(a for a in state.attempts
                       if a.attempt_id == self.prepared.attempt.attempt_id)
        self._attempt_revision = attempt.revision
        self._bound = True

    def on_exit(self, event: Any, session: dict, native_source: Optional[Path]) -> None:
        with self._callback_lock:
            if self._launch_pending:
                self._pending_callbacks.append(('exit', SimpleNamespace(
                    payload=deepcopy(getattr(event, 'payload', {}))), session, native_source))
                return
            self._apply_exit(event, session, native_source)

    def _apply_exit(self, event: Any, session: dict, native_source: Optional[Path]) -> None:
        if self.prepared is None or self.facts.incognito or self._finished:
            return
        if not self._bound or self._attempt_revision is None:
            raise RuntimeError('terminal result arrived before native binding')
        payload = getattr(event, 'payload', {}) or {}
        rc = payload.get('rc')
        if type(rc) is not int:
            raise RuntimeError('terminal result is ambiguous')
        status = lifecycle.AttemptStatus.COMPLETED if rc == 0 else lifecycle.AttemptStatus.FAILED
        self.owner.finish(self.prepared, status,
                          expected_attempt_revision=self._attempt_revision,
                          event_id=f'{self.prepared.attempt.attempt_id}:finish:{rc}')
        self._finished = True


@dataclass(frozen=True)
class PreparedAttempt:
    owner: lifecycle.OwnerToken
    attempt: lifecycle.AttemptToken
    state: lifecycle.ConversationState


@dataclass(frozen=True)
class ReconstructedAttempt:
    token: lifecycle.AttemptToken
    status: lifecycle.AttemptStatus
    attempt_revision: int
    engine: Mapping[str, Any]
    request: Mapping[str, Any]
    native_handle: str | None
    launch_facts: Mapping[str, Any]


class RuntimeAttemptOwner:
    """CAS-based owner facade over the existing lifecycle store."""

    def __init__(self, *, store: ConversationStore, project_id: str,
                 conversation_id: str, owner_id: str) -> None:
        self.store = store
        self.project_id = project_id
        self.conversation_id = conversation_id
        self.owner_id = owner_id

    def prepare(self, *, engine: Mapping[str, object], user_message: dict,
                request_id: str, provenance: dict, event_prefix: str | None = None,
                launch_facts: dict | None = None, incognito: bool = False) -> PreparedAttempt | None:
        if type(incognito) is not bool:
            raise ValueError('incognito must be boolean')
        if incognito:
            return None
        if launch_facts is None:
            raise ValueError('launch_facts are required')
        if not isinstance(engine, Mapping) or not isinstance(provenance, dict):
            raise ValueError('engine and provenance are required objects')
        engine = _canonical(deepcopy(dict(engine)))
        user_message = _canonical(deepcopy(user_message))
        provenance = _canonical(deepcopy(provenance))
        prefix = event_prefix or uuid4().hex
        try:
            state = self.store.lifecycle_state(self.project_id, self.conversation_id)
        except ConversationUnavailable:
            state = self.store.create_lifecycle_conversation(
                self.project_id, self.conversation_id, engine=dict(engine),
                event_id=f'{prefix}:created')
            if state is None:
                return None
        if state.requested_engine_key != _json_engine(engine):
            raise lifecycle.LifecycleConflict('Requested engine differs from durable conversation')
        state, owner = self.store.claim_owner(
            self.project_id, self.conversation_id, owner_id=self.owner_id,
            expected_revision=state.revision, event_id=f'{prefix}:owner')
        state = self.store.accept_request(
            self.project_id, self.conversation_id, request_id=request_id,
            user_message=user_message, engine=dict(engine), provenance=provenance,
            expected_revision=state.revision, event_id=f'{prefix}:request')
        state, attempt = self.store.claim_attempt_with_launch_facts(
            owner, request_id=request_id, attempt_id=f'{prefix}:attempt',
            expected_revision=state.revision, event_id=f'{prefix}:attempt', facts=launch_facts)
        return PreparedAttempt(owner=owner, attempt=attempt, state=state)

    def launch(self, prepared: PreparedAttempt, *, authorize: Callable[[], None],
               spawn: Callable[[], str], event_id: str | None = None) -> lifecycle.ConversationState:
        return self.store.launch_claimed(
            prepared.attempt, expected_attempt_revision=0,
            event_id=event_id or f'{prepared.attempt.attempt_id}:launch',
            authorize=authorize, spawn=spawn)

    def bind_native(self, prepared: PreparedAttempt, handle: str, *,
                    transcript_path: str | None = None, expected_attempt_revision: int,
                    event_id: str | None = None
                    ) -> tuple[lifecycle.ConversationState, Mapping[str, Any]]:
        return self.store.bind_native_with_runtime_source(
            prepared.attempt, native_session_id=handle, transcript_path=transcript_path,
            expected_attempt_revision=expected_attempt_revision,
            event_id=event_id or f'{prepared.attempt.attempt_id}:native:{handle}:{expected_attempt_revision}')

    def finish(self, prepared: PreparedAttempt, status: lifecycle.AttemptStatus,
               *, expected_attempt_revision: int, event_id: str | None = None
               ) -> lifecycle.ConversationState:
        if status not in lifecycle.TERMINAL:
            raise ValueError('finish requires a terminal status')
        return self.store.transition_attempt(
            prepared.attempt, status, expected_attempt_revision=expected_attempt_revision,
            event_id=event_id or f'{prepared.attempt.attempt_id}:finish:{status.value}')

    def reconstruct(self, *, attempt_id: str | None = None,
                    native_handle: str | None = None) -> ReconstructedAttempt:
        state = self.store.lifecycle_state(self.project_id, self.conversation_id)
        if state.deleted:
            raise lifecycle.LifecycleConflict('Conversation is deleted')
        if attempt_id is None and native_handle is None:
            raise lifecycle.LifecycleConflict('Explicit attempt or native handle is required')
        attempt = next((a for a in state.attempts if
                        (attempt_id is not None and a.attempt_id == attempt_id) or
                        (native_handle is not None and a.native_handle == native_handle)), None)
        if attempt is None:
            raise lifecycle.LifecycleConflict('Attempt is missing')
        if attempt.status == lifecycle.AttemptStatus.UNCERTAIN:
            raise lifecycle.LifecycleConflict('Uncertain launch requires explicit reconciliation')
        request = self.store.read_request(self.project_id, self.conversation_id, attempt.request_id)
        if request is None:
            raise lifecycle.LifecycleConflict('Attempt request is missing')
        facts = self.store.read_runtime_launch_facts(self.project_id, self.conversation_id, attempt.attempt_id)
        if facts['provider'] != 'codex' or facts['requested_engine_json'] != attempt.engine_key:
            raise lifecycle.LifecycleConflict('Launch facts provider or engine mismatch')
        return ReconstructedAttempt(
            token=lifecycle.AttemptToken(self.conversation_id, attempt.attempt_id,
                                         attempt.owner_epoch,
                                         attempt.privacy_generation, self.project_id),
            status=attempt.status, attempt_revision=attempt.revision,
            engine=MappingProxyType(json.loads(attempt.engine_key)),
            request=MappingProxyType(request), native_handle=attempt.native_handle,
            launch_facts=MappingProxyType(facts))


def _json_engine(engine: Mapping[str, object]) -> str:
    import json
    return json.dumps(dict(engine), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _canonical(value: Any) -> Any:
    import json
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
