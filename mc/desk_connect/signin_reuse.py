"""Whether a saved X account's own sign-in can be kept instead of made again (MC-1114).

A person reopening a saved X account to tick Post switches its Connection to the
developer app. The Save of that connection claims a freshly made sign-in, and
`desk_oauth.commit_held` refuses it with `already_signed_in` when the account's
vault entry already holds one: the account was never signed out, so nothing the
person can do in Connections lets the new sign-in in. This asks the question
first: does the account already hold a working sign-in for the SAME app? If so
the Save keeps it and changes nothing else (the permission change is its own
step), and no new sign-in is made.

Reads only names and the entry's metadata hint (`state`, `exp`, `refresh`,
`app_id_sha256`); the token is never opened and nothing is written or sent.
"The same app" is the Client ID hash the entry was stored with against the Client
ID this Save would use. Anything it cannot confirm (no hash, a locked vault,
a sign-in that lapsed, another Client ID) answers False, which leaves the Save
exactly as it was: a fresh sign-in, or the refusal it already gave.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

from mc import desk_oauth as _oauth
from mc import secrets_store
from mc.desk_connect import account_attach, x_account_attach

SERVICE = 'x'


def check(service: str, account_id: Any, fields: dict | None = None) -> dict:
    """`{'reusable': bool, 'reason': str | None}`. Never raises."""
    if service != SERVICE or not isinstance(account_id, str) or not account_id:
        return {'reusable': False, 'reason': None}
    try:
        arg = x_account_attach.oauth_arg(x_account_attach.target(SERVICE, account_id))
        if _oauth.status(SERVICE, arg)['state'] != 'connected':
            return {'reusable': False, 'reason': 'this account is not signed in with a working sign-in'}
        app = _oauth.save_client_id(SERVICE, fields or {})
        if not app:
            return {'reusable': False, 'reason': 'no app Client ID to compare the saved sign-in with'}
        name = _oauth.vault_name(SERVICE, arg)
        meta = next((s for s in secrets_store.list_secrets() if s['name'] == name), None)
    except (account_attach.AttachError, LookupError, _oauth.OAuthError, secrets_store.SecretsError):
        return {'reusable': False, 'reason': None}
    hint = {k: v for k, _, v in (p.partition('=') for p in ((meta or {}).get('hint') or '').split()) if v}
    saved = hint.get('app_id_sha256')
    if not saved:
        return {'reusable': False, 'reason': 'the saved sign-in does not say which app made it'}
    if not hmac.compare_digest(saved, hashlib.sha256(app.encode()).hexdigest()):
        return {'reusable': False, 'reason': 'the saved sign-in was made with a different app'}
    return {'reusable': True, 'reason': None}


def applies(clean: dict, signs_in: bool) -> bool:
    """For `provider_commit.apply`: this Save is for a saved X account with a sign-in to keep."""
    return bool(signs_in and clean.get('account_id') and check(clean['service'], clean['account_id'], clean.get('fields'))['reusable'])
