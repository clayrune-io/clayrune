"""MC-1079: share a cookie jar across sites, isolate a second same-site login.

Saved references always win. Selection never moves/deletes a profile or changes
a binding, and it never refuses an explicitly selected shared LinkedIn login.
"""
from __future__ import annotations

import re

from mc import state


def shared_default() -> str:
    return str(state.CONFIG.get('browser_default_profile') or 'main').strip().lower() or 'main'


def bound_profile(account: dict | None) -> str | None:
    if not account:
        return None
    setup = account.get('browser_setup') or {}
    refs = setup.get('refs') or {}
    return (refs.get('browser_profile') or account.get('browser_profile')
            or (account.get('credentials') or {}).get('oauth_profile'))


def select(service: str, accounts: dict, *, account_id: str | None = None,
           identity: str | None = None, separate: str | None = None) -> str:
    own = accounts.get(account_id) if account_id else None
    if not isinstance(own, dict) or own.get('platform') != service:
        own = None
    if own is None and identity:
        own = next((a for a in accounts.values() if isinstance(a, dict)
                    and a.get('platform') == service and
                    str(a.get('identity') or '').lower() == identity.lower()), None)
    saved = bound_profile(own)
    if saved:
        return saved
    others = [a for a in accounts.values() if isinstance(a, dict)
              and a is not own and a.get('platform') == service and not a.get('preview')]
    if not others:
        return shared_default()
    slug = re.sub(r'[^a-z0-9]+', '-', str(identity or account_id or 'account').lower()).strip('-')
    base = separate or (f'{service}-{slug}'[:41].rstrip('-_'))
    from mc.desk_connect.browser_setup_record import held_refs
    used = set().union(*(held_refs(a)['profiles'] for a in accounts.values() if isinstance(a, dict)))
    name, suffix = base, 2
    while name in used or name == shared_default():
        tail = f'-{suffix}'
        name = base[:41 - len(tail)] + tail
        suffix += 1
    return name


def for_account(service: str, *, account_id: str | None = None,
                identity: str | None = None, separate: str | None = None) -> str:
    from mc import desk
    with desk._store_lock:
        return select(service, desk._read_store().get('accounts') or {},
                      account_id=account_id, identity=identity, separate=separate)


def new_account(service: str, accounts: dict, account: dict) -> str:
    separate = None
    if service == 'x':
        from mc import desk_oauth
        # A held OAuth sign-in reserves its account id before Save. Use the
        # same name before and after creation, regardless of the typed handle.
        separate = 'desk-x-' + desk_oauth._suffix(account['id'])
    return select(service, accounts, account_id=account['id'],
                  identity=account.get('identity'), separate=separate)


def oauth_profile(service: str, account_id: str | None, old: str, vault: str) -> str:
    from mc import desk, desk_oauth_profile
    with desk._store_lock:
        accounts = desk._read_store().get('accounts') or {}
        own = next((a for a in accounts.values() if isinstance(a, dict)
                    and (a.get('credentials') or {}).get('oauth_vault') == vault), None)
        if own:
            # New shared profiles have an independent, creation-time binding.
            # Legacy profiles retain their deterministic names. Do not trust
            # credentials here: attachment compares that reference to this one.
            return own.get('oauth_profile_binding') or old
        if desk_oauth_profile.existed(old):
            return old
        return select(service, accounts, account_id=own.get('id') if own else account_id, separate=old)
