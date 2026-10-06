"""Tells a LOCKED vault apart from a credential that cannot be opened (Ron, 2026-10-06:
"The Higgsfield connection established yesterday had to be re-connected again today").

`secrets_store.is_readable()` answers False for both a passphrase-locked vault and an
entry whose key really changed, and it swallows `VaultLocked`. The Desk used that False
to say "sign in again", so an idle relock (`vault_idle_lock_minutes`) made a good,
refreshable sign-in look dead and Ron re-signed in right after unlocking. A locked vault
loses nothing: the sign-in is still saved, it just cannot be opened until a human
unlocks it. This module is the one place the Desk makes that call, from the vault's own
`is_locked()` (never reimplemented), and it decrypts nothing.

`probe` checks the lock BEFORE `is_readable`: `is_readable` -> `load_master_key` fires the
"vault locked" push for a locked vault, and a status poll must not do that. It checks
AGAIN after a False, because an idle expiry can lapse inside the `is_readable` call itself
(the key was in memory a moment ago, and `is_locked()` only reads true once it is cleared).
"""
from __future__ import annotations

from mc import secrets_store

VAULT_LOCKED = 'vault_locked'

# Plain words for a card. No "vault entry" jargon, no em-dash.
SIGNIN_REASON = 'Your vault is locked. Unlock it; your sign-in is still saved.'
KEY_REASON = 'Your vault is locked. Unlock it; your saved key is still there.'
TOKEN_REASON = 'Your vault is locked. Unlock it; your saved token is still there.'

OK = 'ok'
LOCKED = 'locked'
UNREADABLE = 'unreadable'


def is_locked() -> bool:
    """True while the vault is passphrase-locked. Never raises: a vault that cannot say
    reads as "not locked", so the caller falls through to its old unreadable answer."""
    try:
        return bool(secrets_store.is_locked())
    except Exception:
        return False


def probe(name: str) -> str:
    """`'ok'`, `'locked'` (the vault, not the entry, is why it cannot be opened) or
    `'unreadable'` (the entry is missing, damaged, or its key changed)."""
    if is_locked():
        return LOCKED
    if secrets_store.is_readable(name):
        return OK
    return LOCKED if is_locked() else UNREADABLE
