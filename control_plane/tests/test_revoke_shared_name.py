"""Revoking one of a user's two devices on the same name keeps the name.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

MB22 made device revoke release the username claim, blank `users/{uid}.username`
and end the user's sessions. `_claim_username` lets one user enrol a second device
on a name they already hold, so revoking the OLD device must not take the name
from the device that is still enrolled.
"""
from __future__ import annotations

import hashlib

import pytest

from control_plane.app import jwt_es256, sessions

HOST = "sam.clayrune.io"


@pytest.fixture(autouse=True)
def fresh_keys(monkeypatch):
    monkeypatch.delenv("CLAYRUNE_JWT_SIGNING_KEYS", raising=False)
    monkeypatch.setenv("CLAYRUNE_ALLOW_EPHEMERAL_KEY", "1")
    jwt_es256.reset_keys_for_tests()
    yield
    jwt_es256.reset_keys_for_tests()


def _device(mem, device_id, user_id, token):
    mem.collection("devices").document(device_id).set({
        "device_id": device_id, "user_id": user_id, "hostname_claim": HOST,
        "enrollment_token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "revoked_at": None,
    })


def _world(mem, *, n_devices):
    mem.collection("users").document("u_a").set(
        {"user_id": "u_a", "username": "sam", "email": "a@example.com"})
    mem.collection("usernames").document("sam").set({"username": "sam", "user_id": "u_a"})
    _device(mem, "dev_old", "u_a", "tok_old")
    if n_devices == 2:
        _device(mem, "dev_new", "u_a", "tok_new")
    _, refresh, _ = sessions.create(user_id="u_a", username="sam")
    return refresh


def _revoke(client, device_id, token):
    r = client.post(f"/v1/devices/{device_id}/revoke", json={"enrollment_token": token})
    assert r.status_code == 200, r.text


def test_revoking_one_of_two_devices_keeps_the_claim_username_and_sessions(client, mem_firestore):
    refresh = _world(mem_firestore, n_devices=2)

    _revoke(client, "dev_old", "tok_old")

    dump = mem_firestore.dump()
    assert dump["usernames"]["sam"]["user_id"] == "u_a", "claim was released"
    assert dump["users"]["u_a"]["username"] == "sam", "user row was blanked"
    r = client.post("/v1/session/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200, r.text


def test_revoking_the_last_device_releases_and_forgets(client, mem_firestore):
    refresh = _world(mem_firestore, n_devices=1)

    _revoke(client, "dev_old", "tok_old")

    dump = mem_firestore.dump()
    assert "sam" not in dump.get("usernames", {})
    assert not dump["users"]["u_a"].get("username")
    assert client.post("/v1/session/refresh", json={"refresh_token": refresh}).status_code == 401


def test_another_users_device_on_the_name_does_not_count(client, mem_firestore):
    """A row for someone else's device carrying the same hostname is stale data,
    not a reason to keep this user's claim."""
    refresh = _world(mem_firestore, n_devices=1)
    _device(mem_firestore, "dev_other", "u_b", "tok_other")

    _revoke(client, "dev_old", "tok_old")

    assert "sam" not in mem_firestore.dump().get("usernames", {})
    assert client.post("/v1/session/refresh", json={"refresh_token": refresh}).status_code == 401


def test_unclaimed_username_check_counts_only(mem_firestore, capsys):
    from control_plane import check_unclaimed_usernames as chk

    users, names = mem_firestore.collection("users"), mem_firestore.collection("usernames")
    users.document("u1").set({"user_id": "u1", "username": "ok", "email": "x@example.com"})
    names.document("ok").set({"username": "ok", "user_id": "u1"})
    users.document("u2").set({"user_id": "u2", "username": "orphan", "email": "y@example.com"})
    users.document("u3").set({"user_id": "u3", "username": "taken"})
    names.document("taken").set({"username": "taken", "user_id": "u9"})
    users.document("u4").set({"user_id": "u4", "username": ""})

    assert chk.count_unclaimed(mem_firestore) == {"with_username": 3, "unclaimed": 2}
