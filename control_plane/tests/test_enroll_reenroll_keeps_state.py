"""MC-1179 MB28 (the two parts that need no product decision).

1. A re-enroll of a name the account already holds that failed mid-provisioning
   released the name: `_claim_username` returns True for the existing owner, and
   the rollback released whatever it was handed. A transient Cloudflare error on
   a second machine freed the name the first machine was live on.

2. Every enroll rewrote the user row with `created_at`, `tier` and
   `bandwidth_used_period_bytes: 0` under merge=True, so a re-enroll reset the
   account's creation date and its usage counter.
"""
from __future__ import annotations

import base64
import datetime as dt

from control_plane.app import firestore as fs

EMAIL = "dev@clayrune.io"


def _pub(byte: int) -> str:
    return base64.b64encode(bytes([byte]) * 32).decode("ascii")


def _enroll(client, username, pub, email=EMAIL):
    r = client.post("/v1/enroll", headers={"X-Dev-User-Email": email}, json={
        "device_pub_b64": pub, "csrf_nonce": "csrf-test-nonce", "username": username,
        "device_name": "Test PC", "os": "win32-test", "mc_version": "1.4.2",
    })
    return r.status_code, r.json()


def test_a_failed_reenroll_does_not_release_a_name_the_account_already_holds(
        client, mem_firestore, cf_recorder):
    status, body = _enroll(client, "dana", _pub(1))
    assert status == 200, body
    owner = mem_firestore.dump()["usernames"]["dana"]["user_id"]

    cf_recorder.fail_after_n("POST", "/zones/zone-test/dns_records", status=500)
    status, body = _enroll(client, "dana", _pub(2))
    assert status == 503, body

    claim = mem_firestore.dump().get("usernames", {}).get("dana")
    assert claim is not None, "the name the first machine is live on was released"
    assert claim["user_id"] == owner


def test_a_failed_first_enroll_still_releases_the_name_it_claimed(
        client, mem_firestore, cf_recorder):
    cf_recorder.fail_after_n("POST", "/zones/zone-test/dns_records", status=500)
    status, body = _enroll(client, "erin", _pub(3))
    assert status == 503, body
    assert mem_firestore.dump().get("usernames", {}) == {}


def test_a_reenroll_keeps_the_user_rows_creation_date_and_usage(client, mem_firestore):
    status, body = _enroll(client, "fay", _pub(4))
    assert status == 200, body
    users = mem_firestore.dump()["users"]
    (uid,) = users.keys()
    born = dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc)
    mem_firestore.collection(fs.COL_USERS).document(uid).set({
        "created_at": born, "bandwidth_used_period_bytes": 12345, "tier": "paid",
        "device_cap": 5, "bandwidth_quota_period_bytes": 99,
    }, merge=True)

    status, body = _enroll(client, "fay", _pub(5))
    assert status == 200, body

    row = mem_firestore.dump()["users"][uid]
    assert row["created_at"] == born
    assert row["bandwidth_used_period_bytes"] == 12345
    assert row["tier"] == "paid"
    assert row["device_cap"] == 5
    assert row["bandwidth_quota_period_bytes"] == 99
    assert row["username"] == "fay"


def test_a_first_enroll_still_writes_the_row_defaults(client, mem_firestore):
    status, body = _enroll(client, "gus", _pub(6))
    assert status == 200, body
    (row,) = mem_firestore.dump()["users"].values()
    assert row["tier"] == "free"
    assert row["device_cap"] == 2
    assert row["bandwidth_used_period_bytes"] == 0
    assert row["bandwidth_quota_period_bytes"] == 5 * 1024 ** 3
    assert isinstance(row["created_at"], dt.datetime)
    assert row["username"] == "gus"
