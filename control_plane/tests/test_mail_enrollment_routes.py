"""HTTP surface of /v1/mail-enrollment (7d-2e-1, contract 1.2/1.3): default off, closed codes, budgets."""
from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

from control_plane.app import routes_mail_enrollment as rme
from control_plane.app.mail_enrollment_store import COL_GRANT_BUDGET, EnrollmentStore, hour_key
from control_plane.app.mail_enrollment_transcript import FIELDS, b64u, canonical_r
from control_plane.app.mail_grant_keyring import MailGrantKeyring
from control_plane.app.main import app
from control_plane.app.mail_service_auth import RefusalBucket
from control_plane.tests.mail_delivery_support import ISSUER, SERVICE_ORIGIN, World, payload, raw_public, sign_service_call
from control_plane.tests.test_mail_grants import NOW, TUNNEL

GRANT, JWKS, PREFLIGHT = "/v1/mail-enrollment/grant", "/v1/mail-enrollment/jwks", "/v1/mail-enrollment/preflight"
ALL_POSTS = (GRANT, PREFLIGHT)


@pytest.fixture
def w(mem_firestore):
    return World(mem_firestore)


@pytest.fixture
def api(w, mem_firestore):
    deps = rme.EnrollmentDeps(
        enabled=True, keyring=MailGrantKeyring(((w.grant_key, True),)), service_keys=w.service_keys,
        reader=w.reader, issuer=ISSUER, service_origin=SERVICE_ORIGIN,
        store=EnrollmentStore(db=lambda: mem_firestore), bucket=RefusalBucket(), clock=lambda: w.t[0])
    app.dependency_overrides[rme.get_deps] = lambda: deps
    yield TestClient(app), deps
    app.dependency_overrides.pop(rme.get_deps, None)


def sigs(w, r=None, *, device=None, mail=None):
    try:
        raw = canonical_r(r or w.r)
    except ValueError:  # deliberately malformed transcripts still need *some* signature bytes
        raw = b"malformed"
    return {"device_signature": b64u((device or w.device).sign(raw)), "mail_signature": b64u((mail or w.mail).sign(raw))}


def grant_body(w, r=None, **over):
    body = {"r": r or w.r, **sigs(w, r)}
    body.update(over)
    return body


def post_grant(c, body=None, *, raw=None, headers=None, params=None):
    data = raw if raw is not None else json.dumps(body).encode()
    return c.post(GRANT, content=data, headers={"content-type": "application/json", **(headers or {})}, params=params)


def post_preflight(c, w, body, *, raw=None, path=PREFLIGHT, headers=None, sign_path=None):
    data = raw if raw is not None else json.dumps(body).encode()
    h = sign_service_call(w.service_private, path=sign_path or path, body=data, now_ms=int(time.time() * 1000))
    h.update(headers or {})
    return c.post(path, content=data, headers=h)


def pre_body(w, **over):
    body = {"device_id": "device1", "device_public_key": b64u(raw_public(w.device)),
            "tunnel_uuid": TUNNEL, "route_origin": "https://device.example.com"}
    body.update(over)
    return body


def with_r(w, **fields):
    r = list(w.r)
    for k, v in fields.items():
        r[FIELDS.index(k)] = v
    return r


def budget_rows(mem):
    return mem.dump().get(COL_GRANT_BUDGET, {})


# ---- default off ---------------------------------------------------------------------------

def test_default_off_every_route_is_404_and_reads_nothing(monkeypatch):
    monkeypatch.delenv("CLAYRUNE_MAIL_ENROLLMENT_ENABLED", raising=False)
    app.dependency_overrides.pop(rme.get_deps, None)

    def boom(*a, **k):
        raise AssertionError("flag off must not touch the authority reader, keys or store")
    monkeypatch.setattr(rme, "authority_reader_from_env", boom)
    monkeypatch.setattr(rme, "parse_grant_keys", boom)
    monkeypatch.setattr(rme, "parse_service_keys", boom)
    monkeypatch.setattr(rme, "_STORE", None)
    c = TestClient(app)
    assert c.get(JWKS).status_code == 404
    for path in ALL_POSTS:
        r = c.post(path, content=b"{}", headers={"content-type": "application/json"})
        assert r.status_code == 404, path


@pytest.mark.parametrize("value", ["", "0", "false", "yes", "on"])
def test_only_1_or_true_enables(monkeypatch, value):
    monkeypatch.setenv("CLAYRUNE_MAIL_ENROLLMENT_ENABLED", value)
    assert rme.get_deps().enabled is False


def test_the_delivery_flag_does_not_enable_enrollment(monkeypatch):
    monkeypatch.delenv("CLAYRUNE_MAIL_ENROLLMENT_ENABLED", raising=False)
    monkeypatch.setenv("CLAYRUNE_MAIL_DELIVERY_ENABLED", "1")
    assert rme.get_deps().enabled is False


def test_enabled_without_reviewed_config_answers_503_not_configured(monkeypatch):
    monkeypatch.setenv("CLAYRUNE_MAIL_ENROLLMENT_ENABLED", "1")
    for v in ("CLAYRUNE_MAIL_GRANT_KEYS", "CLAYRUNE_MAIL_SERVICE_KEYS", "CLAYRUNE_MAIL_SERVICE_ORIGIN",
              "CLAYRUNE_MAIL_AUTHORITY_READ_ENABLED"):
        monkeypatch.delenv(v, raising=False)
    app.dependency_overrides.pop(rme.get_deps, None)
    c = TestClient(app)
    r = c.get(JWKS)
    assert r.status_code == 503 and r.json()["code"] == "not_configured"
    r = post_grant(c, {"r": [], "device_signature": "", "mail_signature": ""})
    assert r.status_code == 503 and r.json()["code"] == "not_configured"
    r = c.post(PREFLIGHT, content=b"{}", headers={"content-type": "application/json"})
    assert r.status_code == 503 and r.json()["code"] == "not_configured"


def test_reader_absent_grant_and_preflight_answer_503_authority_unavailable(api, w):
    c, deps = api
    deps.reader = None
    r = post_grant(c, grant_body(w))
    assert r.status_code == 503 and r.json()["code"] == "authority_unavailable"
    r = post_preflight(c, w, pre_body(w))
    assert r.status_code == 503 and r.json()["code"] == "authority_unavailable"


# ---- jwks ----------------------------------------------------------------------------------

def test_jwks_publishes_only_grant_public_keys_with_cache_header(api, w):
    c, _ = api
    r = c.get(JWKS)
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=600"
    keys = r.json()["keys"]
    assert [k["kid"] for k in keys] == ["mail-grant-test"]
    assert all(set(k) == {"kid", "kty", "crv", "alg", "use", "x", "y"} for k in keys)


def test_session_and_delivery_jwks_are_separate_sets(api, w):
    c, _ = api
    kids = {k["kid"] for k in c.get("/v1/jwks").json().get("keys", [])}
    assert "mail-grant-test" not in kids


def test_jwks_not_configured_is_503(api):
    c, deps = api
    deps.keyring = None
    r = c.get(JWKS)
    assert r.status_code == 503 and r.json()["code"] == "not_configured"


# ---- grant: happy path ---------------------------------------------------------------------

def test_grant_happy_path_mints_a_60s_enrollment_grant_bound_to_r(api, w, mem_firestore):
    c, _ = api
    r = post_grant(c, grant_body(w))
    assert r.status_code == 200, r.text
    claims = payload(r.json()["grant"])
    assert claims["aud"] == claims["token_use"] == "clayrune-mail-enrollment"
    assert claims["exp"] - claims["iat"] == 60 and claims["sub"] == "account1"
    assert (claims["device_id"], claims["install_id"], claims["tunnel_uuid"]) == ("device1", "install1", TUNNEL)
    assert len(budget_rows(mem_firestore)) == 1


def test_grant_is_not_idempotent_each_call_mints_a_fresh_jti(api, w):
    c, _ = api
    a = payload(post_grant(c, grant_body(w)).json()["grant"])
    b = payload(post_grant(c, grant_body(w)).json()["grant"])
    assert a["jti"] != b["jti"] and a["r_sha256"] == b["r_sha256"]


def test_a_grant_from_the_route_is_accepted_by_the_delivery_bind(api, w):
    """The keyring's JWKS is a superset of what `bind` accepts: bind verifies this very token."""
    c, _ = api
    token = post_grant(c, grant_body(w)).json()["grant"]
    w.t[0] += 1000
    out = w.authority.bind(w.call(path="/v1/mail-delivery/bind"), {"grant": token, "r": w.r})
    assert out is not None


# ---- grant: closed codes -------------------------------------------------------------------

def test_every_grant_refused_code_maps_to_its_status(api, w):
    c, deps = api

    def code_of(r, status):
        assert r.status_code == status, r.text
        body = r.json()
        assert body["message"] == body["code"] and "request_id" in body
        return body["code"]

    # bad_transcript is not a GrantRefused: a 400, never a 500.
    assert code_of(post_grant(c, grant_body(w, r=w.r[:5])), 400) == "bad_transcript"
    assert code_of(post_grant(c, {**grant_body(w), "r": ["x"] * 20}), 400) == "bad_transcript"
    # challenge_unavailable: long-lived challenge and wrong service origin.
    long_r = with_r(w, expires_ms=str(NOW + 901_000))
    assert code_of(post_grant(c, grant_body(w, r=long_r)), 409) == "challenge_unavailable"
    wrong_origin = with_r(w, service_origin="https://other.example.com")
    assert code_of(post_grant(c, grant_body(w, r=wrong_origin)), 409) == "challenge_unavailable"
    # authority_refused: suspended account.
    w.reader.rows["account"]["suspended"] = True
    assert code_of(post_grant(c, grant_body(w)), 403) == "authority_refused"
    w.reader.rows["account"]["suspended"] = False
    # device_unavailable: the row has no usable user id.
    saved = w.reader.rows["device"]["user_id"]
    w.reader.rows["device"]["user_id"] = "bad id!"
    assert code_of(post_grant(c, grant_body(w)), 403) == "device_unavailable"
    w.reader.rows["device"]["user_id"] = saved
    # device_key_unavailable: the row key is not base64.
    saved = w.reader.rows["device"]["device_pub_b64"]
    w.reader.rows["device"]["device_pub_b64"] = "!!!"
    assert code_of(post_grant(c, grant_body(w)), 403) == "device_key_unavailable"
    w.reader.rows["device"]["device_pub_b64"] = saved
    # provisioning_mismatch and cloudflare_mapping_mismatch.
    w.reader.rows["device"]["cf_tunnel_uuid"] = "00000000-0000-0000-0000-000000000000"
    assert code_of(post_grant(c, grant_body(w)), 403) == "provisioning_mismatch"
    w.reader.rows["device"]["cf_tunnel_uuid"] = TUNNEL
    w.reader.rows["dns"]["proxied"] = False
    assert code_of(post_grant(c, grant_body(w)), 403) == "cloudflare_mapping_mismatch"
    w.reader.rows["dns"]["proxied"] = True
    # authority_unavailable (backend failure) and authority_version_required.
    w.reader.fail = "dns"
    r = post_grant(c, grant_body(w))
    assert code_of(r, 503) == "authority_unavailable" and "provider-test-only-secret" not in r.text
    w.reader.fail = None
    w.reader.no_version = "dns"
    assert code_of(post_grant(c, grant_body(w)), 503) == "authority_version_required"
    w.reader.no_version = None
    # authority_check_expired: reads drag past the 60 s token window.
    real_account = w.reader.account

    async def slow(identifier):
        w.t[0] += 61_000
        return await real_account(identifier)
    w.reader.account = slow
    assert code_of(post_grant(c, grant_body(w)), 503) == "authority_check_expired"
    w.reader.account = real_account
    # And it still works once everything is healthy again.
    assert post_grant(c, grant_body(w)).status_code == 200


def test_mail_grant_disabled_and_every_issuer_code_have_a_status():
    from control_plane.app import mail_grants, mail_authority_check
    import re
    import pathlib
    src = "".join(pathlib.Path(m.__file__).read_text(encoding="utf-8") for m in (mail_grants, mail_authority_check))
    codes = set(re.findall(r'GrantRefused\("([a-z_]+)"\)', src))
    assert codes and codes <= set(rme._STATUS), codes - set(rme._STATUS)


def test_a_flipped_byte_in_either_signature_is_key_proof_required(api, w):
    c, _ = api
    for field in ("device_signature", "mail_signature"):
        body = grant_body(w)
        raw = bytearray(base64.urlsafe_b64decode(body[field] + "=="))
        raw[10] ^= 1
        body[field] = b64u(bytes(raw))
        r = post_grant(c, body)
        assert r.status_code == 403 and r.json()["code"] == "key_proof_required", field


def test_signatures_swapped_between_keys_are_key_proof_required(api, w):
    c, _ = api
    s = sigs(w)
    r = post_grant(c, {"r": w.r, "device_signature": s["mail_signature"], "mail_signature": s["device_signature"]})
    assert r.status_code == 403 and r.json()["code"] == "key_proof_required"


def test_device_key_not_matching_the_row_is_device_key_mismatch(api, w):
    c, _ = api
    other = ed25519.Ed25519PrivateKey.generate()
    r_other = with_r(w, device_public_key=b64u(raw_public(other)))
    r = post_grant(c, grant_body(w, r=r_other, **sigs(w, r_other, device=other)))
    assert r.status_code == 403 and r.json()["code"] == "device_key_mismatch"


# ---- grant: request shape ------------------------------------------------------------------

def test_shape_refusals(api, w):
    c, _ = api
    assert post_grant(c, grant_body(w), headers={"content-type": "text/plain"}).status_code == 400
    assert post_grant(c, grant_body(w), params={"x": "1"}).status_code == 400
    r = post_grant(c, raw=b"not json")
    assert r.status_code == 400 and r.json()["code"] == "invalid_json"
    r = post_grant(c, raw=b"[1]")
    assert r.status_code == 400 and r.json()["code"] == "invalid_json"
    assert post_grant(c, {**grant_body(w), "extra": 1}).json()["code"] == "invalid_json"
    assert post_grant(c, {"r": w.r, "device_signature": 1, "mail_signature": 2}).json()["code"] == "bad_request"


def test_body_over_8_kib_is_413(api, w):
    c, _ = api
    big = b'{"x":"' + b"a" * (8 * 1024) + b'"}'
    r = post_grant(c, raw=big)
    assert r.status_code == 413 and r.json()["code"] == "bad_request"


def test_missing_content_length_is_refused(api, w):
    c, _ = api
    data = json.dumps(grant_body(w)).encode()
    r = c.post(GRANT, content=iter([data]), headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["code"] == "bad_request"


def test_no_session_cookie_bearer_or_device_header_is_read_or_needed(api, w):
    c, _ = api
    r = post_grant(c, grant_body(w), headers={"authorization": "Bearer junk", "cookie": "mc_session=junk"})
    assert r.status_code == 200


# ---- grant: budget and abuse bucket --------------------------------------------------------

def test_budget_exhausted_is_429_grant_budget(api, w, mem_firestore):
    c, deps = api
    deps.grant_budget = 2
    assert [post_grant(c, grant_body(w)).status_code for _ in range(2)] == [200, 200]
    r = post_grant(c, grant_body(w))
    assert r.status_code == 429 and r.json()["code"] == "grant_budget"
    assert list(budget_rows(mem_firestore).values())[0]["n"] == 2  # a refused attempt is not counted


def test_budget_is_per_hour_and_per_device(api, w, mem_firestore):
    c, deps = api
    deps.grant_budget = 1
    assert post_grant(c, grant_body(w)).status_code == 200
    assert post_grant(c, grant_body(w)).status_code == 429
    w.t[0] += 3_600_000
    later = with_r(w, expires_ms=str(w.t[0] + 900_000))  # the old challenge has expired by now
    assert post_grant(c, grant_body(w, r=later)).status_code == 200
    keys = set(budget_rows(mem_firestore))
    assert keys == {f"device1.{hour_key(NOW)}", f"device1.{hour_key(NOW + 3_600_000)}"}


def test_refusals_before_possession_do_not_spend_the_device_budget(api, w, mem_firestore):
    c, deps = api
    deps.grant_budget = 1
    bad = grant_body(w)
    bad["device_signature"] = bad["mail_signature"]
    for _ in range(3):
        assert post_grant(c, bad).status_code == 403
        assert post_grant(c, grant_body(w, r=w.r[:3])).status_code == 400
        assert post_grant(c, raw=b"nope").status_code == 400
    assert budget_rows(mem_firestore) == {}
    assert post_grant(c, grant_body(w)).status_code == 200


def test_a_stranger_signing_with_their_own_keys_cannot_spend_a_real_devices_budget(api, w, mem_firestore):
    """Both signatures verify against the keys written into R, so naming a real device_id is free.
    The budget must only move once the row's key equals the key in R."""
    c, deps = api
    deps.grant_budget = 2
    stranger_dev, stranger_mail = ed25519.Ed25519PrivateKey.generate(), ed25519.Ed25519PrivateKey.generate()
    forged = with_r(w, device_public_key=b64u(raw_public(stranger_dev)), mail_public_key=b64u(raw_public(stranger_mail)))
    for _ in range(5):
        r = post_grant(c, grant_body(w, r=forged, **sigs(w, forged, device=stranger_dev, mail=stranger_mail)))
        assert r.status_code == 403 and r.json()["code"] == "device_key_mismatch"
    assert budget_rows(mem_firestore) == {}
    assert [post_grant(c, grant_body(w)).status_code for _ in range(2)] == [200, 200]


def test_budget_storage_failure_is_503_without_leaking(api, w):
    c, deps = api

    class Broken:
        def consume_grant_budget(self, *a, **k):
            raise OSError("firestore down: private-detail")
    deps.store = Broken()
    r = post_grant(c, grant_body(w))
    assert r.status_code == 503 and r.json()["code"] == "storage_or_configuration_failure"
    assert "private-detail" not in r.text


def test_unexpected_failure_is_503_without_leaking(api, w):
    c, deps = api

    def boom(*a, **k):
        raise RuntimeError("private-key-material")
    deps.keyring = type("K", (), {"signing_key": staticmethod(boom), "jwks": staticmethod(boom)})()
    r = post_grant(c, grant_body(w))
    assert r.status_code == 503 and r.json()["code"] == "storage_or_configuration_failure"
    assert "private-key-material" not in r.text


def test_unauthenticated_refusals_share_one_bounded_bucket(api, w):
    c, deps = api
    deps.bucket = RefusalBucket(limit=2)
    bad = lambda: post_grant(c, raw=b"nope")
    assert [(r := bad()).status_code for _ in range(3)] == [400, 400, 429] and r.json()["code"] == "abuse_limit"
    # A valid, possession-proven caller is never charged to, nor locked out by, the bucket.
    assert post_grant(c, grant_body(w)).status_code == 200


def test_refusals_after_possession_do_not_charge_the_bucket(api, w):
    c, deps = api
    deps.bucket = RefusalBucket(limit=1)
    w.reader.rows["account"]["suspended"] = True
    assert [post_grant(c, grant_body(w)).status_code for _ in range(4)] == [403] * 4


# ---- preflight (E2a) -----------------------------------------------------------------------

def test_preflight_accepts_a_real_active_device(api, w):
    c, _ = api
    r = post_preflight(c, w, pre_body(w))
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_preflight_refuses_what_the_grant_would_refuse(api, w):
    c, _ = api
    other = b64u(raw_public(ed25519.Ed25519PrivateKey.generate()))
    cases = [
        (pre_body(w, device_public_key=other), 403, "device_key_mismatch"),
        (pre_body(w, tunnel_uuid="00000000-0000-0000-0000-000000000000"), 403, "provisioning_mismatch"),
        (pre_body(w, route_origin="https://other.example.com"), 403, "provisioning_mismatch"),
    ]
    for body, status, code in cases:
        r = post_preflight(c, w, body)
        assert (r.status_code, r.json()["code"]) == (status, code)
    w.reader.rows["account"]["suspended"] = True
    r = post_preflight(c, w, pre_body(w))
    assert (r.status_code, r.json()["code"]) == (403, "authority_refused")
    w.reader.rows["account"]["suspended"] = False
    w.reader.rows["device"]["revoked_at"] = "2026-01-01"
    r = post_preflight(c, w, pre_body(w))
    assert (r.status_code, r.json()["code"]) == (403, "authority_refused")
    w.reader.rows["device"]["revoked_at"] = None
    w.reader.rows["device"]["provisioning_state"] = "pending"
    r = post_preflight(c, w, pre_body(w))
    assert (r.status_code, r.json()["code"]) == (403, "authority_refused")
    w.reader.rows["device"]["provisioning_state"] = "active"
    w.reader.fail = "tunnel"
    r = post_preflight(c, w, pre_body(w))
    assert (r.status_code, r.json()["code"]) == (503, "authority_unavailable") and "provider-test-only-secret" not in r.text


def test_preflight_rejects_malformed_bodies(api, w):
    c, _ = api
    for over in ({"device_id": "bad id!"}, {"tunnel_uuid": "nope"}, {"route_origin": "http://device.example.com"},
                 {"device_public_key": "short"}, {"device_id": 5}):
        r = post_preflight(c, w, pre_body(w, **over))
        assert (r.status_code, r.json()["code"]) == (400, "bad_request"), over
    r = post_preflight(c, w, {**pre_body(w), "extra": 1})
    assert (r.status_code, r.json()["code"]) == (400, "invalid_json")
    r = post_preflight(c, w, None, raw=b"[1]")
    assert (r.status_code, r.json()["code"]) == (400, "invalid_json")


def test_preflight_unsigned_dashboard_style_and_forged_calls_are_401(api, w):
    c, _ = api
    body = json.dumps(pre_body(w)).encode()
    for headers in ({}, {"authorization": "Bearer abc"}, {"cf-access-jwt-assertion": "x"}):
        r = c.post(PREFLIGHT, content=body, headers={"content-type": "application/json", **headers})
        assert (r.status_code, r.json()["code"]) == (401, "unauthorized"), headers
    assert post_preflight(c, w, pre_body(w), headers={"x-mail-signature": "A" * 86}).status_code == 401


def test_a_preflight_signature_for_another_path_or_direction_is_401(api, w):
    c, _ = api
    assert post_preflight(c, w, pre_body(w), sign_path="/v1/mail-delivery/issue").status_code == 401
    data = json.dumps(pre_body(w)).encode()
    h = sign_service_call(w.service_private, path=PREFLIGHT, body=data, now_ms=int(time.time() * 1000),
                          direction="service-to-install")
    assert c.post(PREFLIGHT, content=data, headers=h).status_code == 401


def test_replayed_preflight_is_401(api, w):
    c, _ = api
    data = json.dumps(pre_body(w)).encode()
    h = sign_service_call(w.service_private, path=PREFLIGHT, body=data, now_ms=int(time.time() * 1000))
    assert c.post(PREFLIGHT, content=data, headers=h).status_code == 200
    r = c.post(PREFLIGHT, content=data, headers=h)
    assert (r.status_code, r.json()["code"]) == (401, "unauthorized")


def test_preflight_refusals_share_the_bucket_but_a_valid_call_is_never_charged(api, w):
    c, deps = api
    deps.bucket = RefusalBucket(limit=2)
    bad = lambda: c.post(PREFLIGHT, content=b"{}", headers={"content-type": "application/json"})
    assert [bad().status_code for _ in range(3)] == [401, 401, 429]
    assert post_preflight(c, w, pre_body(w)).status_code == 200


def test_preflight_nonce_store_failure_is_503_not_401(api, w):
    c, deps = api

    class Broken:
        def claim_service_nonce(self, *a):
            raise OSError("firestore down: secret")
    deps.store = Broken()
    r = post_preflight(c, w, pre_body(w))
    assert r.status_code == 503 and r.json()["code"] == "storage_or_configuration_failure" and "secret" not in r.text


def test_preflight_shape_refusals(api, w):
    c, _ = api
    assert post_preflight(c, w, pre_body(w), headers={"content-type": "text/plain"}).status_code == 400
    assert c.post(PREFLIGHT, content=b"{}", params={"x": "1"}, headers={"content-type": "application/json"}).status_code == 400
    big = b'{"x":"' + b"a" * (8 * 1024) + b'"}'
    assert post_preflight(c, w, None, raw=big).status_code == 413


def test_preflight_mints_nothing_and_spends_no_grant_budget(api, w, mem_firestore):
    c, _ = api
    assert post_preflight(c, w, pre_body(w)).status_code == 200
    assert budget_rows(mem_firestore) == {}


def test_main_registers_the_router_at_the_contract_prefix():
    paths = set(app.openapi()["paths"])
    assert {JWKS, GRANT, PREFLIGHT} <= paths
