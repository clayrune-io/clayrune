"""Desk Connections: the shared sign-in connector (`mc/desk_oauth.py`) and its
`/api/desk/connect/*` routes. NO REAL PROVIDER CALL anywhere in this file:
`desk_oauth._http` is a scripted fake and the vault is four patched
`secrets_store` functions. The callback is exercised through the REAL loopback
listener (a plain urllib GET to 127.0.0.1), because the listener is the part a
mock would hide. Pinned:

  * start builds an authorization-code + PKCE request (S256 challenge, state,
    registered redirect on 127.0.0.1) and opens a listener; nothing about the
    verifier or state is in the answer;
  * a callback whose `state` does not match a pending flow completes nothing and
    stores nothing; a failed code exchange stores nothing and closes the listener;
  * a good sign-in is stored as ONE vault record through the one write path, the
    status becomes connected, and no response, flow poll or log line carries a
    token value;
  * a token near expiry is refreshed on use (a rotated refresh token replaces the
    old one), a rejected refresh marks the sign-in as needing a new one, and a
    token that is still fresh makes no call;
  * disconnect revokes at the provider and deletes the entry, and still deletes
    when the provider refuses;
  * start and disconnect are refused for an unattended caller and, for any caller,
    without the retyped dashboard passcode (MC-995);
  * X: confidential vs public client authentication on the token call, the fixed
    callback port, a hand-pasted legacy token still used until a sign-in exists;
  * "Test connection" makes one read call and never returns the key;
  * audit fixes (Wren, desk-connect-guides): disconnect and sign-in take the
    refresh lock so an in-flight refresh cannot resurrect a deleted entry; an
    unattended refresh reads the X client secret under that secret's own policy;
    a vault failure while saving ends the flow and frees the port; any 400/401 on
    a refresh needs a new sign-in; endpoints from provider metadata must be https.
"""
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk_oauth as oauth  # noqa: E402
from mc import secrets_store  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402

PASSCODE = 'unlock1234'
ACCESS_1 = 'access-token-SENTINEL-aaaaaaaaaaaaaaaaaaaa'
REFRESH_1 = 'refresh-token-SENTINEL-bbbbbbbbbbbbbbbbbbb'
ACCESS_2 = 'access-token-SENTINEL-cccccccccccccccccccc'
REFRESH_2 = 'refresh-token-SENTINEL-dddddddddddddddddddd'
GEMINI_KEY = 'gemkey-SENTINEL-eeeeeeeeeeeeeeeeeeeeeeee'
OPENAI_KEY = 'oaikey-SENTINEL-ffffffffffffffffffffffff'
X_SECRET = 'x-client-secret-SENTINEL-gggggggggggggggg'
SENTINELS = (ACCESS_1, REFRESH_1, ACCESS_2, REFRESH_2, GEMINI_KEY, OPENAI_KEY, X_SECRET)

PRM = 'https://mcp.higgsfield.ai/.well-known/oauth-protected-resource'
AS_META = 'https://clerk.higgsfield.ai/.well-known/oauth-authorization-server'
HF_TOKEN = 'https://clerk.higgsfield.ai/oauth/token'
HF_REVOKE = 'https://clerk.higgsfield.ai/oauth/revoke'


class FakeProvider:
    """Scripted `desk_oauth._http`: `on(METHOD, url-substring, *responses)`; a
    response is `(status, obj)` or an Exception; the last repeats."""

    def __init__(self):
        self.routes: dict = {}
        self.calls: list[dict] = []

    def on(self, method, needle, *responses):
        self.routes[(method, needle)] = list(responses)

    def __call__(self, method, url, *, headers=None, body=None, timeout=30):
        self.calls.append({'method': method, 'url': url, 'headers': dict(headers or {}),
                           'form': dict(urllib.parse.parse_qsl((body or b'').decode())) if body and
                           (headers or {}).get('Content-Type', '').startswith('application/x-www') else None,
                           'json': json.loads(body) if body and (headers or {}).get('Content-Type') == 'application/json' else None})
        for (m, needle), resp in self.routes.items():
            if m == method and needle in url:
                r = resp[0] if len(resp) == 1 else resp.pop(0)
                if isinstance(r, Exception):
                    raise r
                status, obj = r
                return status, {}, json.dumps(obj).encode()
        raise AssertionError(f'unscripted provider call: {method} {url}')

    def to(self, needle, method=None):
        return [c for c in self.calls if needle in c['url'] and (method is None or c['method'] == method)]


@pytest.fixture
def provider(monkeypatch):
    p = FakeProvider()
    monkeypatch.setattr(oauth, '_http', p)
    return p


@pytest.fixture
def vault(monkeypatch):
    """A fake vault: name -> record. `set_secret` is the real signature's subset."""
    state: dict = {'entries': {}, 'writes': [], 'unreadable': set()}

    def list_secrets(project_id=None, **kw):
        return [{'name': n, 'hint': r['hint'], 'scope': r['scope'], 'allow_unattended': r['allow_unattended']}
                for n, r in state['entries'].items()]

    def set_secret(name, value, *, username='', description='', hint='', scope='global',
                   allow_unattended=True, kind='password', entry_type=None):
        state['entries'][name] = {'value': value, 'hint': hint, 'scope': scope,
                                  'allow_unattended': allow_unattended, 'entry_type': entry_type}
        state['writes'].append(name)
        return {'name': name}

    def get_secret_value(name, *, consumer, project_id=None, unattended=False, internal=False):
        if name.startswith('oauth.') and not internal:
            raise secrets_store.SecretDenied(name)
        r = state['entries'].get(name)
        if r is None:
            raise secrets_store.SecretNotFound(name)
        if unattended and not r['allow_unattended']:
            raise secrets_store.SecretsError(f"'{name}' may not be used unattended")
        return r['value']

    def delete_secret(name):
        return state['entries'].pop(name, None) is not None

    monkeypatch.setattr(secrets_store, 'list_secrets', list_secrets)
    monkeypatch.setattr(secrets_store, 'set_secret', set_secret)
    monkeypatch.setattr(secrets_store, 'get_secret_value', get_secret_value)
    monkeypatch.setattr(secrets_store, 'delete_secret', delete_secret)
    monkeypatch.setattr(secrets_store, 'is_readable', lambda n: n in state['entries'] and n not in state['unreadable'])
    monkeypatch.setattr(oauth, '_meta', lambda service, account_id=None: next(
        (s for s in list_secrets() if s['name'] == oauth.vault_name(service, account_id)), None))
    return state


@pytest.fixture
def logs(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(oauth, '_log', lambda msg, *a, **k: lines.append(str(msg)))
    return lines


@pytest.fixture(autouse=True)
def _clean_flows():
    yield
    with oauth._lock:
        servers = [f.get('listener') for f in oauth._flows.values()]
        oauth._flows.clear()
    for s in servers:
        if s is not None:
            s.shutdown()
            s.server_close()


def _hf_discovery(p):
    p.on('GET', PRM, (200, {'resource': 'https://mcp.higgsfield.ai/mcp',
                            'authorization_servers': ['https://clerk.higgsfield.ai'],
                            'scopes_supported': ['openid', 'email', 'offline_access']}))
    p.on('GET', AS_META, (200, {
        'issuer': 'https://clerk.higgsfield.ai', 'authorization_endpoint': 'https://clerk.higgsfield.ai/oauth/authorize',
        'token_endpoint': HF_TOKEN, 'registration_endpoint': 'https://clerk.higgsfield.ai/oauth/register',
        'revocation_endpoint': HF_REVOKE, 'code_challenge_methods_supported': ['S256']}))
    p.on('POST', '/oauth/register', (201, {'client_id': 'cid-1'}))


def _state_of(auth_url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(auth_url).query))


def _hit(redirect_uri, **params):
    """A real GET against the loopback listener, like the browser's redirect."""
    url = redirect_uri + '?' + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def _sign_in_higgsfield(provider, **token_extra):
    _hf_discovery(provider)
    provider.on('POST', HF_TOKEN, (200, {'access_token': ACCESS_1, 'refresh_token': REFRESH_1,
                                         'expires_in': 86399, **token_extra}))
    out = oauth.start('higgsfield')
    q = _state_of(out['auth_url'])
    status, page = _hit(out['redirect_uri'], code='the-code', state=q['state'])
    return out, q, status, page


# -- start -----------------------------------------------------------------------

def test_start_builds_a_pkce_request_and_opens_a_loopback_listener(provider, vault):
    _hf_discovery(provider)
    out = oauth.start('higgsfield')
    q = _state_of(out['auth_url'])
    assert out['auth_url'].startswith('https://clerk.higgsfield.ai/oauth/authorize?')
    assert q['response_type'] == 'code' and q['client_id'] == 'cid-1'
    assert q['code_challenge_method'] == 'S256' and len(q['code_challenge']) == 43
    assert q['resource'] == 'https://mcp.higgsfield.ai/mcp' and q['state']
    assert out['redirect_uri'] == q['redirect_uri'] and out['redirect_uri'].startswith('http://127.0.0.1:')
    assert out['redirect_uri'].endswith('/callback')
    assert out['profile'] == 'desk-higgsfield'
    dcr = provider.to('/oauth/register')[0]['json']
    assert dcr['redirect_uris'] == [out['redirect_uri']] and dcr['token_endpoint_auth_method'] == 'none'
    # the answer names the flow, never the verifier or the state
    assert set(out) == {'flow_id', 'auth_url', 'redirect_uri', 'profile'}
    assert 'verifier' not in json.dumps(out)
    assert oauth.flow_status(out['flow_id'])['status'] == 'pending'


@pytest.mark.parametrize('service', ['higgsfield', 'x'])
def test_a_locked_vault_refuses_start_before_anything_is_opened(provider, vault, monkeypatch, service):
    """The token is written to the vault after the human finishes at the vendor. A locked vault is
    reported at start (Ron 2026-10-05: signed in at Higgsfield, then 'vault is locked'), before
    discovery, a listener or the sign-in page."""
    monkeypatch.setattr(secrets_store, 'is_locked', lambda: True)
    with pytest.raises(oauth.OAuthError) as e:
        oauth.start(service)
    assert e.value.code == 'vault_locked' and e.value.status == 409
    assert 'Unlock the vault' in str(e.value)
    assert provider.calls == [] and oauth._flows == {}


def test_the_start_route_answers_409_vault_locked_when_locked(client, provider, vault, monkeypatch):
    monkeypatch.setattr(secrets_store, 'is_locked', lambda: True)
    r = client.post('/api/desk/connect/higgsfield/start', json={'passcode': PASSCODE})
    assert r.status_code == 409 and r.get_json()['code'] == 'vault_locked'
    assert provider.calls == []


def test_a_failed_discovery_opens_nothing(provider, vault):
    provider.on('GET', PRM, (503, {}))
    with pytest.raises(oauth.OAuthError) as e:
        oauth.start('higgsfield')
    assert e.value.code == 'discovery_failed'
    assert oauth._flows == {}


def test_starting_again_replaces_the_pending_flow_and_frees_its_port(provider, vault):
    _hf_discovery(provider)
    first = oauth.start('higgsfield')
    second = oauth.start('higgsfield')
    assert first['flow_id'] != second['flow_id']
    assert oauth.flow_status(first['flow_id'])['status'] == 'unknown'
    assert len(oauth._flows) == 1


# -- callback --------------------------------------------------------------------

def test_callback_with_the_wrong_state_completes_nothing(provider, vault):
    _hf_discovery(provider)
    out = oauth.start('higgsfield')
    status, page = _hit(out['redirect_uri'], code='the-code', state='not-the-state')
    assert status == 400 and 'not valid any more' in page
    assert provider.to(HF_TOKEN) == []                       # no code was exchanged
    assert vault['writes'] == []
    assert oauth.flow_status(out['flow_id'])['status'] == 'pending'     # the real flow is still open


def test_a_failed_exchange_stores_nothing_and_closes_the_listener(provider, vault, logs):
    _hf_discovery(provider)
    provider.on('POST', HF_TOKEN, (400, {'error': 'invalid_request', 'error_description': 'bad code'}))
    out = oauth.start('higgsfield')
    q = _state_of(out['auth_url'])
    status, page = _hit(out['redirect_uri'], code='the-code', state=q['state'])
    assert status == 400 and 'did not accept' in page
    assert vault['writes'] == [] and oauth.status('higgsfield')['state'] == 'not_connected'
    flow = oauth.flow_status(out['flow_id'])
    assert flow['status'] == 'error' and 'did not accept' in flow['message']
    # the listener is gone: a second hit cannot reach it
    for _ in range(50):
        try:
            urllib.request.urlopen(out['redirect_uri'] + '?code=x&state=y', timeout=1)
        except urllib.error.HTTPError:
            pass
        except OSError:
            break
        threading.Event().wait(0.05)
    else:
        pytest.fail('the callback listener stayed open after the flow ended')


def test_the_provider_reporting_an_error_is_shown_not_exchanged(provider, vault):
    _hf_discovery(provider)
    out = oauth.start('higgsfield')
    q = _state_of(out['auth_url'])
    status, page = _hit(out['redirect_uri'], error='access_denied', state=q['state'])
    assert status == 400 and 'did not finish' in page
    assert provider.to(HF_TOKEN) == [] and vault['writes'] == []


def test_a_good_sign_in_is_stored_once_and_leaks_nothing(provider, vault, logs, capsys):
    out, q, status, page = _sign_in_higgsfield(provider)
    assert status == 200 and 'signed in' in page.lower()
    form = provider.to(HF_TOKEN)[0]['form']
    assert form['grant_type'] == 'authorization_code' and form['code'] == 'the-code'
    assert form['client_id'] == 'cid-1' and len(form['code_verifier']) >= 43
    assert form['resource'] == 'https://mcp.higgsfield.ai/mcp'
    # exactly one record, through the one write path
    assert vault['writes'] == ['oauth.higgsfield']
    rec = json.loads(vault['entries']['oauth.higgsfield']['value'])
    assert rec['access_token'] == ACCESS_1 and rec['refresh_token'] == REFRESH_1
    assert vault['entries']['oauth.higgsfield']['entry_type'] == secrets_store.ENTRY_TOKEN
    # the hint (listed metadata) carries state only, no token
    hint = vault['entries']['oauth.higgsfield']['hint']
    assert 'state=ok' in hint and 'refresh=yes' in hint and ACCESS_1 not in hint
    assert oauth.status('higgsfield') == {'state': 'connected', 'reason': None}
    flow = oauth.flow_status(out['flow_id'])
    assert flow['status'] == 'done'
    blob = json.dumps(flow) + page + json.dumps(oauth.overview()) + '\n'.join(logs) + capsys.readouterr().out
    for s in (ACCESS_1, REFRESH_1, q['state'], form['code_verifier']):
        assert s not in blob


def test_a_replayed_callback_does_nothing_the_second_time(provider, vault):
    out, q, *_ = _sign_in_higgsfield(provider)
    n = len(provider.to(HF_TOKEN))
    ok, _msg = oauth.complete({'state': q['state'], 'code': 'again'})
    assert ok is False and len(provider.to(HF_TOKEN)) == n and vault['writes'] == ['oauth.higgsfield']


# -- status ----------------------------------------------------------------------

def test_status_is_derived_from_the_vault_metadata(provider, vault):
    assert oauth.status('higgsfield')['state'] == 'not_connected'
    _sign_in_higgsfield(provider)
    assert oauth.status('higgsfield')['state'] == 'connected'
    vault['unreadable'].add('oauth.higgsfield')
    assert oauth.status('higgsfield')['state'] == 'needs_signin'
    vault['unreadable'].clear()
    vault['entries']['oauth.higgsfield']['hint'] = 'state=needs_signin exp=1 refresh=yes'
    assert oauth.status('higgsfield')['state'] == 'needs_signin'
    vault['entries']['oauth.higgsfield']['hint'] = 'state=ok exp=1 refresh=no'      # lapsed, nothing to refresh with
    assert oauth.status('higgsfield')['state'] == 'needs_signin'


# -- refresh ---------------------------------------------------------------------

def _expire(vault, name='oauth.higgsfield', *, left=-5):
    rec = json.loads(vault['entries'][name]['value'])
    import time
    rec['expires_at'] = int(time.time()) + left
    vault['entries'][name]['value'] = json.dumps(rec)


def test_a_fresh_token_is_returned_without_any_call(provider, vault):
    _sign_in_higgsfield(provider)
    n = len(provider.calls)
    assert oauth.access_token('higgsfield', consumer='t') == ACCESS_1
    assert len(provider.calls) == n


def test_an_expiring_token_is_refreshed_and_the_rotated_refresh_token_kept(provider, vault, logs):
    _sign_in_higgsfield(provider)
    _expire(vault, left=30)                                  # inside the refresh skew
    provider.on('POST', HF_TOKEN, (200, {'access_token': ACCESS_2, 'refresh_token': REFRESH_2, 'expires_in': 3600}))
    assert oauth.access_token('higgsfield', consumer='t') == ACCESS_2
    form = provider.to(HF_TOKEN)[-1]['form']
    assert form == {'grant_type': 'refresh_token', 'refresh_token': REFRESH_1, 'client_id': 'cid-1',
                    'resource': 'https://mcp.higgsfield.ai/mcp'}
    rec = json.loads(vault['entries']['oauth.higgsfield']['value'])
    assert rec['access_token'] == ACCESS_2 and rec['refresh_token'] == REFRESH_2
    assert oauth.status('higgsfield')['state'] == 'connected'
    assert ACCESS_2 not in '\n'.join(logs) and REFRESH_2 not in '\n'.join(logs)


def test_a_refresh_without_a_new_refresh_token_keeps_the_old_one(provider, vault):
    _sign_in_higgsfield(provider)
    _expire(vault)
    provider.on('POST', HF_TOKEN, (200, {'access_token': ACCESS_2, 'expires_in': 3600}))
    oauth.access_token('higgsfield', consumer='t')
    assert json.loads(vault['entries']['oauth.higgsfield']['value'])['refresh_token'] == REFRESH_1


def test_a_rejected_refresh_asks_for_a_new_sign_in(provider, vault):
    _sign_in_higgsfield(provider)
    _expire(vault)
    provider.on('POST', HF_TOKEN, (400, {'error': 'invalid_grant'}))
    with pytest.raises(oauth.OAuthError) as e:
        oauth.access_token('higgsfield', consumer='t')
    assert e.value.code == 'needs_signin'
    assert oauth.status('higgsfield')['state'] == 'needs_signin'
    with pytest.raises(oauth.OAuthError) as again:           # and it does not retry the dead token
        n = len(provider.calls)
        try:
            oauth.access_token('higgsfield', consumer='t')
        finally:
            assert len(provider.calls) == n
    assert again.value.code == 'needs_signin'


def test_a_refresh_that_cannot_reach_the_provider_does_not_lose_the_sign_in(provider, vault):
    _sign_in_higgsfield(provider)
    _expire(vault)
    provider.on('POST', HF_TOKEN, OSError('network down'))
    with pytest.raises(oauth.OAuthError) as e:
        oauth.access_token('higgsfield', consumer='t')
    assert e.value.code == 'unreachable'
    assert oauth.status('higgsfield')['state'] == 'connected'


def test_unattended_use_respects_the_entry_policy(provider, vault):
    _sign_in_higgsfield(provider)
    vault['entries']['oauth.higgsfield']['allow_unattended'] = False
    with pytest.raises(oauth.OAuthError) as e:
        oauth.access_token('higgsfield', consumer='t', unattended=True)
    assert e.value.code == 'unavailable'
    assert oauth.access_token('higgsfield', consumer='t', unattended=False) == ACCESS_1


# -- disconnect ------------------------------------------------------------------

def test_disconnect_revokes_both_tokens_and_deletes_the_entry(provider, vault):
    _sign_in_higgsfield(provider)
    provider.on('POST', HF_REVOKE, (200, {}))
    out = oauth.disconnect('higgsfield')
    assert out == {'service': 'higgsfield', 'deleted': True, 'revoked': True}
    sent = {c['form']['token']: c['form']['token_type_hint'] for c in provider.to(HF_REVOKE)}
    assert sent == {REFRESH_1: 'refresh_token', ACCESS_1: 'access_token'}
    assert 'oauth.higgsfield' not in vault['entries']
    assert oauth.status('higgsfield')['state'] == 'not_connected'


def test_disconnect_still_deletes_when_the_provider_refuses(provider, vault):
    _sign_in_higgsfield(provider)
    provider.on('POST', HF_REVOKE, (500, {}))
    out = oauth.disconnect('higgsfield')
    assert out['deleted'] is True and out['revoked'] is False
    assert 'oauth.higgsfield' not in vault['entries']


def test_disconnect_with_nothing_connected_is_harmless(provider, vault):
    assert oauth.disconnect('higgsfield') == {'service': 'higgsfield', 'deleted': False, 'revoked': None}


# -- X ---------------------------------------------------------------------------

def _x_app(vault, *, secret=False):
    vault['entries']['x.client-id'] = {'value': 'xclient123', 'hint': '', 'scope': 'global',
                                       'allow_unattended': True, 'entry_type': 'api_key'}
    if secret:
        vault['entries']['x.client-secret'] = {'value': X_SECRET, 'hint': '', 'scope': 'global',
                                               'allow_unattended': True, 'entry_type': 'api_key'}


@pytest.fixture
def x_port(monkeypatch):
    monkeypatch.setattr(oauth, 'X_CALLBACK_PORT', 0)     # the real one is fixed; a test must not fight a live Clayrune for it


def test_x_start_needs_the_app_credentials_first(provider, vault, x_port):
    with pytest.raises(oauth.OAuthError) as e:
        oauth.start('x')
    assert e.value.code == 'app_missing' and oauth._flows == {}


def test_x_start_asks_for_the_four_scopes_including_offline_access(provider, vault, x_port):
    _x_app(vault)
    out = oauth.start('x')
    q = _state_of(out['auth_url'])
    assert out['auth_url'].startswith('https://x.com/i/oauth2/authorize?')
    assert q['scope'].split() == ['tweet.read', 'tweet.write', 'users.read', 'offline.access']
    assert q['client_id'] == 'xclient123' and q['code_challenge_method'] == 'S256'
    assert out['profile'] == 'desk-x'


def test_x_public_client_sends_client_id_in_the_body(provider, vault, x_port):
    _x_app(vault)
    provider.on('POST', 'api.x.com/2/oauth2/token', (200, {'access_token': ACCESS_1, 'refresh_token': REFRESH_1}))
    out = oauth.start('x')
    q = _state_of(out['auth_url'])
    status, _page = _hit(out['redirect_uri'], code='c', state=q['state'])
    assert status == 200
    call = provider.to('api.x.com/2/oauth2/token')[0]
    assert 'Authorization' not in call['headers'] and call['form']['client_id'] == 'xclient123'
    rec = json.loads(vault['entries']['oauth.x']['value'])
    import time
    assert 7100 < rec['expires_at'] - time.time() <= 7200        # no expires_in: the documented two hours


def test_x_confidential_client_authenticates_with_basic(provider, vault, x_port):
    import base64
    _x_app(vault, secret=True)
    provider.on('POST', 'api.x.com/2/oauth2/token', (200, {'access_token': ACCESS_1, 'refresh_token': REFRESH_1,
                                                          'expires_in': 7200}))
    out = oauth.start('x')
    q = _state_of(out['auth_url'])
    _hit(out['redirect_uri'], code='c', state=q['state'])
    call = provider.to('api.x.com/2/oauth2/token')[0]
    assert call['headers']['Authorization'] == 'Basic ' + base64.b64encode(f'xclient123:{X_SECRET}'.encode()).decode()
    assert 'client_id' not in call['form']


def test_x_refresh_uses_the_stored_endpoint_and_rotates(provider, vault, x_port):
    _x_app(vault)
    provider.on('POST', 'api.x.com/2/oauth2/token', (200, {'access_token': ACCESS_1, 'refresh_token': REFRESH_1,
                                                          'expires_in': 7200}))
    out = oauth.start('x')
    _hit(out['redirect_uri'], code='c', state=_state_of(out['auth_url'])['state'])
    _expire(vault, 'oauth.x')
    provider.on('POST', 'api.x.com/2/oauth2/token', (200, {'access_token': ACCESS_2, 'refresh_token': REFRESH_2,
                                                          'expires_in': 7200}))
    assert oauth.x_token(consumer='t') == ACCESS_2
    assert json.loads(vault['entries']['oauth.x']['value'])['refresh_token'] == REFRESH_2


def test_x_port_in_use_is_said_in_plain_words(provider, vault, monkeypatch):
    import socket
    _x_app(vault)
    held = socket.socket()
    held.bind(('127.0.0.1', 0))
    held.listen(1)
    try:
        monkeypatch.setattr(oauth, 'X_CALLBACK_PORT', held.getsockname()[1])
        with pytest.raises(oauth.OAuthError) as e:
            oauth.start('x')
        assert e.value.code == 'port_busy' and 'in use' in str(e.value) and oauth._flows == {}
    finally:
        held.close()


def test_x_callback_url_is_the_fixed_loopback_address():
    assert oauth.x_redirect_uri() == f'http://127.0.0.1:{oauth.X_CALLBACK_PORT}/callback'


def test_a_pasted_x_token_is_used_until_a_sign_in_exists(provider, vault):
    vault['entries']['x.oauth-token'] = {'value': 'pasted-token', 'hint': '', 'scope': 'global',
                                         'allow_unattended': True, 'entry_type': 'api_key'}
    assert oauth.x_token(consumer='t') == 'pasted-token'
    vault['entries']['oauth.x'] = {'value': json.dumps({'access_token': ACCESS_1, 'refresh_token': REFRESH_1,
                                                        'expires_at': 4102444800}),
                                   'hint': 'state=ok exp=4102444800 refresh=yes', 'scope': 'global',
                                   'allow_unattended': True, 'entry_type': 'token'}
    assert oauth.x_token(consumer='t') == ACCESS_1


# -- guided key paste: test connection ---------------------------------------------

def _key(vault, name, value):
    vault['entries'][name] = {'value': value, 'hint': '', 'scope': 'global', 'allow_unattended': True,
                              'entry_type': 'api_key'}


def test_test_connection_makes_one_read_call_and_never_returns_the_key(provider, vault):
    _key(vault, 'gemini-api', GEMINI_KEY)
    provider.on('GET', 'generativelanguage.googleapis.com/v1beta/models', (200, {'models': []}))
    out = oauth.test_key('gemini')
    assert out == {'ok': True, 'message': 'Gemini accepted the key.'}
    assert len(provider.calls) == 1 and provider.calls[0]['headers']['x-goog-api-key'] == GEMINI_KEY
    assert GEMINI_KEY not in json.dumps(out)


def test_test_connection_openai_uses_a_bearer_header_and_reports_a_bad_key(provider, vault):
    _key(vault, 'openai-api', OPENAI_KEY)
    provider.on('GET', 'api.openai.com/v1/models', (401, {'error': {'message': f'Incorrect API key {OPENAI_KEY}'}}))
    out = oauth.test_key('openai')
    assert out['ok'] is False and 'did not accept the key' in out['message'] and OPENAI_KEY not in out['message']
    assert provider.calls[0]['headers']['Authorization'] == f'Bearer {OPENAI_KEY}'


def test_test_connection_without_a_saved_key_says_so(provider, vault):
    out = oauth.test_key('openai')
    assert out == {'ok': False, 'message': 'No OpenAI key is saved yet.'} and provider.calls == []


def test_test_connection_unreachable_is_reported(provider, vault):
    _key(vault, 'gemini-api', GEMINI_KEY)
    provider.on('GET', 'generativelanguage', OSError('no route'))
    out = oauth.test_key('gemini')
    assert out['ok'] is False and 'could not reach' in out['message']


# -- audit fixes -------------------------------------------------------------------

def test_disconnect_waits_for_an_in_flight_refresh_and_the_entry_stays_deleted(provider, vault):
    _sign_in_higgsfield(provider)
    _expire(vault)
    provider.on('POST', HF_REVOKE, (200, {}))
    in_refresh, release = threading.Event(), threading.Event()
    inner = oauth._http

    def gated(method, url, **kw):
        if url == HF_TOKEN:
            in_refresh.set()
            assert release.wait(10)
            return 200, {}, json.dumps({'access_token': ACCESS_2, 'refresh_token': REFRESH_2,
                                        'expires_in': 3600}).encode()
        return inner(method, url, **kw)

    oauth._http = gated
    try:
        refresher = threading.Thread(target=lambda: oauth.access_token('higgsfield', consumer='t'))
        refresher.start()
        assert in_refresh.wait(10)
        out: dict = {}
        remover = threading.Thread(target=lambda: out.update(oauth.disconnect('higgsfield')))
        remover.start()
        remover.join(0.3)
        assert remover.is_alive(), 'disconnect did not wait for the refresh in flight'
        release.set()
        refresher.join(10)
        remover.join(10)
    finally:
        oauth._http = inner
    assert out['deleted'] is True
    assert 'oauth.higgsfield' not in vault['entries']
    assert oauth.status('higgsfield')['state'] == 'not_connected'


def test_an_unattended_x_refresh_honours_the_client_secret_policy(provider, vault, x_port):
    _x_app(vault, secret=True)
    provider.on('POST', 'api.x.com/2/oauth2/token', (200, {'access_token': ACCESS_1, 'refresh_token': REFRESH_1,
                                                          'expires_in': 7200}))
    out = oauth.start('x')
    _hit(out['redirect_uri'], code='c', state=_state_of(out['auth_url'])['state'])
    _expire(vault, 'oauth.x')
    vault['entries']['x.client-secret']['allow_unattended'] = False
    n = len(provider.calls)
    with pytest.raises(oauth.OAuthError) as e:
        oauth.x_token(consumer='desk_publish', unattended=True)
    assert e.value.code == 'unavailable' and len(provider.calls) == n
    provider.on('POST', 'api.x.com/2/oauth2/token', (200, {'access_token': ACCESS_2, 'expires_in': 7200}))
    assert oauth.x_token(consumer='desk_publish', unattended=False) == ACCESS_2


def test_a_vault_failure_while_saving_ends_the_flow_and_frees_the_port(provider, vault, monkeypatch, logs):
    _hf_discovery(provider)
    provider.on('POST', HF_TOKEN, (200, {'access_token': ACCESS_1, 'refresh_token': REFRESH_1, 'expires_in': 3600}))

    def boom(*a, **k):
        raise PermissionError('disk is read-only')
    monkeypatch.setattr(secrets_store, 'set_secret', boom)
    out = oauth.start('higgsfield')
    status, page = _hit(out['redirect_uri'], code='c', state=_state_of(out['auth_url'])['state'])
    assert status == 400 and 'could not be saved' in page
    flow = oauth.flow_status(out['flow_id'])
    assert flow['status'] == 'error' and 'could not be saved' in flow['message']
    assert ACCESS_1 not in page + flow['message'] + '\n'.join(logs)
    for _ in range(50):                                      # the listener (and so the port) is released
        try:
            urllib.request.urlopen(out['redirect_uri'] + '?code=x&state=y', timeout=1)
        except urllib.error.HTTPError:
            pass
        except OSError:
            break
        threading.Event().wait(0.05)
    else:
        pytest.fail('the callback listener stayed open after the vault failed')


@pytest.mark.parametrize('status_,body', [
    (400, {'error': 'invalid_request'}), (401, {'error': 'invalid_client'}), (400, {}), (401, {'error': 'something_new'})])
def test_any_400_or_401_on_a_refresh_needs_a_new_sign_in(provider, vault, status_, body):
    _sign_in_higgsfield(provider)
    _expire(vault)
    provider.on('POST', HF_TOKEN, (status_, body))
    with pytest.raises(oauth.OAuthError) as e:
        oauth.access_token('higgsfield', consumer='t')
    assert e.value.code == 'needs_signin'
    assert oauth.status('higgsfield')['state'] == 'needs_signin'
    n = len(provider.calls)
    with pytest.raises(oauth.OAuthError):
        oauth.access_token('higgsfield', consumer='t')
    assert len(provider.calls) == n                          # not retried on every use


def test_a_5xx_on_a_refresh_is_retried_later_not_a_new_sign_in(provider, vault):
    _sign_in_higgsfield(provider)
    _expire(vault)
    provider.on('POST', HF_TOKEN, (503, {}))
    with pytest.raises(oauth.OAuthError) as e:
        oauth.access_token('higgsfield', consumer='t')
    assert e.value.code == 'refresh_failed' and oauth.status('higgsfield')['state'] == 'connected'


def test_discovery_refuses_a_non_https_endpoint(provider, vault):
    _hf_discovery(provider)
    provider.on('GET', AS_META, (200, {
        'issuer': 'https://clerk.higgsfield.ai', 'authorization_endpoint': 'https://clerk.higgsfield.ai/oauth/authorize',
        'token_endpoint': 'http://clerk.higgsfield.ai/oauth/token',
        'registration_endpoint': 'https://clerk.higgsfield.ai/oauth/register', 'code_challenge_methods_supported': ['S256']}))
    with pytest.raises(oauth.OAuthError) as e:
        oauth.start('higgsfield')
    assert e.value.code == 'insecure_endpoint' and oauth._flows == {}
    assert not provider.to('/oauth/register')                # nothing was registered, no code was ever sent


def test_discovery_refuses_a_non_https_resource_and_any_call_to_one(provider, vault):
    provider.on('GET', PRM, (200, {'resource': 'http://mcp.higgsfield.ai/mcp',
                                   'authorization_servers': ['https://clerk.higgsfield.ai']}))
    with pytest.raises(oauth.OAuthError) as e:
        oauth.start('higgsfield')
    assert e.value.code == 'insecure_endpoint'
    with pytest.raises(oauth.OAuthError) as e2:
        oauth._call('POST', 'http://evil.example/token', form={'code': 'x'})
    assert e2.value.code == 'insecure_endpoint'
    assert oauth._https_ok('http://127.0.0.1:53682/callback') and oauth._https_ok('http://localhost:1/x')
    assert not oauth._https_ok('ftp://a.example/') and not oauth._https_ok('')


# -- routes: human-only, passcode, no token in any answer ------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch, provider, vault):
    from mc.blueprints import local_auth
    from mc.blueprints.secrets_routes import _require_human_passcode as real
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    monkeypatch.setattr(desk_routes, '_require_human_passcode', real)
    local_auth._local_auth_set_passcode(PASSCODE)
    local_auth._LOCAL_AUTH_FAILS.clear()
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(load_projects_fn=lambda: [], load_project_fn=lambda pid: None,
                     store_path=tmp_path / 'desk.json', signals_path=tmp_path / 'sig.jsonl')
    app.register_blueprint(desk_routes.bp)
    return app.test_client()


@pytest.fixture
def unattended():
    from mc.state import agent_sessions
    snapshot = dict(agent_sessions)
    agent_sessions.clear()
    agent_sessions['dispatch-1'] = {'status': 'running', 'trigger_type': 'dispatch'}
    yield
    agent_sessions.clear()
    agent_sessions.update(snapshot)


def test_start_and_disconnect_refuse_an_unattended_caller(client, unattended, monkeypatch):
    monkeypatch.setattr(oauth, 'start', lambda *a, **k: pytest.fail('start reached an unattended caller'))
    monkeypatch.setattr(oauth, 'disconnect', lambda *a, **k: pytest.fail('disconnect reached an unattended caller'))
    for path in ('/api/desk/connect/higgsfield/start', '/api/desk/connect/higgsfield/disconnect'):
        r = client.post(path, json={'passcode': PASSCODE})
        assert r.status_code == 403 and 'needs a human' in r.get_json()['error']


def test_test_connection_refuses_an_unattended_caller(client, unattended, vault, monkeypatch):
    monkeypatch.setattr(oauth, 'test_key', lambda *a, **k: pytest.fail('a saved key was used by an unattended caller'))
    assert client.post('/api/desk/connect/gemini/test', json={}).status_code == 403


def test_start_and_disconnect_need_the_passcode_even_with_a_forged_origin(client, monkeypatch):
    monkeypatch.setattr(oauth, 'start', lambda *a, **k: pytest.fail('start ran without the passcode'))
    monkeypatch.setattr(oauth, 'disconnect', lambda *a, **k: pytest.fail('disconnect ran without the passcode'))
    forged = {'Origin': 'http://localhost:5199'}
    for path in ('/api/desk/connect/higgsfield/start', '/api/desk/connect/x/start',
                 '/api/desk/connect/higgsfield/disconnect'):
        r = client.post(path, json={}, headers=forged)
        assert r.status_code in (401, 403)
        r = client.post(path, json={'passcode': 'wrong-passcode'}, headers=forged)
        assert r.status_code in (401, 403)


def test_the_routes_never_return_a_token(client, provider, vault):
    bodies = []
    _hf_discovery(provider)
    provider.on('POST', HF_TOKEN, (200, {'access_token': ACCESS_1, 'refresh_token': REFRESH_1, 'expires_in': 86399}))
    provider.on('POST', HF_REVOKE, (200, {}))
    r = client.post('/api/desk/connect/higgsfield/start', json={'passcode': PASSCODE})
    assert r.status_code == 201
    bodies.append(r.get_data(as_text=True))
    start = r.get_json()
    q = _state_of(start['auth_url'])
    bodies.append(client.get(f"/api/desk/connect/flows/{start['flow_id']}").get_data(as_text=True))
    _hit(start['redirect_uri'], code='c', state=q['state'])
    done = client.get(f"/api/desk/connect/flows/{start['flow_id']}")
    assert done.get_json()['status'] == 'done'
    bodies.append(done.get_data(as_text=True))
    ov = client.get('/api/desk/connect/status')
    assert ov.get_json()['higgsfield']['state'] == 'connected'
    bodies.append(ov.get_data(as_text=True))
    _key(vault, 'gemini-api', GEMINI_KEY)
    provider.on('GET', 'generativelanguage', (200, {}))
    bodies.append(client.post('/api/desk/connect/gemini/test', json={}).get_data(as_text=True))
    r = client.post('/api/desk/connect/higgsfield/disconnect', json={'passcode': PASSCODE})
    assert r.status_code == 200 and r.get_json()['revoked'] is True
    bodies.append(r.get_data(as_text=True))
    blob = '\n'.join(bodies)
    for s in SENTINELS:
        assert s not in blob, f'a secret value leaked into a response: {s}'
    # the state rides in the sign-in link by design (that is how it reaches the provider);
    # nothing after the start response may hand it back
    assert q['state'] not in '\n'.join(bodies[1:])


def test_overview_lists_states_and_x_app_facts_without_values(client, vault):
    _x_app(vault)
    _key(vault, 'openai-api', OPENAI_KEY)
    ov = client.get('/api/desk/connect/status').get_json()
    assert ov['higgsfield']['state'] == 'not_connected'
    assert ov['x']['state'] == 'not_connected' and ov['x']['app'] == {'client_id': True, 'client_secret': False}
    assert ov['x']['callback_url'] == oauth.x_redirect_uri()
    assert ov['x']['scopes'] == ['tweet.read', 'tweet.write', 'users.read', 'offline.access']
    assert ov['openai']['saved'] is True and ov['gemini']['saved'] is False
    assert OPENAI_KEY not in json.dumps(ov)
