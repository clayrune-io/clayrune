"""MB23: force cleanup must delete only the named user's tunnels.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Cleanup used `name.startswith(f"mc-{username}-")`. With hyphens legal in a
username, revoking `sam` also deleted the tunnel of `sam-dev`.
"""
from __future__ import annotations

import asyncio

import httpx

from control_plane.app import cloudflare, routes_account
from control_plane.app.tunnel_names import is_tunnel_of, new_tunnel_name


def test_names_round_trip_for_any_username():
    for u in ("sam", "sam-dev", "a-b-c", "x1", "ron"):
        for _ in range(50):
            assert is_tunnel_of(new_tunnel_name(u), u)


def test_a_hyphenated_username_is_not_a_prefix_match():
    other = new_tunnel_name("sam-dev")
    assert not is_tunnel_of(other, "sam")
    # and the reverse: sam's tunnel is not sam-dev's
    assert not is_tunnel_of(new_tunnel_name("sam"), "sam-dev")
    # a suffix that happens to look like a username part still must not match
    assert not is_tunnel_of("mc-sam-dev-abcdef", "sam")
    assert not is_tunnel_of("mc-sam-abcdef-x", "sam")
    assert not is_tunnel_of("mc-sam-abcde", "sam")
    assert not is_tunnel_of("", "sam")


def test_force_cleanup_leaves_other_accounts_tunnels(mem_firestore):
    """Measured in the review: A enrolls `sam-dev`, B enrolls `sam`, B revokes —
    both tunnels were deleted."""
    tunnels = [
        {"id": "t_sam", "name": new_tunnel_name("sam")},
        {"id": "t_samdev", "name": new_tunnel_name("sam-dev")},
        {"id": "t_old", "name": new_tunnel_name("sam"), "deleted_at": "2026-01-01"},
    ]
    deleted: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/client/v4")
        ok = lambda r: httpx.Response(200, json={"success": True, "errors": [], "result": r})
        if req.method == "GET" and path == "/accounts/acc-test/cfd_tunnel":
            return ok(tunnels)
        if req.method == "DELETE" and "/cfd_tunnel/" in path:
            tid = path.split("/cfd_tunnel/", 1)[1].split("/", 1)[0]
            if tid not in deleted:
                deleted.append(tid)
            return ok(None)
        return ok([])

    http = httpx.AsyncClient(
        base_url=cloudflare.CF_API_BASE, transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer test", "Content-Type": "application/json"})
    cf = cloudflare.CloudflareClient(
        token="test", account_id="acc-test", zone_id="zone-test", client=http)
    routes_account.set_cf_client_for_tests(cf)
    try:
        asyncio.run(routes_account._force_cleanup_for_hostname(
            hostname="sam.clayrune.io", username="sam"))
    finally:
        routes_account.reset_cf_client()

    assert deleted == ["t_sam"], deleted
