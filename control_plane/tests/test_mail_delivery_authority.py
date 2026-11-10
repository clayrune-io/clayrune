"""Bind / issue / lease / revoke behaviour of MailDeliveryAuthority (7d-2b, contract 1.2-1.5)."""
from __future__ import annotations

import hashlib

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from control_plane.app.mail_delivery_authority import DeliveryRefused
from control_plane.app.mail_delivery_signing import DeliveryKeyring
from control_plane.app.mail_grant_signing import MailGrantKey, PURPOSE as GRANT_PURPOSE
from control_plane.app.mail_service_auth import ServiceKeyring
from control_plane.tests.mail_delivery_support import (
    INSTALL, ISSUER, KEY_ID, World, delivery_key, payload, raw_public, run)

BIND = "/v1/mail-delivery/bind"
REVOKE = "/v1/mail-delivery/revoke"
LEASE = "/v1/mail-delivery/lease"
SCOPE = {"install_id": INSTALL, "generation": "1"}


@pytest.fixture
def w(mem_firestore):
    return World(mem_firestore)


def refused(fn, *a, **kw):
    with pytest.raises(DeliveryRefused) as e:
        fn(*a, **kw)
    return e.value.code


def arefused(coro):
    with pytest.raises(DeliveryRefused) as e:
        run(coro)
    return e.value.code


# ---- bind ---------------------------------------------------------------------------------

def test_bind_creates_binding_from_verified_grant(w):
    out = w.bind()
    assert out == {"install_id": INSTALL, "generation": "1", "state": "bound", "idempotent": False}
    b = w.store.get_binding(INSTALL)
    assert (b["generation"], b["state"], b["generation_floor"], b["user_id"], b["device_id"]) == (
        1, "bound", 0, "account1", "device1")
    assert b["route_origin"] == "https://device.example.com" and b["service_key_id"] == KEY_ID
    assert b["service_public_key_sha256"] == hashlib.sha256(w.service_public).hexdigest()


def test_bind_is_idempotent_on_grant_jti(w):
    token = w.grant()
    w.t[0] += 500
    first = w.authority.bind(w.call(path=BIND), {"grant": token, "r": w.r})
    second = w.authority.bind(w.call(path=BIND), {"grant": token, "r": w.r})
    assert first["idempotent"] is False and second["idempotent"] is True


def test_second_different_grant_for_same_generation_is_binding_conflict(w):
    w.bind()
    w.t[0] += 5
    assert refused(w.bind) == "binding_conflict"


def test_bind_refuses_grant_older_than_ownership_lease(w):
    token = w.grant()
    w.t[0] += 300_000
    assert refused(w.authority.bind, w.call(path=BIND), {"grant": token, "r": w.r}) == "bad_grant"


def test_bind_refuses_grant_from_the_future_or_wrong_key(w):
    token = w.grant()
    w.t[0] -= 5000  # grant iat is now in the future
    assert refused(w.authority.bind, w.call(path=BIND), {"grant": token, "r": w.r}) == "bad_grant"
    w.t[0] += 5500
    other = MailGrantKey("mail-grant-test", ec.generate_private_key(ec.SECP256R1()), GRANT_PURPOSE)
    w.authority.grant_jwks = [other.public_jwk()]  # same kid, different key material
    assert refused(w.authority.bind, w.call(path=BIND), {"grant": token, "r": w.r}) == "bad_grant"


def test_bind_refuses_tampered_grant_and_wrong_transcript(w):
    token = w.grant()
    h, c, s = token.split(".")
    bad = f"{h}.{c[:-2]}AA.{s}"
    assert refused(w.authority.bind, w.call(path=BIND), {"grant": bad, "r": w.r}) == "bad_grant"
    other = list(w.r)
    other[5] = "f" * 64  # transcript no longer hashes to grant.r_sha256
    assert refused(w.authority.bind, w.call(path=BIND), {"grant": token, "r": other}) == "bad_grant"


def test_bind_requires_the_transcript_service_key(w):
    token = w.grant()
    other = ed25519.Ed25519PrivateKey.generate()
    w.authority.service_keys = ServiceKeyring({KEY_ID: raw_public(other)})  # key hash != R.service_public_key_sha256
    assert refused(w.authority.bind, w.call(path=BIND), {"grant": token, "r": w.r}) == "service_key_mismatch"
    w.authority.service_keys = ServiceKeyring({KEY_ID: w.service_public, "svc2": raw_public(other)})
    assert refused(w.authority.bind, w.call(path=BIND, key_id="svc2"),
                   {"grant": token, "r": w.r}) == "service_key_mismatch"


def test_bind_scope_must_match_install_and_generation(w):
    token = w.grant()
    assert refused(w.authority.bind, w.call(path=BIND, generation="2"),
                   {"grant": token, "r": w.r}) == "scope_mismatch"
    assert refused(w.authority.bind, w.call(path=BIND, install="other"),
                   {"grant": token, "r": w.r}) == "scope_mismatch"
    assert refused(w.authority.bind, w.call(path=BIND), {"grant": token}) == "bad_request"
    assert refused(w.authority.bind, w.call(path=BIND), {"grant": token, "r": ["x"]}) == "bad_transcript"


# ---- issue --------------------------------------------------------------------------------

def test_issue_requires_a_binding(w):
    assert arefused(w.authority.issue(w.call(), w.issue_body())) == "binding_required"


def test_issue_happy_path_claims_and_signature_purpose(w):
    w.bind()
    body = w.issue_body(req_digest="9" * 64)
    out = w.issue(body)
    c = payload(out["capability"])
    assert c["iss"] == ISSUER and c["aud"] == c["token_use"] == "clayrune-mail-delivery"
    assert c["sub"] == "account1" and c["install_id"] == INSTALL and c["generation"] == "1" and c["mode"] == "mail"
    assert c["route_origin"] == "https://device.example.com" and c["route_path"] == "/api/agent-mail/ingress"
    assert c["method"] == "POST" and c["req_digest"] == "9" * 64 and c["mail_nonce"] == body["mail_nonce"]
    assert c["iat"] == c["nbf"] and c["exp"] - c["iat"] == 60 and c["exp"] <= c["checked_at_ms"] // 1000 + 300
    assert c["checked_at_ms"] <= c["iat"] * 1000 + 999 and len(c["jti"]) == 32 and out["jti"] == c["jti"]
    assert c["device_id"] == "device1" and c["binding_jti"] and len(c["record_version"]) == 64
    assert set(c) == {"iss", "aud", "token_use", "sub", "jti", "iat", "nbf", "exp", "mode", "install_id", "generation",
                      "device_id", "route_origin", "route_path", "method", "req_digest", "mail_nonce",
                      "mail_key_id", "checked_at_ms", "record_version", "binding_jti"}


def test_route_origin_and_user_come_from_cp_not_the_caller(w):
    w.bind()
    c = payload(w.issue(w.issue_body(mode="proof"))["capability"])
    assert c["route_origin"] == "https://device.example.com" and c["sub"] == "account1" and c["mode"] == "proof"
    assert arefused(w.authority.issue(w.call(), {**w.issue_body(), "route_origin": "https://evil.example"})) == "bad_request"
    assert arefused(w.authority.issue(w.call(), {**w.issue_body(), "sub": "attacker"})) == "bad_request"


def test_issue_scope_generation_mode_and_field_refusals(w):
    w.bind()
    assert arefused(w.authority.issue(w.call(generation="2"), w.issue_body(generation="2"))) == "binding_required"
    assert arefused(w.authority.issue(w.call(generation="1"), w.issue_body(generation="2"))) == "scope_mismatch"
    assert arefused(w.authority.issue(w.call(install="other"), w.issue_body())) == "scope_mismatch"
    for bad in ({"mode": "x"}, {"req_digest": "E" * 64}, {"req_digest": "e" * 63}, {"mail_nonce": "ab"},
                {"mail_key_id": "bad key"}):
        assert arefused(w.authority.issue(w.call(), w.issue_body(**bad))) == "bad_request"
    assert arefused(w.authority.issue(w.call(), {"install_id": INSTALL})) == "bad_request"
    assert arefused(w.authority.issue(w.call(), w.issue_body(mail_key_id="other"))) == "key_mismatch"


def test_issue_refuses_a_different_service_key(w):
    w.bind()
    other = ed25519.Ed25519PrivateKey.generate()
    w.authority.service_keys = ServiceKeyring({KEY_ID: w.service_public, "svc2": raw_public(other)})
    assert arefused(w.authority.issue(w.call(key_id="svc2"), w.issue_body())) == "service_key_mismatch"
    w.authority.service_keys = ServiceKeyring({KEY_ID: raw_public(other)})  # key rotated away
    assert arefused(w.authority.issue(w.call(), w.issue_body())) == "service_key_mismatch"


@pytest.mark.parametrize("row,field,value,code", [
    ("device", "revoked_at", "2026-01-01", "authority_refused"),
    ("device", "provisioning_state", "pending", "authority_refused"),
    ("account", "suspended", True, "authority_refused"),
    ("device", "device_pub_b64", "AAAA", "device_key_mismatch"),
    ("device", "hostname_claim", "evil.example.com", "provisioning_mismatch"),
    ("device", "cf_tunnel_uuid", "00000000-0000-0000-0000-000000000000", "provisioning_mismatch"),
    ("tunnel", "deleted_at", "x", "cloudflare_mapping_mismatch"),
    ("dns", "proxied", False, "cloudflare_mapping_mismatch"),
])
def test_issue_refuses_when_fresh_authority_read_disagrees(w, row, field, value, code):
    w.bind()
    w.reader.rows[row][field] = value
    assert arefused(w.authority.issue(w.call(), w.issue_body())) == code


def test_issue_refuses_when_the_account_owner_changed(w):
    w.bind()
    w.reader.rows["device"]["user_id"] = "account2"
    w.reader.rows["account"]["user_id"] = "account2"
    assert arefused(w.authority.issue(w.call(), w.issue_body())) == "authority_refused"


def test_issue_closed_when_authority_unreadable_or_unversioned_or_no_reader(w):
    w.bind()
    w.reader.fail = "tunnel"
    assert arefused(w.authority.issue(w.call(), w.issue_body())) == "authority_unavailable"
    w.reader.fail = None
    w.reader.no_version = "dns"
    assert arefused(w.authority.issue(w.call(), w.issue_body())) == "authority_version_required"
    w.authority.reader = None
    assert arefused(w.authority.issue(w.call(), w.issue_body())) == "authority_unavailable"


def test_overrun_of_the_read_window_is_refused(w):
    w.bind()
    orig = w.reader.read

    async def slow(kind, identifier):
        out = await orig(kind, identifier)
        w.t[0] += 61_000  # provider latency pushes the check past the 60 s token window
        return out
    w.reader.read = slow
    assert arefused(w.authority.issue(w.call(), w.issue_body())) == "authority_check_expired"
    assert arefused(w.authority.lease(w.call(), SCOPE)) == "authority_check_expired"


def test_replay_same_digest_resigns_different_digest_is_nonce_reused(w):
    w.bind()
    body = w.issue_body()
    a = w.issue(body)
    b = w.issue(body)
    assert a["jti"] != b["jti"]  # retry after a lost response re-signs a fresh capability
    assert arefused(w.authority.issue(w.call(), {**body, "req_digest": "f" * 64})) == "nonce_reused"
    assert w.issue(w.issue_body())["capability"]  # a new nonce is independent


def test_issue_budget_per_install_and_global(w):
    w.bind()
    w.authority.install_budget = 2
    w.issue()
    w.issue()
    assert arefused(w.authority.issue(w.call(), w.issue_body())) == "issue_budget"
    w.authority.install_budget = 600
    w.authority.global_budget = 2
    assert arefused(w.authority.issue(w.call(), w.issue_body())) == "issue_budget"


def test_revoked_binding_refuses_issue_and_lease(w):
    w.bind()
    assert w.authority.revoke(w.call(path=REVOKE), SCOPE)["state"] == "revoked"
    assert arefused(w.authority.issue(w.call(), w.issue_body())) == "binding_required"
    assert arefused(w.authority.lease(w.call(), SCOPE)) == "binding_required"


def test_issue_signs_with_the_current_key_and_old_key_stays_published(w):
    old, new = w.delivery, delivery_key("mail-delivery-new")
    w.authority.keyring = DeliveryKeyring(((new, True), (old, False)))
    w.bind()
    import base64
    import json
    header = json.loads(base64.urlsafe_b64decode(w.issue()["capability"].split(".")[0] + "=="))
    assert header["kid"] == "mail-delivery-new"
    assert {k["kid"] for k in w.authority.keyring.jwks()["keys"]} == {"mail-delivery-test", "mail-delivery-new"}


# ---- lease --------------------------------------------------------------------------------

def test_lease_reports_authority_read_and_five_minute_ceiling(w):
    w.bind()
    out = run(w.authority.lease(w.call(path=LEASE), SCOPE))
    assert out["lease_until_ms"] - out["checked_at_ms"] == 300_000 and len(out["record_version"]) == 64
    w.reader.rows["account"]["suspended"] = True
    assert arefused(w.authority.lease(w.call(), SCOPE)) == "authority_refused"


# ---- revoke -------------------------------------------------------------------------------

def test_revoke_is_idempotent_and_exact_generation(w):
    w.bind()
    assert w.authority.revoke(w.call(path=REVOKE), SCOPE)["state"] == "revoked"
    assert w.authority.revoke(w.call(path=REVOKE), SCOPE)["state"] == "revoked"
    assert w.store.get_binding(INSTALL)["generation_floor"] == 1


def test_revoke_before_bind_fences_a_later_bind(w):
    token = w.grant()
    w.t[0] += 100
    assert w.authority.revoke(w.call(path=REVOKE), SCOPE)["state"] == "fenced"
    assert refused(w.authority.bind, w.call(path=BIND), {"grant": token, "r": w.r}) == "revoked_generation"


def test_revoking_an_old_generation_never_touches_a_newer_binding(w):
    w.bind()
    r2 = list(w.r)
    r2[8] = "1"  # expected_generation 1 -> bound generation 2
    w.t[0] += 10
    token = w.grant(r2)
    w.t[0] += 10
    out = w.authority.bind(w.call(path=BIND, generation="2"), {"grant": token, "r": r2})
    assert out["generation"] == "2"
    res = w.authority.revoke(w.call(path=REVOKE), SCOPE)
    assert res["state"] == "already_superseded"
    b = w.store.get_binding(INSTALL)
    assert b["generation"] == 2 and b["state"] == "bound" and b["generation_floor"] == 1
    assert w.issue(w.issue_body(generation="2"), generation="2")["capability"]
    assert arefused(w.authority.issue(w.call(generation="1"), w.issue_body(generation="1"))) == "binding_required"


def test_revoke_scope_and_service_key_checks(w):
    w.bind()
    assert refused(w.authority.revoke, w.call(generation="2"), SCOPE) == "scope_mismatch"
    assert refused(w.authority.revoke, w.call(), {"install_id": INSTALL}) == "bad_request"
    other = ed25519.Ed25519PrivateKey.generate()
    w.authority.service_keys = ServiceKeyring({KEY_ID: w.service_public, "svc2": raw_public(other)})
    assert refused(w.authority.revoke, w.call(key_id="svc2"), SCOPE) == "service_key_mismatch"
