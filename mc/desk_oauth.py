"""The Desk's shared sign-in connector (backlog c7ac1e8c, Ron 2026-10-02: "for a
non expert user ... we should be able to guide him how to connect his account").

One module walks a service's browser sign-in (authorization code + PKCE, dynamic
client registration where the provider offers it), catches the loopback
callback, exchanges the code, keeps the tokens in the vault and refreshes the
access token before it expires. Two services use it today: Higgsfield (MCP,
spends the user's plan credits) and X (posting).

Rules (CLAUDE.md vault rules; Wren audits this file):

  * **One write path.** `_store_record` is the only function in this module that
    puts a token into the vault, and it is only reached from `complete()` (the
    result of a human sign-in) and `_refresh()` (rotating a token a human
    already granted). No route returns a token value, nothing here logs one,
    and every vendor string goes through `_safe` (the vault's redactor) first.
  * **Starting and ending a sign-in is human-only.** The routes that reach
    `start()` and `disconnect()` refuse an unattended caller and require the
    retyped dashboard passcode (MC-995); this module trusts that gate.
  * **The callback is NOT a Clayrune route.** The browser pane may never reach
    Clayrune's own origin (browser_routes `_is_clayrune_own_origin`, Wren
    2026-09-15 blocker B), so the provider's redirect lands on a short-lived
    listener of its own: `127.0.0.1` only, one path, open only while a flow is
    pending (at most `FLOW_TTL_S`), closed the moment the flow ends. It only
    completes a flow `start()` opened: the `state` must match, in memory.
    Higgsfield takes an ephemeral port per flow (dynamic client registration);
    X needs one callback URL typed into its developer portal, so it gets the
    fixed `X_CALLBACK_PORT`.
  * **Status is derived, never stored.** The vault entry's non-secret `hint`
    carries `state / exp / refresh`, so the Connections screen can ask "is it
    connected" without decrypting (and auditing) a token on every poll.

Vault entries: `oauth.higgsfield`, `oauth.x` (type token, global scope; the
value is one JSON record). X's app credentials are pasted by the human through
the ordinary Secrets form as `x.client-id` and, for a confidential app,
`x.client-secret`; this module only reads them.

X facts (docs.x.com, "OAuth 2.0 Authorization Code Flow with PKCE", read
2026-10-02): authorize `https://x.com/i/oauth2/authorize`; token and refresh
`https://api.x.com/2/oauth2/token`; revoke `https://api.x.com/2/oauth2/revoke`;
a refresh token is issued only when the `offline.access` scope is requested; a
confidential client authenticates the token call with an `Authorization: Basic`
header of `client_id:client_secret`, a public client sends `client_id` in the
form body. The docs page does NOT state the access-token lifetime or whether the
refresh token rotates, so the lifetime is whatever `expires_in` the token
response carries (7200 s, the commonly documented two hours, only if the field
is missing) and a rotated `refresh_token` in a refresh response always replaces
the stored one.

Higgsfield facts (docs/desk_v1/HIGGSFIELD_MCP_SPIKE.md section 8, runtime
proven 2026-10-02): the protected-resource document at
`https://mcp.higgsfield.ai/.well-known/oauth-protected-resource` names the
resource and its authorization servers; the one that offers registration and
S256 is used; the access token lives about 86399 s; tokens revoke at the
server's `revocation_endpoint`; a registered client cannot be deleted.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import re
import secrets as pysecrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from mc import secrets_store
from mc.core import _log

FLOW_TTL_S = 600
MAX_FLOWS = 16
REFRESH_SKEW_S = 120
DEFAULT_X_LIFETIME_S = 7200
CALLBACK_PATH = '/callback'
LEGACY_X_TOKEN = 'x.oauth-token'     # a static token pasted by hand, honoured until a sign-in exists
X_CALLBACK_PORT = 53682          # fixed: the user types this URL into X's developer portal once

SERVICES: dict[str, dict[str, Any]] = {
    'higgsfield': {
        'label': 'Higgsfield', 'vault': 'oauth.higgsfield', 'profile': 'desk-higgsfield',
        'discovery': 'https://mcp.higgsfield.ai/.well-known/oauth-protected-resource',
        'client_name': 'Clayrune',
    },
    'x': {
        'label': 'X', 'vault': 'oauth.x', 'profile': 'desk-x',
        'authorize': 'https://x.com/i/oauth2/authorize',
        'token': 'https://api.x.com/2/oauth2/token',
        'revoke': 'https://api.x.com/2/oauth2/revoke',
        'scopes': ('tweet.read', 'tweet.write', 'users.read', 'offline.access'),
        'client_id_secret': 'x.client-id', 'client_secret_secret': 'x.client-secret',
    },
}

# Services whose sign-in belongs to ONE Desk account (several X accounts, one sign-in
# each). Higgsfield is the workspace's single engine sign-in and stays a singleton.
PER_ACCOUNT = frozenset({'x'})
# Any id the Desk can hold: `desk_accounts` takes 1-80 of [A-Za-z0-9_-] and a channel id lifted
# from a presence may carry a '.', so the one limit here is the widest of them (review 2026-10-03:
# a 40-char cap made `desk_account_refs.bind` raise inside every store read).
_ACCOUNT_ID = re.compile(r'^[A-Za-z0-9_.-]{1,80}$')
_PLAIN_ID = re.compile(r'^[a-z0-9_-]{1,40}$')

_lock = threading.RLock()
_flows: dict[str, dict[str, Any]] = {}          # state -> flow
_refresh_locks: dict[str, threading.Lock] = {}  # vault entry name -> lock (see _refresh_lock)
_refresh_locks_guard = threading.Lock()


class OAuthError(Exception):
    """A sign-in step failed. `code` is stable for the UI, `status` the HTTP
    answer the route gives, `message` plain words with no vendor secret in it."""

    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


def _safe(text: Any, limit: int = 300) -> str:
    s = secrets_store.redact(str(text or ''))
    return ' '.join(s.split())[:limit]


def _http(method: str, url: str, *, headers: dict | None = None, body: bytes | None = None,
          timeout: float = 30) -> tuple[int, dict, bytes]:
    """The one HTTP function in this module; tests replace it. Delegates to the
    engine connector's transport (no redirects followed, bounded body)."""
    from mc import desk_engines
    return desk_engines._http_request(method, url, headers=headers, body=body, timeout=timeout,
                                      max_bytes=1024 * 1024)


def _https_ok(url: Any) -> bool:
    """True for an https URL, or http on loopback (the only cleartext this module
    may use). Endpoints come from provider metadata and dynamic registration, so
    a provider-supplied `http://` address must never receive a code or a token."""
    try:
        u = urllib.parse.urlsplit(str(url or ''))
    except ValueError:
        return False
    return bool(u.hostname) and (u.scheme == 'https' or (
        u.scheme == 'http' and u.hostname in ('127.0.0.1', 'localhost', '::1')))


def _call(method: str, url: str, *, headers: dict | None = None, form: dict | None = None,
          json_body: Any = None) -> tuple[int, Any]:
    if not _https_ok(url):
        raise OAuthError('insecure_endpoint', 'a sign-in address was not secure (https), so nothing was sent', 502)
    h = dict(headers or {})
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        h['Content-Type'] = 'application/x-www-form-urlencoded'
    elif json_body is not None:
        body = json.dumps(json_body).encode()
        h['Content-Type'] = 'application/json'
    h.setdefault('Accept', 'application/json')
    try:
        status, _rh, data = _http(method, url, headers=h, body=body)
    except Exception as e:  # transport failure: say so, with no vendor text beyond the class
        raise OAuthError('unreachable', f'could not reach {urllib.parse.urlsplit(url).netloc}: '
                                        f'{type(e).__name__}', 502) from e
    try:
        parsed = json.loads(data.decode('utf-8')) if data else {}
    except ValueError:
        parsed = {'_raw': _safe(data.decode('utf-8', errors='replace'))}
    return status, parsed


def _def(service: str) -> dict[str, Any]:
    d = SERVICES.get(service)
    if d is None:
        raise OAuthError('unknown_service', f'unknown service {service!r}', 404)
    return d


def _account(service: str, account_id: str | None) -> str | None:
    """The account a call is about: None = the service's legacy singleton sign-in
    (`oauth.x`, profile `desk-x`), the name every sign-in made before per-account
    names existed lives under. Only `PER_ACCOUNT` services take one."""
    if account_id is None:
        return None
    if service not in PER_ACCOUNT:
        raise OAuthError('not_per_account', f"{_def(service)['label']} has one sign-in for the whole workspace", 400)
    if not isinstance(account_id, str) or not _ACCOUNT_ID.match(account_id):
        raise OAuthError('bad_account', 'that account id is not valid', 400)
    return account_id


def _suffix(acc: str) -> str:
    """The part of a vault / profile name that stands for an account. Vault and
    profile names are lowercase but account ids are case-sensitive, so folding case
    would let `Acct-B` and `acct-b` share one sign-in. An id that is already a plain
    lowercase slug is used as it is; any other id becomes a slug of itself plus a hash
    of the EXACT id, behind a '.' no plain id can contain, so no two ids collide."""
    if _PLAIN_ID.match(acc):
        return acc
    slug = re.sub(r'[^a-z0-9_-]', '-', acc.lower())[:24].strip('.')
    return f'{slug}.{hashlib.sha256(acc.encode("utf-8")).hexdigest()[:12]}'


def vault_name(service: str, account_id: str | None = None) -> str:
    """The vault entry holding this sign-in: `oauth.x` for the legacy singleton,
    `oauth.x.<suffix>` for an account (see `_suffix`)."""
    base = _def(service)['vault']
    acc = _account(service, account_id)
    return base if acc is None else f'{base}.{_suffix(acc)}'


def profile_name(service: str, account_id: str | None = None) -> str:
    """The named browser profile the sign-in is made in (one login per account)."""
    base = _def(service)['profile']
    acc = _account(service, account_id)
    return base if acc is None else f'{base}-{_suffix(acc)}'


def _refresh_lock(name: str) -> threading.Lock:
    with _refresh_locks_guard:
        return _refresh_locks.setdefault(name, threading.Lock())


def redirect_uri(port: int) -> str:
    return f'http://127.0.0.1:{int(port)}{CALLBACK_PATH}'


def x_redirect_uri() -> str:
    """The exact callback URL the X wizard tells the user to paste."""
    return redirect_uri(X_CALLBACK_PORT)


# -- vault: the ONE write path --------------------------------------------------

def _hint(rec: dict) -> str:
    return (f"state={'needs_signin' if rec.get('needs_signin') else 'ok'} "
            f"exp={int(rec.get('expires_at') or 0)} refresh={'yes' if rec.get('refresh_token') else 'no'}")


def _meta(service: str, account_id: str | None = None) -> dict | None:
    name = vault_name(service, account_id)
    try:
        return next((s for s in secrets_store.list_secrets() if s['name'] == name), None)
    except secrets_store.SecretsError:
        return None


def _store_record(service: str, rec: dict[str, Any], account_id: str | None = None) -> None:
    """THE ONLY place a token enters the vault. `rec` is the full record; the
    entry's policy (`allow_unattended`, `scope`) is kept from an existing entry
    and defaults to the vault's own default (unattended allowed, global) on the
    first sign-in."""
    d = _def(service)
    name = vault_name(service, account_id)
    old = _meta(service, account_id) or {}
    secrets_store.set_secret(
        name, json.dumps(rec, separators=(',', ':')),
        description=f"{d['label']} sign-in made from Connections. Clayrune keeps it fresh.",
        hint=_hint(rec), scope=old.get('scope') or 'global',
        allow_unattended=bool(old.get('allow_unattended', True)),
        entry_type=secrets_store.ENTRY_TOKEN)
    for key in ('access_token', 'refresh_token'):
        if rec.get(key):
            secrets_store.register_dispensed(name, rec[key])


def _read_record(service: str, *, consumer: str, project_id: str | None = None,
                 unattended: bool = False, account_id: str | None = None) -> dict[str, Any]:
    d = _def(service)
    name = vault_name(service, account_id)
    try:
        raw = secrets_store.get_secret_value(name, consumer=consumer, project_id=project_id,
                                             unattended=unattended, internal=True)
    except secrets_store.SecretNotFound as e:
        raise OAuthError('not_connected', f"{d['label']} is not signed in yet") from e
    except secrets_store.SecretsError as e:
        raise OAuthError('unavailable', _safe(e)) from e
    try:
        rec = json.loads(raw)
    except ValueError as e:
        raise OAuthError('needs_signin', f"the saved {d['label']} sign-in is damaged; sign in again") from e
    if not isinstance(rec, dict) or not rec.get('access_token'):
        raise OAuthError('needs_signin', f"the saved {d['label']} sign-in is incomplete; sign in again")
    for key in ('access_token', 'refresh_token'):
        if rec.get(key):
            secrets_store.register_dispensed(name, rec[key])
    return rec


# -- status ---------------------------------------------------------------------

def status(service: str, account_id: str | None = None) -> dict[str, Any]:
    """`{state: connected|needs_signin|not_connected, reason}` from the vault's
    metadata only (no token is decrypted or audited)."""
    d = _def(service)
    meta = _meta(service, account_id)
    if meta is None:
        return {'state': 'not_connected', 'reason': None}
    if not secrets_store.is_readable(vault_name(service, account_id)):
        return {'state': 'needs_signin', 'reason': 'the saved sign-in can no longer be opened; sign in again'}
    parts = {k: v for k, _, v in (p.partition('=') for p in (meta.get('hint') or '').split()) if v}
    if parts.get('state') == 'needs_signin':
        return {'state': 'needs_signin', 'reason': f"{d['label']} no longer accepts the saved sign-in; sign in again"}
    try:
        exp = int(parts.get('exp') or 0)
    except ValueError:
        exp = 0
    if exp and exp < time.time() and parts.get('refresh') != 'yes':
        return {'state': 'needs_signin', 'reason': 'the sign-in ran out; sign in again'}
    return {'state': 'connected', 'reason': None}


def overview() -> dict[str, Any]:
    """Everything Connections needs to paint the guided cards, from the vault's
    metadata only (names and states, never a value)."""
    try:
        have = {s['name'] for s in secrets_store.list_secrets()}
    except secrets_store.SecretsError:
        have = set()
    out: dict[str, Any] = {'higgsfield': status('higgsfield'),
                           'x': {**status('x'), 'app': x_app_state(), 'callback_url': x_redirect_uri(),
                                 'scopes': list(SERVICES['x']['scopes'])}}
    for key, d in KEY_SERVICES.items():
        out[key] = {'saved': d['vault'] in have, 'vault_entry': d['vault']}
    return out


def x_app_state() -> dict[str, bool]:
    """Which of X's two app credentials the human has saved (names only)."""
    d = SERVICES['x']
    try:
        have = {s['name'] for s in secrets_store.list_secrets()}
    except secrets_store.SecretsError:
        have = set()
    return {'client_id': d['client_id_secret'] in have, 'client_secret': d['client_secret_secret'] in have}


# -- start ----------------------------------------------------------------------

def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(pysecrets.token_bytes(48)).rstrip(b'=').decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


def _vault_plain(name: str, *, project_id: str | None = None, unattended: bool = False) -> str | None:
    """An app credential the human saved (X Client ID / secret). None if absent.
    `unattended` / `project_id` are the caller's, so the entry's own policy
    (`allow_unattended`, `scope`) applies to this read as it does to the token's."""
    try:
        return secrets_store.get_secret_value(name, consumer='desk_oauth', project_id=project_id,
                                              unattended=unattended)
    except secrets_store.SecretNotFound:
        return None
    except secrets_store.SecretsError as e:
        raise OAuthError('unavailable', _safe(e)) from e


def _discover_higgsfield() -> dict[str, Any]:
    d = SERVICES['higgsfield']
    status_, prm = _call('GET', d['discovery'])
    if status_ != 200 or not isinstance(prm, dict) or not prm.get('resource'):
        raise OAuthError('discovery_failed', 'could not read how to sign in to Higgsfield', 502)
    if not _https_ok(prm['resource']):
        raise OAuthError('insecure_endpoint', 'Higgsfield offered a sign-in address that is not secure, so nothing was sent', 502)
    for asu in prm.get('authorization_servers') or []:
        st, meta = _call('GET', str(asu).rstrip('/') + '/.well-known/oauth-authorization-server')
        if (st == 200 and isinstance(meta, dict) and meta.get('registration_endpoint')
                and meta.get('authorization_endpoint') and meta.get('token_endpoint')
                and 'S256' in (meta.get('code_challenge_methods_supported') or [])):
            for k in ('registration_endpoint', 'authorization_endpoint', 'token_endpoint'):
                if not _https_ok(meta[k]):
                    raise OAuthError('insecure_endpoint', 'Higgsfield offered a sign-in address that is not secure, so nothing was sent', 502)
            if meta.get('revocation_endpoint') and not _https_ok(meta['revocation_endpoint']):
                meta = {k: v for k, v in meta.items() if k != 'revocation_endpoint'}
            return {'resource': prm['resource'], 'scopes': list(prm.get('scopes_supported') or []),
                    'meta': meta}
    raise OAuthError('discovery_failed', 'Higgsfield did not offer a sign-in this app can use', 502)


class _CallbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False      # a second Clayrune (or anything) on the X port must fail, not share it


_PAGE = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
         '<title>Clayrune</title><body style="font:16px system-ui;max-width:32em;margin:15vh auto;padding:0 1em">'
         '<h2>{head}</h2><p>{msg}</p><p>{tail}</p></body>')


class _CallbackHandler(BaseHTTPRequestHandler):
    server_version = 'Clayrune'

    def log_message(self, *a):          # the request line carries the sign-in code: never log it
        return

    def do_GET(self):
        parts = urllib.parse.urlsplit(self.path)
        if parts.path != CALLBACK_PATH:
            self.send_error(404)
            return
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        ok, msg = complete(params)
        body = _PAGE.format(
            head='You are signed in' if ok else 'Sign in did not finish', msg=html.escape(msg),
            tail='You can close this tab and go back to Clayrune.' if ok else 'Go back to Clayrune and try again.').encode()
        self.send_response(200 if ok else 400)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(body)


def _open_listener(port: int) -> _CallbackServer:
    try:
        srv = _CallbackServer(('127.0.0.1', port), _CallbackHandler)
    except OSError as e:
        raise OAuthError('port_busy', f'Clayrune could not open the sign-in return address (port {port} is in '
                                      f'use by another program). Close it and try again.', 409) from e
    threading.Thread(target=srv.serve_forever, name='desk-oauth-callback', daemon=True).start()
    return srv


def _close_listener(srv: _CallbackServer | None) -> None:
    if srv is None:
        return

    def _stop():
        try:
            srv.shutdown()
            srv.server_close()
        except Exception as e:
            _log(f'[desk_oauth] closing the sign-in listener failed: {type(e).__name__}', flush=True)
    threading.Thread(target=_stop, name='desk-oauth-close', daemon=True).start()


def _expire(state: str) -> None:
    """TTL end of a flow nobody finished: close its listener, mark it."""
    with _lock:
        flow = _flows.get(state)
        if flow is None or flow['status'] != 'pending':
            return
        flow['status'], flow['message'] = 'error', 'The sign-in took too long. Start again.'
        flow.pop('verifier', None)
        srv = flow.pop('listener', None)
    _close_listener(srv)


def start(service: str, account_id: str | None = None) -> dict[str, Any]:
    """Open a sign-in flow -> `{flow_id, auth_url, redirect_uri, profile}`. The
    caller opens `auth_url` in the browser pane on `profile`. Human-gated by the
    route; this function only builds the request and remembers the secrets of the
    flow (verifier, client) in memory. `account_id` names the Desk account the
    sign-in is for (its own vault entry and profile); None is the legacy one."""
    d = _def(service)
    account_id = _account(service, account_id)
    if secrets_store.is_locked():        # the token is written to the vault only after the human finishes at the
        # vendor: a locked vault is reported now, before discovery, a port or the sign-in page, never after.
        raise OAuthError('vault_locked', 'Unlock the vault first, then sign in', 409)
    if service == 'x' and not _vault_plain(d['client_id_secret']):     # fail before a port is opened
        raise OAuthError('app_missing', 'Save your X Client ID first (step 2), then sign in', 409)
    if service == 'higgsfield':
        disc = _discover_higgsfield()        # network first: a failure here opens nothing
    _drop_pending(service)
    srv = _open_listener(X_CALLBACK_PORT if service == 'x' else 0)
    try:
        return _start_flow(service, d, srv, disc if service == 'higgsfield' else None, account_id)
    except BaseException:
        _close_listener(srv)
        raise


def _drop_pending(service: str) -> None:
    """Ending an earlier unfinished sign-in of this service (its port may be the
    one the next flow needs)."""
    with _lock:
        now = time.time()
        gone = [k for k, f in _flows.items() if now - f['created'] > FLOW_TTL_S
                or (f['service'] == service and f['status'] == 'pending')]
        servers = [_flows.pop(k).get('listener') for k in gone]
    for s in servers:
        _close_listener(s)


def _start_flow(service: str, d: dict, srv: _CallbackServer, disc: dict | None,
                account_id: str | None = None) -> dict[str, Any]:
    redirect = redirect_uri(srv.server_address[1])
    verifier, challenge = _pkce()
    state = pysecrets.token_urlsafe(24)
    flow: dict[str, Any] = {'service': service, 'verifier': verifier, 'redirect': redirect,
                            'flow_id': pysecrets.token_urlsafe(12), 'created': time.time(),
                            'status': 'pending', 'message': '', 'listener': srv, 'account_id': account_id}
    if service == 'higgsfield':
        assert disc is not None
        meta, scopes = disc['meta'], disc['scopes']
        st, dcr = _call('POST', meta['registration_endpoint'], json_body={
            'client_name': d['client_name'], 'redirect_uris': [redirect],
            'token_endpoint_auth_method': 'none',
            'grant_types': ['authorization_code', 'refresh_token'],
            'response_types': ['code'], 'scope': ' '.join(scopes)})
        if st >= 300 or not isinstance(dcr, dict) or not dcr.get('client_id'):
            # A refused registration body carries no secret; log it so the refusal is visible.
            _log(f'[desk_oauth] higgsfield client registration refused: HTTP {st} '
                 f'{_safe(json.dumps(dcr) if isinstance(dcr, dict) else dcr, 200)}', flush=True)
            raise OAuthError('register_failed', f'Higgsfield did not accept this app\'s sign-in request '
                                                f'(HTTP {st})', 502)
        flow.update(client_id=dcr['client_id'], token_endpoint=meta['token_endpoint'],
                    revocation_endpoint=meta.get('revocation_endpoint'), resource=disc['resource'],
                    issuer=meta.get('issuer'), scope=' '.join(scopes))
        params = {'response_type': 'code', 'client_id': dcr['client_id'], 'redirect_uri': redirect,
                  'scope': ' '.join(scopes), 'state': state, 'code_challenge': challenge,
                  'code_challenge_method': 'S256', 'resource': disc['resource']}
        base = meta['authorization_endpoint']
    else:
        client_id = _vault_plain(d['client_id_secret'])
        if not client_id:
            raise OAuthError('app_missing', 'Save your X Client ID first (step 2), then sign in', 409)
        scope = ' '.join(d['scopes'])
        flow.update(client_id=client_id, token_endpoint=d['token'], revocation_endpoint=d['revoke'],
                    resource=None, issuer=None, scope=scope)
        params = {'response_type': 'code', 'client_id': client_id, 'redirect_uri': redirect,
                  'scope': scope, 'state': state, 'code_challenge': challenge,
                  'code_challenge_method': 'S256'}
        base = d['authorize']
    with _lock:
        while len(_flows) >= MAX_FLOWS:
            old = _flows.pop(min(_flows, key=lambda k: _flows[k]['created']), None)
            _close_listener((old or {}).get('listener'))
        _flows[state] = flow
    timer = threading.Timer(FLOW_TTL_S, _expire, args=(state,))
    timer.daemon = True
    timer.start()
    _log(f"[desk_oauth] sign-in started for {service}", flush=True)
    return {'flow_id': flow['flow_id'], 'auth_url': base + '?' + urllib.parse.urlencode(params),
            'redirect_uri': redirect, 'profile': profile_name(service, account_id)}


def flow_status(flow_id: str) -> dict[str, Any]:
    """`{status: pending|done|error|unknown, message}` for the page's poll. Never
    carries the state, the verifier or a token."""
    with _lock:
        for f in _flows.values():
            if f['flow_id'] == flow_id:
                if f['status'] == 'pending' and time.time() - f['created'] > FLOW_TTL_S:
                    return {'status': 'error', 'message': 'The sign-in took too long. Start again.'}
                return {'status': f['status'], 'message': f['message'], 'service': f['service']}
    return {'status': 'unknown', 'message': 'No sign-in is waiting.'}


# -- exchange / callback --------------------------------------------------------

def _client_auth(service: str, form: dict, headers: dict, *, project_id: str | None = None,
                 unattended: bool = False) -> None:
    """Add the client's own authentication to a token-endpoint call. X: a
    confidential app sends Basic client_id:secret and no body client_id; a
    public one (no saved secret) sends client_id in the body. Higgsfield is a
    public client."""
    if service != 'x':
        return
    d = SERVICES['x']
    secret = _vault_plain(d['client_secret_secret'], project_id=project_id, unattended=unattended)
    cid = form.get('client_id') or _vault_plain(d['client_id_secret'], project_id=project_id,
                                                unattended=unattended) or ''
    if secret:
        headers['Authorization'] = 'Basic ' + base64.b64encode(f'{cid}:{secret}'.encode()).decode()
        form.pop('client_id', None)
    else:
        form['client_id'] = cid


def _token_response(service: str, st: int, body: Any, what: str) -> dict[str, Any]:
    if st >= 300 or not isinstance(body, dict) or not body.get('access_token'):
        err = body.get('error') if isinstance(body, dict) else None
        # A refresh the provider answers 400/401 will never work by retrying, whatever
        # the error code says (X also sends invalid_request / invalid_client): the
        # human has to sign in again. Left as 'connected' it would retry on every publish.
        if st in (400, 401) and (what == 'refresh' or err in ('invalid_grant', 'invalid_token', 'unauthorized_client')):
            raise OAuthError('needs_signin', f"{SERVICES[service]['label']} no longer accepts the saved sign-in; sign in again")
        raise OAuthError(f'{what}_failed', f"{SERVICES[service]['label']} did not accept the {what} "
                                           f"(HTTP {st}{': ' + _safe(err) if err else ''})", 502)
    return body


def complete(params: dict[str, str]) -> tuple[bool, str]:
    """The callback. `params` is the redirect's query. Returns `(ok, message)`
    for the page the user sees; the flow's own status is updated for the poll."""
    state = params.get('state') or ''
    with _lock:
        flow = None
        for k, f in _flows.items():
            if hmac.compare_digest(k.encode(), state.encode()):
                flow = f
                break
        if flow is None or time.time() - flow['created'] > FLOW_TTL_S or flow['status'] != 'pending':
            return False, 'This sign-in link is not valid any more. Go back to Clayrune and start again.'
        flow['status'] = 'working'
    service = flow['service']
    account_id = flow.get('account_id')
    label = SERVICES[service]['label']
    vault = vault_name(service, account_id)

    def fail(msg: str) -> tuple[bool, str]:
        with _lock:
            flow['status'], flow['message'] = 'error', msg
            flow.pop('verifier', None)
            srv = flow.pop('listener', None)
        _close_listener(srv)
        _log(f'[desk_oauth] sign-in for {service} failed', flush=True)
        return False, msg

    if params.get('error'):
        return fail(f'{label} did not finish signing you in ({_safe(params.get("error"), 80)}).')
    if flow.get('issuer') and params.get('iss') and params['iss'] != flow['issuer']:
        return fail(f'{label} answered from an address this app does not expect, so nothing was saved.')
    code = params.get('code')
    if not code:
        return fail(f'{label} sent no sign-in code. Start again.')
    form = {'grant_type': 'authorization_code', 'code': code, 'redirect_uri': flow['redirect'],
            'client_id': flow['client_id'], 'code_verifier': flow['verifier']}
    if flow.get('resource'):
        form['resource'] = flow['resource']
    headers: dict[str, str] = {}
    try:
        _client_auth(service, form, headers)
        st, body = _call('POST', flow['token_endpoint'], headers=headers, form=form)
        tok = _token_response(service, st, body, 'sign-in')
        life = tok.get('expires_in')
        life = int(life) if isinstance(life, (int, float)) and life > 0 else (
            DEFAULT_X_LIFETIME_S if service == 'x' else 3600)
        rec = {'v': 1, 'access_token': tok['access_token'], 'refresh_token': tok.get('refresh_token') or '',
               'expires_at': int(time.time()) + life, 'client_id': flow['client_id'],
               'token_endpoint': flow['token_endpoint'], 'revocation_endpoint': flow.get('revocation_endpoint'),
               'resource': flow.get('resource'), 'scope': tok.get('scope') or flow.get('scope'),
               'needs_signin': False}
        secrets_store.register_dispensed(vault, rec['access_token'])
        if rec['refresh_token']:
            secrets_store.register_dispensed(vault, rec['refresh_token'])
        with _refresh_lock(vault):          # a refresh in flight must not land after (or over) this sign-in
            _store_record(service, rec, account_id)
    except OAuthError as e:
        return fail(str(e))
    except secrets_store.SecretsError as e:
        return fail(f'Signed in, but the sign-in could not be saved: {_safe(e)}')
    except Exception as e:      # e.g. a vault OSError: end the flow and free the port, never leave it 'working'
        _log(f'[desk_oauth] saving the {service} sign-in failed: {type(e).__name__}', flush=True)
        return fail(f'Signed in, but the sign-in could not be saved ({type(e).__name__}). Try again.')
    with _lock:
        flow['status'], flow['message'] = 'done', f'{label} is connected.'
        flow.pop('verifier', None)
        srv = flow.pop('listener', None)
    _close_listener(srv)
    _log(f'[desk_oauth] {service} connected', flush=True)
    return True, f'{label} is connected.'


# -- use + refresh --------------------------------------------------------------

def _refresh(service: str, rec: dict[str, Any], *, project_id: str | None = None,
             unattended: bool = False, account_id: str | None = None) -> dict[str, Any]:
    label = SERVICES[service]['label']
    if not rec.get('refresh_token'):
        _store_record(service, {**rec, 'needs_signin': True}, account_id)
        raise OAuthError('needs_signin', f'The {label} sign-in ran out; sign in again')
    form = {'grant_type': 'refresh_token', 'refresh_token': rec['refresh_token'],
            'client_id': rec.get('client_id') or ''}
    if rec.get('resource'):
        form['resource'] = rec['resource']
    headers: dict[str, str] = {}
    _client_auth(service, form, headers, project_id=project_id, unattended=unattended)
    st, body = _call('POST', rec['token_endpoint'], headers=headers, form=form)
    try:
        tok = _token_response(service, st, body, 'refresh')
    except OAuthError as e:
        if e.code == 'needs_signin':
            _store_record(service, {**rec, 'needs_signin': True}, account_id)
        raise
    life = tok.get('expires_in')
    life = int(life) if isinstance(life, (int, float)) and life > 0 else (
        DEFAULT_X_LIFETIME_S if service == 'x' else 3600)
    new = {**rec, 'access_token': tok['access_token'],
           'refresh_token': tok.get('refresh_token') or rec['refresh_token'],
           'expires_at': int(time.time()) + life, 'needs_signin': False}
    _store_record(service, new, account_id)
    return new


def access_token(service: str, *, consumer: str, project_id: str | None = None,
                 unattended: bool = False, account_id: str | None = None) -> str:
    """A bearer token good for the next `REFRESH_SKEW_S` seconds, refreshing it
    first when it is about to lapse. Raises OAuthError: `not_connected`,
    `needs_signin` (the human must sign in again) or `refresh_failed` /
    `unreachable` (try again later)."""
    rec = _read_record(service, consumer=consumer, project_id=project_id, unattended=unattended,
                       account_id=account_id)
    if rec.get('needs_signin'):
        raise OAuthError('needs_signin', f"{SERVICES[service]['label']} needs you to sign in again")
    if rec.get('expires_at', 0) - time.time() > REFRESH_SKEW_S:
        return rec['access_token']
    with _refresh_lock(vault_name(service, account_id)):
        rec = _read_record(service, consumer=consumer, project_id=project_id, unattended=unattended,
                           account_id=account_id)
        if rec.get('expires_at', 0) - time.time() > REFRESH_SKEW_S:
            return rec['access_token']
        return _refresh(service, rec, project_id=project_id, unattended=unattended,
                        account_id=account_id)['access_token']


def x_token(*, consumer: str, project_id: str | None = None, unattended: bool = False,
            account_id: str | None = None) -> str:
    """The X bearer token the Desk posts and reads with. A sign-in made from
    Connections wins (kept fresh here); a vault entry `x.oauth-token` pasted by a
    human before sign-in existed still works until one is made. `account_id`
    selects that Desk account's own sign-in; None is the legacy singleton. An
    account with its own name never falls back to the singleton's token or to the
    pasted one: that would post as another account. Raises OAuthError (sign-in
    route) or secrets_store.SecretsError (pasted token)."""
    if account_id is not None:
        return access_token('x', consumer=consumer, project_id=project_id, unattended=unattended,
                            account_id=account_id)
    if status('x')['state'] != 'not_connected':
        return access_token('x', consumer=consumer, project_id=project_id, unattended=unattended)
    return secrets_store.get_secret_value(LEGACY_X_TOKEN, consumer=consumer, project_id=project_id,
                                          unattended=unattended)


# -- disconnect -----------------------------------------------------------------

def disconnect(service: str, account_id: str | None = None) -> dict[str, Any]:
    """Revoke both tokens at the provider (best effort) and delete the vault
    entry. `revoked` is True only when the provider answered 2xx for every token
    sent; the local sign-in is removed either way."""
    name = vault_name(service, account_id)
    with _refresh_lock(name):       # an in-flight refresh must finish first, or it re-creates the entry we delete
        revoked: bool | None = None
        try:
            rec = _read_record(service, consumer='desk_oauth:disconnect', account_id=account_id)
        except OAuthError:
            rec = None
        if rec and rec.get('revocation_endpoint'):
            results = []
            for hint, key in (('refresh_token', 'refresh_token'), ('access_token', 'access_token')):
                if not rec.get(key):
                    continue
                form = {'token': rec[key], 'token_type_hint': hint, 'client_id': rec.get('client_id') or ''}
                headers: dict[str, str] = {}
                try:
                    _client_auth(service, form, headers)
                    st, _b = _call('POST', rec['revocation_endpoint'], headers=headers, form=form)
                    results.append(200 <= st < 300)
                except OAuthError:
                    results.append(False)
            revoked = bool(results) and all(results)
        try:
            deleted = secrets_store.delete_secret(name)
        except secrets_store.SecretsError as e:
            raise OAuthError('unavailable', _safe(e)) from e
    _log(f'[desk_oauth] {service} disconnected (revoked={revoked})', flush=True)
    return {'service': service, 'deleted': bool(deleted), 'revoked': revoked}


# -- guided key paste: one free read call per service ------------------------------

KEY_SERVICES = {
    'gemini': {'label': 'Gemini', 'vault': 'gemini-api', 'url': 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=1',
               'header': 'x-goog-api-key'},
    'openai': {'label': 'OpenAI', 'vault': 'openai-api', 'url': 'https://api.openai.com/v1/models',
               'header': 'Authorization'},
}


def test_key(service: str, *, project_id: str | None = None) -> dict[str, Any]:
    """The "Test connection" button: ONE free read call (list models) with the
    saved key. Returns `{ok, message}`; never the key."""
    d = KEY_SERVICES.get(service)
    if d is None:
        raise OAuthError('unknown_service', f'unknown service {service!r}', 404)
    try:
        key = secrets_store.get_secret_value(d['vault'], consumer='desk_oauth:test', project_id=project_id)
    except secrets_store.SecretNotFound:
        return {'ok': False, 'message': f"No {d['label']} key is saved yet."}
    except secrets_store.SecretsError as e:
        return {'ok': False, 'message': _safe(e)}
    headers = {d['header']: (f'Bearer {key}' if d['header'] == 'Authorization' else key)}
    try:
        st, body = _call('GET', d['url'], headers=headers)
    except OAuthError as e:
        return {'ok': False, 'message': str(e)}
    if 200 <= st < 300:
        return {'ok': True, 'message': f"{d['label']} accepted the key."}
    if st in (400, 401, 403):
        return {'ok': False, 'message': f"{d['label']} did not accept the key (HTTP {st}). Check it was copied whole, then save it again."}
    return {'ok': False, 'message': f"{d['label']} answered with HTTP {st}. Try again in a minute."}
