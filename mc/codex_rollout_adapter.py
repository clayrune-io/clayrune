"""Explicit, opt-in owner boundary for durable Codex rollout replay.

This adapter is intentionally not wired into the runtime.  The caller must
already possess the authorized lifecycle token and immutable provenance; the
adapter never discovers projects, sessions, models, or accounts from a file.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import BinaryIO
from mc.capture_ingress import CaptureProvenance
from mc.capture_replay import replay_file_prefix
from mc.codex_rollout_capture import CodexRolloutCapture, SUPPORTED_FORMATS
from mc.conversation_store import ConversationStore
from mc.execution_lifecycle import AttemptToken


class CodexRolloutAdapterError(ValueError):
    """Caller-supplied authorization, source, or rollout metadata is invalid."""


def _same_path(left: str, right: str) -> bool:
    try:
        return str(Path(left).resolve()).casefold() == str(Path(right).resolve()).casefold()
    except (OSError, RuntimeError):
        return left.casefold() == right.casefold()


def _format_for_cli_version(cli_version: object) -> str | None:
    """The rollout format a CLI version writes, or None if unsurveyed."""
    if not isinstance(cli_version, str):
        return None
    match = re.fullmatch(r'0\.(15[34])\.[0-9]+', cli_version)
    return f'codex-rollout-jsonl-0.{match.group(1)}' if match else None


def _rollout_meta(stream: BinaryIO, format_version: str) -> tuple[str, str]:
    try:
        stream.seek(0)
        line = stream.readline()
    except (AttributeError, OSError) as exc:
        raise CodexRolloutAdapterError('Codex rollout source is unavailable') from exc
    if not isinstance(line, bytes) or not line.endswith(b'\n'):
        raise CodexRolloutAdapterError('Codex rollout metadata is incomplete')
    try:
        record = json.loads(line.decode('utf-8'))
    except (TypeError, ValueError) as exc:
        raise CodexRolloutAdapterError('Codex rollout metadata is malformed') from exc
    if not isinstance(record, dict) or record.get('type') != 'session_meta':
        raise CodexRolloutAdapterError('Codex rollout must begin with session_meta')
    payload = record.get('payload')
    if not isinstance(payload, dict):
        raise CodexRolloutAdapterError('Codex rollout session_meta payload is missing')
    native_id = payload.get('id')
    session_id = payload.get('session_id')
    cwd = payload.get('cwd')
    if not isinstance(native_id, str) or not native_id.strip() \
            or not isinstance(session_id, str) or not session_id.strip() \
            or native_id != session_id:
        raise CodexRolloutAdapterError('Codex rollout native session identities conflict or are missing')
    if _format_for_cli_version(payload.get('cli_version')) != format_version:
        raise CodexRolloutAdapterError(
            'Codex rollout CLI version is unsupported, missing, or not the requested format')
    if not isinstance(cwd, str) or not cwd.strip():
        raise CodexRolloutAdapterError('Codex rollout session identity or cwd is missing')
    return native_id, cwd


def replay_authorized_codex_rollout(*, path: Path | None, store: ConversationStore | None,
                                    token: AttemptToken, provenance: CaptureProvenance | None,
                                    project_path: str | None, native_session_id: str | None,
                                    source_id: str | None, incarnation: str | None,
                                    format_version: str, incognito: bool = False):
    """Replay one explicitly authorized durable rollout source.

    All source and lifecycle checks precede source binding.  A mismatch never
    creates a capture source row.  Replay remains observation-only regarding
    execution completion and coverage.
    """
    if type(incognito) is not bool:
        raise CodexRolloutAdapterError('incognito must be boolean')
    if incognito:
        return None
    if format_version not in SUPPORTED_FORMATS:
        raise CodexRolloutAdapterError('unsupported Codex rollout format')
    if path is None or store is None or provenance is None or project_path is None \
            or native_session_id is None or source_id is None or incarnation is None:
        raise CodexRolloutAdapterError('explicit rollout authorization is required')
    if not isinstance(path, Path) or not isinstance(project_path, str) or not project_path.strip():
        raise CodexRolloutAdapterError('explicit rollout path and project path are required')
    for value, label in ((native_session_id, 'native session ID'),
                         (source_id, 'source ID'), (incarnation, 'source incarnation')):
        if not isinstance(value, str) or not value.strip():
            raise CodexRolloutAdapterError(f'{label} is required')
    if not path.is_file():
        raise CodexRolloutAdapterError('Codex rollout source is missing')
    if provenance.provider != 'codex' or provenance.native_session_id != native_session_id \
            or provenance.requested_engine.get('provider') != 'codex':
        raise CodexRolloutAdapterError('Codex rollout provenance identity mismatch')
    if provenance.privacy_generation != token.privacy_generation:
        raise CodexRolloutAdapterError('Codex rollout privacy generation mismatch')
    try:
        with path.open('rb') as stream:
            if not stream.seekable():
                raise CodexRolloutAdapterError('Codex rollout source must be seekable')
            os.fstat(stream.fileno())  # pin validation and replay to one open descriptor
            observed_id, observed_cwd = _rollout_meta(stream, format_version)
            if observed_id != native_session_id:
                raise CodexRolloutAdapterError('Codex rollout native session identity mismatch')
            if not _same_path(observed_cwd, project_path):
                raise CodexRolloutAdapterError('Codex rollout project cwd mismatch')
            state = store.lifecycle_state(token.project_id, token.conversation_id)
            if state.requested_engine_key != provenance.requested_engine_json:
                raise CodexRolloutAdapterError('Codex rollout requested engine snapshot mismatch')
            stream.seek(0)
            return replay_file_prefix(path=None, source=stream, store=store, token=token,
                provider='codex', format_version=format_version, source_id=source_id,
                incarnation=incarnation,
                decoder_factory=lambda: CodexRolloutCapture(format_version=format_version))
    except OSError as exc:
        raise CodexRolloutAdapterError('Codex rollout source is unavailable') from exc
