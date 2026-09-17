"""Store-backed execution ownership foundation.

This module is deliberately independent of routes, runtimes, and processes.
`ConversationStore` is the sole authority; callers may project returned state
into session dictionaries, but must never use that projection for decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
import json
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import uuid4

from mc import execution_lifecycle as lifecycle
from mc.conversation_store import ConversationStore, ConversationUnavailable


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
