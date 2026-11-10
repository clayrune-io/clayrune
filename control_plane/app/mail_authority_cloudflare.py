"""Cloudflare half of the production AuthorityReader: tunnel, ingress, DNS (MC-1166 7d-2r).

READ ONLY. The adapter's one dependency is `get(path) -> result`, an async GET
against the Cloudflare v4 API that returns the unwrapped `result`. Nothing here
can issue another HTTP method. The account and zone ids are pinned by config (no
discovery), and every identifier that came from a device row is shape-checked
before it is spliced into a URL path.

Each snapshot is projected to just the fields `check_authority` compares:
  * tunnel : id, created_at, deleted_at, config_src
  * ingress: tunnel_id, config.ingress (a lone empty `originRequest: {}` is dropped,
             which is semantically nothing; any non-empty origin option stays and
             makes the mapping mismatch)
  * dns    : id, type, name, proxied, content

Snapshot versions: ingress uses the configuration `version` integer and DNS uses
`modified_on`, both assigned by Cloudflare. A tunnel object has no version field,
so its version is a SHA-256 over (id, created_at, deleted_at, config_src).

UNVERIFIED against live Cloudflare (no network in this unit): that a live tunnel
response carries an explicit `deleted_at: null`, and whether tunnel config source
arrives as `config_src` or as `remote_config`. Both fail closed if wrong
(`cloudflare_mapping_mismatch`), they never pass a bad mapping.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Awaitable, Callable

from .mail_authority_check import GrantRefused, Snapshot
from .mail_authority_firestore import AuthorityReadError

log = logging.getLogger(__name__)

_TUNNEL_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DNS_ID = re.compile(r"[A-Za-z0-9]{1,64}")
_PATH_ID = re.compile(r"[A-Za-z0-9]{1,64}")  # account and zone ids

CfGet = Callable[[str], Awaitable[Any]]


class CloudflareAuthorityReads:
    def __init__(self, get: CfGet, *, account_id: str, zone_id: str):
        if not _PATH_ID.fullmatch(account_id or "") or not _PATH_ID.fullmatch(zone_id or ""):
            raise ValueError("bad_cloudflare_ids")
        self._get = get
        self._acc = account_id
        self._zone = zone_id

    async def _result(self, kind: str, path: str) -> dict:
        try:
            result = await self._get(path)
        except Exception as e:
            if getattr(e, "status", None) == 404:
                # A 404 inside the pinned account/zone is a missing object, which is a refusal.
                raise GrantRefused("cloudflare_mapping_mismatch") from None
            log.warning("[mail-authority] cloudflare %s read failed: %s status=%s", kind,
                        type(e).__name__, getattr(e, "status", None))
            raise AuthorityReadError("cloudflare_read_failed") from None
        if not isinstance(result, dict):
            raise AuthorityReadError("cloudflare_bad_result")
        return result

    @staticmethod
    def _tunnel_id(tunnel_id: str) -> str:
        if not isinstance(tunnel_id, str) or not _TUNNEL_ID.fullmatch(tunnel_id):
            raise AuthorityReadError("cloudflare_bad_id")
        return tunnel_id

    async def tunnel(self, tunnel_id: str) -> Snapshot:
        tid = self._tunnel_id(tunnel_id)
        r = await self._result("tunnel", f"/accounts/{self._acc}/cfd_tunnel/{tid}")
        src = r.get("config_src")
        if src is None and isinstance(r.get("remote_config"), bool):
            src = "cloudflare" if r["remote_config"] else "local"
        data: dict = {k: r[k] for k in ("id", "created_at", "deleted_at") if k in r}
        if src is not None:
            data["config_src"] = src
        version = hashlib.sha256(json.dumps(
            [data.get("id"), data.get("created_at"), data.get("deleted_at", "<absent>"), src],
            separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        return Snapshot(data, "tunnel-" + version)

    async def ingress(self, tunnel_id: str) -> Snapshot:
        tid = self._tunnel_id(tunnel_id)
        r = await self._result("ingress", f"/accounts/{self._acc}/cfd_tunnel/{tid}/configurations")
        version = r.get("version")
        if type(version) is not int or version < 0:
            raise AuthorityReadError("cloudflare_no_version")
        config = r.get("config")
        rules = config.get("ingress") if isinstance(config, dict) else None
        if not isinstance(rules, list) or not all(isinstance(x, dict) for x in rules):
            raise AuthorityReadError("cloudflare_bad_ingress")
        rules = [{k: v for k, v in x.items() if not (k == "originRequest" and v == {})} for x in rules]
        return Snapshot({"tunnel_id": r.get("tunnel_id"), "config": {"ingress": rules}}, f"ingress-{version}")

    async def dns(self, record_id: str) -> Snapshot:
        if not isinstance(record_id, str) or not _DNS_ID.fullmatch(record_id):
            raise AuthorityReadError("cloudflare_bad_id")
        r = await self._result("dns", f"/zones/{self._zone}/dns_records/{record_id}")
        modified = r.get("modified_on")
        if not isinstance(modified, str) or not modified:
            raise AuthorityReadError("cloudflare_no_version")
        data = {k: r[k] for k in ("id", "type", "name", "proxied", "content") if k in r}
        return Snapshot(data, "dns-" + modified)
