"""Disabled enrollment authority; all external reads are injected, read-only adapters.

This module registers no routes and imports no Firestore/Cloudflare clients.
Deployment, authenticated human/API wiring and JWKS publication are later gates.
"""
from __future__ import annotations

import hashlib
import secrets
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .mail_authority_check import (  # noqa: F401  (re-exported: 7a callers import these names from here)
    AuthorityReader, GrantRefused, Snapshot, check_authority, snapshot_data)
from .mail_enrollment_transcript import FIELDS, canonical_r, origin, unb64u
from .mail_grant_signing import MailGrantKey, PURPOSE


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
        auth = await check_authority(
            self.reader, device_id=m["device_id"], device_public_key=unb64u(m["device_public_key"], 32),
            tunnel_uuid=m["tunnel_uuid"], route_origin=m["route_origin"], expected_service=self.expected_service)
        user_id = auth.user_id
        issued_at = checked_at // 1000
        now = self.clock()
        if now < checked_at or now >= (issued_at + 60) * 1000 or now >= int(m["expires_ms"]):
            raise GrantRefused("authority_check_expired")
        version = auth.record_version
        claims = {"iss": self.issuer, "aud": PURPOSE, "token_use": PURPOSE, "sub": user_id,
                  "jti": secrets.token_hex(16), "iat": issued_at, "nbf": issued_at, "exp": issued_at + 60,
                  "checked_at_ms": checked_at, "record_version": version,
                  "r_sha256": hashlib.sha256(raw).hexdigest(),
                  **{k: m[k] for k in ("device_id", "tunnel_uuid", "route_origin", "install_id", "expected_generation")}}
        return self.key.sign(claims)

    _snapshot = staticmethod(snapshot_data)
