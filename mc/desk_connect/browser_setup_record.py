"""The shape of a saved browser setup on a Desk account (MC-1062 ticket 03), and the pure reads of it.

    account['browser_setup'] = {
        'service': 'x', 'route_id': 'x-browser', 'account_kind': 'account',
        'refs': {'browser_profile': 'x-ron', 'login': 'x.ron'},          # `login` only when one was chosen
        'saved_at': '2026-10-06T12:00:00',
    }

NAMES ONLY. `login` is the name of a vault entry, `browser_profile` the name of a named browser
profile; a password, a cookie and a token are never here. It lives in its own field, not in
`connections[purpose][capability]`, so an account can hold a browser sign-in with no permission at
all, and so removing a permission can never remove the sign-in. It is also not the legacy read
setting (`read_via`, `browser_profile` on the account): writing a setup never changes how the Desk
reads an account today.

This module imports nothing from `signin_fill`, which reads it (`saved_refs`), so the two can depend
on one shape without a cycle.
"""
from __future__ import annotations

FIELD = 'browser_setup'
REF_KEYS = ('browser_profile', 'login')


def build(service: str, route_id: str, account_kind: str, refs: dict, saved_at: str) -> dict:
    return {'service': service, 'route_id': route_id, 'account_kind': account_kind,
            'refs': {k: refs[k] for k in REF_KEYS if refs.get(k)}, 'saved_at': saved_at}


def of(rec: dict | None) -> dict | None:
    """The account's setup, or None. Tolerates a hand-edited record: a malformed one reads as absent."""
    s = rec.get(FIELD) if isinstance(rec, dict) else None
    if not (isinstance(s, dict) and isinstance(s.get('route_id'), str) and isinstance(s.get('refs'), dict)):
        return None
    return s


def saved_refs(rec: dict | None, route_id: str) -> dict:
    """The saved refs for one sign-in route (`login`, `browser_profile`), empty when the account has
    no setup for that route."""
    s = of(rec)
    if s is None or s['route_id'] != route_id:
        return {}
    return {k: v for k, v in s['refs'].items() if k in REF_KEYS and isinstance(v, str) and v}


def held_refs(rec: dict) -> dict:
    """Every browser profile and login name an account holds, from any of its own records, as
    `{'profiles': set, 'logins': set}`: the setup, the legacy read profile, the account's OAuth
    sign-in profile and every purpose binding."""
    profiles: set = set()
    logins: set = set()
    s = of(rec)
    refs = s['refs'] if s is not None else {}
    for k, bag in (('browser_profile', profiles), ('login', logins)):
        if isinstance(refs.get(k), str) and refs[k]:
            bag.add(refs[k])
    for v in (rec.get('browser_profile'), (rec.get('credentials') or {}).get('oauth_profile')):
        if isinstance(v, str) and v:
            profiles.add(v)
    conns = rec.get('connections')
    for caps in conns.values() if isinstance(conns, dict) else []:
        for b in caps.values() if isinstance(caps, dict) else []:
            r = b.get('refs') if isinstance(b, dict) else None
            for k, bag in (('browser_profile', profiles), ('oauth_profile', profiles), ('login', logins)):
                if isinstance(r, dict) and isinstance(r.get(k), str) and r[k]:
                    bag.add(r[k])
    return {'profiles': profiles, 'logins': logins}


def kind_of(rec: dict) -> str | None:
    """The account kind recorded on the account (setup first, then any binding), or None."""
    s = of(rec)
    if s is not None and isinstance(s.get('account_kind'), str):
        return s['account_kind']
    conns = rec.get('connections')
    for caps in conns.values() if isinstance(conns, dict) else []:
        for b in caps.values() if isinstance(caps, dict) else []:
            if isinstance(b, dict) and isinstance(b.get('account_kind'), str):
                return b['account_kind']
    return None


def bound_routes(rec: dict) -> set:
    """Route ids this account has purpose bindings for."""
    out: set = set()
    conns = rec.get('connections')
    for caps in conns.values() if isinstance(conns, dict) else []:
        for b in caps.values() if isinstance(caps, dict) else []:
            if isinstance(b, dict) and isinstance(b.get('route_id'), str):
                out.add(b['route_id'])
    return out
