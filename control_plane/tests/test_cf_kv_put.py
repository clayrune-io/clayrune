"""MB24: the suspension denylist write must actually be sent.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

`CloudflareClient.kv_put` used to pass `headers={"Content-Type": None}` to "let
httpx set the multipart boundary". httpx does not read None as "unset"; it raised
TypeError while building the request, before anything was sent. `denylist.add`
caught it (a bare `except Exception`) and returned False, so a suspension never
reached the edge and the admin console reported `edge_cutoff_immediate: false`.

These tests build the client the way `CloudflareClient.__init__` builds its own
(default headers include a JSON Content-Type) and put a MockTransport under it,
so they see the request exactly as it would leave the process.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

from control_plane.app import cloudflare, denylist


def _kv_client(seen: list[httpx.Request]) -> cloudflare.CloudflareClient:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"success": True, "errors": [], "result": None})

    http = httpx.AsyncClient(
        base_url=cloudflare.CF_API_BASE,
        transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer test", "Content-Type": "application/json"},
    )
    return cloudflare.CloudflareClient(
        token="test", account_id="acc-test", zone_id="zone-test", client=http)


def test_kv_put_sends_a_multipart_request():
    seen: list[httpx.Request] = []
    cf = _kv_client(seen)

    asyncio.run(cf.kv_put(namespace_id="ns1", key="u:uid_1", value="1"))

    assert len(seen) == 1, "kv_put raised before sending anything"
    req = seen[0]
    assert req.method == "PUT"
    assert req.url.path.endswith("/accounts/acc-test/storage/kv/namespaces/ns1/values/u:uid_1")
    ctype = req.headers["content-type"]
    assert ctype.startswith("multipart/form-data; boundary="), ctype
    assert req.headers["authorization"] == "Bearer test"
    body = req.content
    assert b'name="value"' in body and b"\r\n\r\n1\r\n" in body
    assert b'name="metadata"' in body


def test_denylist_add_reports_success_when_the_write_is_sent(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_KV_DENYLIST_ID", "ns1")
    seen: list[httpx.Request] = []
    cf = _kv_client(seen)

    assert asyncio.run(denylist.add("uid_1", cf=cf)) is True
    assert len(seen) == 1
    assert seen[0].url.path.endswith("/values/u:uid_1")


def test_kv_put_still_raises_on_a_cloudflare_refusal():
    """The fix must not turn a refused write into a silent success."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"success": False, "errors": [{"code": 10000}]})

    http = httpx.AsyncClient(
        base_url=cloudflare.CF_API_BASE, transport=httpx.MockTransport(handler),
        headers={"Authorization": "Bearer test", "Content-Type": "application/json"})
    cf = cloudflare.CloudflareClient(
        token="test", account_id="acc-test", zone_id="zone-test", client=http)
    with pytest.raises(cloudflare.CloudflareAPIError):
        asyncio.run(cf.kv_put(namespace_id="ns1", key="u:x"))
