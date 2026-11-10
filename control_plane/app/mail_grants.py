"""Disabled enrollment authority; all external reads are injected, read-only adapters.

This module registers no routes and imports no Firestore/Cloudflare clients.
Deployment, authenticated human/API wiring and JWKS publication are later gates.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .mail_enrollment_transcript import FIELDS, canonical_r, origin, unb64u
from .mail_grant_signing import MailGrantKey, PURPOSE


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


class MailGrantIssuer:
    def __init__(self, *, reader: AuthorityReader, key: MailGrantKey, issuer: str,
                 service_origin: str, enabled: bool = False, clock=None,
                 expected_service: str = "http://localhost:5199"):
        if not origin(issuer) or not origin(service_origin):
            raise ValueError("bad_authority_config")
        self.reader, self.key, self.issuer = reader, key, issuer
        self.service_origin, self.enabled = service_origin, enabled
        self.clock = clock or (lambda: int(time.time() * 1000))
        self.expected_service = expected_service

    async def issue(self, r: list[str], *, device_signature: str, mail_signature: str) -> str:
        if self.enabled is not True:
            raise GrantRefused("mail_grant_disabled")
        raw = canonical_r(r)
        m = dict(zip(FIELDS, r))
        checked_at = self.clock()  # Earliest read time; I/O latency never lengthens the lease.
        if (m["service_origin"] != self.service_origin
                or not checked_at < int(m["expires_ms"]) <= checked_at + 900_000):
            raise GrantRefused("challenge_unavailable")
        for pub, sig in ((m["device_public_key"], device_signature), (m["mail_public_key"], mail_signature)):
            try:
                Ed25519PublicKey.from_public_bytes(unb64u(pub, 32)).verify(unb64u(sig, 64), raw)
            except (ValueError, InvalidSignature) as e:
                raise GrantRefused("key_proof_required") from e
        try:
            device = await self.reader.device(m["device_id"])
            d = self._snapshot(device)
            user_id = d.get("user_id")
            if not isinstance(user_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", user_id):
                raise GrantRefused("device_unavailable")
            account = await self.reader.account(user_id)
            a = self._snapshot(account)
            if (d.get("device_id") != m["device_id"] or "revoked_at" not in d or d["revoked_at"] is not None
                    or d.get("provisioning_state") != "active" or a.get("user_id") != user_id
                    or a.get("suspended") is not False):
                raise GrantRefused("authority_refused")
            try:
                device_key = base64.b64decode(d.get("device_pub_b64", ""), validate=True)
            except (ValueError, TypeError) as e:
                raise GrantRefused("device_key_unavailable") from e
            if device_key != unb64u(m["device_public_key"], 32):
                raise GrantRefused("device_key_mismatch")
            if (d.get("cf_tunnel_uuid") != m["tunnel_uuid"] or not d.get("cf_dns_record_id")
                    or m["route_origin"] != "https://" + str(d.get("hostname_claim", ""))):
                raise GrantRefused("provisioning_mismatch")
            tunnel, ingress, dns = await asyncio.gather(
                self.reader.tunnel(d["cf_tunnel_uuid"]), self.reader.ingress(d["cf_tunnel_uuid"]),
                self.reader.dns(d["cf_dns_record_id"]))
            t, i, n = (self._snapshot(s) for s in (tunnel, ingress, dns))
            expected_ingress = [{"hostname": d["hostname_claim"], "service": self.expected_service},
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
        issued_at = checked_at // 1000
        now = self.clock()
        if now < checked_at or now >= (issued_at + 60) * 1000 or now >= int(m["expires_ms"]):
            raise GrantRefused("authority_check_expired")
        # Only version strings enter this projection. Device rows can contain
        # tunnel tokens; no row serialization, client field mask or broad relay.
        version = hashlib.sha256(json.dumps([s.version for s in (device, account, tunnel, ingress, dns)],
                                           separators=(",", ":")).encode()).hexdigest()
        claims = {"iss": self.issuer, "aud": PURPOSE, "token_use": PURPOSE, "sub": user_id,
                  "jti": secrets.token_hex(16), "iat": issued_at, "nbf": issued_at, "exp": issued_at + 60,
                  "checked_at_ms": checked_at, "record_version": version,
                  "r_sha256": hashlib.sha256(raw).hexdigest(),
                  **{k: m[k] for k in ("device_id", "tunnel_uuid", "route_origin", "install_id", "expected_generation")}}
        return self.key.sign(claims)

    @staticmethod
    def _snapshot(s: Snapshot) -> dict:
        if (not isinstance(s, Snapshot) or not isinstance(s.data, dict)
                or not isinstance(s.version, str) or not 1 <= len(s.version) <= 256):
            raise GrantRefused("authority_version_required")
        return s.data
