"""Opt-in bridge from a native protocol decoder to the canonical journal.

The bridge deliberately has no provider, process, filesystem, or configuration
knowledge.  A composition root supplies an already-authorized lifecycle token,
the requested-engine snapshot, decoder, and ConversationStore.  It is not a
replacement for a native transcript and is never enabled implicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import json
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from mc.conversation_store import ConversationStore
from mc.execution_lifecycle import AttemptToken


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_plain(child) for child in value]
    return value


class CaptureDecoder(Protocol):
    def feed(self, *, source_reference: str, sequence: int,
             frame_json: str) -> tuple[Any, ...]: ...

    def finish(self, *, source_reference: str, sequence: int,
               exit_status: int | None) -> tuple[Any, ...]: ...


@dataclass(frozen=True)
class CaptureProvenance:
    """Immutable admission facts captured by the execution owner."""

    provider: str
    native_session_id: str
    mc_session_id: str
    requested_engine: Mapping[str, Any]
    privacy_generation: int
    requested_engine_json: str = field(init=False)

    def __post_init__(self) -> None:
        for name in ('provider', 'native_session_id', 'mc_session_id'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{name} must be a nonempty identity')
        if type(self.privacy_generation) is not int or self.privacy_generation < 0:
            raise ValueError('privacy_generation must be a nonnegative integer')
        if not isinstance(self.requested_engine, Mapping):
            raise ValueError('requested_engine must be an object')
        frozen = _freeze(deepcopy(dict(self.requested_engine)))
        object.__setattr__(self, 'requested_engine', frozen)
        object.__setattr__(self, 'requested_engine_json', json.dumps(
            _plain(frozen), ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False))


class CaptureIngress:
    """Decode source frames and atomically append their protocol evidence.

    Provider/native/MC IDs in provenance are observations from the same source;
    authority is the lifecycle token, requested-engine snapshot, and privacy
    generation. The bridge does not infer or authenticate native IDs.

    A failed store write leaves the exact decoded batch in ``pending``.  The
    caller must explicitly call :meth:`retry_pending`; the bridge never drops
    it or claims persistence.  Replaying a source frame after a restart is safe
    because the store owns stable event identity and conflict detection.
    """

    def __init__(self, *, decoder: CaptureDecoder, store: ConversationStore,
                 token: AttemptToken, provenance: CaptureProvenance):
        self.decoder = decoder
        self.store = store
        self.token = token
        self.provenance = provenance
        self._pending: list[tuple[str, str, dict[str, Any]]] | None = None

    @property
    def pending(self) -> tuple[tuple[str, str, dict[str, Any]], ...]:
        return tuple(deepcopy(self._pending or ()))

    def _reject_advanced_source(self) -> None:
        if self._pending is not None:
            raise RuntimeError('capture batch pending; retry before advancing source')

    def _authorize(self) -> None:
        state = self.store.lifecycle_state(self.token.project_id,
                                           self.token.conversation_id)
        if state.requested_engine_key != self.provenance.requested_engine_json:
            raise ValueError('requested-engine snapshot does not match lifecycle')
        attempt = next((a for a in state.attempts
                        if a.attempt_id == self.token.attempt_id), None)
        if attempt is None or attempt.privacy_generation != self.provenance.privacy_generation:
            raise ValueError('capture provenance does not match lifecycle attempt')

    @staticmethod
    def _batch(events: tuple[Any, ...]) -> list[tuple[str, str, dict[str, Any]]]:
        return [(f'{event.source_reference}:{event.event_index}', event.kind,
                 event.payload) for event in events]

    def _append(self, events: tuple[Any, ...]) -> list[tuple[Any, Any]]:
        if not events:
            return []
        batch = self._batch(events)
        self._pending = deepcopy(batch)
        self._authorize()
        saved = self.store.append_evidence_batch(self.token, batch)
        self._pending = None
        return saved

    def ingest(self, *, source_reference: str, sequence: int,
               frame_json: str) -> list[tuple[Any, Any]]:
        """Ingest one original native frame, before lossy UI parsing."""
        self._reject_advanced_source()
        return self._append(self.decoder.feed(source_reference=source_reference,
                                               sequence=sequence,
                                               frame_json=frame_json))

    def finish(self, *, source_reference: str, sequence: int,
               exit_status: int | None) -> list[tuple[Any, Any]]:
        self._reject_advanced_source()
        return self._append(self.decoder.finish(source_reference=source_reference,
                                                sequence=sequence,
                                                exit_status=exit_status))

    def retry_pending(self) -> list[tuple[Any, Any]]:
        if self._pending is None:
            return []
        self._authorize()
        saved = self.store.append_evidence_batch(self.token, deepcopy(self._pending))
        self._pending = None
        return saved


def build_capture_ingress(*, incognito: bool, decoder: CaptureDecoder | None = None,
                          store: ConversationStore | None = None,
                          token: AttemptToken | None = None,
                          provenance: CaptureProvenance | None = None
                          ) -> CaptureIngress | None:
    """Composition helper; incognito returns before decoder/store use."""
    if type(incognito) is not bool:
        raise ValueError('incognito must be boolean')
    if incognito:
        return None
    if decoder is None or store is None or token is None or provenance is None:
        raise ValueError('authorized decoder, store, token, and provenance required')
    return CaptureIngress(decoder=decoder, store=store, token=token,
                          provenance=provenance)
