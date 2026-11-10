"""Service-to-CP request verifier (7d-2b, contract section 2, D1)."""
from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519

from control_plane.app.mail_enrollment_transcript import b64u
from control_plane.app.mail_service_auth import (
    RefusalBucket, SKEW_MS, ServiceAuthRefused, ServiceKeyring, parse_service_keys, verify_service_call)
from control_plane.tests.mail_delivery_support import INSTALL, KEY_ID, raw_public, sign_service_call

PATH = "/v1/mail-delivery/issue"
NOW = 1_794_249_600_123
BODY = b'{"install_id":"install1"}'


@pytest.fixture
def parts():
    private = ed25519.Ed25519PrivateKey.generate()
    seen = set()

    def claim(key_id, nonce, expires_ms):
        if (key_id, nonce) in seen:
            return False
        seen.add((key_id, nonce))
        return True
    return private, ServiceKeyring({KEY_ID: raw_public(private)}), claim


def verify(parts, headers, body=BODY, *, now=NOW, path=PATH, method="POST"):
    _, ring, claim = parts
    return verify_service_call(headers, body, method=method, path=path, keyring=ring, claim_nonce=claim,
                               clock_ms=lambda: now)


def signed(parts, **kw):
    kw.setdefault("path", PATH)
    kw.setdefault("body", BODY)
    kw.setdefault("now_ms", NOW)
    return sign_service_call(parts[0], **kw)


def code(parts, headers, **kw):
    with pytest.raises(ServiceAuthRefused) as e:
        verify(parts, headers, **kw)
    return e.value.code


def test_valid_call_verifies_and_returns_scope(parts):
    call = verify(parts, signed(parts, generation="3"))
    assert (call.key_id, call.install_id, call.generation, call.path, call.timestamp) == (KEY_ID, INSTALL, "3", PATH, NOW)


def test_replayed_nonce_is_refused_only_after_signature_verifies(parts):
    h = signed(parts)
    verify(parts, h)
    assert code(parts, h) == "replayed_nonce"
    # A forged request reusing a victim's nonce must not burn it.
    victim = signed(parts, nonce="a" * 32)
    forged = dict(victim, **{"x-mail-signature": b64u(b"\0" * 64)})
    assert code(parts, forged) == "bad_signature"
    assert verify(parts, victim).nonce == "a" * 32


@pytest.mark.parametrize("field,value,expected", [
    ("x-mail-direction", "service-to-install", "bad_signature_fields"),
    ("x-mail-direction", "install-to-service", "bad_signature_fields"),
    ("x-mail-version", "2", "bad_signature_fields"),
    ("x-mail-generation", "0", "bad_signature_fields"),
    ("x-mail-nonce", "xyz", "bad_signature_fields"),
    ("x-mail-key-id", "nobody", "unknown_service_key"),
    ("x-mail-install-id", "other", "bad_signature"),  # signed array covers the install
    ("x-mail-generation", "2", "bad_signature"),      # and the generation
    ("x-mail-nonce", "b" * 32, "bad_signature"),
    ("x-mail-signature", "not-base64!", "bad_signature"),
])
def test_header_mutations_are_refused(parts, field, value, expected):
    assert code(parts, dict(signed(parts), **{field: value})) == expected


def test_wrong_path_method_body_and_key_refused(parts):
    h = signed(parts)
    assert code(parts, h, path="/v1/mail-delivery/lease") == "bad_signature"
    assert code(parts, h, path="/v1/mail-delivery/issue?x=1") == "bad_signature_fields"
    assert code(parts, h, method="GET") == "bad_signature_fields"
    assert code(parts, h, body=BODY + b" ") == "bad_digest"
    stranger = ed25519.Ed25519PrivateKey.generate()
    assert code(parts, signed((stranger,))) == "bad_signature"


def test_skew_window_both_directions_and_after_slow_claim(parts):
    assert verify(parts, signed(parts), now=NOW + SKEW_MS)
    assert code(parts, signed(parts), now=NOW + SKEW_MS + 1) == "stale_signature"
    assert code(parts, signed(parts), now=NOW - SKEW_MS - 1) == "stale_signature"
    ticks = iter([NOW, NOW, NOW + SKEW_MS + 1])  # clock passes the window while the durable claim runs
    _, ring, claim = parts
    with pytest.raises(ServiceAuthRefused) as e:
        verify_service_call(signed(parts), BODY, method="POST", path=PATH, keyring=ring, claim_nonce=claim,
                            clock_ms=lambda: next(ticks, NOW + SKEW_MS + 1))
    assert e.value.code == "stale_signature"


def test_nonce_store_failure_propagates_as_an_operational_error_not_a_refusal(parts):
    _, ring, _ = parts

    def boom(*_):
        raise OSError("firestore down")
    with pytest.raises(OSError):
        verify_service_call(signed(parts), BODY, method="POST", path=PATH, keyring=ring, claim_nonce=boom,
                            clock_ms=lambda: NOW)


def test_nonce_claim_expiry_is_skew_plus_one_second(parts):
    _, ring, _ = parts
    got = []
    verify_service_call(signed(parts), BODY, method="POST", path=PATH, keyring=ring,
                        claim_nonce=lambda k, n, e: got.append(e) or True, clock_ms=lambda: NOW)
    assert got == [NOW + SKEW_MS + 1000]


def test_a_capability_style_credential_cannot_stand_in(parts):
    # No signature header at all (e.g. a bearer token or Access header only): refused, not crashed.
    h = {k: v for k, v in signed(parts).items() if k != "x-mail-signature"}
    assert code(parts, dict(h, authorization="Bearer abc")) == "bad_signature"


def test_parse_service_keys_is_strict():
    good = [{"key_id": "service1", "public_key_b64u": b64u(b"\x01" * 32)}]
    assert parse_service_keys(json.dumps(good)).keys == {"service1": b"\x01" * 32}
    for bad in (None, "", "   ", "{", "{}", "[]", json.dumps(good + good),
                json.dumps([{"key_id": "service1"}]),
                json.dumps([{"key_id": "service1", "public_key_b64u": b64u(b"\x01" * 31)}]),
                json.dumps([{"key_id": "bad id", "public_key_b64u": b64u(b"\x01" * 32)}]),
                json.dumps([{**good[0], "extra": 1}])):
        with pytest.raises(ServiceAuthRefused):
            parse_service_keys(bad)


def test_public_key_hash_and_refusal_bucket():
    ring = ServiceKeyring({"k": b"\x02" * 32})
    assert len(ring.public_key_sha256("k")) == 64
    t = [0]
    b = RefusalBucket(limit=2, window_ms=1000, clock_ms=lambda: t[0])
    assert [b.allow(), b.allow(), b.allow()] == [True, True, False]
    t[0] = 1000
    assert b.allow() is True
