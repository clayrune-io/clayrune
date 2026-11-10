"""Delivery-capability key class, JWKS shape, rotation and cross-purpose refusals (7d-2b, contract 1.1/1.3)."""
from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from control_plane.app import jwt_es256
from control_plane.app.mail_delivery_signing import (
    DeliveryKeyError, DeliveryKeyring, MailDeliveryKey, PURPOSE, TOKEN_TYPE, parse_delivery_keys)
from control_plane.app.mail_grant_signing import MailGrantKey, PURPOSE as GRANT_PURPOSE
from control_plane.tests.mail_delivery_support import delivery_key, payload

CLAIMS = {"aud": PURPOSE, "token_use": PURPOSE, "sub": "u1"}


def pem(key) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def test_header_is_exactly_alg_typ_kid_and_purpose_pinned():
    k = delivery_key()
    token = k.sign(CLAIMS)
    import base64
    header = json.loads(base64.urlsafe_b64decode(token.split(".")[0] + "=="))
    assert header == {"alg": "ES256", "typ": TOKEN_TYPE, "kid": "mail-delivery-test"}
    assert TOKEN_TYPE == "clayrune-mail-delivery+jwt"
    assert payload(token)["aud"] == PURPOSE == "clayrune-mail-delivery"


@pytest.mark.parametrize("kid", ["mail-grant-x", "cp-2026a", "mail-delivery-", "mail-delivery-bad kid", "", "x" * 200])
def test_kid_pattern_is_pinned(kid):
    with pytest.raises(DeliveryKeyError, match="mail_delivery_key_required"):
        MailDeliveryKey(kid, ec.generate_private_key(ec.SECP256R1()), PURPOSE)


def test_only_p256_ec_keys_and_only_the_delivery_purpose():
    with pytest.raises(DeliveryKeyError):
        MailDeliveryKey("mail-delivery-a", ec.generate_private_key(ec.SECP384R1()), PURPOSE)
    with pytest.raises(DeliveryKeyError):
        MailDeliveryKey("mail-delivery-a", ed25519.Ed25519PrivateKey.generate(), PURPOSE)  # type: ignore[arg-type]
    with pytest.raises(DeliveryKeyError):
        MailDeliveryKey("mail-delivery-a", ec.generate_private_key(ec.SECP256R1()), GRANT_PURPOSE)


@pytest.mark.parametrize("claims", [
    {"aud": "clayrune-mail-enrollment", "token_use": PURPOSE}, {"aud": PURPOSE, "token_use": "other"},
    {"aud": "clayrune-dashboard", "token_use": "clayrune-dashboard"}, {"aud": PURPOSE}, {}])
def test_delivery_key_refuses_to_sign_any_other_purpose(claims):
    with pytest.raises(DeliveryKeyError, match="wrong_signing_purpose"):
        delivery_key().sign(claims)


def test_the_other_key_classes_cannot_mint_delivery_tokens():
    grant = MailGrantKey("mail-grant-t", ec.generate_private_key(ec.SECP256R1()), GRANT_PURPOSE)
    with pytest.raises(ValueError, match="wrong_signing_purpose"):
        grant.sign(CLAIMS)  # the enrollment-grant key refuses the delivery purpose
    # And the delivery key class cannot be built over a grant-purpose key at all (above), so
    # no delivery key can ever produce an enrollment grant either.
    with pytest.raises(DeliveryKeyError, match="wrong_signing_purpose"):
        delivery_key().sign({"aud": GRANT_PURPOSE, "token_use": GRANT_PURPOSE})
    # Session signing is a different module with a different key set: the delivery JWKS never
    # contains a session kid and the session JWKS never a delivery kid.
    session_kids = {k["kid"] for k in jwt_es256.jwks()["keys"]}
    assert not any(kid.startswith("mail-delivery-") for kid in session_kids)
    assert not {k["kid"] for k in DeliveryKeyring(((delivery_key(), True),)).jwks()["keys"]} & session_kids


def test_jwks_publishes_every_key_public_only():
    a, b = delivery_key("mail-delivery-a"), delivery_key("mail-delivery-b")
    ring = DeliveryKeyring(((a, False), (b, True)))
    keys = ring.jwks()["keys"]
    assert [k["kid"] for k in keys] == ["mail-delivery-a", "mail-delivery-b"]
    for k in keys:
        assert set(k) == {"kid", "kty", "crv", "alg", "use", "x", "y"} and "d" not in k
        assert (k["kty"], k["crv"], k["alg"], k["use"]) == ("EC", "P-256", "ES256", "sig")


def test_rotation_signs_with_first_active_while_old_key_stays_published():
    old, new = delivery_key("mail-delivery-old"), delivery_key("mail-delivery-new")
    publish_only = DeliveryKeyring(((old, True), (new, False)))   # step 1: publish the new key
    assert publish_only.signing_key().kid == "mail-delivery-old"
    switched = DeliveryKeyring(((new, True), (old, False)))       # step 2: sign with it, keep old published
    assert switched.signing_key().kid == "mail-delivery-new"
    assert {k["kid"] for k in switched.jwks()["keys"]} == {"mail-delivery-old", "mail-delivery-new"}
    dropped = DeliveryKeyring(((new, True),))                     # step 3: drop after one token lifetime
    assert {k["kid"] for k in dropped.jwks()["keys"]} == {"mail-delivery-new"}


def test_keyring_refuses_empty_duplicate_or_no_active():
    k = delivery_key()
    for entries in [(), ((k, True), (k, True)), ((k, False),)]:
        with pytest.raises(DeliveryKeyError, match="mail_delivery_keyring_invalid"):
            DeliveryKeyring(entries)


def test_config_parse_has_no_default_and_no_ephemeral_fallback(monkeypatch):
    monkeypatch.setenv("CLAYRUNE_ALLOW_EPHEMERAL_KEY", "1")  # the session-key escape hatch must not apply here
    for raw in (None, "", "   "):
        with pytest.raises(DeliveryKeyError, match="mail_delivery_keys_not_configured"):
            parse_delivery_keys(raw)


@pytest.mark.parametrize("raw", ["not json", "{}", "[]", '[1]', '[{"kid":"mail-delivery-a"}]',
                                 '[{"kid":"mail-delivery-a","pem":"nope"}]'])
def test_config_parse_refuses_malformed(raw):
    with pytest.raises(DeliveryKeyError, match="mail_delivery_keys_invalid"):
        parse_delivery_keys(raw)


def test_config_parse_refuses_extra_fields_wrong_curve_wrong_kid_and_no_active():
    p256 = pem(ec.generate_private_key(ec.SECP256R1()))
    ok = {"kid": "mail-delivery-a", "pem": p256, "active": True}
    assert parse_delivery_keys(json.dumps([ok])).signing_key().kid == "mail-delivery-a"
    for bad in [{**ok, "extra": 1}, {**ok, "kid": "mail-grant-a"}, {**ok, "pem": pem(ec.generate_private_key(ec.SECP384R1()))},
                {**ok, "pem": pem(ed25519.Ed25519PrivateKey.generate())}]:
        with pytest.raises((DeliveryKeyError,)):
            parse_delivery_keys(json.dumps([bad]))
    with pytest.raises(DeliveryKeyError, match="mail_delivery_keyring_invalid"):
        parse_delivery_keys(json.dumps([{**ok, "active": False}]))
