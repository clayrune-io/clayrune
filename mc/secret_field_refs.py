"""Metadata-only bindings for human-selected Setup fields (MC-1113).

Bindings occupy the existing canonical vault entry, so every consumer uses the
same resolver. Only the passcode-gated provider Save calls create(); no route
dispenses or creates a source. Typed slots remain encrypted in the vault.
"""
from __future__ import annotations

import threading
import hashlib
import json
from typing import Any

from mc import secrets_store as vault
from mc.core import _log, now_iso

_reading = threading.local()
RECONNECT = 'Reconnect this service because its saved entry is unavailable.'
UNLOCK = 'Unlock Secrets to use this connection.'


class ReferenceError(vault.SecretsError):
    def __init__(self, locked=False):
        super().__init__(UNLOCK if locked else RECONNECT)
        self.code = 'vault_locked' if locked else 'source_unavailable'


def clean(raw: Any) -> dict:
    if not isinstance(raw, dict) or set(raw) != {'name', 'part'}:
        raise ReferenceError()
    name, part = raw['name'], raw['part']
    if not isinstance(name, str) or not vault.valid_name(name) or vault.is_server_internal(name):
        raise ReferenceError()
    if part not in ('username', 'value'):
        raise ReferenceError()
    return {'name': name, 'part': part}


def resolve(raw: Any, *, project_id=None, unattended=False, consumer='desk_setup') -> str:
    """Resolve under the actual caller's policy; no canonical fallback on failure."""
    stack = getattr(_reading, 'stack', set())
    key = None
    try:
        ref = clean(raw)
        key = (ref['name'], ref['part'])
        if key in stack or len(stack) >= 12:
            raise ReferenceError()
        _reading.stack = stack | {key}
        # Even a username used as an app credential requires the unlocked vault.
        vault.load_master_key()
        if ref['part'] == 'value':
            value = vault.get_secret_value(ref['name'], consumer=consumer,
                                           project_id=project_id, unattended=unattended)
        else:
            value = vault.get_username(ref['name'], project_id=project_id)
        if not value.strip():
            raise ReferenceError()
        vault.register_dispensed(ref['name'], value)
        return value
    except vault.SecretsError:
        # Exception text may come from a corrupt source. Never print it.
        locked = vault.is_locked()
        _log(f'[desk_setup] saved field could not be resolved ({"vault_locked" if locked else "source_unavailable"})', flush=True)
        raise ReferenceError(locked) from None
    finally:
        _reading.stack = stack


def slot(rec: dict, part: str, **policy) -> str:
    refs = rec.get('field_refs')
    if not isinstance(refs, dict) or part not in refs:
        raise ReferenceError()
    return resolve(refs[part], **policy)


def has_slot(rec: dict, part: str) -> bool:
    refs = rec.get('field_refs')
    if not isinstance(refs, dict) or not refs or set(refs) - {'value', 'username'}:
        _log('[desk_setup] saved field binding is damaged; reconnect required', flush=True)
        raise ReferenceError()
    return part in refs


def create(name: str, value, *, username: str | dict = '', allow_unattended=True, entry_type=None, undo=None) -> dict:
    """Create a canonical binding, with the provider's ordinary undo semantics.

    Called only after the Save's human proof. Resolve now to catch broken sources;
    persist only the name/part. A newly selected source may rebind an existing
    canonical entry only with the provider's compensating undo stack.
    """
    refs = {}
    for part, item in (('value', value), ('username', username)):
        if isinstance(item, dict):
            refs[part] = clean(item)
            if refs[part]['name'] == name:
                raise ReferenceError()
            resolve(item)
    if not refs or not vault.valid_name(name):
        raise ReferenceError()
    if not isinstance(value, dict) and (not isinstance(value, str) or not value):
        raise ReferenceError()
    with vault._lock:
        store = vault._load_store()
        existing = store['secrets'].get(name)
        if existing and undo is None:
            raise vault.SecretExists('This connection already has saved details.')
        rec = {} if isinstance(value, dict) else vault._seal(name, value)
        rec.update(field_refs=refs, username='' if 'username' in refs else username,
                   scope='global', allow_unattended=allow_unattended,
                   kind=vault.KIND_PASSWORD, entry_type=entry_type or vault.ENTRY_API_KEY,
                   created_at=now_iso(), updated_at=now_iso(), use_count=0)
        store['secrets'][name] = rec
        vault._save_store(store)
        if undo is not None:
            def restore():
                with vault._lock:
                    current = vault._load_store()
                    stable = lambda r: {k: v for k, v in (r or {}).items() if k not in ('last_used_at', 'use_count')}
                    if stable(current['secrets'].get(name)) != stable(rec):
                        raise vault.SecretsError('Saved details changed during this Save; they were left in place.')
                    if existing is None:
                        current['secrets'].pop(name, None)
                    else:
                        current['secrets'][name] = existing
                    vault._save_store(current)
            undo.push(f'vault binding {name}', restore)
    vault.audit_event('set_reference', name=name)
    return vault._public(name, rec)


def health(name: str) -> dict | None:
    """Actionable, value-free card status; None for ordinary typed entries."""
    if not any(s['name'] == name and s.get('has_field_refs') for s in vault.list_secrets()):
        return None
    with vault._lock:
        rec = vault._load_store()['secrets'].get(name)
    if not rec or 'field_refs' not in rec:
        return None
    if vault.is_locked():
        return {'state': 'vault_locked', 'entry': name, 'reason': UNLOCK}
    try:
        if not isinstance(rec['field_refs'], dict) or not rec['field_refs'] or set(rec['field_refs']) - {'value', 'username'}:
            raise ReferenceError()
        for part in rec['field_refs']:
            slot(rec, part)
    except vault.SecretsError:
        locked = vault.is_locked()
        return {'state': 'vault_locked' if locked else 'key_unreadable',
                'entry': name, 'reason': UNLOCK if locked else RECONNECT}
    return None


def stamp(name: str) -> str:
    """Invalidate a prior verification when any bound source changes; no values leave."""
    with vault._lock:
        records = vault._load_store()['secrets']
    seen = set()
    def visit(key):
        if key in seen or len(seen) >= 12:
            return None
        seen.add(key)
        rec = records.get(key)
        if not isinstance(rec, dict):
            return None
        stable = {k: v for k, v in rec.items() if k not in ('last_used_at', 'use_count')}
        children = rec.get('field_refs') or {}
        return [stable, {part: visit(ref.get('name')) for part, ref in children.items() if isinstance(ref, dict)}]
    return hashlib.sha256(json.dumps(visit(name), sort_keys=True).encode()).hexdigest()


def patch_metadata(name: str, data: dict) -> dict:
    """Keep a human metadata edit from materializing a binding into a stale copy."""
    vault.load_master_key()
    with vault._lock:
        store = vault._load_store()
        rec = store['secrets'].get(name)
        if not rec or 'field_refs' not in rec:
            raise ReferenceError()
        if (data.get('value') or data.get('username', rec.get('username', '')) != rec.get('username', '')
                or data.get('kind', rec.get('kind')) != rec.get('kind')
                or data.get('entry_type', rec.get('entry_type')) != rec.get('entry_type')):
            raise vault.SecretsError('Change the saved entry in Setup, or edit the original entry in Secrets.')
        for key in ('description', 'hint', 'scope'):
            if key in data:
                if not isinstance(data[key], str):
                    raise vault.SecretsError('The saved entry details must be text.')
                rec[key] = data[key] or ('global' if key == 'scope' else '')
        if 'allow_unattended' in data:
            rec['allow_unattended'] = bool(data['allow_unattended'])
        rec['updated_at'] = now_iso()
        vault._save_store(store)
    vault.audit_event('edit_reference_metadata', name=name)
    return vault._public(name, rec)
