"""A sign-in held in server memory until the Save (`mc/desk_oauth_hold.py`; Dave 2026-10-05,
option B, after Ron: "if step 3 is the login details, all options should be covered there").

The Connect flow's Details step signs in BEFORE the one Save. Pinned:

  * the callback of a held sign-in stores NOTHING: no vault entry, no Desk account; the poll
    says `held`, the callback page says to go back and press Save;
  * a back-out (cancel) leaves no vault entry and no account, and revokes the token at the
    vendor; a wrong claim cancels nothing; expiry clears the held token the same way;
  * the Save claims it: account + Client ID + token written in ONE commit (X and Higgsfield),
    a failure part-way undoes all of it and keeps the held sign-in for a retry, and a claim
    that does not fit refuses before anything is written;
  * a restart (the held memory is gone) refuses the Save with "sign in again";
  * starting is human-only and passcode-gated; no response, poll or log line carries a token.

NO REAL PROVIDER CALL: `desk_oauth._http` is the scripted fake of `test_desk_oauth.py`; the
vault is the real file-backed one in a temp dir, so "nothing was written" is checked against
the real thing.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc import desk_oauth as oauth  # noqa: E402
from mc import desk_oauth_hold as hold  # noqa: E402
from mc import secrets_store  # noqa: E402
from tests.test_desk_connect import PASSCODE, env  # noqa: E402,F401
from tests.test_desk_oauth import (ACCESS_1, HF_REVOKE, HF_TOKEN, REFRESH_1, SENTINELS, FakeProvider,  # noqa: E402,F401
                                   _clean_flows, _hf_discovery, _hit, _state_of)

X_TOKEN = 'api.x.com/2/oauth2/token'
X_REVOKE = 'api.x.com/2/oauth2/revoke'
CLIENT_ID = 'x-client-id-1234'
HF_URL = 'https://higgsfield.ai'
X_URL = 'https://x.com'


class Profiles:
    """A stand-in for the pane's saved-profile directory, so no test reads or deletes the operator's
    real ~/.clayrune/browser_profiles_named. `have` is what is on disk; `forgotten` what a flow removed."""

    def __init__(self):
        self.have: set[str] = set()
        self.forgotten: list[str] = []


@pytest.fixture()
def profiles(monkeypatch):
    from mc import desk_oauth_profile
    ps = Profiles()
    monkeypatch.setattr(desk_oauth_profile, 'existed', lambda name: name in ps.have)

    def forget(name):
        ps.forgotten.append(name)
        ps.have.discard(name)
        return True
    monkeypatch.setattr(desk_oauth_profile, 'forget', forget)
    return ps


@pytest.fixture()
def api(env, monkeypatch, profiles):
    """The Connect + held-sign-in routes over the real (temp) vault and Desk store, with a
    scripted vendor."""
    from mc.blueprints import desk_connect_routes, desk_held_signin_routes, desk_routes
    p = FakeProvider()
    monkeypatch.setattr(oauth, '_http', p)
    # conftest blanks `_meta` so no test reads the operator's real vault; this vault is the temp one `env` made
    monkeypatch.setattr(oauth, '_meta', lambda service, account_id=None: next(
        (m for m in secrets_store.list_secrets() if m['name'] == oauth.vault_name(service, account_id)), None))
    p.on('POST', HF_REVOKE, (200, {}))
    p.on('POST', X_REVOKE, (200, {}))
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(desk_connect_routes.bp)
    app.register_blueprint(desk_held_signin_routes.bp)
    app.register_blueprint(desk_routes.bp)      # the flow poll
    hold._forget_all_for_tests()
    _wait_x_port_free()
    yield app.test_client(), p
    hold._forget_all_for_tests()
    _wait_x_port_free()


def _wait_x_port_free():
    """A finished flow closes its listener on a background thread; X's callback port is fixed,
    so the next test must wait for it."""
    import socket
    deadline = time.time() + 5
    while time.time() < deadline:
        with socket.socket() as sk:
            if sk.connect_ex(('127.0.0.1', oauth.X_CALLBACK_PORT)) != 0:
                return
        time.sleep(0.05)


def _vault_names():
    return sorted(s['name'] for s in secrets_store.list_secrets())


def _accounts():
    from mc import desk
    return desk._read_store().get('accounts') or {}


def _start_hf(client, p):
    _hf_discovery(p)
    p.on('POST', HF_TOKEN, (200, {'access_token': ACCESS_1, 'refresh_token': REFRESH_1, 'expires_in': 86399}))
    r = client.post('/api/desk/connect/higgsfield/start-held', json={'passcode': PASSCODE})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _start_x(client, p, **hold_fields):
    p.on('POST', X_TOKEN, (200, {'access_token': ACCESS_1, 'refresh_token': REFRESH_1, 'expires_in': 7200}))
    r = client.post('/api/desk/connect/x/start-held', json={'passcode': PASSCODE, 'hold': {'client_id': CLIENT_ID, **hold_fields}})
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _sign_in(out):
    q = _state_of(out['auth_url'])
    return _hit(out['redirect_uri'], code='the-code', state=q['state'])


def _save(client, url, method='oauth', fields=None, held=None, rid='req-held-0001', **over):
    draft = {'url': url, 'method': method, 'fields': fields or {}}
    if held is not None:
        draft['held'] = held
    return client.post('/api/desk/connect/commit', json={'request_id': rid, 'draft': draft, 'passcode': PASSCODE, **over})


def _held(out):
    return {'flow_id': out['flow_id'], 'claim': out['claim']}


X_FIELDS = {'identity': '@ronbuilds', 'client_id': CLIENT_ID}


# -- the callback holds, it does not store ----------------------------------------------

def test_a_held_sign_in_stores_nothing_and_says_to_press_save(api):
    client, p = api
    out = _start_hf(client, p)
    assert set(out) == {'flow_id', 'auth_url', 'redirect_uri', 'profile', 'claim', 'hold_ttl_s'}
    assert out['hold_ttl_s'] <= 15 * 60 and out['hold_ttl_s'] <= oauth.FLOW_TTL_S
    status, page = _sign_in(out)
    assert status == 200 and 'press Save' in page and 'Nothing is saved' in page
    assert _vault_names() == [] and _accounts() == {}
    poll = client.get(f'/api/desk/connect/flows/{out["flow_id"]}').get_json()
    assert poll['status'] == 'held' and poll['held_ttl_s'] > 0
    blob = json.dumps(poll) + page
    assert not any(s in blob for s in SENTINELS) and out['claim'] not in blob


def test_start_held_is_human_only_and_needs_the_passcode(api, monkeypatch):
    client, p = api
    _hf_discovery(p)
    assert client.post('/api/desk/connect/higgsfield/start-held', json={}).status_code == 403
    assert client.post('/api/desk/connect/higgsfield/start-held', json={'passcode': 'wrong-pass'}).status_code == 403
    assert not p.to('oauth/register') and oauth._flows == {}
    from mc.blueprints import desk_held_signin_routes as r
    monkeypatch.setattr(r, 'is_unattended_caller', lambda: True)
    assert client.post('/api/desk/connect/higgsfield/start-held', json={'passcode': PASSCODE}).status_code == 403
    assert not p.to('oauth/register')


# -- back out ---------------------------------------------------------------------------

def test_backing_out_leaves_no_vault_entry_and_no_account_for_x(api):
    client, p = api
    out = _start_x(client, p)
    _sign_in(out)
    assert hold.alive(out['flow_id'])
    r = client.post(f'/api/desk/connect/flows/{out["flow_id"]}/cancel', json={'claim': out['claim']})
    assert r.get_json() == {'ok': True}
    assert _vault_names() == [] and _accounts() == {}        # the commit-first design left both behind
    assert hold.alive(out['flow_id']) == 0
    assert client.get(f'/api/desk/connect/flows/{out["flow_id"]}').get_json()['status'] == 'unknown'
    assert [c['form']['token_type_hint'] for c in p.to(X_REVOKE)] == ['refresh_token', 'access_token']
    assert _save(client, X_URL, fields=X_FIELDS, held=_held(out)).status_code == 409      # nothing left to claim


def test_a_cancel_with_a_wrong_claim_changes_nothing(api):
    client, p = api
    out = _start_hf(client, p)
    _sign_in(out)
    r = client.post(f'/api/desk/connect/flows/{out["flow_id"]}/cancel', json={'claim': 'not-the-claim'})
    assert r.get_json() == {'ok': False} and hold.alive(out['flow_id'])
    assert not p.to(HF_REVOKE)


def test_expiry_clears_the_held_token_and_revokes_it(api, monkeypatch):
    client, p = api
    monkeypatch.setattr(oauth, 'HOLD_TTL_S', 0.15)
    out = _start_hf(client, p)
    _sign_in(out)
    deadline = time.time() + 5
    while time.time() < deadline and not p.to(HF_REVOKE):
        time.sleep(0.05)
    assert hold.alive(out['flow_id']) == 0 and len(p.to(HF_REVOKE)) == 2
    assert client.get(f'/api/desk/connect/flows/{out["flow_id"]}').get_json()['status'] == 'unknown'
    assert _save(client, HF_URL, held=_held(out)).status_code == 409
    assert _vault_names() == []


def test_a_second_held_sign_in_of_the_same_service_replaces_the_first(api):
    client, p = api
    first = _start_hf(client, p)
    _sign_in(first)
    second = _start_hf(client, p)
    assert hold.alive(first['flow_id']) == 0 and len(p.to(HF_REVOKE)) == 2
    assert second['flow_id'] != first['flow_id']


# -- the Save claims it -----------------------------------------------------------------

def test_save_writes_higgsfield_sign_in_in_one_commit_and_starts_nothing(api):
    client, p = api
    out = _start_hf(client, p)
    _sign_in(out)
    starts_before = len(p.to('/oauth/register'))
    r = _save(client, HF_URL, held=_held(out))
    body = r.get_json()
    assert r.status_code == 201, body
    assert _vault_names() == ['oauth.higgsfield']
    assert 'signin' not in body and body['status']['state'] != 'not_connected'
    assert len(p.to('/oauth/register')) == starts_before               # no second sign-in was opened
    assert hold.alive(out['flow_id']) == 0 and not p.to(HF_REVOKE)     # consumed, not revoked
    assert not any(s in json.dumps(body) for s in SENTINELS)
    assert _save(client, HF_URL, held=_held(out), rid='req-held-0002').status_code == 409   # single use


def test_save_writes_x_account_client_id_and_token_atomically(api):
    client, p = api
    out = _start_x(client, p, client_secret='x-secret-9876')
    assert out['account_id'].startswith('acct-')
    _sign_in(out)
    r = _save(client, X_URL, fields={**X_FIELDS, 'client_secret': 'x-secret-9876'}, held=_held(out))
    body = r.get_json()
    assert r.status_code == 201, body
    assert body['account_id'] == out['account_id']                     # the account the sign-in was started for
    assert _vault_names() == ['oauth.x', 'x.client-id', 'x.client-secret']
    assert list(_accounts()) == [out['account_id']]
    assert 'signin' not in body
    # the code exchange used the client typed in the form, which was not stored yet
    exch = p.to(X_TOKEN)[0]
    assert exch['headers']['Authorization'].startswith('Basic ')
    assert not any(s in json.dumps(body) for s in SENTINELS + ('x-secret-9876',))


def test_a_failure_part_way_undoes_every_write_and_keeps_the_held_sign_in(api, monkeypatch):
    client, p = api
    out = _start_x(client, p)
    _sign_in(out)

    def boom(*_a, **_k):
        raise RuntimeError('the vault went away')
    real_store = oauth._store_record
    monkeypatch.setattr(oauth, '_store_record', boom)
    r = _save(client, X_URL, fields=X_FIELDS, held=_held(out))
    assert r.status_code >= 400
    assert _vault_names() == [] and _accounts() == {}                  # client id and account came back out
    assert hold.alive(out['flow_id'])                                  # still held: press Save again
    monkeypatch.setattr(oauth, '_store_record', real_store)
    r = _save(client, X_URL, fields=X_FIELDS, held=_held(out), rid='req-held-0002')
    assert r.status_code == 201, r.get_json()


def test_a_claim_that_does_not_fit_writes_nothing(api):
    client, p = api
    out = _start_x(client, p)
    _sign_in(out)
    bad = _save(client, X_URL, fields=X_FIELDS, held={'flow_id': out['flow_id'], 'claim': 'nope'})
    assert bad.status_code == 409 and bad.get_json()['code'] == 'hold_missing'
    assert _vault_names() == [] and _accounts() == {} and hold.alive(out['flow_id'])
    other = _save(client, HF_URL, held=_held(out), rid='req-held-0002')       # a claim for the other service
    assert other.status_code == 409 and _vault_names() == []
    assert _save(client, X_URL, fields=X_FIELDS, held={'flow_id': out['flow_id']}, rid='req-held-0003').status_code == 400


def test_a_restart_forgets_a_held_sign_in_and_the_save_says_sign_in_again(api):
    client, p = api
    out = _start_hf(client, p)
    _sign_in(out)
    hold._forget_all_for_tests()                                       # the server restarted: memory is gone
    with oauth._lock:
        oauth._flows.clear()
    r = _save(client, HF_URL, held=_held(out))
    assert r.status_code == 409 and 'Sign in again' in r.get_json()['error']
    assert 'sign in again' in client.get(f'/api/desk/connect/flows/{out["flow_id"]}').get_json()['message'].lower()
    assert _vault_names() == []


def test_a_held_claim_is_refused_for_a_method_that_has_no_sign_in(api):
    client, _p = api
    r = _save(client, 'https://aistudio.google.com/app/apikey', method='api_key', fields={'secret': 'k' * 20},
              held={'flow_id': 'a', 'claim': 'b'})
    assert r.status_code == 400 and _vault_names() == []


def test_higgsfield_already_signed_in_is_refused_before_a_sign_in_opens(api):
    client, p = api
    oauth._store_record('higgsfield', {'v': 1, 'access_token': 'a', 'refresh_token': 'r', 'expires_at': 2 ** 31})
    assert oauth.status('higgsfield')['state'] == 'connected'
    _hf_discovery(p)
    r = client.post('/api/desk/connect/higgsfield/start-held', json={'passcode': PASSCODE})
    assert r.status_code == 409 and r.get_json()['code'] == 'already_signed_in'
    assert not p.to('/oauth/register')
