"""MC-1210: revoking one device must not destroy a sibling device on the same name.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

`revoke_device` ran `_force_cleanup_for_hostname` with no excludes. That helper
deletes every tunnel named for the user, every DNS record for the hostname and
every device row claiming it, so a SECOND device row of the same user on the same
hostname lost its tunnel, DNS record and row. The MB22 fix kept the claim,
username and sessions in that case, but not the sibling's infrastructure.

The Cloudflare fake here is stateful (deletes remove things) so the assertions
read what is left, not what was asked for.
"""
from __future__ import annotations

import hashlib

import httpx
import pytest

from control_plane.app import cloudflare, jwt_es256, routes_account
from control_plane.app.tunnel_names import new_tunnel_name

HOST = "sam.clayrune.io"


@pytest.fixture(autouse=True)
def fresh_keys(monkeypatch):
    monkeypatch.delenv("CLAYRUNE_JWT_SIGNING_KEYS", raising=False)
    monkeypatch.setenv("CLAYRUNE_ALLOW_EPHEMERAL_KEY", "1")
    jwt_es256.reset_keys_for_tests()
    yield
    jwt_es256.reset_keys_for_tests()


class FakeCF:
    """Just enough Cloudflare: tunnels, DNS records, Access apps, with real deletes."""

    def __init__(self):
        self.tunnels: dict[str, str] = {}      # id -> name
        self.dns: dict[str, str] = {}          # id -> hostname
        self.apps: dict[str, str] = {}         # id -> domain
        self.deleted: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/client/v4")
        ok = lambda r: httpx.Response(200, json={"success": True, "errors": [], "result": r})
        if req.method == "GET" and path == "/accounts/acc-test/cfd_tunnel":
            return ok([{"id": i, "name": n} for i, n in self.tunnels.items()])
        if req.method == "GET" and path == "/zones/zone-test/dns_records":
            name = req.url.params.get("name")
            return ok([{"id": i, "name": h} for i, h in self.dns.items() if h == name])
        if req.method == "GET" and path == "/accounts/acc-test/access/apps":
            return ok([{"id": i, "domain": d} for i, d in self.apps.items()])
        for store, marker in ((self.tunnels, "/cfd_tunnel/"), (self.dns, "/dns_records/"),
                              (self.apps, "/access/apps/")):
            if req.method == "DELETE" and marker in path:
                rid = path.split(marker, 1)[1].split("/", 1)[0]
                store.pop(rid, None)
                self.deleted.append(rid)
                return ok(None)
        return ok([])


@pytest.fixture
def fake_cf():
    fake = FakeCF()
    http = httpx.AsyncClient(
        base_url=cloudflare.CF_API_BASE, transport=httpx.MockTransport(fake.handler),
        headers={"Authorization": "Bearer test", "Content-Type": "application/json"})
    routes_account.set_cf_client_for_tests(cloudflare.CloudflareClient(
        token="test", account_id="acc-test", zone_id="zone-test", client=http))
    yield fake
    routes_account.reset_cf_client()


def _device(mem, device_id, user_id, token, *, tunnel, dns, app=None):
    row = {
        "device_id": device_id, "user_id": user_id, "hostname_claim": HOST,
        "enrollment_token_hash": hashlib.sha256(token.encode()).hexdigest(),
        "revoked_at": None, "cf_tunnel_uuid": tunnel, "cf_dns_record_id": dns,
    }
    if app:
        row["cf_access_app_id"] = app
    mem.collection("devices").document(device_id).set(row)


def _two_device_world(mem, fake, *, shared_dns=False):
    """Two rows, same user, same hostname, each with its own tunnel. By default each
    has its own DNS record id; `shared_dns` makes both rows point at one record."""
    mem.collection("users").document("u_a").set(
        {"user_id": "u_a", "username": "sam", "email": "a@example.com"})
    mem.collection("usernames").document("sam").set({"username": "sam", "user_id": "u_a"})
    fake.tunnels.update({"t_old": new_tunnel_name("sam"), "t_new": new_tunnel_name("sam")})
    if not shared_dns:
        fake.dns["d_old"] = HOST
    fake.dns["d_new"] = HOST
    _device(mem, "dev_old", "u_a", "tok_old", tunnel="t_old",
            dns="d_new" if shared_dns else "d_old")
    _device(mem, "dev_new", "u_a", "tok_new", tunnel="t_new", dns="d_new")


def _revoke(client, device_id, token):
    r = client.post(f"/v1/devices/{device_id}/revoke", json={"enrollment_token": token})
    assert r.status_code == 200, r.text


def test_revoking_one_device_leaves_the_siblings_tunnel_dns_and_row(client, mem_firestore, fake_cf):
    _two_device_world(mem_firestore, fake_cf)

    _revoke(client, "dev_old", "tok_old")

    assert "t_old" not in fake_cf.tunnels, "revoked device's own tunnel must go"
    assert "d_old" not in fake_cf.dns, "revoked device's own DNS record must go"
    assert "dev_old" not in mem_firestore.dump().get("devices", {})
    assert "t_new" in fake_cf.tunnels, "sibling's tunnel was deleted"
    assert "d_new" in fake_cf.dns, "sibling's DNS record was deleted"
    assert "dev_new" in mem_firestore.dump().get("devices", {}), "sibling's device row was deleted"


def test_a_dns_record_both_rows_point_at_is_kept(client, mem_firestore, fake_cf):
    """If both rows carry the same cf_dns_record_id the by-id delete would remove the
    survivor's record before force cleanup even runs."""
    _two_device_world(mem_firestore, fake_cf, shared_dns=True)

    _revoke(client, "dev_old", "tok_old")

    assert "d_new" in fake_cf.dns, "DNS record still held by the sibling was deleted"
    assert "t_new" in fake_cf.tunnels
    assert "dev_new" in mem_firestore.dump().get("devices", {})


def test_another_users_row_on_the_name_is_still_swept(client, mem_firestore, fake_cf):
    """Only the SAME user's other device is a survivor; stale rows of someone else
    carrying the hostname are what force cleanup exists to remove."""
    mem_firestore.collection("users").document("u_a").set(
        {"user_id": "u_a", "username": "sam", "email": "a@example.com"})
    mem_firestore.collection("usernames").document("sam").set(
        {"username": "sam", "user_id": "u_a"})
    fake_cf.tunnels.update({"t_old": new_tunnel_name("sam"), "t_other": new_tunnel_name("sam")})
    fake_cf.dns.update({"d_old": HOST})
    _device(mem_firestore, "dev_old", "u_a", "tok_old", tunnel="t_old", dns="d_old")
    _device(mem_firestore, "dev_other", "u_b", "tok_other", tunnel="t_other", dns="d_other")

    _revoke(client, "dev_old", "tok_old")

    assert fake_cf.tunnels == {}
    assert fake_cf.dns == {}
    assert "dev_other" not in mem_firestore.dump().get("devices", {})


def test_revoking_the_only_device_still_sweeps_orphans(client, mem_firestore, fake_cf):
    """No survivor: unchanged behaviour. An orphan tunnel for the same username goes."""
    mem_firestore.collection("users").document("u_a").set(
        {"user_id": "u_a", "username": "sam", "email": "a@example.com"})
    mem_firestore.collection("usernames").document("sam").set(
        {"username": "sam", "user_id": "u_a"})
    fake_cf.tunnels.update({"t_old": new_tunnel_name("sam"), "t_orphan": new_tunnel_name("sam")})
    fake_cf.dns.update({"d_old": HOST, "d_orphan": HOST})
    _device(mem_firestore, "dev_old", "u_a", "tok_old", tunnel="t_old", dns="d_old")

    _revoke(client, "dev_old", "tok_old")

    assert fake_cf.tunnels == {}
    assert fake_cf.dns == {}


class _FailingDeviceQuery:
    """`devices.where(...)` whose stream() yields one row, then raises."""

    def __init__(self, first_row):
        self._first = first_row

    def stream(self):
        yield self._first
        raise RuntimeError("firestore unavailable")


class _FlakyDevices:
    """Delegates to the real in-memory devices collection except for the hostname
    query revoke uses to find the user's other devices."""

    def __init__(self, real):
        self._real = real

    def where(self, field, op, value):
        first = next(iter(self._real.where(field, op, value).stream()))
        return _FailingDeviceQuery(first)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_a_failed_read_of_other_devices_fails_closed(client, mem_firestore, fake_cf, monkeypatch):
    """The read that finds the sibling raises part-way. Revoke cannot tell whether a
    sibling exists, so it must not sweep the hostname and must keep the username;
    it still deletes its own tunnel, DNS record and row."""
    _two_device_world(mem_firestore, fake_cf)
    real_collection = mem_firestore.collection
    monkeypatch.setattr(
        mem_firestore, "collection",
        lambda name: _FlakyDevices(real_collection(name)) if name == "devices"
        else real_collection(name))

    _revoke(client, "dev_old", "tok_old")

    assert "t_old" not in fake_cf.tunnels, "revoked device's own tunnel must still go"
    assert "d_old" not in fake_cf.dns, "revoked device's own DNS record must still go"
    assert "dev_old" not in mem_firestore.dump().get("devices", {})
    assert "t_new" in fake_cf.tunnels, "sibling's tunnel was swept on a failed read"
    assert "d_new" in fake_cf.dns, "sibling's DNS record was swept on a failed read"
    assert "dev_new" in mem_firestore.dump().get("devices", {}), "sibling's row was swept"
    assert mem_firestore.dump()["usernames"]["sam"]["user_id"] == "u_a", "username was released"
    assert mem_firestore.dump()["users"]["u_a"]["username"] == "sam"
