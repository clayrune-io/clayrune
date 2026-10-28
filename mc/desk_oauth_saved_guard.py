"""Whether dropping a held sign-in may revoke it at the vendor (MC-1117).

A held sign-in (`desk_oauth_hold`) is dropped on cancel, back-out or expiry, and the drop revokes
its tokens at the vendor. For a brand-new account that is the right clean-up. For an account that
already has a SAVED sign-in from the same app it is not: X revoked the saved account's
authorization along with the held token (or the second grant retired the first), and the saved
refresh token then failed with a 400, so a cancelled re-sign-in turned a working account into
`needs_signin` (acct-0362a76b, 2026-10-26). Forgetting the held tokens in memory is enough, the
vendor's own expiry covers them.

`skip_reason` answers that one question. It reads the vault entry's METADATA (name, hint) only:
no secret is opened, nothing is written, no network call is made. It never raises: when the
vault cannot say, it answers "do not revoke", because a token that lingers unrevoked in a
forgotten memory slot costs nothing and a saved sign-in killed by a revoke costs the account.

Only X has a per-account app whose authorization a second grant can collide with. Higgsfield
registers a fresh client for every sign-in, so its held token is never the saved one's grant.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

from mc import secrets_store
from mc.core import _log

SAVED = 'account has a saved sign-in'
UNKNOWN = 'the account\'s saved sign-in could not be checked'


def skip_reason(service: str, account_id: str | None, rec: dict | None) -> str | None:
    """Why the held sign-in `rec` must NOT be revoked, or None when revoking is right. `account_id`
    is the `desk_oauth` account argument (None = the legacy singleton)."""
    if service != 'x':
        return None
    try:
        from mc import desk_oauth as _oauth      # lazy: desk_oauth imports the hold module, which imports this
        meta = _oauth._meta(service, account_id)
        if meta is None:
            return None
        hint = {k: v for k, _, v in (p.partition('=') for p in (meta.get('hint') or '').split()) if v}
        saved_app = hint.get('app_id_sha256')
        held_app = (rec or {}).get('client_id')
        if saved_app and isinstance(held_app, str) and held_app:
            # Another app's authorization is its own grant: revoking the held one cannot touch the saved one.
            same = hmac.compare_digest(saved_app, hashlib.sha256(held_app.encode()).hexdigest())
            return SAVED if same else None
        return SAVED        # a sign-in from before the hash was recorded: the entry's existence is all there is to go on
    except Exception as e:
        _log(f'[desk_oauth] checking for a saved {service} sign-in failed: {type(e).__name__}', flush=True)
        return UNKNOWN


def revoke_unless_saved(service: str, account_id: str | None, rec: dict | None, *, app: dict | None = None) -> bool | None:
    """`desk_oauth.revoke_record`, except that a held sign-in whose account already has a saved one
    is only forgotten. Logs the skip. Returns what `revoke_record` does (None = not revoked)."""
    from mc import desk_oauth as _oauth
    why = skip_reason(service, account_id, rec)
    if why:
        _log(f'[desk_oauth] held {service} sign-in dropped without revoke: {why}', flush=True)
        return None
    return _oauth.revoke_record(service, rec, app=app)
