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
        if _policy.POLICY_KEY not in rec and _policy.VERSION_KEY not in rec:
            return None
        permitted = _policy.decision(rec, operation, purpose=purpose, capability=capability,
                                     route_id=route_id, account_id=account_id, account_kind=account_kind)
    if permitted is False:
        raise PermissionDenied(f'Desk {operation} permission denied for {account_id}: '
                               f'{purpose}/{capability} via {route_id}')
    return permitted


def require_publish(item: dict[str, Any]) -> bool | None:
    """Consent for the publisher's actual text API dispatch, not caller claims.

    Explicit Post covers post only. Replies keep their legacy human path but
    can never borrow explicit publish/post consent. Credential resolution and
    readiness remain with the existing publisher and its callers.
    """
    service = item.get('platform')
    if service not in ('x', 'linkedin'):
        raise PermissionDenied('this Desk publisher has no route for that service')
    account_id = item.get('account_id')
    return require_permission(account_id, service, 'post', purpose='publish',
                                   capability='reply' if 'in_reply_to' in item else 'post',
                                   route_id='x-oauth' if service == 'x' else 'linkedin-oauth',
                                   account_kind='account' if service == 'x' else 'organization')
