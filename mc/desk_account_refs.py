"""Which sign-in belongs to which Desk account (docs/DESK_CONNECT_BY_URL_SPEC.md,
"Multiple accounts", Dave 2026-10-03: per-account vault and profile references;
migrate the singleton OAuth names in slice 2).

Until now one X sign-in (`oauth.x`, browser profile `desk-x`) served every X
account. An account now carries a NON-SECRET reference to its own:

    account['credentials'] = {'oauth_vault': 'oauth.x', 'oauth_profile': 'desk-x'}          # legacy names
    account['credentials'] = {'oauth_vault': 'oauth.x.acct_1', 'oauth_profile': 'desk-x-acct_1'}

The FIRST X account keeps the legacy names, so a sign-in made before this existed
keeps working and nothing is signed out. Every later account gets names of its
own and starts "not signed in": it never reads the singleton's token, which would
post as the wrong account. `data['oauth_legacy_bound']` records that the legacy
names have been handed out, once, and holds the id of the account that has them:
deleting that account does not free them for a stranger (the next account would
inherit the previous person's sign-in), but restoring it with Undo (the same id
again) gives them back to it. The singleton stays in the vault until a human
disconnects it.

`bind` is deterministic and idempotent: it runs on every store read
(`mc.desk._migrate_store`) and when an account is created, binding unbound
accounts oldest first. A record that already has `credentials` is never touched.
Only a direct, non-preview X account has a sign-in to refer to.
"""
from __future__ import annotations

from mc import desk_oauth as _oauth
from mc.core import _log

LEGACY_FLAG = 'oauth_legacy_bound'


def needs_oauth(acc: dict) -> bool:
    return (acc.get('platform') == 'x' and acc.get('capability') == 'direct'
            and not acc.get('preview'))


def _credentials(account_id: str, legacy: bool) -> dict:
    arg = None if legacy else account_id
    return {'oauth_vault': _oauth.vault_name('x', arg), 'oauth_profile': _oauth.profile_name('x', arg)}


def _legacy_holder(data: dict, accounts: dict):
    """The id holding the legacy names, or None. A flag written as `True` (before it
    named the holder) is resolved from the account that carries those names."""
    flag = data.get(LEGACY_FLAG)
    if flag is True:
        legacy = _oauth.vault_name('x')
        flag = next((a.get('id') for a in accounts.values()
                     if isinstance(a, dict) and (a.get('credentials') or {}).get('oauth_vault') == legacy), True)
        data[LEGACY_FLAG] = flag
    return flag or None


def bind(data: dict) -> None:
    """Give every unbound X account its reference (see module doc). Mutates `data`.
    Never raises: this runs inside every Desk store read, so an account whose id
    cannot be named is left unbound and logged, not allowed to take the Desk down."""
    accounts = data.get('accounts') or {}
    todo = sorted((a for a in accounts.values()
                   if isinstance(a, dict) and needs_oauth(a) and not a.get('credentials')),
                  key=lambda a: (str(a.get('created_at') or ''), str(a.get('id') or '')))
    for acc in todo:
        try:
            holder = _legacy_holder(data, accounts)
            legacy = holder is None or holder == acc.get('id')
            acc['credentials'] = _credentials(acc['id'], legacy)
            if legacy:
                data[LEGACY_FLAG] = acc['id']
        except Exception as e:
            _log(f'[desk_account_refs] could not give account {str(acc.get("id"))[:40]!r} a sign-in name: {e}', flush=True)


def oauth_arg(acc: dict | None) -> str | None:
    """The `account_id` argument `desk_oauth` takes for this account: None for the
    legacy singleton (and for an account with no sign-in of its own to name),
    the account's id otherwise."""
    if not isinstance(acc, dict) or not needs_oauth(acc):
        return None
    vault = (acc.get('credentials') or {}).get('oauth_vault')
    if not vault or vault == _oauth.vault_name('x'):
        return None
    return acc.get('id')


def require_oauth_arg(account_id: str) -> str | None:
    """`oauth_arg` for an account a human is signing in or out of. Raises
    LookupError for an id that is not a Desk account with a sign-in: a route must
    not create a vault entry for a name nobody owns."""
    from mc import desk as _desk   # lazy: desk imports this module
    with _desk._store_lock:
        acc = (_desk._read_store().get('accounts') or {}).get(account_id) if isinstance(account_id, str) else None
    if not isinstance(acc, dict) or not needs_oauth(acc):
        raise LookupError('that is not an X account that can sign in')
    return oauth_arg(acc)


def oauth_arg_for(account_id: str | None) -> str | None:
    """`oauth_arg` for a Desk account id (None, or one that is not there, reads as
    the legacy singleton: callers that never knew an account keep their behaviour)."""
    if not account_id:
        return None
    from mc import desk as _desk   # lazy: desk imports this module
    with _desk._store_lock:
        acc = (_desk._read_store().get('accounts') or {}).get(account_id)
    return oauth_arg(acc)


def planned_oauth_arg(account_id: str) -> str | None:
    """`oauth_arg` for an X account that does NOT exist yet and will be created with
    `account_id` (a sign-in held until the Save): None when the legacy names are still
    free, so it will be the one to get them, the id otherwise. The Save re-derives it from
    the account it really makes; this only picks the browser profile the sign-in is made in."""
    from mc import desk as _desk   # lazy: desk imports this module
    with _desk._store_lock:
        data = _desk._read_store()
        holder = _legacy_holder(data, data.get('accounts') or {})
    return None if holder is None else account_id


def release_legacy(account_id: str) -> None:
    """Hand the legacy sign-in names back when the account that was given them was just
    created and is being undone (a Save that failed part-way): nobody ever signed in under
    them, so they must not stay with a stranger who no longer exists."""
    from mc import desk as _desk   # lazy: desk imports this module
    with _desk._store_lock:
        data = _desk._read_store()
        if data.get(LEGACY_FLAG) == account_id and account_id not in (data.get('accounts') or {}):
            del data[LEGACY_FLAG]
            _desk._write_store(data)
