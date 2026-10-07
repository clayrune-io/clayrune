"""Small registry for remote-engine schema snapshots; no route or credential API.

User save/price/render paths refresh stale evidence. Discovery never grants a
tool permission or marks a connection Verified; that remains human-gated.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from mc.core import _log

TTL_SECONDS = 24 * 60 * 60


class CaptureBusy(RuntimeError):
    """A human probe must not mistake another capture for a verified connection."""


@dataclass
class Snapshotter:
    read: Callable[[], dict | None]
    refresh: Callable[..., None]
    capabilities: Callable[[dict | None, str, str], dict] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


_snapshotters: dict[str, Snapshotter] = {}
_connections: dict[tuple[str, str], str] = {}
_registry_lock = threading.RLock()


def register(engine_id: str, *, service: str, method: str,
             read: Callable[[], dict | None], refresh: Callable[..., None],
             capabilities: Callable[[dict | None, str, str], dict] | None = None) -> None:
    with _registry_lock:
        _snapshotters[engine_id] = Snapshotter(read, refresh, capabilities)
        _connections[(service, method)] = engine_id


def _entry(engine_id: str) -> Snapshotter | None:
    with _registry_lock:
        if engine_id == 'higgsfield_mcp' and engine_id not in _snapshotters:
            from mc.desk_connect import higgsfield_mcp_capture, higgsfield_mcp_snapshot
            from mc.desk_mcp_picture_schema import derive
            register(engine_id, service='higgsfield', method='oauth',
                     read=higgsfield_mcp_snapshot.read, refresh=higgsfield_mcp_capture.refresh,
                     capabilities=derive)
        return _snapshotters.get(engine_id)


def read(engine_id: str) -> dict | None:
    entry = _entry(engine_id)
    return _read(entry, engine_id) if entry else None


def _read(entry: Snapshotter, engine_id: str) -> dict | None:
    try:
        snapshot = entry.read()
        if snapshot is not None and not isinstance(snapshot, dict):
            raise ValueError('malformed engine snapshot')
        return snapshot
    except Exception as e:
        _log(f'[desk_engine_schemas] {engine_id} read failed: {type(e).__name__}', flush=True)
        return None


def registered(engine_id: str) -> bool:
    return _entry(engine_id) is not None


def model_inputs(engine_id: str, kind: str, model_id: str) -> dict | None:
    entry = _entry(engine_id)
    if entry is None or entry.capabilities is None:
        return None
    snapshot = read(engine_id)
    inputs = entry.capabilities(snapshot if fresh(snapshot) else None, kind, model_id)
    if snapshot is not None and not fresh(snapshot):
        inputs.update(schema_state='stale', picture_refusal=
                      'The engine model list is older than 24 hours; Clayrune refreshes it on the next price check')
    return inputs


def fresh(snapshot: dict | None) -> bool:
    if not snapshot or snapshot.get('untrusted_vendor_text') is not True:
        return False
    try:
        captured = datetime.fromisoformat(snapshot['captured_at'])
        age = (datetime.now(timezone.utc) - captured).total_seconds()
        return 0 <= age < TTL_SECONDS
    except (KeyError, TypeError, ValueError):
        return False


def ensure(engine_id: str, *, token: str | None = None, project_id: str | None = None,
           unattended: bool = False, force: bool = False, strict: bool = False) -> dict | None:
    entry = _entry(engine_id)
    if entry is None:
        return None
    snapshot = _read(entry, engine_id)
    if not force and fresh(snapshot):
        return snapshot
    # Another request never waits behind discovery; use the previous observation.
    if not entry.lock.acquire(blocking=False):
        if strict:
            raise CaptureBusy('schema capture is already in progress')
        return snapshot
    try:
        snapshot = _read(entry, engine_id)
        if force or not fresh(snapshot):
            entry.refresh(token=token, project_id=project_id, unattended=unattended)
        return _read(entry, engine_id)
    except Exception as e:
        _log(f'[desk_engine_schemas] {engine_id} capture failed: {type(e).__name__}', flush=True)
        if strict:
            raise
        return snapshot
    finally:
        entry.lock.release()


def connection_saved(service: str, method: str, *, token: str | None = None) -> None:
    # Load the built-in registration even when a connection precedes any price check.
    _entry('higgsfield_mcp')
    engine_id = _connections.get((service, method))
    if engine_id:
        ensure(engine_id, token=token, force=True)
