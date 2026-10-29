"""Save a held sign-in without losing a dead saved X entry on rollback.

Only explicit dead-entry hints permit replacement. Keep the encrypted vault
record in the Save's undo closure, following secret_field_refs' rollback pattern.
The snapshot needs no decryption and no token is revoked: X grants may be shared
(MC-1117). The existing vault setter still handles token writes and redaction.
"""
from __future__ import annotations

from copy import deepcopy
import time

from mc import secrets_store as vault
from mc.desk_connect.undo import UndoStack


def _unusable(service: str, entry: dict) -> bool:
    if service != 'x':
        return False
    hint = {k: v for k, _, v in (p.partition('=') for p in (entry.get('hint') or '').split()) if v}
    if hint.get('state') == 'needs_signin':
        return True
    try:
        exp = int(hint.get('exp') or 0)
    except ValueError:
        return False
    return bool(exp and exp < time.time() and hint.get('refresh') != 'yes')


def store_with_undo(service: str, rec: dict, account_id: str | None, undo: UndoStack) -> None:
    """Called inside the account refresh lock; check and write under the vault lock.

    Undo restores the exact encrypted record, including metadata and policy. If
    another write has since changed the entry, refuse to overwrite that change.
    Usage counters alone do not stop rollback, as with saved-field bindings.
    """
    from mc import desk_oauth as oauth

    name = oauth.vault_name(service, account_id)
    with vault._lock:
        previous = deepcopy(vault._load_store()['secrets'].get(name))
        if previous is not None and not _unusable(service, previous):
            raise oauth.OAuthError('already_signed_in',
                                   f"{oauth.SERVICES[service]['label']} is already signed in under that name. "
                                   f"Delete the saved sign-in '{name}' in Secrets, then sign in again.", 409)
        oauth._store_record(service, rec, account_id)
        written = deepcopy(vault._load_store()['secrets'][name])

        def restore() -> None:
            with oauth._refresh_lock(name), vault._lock:
                store = vault._load_store()
                stable = lambda r: {k: v for k, v in (r or {}).items() if k not in ('last_used_at', 'use_count')}
                if stable(store['secrets'].get(name)) != stable(written):
                    raise vault.SecretsError('Saved sign-in changed during this Save; it was left in place.')
                if previous is None:
                    store['secrets'].pop(name, None)
                else:
                    store['secrets'][name] = previous
                vault._save_store(store)
            vault.audit_event('oauth_undo', name=name, restored=previous is not None)

        undo.push(f'vault entry {name}', restore)
