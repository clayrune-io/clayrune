"""U1: production reviewed_service callable. Fake origin, synthetic stores, test-only keys."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mc import agent_mail_factory as fac
from mc import agent_mail_reviewed_service as rs
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_enrollment_manifest import manifest_digest
from mc.agent_mail_enrollment_state import ManifestCAS
from mc.agent_mail_service_manifest import ManifestState, canonical_manifest_bytes
from mc.agent_mail_service_review_schema import initialize as install_floor
from mc.agent_mail_signup import Signup

NOW = 1_794_249_600_000
ORIGIN = 'https://mail.example.com'
MASTER = b'M' * 32


def enc(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


def pub(key) -> str:
    return enc(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


class Origin:
    """Fake mail service. ``reply`` is a (status, body) pair, an Exception, or a callable."""

    def __init__(self):
        self.reply: Any = None
        self.urls: list[str] = []

    def get(self, url):
        self.urls.append(url)
        if isinstance(self.reply, Exception):
            raise self.reply
        status, body = self.reply
        return {'status': status, 'body': body}


class World:
    def __init__(self, tmp_path, *, floor=True):
        self.root, self.service = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
        self.now = NOW
        self.origin = Origin()
        self.config = {rs.ORIGIN_KEY: ORIGIN, rs.ROOT_KEY: pub(self.root)}
        self.factory = fac.MailFactory(tmp_path / 'mail', clock=lambda: self.now / 1000, key=lambda: MASTER,
                                       config=self.config)
        self.store = self.factory.bootstrap(authorize=lambda: True)
        if floor:
            install_floor(self.store)
        self.serve()
        self.call = rs.build(self.factory, transport=self.origin)

    def payload(self, *, seq=3, expires=NOW + 3_600_000, origin=ORIGIN):
        return ['clayrune-mail-service-manifest', '1', origin, str(seq), str(NOW - 1000), str(expires),
                'service1', pub(self.service), str(NOW - 1000), str(NOW + 7_200_000), []]

    def serve(self, payload=None, *, signer=None, **kw):
        payload = payload or self.payload(**kw)
        signature = enc((signer or self.root).sign(canonical_manifest_bytes(payload)))
        self.origin.reply = (200, json.dumps({'manifest': payload, 'signature': signature}).encode())
        return payload


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def test_returns_candidate_and_digest_equal_to_floor_and_signup_digest(w):
    payload = w.payload()
    candidate, digest = w.call()
    expected = hashlib.sha256(canonical_manifest_bytes(payload)).hexdigest()
    assert digest == expected == manifest_digest(payload)
    assert (candidate.origin, candidate.sequence, candidate.key_id, candidate.public_key,
            candidate.expires_at_ms) == (ORIGIN, 3, 'service1', pub(w.service), NOW + 3_600_000)
    assert ManifestCAS(w.store).load(ORIGIN) == ManifestState(3, expected)
    assert w.origin.urls == [ORIGIN + '/v1/mail/manifest']


def test_signup_service_accepts_the_pair_and_matches_a_stored_row(w):
    signup = Signup(w.store, w.factory.seal(), lambda *a, **k: (200, {}, b""), reviewed_service=w.call, authorize=lambda: True,
                    clock_ms=lambda: w.now)
    pin, digest = signup._service()
    row = {'service_origin': pin.origin, 'service_key_id': pin.key_id, 'service_public_key': pin.public_key,
           'manifest_sequence': pin.sequence, 'manifest_digest': digest}
    assert signup._service(row) == (pin, digest)
    with pytest.raises(EnrollmentRefused, match='service_key_changed'):
        signup._service({**row, 'manifest_digest': 'a' * 64})


def test_factory_wiring_passes_origin_check(tmp_path):
    holder = []
    w = World(tmp_path)
    f = fac.MailFactory(tmp_path / 'mail', clock=w.factory.clock, key=lambda: MASTER, config=w.config,
                        reviewed_service=lambda: holder[0]())
    holder.append(rs.build(f, transport=w.origin))
    candidate, digest = f.reviewed_service()
    assert candidate.origin == ORIGIN and len(digest) == 64


def test_cached_for_ten_minutes_then_refetched(w):
    first = w.call()
    w.now += rs.CACHE_MS - 1
    assert w.call() == first
    assert len(w.origin.urls) == 1
    w.now += 1
    w.call()
    assert len(w.origin.urls) == 2


def test_cache_never_outlives_manifest_expiry(w):
    w.serve(expires=NOW + 60_000)
    w.call()
    w.now += 59_999
    w.call()
    assert len(w.origin.urls) == 1
    w.now += 1
    w.origin.reply = OSError('down')
    with pytest.raises(EnrollmentRefused, match='service_unavailable'):
        w.call()
    assert len(w.origin.urls) == 2


def test_refresh_failure_does_not_fall_back_to_cached_pair(w):
    w.call()
    w.now += rs.CACHE_MS
    w.origin.reply = OSError('down')
    for _ in range(2):
        with pytest.raises(EnrollmentRefused, match='service_unavailable'):
            w.call()
    assert len(w.origin.urls) == 3  # each failed call retried; nothing served from memory


def test_failure_is_not_cached_and_recovers(w):
    w.origin.reply = (500, b'')
    with pytest.raises(EnrollmentRefused, match='service_unavailable'):
        w.call()
    w.serve()
    assert w.call()[0].sequence == 3


def test_bad_root_signature_refused_and_floor_untouched(w):
    w.serve(signer=Ed25519PrivateKey.generate())
    with pytest.raises(EnrollmentRefused, match='service_unavailable'):
        w.call()
    assert ManifestCAS(w.store).load(ORIGIN) is None


def test_rolled_back_manifest_refused_and_floor_kept(w):
    w.serve(seq=5)
    w.call()
    w.now += rs.CACHE_MS
    w.serve(seq=4)
    with pytest.raises(EnrollmentRefused, match='service_unavailable'):
        w.call()
    floor = ManifestCAS(w.store).load(ORIGIN)
    assert floor is not None and floor.sequence == 5


def test_higher_sequence_advances_floor(w):
    w.call()
    w.now += rs.CACHE_MS
    w.serve(seq=4)
    assert w.call()[0].sequence == 4
    floor = ManifestCAS(w.store).load(ORIGIN)
    assert floor is not None and floor.sequence == 4


def test_manifest_for_other_origin_refused(w):
    w.serve(origin='https://evil.example.com')
    with pytest.raises(EnrollmentRefused, match='service_unavailable'):
        w.call()


def test_missing_floor_table_fails_closed(tmp_path):
    w = World(tmp_path, floor=False)
    with pytest.raises(EnrollmentRefused, match='service_unavailable'):
        w.call()
    assert w.origin.urls  # fetched, then refused at the floor: nothing was returned or cached


@pytest.mark.parametrize('missing', [rs.ORIGIN_KEY, rs.ROOT_KEY])
def test_unconfigured_refuses_without_fetching(w, missing):
    del w.config[missing]
    with pytest.raises(EnrollmentRefused, match='service_unavailable'):
        w.call()
    assert w.origin.urls == []


def test_config_change_invalidates_cache(w):
    w.call()
    other = Ed25519PrivateKey.generate()
    w.config[rs.ROOT_KEY] = pub(other)
    with pytest.raises(EnrollmentRefused, match='service_unavailable'):
        w.call()  # old root's manifest no longer verifies; the cached pair is not served
    assert len(w.origin.urls) == 2


def test_not_a_pull_store_refuses(tmp_path):
    w = World(tmp_path)
    w.store.path.unlink()  # marker says ready, database gone: StoreHeld store_missing
    w.factory._store = None
    with pytest.raises(EnrollmentRefused, match='service_unavailable'):
        w.call()


def test_config_keys_match_factory():
    assert (rs.ORIGIN_KEY, rs.ROOT_KEY) == (fac.ORIGIN_KEY, fac.ROOT_KEY)


def test_build_touches_nothing(tmp_path):
    f = fac.MailFactory(tmp_path / 'mail', config={})
    origin = Origin()
    rs.build(f, transport=origin)
    assert origin.urls == [] and not f.paths.root.exists()
