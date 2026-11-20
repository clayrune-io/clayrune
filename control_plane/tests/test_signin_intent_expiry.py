"""MC-1179 MB30: a sign-in intent must stop working when it expires.

`/v1/signin/start` stores `expires_at` = now + 15 minutes. `/v1/signin/complete`
checked that the intent existed, the key hash and the callback, and never read
`expires_at`, though its own comment says "still valid" and its error text says
"Sign-in expired". An intent from 2020 completed with 200.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib

import pytest

from control_plane.app import firestore as fs, routes_account

GOOD_PUB = base64.b64encode(b"\x01" * 32).decode()
GOOD_CB = "http://127.0.0.1:5199/api/mc-callback"
_UTC = dt.timezone.utc


def _seed(mem_firestore, nonce, **fields):
    h = hashlib.sha256(nonce.encode()).hexdigest()
    row = {
        "csrf_nonce_hash": h,
        "device_pub_hash": hashlib.sha256(GOOD_PUB.encode()).hexdigest(),
        "callback": GOOD_CB,
    }
    row.update(fields)
    mem_firestore.collection(fs.COL_ENROLL_INTENTS).document(h).set(row)
    return h


@pytest.fixture
def enroll_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(routes_account, "_verify_firebase_token",
                        lambda t: {"email": "a@b.c", "email_verified": True, "uid": "u"})

    async def fake_enroll(**kw):
        calls.append(kw)
        return {"enrollment_token": "TOK", "device_id": "d", "hostname": "h", "username": "ron"}
    monkeypatch.setattr(routes_account, "_do_enroll_after_auth", fake_enroll)
    return calls


def _complete(client, nonce):
    return client.post("/v1/signin/complete", json={
        "id_token": "x", "nonce": nonce, "pub": GOOD_PUB, "username": "ron"})


def test_a_live_intent_completes(client, mem_firestore, enroll_calls):
    nonce = "a" * 16
    _seed(mem_firestore, nonce,
          expires_at=dt.datetime.now(_UTC) + dt.timedelta(minutes=5))
    r = _complete(client, nonce)
    assert r.status_code == 200, r.text
    assert len(enroll_calls) == 1


@pytest.mark.parametrize("expires_at", [
    dt.datetime(2020, 1, 1, tzinfo=_UTC),                 # the measured case
    dt.datetime(2020, 1, 1),                              # naive, read as UTC
    "2099-01-01T00:00:00Z",                               # not a timestamp
    None,
])
def test_an_expired_or_unreadable_intent_is_refused_before_enrolling(
        client, mem_firestore, enroll_calls, expires_at):
    nonce = "b" * 16
    _seed(mem_firestore, nonce, expires_at=expires_at)
    r = _complete(client, nonce)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "enrollment_intent_invalid"
    assert enroll_calls == []
    assert "enrollment_token" not in r.text


def test_an_intent_with_no_expiry_field_is_refused(client, mem_firestore, enroll_calls):
    nonce = "c" * 16
    _seed(mem_firestore, nonce)
    r = _complete(client, nonce)
    assert r.status_code == 400, r.text
    assert enroll_calls == []


def test_start_then_complete_round_trip_still_works(client, enroll_calls):
    nonce = "d" * 16
    r = client.post("/v1/signin/start", json={"nonce": nonce, "pub": GOOD_PUB, "callback": GOOD_CB})
    assert r.status_code == 200, r.text
    r = _complete(client, nonce)
    assert r.status_code == 200, r.text
