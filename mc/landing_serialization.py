"""Repository-wide landing ownership and exact Git-child leases (DH-M5).

Order: landing, snapshot/CAS, worktree lifecycle. The OS byte lock lives in
the common Git directory so checkout and project aliases share ownership.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from mc.atomic_json import write_json_atomic
from mc.dev_harness_catalogue import digest
from mc.dev_harness_diff import git_bytes
from mc.dev_harness_store import HarnessStore, _within

LEASE_ENV = 'CLAYRUNE_LANDING_LEASE'
_registry_lock = threading.Lock()
_locks: dict[str, threading.RLock] = {}
_local = threading.local()


class LandingBusy(RuntimeError):
    """Infrastructure contention: preserve work regardless of harness mode."""


def common_dir(repo: Path) -> Path:
    raw = git_bytes(repo, ['rev-parse', '--git-common-dir']).decode('utf-8').strip()
    return (repo / raw).resolve()


def approval_serialization(project: dict[str, Any] | None):
    """Human activation takes landing before the existing snapshot CAS lock."""
    from mc.project_sync import is_git_repo
    path = (project or {}).get('project_path', '')
    return landing(Path(path)) if path and is_git_repo(path) else nullcontext()


def process_start(pid: int) -> str:
    """Kernel creation identity; no PID-only lease validation or dependency."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise ValueError('lease_owner_unavailable')
        try:
            values = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
                raise ValueError('lease_owner_unavailable')
            return str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    # Linux /proc stat starts its field count after the parenthesized command.
    if Path('/proc').is_dir():
        raw = Path(f'/proc/{pid}/stat').read_text()
        return raw[raw.rfind(')') + 2:].split()[19]
    # macOS ships ps; lstart is immutable for the lifetime of a process.
    import subprocess
    value = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True,
                           check=True, timeout=5).stdout.decode('ascii').strip()
    if not value:
        raise ValueError('lease_owner_unavailable')
    return value


def _os_lock(stream, acquire: bool) -> None:
    stream.seek(0)
    if os.name == 'nt':
        import msvcrt
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK if acquire else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(stream.fileno(), (fcntl.LOCK_EX | fcntl.LOCK_NB) if acquire else fcntl.LOCK_UN)


@dataclass(frozen=True)
class Ownership:
    key: str
    token: str
    pid: int
    start: str
    thread: int


def assert_held(owner: Ownership) -> None:
    if (owner.pid != os.getpid() or owner.thread != threading.get_ident()
            or getattr(_local, 'held', {}).get(owner.key) is not owner):
        raise ValueError('landing_ownership_required')


@contextmanager
def landing(repo: Path, *, timeout: float = 5) -> Iterator[Ownership]:
    folder = common_dir(repo)
    key = os.path.normcase(str(folder))
    with _registry_lock:
        lock = _locks.setdefault(key, threading.RLock())
    deadline = time.monotonic() + timeout
    if not lock.acquire(timeout=max(0, timeout)):
        raise LandingBusy('landing_busy')
    held = getattr(_local, 'held', None)
    if held is None:
        held = _local.held = {}
    stream = None
    owner = None
    try:
        if key in held:
            yield held[key]
            return
        stream = (folder / 'clayrune-landing.lock').open('a+b')
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        while True:
            try:
                _os_lock(stream, True)
                break
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise LandingBusy('landing_busy') from exc
                time.sleep(0.02)
        owner = Ownership(key, uuid.uuid4().hex, os.getpid(), process_start(os.getpid()), threading.get_ident())
        held[key] = owner
        write_json_atomic(folder / 'clayrune-landing-owner.json', vars(owner), allow_nan=False)
        yield owner
    finally:
        try:
            if owner is not None:
                held.pop(key, None)
                try:
                    (folder / 'clayrune-landing-owner.json').unlink(missing_ok=True)
                finally:
                    if stream is not None:
                        _os_lock(stream, False)
        finally:
            try:
                if stream is not None:
                    stream.close()
            finally:
                lock.release()


def _lease_path(store: HarnessStore, project_id: str, identity: str) -> Path:
    if not isinstance(identity, str) or not re.fullmatch(r'[0-9a-f]{32}', identity):
        raise ValueError('invalid_landing_lease')
    folder = store.project_dir(project_id)
    path = folder / 'landing_leases' / (identity + '.json')
    if not _within(path.resolve(), folder.resolve()):
        raise ValueError('invalid_landing_lease')
    return path


@contextmanager
def child_lease(store: HarnessStore, project_id: str, owner: Ownership,
                pins: dict[str, Any], *, merge_timeout: float = 120) -> Iterator[dict[str, str]]:
    assert_held(owner)
    identity = uuid.uuid4().hex
    value = {'schema_version': 1, 'id': identity, 'project_id': project_id,
             'owner': vars(owner), 'created_at': time.time(), 'expires_at': time.time() + min(180, merge_timeout + 30),
             'pins': pins}
    value['content_digest'] = digest(value)
    path = _lease_path(store, project_id, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, value, allow_nan=False)
    try:
        yield {**os.environ, LEASE_ENV: identity}
    finally:
        path.unlink(missing_ok=True)


def validate_lease(store: HarnessStore, project_id: str, repo: Path, identity: str,
                   pins: dict[str, Any]) -> dict[str, Any]:
    value = store._read_json(_lease_path(store, project_id, identity))
    folder = common_dir(repo)
    key = os.path.normcase(str(folder))
    owner = value.get('owner', {})
    if (set(value) != {'schema_version', 'id', 'project_id', 'owner', 'created_at', 'expires_at', 'pins', 'content_digest'}
            or not isinstance(owner, dict) or set(owner) != {'key', 'token', 'pid', 'start', 'thread'}
            or type(value.get('created_at')) not in (int, float) or type(value.get('expires_at')) not in (int, float)
            or value.get('schema_version') != 1 or value.get('id') != identity
            or value.get('project_id') != project_id or value.get('pins') != pins
            or value.get('content_digest') != digest({k: v for k, v in value.items() if k != 'content_digest'})
            or not 0 <= time.time() - value['created_at'] < 180
            or not time.time() < value['expires_at'] <= value['created_at'] + 180.01
            or owner.get('key') != key or type(owner.get('pid')) is not int
            or process_start(owner['pid']) != owner.get('start')
            or store._read_json(folder / 'clayrune-landing-owner.json') != owner):
        raise ValueError('invalid_landing_lease')
    # A live PID and a leftover owner file are insufficient: prove the OS lock
    # is currently held. In-process calls must prove exact local ownership too.
    if owner['pid'] == os.getpid():
        active = getattr(_local, 'held', {}).get(key)
        if active is None or vars(active) != owner:
            raise ValueError('invalid_landing_lease')
    else:
        with (folder / 'clayrune-landing.lock').open('r+b') as stream:
            try:
                _os_lock(stream, True)
            except OSError:
                pass  # Expected contention is the proof, never a policy error.
            else:
                _os_lock(stream, False)
                raise ValueError('invalid_landing_lease')
    if store._read_json(folder / 'clayrune-landing-owner.json') != owner:
        raise ValueError('invalid_landing_lease')
    return value
