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
names have been handed out, once; deleting the account that holds them does not
free them for a stranger (the next account would inherit the previous person's
sign-in), the singleton stays in the vault until a human disconnects it.

`bind` is deterministic and idempotent: it runs on every store read
(`mc.desk._migrate_store`) and when an account is created, binding unbound
accounts oldest first. A record that already has `credentials` is never touched.
Only a direct, non-preview X account has a sign-in to refer to.
"""
from __future__ import annotations

from mc import desk_oauth as _oauth

LEGACY_FLAG = 'oauth_legacy_bound'


def needs_oauth(acc: dict) -> bool:
    return (acc.get('platform') == 'x' and acc.get('capability') == 'direct'
            and not acc.get('preview'))


def _credentials(account_id: str, legacy: bool) -> dict:
    arg = None if legacy else account_id
    return {'oauth_vault': _oauth.vault_name('x', arg), 'oauth_profile': _oauth.profile_name('x', arg)}


def bind(data: dict) -> None:
    """Give every unbound X account its reference (see module doc). Mutates `data`."""
    accounts = data.get('accounts') or {}
    todo = sorted((a for a in accounts.values()
                   if isinstance(a, dict) and needs_oauth(a) and not a.get('credentials')),
                  key=lambda a: (a.get('created_at') or '', a.get('id') or ''))
    for acc in todo:
        legacy = not data.get(LEGACY_FLAG)
        acc['credentials'] = _credentials(acc['id'], legacy)
        if legacy:
            data[LEGACY_FLAG] = True


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
