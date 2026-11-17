"""MC-1179 MB26: the public /v1/connect loopback callback check.

MB26: `_is_safe_callback` judged the host with urlparse alone. A backslash makes
urlparse and a browser disagree about the host, so a URL that "is" 127.0.0.1 to
this check navigates to another site with the enrollment token attached.

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
