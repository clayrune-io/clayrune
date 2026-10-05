"""Sign in with a saved login (docs/DESK_SERVICE_PROFILES_SPEC.md, "P2b"; Ron 2026-10-05).

A sign-in route that declares a page (`route['signin']`: its exact https hosts) can be signed
in with ONE vault login entry (username and password on the same entry): the server types it
into the sign-in page in the browser pane, so the value never reaches the browser's own
client, a response, a log, a draft or a model.

What makes that safe, in the order the checks run (`fill`):

    human start    `unattended=True` is refused before anything else; the route refuses an
                   unattended caller and then asks for the dashboard passcode, the one proof
                   an agent cannot forge. Nothing here is reachable from a timer or a token
                   expiring: re-signing-in with the same login is the same human click.
    the login      a vault entry that exists, is not a Clayrune-internal sign-in, and has a
                   username; for an account's route it is the login saved for that account
                   and route (`bound_refs`), never a name from the request. Its scope and
                   `allow_unattended` are enforced by the vault itself (`get_secret_value`,
                   given the route's real caller check). A locked vault is reported as locked (423);
                   the value is never asked for any other way.
    the pane       the running pane holding the named browser profile.
    the origin     read FROM THE BROWSER, never from the page: the top frame's committed URL
                   over CDP (`signin_fill_cdp.PageLink.top`). The vault is not touched unless
                   it is exactly one of the route's declared https hosts, and it is read again
                   immediately before the typing. The script that types runs in an isolated
                   world, so a page's own JavaScript (overridden prototypes, listeners) can
                   neither fake the answer nor see the value, and it re-checks the origin and
                   the form's destination in the same evaluation as the typing
                   (`signin_fill_js`): a redirect between the checks types nothing.
    the person     a CAPTCHA or second-factor prompt on the page ends the run: nothing is
                   typed, the pane is the person's (`handoff`).

The username is typed on a username-only page, the password only on a page that shows a
password field (it is not even fetched from the vault before then) and at most ONCE per run,
so a wrong password is never retried into a lockout.

Nothing returned or logged carries the username or the password: results are state words.
Every result is checked against both before it leaves (`_clean`), and the dispensed password
is registered with the vault's redactor by the vault itself.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from mc import desk as _desk
from mc import secrets_store as _vault
from mc.core import _log
from mc.desk_connect import registry as _registry
from mc.desk_connect import signin_fill_cdp as _cdp
from mc.desk_connect import signin_fill_js as _js

CONSUMER = 'desk-signin-fill'
SETTLE_S = 2.5                       # after a submit, before the next look at the page
MAX_STEPS = 3                        # username page, password page, one look at the result


class FillError(Exception):
    """A refusal with the HTTP status and a short machine `code`."""

    def __init__(self, message: str, status: int = 400, code: str = 'invalid'):
        super().__init__(message)
        self.status = status
        self.code = code


class Pane:
    """The browser pane, as `fill` needs it: `find` the session holding a profile, `open` one link
    to its page. The link's `top()` is the browser's own reading of the top frame's URL;
    `evaluate(expression)` runs one expression in an isolated world and returns `(ok, value)`
    (it is the only thing that is given a secret); `close()` ends it."""

    def find(self, profile: str) -> Any:
        from mc.blueprints import browser_routes as _b
        return _b._session_using_profile(profile)

    def open(self, session: Any) -> Any:
        return _cdp.connect(session)


def route_signin(service: str, route_id: str) -> tuple[dict, dict]:
    """`(profile, signin)` for a route that declares a sign-in page, else FillError."""
    profile = _registry.profile(service) if isinstance(service, str) else None
    if profile is None:
        raise FillError('that service has no profile', 404, 'unknown_service')
    route = next((r for r in profile['routes'] if r['id'] == route_id), None) if isinstance(route_id, str) else None
    if route is None:
        raise FillError('that service has no such route', 404, 'unknown_route')
    signin = route.get('signin')
    if not signin:
        raise FillError(f'{route["title"]} declares no sign-in page, so Clayrune will not type a login for it', 400, 'no_signin_declared')
    return route, signin


def options(service: str, project_id: str | None = None) -> dict:
    """What the sign-in panel needs, value-free: the service's sign-in routes, the stored logins
    that could be used (names only), and whether the vault is locked. Raises FillError."""
    profile = _registry.profile(service) if isinstance(service, str) else None
    if profile is None:
        raise FillError('that service has no profile', 404, 'unknown_service')
    routes = [{'route_id': r['id'], 'title': r['title'], 'connect_method': r.get('connect_method'),
               'url': r['signin']['url'], 'hosts': r['signin']['hosts']} for r in profile['routes'] if r.get('signin')]
    try:
        rows = _vault.list_secrets(project_id, check_readable=False)
    except _vault.SecretsError as e:
        raise FillError(f'the vault could not be read: {_oneline(e)}', 503, 'vault_unavailable') from e
    logins = [{'name': s['name'], 'matches': s['name'].split('.')[0].lower() == service}
              for s in rows if s.get('entry_type') == _vault.ENTRY_LOGIN and s.get('kind') != _vault.KIND_TOTP
              and s.get('username') and s.get('scope', 'global') == 'global' and not _vault.is_server_internal(s['name'])]
    return {'service': service, 'routes': routes, 'logins': sorted(logins, key=lambda x: (not x['matches'], x['name'])),
            'vault_locked': _vault.is_locked()}


def bound_refs(account_id: Any, service: str, route_id: str) -> dict:
    """The non-secret refs an account's saved Save holds for this route (`login`, `browser_profile`,
    `oauth_profile`), merged over its capabilities. Raises FillError when the account is not bound to it."""
    if not isinstance(account_id, str):
        raise FillError('that account id is not valid', 400, 'bad_account')
    with _desk._store_lock:
        rec = (_desk._read_store().get('accounts') or {}).get(account_id)
    if not isinstance(rec, dict) or rec.get('platform') != service:
        raise FillError('account not found', 404, 'account_not_found')
    refs: dict = {}
    for caps in (rec.get('connections') or {}).values():
        for c in (caps or {}).values():
            if isinstance(c, dict) and c.get('route_id') == route_id and isinstance(c.get('refs'), dict):
                refs.update({k: v for k, v in c['refs'].items() if isinstance(v, str)})
    if not refs.get('login'):
        raise FillError('that account has no saved login for this route: choose one and Save first', 409, 'login_not_bound')
    return refs


def pick_login(service: str, route_id: str, account_id: Any, login: Any, profile: Any) -> tuple[str, str]:
    """`(login name, browser profile)` for a fill, from what the account SAVED, not from the request.

    With an `account_id` the login is the one bound to that account and route (`bound_refs`); a different
    name in the request is refused, so a request cannot type another stored login (a bank's, say) into
    this service's page. The pane profile is the bound one when there is one.

    Without an account (the sign-in step of a provider connect, which has none) the login is the
    request's pick, and must be in this service's own namespace (`<service>.<name>`, the same
    rule the picker already uses to put the service's logins first). Raises FillError."""
    route_signin(service, route_id)
    if account_id is not None:
        refs = bound_refs(account_id, service, route_id)
        if login is not None and login != refs['login']:
            raise FillError('that is not the login saved for this account and route', 409, 'login_not_bound')
        return refs['login'], str(refs.get('browser_profile') or refs.get('oauth_profile') or profile or '')
    name = login.strip() if isinstance(login, str) else ''
    if not name:
        raise FillError('name the saved login to use', 400, 'bad_login')
    if not name.lower().startswith(service + '.'):
        raise FillError(f'{name} is not one of {service}\'s logins (its name starts with "{service}.")', 409, 'login_not_for_service')
    return name, profile if isinstance(profile, str) else ''


def origins_of(signin: dict) -> list[str]:
    return [f'https://{h}' for h in signin['hosts']]


def login_entry(name: Any, project_id: str | None) -> dict:
    """The vault entry's METADATA, after the checks that need no secret. Raises FillError."""
    if not isinstance(name, str) or not _vault.valid_name(name.strip()):
        raise FillError('name the saved login to use', 400, 'bad_login')
    name = name.strip()
    if _vault.is_server_internal(name):
        raise FillError(f'{name} is kept by Clayrune for its own sign-ins', 400, 'bad_login')
    try:
        rows = _vault.list_secrets(project_id)
    except _vault.SecretsError as e:
        raise FillError(f'the vault could not be read: {_oneline(e)}', 503, 'vault_unavailable') from e
    rec = next((s for s in rows if s['name'] == name), None)
    if rec is None:
        raise FillError(f'there is no saved login named {name}', 404, 'login_not_found')
    if rec.get('kind') == _vault.KIND_TOTP or rec.get('entry_type') != _vault.ENTRY_LOGIN:
        raise FillError(f'{name} is not a login (it has no username and password on one entry)', 400, 'not_a_login')
    if not rec.get('username'):
        raise FillError(f'{name} has no username stored: add one in Secrets, or store a new login', 409, 'no_username')
    return rec


def _oneline(e: Exception) -> str:
    return _vault.redact(str(e)).replace('\n', ' ')[:200]


def _declared(top: Any, origins: list[str]) -> bool:
    """True when the BROWSER's own reading of the top frame (`PageLink.top()`) is exactly one of the
    declared origins: https, the exact host, the default port, no credentials in the URL, a loaded
    page (not an error page), and the browser's security origin agrees."""
    if not isinstance(top, dict) or top.get('unreachable'):
        return False
    try:
        parts = urlsplit(str(top.get('url') or ''))
        host, port = parts.hostname, parts.port
    except ValueError:
        return False
    if parts.scheme != 'https' or not host or port is not None or parts.username or parts.password:
        return False
    origin = f'https://{host}'
    return origin in origins and top.get('origin') == origin


def _where(page: Any, origins: list[str]) -> tuple[bool, str]:
    """`(declared, host)` from the browser, never from the page. Raises FillError when the pane does
    not answer."""
    try:
        top = page.top()
    except _cdp.LinkError as e:
        _log(f'[desk_signin] reading the pane failed: {str(e).split(":", 1)[0][:40]}', flush=True)
        raise FillError('the browser pane did not answer; open the sign-in page in the pane and try again', 502, 'pane_error') from None
    if _declared(top, origins):
        return True, ''
    try:
        host = (urlsplit(str(top.get('url') or '')).hostname or '')[:80]
    except ValueError:
        host = ''
    return False, host


def _probe(page: Any, origins: list[str]) -> dict:
    """The browser-side origin check first (no secret is in play), then the page probe."""
    declared, host = _where(page, origins)
    if not declared:
        return {'state': 'origin_mismatch', 'host': host}
    ok, value = page.evaluate(_js.build(origins, fill=False))
    if not ok or not isinstance(value, dict) or not isinstance(value.get('state'), str):
        _log(f'[desk_signin] page probe failed: {str(value).split(":", 1)[0][:40]}', flush=True)
        raise FillError('the browser pane did not answer; open the sign-in page in the pane and try again', 502, 'pane_error')
    return value


def _clean(out: dict, secrets: tuple) -> dict:
    """The result, checked against the values that were typed. A hit means a bug: it is
    replaced by an error rather than returned."""
    text = json.dumps(out)
    # A short username would match ordinary words, so it is only scanned for from 6 characters.
    if any(len(s) >= 6 and s in text for s in secrets) or _vault.redact(text) != text:
        _log('[desk_signin] a result held a typed value: withheld', flush=True)
        return {'ok': False, 'state': 'error', 'code': 'internal', 'message': 'the result could not be shown'}
    return out


def _refuse(host: str) -> FillError:
    return FillError(f'the pane is on {host or "another page"}, which is not this route\'s declared sign-in page. '
                     f'Nothing was typed', 409, 'origin_mismatch')


def _refuse_action() -> FillError:
    return FillError('the sign-in form on this page sends what is typed to an address that is not this route\'s declared '
                     'sign-in page. Nothing was typed', 409, 'action_mismatch')


def fill(service: str, route_id: str, login: str, profile_name: str, *, project_id: str | None = None,
         unattended: bool = False, pane: Pane | None = None, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Type the saved login into the pane's sign-in page. Returns a result of state words:

        {'ok': True, 'state': 'submitted' | 'filled' | 'handoff' | 'no_form', ...}

    `handoff` carries `reason` (`captcha` | `two_factor`) and means the pane is the person's now.
    `unattended` is the caller's REAL answer (the route's `is_unattended_caller()`); it is refused when
    true and is what the vault is given, so a login stored `allow_unattended: false` is enforced there.
    Raises FillError for every refusal (nothing typed)."""
    if unattended:
        raise FillError('this action needs a human: an unattended agent session cannot sign a service in', 403, 'human_required')
    _route, signin = route_signin(service, route_id)
    origins = origins_of(signin)
    if not isinstance(profile_name, str) or not profile_name.strip():
        raise FillError('open the sign-in page in the browser pane first', 409, 'no_pane')
    entry = login_entry(login, project_id)
    if _vault.is_locked():
        raise FillError('the vault is locked: unlock it in Settings > Vault, then try again', 423, 'vault_locked')
    pane = pane or Pane()
    session = pane.find(profile_name.strip())
    if session is None or session.get('status') != 'running':
        raise FillError('the sign-in page is not open in the browser pane: open it, then try again', 409, 'no_pane')
    try:
        page = pane.open(session)
    except _cdp.LinkError as e:
        _log(f'[desk_signin] opening the pane failed: {str(e).split(":", 1)[0][:40]}', flush=True)
        raise FillError('the browser pane did not answer; open the sign-in page in the pane and try again', 502, 'pane_error') from None
    try:
        return _run(page, origins, entry, project_id, unattended, sleep)
    finally:
        page.close()


def _run(page: Any, origins: list[str], entry: dict, project_id: str | None, unattended: bool,
         sleep: Callable[[float], None]) -> dict:
    typed_user = typed_password = False
    secrets: tuple = ()
    last: dict = {}
    for _ in range(MAX_STEPS):
        seen = _probe(page, origins)
        state = seen['state']
        if state in ('origin_mismatch', 'not_top', 'action_mismatch'):
            if typed_user or typed_password:             # the page moved on after the sign-in (another host is normal): nothing is typed there
                return _clean({'ok': True, 'state': 'submitted', 'moved_on': True,
                               'message': 'The login was typed and submitted, and the page has moved on. Check the pane.'}, secrets)
            raise _refuse_action() if state == 'action_mismatch' else _refuse(str(seen.get('host') or ''))
        if state in ('captcha', 'two_factor'):
            return _clean({'ok': True, 'state': 'handoff', 'reason': state,
                           'message': 'The page asks for a check only you can do. Finish it in the pane; nothing more is typed.'}, secrets)
        if state == 'script_error':
            raise FillError('the sign-in page could not be read', 502, 'pane_error')
        if typed_password:                                   # the one look at the result after typing the password
            return _clean({'ok': True, 'state': 'submitted', 'still_on_sign_in': state in ('login_form', 'username_step'),
                           'message': 'The login was typed and submitted. Check the pane for the result.'}, secrets)
        if state == 'other':
            return _clean({'ok': True, 'state': 'no_form' if not typed_user else 'submitted',
                           'message': 'There is no sign-in form on this page.' if not typed_user
                           else 'The username was typed and submitted. Check the pane.'}, secrets)
        if state == 'username_step' and typed_user:           # the username page is still showing: do not type it twice
            return _clean({'ok': True, 'state': 'submitted', 'still_on_sign_in': True,
                           'message': 'The username was typed; the page has not moved on. Check the pane.'}, secrets)

        wants_password = state == 'login_form'
        try:
            user = _vault.get_username(entry['name'], project_id=project_id)
            password = _vault.get_secret_value(entry['name'], consumer=CONSUMER, project_id=project_id,
                                               unattended=unattended) if wants_password else None
        except _vault.VaultLocked as e:
            raise FillError('the vault is locked: unlock it in Settings > Vault, then try again', 423, 'vault_locked') from e
        except _vault.SecretNotFound as e:
            raise FillError(f'there is no saved login named {entry["name"]}', 404, 'login_not_found') from e
        except _vault.SecretDenied as e:
            raise FillError(_oneline(e), 403, 'login_denied') from e
        except _vault.SecretsError as e:
            raise FillError(_oneline(e), 409, 'login_unusable') from e
        secrets = tuple(x for x in (user, password) if x)
        declared, host = _where(page, origins)           # the browser again, immediately before the typing
        if not declared:
            user = password = None
            raise _refuse(host)
        ok, value = page.evaluate(_js.build(origins, fill=True, user=user, password=password))
        user = password = None
        if not ok or not isinstance(value, dict) or not isinstance(value.get('state'), str):
            _log(f'[desk_signin] fill failed: {str(value).split(":", 1)[0][:40]}', flush=True)
            raise FillError('the browser pane did not take the login; nothing more was typed', 502, 'pane_error')
        if value['state'] in ('origin_mismatch', 'not_top'):
            raise _refuse(str(value.get('host') or ''))          # a redirect between the checks and the typing
        if value['state'] == 'action_mismatch':
            raise _refuse_action()
        if value['state'] in ('captcha', 'two_factor'):
            return _clean({'ok': True, 'state': 'handoff', 'reason': value['state'],
                           'message': 'The page asks for a check only you can do. Finish it in the pane; nothing more is typed.'}, secrets)
        if value['state'] != 'filled':
            return _clean({'ok': True, 'state': 'no_form', 'message': 'The sign-in form could not be found on this page.'}, secrets)
        last = value
        typed_user = typed_user or 'username' in (value.get('filled') or [])
        typed_password = typed_password or 'password' in (value.get('filled') or [])
        if value.get('action_blocked'):
            return _clean({'ok': True, 'state': 'filled', 'filled': list(value.get('filled') or []), 'action_blocked': True,
                           'message': 'The login was typed, but the form now sends it to an address that is not this sign-in\'s, '
                                      'so it was not submitted. Check the pane before you press anything.'}, secrets)
        if not value.get('submitted'):
            return _clean({'ok': True, 'state': 'filled', 'filled': list(value.get('filled') or []),
                           'message': 'The login was typed; press the sign-in button in the pane.'}, secrets)
        sleep(SETTLE_S)
    return _clean({'ok': True, 'state': 'submitted', 'message': 'The login was typed and submitted. Check the pane for the result.',
                   'filled': list(last.get('filled') or [])}, secrets)
