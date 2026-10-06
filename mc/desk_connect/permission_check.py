"""Account consent at Desk execution boundaries (MC-1062/06a).

Use the original workspace account id, never the OAuth argument (the first
account maps to None there). Missing consent on a real legacy account, or an
old caller with no account id, retains legacy behavior. A supplied bad id or
wrong service is refused, never resolved to another connection.

Allow is only an additional restriction. Each consumer retains its campaign,
provider, budget, credential and browser gates. Readers are wired in 06b-d.
"""
from __future__ import annotations

from typing import Any

from mc import desk as _desk
from mc import desk_account_refs as _refs
from mc import desk_oauth as _oauth
from mc.desk_connect import permission_policy as _policy


class PermissionDenied(ValueError):
    """An execution was refused before resolving a credential or calling out."""


def require_permission(account_id: str | None, service: str, operation: str, *,
                       purpose: str, capability: str, route_id: str,
                       account_kind: str | None = None) -> bool | None:
    """Check current persisted consent for an exact operation/scope.

    Returns True for explicit Allow or None for the genuine legacy path;
    False is raised as PermissionDenied. Never writes, chooses a route, reads
    a secret value, refreshes a token, or contacts a platform.
    """
    if operation not in ('read', 'post') or purpose != {'read': 'read_own', 'post': 'publish'}[operation]:
        raise PermissionDenied('that operation is not covered by Desk Read/Post permission')
    if account_id is None:
        return None
    if not isinstance(account_id, str) or not account_id:
        raise PermissionDenied('a supplied Desk account id must name a workspace account')
    with _desk._store_lock:
        rec = (_desk._read_store().get('accounts') or {}).get(account_id)
        if not isinstance(rec, dict) or rec.get('id') != account_id:
            raise PermissionDenied(f'Desk account {account_id!r} was not found; no other account will be used')
        if rec.get('platform') != service:
            raise PermissionDenied(f'Desk account {account_id!r} is not on {service}; no other account will be used')
        permitted = _policy.decision(rec, operation, purpose=purpose, capability=capability,
                                     route_id=route_id, account_id=account_id, account_kind=account_kind)
    if permitted is False:
        raise PermissionDenied(f'Desk {operation} permission denied for {account_id}: '
                               f'{purpose}/{capability} via {route_id}')
    return permitted


def require_publish(item: dict[str, Any], *, unattended: bool = False) -> bool | None:
    """Consent for the publisher's actual text API dispatch, not caller claims.

    Explicit Post covers post only. Replies keep their legacy human path but
    can never borrow explicit publish/post consent. Existing explicit accounts
    must also still be ready for the actual API publisher.
    """
    service = item.get('platform')
    if service not in ('x', 'linkedin'):
        raise PermissionDenied('this Desk publisher has no route for that service')
    account_id = item.get('account_id')
    if account_id is None and service == 'x':
        # Old callers use the singleton token. If a workspace account owns it,
        # its consent still applies: omitting the id must not bypass revocation.
        with _desk._store_lock:
            store = _desk._read_store()
            holder = store.get(_refs.LEGACY_FLAG)
            if not holder or holder is True:
                owners = [aid for aid, a in (store.get('accounts') or {}).items()
                          if isinstance(a, dict) and a.get('platform') == 'x'
                          and isinstance(a.get('credentials'), dict)
                          and a['credentials'].get('oauth_vault') == _oauth.vault_name('x')]
                if len(owners) > 1:
                    raise PermissionDenied('the legacy X sign-in has ambiguous workspace owners')
                if owners:
                    holder = owners[0]
        if holder:
            if not isinstance(holder, str):
                raise PermissionDenied('the legacy X sign-in owner cannot be resolved')
            account_id = holder
    permitted = require_permission(account_id, service, 'post', purpose='publish',
                                   capability='reply' if 'in_reply_to' in item else 'post',
                                   route_id='x-oauth' if service == 'x' else 'linkedin-oauth',
                                   account_kind='account' if service == 'x' else 'organization')
    if permitted is True:
        from mc import desk_accounts as _accounts  # lazy: accounts imports publisher constants
        with _desk._store_lock:
            rec = (_desk._read_store().get('accounts') or {}).get(account_id)
        if not isinstance(rec, dict) or rec.get('capability') == 'manual':
            raise PermissionDenied('this account is not an API publishing account')
        state = _accounts.publish_state(rec)
        if not state['ready']:
            raise PermissionDenied(f'this account cannot publish: {state["reason"]}')
        if unattended and state.get('unattended_ok') is False:
            raise PermissionDenied('this account credential is attended-only')
    return permitted
