"""MB22: a released username must stop minting session tokens for its old owner.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

The edge admits a request when the JWT's `u` claim equals the subdomain. Device
revoke released `usernames/{name}` but left `users/{uid}.username` and the user's
sessions alone, and sign-in / refresh took `u` from that user row. After B claimed
the name, A's refresh still returned a JWT with `u` = the name.
"""
from __future__ import annotations

import base64

import pytest

from control_plane.app import jwt_es256, routes_account, sessions


@pytest.fixture(autouse=True)
def fresh_keys(monkeypatch):
    monkeypatch.delenv("CLAYRUNE_JWT_SIGNING_KEYS", raising=False)
    monkeypatch.setenv("CLAYRUNE_ALLOW_EPHEMERAL_KEY", "1")
    jwt_es256.reset_keys_for_tests()
    yield
    jwt_es256.reset_keys_for_tests()


def _enroll(client, *, email, username):
    r = client.post("/v1/enroll", headers={"X-Dev-User-Email": email}, json={
        "device_pub_b64": base64.b64encode(b"\x01" * 32).decode("ascii"),
        "csrf_nonce": "csrf-test-nonce", "username": username,
        "device_name": "Test PC", "os": "win32-test", "mc_version": "1.4.2",
    })
    assert r.status_code == 200, r.text
    return r.json()


def _user_id(mem, email):
    for (coll, doc_id), data in mem._store.items():
        if coll == "users" and data.get("email") == email:
            return doc_id
    raise AssertionError(f"no user row for {email}")


def _revoke(client, enrolled):
    r = client.post(f"/v1/devices/{enrolled['device_id']}/revoke",
                    json={"enrollment_token": enrolled["enrollment_token"]})
    assert r.status_code == 200, r.text


def test_old_owner_cannot_refresh_into_a_name_another_account_now_holds(client, mem_firestore):
    a = _enroll(client, email="a@example.com", username="sam")
    uid_a = _user_id(mem_firestore, "a@example.com")
    _, token_a, _ = sessions.create(user_id=uid_a, username="sam")

    _revoke(client, a)
    b = _enroll(client, email="b@example.com", username="sam")
    uid_b = _user_id(mem_firestore, "b@example.com")
    assert mem_firestore.dump()["usernames"]["sam"]["user_id"] == uid_b

    r = client.post("/v1/session/refresh", json={"refresh_token": token_a})
    assert r.status_code == 401, r.text
    assert r.json()["code"] == "session_invalid"
    assert b["hostname"] == "sam.clayrune.io"


def test_revoke_ends_the_owners_sessions_and_clears_their_username(client, mem_firestore):
    a = _enroll(client, email="a@example.com", username="sam")
    uid_a = _user_id(mem_firestore, "a@example.com")
    sid, token_a, _ = sessions.create(user_id=uid_a, username="sam")

    _revoke(client, a)

    assert not mem_firestore.dump()["users"][uid_a].get("username")
    assert sessions.resolve_refresh(token_a) is None, "revoke must kill the user's sessions"
    # ...and nobody has claimed the name yet, so this is the released-but-unclaimed case.
    assert mem_firestore.dump().get("usernames", {}).get("sam") is None


def test_sign_in_refuses_a_user_whose_name_is_held_by_someone_else(client, mem_firestore, monkeypatch):
    """session/start reads the same user row; a stale row must not pass the
    not_enrolled check just because it still has a username in it."""
    mem_firestore.collection("users").document("u_a").set(
        {"user_id": "u_a", "username": "sam", "email": "a@example.com"})
    mem_firestore.collection("usernames").document("sam").set(
        {"username": "sam", "user_id": "u_b"})
    monkeypatch.setattr(routes_account, "_verify_firebase_token",
                        lambda tok: {"user_id": "u_a", "email_verified": True})

    r = client.post("/v1/session/start", json={"id_token": "x"})
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "not_enrolled"


def test_sign_in_and_refresh_still_work_for_the_current_owner(client, mem_firestore, monkeypatch):
    _enroll(client, email="a@example.com", username="sam")
    uid_a = _user_id(mem_firestore, "a@example.com")
    monkeypatch.setattr(routes_account, "_verify_firebase_token",
                        lambda tok: {"user_id": uid_a, "email_verified": True})

    r = client.post("/v1/session/start", json={"id_token": "x"})
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "sam"

    _, token, _ = sessions.create(user_id=uid_a, username="sam")
    r = client.post("/v1/session/refresh", json={"refresh_token": token})
    assert r.status_code == 200, r.text
