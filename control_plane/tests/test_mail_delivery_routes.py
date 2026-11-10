"""HTTP surface of /v1/mail-delivery (7d-2b, contract section 2): default off, service auth, closed codes."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from control_plane.app import routes_mail_delivery as rmd
from control_plane.app.main import app
from control_plane.app.mail_service_auth import RefusalBucket
from control_plane.tests.mail_delivery_support import INSTALL, World, payload, sign_service_call

PATHS = {"bind": "/v1/mail-delivery/bind", "issue": "/v1/mail-delivery/issue",
         "lease": "/v1/mail-delivery/lease", "revoke": "/v1/mail-delivery/revoke"}


@pytest.fixture
def w(mem_firestore):
    return World(mem_firestore)


@pytest.fixture
def api(w, mem_firestore):
    deps = rmd.DeliveryDeps(enabled=True, keyring=w.keyring, service_keys=w.service_keys,
                            authority=w.authority, store=w.store, bucket=RefusalBucket())
    app.dependency_overrides[rmd.get_deps] = lambda: deps
    yield TestClient(app), deps
    app.dependency_overrides.pop(rmd.get_deps, None)


def post(c, w, name, body, *, generation="1", install=INSTALL, raw=None, headers=None, params=None):
    data = raw if raw is not None else json.dumps(body).encode()
    h = sign_service_call(w.service_private, install_id=install, generation=generation,
                          path=PATHS[name], body=data, now_ms=int(time.time() * 1000))
    h.update(headers or {})
    return c.post(PATHS[name], content=data, headers=h, params=params)


def bound(api, w):
    c, _ = api
    r = post(c, w, "bind", {"grant": w.grant(), "r": w.r})
    assert r.status_code == 200, r.text
    return c


# ---- default off ---------------------------------------------------------------------------

def test_default_off_every_route_is_404_and_reads_nothing(monkeypatch):
    monkeypatch.delenv("CLAYRUNE_MAIL_DELIVERY_ENABLED", raising=False)
    app.dependency_overrides.pop(rmd.get_deps, None)
    c = TestClient(app)
    assert c.get("/v1/mail-delivery/jwks").status_code == 404
    for name, path in PATHS.items():
        r = c.post(path, content=b"{}", headers={"content-type": "application/json"})
        assert r.status_code == 404, name


@pytest.mark.parametrize("value", ["", "0", "false", "yes", "on"])
def test_only_1_or_true_enables(monkeypatch, value):
    monkeypatch.setenv("CLAYRUNE_MAIL_DELIVERY_ENABLED", value)
    assert rmd.get_deps().enabled is False


def test_enabled_without_reviewed_config_answers_503_not_configured(monkeypatch):
    monkeypatch.setenv("CLAYRUNE_MAIL_DELIVERY_ENABLED", "1")
    for v in ("CLAYRUNE_MAIL_DELIVERY_KEYS", "CLAYRUNE_MAIL_SERVICE_KEYS", "CLAYRUNE_MAIL_GRANT_JWKS"):
        monkeypatch.delenv(v, raising=False)
    app.dependency_overrides.pop(rmd.get_deps, None)
    c = TestClient(app)
    r = c.get("/v1/mail-delivery/jwks")
    assert r.status_code == 503 and r.json()["code"] == "not_configured"
    r = c.post(PATHS["issue"], content=b"{}", headers={"content-type": "application/json", "content-length": "2"})
    assert r.status_code == 503 and r.json()["code"] == "not_configured"


# ---- jwks ----------------------------------------------------------------------------------

def test_jwks_publishes_only_delivery_public_keys_with_cache_header(api, w):
    c, _ = api
    r = c.get("/v1/mail-delivery/jwks")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=600"
    keys = r.json()["keys"]
    assert [k["kid"] for k in keys] == ["mail-delivery-test"] and all("d" not in k for k in keys)


def test_session_jwks_is_a_separate_set(api, w):
    c, _ = api
    kids = {k["kid"] for k in c.get("/v1/jwks").json().get("keys", [])}
    assert "mail-delivery-test" not in kids


# ---- full flow -----------------------------------------------------------------------------

def test_bind_issue_lease_revoke_over_http(api, w):
    c = bound(api, w)
    r = post(c, w, "issue", w.issue_body(mode="mail"))
    assert r.status_code == 200
    claims = payload(r.json()["capability"])
    assert claims["aud"] == "clayrune-mail-delivery" and claims["install_id"] == INSTALL
    assert post(c, w, "lease", {"install_id": INSTALL, "generation": "1"}).status_code == 200
    assert post(c, w, "revoke", {"install_id": INSTALL, "generation": "1"}).json()["state"] == "revoked"
    r = post(c, w, "issue", w.issue_body())
    assert r.status_code == 403 and r.json()["code"] == "binding_required"


def test_closed_codes_map_to_statuses(api, w):
    c = bound(api, w)
    body = w.issue_body()
    assert post(c, w, "issue", body).status_code == 200
    r = post(c, w, "issue", {**body, "req_digest": "f" * 64})
    assert r.status_code == 409 and r.json()["code"] == "nonce_reused"
    assert post(c, w, "issue", w.issue_body(mode="x")).status_code == 400
    w.reader.rows["account"]["suspended"] = True
    r = post(c, w, "issue", w.issue_body())
    assert r.status_code == 403 and r.json()["code"] == "authority_refused"
    w.reader.rows["account"]["suspended"] = False
    w.reader.fail = "dns"
    r = post(c, w, "issue", w.issue_body())
    assert r.status_code == 503 and r.json()["code"] == "authority_unavailable"
    assert "provider-test-only-secret" not in r.text


def test_issue_budget_is_429(api, w):
    c = bound(api, w)
    w.authority.install_budget = 1
    assert post(c, w, "issue", w.issue_body()).status_code == 200
    r = post(c, w, "issue", w.issue_body())
    assert r.status_code == 429 and r.json()["code"] == "issue_budget"


def test_no_authority_reader_wired_issue_and_lease_are_503_but_bind_and_revoke_work(api, w):
    c, deps = api
    deps.authority.reader = None
    assert post(c, w, "bind", {"grant": w.grant(), "r": w.r}).status_code == 200
    r = post(c, w, "issue", w.issue_body())
    assert r.status_code == 503 and r.json()["code"] == "authority_unavailable"
    r = post(c, w, "lease", {"install_id": INSTALL, "generation": "1"})
    assert r.status_code == 503
    assert post(c, w, "revoke", {"install_id": INSTALL, "generation": "1"}).status_code == 200


# ---- service auth and request shape --------------------------------------------------------

def test_unsigned_dashboard_style_and_forged_calls_are_401(api, w):
    c, _ = api
    body = json.dumps(w.issue_body()).encode()
    for headers in ({}, {"authorization": "Bearer abc"}, {"cf-access-jwt-assertion": "x"}):
        r = c.post(PATHS["issue"], content=body, headers={"content-type": "application/json", **headers})
        assert r.status_code == 401 and r.json()["code"] == "unauthorized", headers
    r = post(c, w, "issue", w.issue_body(), headers={"x-mail-signature": "A" * 86})
    assert r.status_code == 401


def test_a_signature_for_another_path_is_401(api, w):
    c = bound(api, w)
    data = json.dumps(w.issue_body()).encode()
    h = sign_service_call(w.service_private, path=PATHS["lease"], body=data, now_ms=int(time.time() * 1000))
    assert c.post(PATHS["issue"], content=data, headers=h).status_code == 401


def test_replayed_service_request_is_401(api, w):
    c, _ = api
    data = json.dumps({"grant": w.grant(), "r": w.r}).encode()
    h = sign_service_call(w.service_private, path=PATHS["bind"], body=data, now_ms=int(time.time() * 1000))
    assert c.post(PATHS["bind"], content=data, headers=h).status_code == 200
    r = c.post(PATHS["bind"], content=data, headers=h)
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"


def test_shape_refusals_before_auth_work(api, w):
    c, _ = api
    h = {"content-type": "text/plain"}
    assert c.post(PATHS["issue"], content=b"{}", headers=h).status_code == 400
    assert post(c, w, "issue", {}, params={"x": "1"}).status_code == 400
    big = b'{"x":"' + b"a" * (16 * 1024) + b'"}'
    assert post(c, w, "issue", None, raw=big).status_code == 413
    r = post(c, w, "issue", None, raw=b"not json")
    assert r.status_code == 400 and r.json()["code"] == "invalid_json"
    r = post(c, w, "issue", None, raw=b"[1]")
    assert r.status_code == 400 and r.json()["code"] == "invalid_json"


def test_unauthenticated_refusals_share_one_bounded_bucket(api, w):
    c, deps = api
    deps.bucket = RefusalBucket(limit=2)
    bad = lambda: c.post(PATHS["issue"], content=b"{}", headers={"content-type": "application/json"})
    assert [bad().status_code for _ in range(3)] == [401, 401, 429]
    # A validly signed call is never charged to, nor locked out by, the bucket.
    c2 = bound(api, w)
    assert post(c2, w, "issue", w.issue_body()).status_code == 200


def test_nonce_store_failure_is_503_not_401(api, w):
    c, deps = api

    class Broken:
        def claim_service_nonce(self, *a):
            raise OSError("firestore down: secret")
    deps.store = Broken()
    r = post(c, w, "issue", w.issue_body())
    assert r.status_code == 503 and r.json()["code"] == "storage_or_configuration_failure"
    assert "secret" not in r.text


def test_authority_failure_is_503_without_leaking(api, w):
    c = bound(api, w)

    def boom(*a, **k):
        raise RuntimeError("private-key-material")
    w.authority.store.claim_issuance = boom
    r = post(c, w, "issue", w.issue_body())
    assert r.status_code == 503 and "private-key-material" not in r.text
