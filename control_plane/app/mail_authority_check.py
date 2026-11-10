"""The authority cross-check shared by the enrollment grant and the delivery capability.

Extracted verbatim from `MailGrantIssuer.issue` (7a) so 7d-2b does not fork the
cross-check: a mapping rule fixed here is fixed for both issuers. Every external
read is an injected, read-only adapter; this module imports no Firestore or
Cloudflare client. `control_plane/tests/test_mail_grants.py` is the regression
gate for the extraction and must pass unchanged.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol


class GrantRefused(ValueError):
    """Safe constant reason; never contains a row, credential or provider body."""


@dataclass(frozen=True)
class Snapshot:
    data: dict
    version: str  # Adapter must supply the authoritative update/version, not a client hint.


class AuthorityReader(Protocol):
    async def device(self, device_id: str) -> Snapshot: ...
    async def account(self, user_id: str) -> Snapshot: ...
    async def tunnel(self, tunnel_id: str) -> Snapshot: ...
    async def ingress(self, tunnel_id: str) -> Snapshot: ...
    async def dns(self, record_id: str) -> Snapshot: ...


@dataclass(frozen=True)
class AuthorityResult:
    user_id: str
    record_version: str  # SHA-256 of the five authority version strings, nothing else.


def snapshot_data(s: Snapshot) -> dict:
    if (not isinstance(s, Snapshot) or not isinstance(s.data, dict)
            or not isinstance(s.version, str) or not 1 <= len(s.version) <= 256):
        raise GrantRefused("authority_version_required")
    return s.data


async def check_authority(reader: AuthorityReader, *, device_id: str, device_public_key: bytes,
                          tunnel_uuid: str, route_origin: str,
                          expected_service: str) -> AuthorityResult:
    """Fresh device/account/tunnel/ingress/DNS cross-check. Raises GrantRefused only.

    `device_public_key` is the raw 32-byte Ed25519 key the caller expects the
    device row to hold. Any non-GrantRefused failure of a reader surfaces as
    `authority_unavailable`; nothing is swallowed into success.
    """
    try:
        device = await reader.device(device_id)
        d = snapshot_data(device)
        user_id = d.get("user_id")
        if not isinstance(user_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", user_id):
            raise GrantRefused("device_unavailable")
        account = await reader.account(user_id)
        a = snapshot_data(account)
        if (d.get("device_id") != device_id or "revoked_at" not in d or d["revoked_at"] is not None
                or d.get("provisioning_state") != "active" or a.get("user_id") != user_id
                or a.get("suspended") is not False):
            raise GrantRefused("authority_refused")
        try:
            device_key = base64.b64decode(d.get("device_pub_b64", ""), validate=True)
        except (ValueError, TypeError) as e:
            raise GrantRefused("device_key_unavailable") from e
        if device_key != device_public_key:
            raise GrantRefused("device_key_mismatch")
        if (d.get("cf_tunnel_uuid") != tunnel_uuid or not d.get("cf_dns_record_id")
                or route_origin != "https://" + str(d.get("hostname_claim", ""))):
            raise GrantRefused("provisioning_mismatch")
        tunnel, ingress, dns = await asyncio.gather(
            reader.tunnel(d["cf_tunnel_uuid"]), reader.ingress(d["cf_tunnel_uuid"]),
            reader.dns(d["cf_dns_record_id"]))
        t, i, n = (snapshot_data(s) for s in (tunnel, ingress, dns))
        expected_ingress = [{"hostname": d["hostname_claim"], "service": expected_service},
                            {"service": "http_status:404"}]
        if (t.get("id") != d["cf_tunnel_uuid"] or "deleted_at" not in t or t["deleted_at"] is not None
                or t.get("config_src") != "cloudflare" or i.get("tunnel_id") != d["cf_tunnel_uuid"]
                or i.get("config", {}).get("ingress") != expected_ingress
                or n.get("id") != d["cf_dns_record_id"] or n.get("type") != "CNAME"
                or n.get("name") != d["hostname_claim"] or n.get("proxied") is not True
                or n.get("content") != d["cf_tunnel_uuid"] + ".cfargotunnel.com"):
            raise GrantRefused("cloudflare_mapping_mismatch")
    except GrantRefused:
        raise
    except Exception:
        # Propagate a safe failure, never swallow unavailable/malformed authority.
        raise GrantRefused("authority_unavailable") from None
    # Only version strings enter this projection. Device rows can contain
    # tunnel tokens; no row serialization, client field mask or broad relay.
    version = hashlib.sha256(json.dumps([s.version for s in (device, account, tunnel, ingress, dns)],
                                       separators=(",", ":")).encode()).hexdigest()
    return AuthorityResult(user_id=user_id, record_version=version)
