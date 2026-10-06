"""Save a browser connection without a developer app (docs/desk_v1/CONNECT_FLOW_SIMPLIFY.md section 7,
"Browser setup"; MC-1062 ticket 03).

One Save makes, or reuses, an X or LinkedIn account and binds to it a NAMED BROWSER PROFILE and,
optionally, the NAME of a stored login (`browser_setup_record` is the shape). That is all it does:

  * it grants no permission. Nothing is added to `connections`, `read_via` or the account's legacy
    `browser_profile`, so no agent read or post starts, the Desk reads the account exactly as it did
    before, and a later permission edit has nothing to undo here. Zero permissions is the normal result.
  * it creates no browser profile and starts no sign-in. A profile that does not exist yet is
    reported as `new`: the person signs in to it afterwards, through the existing pane.
  * it mints no OAuth token, makes no network call, fakes no probe and never starts `start-held`
    (that route is OAuth; a cookie sign-in is not).
  * the only vault write is a login the person typed on this Save (`new_login`): created first with
    the vault's create-only guard, removed again if the account write that follows fails, never
    replacing an existing entry. The value is held in the request that carries it; it is not in a
    fingerprint, a remembered result, a log line or an error.

The route is the fixed human path of `desk_connect_purpose_routes`: refuse an unattended caller,
validate the draft (a bad draft costs no passcode guess), check the dashboard passcode once, write.

Identity rules:

  * X: one account per handle, and a profile or login belongs to ONE X account (two identities in one
    profile would be one x.com session). The handle is the identity; a second account is a second id.
  * LinkedIn: the member (`member`) and the Company Page (`organization`) are different accounts and
    never stand in for each other. The kind is recorded, never inferred from the login: a Page is
    reached through a member's login, so a Page may share a profile and login with a member or another
    Page (the result names who, `shared_with`), but two members never share one. An organization id is
    only accepted on a NEW Page; it is not inferred from the member.
  * An existing account is never changed: a setup already saved is answered `unchanged` when the draft
    is identical and 409 `already_set_up` when it is not; an account whose sign-in route is already
    bound through purposes is 409 `already_bound`. Nothing here replaces a saved profile or login.

A repeated `request_id` with the same draft answers the first result without writing; the same id with
a different draft is 409. A failed Save is not remembered.

Attaching a later OAuth connection to an account saved here is defined in `account_attach`.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict

from mc import desk as _desk
from mc import desk_account_refs as _refs
from mc import desk_accounts as _accounts
from mc.core import _log, now_iso
from mc.desk_connect import browser_setup_record as _record
from mc.desk_connect import commit as _commit
from mc.desk_connect import purpose_bindings as _bindings
from mc.desk_connect import registry as _registry
from mc.desk_connect import signin_fill as _fill
from mc.desk_connect import signin_login_store as _logins

SERVICES = ('x', 'linkedin')              # the platforms `desk_accounts.create_account` can make a read/post account for
_X_HANDLE = re.compile(r'^[A-Za-z0-9_]{1,15}$')
_REMEMBER = 200
_PROFILE_MAX = 40


class SetupError(ValueError):
    """A refusal with the HTTP status and a short machine `code` for the route."""

    def __init__(self, message: str, status: int = 400, code: str = 'invalid'):
        super().__init__(message)
        self.status = status
        self.code = code


_lock = threading.Lock()
_done: 'OrderedDict[str, tuple[str, dict]]' = OrderedDict()


def _forget_all_for_tests() -> None:
    with _lock:
        _done.clear()


def _need(cond, message: str, status: int = 400, code: str = 'invalid') -> None:
    if not cond:
        raise SetupError(message, status, code)


# -- the draft --------------------------------------------------------------------

def _signin_route(profile: dict, route_id) -> dict:
    """The profile's browser route that declares a sign-in page and takes a browser sign-in, else SetupError."""
    route = next((r for r in profile['routes'] if r['id'] == route_id), None) if isinstance(route_id, str) else None
    _need(route is not None, 'that service has no such route', 404, 'unknown_route')
    assert route is not None
    _need(route['transport'] == 'browser' and route.get('signin') and any(a['type'] == 'browser_signin' for a in route['auth']),
          f'{route["title"]} declares no sign-in page, so a browser connection cannot be saved for it', 400, 'not_a_signin_route')
    return route


def _clean_identity(service: str, raw) -> str:
    try:
        identity = _accounts._clean_text(raw, 'name' if service == 'linkedin' else 'handle', required=True)
    except _accounts.AccountError as e:
        raise SetupError(str(e), e.status) from e
    if service == 'x':
        identity = identity.lstrip('@')
        _need(bool(_X_HANDLE.match(identity)), 'an X handle is 1-15 letters, digits or _ (for example @yourname)')
    return identity


def _clean_account(raw, service: str, kind: str) -> dict:
    _need(isinstance(raw, dict), 'account must be an object')
    unknown = sorted(set(raw) - {'id', 'new'})
    _need(not unknown, f'unknown account field(s): {", ".join(unknown)}')
    _need(('id' in raw) != ('new' in raw), 'name either an existing account (id) or a new one (new)')
    if 'id' in raw:
        aid = raw['id']
        _need(isinstance(aid, str) and _bindings._ACCOUNT_ID.match(aid), 'that account id is not valid', 400, 'bad_account')
        rec = _stored_account(aid)
        if rec is None:
            raise SetupError('account not found', 404, 'account_not_found')
        _need(rec.get('platform') == service, f'that account is on {rec.get("platform")}, not {service}', 400, 'wrong_platform')
        _need(not (service == 'linkedin' and kind == 'member' and rec.get('organization_id')),
              'that account has a Company Page id, so it is not a member profile', 400, 'kind_mismatch')
        return {'id': aid}
    new = raw['new']
    _need(isinstance(new, dict), 'new account must be an object')
    unknown = sorted(set(new) - {'identity', 'label', 'organization_id'})
    _need(not unknown, f'unknown new-account field(s): {", ".join(unknown)}')
    identity = _clean_identity(service, new.get('identity'))
    try:
        label = _accounts._clean_text(new.get('label'), 'label')
    except _accounts.AccountError as e:
        raise SetupError(str(e), e.status) from e
    org = new.get('organization_id')
    if org not in (None, ''):
        _need(service == 'linkedin' and kind == 'organization', 'an organization id belongs to a LinkedIn Company Page')
        _need(isinstance(org, str) and bool(_accounts._ORG_ID.match(org.strip())),
              'organization_id is digits only (the number in the Company Page admin URL)')
        org = org.strip()
    else:
        org = ''
    with _desk._store_lock:
        others = list((_desk._read_store().get('accounts') or {}).values())
    _need(not any(o.get('platform') == service and (o.get('identity') or '').lower() == identity.lower() for o in others),
          f'{identity} on {service} is already an account: pick it instead', 409, 'account_exists')
    return {'new': {'identity': identity, 'label': label, 'organization_id': org}}


def _stored_account(account_id: str) -> dict | None:
    with _desk._store_lock:
        return (_desk._read_store().get('accounts') or {}).get(account_id)


def _slug(text: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def _derived_profile(service: str, identity: str) -> str:
    """The profile name a Save uses when the person names none: `<service>-<identity>`, lowercased."""
    name = f'{service}-{_slug(identity)}'[:_PROFILE_MAX].rstrip('-_')
    return name if _bindings._PROFILE_NAME.match(name) else f'{service}-account'


def _clean_profile(raw, service: str, identity: str) -> str:
    if raw is None:
        return _derived_profile(service, identity)
    _need(isinstance(raw, str) and bool(_bindings._PROFILE_NAME.match(raw.strip().lower())),
          'a browser profile name is lowercase letters, digits, - or _ (at most 41 characters)')
    return raw.strip().lower()


def _clean_login(draft: dict) -> tuple[str | None, dict | None]:
    """`(stored login name, typed login)`: at most one. A stored login must be a global-scope login entry
    with a username (what the fill engine can use); a typed one is validated and its name must be free."""
    _need(not ('login' in draft and 'new_login' in draft), 'pick a stored login or type a new one, not both')
    if draft.get('login') is not None:
        name = draft['login']
        try:
            meta = _fill.login_entry(name, None)
        except _fill.FillError as e:
            raise SetupError(str(e), e.status, e.code) from e
        _need(meta.get('scope', 'global') == 'global', f'{name} is not a workspace login: the Desk uses global logins only', 400, 'login_not_global')
        return name.strip(), None
    if draft.get('new_login') is not None:
        try:
            new = _logins.clean(draft['new_login'])
            _logins.check_free(new)
        except _logins.LoginError as e:
            raise SetupError(str(e), e.status, e.code) from e
        return new['name'], new
    return None, None


def clean_draft(draft) -> dict:
    """Validate a draft into the exact shape that is saved. Reads the profile, the Desk accounts and the
    vault's NAMES; writes nothing. Raises SetupError."""
    _need(isinstance(draft, dict), 'draft must be an object')
    unknown = sorted(set(draft) - {'service', 'revision', 'route_id', 'account', 'account_kind', 'browser_profile', 'login', 'new_login'})
    _need(not unknown, f'unknown draft field(s): {", ".join(unknown)}')
    service = draft.get('service')
    _need(service in SERVICES, f'a browser connection can be saved for {", ".join(SERVICES)}', 400, 'unknown_service')
    profile = _registry.profile(service)
    _need(profile is not None, 'that service has no profile', 400, 'unknown_service')
    assert profile is not None
    _need(draft.get('revision') == profile['revision'], 'The service profile changed since you opened it. Open it again and review.',
          409, 'profile_changed')
    route = _signin_route(profile, draft.get('route_id'))
    kind = draft.get('account_kind')
    _need(isinstance(kind, str) and kind in {k['id'] for k in profile['account_kinds']},
          f'account_kind must be one of {[k["id"] for k in profile["account_kinds"]]}')
    account = _clean_account(draft.get('account'), service, kind)
    rec = _stored_account(account['id']) if 'id' in account else None
    identity = (rec or {}).get('identity') if rec else account['new']['identity']
    refs = {'browser_profile': _clean_profile(draft.get('browser_profile'), service, identity or '')}
    login, typed = _clean_login(draft)
    if login:
        refs['login'] = login
    if rec is not None:
        _check_existing(rec, route['id'], kind, refs)
    _check_conflicts(_all_accounts(), account.get('id'), service, kind, refs)
    out = {'service': service, 'revision': profile['revision'], 'route_id': route['id'], 'account': account,
           'account_kind': kind, 'refs': refs}
    if typed is not None:
        out['new_login'] = typed
    return out


def _all_accounts() -> dict:
    with _desk._store_lock:
        return dict(_desk._read_store().get('accounts') or {})


def _draft_fingerprint(raw) -> str | None:
    """Over the draft as SENT, without any typed password: a typed login counts by its name and username
    only. Taken before validation, because validation reads live state (an account that now exists, a
    login name now taken) and a replay of a saved request must not fail on what that request itself wrote."""
    if not isinstance(raw, dict):
        return None
    view = dict(raw)
    typed = view.get('new_login')
    if isinstance(typed, dict):
        view['new_login'] = {k: v for k, v in typed.items() if k != 'value'}
    try:
        return hashlib.sha256(json.dumps(view, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    except (TypeError, ValueError):
        return None


# -- rules about an existing account ----------------------------------------------

def _check_existing(rec: dict, route_id: str, kind: str, refs: dict) -> bool:
    """True when the account already holds exactly this setup (nothing to write). Raises SetupError for
    anything that would change or replace what the account already holds."""
    conns = rec.get('connections')
    other = {b.get('account_kind') for caps in (conns.values() if isinstance(conns, dict) else [])
             for b in (caps.values() if isinstance(caps, dict) else []) if isinstance(b, dict)} - {None, kind}
    _need(not other, 'this account already has routes chosen for a different kind of account. One account is one kind.',
          409, 'account_kind_changed')
    _need(route_id not in _record.bound_routes(rec),
          'this account already has this sign-in saved with its permissions. Change it there; a setup here would be a second copy.',
          409, 'already_bound')
    setup = _record.of(rec)
    if setup is None:
        return False
    same = setup['route_id'] == route_id and setup.get('account_kind') == kind and setup['refs'] == refs
    _need(same, 'this account already has a browser connection saved. Clayrune does not replace its profile or login from here.',
          409, 'already_set_up')
    return True


def _check_conflicts(accounts: dict, account_id: str | None, service: str, kind: str, refs: dict) -> list:
    """Refuse a profile or login another account on the same site already holds, except where a LinkedIn
    Page is reached through a member's login. Returns the ids this setup shares a profile or login with."""
    shared: list = []
    for oid, o in accounts.items():
        if oid == account_id or o.get('platform') != service:
            continue
        held = _record.held_refs(o)
        same_profile = refs['browser_profile'] in held['profiles']
        same_login = bool(refs.get('login')) and refs['login'] in held['logins']
        if not (same_profile or same_login):
            continue
        other_kind = _record.kind_of(o) or ('organization' if o.get('organization_id') else None)
        if service == 'linkedin' and (kind == 'organization' or other_kind == 'organization'):
            shared.append(oid)
            continue
        what, code = (('browser profile', 'profile_in_use') if same_profile else ('login', 'login_in_use'))
        value = refs['browser_profile'] if same_profile else refs['login']
        raise SetupError(f'the {what} "{value}" already belongs to {o.get("label") or o.get("identity") or oid}. '
                         f'Each {service} account needs its own: choose another.', 409, code)
    return sorted(set(shared))


# -- the write --------------------------------------------------------------------

def _profile_state(name: str) -> str:
    """`exists` (a saved browser profile of that name is on disk), `new` (none yet: the person signs in to
    it afterwards) or `unknown` (it could not be looked up). Reads a directory name; creates nothing."""
    try:
        import os

        from mc.blueprints import browser_routes as _browser
        path = _browser._profile_dir(name)
        return 'unknown' if path is None else ('exists' if os.path.isdir(path) else 'new')
    except Exception as e:
        _log(f'[desk_connect] browser setup could not look up profile {name!r}: {type(e).__name__}', flush=True)
        return 'unknown'


def _result(account_id: str, created: bool, clean: dict, shared: list, unchanged: bool, login_created: bool) -> dict:
    refs = clean['refs']
    return {'account_id': account_id, 'account_created': created, 'unchanged': unchanged, 'service': clean['service'],
            'route_id': clean['route_id'], 'account_kind': clean['account_kind'], 'browser_profile': refs['browser_profile'],
            'profile_state': _profile_state(refs['browser_profile']), 'login': refs.get('login'),
            'login_created': login_created, 'shared_with': shared}


def _apply(store: dict, account_id: str, clean: dict, org_id: str) -> tuple[list, bool]:
    """Mutate `store` (the caller holds the lock and writes). Returns `(shared_with, unchanged)`."""
    rec = (store.get('accounts') or {}).get(account_id)
    if rec is None:
        raise SetupError('account not found', 404, 'account_not_found')
    if rec.get('platform') != clean['service']:
        raise SetupError(f'that account is on {rec.get("platform")}, not {clean["service"]}', 400, 'wrong_platform')
    if clean['service'] == 'linkedin' and clean['account_kind'] == 'member' and rec.get('organization_id'):
        raise SetupError('that account has a Company Page id, so it is not a member profile', 400, 'kind_mismatch')
    unchanged = _check_existing(rec, clean['route_id'], clean['account_kind'], clean['refs'])
    shared = _check_conflicts(store.get('accounts') or {}, account_id, clean['service'], clean['account_kind'], clean['refs'])
    if unchanged:
        return shared, True
    if org_id:
        rec['organization_id'] = org_id
    rec[_record.FIELD] = _record.build(clean['service'], clean['route_id'], clean['account_kind'], clean['refs'], now_iso())
    return shared, False


def _write_setup(clean: dict, login_created: bool) -> dict:
    created = None
    account_id = clean['account'].get('id')
    org_id = ''
    if account_id is None:
        new = clean['account']['new']
        org_id = new['organization_id']
        try:
            created = _accounts.create_account(clean['service'], new['identity'], label=new['label'] or None)
        except _accounts.AccountError as e:
            raise SetupError(str(e), e.status, 'account_refused') from e
        account_id = created['id']
    try:
        with _desk._store_lock:
            store = _desk._read_store()
            shared, unchanged = _apply(store, account_id, clean, org_id)
            if not unchanged:
                _desk._write_store(store)
    except Exception as e:
        orphan = _remove_account(account_id) if created is not None else False
        if isinstance(e, SetupError) and not orphan:
            raise
        _log(f'[desk_connect] browser setup write failed ({type(e).__name__})', flush=True)
        msg = str(e) if isinstance(e, SetupError) else 'the browser connection could not be saved; see the server log'
        if orphan:
            msg += f'; the new account {account_id} was made and could not be removed: delete it in Connections'
        raise SetupError(msg, e.status if isinstance(e, SetupError) else 500, 'record_failed') from e
    return _result(account_id, created is not None, clean, shared, unchanged, login_created)


def _remove_account(account_id: str) -> bool:
    """Take back the account this request made. True when it could NOT be removed (an orphan is left)."""
    try:
        gone = _accounts.delete_account(account_id)
        _refs.release_legacy(account_id)
        return not gone
    except Exception as e:
        _log(f'[desk_connect] could not remove the account made for a failed browser setup: {type(e).__name__}', flush=True)
        return True


def replayed(request_id: str, raw_draft) -> dict | None:
    """The saved result when this request_id was already committed with this same draft; None when it was
    not; SetupError 409 when the id was used for a different draft. Writes nothing."""
    fp = _draft_fingerprint(raw_draft)
    with _lock:
        seen = _done.get(request_id)
    if seen is None:
        return None
    if fp is not None and seen[0] == fp:
        return seen[1]
    raise SetupError('that request_id was already used for a different draft', 409, 'request_id_reused')


def commit(request_id: str, raw_draft, clean: dict) -> tuple[dict, bool]:
    """Write the draft. Returns `(result, duplicate)`. Raises SetupError. `raw_draft` is the draft as sent
    (for the replay check); `clean` is `clean_draft(raw_draft)`."""
    fp = _draft_fingerprint(raw_draft)
    with _lock:
        seen = _done.get(request_id)
        if seen is not None:
            if fp is not None and seen[0] == fp:
                return seen[1], True
            raise SetupError('that request_id was already used for a different draft', 409, 'request_id_reused')
        typed = clean.get('new_login')
        if typed is not None:
            try:
                _logins.write(typed)
            except _logins.LoginError as e:
                raise SetupError(str(e), e.status, e.code) from e
        try:
            result = _write_setup(clean, typed is not None)
        except Exception as e:
            if typed is not None and not _logins.remove(typed['name']):
                if isinstance(e, SetupError):
                    raise SetupError(f'{e}; the login "{typed["name"]}" was stored and could not be removed: delete it in Secrets',
                                     e.status, e.code) from e
            raise
        _done[request_id] = (fp or '', result)
        while len(_done) > _REMEMBER:
            _done.popitem(last=False)
    return result, False


clean_request_id = _commit.clean_request_id
