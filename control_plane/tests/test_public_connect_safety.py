"""MC-1179 MB25 + MB26: the public /v1/connect page and its loopback callback.

MB26: `_is_safe_callback` judged the host with urlparse alone. A backslash makes
urlparse and a browser disagree about the host, so a URL that "is" 127.0.0.1 to
this check navigates to another site with the enrollment token attached.

MB25: the page put `pub`, `nonce` and `callback` into an inline <script> through
json.dumps (which leaves `</script>` intact) and sent no Content-Security-Policy.
"""
from __future__ import annotations

import base64
import re

import pytest

from control_plane.app import routes_public

GOOD_PUB = base64.b64encode(b"\x01" * 32).decode()
GOOD_CB = "http://127.0.0.1:5199/api/mc-callback"


# ─── MB26: the callback check ────────────────────────────────────────────────

@pytest.mark.parametrize("cb", [
    GOOD_CB,
    "http://localhost:5199/api/mc-callback",
    "http://127.0.0.1/api/mc-callback",
])
def test_real_loopback_callbacks_still_pass(cb):
    assert routes_public._is_safe_callback(cb) is True


@pytest.mark.parametrize("cb", [
    # the reported case: urlparse says 127.0.0.1, a browser says evil.example
    "http://evil.example\\@127.0.0.1/api/mc-callback",
    "http://evil.example\\@127.0.0.1:5199/api/mc-callback",
    "http://127.0.0.1\\@evil.example/api/mc-callback",
    "http://user@127.0.0.1:5199/api/mc-callback",
    "http://127.0.0.1:5199@evil.example/api/mc-callback",
    "http://127.0.0.1 /api/mc-callback",
    "http://127.0.0.1:5199/api/mc-callback\t",
    "http://127.0.0.1:5199/api/mc-callback\n",
    "http://127.0.0.1:99999/api/mc-callback",
    "http://127.0.0.1:abc/api/mc-callback",
    "http://127.0.0.1:5199/api/mc-callback?x=1",
    "http://127.0.0.1:5199/api/mc-callback#frag",
    "https://127.0.0.1:5199/api/mc-callback",
    "http://evil.example/api/mc-callback",
])
def test_hostile_callbacks_are_refused(cb):
    assert routes_public._is_safe_callback(cb) is False


def test_signin_start_refuses_the_backslash_callback(client, mem_firestore):
    r = client.post("/v1/signin/start", json={
        "nonce": "n" * 16, "pub": GOOD_PUB,
        "callback": "http://evil.example\\@127.0.0.1/api/mc-callback",
    })
    assert r.status_code == 400, r.text


def test_signin_complete_redirects_to_a_rebuilt_callback_not_the_stored_string(
        client, mem_firestore, monkeypatch):
    """A stored intent (written before this fix) holding the hostile string must
    not produce a redirect_url pointing off-box."""
    import hashlib
    from control_plane.app import firestore as fs, routes_account

    nonce = "n" * 16
    stored = "http://evil.example\\@127.0.0.1/api/mc-callback"
    h = hashlib.sha256(nonce.encode()).hexdigest()
    mem_firestore.collection(fs.COL_ENROLL_INTENTS).document(h).set({
        "csrf_nonce_hash": h,
        "device_pub_hash": hashlib.sha256(GOOD_PUB.encode()).hexdigest(),
        "callback": stored,
    })
    monkeypatch.setattr(routes_account, "_verify_firebase_token",
                        lambda t: {"email": "a@b.c", "email_verified": True, "uid": "u"})

    async def fake_enroll(**kw):
        return {"enrollment_token": "TOK", "device_id": "d", "hostname": "h", "username": "ron"}
    monkeypatch.setattr(routes_account, "_do_enroll_after_auth", fake_enroll)

    r = client.post("/v1/signin/complete", json={
        "id_token": "x", "nonce": nonce, "pub": GOOD_PUB, "username": "ron"})
    assert r.status_code == 400, r.text  # stored callback no longer passes
    assert "enrollment_token" not in r.text


def test_signin_complete_redirect_is_rebuilt_from_parsed_parts(
        client, mem_firestore, monkeypatch):
    import hashlib
    from control_plane.app import firestore as fs, routes_account

    nonce = "m" * 16
    h = hashlib.sha256(nonce.encode()).hexdigest()
    mem_firestore.collection(fs.COL_ENROLL_INTENTS).document(h).set({
        "csrf_nonce_hash": h,
        "device_pub_hash": hashlib.sha256(GOOD_PUB.encode()).hexdigest(),
        "callback": GOOD_CB,
    })
    monkeypatch.setattr(routes_account, "_verify_firebase_token",
                        lambda t: {"email": "a@b.c", "email_verified": True, "uid": "u"})

    async def fake_enroll(**kw):
        return {"enrollment_token": "TOK", "device_id": "d", "hostname": "h", "username": "ron"}
    monkeypatch.setattr(routes_account, "_do_enroll_after_auth", fake_enroll)

    r = client.post("/v1/signin/complete", json={
        "id_token": "x", "nonce": nonce, "pub": GOOD_PUB, "username": "ron"})
    assert r.status_code == 200, r.text
    assert r.json()["redirect_url"].startswith("http://127.0.0.1:5199/api/mc-callback?")


# ─── MB25: the connect page ──────────────────────────────────────────────────

def test_connect_page_sends_a_nonce_csp_that_matches_its_inline_script(client):
    r = client.get("/v1/connect", params={"pub": GOOD_PUB, "nonce": "abc", "callback": GOOD_CB})
    assert r.status_code == 200
    csp = r.headers.get("content-security-policy")
    assert csp, "connect page must carry a Content-Security-Policy"
    m = re.search(r"script-src 'nonce-([^']+)'", csp)
    assert m, csp
    assert f'<script type="module" nonce="{m.group(1)}">' in r.text
    assert "'unsafe-inline'" not in csp.split("style-src")[0]  # script-src stays strict
    assert "default-src 'none'" in csp and "base-uri 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "connect-src 'self'" in csp  # the page fetches /v1/signin/*


def test_connect_page_nonce_differs_per_response(client):
    p = {"pub": GOOD_PUB, "nonce": "abc", "callback": GOOD_CB}
    a = client.get("/v1/connect", params=p).headers["content-security-policy"]
    b = client.get("/v1/connect", params=p).headers["content-security-policy"]
    assert a != b


def test_connect_page_refuses_a_pub_that_is_not_32_byte_base64(client):
    r = client.get("/v1/connect", params={
        "pub": "</script><script>alert(1)</script>", "nonce": "abc", "callback": GOOD_CB})
    assert r.status_code == 400
    assert "alert(1)" not in r.text


def test_connect_page_json_values_cannot_close_the_script(client):
    r = client.get("/v1/connect", params={
        "pub": GOOD_PUB, "nonce": "</script><script>alert(1)</script>", "callback": GOOD_CB})
    assert r.status_code == 200
    assert "</script><script>alert(1)" not in r.text
    assert "alert(1)" in r.text  # still delivered, escaped as data
    assert "\\u003c/script\\u003e" in r.text
