"""Whether a saved X account's own sign-in can be kept instead of made again (MC-1114).

A person reopening a saved X account to tick Post switches its Connection to the
developer app. The Save of that connection claims a freshly made sign-in, and
`desk_oauth.commit_held` refuses it with `already_signed_in` when the account's
vault entry already holds one: the account was never signed out, so nothing the
person can do in Connections lets the new sign-in in. This asks the question
first: does the account already hold a working sign-in for the SAME app? If so
the Save keeps it and changes nothing else (the permission change is its own
step), and no new sign-in is made.

Reads the entry's metadata hint (`state`, `exp`, `refresh`, `app_id_sha256`) and
compares the Client ID hash. An entry made before the hash existed (MC-1113) has
none: the stored record is then opened in this process and its own `client_id` is
compared with `hmac.compare_digest`; the value is never returned or logged and
nothing is written (no hint backfill). "The same app" is the Client ID this Save
would use. Anything it cannot confirm (a locked vault, a sign-in that lapsed, a
record with no `client_id`, another Client ID) answers False, which leaves the Save
as it was: a fresh sign-in, or the `already_signed_in` refusal, which says the way out.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

from mc import desk_oauth as _oauth
from mc import desk_vault_lock as _vault_lock
from mc import secrets_store
from mc.desk_connect import account_attach, x_account_attach

SERVICE = 'x'
UNLOCK = 'Unlock the Vault to check this sign-in; it is still saved.'
DIFFERENT = 'the saved sign-in was made with a different app'
NO_APP_RECORDED = ("the saved sign-in does not say which app made it, so Save cannot keep it. "
                   "Delete the saved sign-in '{name}' in Secrets, then sign in again.")


def check(service: str, account_id: Any, fields: dict | None = None) -> dict:
    """`{'reusable': bool, 'reason': str | None}`. Never raises."""
    if service != SERVICE or not isinstance(account_id, str) or not account_id:
        return {'reusable': False, 'reason': None}
    try:
        arg = x_account_attach.oauth_arg(x_account_attach.target(SERVICE, account_id))
        st = _oauth.status(SERVICE, arg)
        if st['state'] == _vault_lock.VAULT_LOCKED:
            return {'reusable': False, 'reason': UNLOCK}
        # A sign-in made before its entry recorded the app (`APP_UNCONFIRMED`) is not dead: the stored record says.
        if st['state'] != 'connected' and st.get('reason') != _oauth.APP_UNCONFIRMED:
            return {'reusable': False, 'reason': 'this account is not signed in with a working sign-in'}
        app = _oauth.save_client_id(SERVICE, fields or {})
        if not app:
            return {'reusable': False, 'reason': 'no app Client ID to compare the saved sign-in with'}
        name = _oauth.vault_name(SERVICE, arg)
        meta = next((s for s in secrets_store.list_secrets() if s['name'] == name), None)
        hint = {k: v for k, _, v in (p.partition('=') for p in ((meta or {}).get('hint') or '').split()) if v}
        saved = hint.get('app_id_sha256')
        if saved:
            same = hmac.compare_digest(saved, hashlib.sha256(app.encode()).hexdigest())
        else:
            return _by_record(arg, app, name)
    except _oauth.OAuthError as e:
        return {'reusable': False, 'reason': UNLOCK if e.code == _vault_lock.VAULT_LOCKED else None}
    except (account_attach.AttachError, LookupError, secrets_store.SecretsError):
        return {'reusable': False, 'reason': None}
    return {'reusable': True, 'reason': None} if same else {'reusable': False, 'reason': DIFFERENT}


def _by_record(arg: str | None, app: str, name: str) -> dict:
    """The entry predates the app hash in its hint (MC-1113): open the stored record in this process and compare
    its own `client_id` with the Client ID this Save would use. The value is compared, never returned or logged;
    nothing is written (no hint backfill)."""
    try:
        rec = _oauth._read_record(SERVICE, consumer='desk_signin_reuse', account_id=arg)
    except _oauth.OAuthError as e:
        if e.code == _vault_lock.VAULT_LOCKED:
            return {'reusable': False, 'reason': UNLOCK}
        return {'reusable': False, 'reason': 'the saved sign-in could not be opened'}
    held = rec.get('client_id')
    rec = None
    if not isinstance(held, str) or not held:
        return {'reusable': False, 'reason': NO_APP_RECORDED.format(name=name)}
    if not hmac.compare_digest(held.encode(), app.encode()):
        return {'reusable': False, 'reason': DIFFERENT}
    return {'reusable': True, 'reason': None}


def applies(clean: dict, signs_in: bool) -> bool:
    """For `provider_commit.apply`: this Save is for a saved X account with a sign-in to keep."""
    return bool(signs_in and clean.get('account_id') and check(clean['service'], clean['account_id'], clean.get('fields'))['reusable'])
