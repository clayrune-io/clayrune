"""A pane-only site (Instagram, YouTube, TikTok) connected from the Connect wizard becomes an ACCOUNT.

Before this, the generic browser-profile setup saved every site but X and LinkedIn as a
`services` record. A service is a reference: Where's tray, the campaign plan and the assisted
post button read ACCOUNTS only, so a connected Instagram never reached them (2026-10-16: a
test campaign's tray listed three accounts and no Instagram).

`browser_connections.commit` hands such a draft here. The draft shape is unchanged
(`name`, `url`, `browser_profile`); for these sites `name` is the account's handle. The account
gets the platform's own default capability from `desk_accounts.create_account` (Instagram
`assisted`, YouTube/TikTok `none`) and the typed browser profile. Nothing else is granted: no
read setting is changed (absent means the browser pane) and no post is made or approved.
"""
from __future__ import annotations

from mc import desk, desk_accounts as _accounts
from mc.desk_connect.browser_check import status_for
from mc.desk_connect.browser_setup import SetupError, _profile_state
from mc.desk_pane_pages import PANE_ONLY_PLATFORMS
from mc.desk_connect import signin_method


def platform_of(clean: dict) -> str | None:
    """The account platform this draft creates, or None when it stays a saved service."""
    svc = clean.get('service')
    return svc if svc in PANE_ONLY_PLATFORMS else None


def handle_of(name: str) -> str:
    """The handle as accounts store it: no leading @, never empty."""
    handle = str(name or '').strip().lstrip('@').strip()
    if not handle:
        raise SetupError('enter the account handle', 400, 'handle_required')
    return handle


def view(rec: dict, url: str, unchanged: bool) -> dict:
    v1 = _accounts.v1_account(rec)
    profile = v1.get('browser_profile') or ''
    return {'connection_id': rec['id'], 'account_id': rec['id'], 'kind': 'account', 'name': v1['label'],
            'url': url, 'service': rec['platform'], 'capability': v1['capability'],
            'browser_profile': profile, 'profile_state': _profile_state(profile),
            'signin_method': signin_method.stored(rec),
            'read': False, 'post': False, 'executes': False,
            'status': status_for(url, profile), 'unchanged': unchanged}


def save(clean: dict) -> dict:
    """Create the account (or report the one already there). Raises SetupError."""
    platform = platform_of(clean)
    if platform is None:
        raise SetupError('this site is saved as a service, not an account', 400, 'not_pane_account')
    handle = handle_of(clean['name'])
    with desk._store_lock:
        held = next((a for a in desk._read_store()['accounts'].values()
                     if a.get('platform') == platform and (a.get('identity') or '').lower() == handle.lower()), None)
    if held is not None:
        if (held.get('browser_profile') or '') != clean['browser_profile']:
            raise SetupError(f'{handle} on {platform} is already an account with a different browser profile; '
                             'change it on that account, it is not replaced here', 409, 'already_set_up')
        if signin_method.stored(held) != clean.get('signin_method', 'other'):
            raise SetupError(f'{handle} on {platform} is already an account with a different sign-in method saved; '
                             'change it on that account, it is not replaced here', 409, 'already_set_up')
        return view(held, clean['url'], True)
    try:
        created = _accounts.create_account(platform, handle)
        if created.get('browser_profile') != clean['browser_profile']:
            created = _accounts.update_account(created['id'], {'browser_profile': clean['browser_profile']})
    except _accounts.AccountError as e:
        raise SetupError(str(e), getattr(e, 'status', 400)) from e
    with desk._store_lock:
        store = desk._read_store()
        rec = store['accounts'][created['id']]
        rec['signin_method'] = clean.get('signin_method', 'other')
        desk._write_store(store)
    return view(rec, clean['url'], False)
