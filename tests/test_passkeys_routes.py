"""Enrollment, list and revoke routes (mc/blueprints/passkey_routes.py).

Slice 1 acceptance from docs/PASSKEYS_SPEC.md: forged headers, LAN/tunnel
bootstrap refusal, duplicate/replayed registration, corrupt store, concurrent
writes, revocation and restart. Registration bytes come from a software
authenticator (tests/passkeys_fake_authenticator.py): these tests prove the
server's protocol logic, not a real authenticator, browser or biometric.
"""
from __future__ import annotations

import base64
import json
import sys
import threading
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mc.blueprints import local_auth, passkey_routes
from mc.passkeys import audit, ceremony, challenges, store

ORIGIN = 'http://localhost:5199'
HOST_URL = 'http://localhost:5199'
GOOD = {'Origin': ORIGIN}
PASSCODE = 'unlock1234'
OPTIONS = '/api/passkeys/register/options'
FINISH = '/api/passkeys/register/finish'


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    local_auth._local_auth_set_passcode(PASSCODE)
    local_auth._LOCAL_AUTH_FAILS.clear()
    local_auth._LOCAL_AUTH_IN_FLIGHT.clear()
    challenges.STORE.clear()
    passkey_routes.wire(port=5199)
    yield
    challenges.STORE.clear()
    local_auth._LOCAL_AUTH_FAILS.clear()


@pytest.fixture
def authn():
    pytest.importorskip('webauthn')
    pytest.importorskip('cbor2')
    from passkeys_fake_authenticator import FakeAuthenticator
    return FakeAuthenticator


def make_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(passkey_routes.bp)
    return app


@pytest.fixture
def app():
    return make_app()


def browser(app, addr='127.0.0.1'):
    c = app.test_client()
    c.environ_base['REMOTE_ADDR'] = addr
    return c


def call(c, method, path, body=None, headers=GOOD, base_url=HOST_URL):
    return getattr(c, method)(path, json=body, headers=headers, base_url=base_url)


def start(c, label=None, passcode=PASSCODE, **kw):
    body = {'passcode': passcode}
    if label is not None:
        body['label'] = label
    return call(c, 'post', OPTIONS, body, **kw)


def enroll(c, authenticator, label='Laptop', **create_kw):
    r = start(c, label)
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    cred = authenticator.create(j['options'], create_kw.pop('origin', ORIGIN), **create_kw)
    return j, cred, call(c, 'post', FINISH, {'ceremony_id': j['ceremony_id'], 'credential': cred})


def passkey_ids():
    return store.active_credential_ids()


# ── the happy path and the options profile ──────────────────────────────────

def test_enroll_list_and_options_profile(app, authn):
    c = browser(app)
    j, cred, fin = enroll(c, authn(), 'My laptop')
    assert fin.status_code == 200 and fin.get_json()['ok'] is True
    opts = j['options']
    assert opts['authenticatorSelection']['userVerification'] == 'required'
    assert opts['authenticatorSelection']['residentKey'] == 'preferred'
    assert opts['attestation'] == 'none'
    assert opts['rp']['id'] == 'localhost'
    assert len(base64.urlsafe_b64decode(opts['challenge'] + '==')) == 32
    assert opts['excludeCredentials'] == []

    listing = call(c, 'get', '/api/passkeys').get_json()
    assert listing['available'] and listing['can_enroll_here'] and listing['enrolled']
    row, = listing['credentials']
    assert row['label'] == 'My laptop' and row['rp_id'] == 'localhost' and row['revoked_at'] is None
    assert row['id'] == cred['id']
    assert 'public_key' not in row and 'sign_count' not in row


def test_second_enrollment_excludes_the_first_and_shares_the_owner_handle(app, authn):
    c = browser(app)
    j1, cred1, fin1 = enroll(c, authn(), 'one')
    assert fin1.status_code == 200
    j2, _cred2, fin2 = enroll(c, authn(), 'two')
    assert fin2.status_code == 200
    assert [d['id'] for d in j2['options']['excludeCredentials']] == [cred1['id']]
    assert j2['options']['user']['id'] == j1['options']['user']['id']
    assert len(passkey_ids()) == 2


def test_abandoned_options_call_enrolls_nothing(app, authn):
    c = browser(app)
    assert start(c).status_code == 200
    assert store.status() == {'enrolled': False, 'active_count': 0, 'policy_epoch': 0}
    assert not store.registry_path().exists()


def test_label_is_cleaned_and_bounded(app, authn):
    c = browser(app)
    _j, _cred, fin = enroll(c, authn(), '  \x00Desk\x07top ' + 'x' * 200)
    assert fin.status_code == 200
    label = fin.get_json()['credential']['label']
    assert label.startswith('Desktop') and len(label) <= store.MAX_LABEL_LEN
    assert start(c, label=123).status_code == 400


# ── host-only: forged headers, LAN, tunnel ──────────────────────────────────

REFUSED = [
    ('lan peer', dict(addr='192.168.1.20'), GOOD, HOST_URL, 'not_loopback'),
    ('lan peer forging loopback xff', dict(addr='192.168.1.20'),
     dict(GOOD, **{'X-Forwarded-For': '127.0.0.1'}), HOST_URL, 'not_loopback'),
    ('tunnel: loopback peer + CF Access email', {}, dict(GOOD, **{
        'Cf-Access-Authenticated-User-Email': 'owner@example.com'}), HOST_URL, 'proxied'),
    ('tunnel: loopback peer + CF JWT', {}, dict(GOOD, **{'Cf-Access-Jwt-Assertion': 'a.b.c'}),
     HOST_URL, 'proxied'),
    ('proxy: x-forwarded-host', {}, dict(GOOD, **{'X-Forwarded-Host': 'x.example'}), HOST_URL, 'proxied'),
    ('tunnel hostname as Host', {}, {'Origin': 'https://abc.trycloudflare.com'},
     'https://abc.trycloudflare.com', 'bad_host'),
    ('loopback alias 127.0.0.1', {}, {'Origin': 'http://127.0.0.1:5199'},
     'http://127.0.0.1:5199', 'bad_host'),
    ('forged origin only', {}, {'Origin': 'http://evil.example'}, HOST_URL, 'bad_origin'),
    ('no origin (curl)', {}, {}, HOST_URL, 'bad_origin'),
    ('cross-site fetch', {}, dict(GOOD, **{'Sec-Fetch-Site': 'cross-site'}), HOST_URL, 'cross_site'),
]


@pytest.mark.parametrize('name,kw,headers,base,reason', REFUSED, ids=[r[0] for r in REFUSED])
def test_options_refuses_non_host_callers_before_spending_a_passcode_guess(
        app, authn, name, kw, headers, base, reason):
    c = browser(app, **kw)
    # WRONG passcode on purpose: if the passcode were consulted first the
    # response would be bad_passcode and the per-IP counter would move.
    r = call(c, 'post', OPTIONS, {'passcode': 'definitely-wrong'}, headers=headers, base_url=base)
    assert r.status_code == 403
    assert r.get_json()['error'] == 'host_only' and r.get_json()['reason'] == reason
    assert local_auth._LOCAL_AUTH_FAILS == {}
    assert challenges.STORE.pending() == 0
    assert not store.registry_path().exists()


@pytest.mark.parametrize('name,kw,headers,base,reason', REFUSED, ids=[r[0] for r in REFUSED])
def test_finish_and_revoke_refuse_non_host_callers_without_touching_state(
        app, authn, name, kw, headers, base, reason):
    owner = browser(app)
    j, cred, fin = enroll(owner, authn())
    assert fin.status_code == 200
    # a pending ceremony that a refused caller must not be able to consume
    j2 = start(owner).get_json()
    intruder = browser(app, **kw)
    r = call(intruder, 'post', FINISH, {'ceremony_id': j2['ceremony_id'], 'credential': cred},
             headers=headers, base_url=base)
    assert r.status_code == 403 and r.get_json()['reason'] == reason
    assert challenges.STORE.pending() == 1                       # untouched
    r = call(intruder, 'delete', f"/api/passkeys/{cred['id']}", {'passcode': PASSCODE},
             headers=headers, base_url=base)
    assert r.status_code == 403 and r.get_json()['reason'] == reason
    assert passkey_ids() == [cred['id']]                         # not revoked
    assert local_auth._LOCAL_AUTH_FAILS == {}


def test_status_get_reports_why_this_browser_cannot_enroll(app):
    lan = call(browser(app, '192.168.1.20'), 'get', '/api/passkeys', headers={}).get_json()
    assert lan['can_enroll_here'] is False and lan['enroll_blocked_reason'] == 'not_loopback'
    tunnel = call(browser(app), 'get', '/api/passkeys',
                  headers={'Cf-Access-Jwt-Assertion': 'a.b.c'}).get_json()
    assert tunnel['enroll_blocked_reason'] == 'proxied'
    host = call(browser(app), 'get', '/api/passkeys', headers={}).get_json()
    assert host['can_enroll_here'] is True and host['origin'] == ORIGIN


def test_host_check_uses_the_configured_port_not_the_request(app):
    passkey_routes.wire(port=6000)
    try:
        r = call(browser(app), 'post', OPTIONS, {'passcode': PASSCODE})
        assert r.status_code == 403 and r.get_json()['reason'] == 'bad_host'
    finally:
        passkey_routes.wire(port=5199)


# ── passcode ─────────────────────────────────────────────────────────────────

def test_wrong_or_missing_passcode_issues_no_ceremony(app, authn):
    c = browser(app)
    for body in ({'passcode': 'wrong-wrong'}, {}, {'passcode': 12345}, {'passcode': ''}):
        r = call(c, 'post', OPTIONS, body)
        assert r.status_code == 403 and r.get_json()['error'] == 'bad_passcode', body
    assert challenges.STORE.pending() == 0


def test_no_passcode_configured_refuses_and_enrolls_nothing(app, authn, tmp_path, monkeypatch):
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'none.json')
    r = start(browser(app))
    assert r.status_code == 403 and r.get_json()['error'] == 'passcode_required'
    assert not store.registry_path().exists()


def test_options_shares_the_passcode_guess_budget(app, authn):
    c = browser(app)
    for _ in range(local_auth._LOCAL_AUTH_FAIL_CAP):
        assert call(c, 'post', OPTIONS, {'passcode': 'wrong-wrong'}).status_code == 403
    r = start(c)                                    # even the RIGHT passcode is now throttled
    assert r.status_code == 429 and r.get_json()['error'] == 'too_many_attempts'


def test_malformed_requests_do_not_cost_a_passcode_guess(app, authn):
    c = browser(app)
    assert call(c, 'post', OPTIONS, {'passcode': 'wrong-wrong', 'label': 5}).status_code == 400
    assert call(c, 'delete', '/api/passkeys/bad id!', {'passcode': 'wrong-wrong'}).status_code in (400, 404)
    assert local_auth._LOCAL_AUTH_FAILS == {}


def test_oversized_body_is_refused(app, authn):
    c = browser(app)
    r = c.post(OPTIONS, data=b'{"passcode":"' + b'x' * 70000 + b'"}', headers=GOOD,
               content_type='application/json', base_url=HOST_URL)
    assert r.status_code == 413
    assert local_auth._LOCAL_AUTH_FAILS == {}


# ── replayed and duplicate registration ─────────────────────────────────────

def test_a_finished_ceremony_cannot_be_replayed(app, authn):
    c = browser(app)
    j, cred, fin = enroll(c, authn())
    assert fin.status_code == 200
    again = call(c, 'post', FINISH, {'ceremony_id': j['ceremony_id'], 'credential': cred})
    assert again.status_code == 400 and again.get_json()['error'] == 'unknown_or_used_ceremony'
    assert len(passkey_ids()) == 1


def test_registering_the_same_credential_again_is_a_duplicate(app, authn):
    c = browser(app)
    a = authn()
    assert enroll(c, a)[2].status_code == 200
    _j, _cred, fin = enroll(c, a)                   # same key + credential id, fresh ceremony
    assert fin.status_code == 409 and fin.get_json()['error'] == 'duplicate_credential'
    assert len(passkey_ids()) == 1 and challenges.STORE.pending() == 0


def test_a_ceremony_id_that_never_existed_is_refused(app, authn):
    c = browser(app)
    assert start(c).status_code == 200
    r = call(c, 'post', FINISH, {'ceremony_id': 'made-up', 'credential': {}})
    assert r.status_code == 400 and r.get_json()['error'] == 'unknown_or_used_ceremony'
    assert challenges.STORE.pending() == 1          # the real one is untouched


def test_finish_from_another_browser_session_is_refused_and_spends_the_ceremony(app, authn):
    c = browser(app)
    j = start(c).get_json()
    cred = authn().create(j['options'], ORIGIN)
    other = browser(app)                            # no ceremony cookie
    r = call(other, 'post', FINISH, {'ceremony_id': j['ceremony_id'], 'credential': cred})
    assert r.status_code == 403 and r.get_json()['error'] == 'session_mismatch'
    retry = call(c, 'post', FINISH, {'ceremony_id': j['ceremony_id'], 'credential': cred})
    assert retry.status_code == 400                 # consumption is final even after failure
    assert passkey_ids() == []


def test_an_expired_ceremony_is_refused(app, authn, monkeypatch):
    c = browser(app)
    j = start(c).get_json()
    cred = authn().create(j['options'], ORIGIN)
    real = challenges._clock
    monkeypatch.setattr(challenges, '_clock', lambda: real() + challenges.CHALLENGE_TTL_S + 1)
    r = call(c, 'post', FINISH, {'ceremony_id': j['ceremony_id'], 'credential': cred})
    assert r.status_code == 410 and r.get_json()['error'] == 'expired_ceremony'
    assert passkey_ids() == []


def test_finish_requires_a_ceremony_id_and_credential_object(app, authn):
    c = browser(app)
    for body in ({}, {'ceremony_id': 5, 'credential': {}}, {'ceremony_id': 'x', 'credential': 'str'}):
        assert call(c, 'post', FINISH, body).status_code == 400


def test_pending_ceremonies_are_bounded(app, authn):
    c = browser(app)
    for _ in range(challenges.MAX_PENDING):
        assert start(c).status_code == 200
    r = start(c)
    assert r.status_code == 429 and r.get_json()['error'] == 'too_many_ceremonies'


def test_concurrent_finishes_of_one_ceremony_have_one_winner(app, authn):
    c = browser(app)
    r = start(c)
    j = r.get_json()
    nonce = r.headers['Set-Cookie'].split(';')[0].split('=', 1)[1]
    cred = authn().create(j['options'], ORIGIN)
    results, gate = [], threading.Barrier(6)

    def go():
        cl = browser(app)
        cl.set_cookie('mc_passkey_ceremony', nonce, domain='localhost', path='/api/passkeys/register')
        gate.wait()
        resp = call(cl, 'post', FINISH, {'ceremony_id': j['ceremony_id'], 'credential': cred})
        results.append(resp.status_code)

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 400, 400, 400, 400, 400]
    assert len(passkey_ids()) == 1


def test_concurrent_enrollments_from_one_browser_all_register(app, authn):
    # six independent authenticators finishing at once: registry writes serialize
    c = browser(app)
    assert enroll(c, authn(), 'first')[2].status_code == 200   # fixes the owner handle
    ceremonies = []
    for i in range(5):
        r = start(c, f'dev{i}')
        nonce = r.headers['Set-Cookie'].split(';')[0].split('=', 1)[1]
        a = authn()
        ceremonies.append((r.get_json(), a, nonce))
    results, gate = [], threading.Barrier(5)

    def go(j, a, nonce):
        cl = browser(app)
        cl.set_cookie('mc_passkey_ceremony', nonce, domain='localhost', path='/api/passkeys/register')
        gate.wait()
        cred = a.create(j['options'], ORIGIN)
        resp = call(cl, 'post', FINISH, {'ceremony_id': j['ceremony_id'], 'credential': cred})
        results.append(resp.status_code)

    threads = [threading.Thread(target=go, args=args) for args in ceremonies]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200] * 5
    assert len(passkey_ids()) == 6
    store.load()                                   # still a valid registry


def test_ceremonies_begun_before_any_owner_exists_cannot_split_the_handle(app, authn):
    # Six starts against a pristine registry each mint their own owner handle;
    # only the first finish may fix it, the rest are refused, never stored.
    c = browser(app)
    ceremonies = []
    for i in range(3):
        r = start(c, f'dev{i}')
        nonce = r.headers['Set-Cookie'].split(';')[0].split('=', 1)[1]
        ceremonies.append((r.get_json(), authn(), nonce))
    codes = []
    for j, a, nonce in ceremonies:
        cl = browser(app)
        cl.set_cookie('mc_passkey_ceremony', nonce, domain='localhost', path='/api/passkeys/register')
        resp = call(cl, 'post', FINISH, {'ceremony_id': j['ceremony_id'],
                                         'credential': a.create(j['options'], ORIGIN)})
        codes.append((resp.status_code, (resp.get_json() or {}).get('error')))
    assert codes == [(200, None), (409, 'owner_handle_mismatch'), (409, 'owner_handle_mismatch')]
    assert len(passkey_ids()) == 1


# ── verification: every refusal stores nothing and spends the ceremony ──────

BAD = [
    ('wrong origin', dict(origin='http://localhost:5200')),
    ('lookalike origin', dict(origin='http://localhost:5199.evil.example')),
    ('wrong rp id hash', dict(rp_id='evil.example')),
    ('wrong challenge', dict(challenge='AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA')),
    ('user verification missing', dict(uv=False)),
    ('user presence missing', dict(up=False)),
    ('assertion type, not create', dict(ceremony_type='webauthn.get')),
    ('cross-origin iframe', dict(cross_origin=True)),
    ('top origin present', dict(top_origin='http://evil.example')),
]


@pytest.mark.parametrize('name,kw', BAD, ids=[b[0] for b in BAD])
def test_invalid_registrations_are_rejected_and_store_nothing(app, authn, name, kw):
    c = browser(app)
    j = start(c).get_json()
    cred = authn().create(j['options'], kw.pop('origin', ORIGIN), **kw)
    r = call(c, 'post', FINISH, {'ceremony_id': j['ceremony_id'], 'credential': cred})
    assert r.status_code == 400 and r.get_json()['error'] == 'invalid_registration', name
    assert passkey_ids() == [] and not store.registry_path().exists()
    retry = call(c, 'post', FINISH, {'ceremony_id': j['ceremony_id'], 'credential': cred})
    assert retry.status_code == 400 and retry.get_json()['error'] == 'unknown_or_used_ceremony'


@pytest.mark.parametrize('mangle', [
    lambda cred: cred.update(id='different'),
    lambda cred: cred.update(type='password'),
    lambda cred: cred['response'].update(clientDataJSON='%%%'),
    lambda cred: cred['response'].update(attestationObject='AAAA'),
    lambda cred: cred.pop('rawId'),
    lambda cred: cred.update(response=None),
])
def test_malformed_credentials_are_rejected_not_crashed(app, authn, mangle):
    c = browser(app)
    j = start(c).get_json()
    cred = authn().create(j['options'], ORIGIN)
    mangle(cred)
    r = call(c, 'post', FINISH, {'ceremony_id': j['ceremony_id'], 'credential': cred})
    assert r.status_code == 400 and r.get_json()['error'] == 'invalid_registration'
    assert passkey_ids() == []


# ── revocation ───────────────────────────────────────────────────────────────

def test_revoke_requires_the_passcode_and_tombstones(app, authn):
    c = browser(app)
    _j, cred, fin = enroll(c, authn())
    assert fin.status_code == 200
    path = f"/api/passkeys/{cred['id']}"
    assert call(c, 'delete', path, {}).status_code == 403
    assert call(c, 'delete', path, {'passcode': 'wrong-wrong'}).get_json()['error'] == 'bad_passcode'
    assert passkey_ids() == [cred['id']]
    r = call(c, 'delete', path, {'passcode': PASSCODE})
    assert r.status_code == 200 and r.get_json()['credential']['revoked_at']
    row, = call(c, 'get', '/api/passkeys').get_json()['credentials']
    assert row['revoked_at'] and passkey_ids() == []
    again = call(c, 'delete', path, {'passcode': PASSCODE})
    assert again.status_code == 404 and again.get_json()['error'] == 'unknown_credential'


def test_revoking_invalidates_pending_ceremonies(app, authn):
    c = browser(app)
    _j, cred, fin = enroll(c, authn())
    pending = start(c).get_json()                   # started before the revocation
    new_cred = authn().create(pending['options'], ORIGIN)
    assert call(c, 'delete', f"/api/passkeys/{cred['id']}", {'passcode': PASSCODE}).status_code == 200
    r = call(c, 'post', FINISH, {'ceremony_id': pending['ceremony_id'], 'credential': new_cred})
    assert r.status_code == 400 and r.get_json()['error'] == 'unknown_or_used_ceremony'
    assert passkey_ids() == []


def test_a_revoked_credential_cannot_be_re_enrolled(app, authn):
    c = browser(app)
    a = authn()
    _j, cred, fin = enroll(c, a)
    call(c, 'delete', f"/api/passkeys/{cred['id']}", {'passcode': PASSCODE})
    _j2, _c2, again = enroll(c, a)                  # same credential id after revocation
    assert again.status_code == 409 and again.get_json()['error'] == 'duplicate_credential'


def test_revoke_everything_then_enroll_again(app, authn):
    c = browser(app)
    _j, cred, _f = enroll(c, authn())
    call(c, 'delete', f"/api/passkeys/{cred['id']}", {'passcode': PASSCODE})
    listing = call(c, 'get', '/api/passkeys').get_json()
    assert listing['enrolled'] is True and listing['active_count'] == 0     # not "fresh install"
    assert enroll(c, authn())[2].status_code == 200


# ── corrupt store ────────────────────────────────────────────────────────────

def test_a_corrupt_registry_fails_closed_on_every_route(app, authn):
    c = browser(app)
    _j, cred, fin = enroll(c, authn())
    pending = start(c).get_json()
    store.registry_path().write_text('{"schema": 1, truncated', encoding='utf-8')
    listing = call(c, 'get', '/api/passkeys')
    assert listing.status_code == 503 and listing.get_json()['error'] == 'passkey_store_unreadable'
    assert 'credentials' not in listing.get_json()                  # not "zero credentials"
    r = call(c, 'post', OPTIONS, {'passcode': 'wrong-wrong'})
    assert r.status_code == 503 and local_auth._LOCAL_AUTH_FAILS == {}
    new_cred = authn().create(pending['options'], ORIGIN)
    fin = call(c, 'post', FINISH, {'ceremony_id': pending['ceremony_id'], 'credential': new_cred})
    assert fin.status_code == 503
    rev = call(c, 'delete', f"/api/passkeys/{cred['id']}", {'passcode': PASSCODE})
    assert rev.status_code == 503
    assert store.registry_path().read_text(encoding='utf-8').startswith('{"schema": 1, trunc')


def test_a_deleted_registry_after_enrollment_is_not_a_fresh_install(app, authn):
    c = browser(app)
    assert enroll(c, authn())[2].status_code == 200
    store.registry_path().unlink()
    assert call(c, 'get', '/api/passkeys').status_code == 503
    assert start(c).status_code == 503


# ── restart ──────────────────────────────────────────────────────────────────

def test_restart_keeps_credentials_and_forgets_ceremonies(app, authn):
    c = browser(app)
    _j, cred, _f = enroll(c, authn())
    pending = start(c).get_json()
    new_cred = authn().create(pending['options'], ORIGIN)
    # restart: new process = new app, empty ceremony table, registry re-read from disk
    challenges.STORE._items.clear()
    app2 = make_app()
    c2 = browser(app2)
    assert [r['id'] for r in call(c2, 'get', '/api/passkeys').get_json()['credentials']] == [cred['id']]
    r = call(c2, 'post', FINISH, {'ceremony_id': pending['ceremony_id'], 'credential': new_cred})
    assert r.status_code == 400 and r.get_json()['error'] == 'unknown_or_used_ceremony'
    assert passkey_ids() == [cred['id']]


# ── library absent, audit ────────────────────────────────────────────────────

def test_without_the_library_the_routes_report_it_and_the_list_still_works(app, monkeypatch):
    def gone():
        raise ceremony.LibraryUnavailable('no module named webauthn')
    monkeypatch.setattr(ceremony, '_lib', gone)
    c = browser(app)
    listing = call(c, 'get', '/api/passkeys').get_json()
    assert listing['available'] is False and listing['credentials'] == []
    r = start(c)
    assert r.status_code == 503 and r.get_json()['error'] == 'passkeys_unavailable'
    assert local_auth._LOCAL_AUTH_FAILS == {}


def test_audit_records_outcomes_without_bodies_or_secrets(app, authn):
    c = browser(app)
    _j, cred, _f = enroll(c, authn(), 'Laptop')
    call(c, 'delete', f"/api/passkeys/{cred['id']}", {'passcode': PASSCODE})
    text = audit.audit_path().read_text(encoding='utf-8')
    lines = [json.loads(line) for line in text.splitlines()]
    assert [(e['operation'], e['outcome']) for e in lines] == [('register', 'ok'), ('revoke', 'ok')]
    assert all(e['credential'] == cred['id'][:8] for e in lines)
    for needle in (PASSCODE, cred['id'], 'attestationObject', 'clientDataJSON', 'Laptop'):
        assert needle not in text
