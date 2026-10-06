"""Attach X OAuth to a saved account (MC-1062/03b), without changing its browser route.

The browser names an existing account at start-held and Save. Validate through the
03 contract each time; use only that account's already-owned OAuth references.
No account, browser setup, permission or connection is created or rewritten here.
"""
from __future__ import annotations

from mc import desk_account_refs as _refs
from mc import desk_oauth as _oauth
from mc.desk_connect import account_attach


def target(service: str, account_id: object, *, identity: str | None = None) -> dict:
    """An X attachment target with usable, account-owned token/profile references."""
    rec = account_attach.target(service, account_id, identity=identity)
    if service != 'x':
        raise account_attach.AttachError('attaching an API is available only for X', 400, 'method_not_available')
    refs = rec['oauth'] or {}
    try:
        arg = _refs.require_oauth_arg(rec['account_id'])
    except LookupError as e:
        raise account_attach.AttachError(str(e), 400, 'account_refused') from e
    if (refs.get('oauth_vault') != _oauth.vault_name('x', arg)
            or refs.get('oauth_profile') != _oauth.profile_name('x', arg)):
        raise account_attach.AttachError('that account has no usable OAuth references', 409, 'account_refused')
    return rec


def oauth_arg(rec: dict) -> str | None:
    """The legacy singleton argument is valid only for its actual saved owner."""
    return None if rec['oauth']['oauth_vault'] == _oauth.vault_name('x') else rec['account_id']
