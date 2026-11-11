"""Server-side fetch + verify of the signed mail-service manifest, and its local GET route.

A fake origin stands in for the mail service (no network). Test-only keys.
"""
from __future__ import annotations

import base64
import json
import urllib.error

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask

from mc import state
from mc import agent_mail_manifest_fetch as fetch_mod
from mc.agent_mail_manifest_fetch import ManifestFetchError, fetch_verified_manifest
from mc.agent_mail_service_manifest import ManifestState
from mc.blueprints import agent_mail_manifest_routes as routes
from mc.blueprints.agent_mail_routes import create_blueprint as ingress_blueprint

NOW = 1_794_249_600_000
ORIGIN = 'https://mail.example.com'


def enc(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def pub(key) -> str:
    return enc(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


class Origin:
    """Fake mail service: whatever it is told to answer, recording what was asked."""

    def __init__(self, reply):
        self.reply, self.urls = reply, []

    def get(self, url):
        self.urls.append(url)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.fixture
def world():
    root, service = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    payload = ['clayrune-mail-service-manifest', '1', ORIGIN, '3', str(NOW - 1000), str(NOW + 100_000),
               'service1', pub(service), str(NOW - 1000), str(NOW + 200_000), []]

    def sign(p, key=root):
        return enc(key.sign(json.dumps(p, separators=(',', ':'), ensure_ascii=True).encode('ascii')))

    def served(p=None, signature=None, key=root, status=200):
        p = payload if p is None else p
        body = json.dumps({'manifest': p, 'signature': sign(p, key) if signature is None else signature}).encode()
        return Origin({'status': status, 'body': body})

    def run(fake=None, **over):
        fake = fake or served()
        args = dict(origin=ORIGIN, root_public_key=pub(root), transport=fake, now_ms=lambda: NOW)
        args.update(over)
        return fetch_verified_manifest(**args), fake

    return payload, root, sign, served, run


def refusal(run, **kw):
    with pytest.raises(ManifestFetchError) as info:
        run(**kw)
    return info.value


def test_good_manifest_is_returned_verified_and_url_is_fixed(world):
    payload, _, sign, _, run = world
    result, origin = run()
    assert result == {'manifest': payload, 'signature': sign(payload), 'sequence': 3,
                      'expires_at_ms': NOW + 100_000}
    assert origin.urls == [ORIGIN + '/v1/mail/manifest']


def test_bad_signature_is_refused_and_returns_nothing(world):
    _, _, _, served, run = world
    wrong = served(key=Ed25519PrivateKey.generate())
    assert refusal(run, fake=wrong).code == 'bad_signature'
    assert refusal(run, fake=served(signature=enc(b'\x00' * 64))).code == 'bad_signature'


def test_tampered_payload_under_a_real_signature_is_refused(world):
    payload, _, sign, _, run = world
    signature = sign(payload)
    tampered = list(payload)
    tampered[3] = '99'
    body = json.dumps({'manifest': tampered, 'signature': signature}).encode()
    assert refusal(run, fake=Origin({'status': 200, 'body': body})).code == 'bad_signature'


def test_expired_and_not_yet_valid_are_stale(world):
    payload, _, _, served, run = world
    assert refusal(run, now_ms=lambda: NOW + 150_000).code == 'stale'
    assert refusal(run, now_ms=lambda: NOW + 150_000).detail == 'manifest_time'
    assert refusal(run, now_ms=lambda: NOW - 5000).code == 'stale'


def test_rollback_against_the_persisted_floor_is_stale(world):
    payload, _, _, _, run = world
    floor = lambda origin: ManifestState(4, 'a' * 64)
    err = refusal(run, floor=floor)
    assert (err.code, err.detail) == ('stale', 'manifest_rollback')
    ok, _ = run(floor=lambda origin: ManifestState(2, 'a' * 64))
    assert ok['sequence'] == 3


def test_fetch_never_writes_the_floor(world):
    class Floor:
        writes = 0
        def __call__(self, origin): return None
    floor = Floor()
    run = world[4]
    run(floor=floor)
    assert Floor.writes == 0   # the callable has no write path; start owns the CAS


def test_unreadable_floor_does_not_block_a_good_manifest(world):
    def boom(origin): raise RuntimeError('store held')
    assert world[4](floor=boom)[0]['sequence'] == 3


def test_oversize_body_is_refused(world):
    _, _, _, _, run = world
    err = refusal(run, fake=Origin({'status': 200, 'body': b'x' * (fetch_mod.RESPONSE_LIMIT + 1)}))
    assert (err.code, err.detail) == ('refused', 'oversize')


@pytest.mark.parametrize('body, detail', [
    (b'not json', 'not_json'),
    (b'[]', 'shape'),
    (json.dumps({'manifest': [], 'signature': 'x', 'extra': 1}).encode(), 'shape'),
    (json.dumps({'manifest': 'no', 'signature': 'x'}).encode(), 'shape'),
    (json.dumps({'manifest': [], 'signature': 'x'}).encode(), 'manifest_shape'),
])
def test_malformed_answers_are_refused(world, body, detail):
    err = refusal(world[4], fake=Origin({'status': 200, 'body': body}))
    assert (err.code, err.detail) == ('refused', detail)


def test_manifest_for_another_origin_is_refused(world):
    payload, _, _, served, run = world
    other = list(payload)
    other[2] = 'https://evil.example.com'
    err = refusal(run, fake=served(other))
    assert err.code == 'refused' and err.detail == 'manifest_fields'


@pytest.mark.parametrize('origin', [None, '', 'http://mail.example.com', 'ftp://mail.example.com',
                                    'https://mail.example.com/path', 'https://user@mail.example.com',
                                    'https://localhost', 'mail.example.com'])
def test_unconfigured_or_non_https_origin_makes_no_request(world, origin):
    served = world[3]()
    err = refusal(world[4], fake=served, origin=origin)
    assert err.code == 'not_configured' and served.urls == []


def test_missing_root_makes_no_request(world):
    served = world[3]()
    assert refusal(world[4], fake=served, root_public_key='').code == 'not_configured'
    assert served.urls == []


def test_unreachable_and_http_errors(world):
    run = world[4]
    assert refusal(run, fake=Origin(ManifestFetchError('unreachable', 'transport'))).code == 'unreachable'
    for status in (404, 500, 503):
        err = refusal(run, fake=Origin({'status': status, 'body': b''}))
        assert (err.code, err.detail) == ('unreachable', f'http_{status}')


def test_redirect_is_refused_not_followed(world):
    err = refusal(world[4], fake=Origin({'status': 302, 'body': b''}))
    assert (err.code, err.detail) == ('refused', 'redirect')


# ---- the real transport, with a fake opener --------------------------------------------------------
class Reply:
    def __init__(self, status, body): self.status, self._body = status, body
    def read(self, n): return self._body[:n]
    def __enter__(self): return self
    def __exit__(self, *a): return False


class Opener:
    def __init__(self, behaviour): self.behaviour, self.seen = behaviour, []
    def open(self, request, timeout=None):
        self.seen.append((request.full_url, request.get_method(), timeout))
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return self.behaviour


def test_transport_is_get_only_short_timeout_and_bounded():
    opener = Opener(Reply(200, b'y' * (fetch_mod.RESPONSE_LIMIT + 50)))
    out = fetch_mod.HttpsGetTransport(opener=opener).get(ORIGIN + '/v1/mail/manifest')
    assert opener.seen == [(ORIGIN + '/v1/mail/manifest', 'GET', fetch_mod.TIMEOUT_S)]
    assert fetch_mod.TIMEOUT_S <= 10 and len(out['body']) == fetch_mod.RESPONSE_LIMIT + 1


@pytest.mark.parametrize('exc', [urllib.error.URLError('dns'), TimeoutError(), ConnectionResetError(), OSError('tls')])
def test_transport_failures_are_one_closed_code(exc):
    with pytest.raises(ManifestFetchError) as info:
        fetch_mod.HttpsGetTransport(opener=Opener(exc)).get(ORIGIN + '/x')
    assert info.value.code == 'unreachable'
    assert 'dns' not in str(info.value) and 'tls' not in info.value.detail


def test_transport_http_error_status_is_returned_not_raised():
    exc = urllib.error.HTTPError(ORIGIN, 302, 'Found', {}, None)
    assert fetch_mod.HttpsGetTransport(opener=Opener(exc)).get(ORIGIN + '/x')['status'] == 302


def test_no_redirect_handler_declines_every_redirect():
    handler = fetch_mod._NoRedirect()
    assert handler.redirect_request(object(), None, 302, 'Found', {}, 'https://other.example.com') is None


# ---- the route -------------------------------------------------------------------------------------
@pytest.fixture
def web(monkeypatch, world):
    payload, root, _, served, _ = world
    monkeypatch.setitem(state.CONFIG, routes.FLAG, True)
    monkeypatch.setitem(state.CONFIG, routes.ORIGIN_KEY, ORIGIN)
    monkeypatch.setitem(state.CONFIG, routes.ROOT_KEY, pub(root))
    box = {'transport': served()}
    monkeypatch.setattr(fetch_mod.time, 'time', lambda: NOW / 1000)

    def build(**kw):
        app = Flask(__name__)
        app.register_blueprint(routes.create_blueprint(transport=box['transport'], floor=lambda origin: None, **kw))
        return app.test_client()
    return build, box, served


def test_route_off_flag_is_404_and_never_fetches(web, monkeypatch):
    build, box, _ = web
    monkeypatch.setitem(state.CONFIG, routes.FLAG, False)
    r = build().get(routes.ROUTE)
    assert r.status_code == 404 and box['transport'].urls == []


def test_route_returns_verified_manifest_no_passcode(web):
    build, box, _ = web
    r = build().get(routes.ROUTE)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    assert set(r.json) == {'manifest', 'signature', 'sequence', 'expires_at_ms'} and r.json['sequence'] == 3
    assert box['transport'].urls == [ORIGIN + '/v1/mail/manifest']


def test_route_error_codes(web, world):
    build, box, served = web
    box['transport'] = served(key=Ed25519PrivateKey.generate())
    r = build().get(routes.ROUTE)
    assert (r.status_code, r.json) == (409, {'error': 'bad_signature', 'detail': 'manifest_signature'})
    box['transport'] = Origin({'status': 404, 'body': b''})
    r = build().get(routes.ROUTE)
    assert (r.status_code, r.json['error']) == (502, 'unreachable')


def test_route_unconfigured(web, monkeypatch):
    build, box, _ = web
    monkeypatch.setitem(state.CONFIG, routes.ORIGIN_KEY, 'http://mail.example.com')
    r = build().get(routes.ROUTE)
    assert (r.status_code, r.json) == (409, {'error': 'not_configured'}) and box['transport'].urls == []


def test_route_is_get_only_and_takes_no_input(web):
    build, box, _ = web
    client = build()
    assert client.post(routes.ROUTE, json={}).status_code == 405
    client.get(routes.ROUTE + '?origin=https://evil.example.com')
    assert box['transport'].urls == [ORIGIN + '/v1/mail/manifest']


def test_registered_under_the_ingress_blueprint(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(ingress_blueprint(tmp_path))
    assert 'agent_mail_ingress.agent_mail_manifest' in app.blueprints
    monkeypatch.setitem(state.CONFIG, routes.FLAG, False)
    assert app.test_client().get(routes.ROUTE).status_code == 404


def test_production_floor_reads_without_creating_a_store(tmp_path, monkeypatch):
    from mc import agent_mail_factory
    agent_mail_factory.reset_for_tests(agent_mail_factory.MailFactory(tmp_path / 'mail'))
    try:
        assert routes._production_floor(ORIGIN) is None
        assert not (tmp_path / 'mail').exists()
    finally:
        agent_mail_factory.reset_for_tests(None)


def test_fetched_manifest_is_accepted_by_the_real_floor_then_rollback_is_stale(world, tmp_path):
    """What the route returns is exactly what Enrollment.start re-verifies against ManifestCAS; the
    fetch itself leaves that floor alone, and a later lower sequence is refused through the real floor."""
    from mc.agent_mail_enrollment_state import ManifestCAS, initialize
    from mc.agent_mail_service_manifest import verify_service_manifest
    from mc.agent_mail_store import MailStore
    payload, root, sign, served, run = world
    store = MailStore(tmp_path / 'mail.sqlite3', clock=lambda: NOW / 1000)
    initialize(store)
    cas = ManifestCAS(store)
    fetched, _ = run(floor=cas.load)
    assert cas.load(ORIGIN) is None                       # fetch did not advance the floor
    candidate = verify_service_manifest(fetched['manifest'], fetched['signature'], root_public_key=pub(root),
                                        service_origin=ORIGIN, store=cas, now_ms=NOW, enabled=True)
    assert candidate.sequence == 3 and cas.load(ORIGIN).sequence == 3   # start's own CAS write
    assert run(floor=cas.load)[0]['sequence'] == 3        # same manifest still fine
    older = list(payload)
    older[3] = '2'
    err = refusal(run, fake=served(older), floor=cas.load)
    assert (err.code, err.detail) == ('stale', 'manifest_rollback')
