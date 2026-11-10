"""Enrollment-grant keyring: config parsing, one active signer, JWKS shape (7d-2e-1, contract 1.1/1.3)."""
from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from control_plane.app import jwt_es256
from control_plane.app.mail_delivery_signing import PURPOSE as DELIVERY_PURPOSE
from control_plane.app.mail_grant_keyring import GrantKeyError, MailGrantKeyring, parse_grant_keys
from control_plane.app.mail_grant_signing import MailGrantKey, PURPOSE


def pem(key) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def ec_key(curve=None):
    return ec.generate_private_key(curve or ec.SECP256R1())


def grant_key(kid="mail-grant-a") -> MailGrantKey:
    return MailGrantKey(kid, ec_key(), PURPOSE)


def cfg(*entries) -> str:
    return json.dumps(list(entries))


@pytest.mark.parametrize("raw", [None, "", "   ", "not json", "{}", "[]", "[1]", '[{"kid":"mail-grant-a"}]'])
def test_missing_or_malformed_config_refuses_and_nothing_is_generated(raw):
    with pytest.raises(GrantKeyError):
        parse_grant_keys(raw)


def test_unset_config_is_not_configured_with_no_dev_fallback():
    with pytest.raises(GrantKeyError, match="mail_grant_keys_not_configured"):
        parse_grant_keys(None)


@pytest.mark.parametrize("kid", ["mail-delivery-x", "cp-2026a", "mail-grant-", "mail-grant-bad kid", "", "x" * 200])
def test_wrong_kid_shape_is_refused_by_the_parser_and_the_key_class(kid):
    with pytest.raises(GrantKeyError, match="mail_grant_keys_invalid"):
        parse_grant_keys(cfg({"kid": kid, "pem": pem(ec_key())}))
    with pytest.raises(ValueError, match="mail_grant_key_required"):
        MailGrantKey(kid, ec_key(), PURPOSE)


def test_only_p256_ec_keys_are_accepted():
    with pytest.raises(GrantKeyError):
        parse_grant_keys(cfg({"kid": "mail-grant-a", "pem": pem(ec_key(ec.SECP384R1()))}))
    with pytest.raises(GrantKeyError):
        parse_grant_keys(cfg({"kid": "mail-grant-a", "pem": pem(ed25519.Ed25519PrivateKey.generate())}))
    with pytest.raises(GrantKeyError):
        parse_grant_keys(cfg({"kid": "mail-grant-a", "pem": "-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----"}))


def test_the_key_class_refuses_every_purpose_but_the_enrollment_grant():
    with pytest.raises(ValueError, match="mail_grant_key_required"):
        MailGrantKey("mail-grant-a", ec_key(), DELIVERY_PURPOSE)
    with pytest.raises(ValueError, match="wrong_signing_purpose"):
        grant_key().sign({"aud": DELIVERY_PURPOSE, "token_use": DELIVERY_PURPOSE})


def test_duplicate_kid_is_refused():
    with pytest.raises(GrantKeyError, match="mail_grant_keyring_invalid"):
        parse_grant_keys(cfg({"kid": "mail-grant-a", "pem": pem(ec_key())}, {"kid": "mail-grant-a", "pem": pem(ec_key()), "active": False}))
    with pytest.raises(GrantKeyError, match="mail_grant_keyring_invalid"):
        MailGrantKeyring(((grant_key("mail-grant-a"), True), (grant_key("mail-grant-a"), False)))


def test_exactly_one_key_must_be_active():
    a, b = grant_key("mail-grant-a"), grant_key("mail-grant-b")
    with pytest.raises(GrantKeyError):
        MailGrantKeyring(((a, False), (b, False)))
    with pytest.raises(GrantKeyError):
        MailGrantKeyring(((a, True), (b, True)))
    with pytest.raises(GrantKeyError):
        parse_grant_keys(cfg({"kid": "mail-grant-a", "pem": pem(ec_key())}, {"kid": "mail-grant-b", "pem": pem(ec_key())}))


def test_unknown_or_mistyped_entry_fields_are_refused():
    with pytest.raises(GrantKeyError):
        parse_grant_keys(cfg({"kid": "mail-grant-a", "pem": pem(ec_key()), "extra": 1}))
    with pytest.raises(GrantKeyError):
        parse_grant_keys(cfg({"kid": "mail-grant-a", "pem": pem(ec_key()), "active": "yes"}))


def test_rotation_publishes_every_key_and_signs_with_the_active_one_only():
    old_private, new_private = ec_key(), ec_key()
    ring = parse_grant_keys(cfg({"kid": "mail-grant-old", "pem": pem(old_private), "active": False},
                                {"kid": "mail-grant-new", "pem": pem(new_private), "active": True}))
    assert ring.signing_key().kid == "mail-grant-new"
    keys = ring.jwks()["keys"]
    assert [k["kid"] for k in keys] == ["mail-grant-old", "mail-grant-new"]
    for k in keys:
        assert set(k) == {"kid", "kty", "crv", "alg", "use", "x", "y"} and "d" not in k
        assert (k["kty"], k["crv"], k["alg"], k["use"]) == ("EC", "P-256", "ES256", "sig")


def test_signing_uses_the_active_key_and_the_grant_purpose():
    ring = parse_grant_keys(cfg({"kid": "mail-grant-a", "pem": pem(ec_key())}))
    import base64
    token = ring.signing_key().sign({"aud": PURPOSE, "token_use": PURPOSE, "sub": "u1"})
    header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
    assert header == {"alg": "ES256", "typ": "clayrune-mail-enrollment+jwt", "kid": "mail-grant-a"}


def test_the_grant_key_set_shares_nothing_with_session_or_delivery_keys():
    ring = MailGrantKeyring(((grant_key(), True),))
    kids = {k["kid"] for k in ring.jwks()["keys"]}
    assert kids and not kids & {k["kid"] for k in jwt_es256.jwks()["keys"]}
    assert all(k.startswith("mail-grant-") for k in kids)
