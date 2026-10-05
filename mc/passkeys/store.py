"""Passkey credential registry (docs/PASSKEYS_SPEC.md, "Enrollment and storage").

Lives under ``~/.clayrune/passkeys/`` (``CLAYRUNE_HOME`` overrides it for tests),
never the repo and never DATA_DIR, so ``load_projects()`` cannot see it and no
project restore or build bundles it. Two files:

    registry.json   schema-versioned: owner handle, policy epoch, credentials
    enrolled.json   enrollment marker, written once a credential is activated

The marker is what makes "established" mean something. A registry that is
missing, unparseable, from a newer schema, rolled back below the marker's epoch,
or carrying a different owner handle than the marker raises `StoreCorrupt`.
Callers must treat that as "unavailable", never as "zero credentials": an empty
reading is how a deleted file would otherwise read as permission to fall back.

Revocation keeps a tombstone (id, label, times) and drops the public key, so a
revoked credential id can never be registered again and the registry never
shrinks back to look like an earlier state.

Writes are serialized in-process by a lock and across processes by an O_EXCL
lock file (the server restart deliberately overlaps two processes for ~10s),
then land through `write_json_atomic`. Same-user tampering is out of scope, as
the spec's threat model says.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, TypeVar

from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.core import _harden_secret_perms
from mc.secrets_store import clayrune_home

SCHEMA_VERSION = 1
MAX_ACTIVE_CREDENTIALS = 10
MAX_LABEL_LEN = 60

_LOCK_TIMEOUT_S = 15.0
_THREAD_LOCK = threading.RLock()
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,1024}$')
_T = TypeVar('_T')


class StoreError(Exception):
    code = 'store_error'


class StoreCorrupt(StoreError):
    """The registry cannot be trusted. Distinct from an empty one."""
    code = 'passkey_store_unreadable'


class DuplicateCredential(StoreError):
    code = 'duplicate_credential'


class UnknownCredential(StoreError):
    code = 'unknown_credential'


class TooManyCredentials(StoreError):
    code = 'too_many_credentials'


class PolicyChanged(StoreError):
    code = 'ceremony_invalidated'


class OwnerHandleMismatch(StoreError):
    code = 'owner_handle_mismatch'


def passkeys_dir() -> Path:
    return clayrune_home() / 'passkeys'


def registry_path() -> Path:
    return passkeys_dir() / 'registry.json'


def marker_path() -> Path:
    return passkeys_dir() / 'enrolled.json'


def _lock_path() -> Path:
    return passkeys_dir() / 'registry.lock'


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _empty_state() -> dict:
    return {'schema': SCHEMA_VERSION, 'owner_handle': None, 'enrolled_at': None,
            'policy_epoch': 0, 'credentials': []}


def _handle_digest(handle: str) -> str:
    return hashlib.sha256(handle.encode('ascii')).hexdigest()


def _read_json(path: Path, what: str) -> dict:
    try:
        raw = read_text_with_retry(path)
        obj = json.loads(raw)
    except FileNotFoundError:
        raise
    except (OSError, ValueError) as e:
        raise StoreCorrupt(f'{what} unreadable: {type(e).__name__}') from e
    if not isinstance(obj, dict):
        raise StoreCorrupt(f'{what} is not an object')
    return obj


def _validate_credential(c) -> None:
    if not isinstance(c, dict):
        raise StoreCorrupt('credential entry is not an object')
    cid = c.get('id')
    if not isinstance(cid, str) or not _ID_RE.match(cid):
        raise StoreCorrupt('credential id malformed')
    for key in ('rp_id', 'label', 'created_at'):
        if not isinstance(c.get(key), str):
            raise StoreCorrupt(f'credential {key} malformed')
    revoked = c.get('revoked_at')
    if revoked is not None and not isinstance(revoked, str):
        raise StoreCorrupt('credential revoked_at malformed')
    if revoked is None:
        if not isinstance(c.get('public_key'), str) or not c['public_key']:
            raise StoreCorrupt('active credential has no public key')
        if not isinstance(c.get('sign_count'), int) or isinstance(c.get('sign_count'), bool):
            raise StoreCorrupt('credential sign_count malformed')
        if not isinstance(c.get('transports', []), list):
            raise StoreCorrupt('credential transports malformed')


def _load_unlocked() -> dict:
    """Read + validate. Raises StoreCorrupt on anything untrustworthy; returns
    a fresh empty state only when NOTHING has ever been enrolled."""
    reg_p, mark_p = registry_path(), marker_path()
    marker = None
    try:
        marker = _read_json(mark_p, 'enrollment marker')
    except FileNotFoundError:
        pass
    try:
        state = _read_json(reg_p, 'registry')
    except FileNotFoundError:
        if marker is not None:
            raise StoreCorrupt('registry missing but enrollment marker exists')
        return _empty_state()
    if state.get('schema') != SCHEMA_VERSION:
        raise StoreCorrupt(f"registry schema {state.get('schema')!r} not supported")
    creds = state.get('credentials')
    if not isinstance(creds, list):
        raise StoreCorrupt('registry credentials is not a list')
    seen = set()
    for c in creds:
        _validate_credential(c)
        if c['id'] in seen:
            raise StoreCorrupt('registry has duplicate credential ids')
        seen.add(c['id'])
    epoch = state.get('policy_epoch')
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise StoreCorrupt('registry policy_epoch malformed')
    handle = state.get('owner_handle')
    if handle is not None and (not isinstance(handle, str) or not _ID_RE.match(handle)):
        raise StoreCorrupt('registry owner_handle malformed')
    if creds and (handle is None or not state.get('enrolled_at')):
        raise StoreCorrupt('registry has credentials but no owner handle')
    if marker is not None:
        if marker.get('schema') != SCHEMA_VERSION:
            raise StoreCorrupt('enrollment marker schema not supported')
        if handle is None or marker.get('owner_digest') != _handle_digest(handle):
            raise StoreCorrupt('registry owner does not match enrollment marker')
        m_epoch = marker.get('epoch')
        if not isinstance(m_epoch, int) or isinstance(m_epoch, bool) or epoch < m_epoch:
            raise StoreCorrupt('registry is older than the enrollment marker')
    elif creds:
        raise StoreCorrupt('registry has credentials but the enrollment marker is missing')
    return state


@contextlib.contextmanager
def _write_lock():
    """In-process RLock, then a cross-process O_EXCL lock file. A lock older
    than the timeout is broken rather than wedging every later write."""
    with _THREAD_LOCK:
        d = passkeys_dir()
        d.mkdir(parents=True, exist_ok=True)
        lock = _lock_path()
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        fd = None
        while fd is None:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    stale = (time.time() - lock.stat().st_mtime) > _LOCK_TIMEOUT_S
                except OSError:
                    stale = True
                if stale:
                    with contextlib.suppress(OSError):
                        lock.unlink()
                    continue
                if time.monotonic() > deadline:
                    raise StoreError('passkey registry is busy')
                time.sleep(0.005)
            except PermissionError:
                # Windows reports a delete-pending lock file as EACCES.
                if time.monotonic() > deadline:
                    raise StoreError('passkey registry is busy')
                time.sleep(0.005)
        try:
            yield
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                lock.unlink()


def _harden_dir(d: Path) -> None:
    """Owner-only directory: 0700 on POSIX; on Windows strip inheritance and
    grant the current user full control, inheritable so files made inside it
    (including each atomic-write temp file) carry the same ACL. Best-effort and
    never raises, like `_harden_secret_perms`."""
    try:
        if os.name == 'nt':
            import getpass
            import subprocess
            user = os.environ.get('USERNAME') or getpass.getuser()
            subprocess.run(['icacls', str(d), '/inheritance:r', '/grant:r', f'{user}:(OI)(CI)F'],
                           capture_output=True, stdin=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        else:
            os.chmod(str(d), 0o700)
    except Exception:
        pass


def _write_state(state: dict) -> None:
    d = passkeys_dir()
    if not (registry_path().exists() or marker_path().exists()):
        _harden_dir(d)
    write_json_atomic(registry_path(), state, indent=2, sort_keys=True)
    write_json_atomic(marker_path(), {
        'schema': SCHEMA_VERSION,
        'owner_digest': _handle_digest(state['owner_handle']),
        'epoch': state['policy_epoch'],
        'enrolled_at': state['enrolled_at'],
    }, indent=2, sort_keys=True)
    # A replace keeps the temp file's ACL, so harden on every write, as
    # local_auth does for its passcode hash.
    _harden_secret_perms(registry_path())
    _harden_secret_perms(marker_path())


def _mutate(fn: Callable[[dict], _T]) -> _T:
    with _write_lock():
        state = _load_unlocked()
        out = fn(state)
        _write_state(state)
        return out


def _public(c: dict) -> dict:
    """Metadata only. The public key and counter stay inside the store."""
    return {
        'id': c['id'], 'label': c['label'], 'rp_id': c['rp_id'],
        'created_at': c['created_at'], 'last_used_at': c.get('last_used_at'),
        'revoked_at': c.get('revoked_at'),
        'transports': list(c.get('transports') or []),
        'backup_eligible': bool(c.get('backup_eligible')),
        'backup_state': bool(c.get('backup_state')),
    }


def clean_label(label) -> str:
    text = ''.join(ch for ch in str(label or '') if ch.isprintable()).strip()
    return text[:MAX_LABEL_LEN] or 'Passkey ' + _now()[:10]


# ── reads ────────────────────────────────────────────────────────────────────

def load() -> dict:
    """The validated registry as a deep-enough copy to read. May raise
    StoreCorrupt; never returns a partial or default state for a damaged one."""
    with _THREAD_LOCK:
        return json.loads(json.dumps(_load_unlocked()))


def list_credentials() -> list:
    return [_public(c) for c in load()['credentials']]


def status() -> dict:
    state = load()
    active = [c for c in state['credentials'] if not c.get('revoked_at')]
    return {'enrolled': bool(state['enrolled_at']), 'active_count': len(active),
            'policy_epoch': state['policy_epoch']}


def owner_handle() -> Optional[str]:
    return load()['owner_handle']


def active_credential_ids() -> list:
    return [c['id'] for c in load()['credentials'] if not c.get('revoked_at')]


# ── writes ───────────────────────────────────────────────────────────────────

def add_credential(*, credential_id: str, public_key: str, rp_id: str, origin: str,
                   transports: list, sign_count: int, backup_eligible: bool,
                   backup_state: bool, aaguid: str, label: str, owner_handle: str,
                   epoch: int) -> dict:
    """Activate a verified credential. `epoch` is the policy epoch the ceremony
    started under; a revocation in between invalidates it."""
    if not _ID_RE.match(credential_id or ''):
        raise StoreError('credential id malformed')

    def apply(state: dict):
        if state['policy_epoch'] != epoch:
            raise PolicyChanged('policy changed during enrollment')
        if any(c['id'] == credential_id for c in state['credentials']):
            raise DuplicateCredential(credential_id)
        if sum(1 for c in state['credentials'] if not c.get('revoked_at')) >= MAX_ACTIVE_CREDENTIALS:
            raise TooManyCredentials(str(MAX_ACTIVE_CREDENTIALS))
        if state['owner_handle'] is None:
            state['owner_handle'] = owner_handle
            state['enrolled_at'] = _now()
        elif state['owner_handle'] != owner_handle:
            raise OwnerHandleMismatch('another enrollment finished first')
        rec = {
            'id': credential_id, 'public_key': public_key, 'rp_id': rp_id,
            'origin': origin, 'transports': [str(t) for t in transports or []],
            'sign_count': int(sign_count), 'backup_eligible': bool(backup_eligible),
            'backup_state': bool(backup_state), 'aaguid': aaguid, 'label': label,
            'created_at': _now(), 'last_used_at': None, 'revoked_at': None,
        }
        state['credentials'].append(rec)
        return _public(rec)

    return _mutate(apply)


def revoke(credential_id: str) -> dict:
    """Tombstone one credential and bump the policy epoch, which invalidates
    every ceremony that started before it. Revoking twice is an
    UnknownCredential, not a silent success."""
    def apply(state: dict):
        for c in state['credentials']:
            if c['id'] == credential_id and not c.get('revoked_at'):
                c['revoked_at'] = _now()
                c['public_key'] = None
                c['transports'] = []
                state['policy_epoch'] += 1
                return _public(c)
        raise UnknownCredential(credential_id)

    return _mutate(apply)
