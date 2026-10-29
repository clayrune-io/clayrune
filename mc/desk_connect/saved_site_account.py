"""Explicit, atomic attachment of an old browser-site reference to an account.

Only metadata is read. No browser/profile is opened and no credential is used.
The source stays in services as a reference, linked to the account after Save.
"""
from __future__ import annotations

from mc import desk, desk_accounts as accounts, desk_account_refs
from mc.core import now_iso
from mc.desk_connect import registry, permission_policy, signin_method
from mc.desk_connect.browser_setup import SetupError
from mc.desk_connect.pane_account import handle_of
from mc.desk_pane_pages import PANE_ONLY_PLATFORMS
from urllib.parse import urlsplit


def _source(store: dict, service_id: str) -> tuple[dict, str]:
    rec = store.get('services', {}).get(service_id)
    if not rec or not isinstance(rec.get('browser_connection'), dict):
        raise SetupError('saved browser site not found', 404, 'not_found')
    profile = registry.lookup(urlsplit(rec.get('link', '')).hostname or '')
    if not profile or profile['id'] not in PANE_ONLY_PLATFORMS:
        raise SetupError('this saved site has no account connection yet', 400, 'unsupported_site')
    return rec, profile['id']


def _target(store: dict, source: dict, platform: str, raw) -> tuple[dict | None, str]:
    if not isinstance(raw, dict) or set(raw) not in ({'id'}, {'identity'}):
        raise SetupError('choose a saved account or enter its handle')
    if 'id' in raw:
        rec = store['accounts'].get(raw['id']) if isinstance(raw['id'], str) else None
        if not rec or rec.get('platform') != platform:
            raise SetupError('that account is not on this service', 400, 'wrong_account')
        identity = rec['identity']
    else:
        identity = accounts._clean_text(raw['identity'], 'identity', required=True)
        identity = handle_of(identity)
        rec = next((a for a in store['accounts'].values() if a.get('platform') == platform
                    and a.get('identity', '').casefold() == identity.casefold()), None)
    if rec and (rec.get('browser_profile') != source['browser_connection']['browser_profile']
                or signin_method.stored(rec) != signin_method.stored(source['browser_connection'])):
        raise SetupError('this account uses different sign-in details; edit that account first', 409, 'already_set_up')
    attached = source.get('account_id')
    if attached in store['accounts'] and (not rec or attached != rec['id']):
        raise SetupError('this site is already attached to another account', 409, 'already_attached')
    return rec, identity


def _check_source(rec: dict, expected) -> None:
    if expected is not None and expected != {'url': rec['link'],
            'browser_profile': rec['browser_connection']['browser_profile'],
            'signin_method': signin_method.stored(rec['browser_connection'])}:
        raise SetupError('the saved sign-in changed; close Edit and open it again', 409, 'source_changed')


def validate(service_id: str, raw, expected=None) -> None:
    with desk._store_lock:
        store = desk._read_store()
        source, platform = _source(store, service_id)
        _check_source(source, expected)
        _target(store, source, platform, raw)


def attach(service_id: str, raw, expected=None) -> dict:
    with desk._store_lock:
        store = desk._read_store()
        source, platform = _source(store, service_id)
        _check_source(source, expected)
        rec, identity = _target(store, source, platform, raw)
        unchanged = rec is not None and source.get('account_id') == rec['id']
        if rec is None:
            rec = {'id': desk._new_id('acct'), 'platform': platform, 'identity': identity,
                   'label': f'{accounts._LABEL_PREFIX[platform]}{identity}', 'voice': '',
                   'capability': 'assisted' if platform in accounts.assisted_platforms() else 'none',
                   'browser_profile': source['browser_connection']['browser_profile'],
                   'signin_method': signin_method.stored(source['browser_connection']), 'created_at': now_iso()}
            profile = registry.profile(platform)
            if profile and any(k['id'] == 'account' for k in profile['account_kinds']):
                permission_policy.initialize_new_account(rec, 'account')
            store['accounts'][rec['id']] = rec
            desk_account_refs.bind(store, new_account_id=rec['id'])
        source['account_id'] = rec['id']
        desk._write_store(store)
        return {'account_id': rec['id'], 'unchanged': unchanged}
