"""Neutral replay controller for a durable, format-bound source prefix."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO, Callable, TypeVar

from mc.capture_ingress import CaptureDecoder
from mc.conversation_store import ConversationStore, SourceCursor
from mc.execution_lifecycle import AttemptToken


class SourceRecoveryError(RuntimeError):
    """The durable source no longer matches its committed incarnation."""


DecoderT = TypeVar('DecoderT', bound=CaptureDecoder)


def replay_file_prefix(*, path: Path | None, store: ConversationStore | None,
                       token: AttemptToken, provider: str,
                       format_version: str, source_id: str, incarnation: str,
                       decoder_factory: Callable[[], DecoderT],
                       incognito: bool = False, seal: bool = False,
                       exit_status: int | None = None,
                       source: BinaryIO | None = None) -> SourceCursor | None:
    """Rebuild decoder state and commit the next complete source spans.

    The caller supplies a fixture/native source whose format is already bound
    explicitly. This function never interprets a provider source as another
    provider's format. Each restart creates a fresh decoder and replays the
    committed prefix before advancing, which preserves decoder RAM state such
    as open tools and last sequence.
    """
    if type(incognito) is not bool:
        raise ValueError('incognito must be boolean')
    if incognito:
        return None
    if type(seal) is not bool:
        raise ValueError('seal must be boolean')
    if store is None or (path is None) == (source is None):
        raise ValueError('exactly one path or binary source and a store are required')
    if source is None:
        assert path is not None
        if not path.is_file():
            raise SourceRecoveryError('durable source is missing')
        with path.open('rb') as opened:
            return _replay_stream(source=opened, store=store, token=token,
                provider=provider, format_version=format_version, source_id=source_id,
                incarnation=incarnation, decoder_factory=decoder_factory, seal=seal,
                exit_status=exit_status)
    return _replay_stream(source=source, store=store, token=token, provider=provider,
        format_version=format_version, source_id=source_id, incarnation=incarnation,
        decoder_factory=decoder_factory, seal=seal, exit_status=exit_status)


def _replay_stream(*, source: BinaryIO, store: ConversationStore, token: AttemptToken,
                   provider: str, format_version: str, source_id: str,
                   incarnation: str, decoder_factory: Callable[[], DecoderT],
                   seal: bool, exit_status: int | None) -> SourceCursor | None:
    """Replay a caller-owned, seekable binary stream without closing it."""
    try:
        if not source.seekable():
            raise SourceRecoveryError('replay source must be seekable')
        source.seek(0)
        if not isinstance(source.readline(), bytes):
            raise SourceRecoveryError('replay source must be binary')
        source.seek(0)
    except (AttributeError, OSError, TypeError) as exc:
        raise SourceRecoveryError('replay source must be seekable binary') from exc
    decoder = decoder_factory()
    # Validate the explicitly selected format before mutating durable source
    # metadata. Unsupported profiles must fail closed without even a binding
    # that could be mistaken for accepted evidence.
    cursor = store.bind_capture_source(token, provider=provider,
        format_version=format_version, source_id=source_id, incarnation=incarnation)
    count = 0
    saw_partial = False
    for raw in iter(source.readline, b''):
        if not isinstance(raw, bytes):
            raise SourceRecoveryError('replay source must be binary')
        if not raw.endswith(b'\n'):
            saw_partial = True
            break
        raw = raw[:-1]
        if raw.endswith(b'\r'):
            raw = raw[:-1]
        frame = raw.decode('utf-8', errors='strict')
        sequence = count
        count += 1
        digest = hashlib.sha256(frame.encode('utf-8')).hexdigest()
        prior = store.read_capture_span(token, source_sequence=sequence)
        if sequence <= cursor.cursor_sequence:
            if prior is None or prior[0] != digest or prior[1] != frame or not prior[2]:
                raise SourceRecoveryError('committed source prefix changed or is incomplete')
            decoder.feed(source_reference=f'{source_id}/{incarnation}/{sequence}',
                         sequence=sequence, frame_json=frame)
            continue
        if sequence != cursor.cursor_sequence + 1:
            raise SourceRecoveryError('source cursor cannot skip a span')
        store.stage_capture_span(token, source_sequence=sequence,
                                 frame_json=frame, frame_digest=digest)
        events = decoder.feed(source_reference=f'{source_id}/{incarnation}/{sequence}',
                              sequence=sequence, frame_json=frame)
        cursor = store.commit_capture_span(token, source_sequence=sequence,
                                           frame_digest=digest,
                                           events=[(f'{e.source_reference}:{e.event_index}', e.kind, e.payload)
                                                   for e in events])
    if saw_partial:
        raise SourceRecoveryError('source has an incomplete trailing frame')
    if cursor.cursor_sequence >= count:
        raise SourceRecoveryError('durable source was truncated below committed cursor')
    if seal:
        cursor = store.seal_capture_source(token, eof_sequence=count,
            exit_status=exit_status, event_id=f'{source_id}/{incarnation}/eof')
    return cursor
