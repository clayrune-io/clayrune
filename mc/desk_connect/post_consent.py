"""Whether an account has the Post consent the publisher will ask for (MC-1114).

`permission_check.require_publish` refuses a Desk post when the account's
explicit policy lacks Post. Until now nothing told the person before Approve:
the account read as publish-ready, the campaign started, and the refusal only
surfaced as a failed version ("Approving again retries it") that could never
succeed. This module asks the SAME question, on the same stored record, ahead
of time. It never grants Post: the tick is still the person's, on the
Permissions step.

Pure reads of the record handed in. It takes no store lock (the callers hold
one or read the store themselves), writes nothing and contacts nothing.
Only an account the Desk posts through the API for is judged; an assisted or
manual account has a person press Share and never passes `require_publish`.
"""
from __future__ import annotations

from typing import Any

from mc.desk_connect import permission_policy as _policy

CODE = 'post_permission'
_ROUTE = {'x': 'x-oauth', 'linkedin': 'linkedin-oauth'}
_KIND = {'x': 'account', 'linkedin': 'organization'}
_API_CAPABILITIES = (None, '', 'direct')


def reason_for(rec: Any) -> str | None:
    """Why the publisher would refuse this account on consent, or None when it would not
    (Post allowed, a legacy account with no policy, or an account it never posts through the API for)."""
    if not isinstance(rec, dict) or rec.get('platform') not in _ROUTE or rec.get('preview'):
        return None
    if rec.get('capability') not in _API_CAPABILITIES:
        return None
    service = rec['platform']
    permitted = _policy.decision(rec, 'post', purpose='publish', capability='post', route_id=_ROUTE[service],
                                 account_id=rec.get('id'), account_kind=_KIND[service])
    if permitted is False:
        return 'Post is not allowed for this account yet: tick Post on its Permissions step.'
    return None


def view(rec: Any) -> dict | None:
    """The v1 account's `post_permission` member: present only when Post is missing."""
    why = reason_for(rec)
    return {'allowed': False, 'code': CODE, 'reason': why} if why else None


def start_problems(accounts: dict, account_ids) -> list[str]:
    """Start-gate lines for the accounts a campaign plans to post through (`accounts` is the store's map)."""
    out = []
    for aid in account_ids or []:
        rec = accounts.get(aid) if isinstance(aid, str) else None
        why = reason_for(rec)
        if why and rec:
            name = rec.get('label') or rec.get('identity') or aid
            out.append(f'{name} cannot post yet: {why[0].lower()}{why[1:]}')
    return out
