"""A login typed on the Details step of a sign-in method is stored by the connection's ONE Save
(backlog caf2d45c; `draft.new_login`, `signin_login_store.clean_for_save` / `write_with_undo`,
`provider_commit.apply`).

Before this, the Details step stored the typed login with its own passcode (`store-login`) and the
Save then asked again. Pinned:

  * one passcode check covers the typed login AND the connection (a held sign-in, the account);
  * atomic: if the connection's writes fail the login is removed again, and if the login's write
    fails the connection's writes come back out; the held sign-in stays held for a retry;
  * a bad login, a taken name, a method with no sign-in page, a wrong passcode: refused with
    nothing written (and the first two before the passcode is even asked);
  * the password is in no response, no log line, no remembered result and no draft fingerprint.

NO REAL PROVIDER CALL: the scripted vendor and the real (temp) vault of `test_desk_oauth_hold.py`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from mc import desk_oauth as oauth  # noqa: E402
from mc import desk_oauth_hold as hold  # noqa: E402
from mc import secrets_store  # noqa: E402
from mc.desk_connect import commit as _commit  # noqa: E402
from mc.desk_connect import provider_commit  # noqa: E402
from mc.desk_connect import signin_login_store as _logins  # noqa: E402
from tests.test_desk_connect import PASSCODE, env  # noqa: E402,F401  (fixture)
from tests.test_desk_oauth import SENTINELS, _clean_flows  # noqa: E402,F401  (autouse: ends the flows and listeners a test started)
from tests.test_desk_oauth_hold import (HF_URL, X_FIELDS, X_URL, _accounts, _held, _sign_in,  # noqa: E402
                                        _start_hf, _start_x, _vault_names, api, profiles)  # noqa: F401  (fixtures)

PW = 'hunter2-typed-on-details-9931'
LOGIN = 'higgsfield.login'
USER = 'ron@example.test'


def _login(**kw):
    return {'name': LOGIN, 'username': USER, 'value': PW, 'description': 'typed on Details', 'allow_unattended': True, **kw}


def _save_with_login(client, url, new_login, *, rid='req-login-0001', **kw):
    draft = {'url': url, 'method': 'oauth', 'fields': kw.pop('fields', {}), 'new_login': new_login}
    if 'held' in kw:
        draft['held'] = kw.pop('held')
    return client.post('/api/desk/connect/commit', json={'request_id': rid, 'draft': draft, 'passcode': kw.pop('passcode', PASSCODE)})


@pytest.fixture()
def passcodes(monkeypatch):
    """Count every passcode check the Connect route makes."""
    from mc.blueprints import desk_connect_routes as routes
    calls = {'n': 0}
    real = routes._require_human_passcode

    def counting(data):
        calls['n'] += 1
        return real(data)
    monkeypatch.setattr(routes, '_require_human_passcode', counting)
    return calls


def _entry(name):
    return next((s for s in secrets_store.list_secrets() if s['name'] == name), None)


# -- one passcode, both writes ------------------------------------------------------------

def test_one_passcode_stores_the_typed_login_and_the_held_sign_in(api, passcodes, capsys):
    client, p = api
    out = _start_hf(client, p)
    _sign_in(out)
    r = _save_with_login(client, HF_URL, _login(), held=_held(out))
    body = r.get_json()
    assert r.status_code == 201, body
    assert passcodes['n'] == 1                                            # the whole Save: one prompt
    assert _vault_names() == [LOGIN, 'oauth.higgsfield']
    entry = _entry(LOGIN)
    assert entry is not None
    assert entry['entry_type'] == secrets_store.ENTRY_LOGIN and entry['username'] == USER and entry['scope'] == 'global'
    assert body['login'] == {'name': LOGIN, 'entry_type': secrets_store.ENTRY_LOGIN, 'allow_unattended': True}
    assert secrets_store.get_secret_value(LOGIN, consumer='test') == PW          # the vault holds it, whole
    blob = json.dumps(body) + capsys.readouterr().out
    assert not any(s in blob for s in SENTINELS + (PW,))


def test_a_typed_login_with_a_save_that_starts_the_sign_in_is_stored_in_the_same_commit(api, passcodes, monkeypatch):
    client, _p = api
    monkeypatch.setattr(provider_commit, 'follow', lambda clean, applied: {})      # the sign-in's network is not under test
    r = _save_with_login(client, HF_URL, _login())
    assert r.status_code == 201, r.get_json()
    assert passcodes['n'] == 1 and LOGIN in _vault_names()


# -- atomic ------------------------------------------------------------------------------

def test_if_the_connection_fails_the_typed_login_is_removed_and_the_sign_in_stays_held(api, monkeypatch):
    client, p = api
    out = _start_x(client, p)
    _sign_in(out)
    real_store = oauth._store_record

    def boom(*_a, **_k):
        raise RuntimeError('the vault went away')
    monkeypatch.setattr(oauth, '_store_record', boom)
    r = _save_with_login(client, X_URL, _login(name='x.login'), fields=X_FIELDS, held=_held(out))
    assert r.status_code >= 400
    assert _vault_names() == [] and _accounts() == {}                     # the login, client id and account all came back out
    assert hold.alive(out['flow_id'])
    monkeypatch.setattr(oauth, '_store_record', real_store)
    r = _save_with_login(client, X_URL, _login(name='x.login'), fields=X_FIELDS, held=_held(out), rid='req-login-0002')
    assert r.status_code == 201, r.get_json()                             # the retry writes both
    assert _vault_names() == ['oauth.x', 'x.client-id', 'x.login']


def test_if_the_login_cannot_be_written_the_connection_is_not_kept_either(api, monkeypatch):
    client, p = api
    out = _start_x(client, p)
    _sign_in(out)

    def refuse(login):
        raise _logins.LoginError('the login could not be stored: the vault refused it', 400, 'vault_refused')
    real_write = _logins.write
    monkeypatch.setattr(_logins, 'write', refuse)
    r = _save_with_login(client, X_URL, _login(name='x.login'), fields=X_FIELDS, held=_held(out))
    assert r.status_code == 400 and r.get_json()['code'] == 'vault_refused'
    assert _vault_names() == [] and _accounts() == {}                     # the Client ID and the Desk account came back out
    assert hold.alive(out['flow_id'])
    monkeypatch.setattr(_logins, 'write', real_write)
    r = _save_with_login(client, X_URL, _login(name='x.login'), fields=X_FIELDS, held=_held(out), rid='req-login-0002')
    assert r.status_code == 201, r.get_json()


def test_a_login_that_cannot_be_removed_is_named_not_hidden(api, monkeypatch):
    client, p = api
    out = _start_x(client, p)
    _sign_in(out)

    def boom(*_a, **_k):
        raise RuntimeError('the vault went away')
    monkeypatch.setattr(oauth, '_store_record', boom)
    monkeypatch.setattr(_logins, 'remove', lambda name: False)
    r = _save_with_login(client, X_URL, _login(name='x.login'), fields=X_FIELDS, held=_held(out))
    assert r.status_code >= 400
    assert 'x.login' in r.get_json()['error'] and 'by hand' in r.get_json()['error']
    assert PW not in json.dumps(r.get_json())


# -- refused, nothing written ---------------------------------------------------------------

def test_a_taken_name_is_refused_before_the_passcode_is_asked(api, passcodes):
    client, _p = api
    secrets_store.set_secret(LOGIN, 'someone-elses-password', username='other', entry_type=secrets_store.ENTRY_LOGIN)
    r = _save_with_login(client, HF_URL, _login())
    assert r.status_code == 409 and r.get_json()['code'] == 'secret_exists'
    assert passcodes['n'] == 0
    assert secrets_store.get_secret_value(LOGIN, consumer='test') == 'someone-elses-password'      # not replaced


@pytest.mark.parametrize('bad', [
    _login(username=''), _login(value=''), _login(name='Bad Name'), _login(name='oauth.x.test'), {**_login(), 'extra': 'x'},
    'a string', ['a', 'list']])
def test_a_bad_login_is_refused_before_the_passcode_is_asked(api, passcodes, bad):
    client, _p = api
    r = _save_with_login(client, HF_URL, bad)
    assert r.status_code == 400
    assert passcodes['n'] == 0 and _vault_names() == []
    assert PW not in json.dumps(r.get_json())


def test_a_method_with_no_sign_in_page_takes_no_typed_login(api, passcodes):
    client, _p = api
    r = client.post('/api/desk/connect/commit', json={'request_id': 'req-login-0003', 'passcode': PASSCODE, 'draft': {
        'url': 'https://aistudio.google.com/app/apikey', 'method': 'api_key', 'fields': {'secret': 'k' * 20}, 'new_login': _login()}})
    assert r.status_code == 400 and r.get_json()['code'] == 'method_not_available'
    assert passcodes['n'] == 0 and _vault_names() == []


def test_a_wrong_passcode_writes_nothing(api):
    client, p = api
    out = _start_hf(client, p)
    _sign_in(out)
    r = _save_with_login(client, HF_URL, _login(), held=_held(out), passcode='not-the-passcode')
    assert r.status_code in (401, 403)
    assert _vault_names() == [] and _accounts() == {} and hold.alive(out['flow_id'])


def test_an_unattended_caller_cannot_store_it(api, monkeypatch):
    client, _p = api
    from mc.blueprints import desk_connect_routes as routes
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: True)
    r = _save_with_login(client, HF_URL, _login())
    assert r.status_code == 403 and _vault_names() == []


# -- the password goes to the vault and nowhere else ----------------------------------------

def test_the_password_is_not_in_the_fingerprint_or_the_remembered_result(api, monkeypatch):
    client, _p = api
    monkeypatch.setattr(provider_commit, 'follow', lambda clean, applied: {})
    r = _save_with_login(client, HF_URL, _login())
    assert r.status_code == 201, r.get_json()
    blob = json.dumps({k: v for k, v in _commit._done.items()}, default=str)
    assert PW not in blob and USER not in blob
    clean = {'new_login': _logins.clean(_login()), 'method': 'oauth'}
    other = {'new_login': _logins.clean(_login(value='a-different-password')), 'method': 'oauth'}
    assert _commit._fingerprint(clean) == _commit._fingerprint(other)      # counted by name and type, never by value
    again = _save_with_login(client, HF_URL, _login())                     # a lost response replayed: the first answer, no second write
    assert again.status_code == 200 and again.get_json()['duplicate'] is True and PW not in json.dumps(again.get_json())


def test_save_for_agents_takes_no_sign_in_login(api, passcodes):
    client, _p = api
    r = client.post('/api/desk/connect/commit', json={'request_id': 'req-login-0004', 'passcode': PASSCODE, 'draft': {
        'url': 'https://plausible.io/mysite', 'method': 'save_for_agents', 'name': 'Plausible', 'new_login': _login()}})
    assert r.status_code == 400 and 'sign-in login' in r.get_json()['error']
    assert passcodes['n'] == 0 and _vault_names() == []
