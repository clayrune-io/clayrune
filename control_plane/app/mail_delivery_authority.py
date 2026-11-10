"""Delivery-capability authority: bind, issue, lease, revoke (MC-1166 7d-2b, contract 1.2-1.5).

Registers no routes and imports no Firestore or Cloudflare client of its own: the
authority reader and the store are injected. A capability is a 60 s ES256 token,
issued per request, bound to one (install, generation, nonce, digest). CP issues
only when a fresh authority read agrees with a binding created from a verified
enrollment grant; anything unreadable, stale or mismatched refuses with a closed
code and there is no fallback to a dashboard credential.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from . import mail_grant_signing
from .mail_authority_check import AuthorityReader, GrantRefused, check_authority
from .mail_delivery_signing import DeliveryKeyring, PURPOSE as DELIVERY_PURPOSE
from .mail_delivery_store import DeliveryStore
from .mail_enrollment_transcript import FIELDS, b64u, canonical_r, unb64u
from .mail_service_auth import ServiceCall, ServiceKeyring

ROUTE_PATH = "/api/agent-mail/ingress"
LEASE_MS = 300_000
GRANT_VERIFY_WINDOW_MS = 300_000
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_GEN = re.compile(r"[1-9][0-9]{0,14}")
_NONCE = re.compile(r"[a-f0-9]{32}")
_HEX = re.compile(r"[a-f0-9]{64}")
MODES = ("mail", "proof")


class DeliveryRefused(Exception):
    """Closed-code refusal. `code` is a constant; no row, key or body is ever in it."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _strict_b64u(s: str) -> bytes:
    if not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", s):
        raise ValueError("bad_encoding")
    raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    if b64u(raw) != s:
        raise ValueError("bad_encoding")
    return raw


def verify_own_grant(token: str, jwks: list, *, issuer: str, now_ms: int) -> dict:
    """Verify a grant this CP issued (aud `clayrune-mail-enrollment`) against reviewed public JWKs.

    The 60 s `exp` bounds how long the Worker may use the grant to commit; binding
    happens afterwards, so the freshness bound here is the ownership lease:
    `checked_at_ms + 300000 > now` (contract 1.5). Raises DeliveryRefused('bad_grant').
    """
    bad = DeliveryRefused("bad_grant")
    try:
        if not isinstance(token, str) or len(token) > 8192:
            raise bad
        parts = token.split(".")
        if len(parts) != 3:
            raise bad
        decode = lambda s: json.loads(_strict_b64u(s).decode("utf-8"))
        h, c = decode(parts[0]), decode(parts[1])
        if (not isinstance(h, dict) or sorted(h) != ["alg", "kid", "typ"] or h["alg"] != "ES256"
                or h["typ"] != mail_grant_signing.TOKEN_TYPE or not isinstance(h["kid"], str)):
            raise bad
        matches = [k for k in jwks if isinstance(k, dict) and k.get("kid") == h["kid"]]
        if len(matches) != 1:
            raise bad
        k = matches[0]
        if (k.get("kty") != "EC" or k.get("crv") != "P-256" or k.get("alg") != "ES256"
                or k.get("use") != "sig" or "d" in k):
            raise bad
        pub = ec.EllipticCurvePublicNumbers(int.from_bytes(unb64u(k["x"], 32), "big"),
                                            int.from_bytes(unb64u(k["y"], 32), "big"),
                                            ec.SECP256R1()).public_key()
        sig = _strict_b64u(parts[2])
        if len(sig) != 64:
            raise bad
        pub.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                   f"{parts[0]}.{parts[1]}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    except DeliveryRefused:
        raise
    except (ValueError, KeyError, TypeError, InvalidSignature, UnicodeDecodeError):
        raise bad from None
    ints = ("iat", "nbf", "exp", "checked_at_ms")
    if (not isinstance(c, dict) or c.get("iss") != issuer
            or c.get("aud") != mail_grant_signing.PURPOSE or c.get("token_use") != mail_grant_signing.PURPOSE
            or not isinstance(c.get("sub"), str) or not _ID.fullmatch(c["sub"])
            or not isinstance(c.get("jti"), str) or not _NONCE.fullmatch(c["jti"])
            or not isinstance(c.get("record_version"), str) or not _HEX.fullmatch(c["record_version"])
            or not all(type(c.get(i)) is int for i in ints)
            or c["iat"] != c["nbf"] or not 0 < c["exp"] - c["iat"] <= 60
            or c["iat"] * 1000 > now_ms or c["checked_at_ms"] + GRANT_VERIFY_WINDOW_MS <= now_ms
            or c["checked_at_ms"] > c["iat"] * 1000 + 999 or c["checked_at_ms"] < c["iat"] * 1000 - 999):
        raise bad
    return c


@dataclass
class MailDeliveryAuthority:
    reader: Optional[AuthorityReader]
    keyring: DeliveryKeyring
    store: DeliveryStore
    issuer: str
    service_keys: ServiceKeyring
    grant_jwks: list
    expected_service: str = "http://localhost:5199"
    install_budget: int = 600       # initial; 3x the ingress cap, tunable without a contract change
    global_budget: int = 50_000     # initial global breaker
    clock: Callable[[], int] = lambda: int(time.time() * 1000)

    # ---- shared -----------------------------------------------------------------------

    @staticmethod
    def _scope(call: ServiceCall, body: dict) -> int:
        if (body.get("install_id") != call.install_id or body.get("generation") != call.generation
                or not _GEN.fullmatch(str(body.get("generation", "")))):
            raise DeliveryRefused("scope_mismatch")
        return int(call.generation)

    def _binding_for(self, call: ServiceCall, generation: int) -> dict:
        b = self.store.get_binding(call.install_id)
        if (b is None or b.get("state") != "bound" or b.get("generation") != generation
                or generation <= b.get("generation_floor", 0)):
            raise DeliveryRefused("binding_required")
        if b.get("service_key_id") != call.key_id or (
                call.key_id not in self.service_keys.keys
                or self.service_keys.public_key_sha256(call.key_id) != b.get("service_public_key_sha256")):
            raise DeliveryRefused("service_key_mismatch")
        return b

    async def _authority(self, b: dict):
        if self.reader is None:
            raise DeliveryRefused("authority_unavailable")
        try:
            auth = await check_authority(
                self.reader, device_id=b["device_id"], device_public_key=_strict_b64u(b["device_public_key"]),
                tunnel_uuid=b["tunnel_uuid"], route_origin=b["route_origin"],
                expected_service=self.expected_service)
        except GrantRefused as e:
            raise DeliveryRefused(str(e)) from None
        if auth.user_id != b["user_id"]:
            raise DeliveryRefused("authority_refused")
        return auth

    # ---- bind -------------------------------------------------------------------------

    def bind(self, call: ServiceCall, body: dict) -> dict:
        if set(body) != {"grant", "r"}:
            raise DeliveryRefused("bad_request")
        try:
            raw = canonical_r(body["r"])
        except ValueError:
            raise DeliveryRefused("bad_transcript") from None
        m = dict(zip(FIELDS, body["r"]))
        generation = int(m["expected_generation"]) + 1
        if call.install_id != m["install_id"] or call.generation != str(generation):
            raise DeliveryRefused("scope_mismatch")
        now = self.clock()
        c = verify_own_grant(body["grant"], self.grant_jwks, issuer=self.issuer, now_ms=now)
        if (c["r_sha256"] != hashlib.sha256(raw).hexdigest()
                or any(c.get(k) != m[k] for k in ("device_id", "tunnel_uuid", "route_origin", "install_id",
                                                  "expected_generation"))):
            raise DeliveryRefused("bad_grant")
        if (call.key_id != m["service_key_id"] or call.key_id not in self.service_keys.keys
                or self.service_keys.public_key_sha256(call.key_id) != m["service_public_key_sha256"]):
            raise DeliveryRefused("service_key_mismatch")
        record = {"generation": generation, "state": "bound", "generation_floor": 0,
                  "user_id": c["sub"], "device_id": m["device_id"], "tunnel_uuid": m["tunnel_uuid"],
                  "route_origin": m["route_origin"], "mail_key_id": m["mail_key_id"],
                  "device_public_key": m["device_public_key"], "service_key_id": m["service_key_id"],
                  "service_public_key_sha256": m["service_public_key_sha256"], "grant_jti": c["jti"],
                  "r_sha256": c["r_sha256"], "bound_at_ms": now, "revoked_at_ms": None}

        def decide(cur):
            if cur is None:
                return record, "bound"
            floor = cur.get("generation_floor", 0)
            if generation <= floor:
                return None, "revoked_generation"
            if cur.get("generation") == generation:
                if cur.get("grant_jti") == c["jti"]:
                    return None, ("already_bound" if cur.get("state") == "bound" else "revoked_generation")
                return None, "binding_conflict"
            if generation < cur.get("generation", 0):
                return None, "binding_conflict"
            return {**record, "generation_floor": floor}, "bound"

        result = self.store.mutate_binding(call.install_id, decide)
        if result not in ("bound", "already_bound"):
            raise DeliveryRefused(result)
        return {"install_id": call.install_id, "generation": str(generation), "state": "bound",
                "idempotent": result == "already_bound"}

    # ---- issue ------------------------------------------------------------------------

    async def issue(self, call: ServiceCall, body: dict) -> dict:
        allowed = {"install_id", "generation", "mode", "req_digest", "mail_nonce", "mail_key_id", "worker_active"}
        if (not isinstance(body, dict) or not {"install_id", "generation", "mode", "req_digest",
                                               "mail_nonce", "mail_key_id"} <= set(body) <= allowed):
            raise DeliveryRefused("bad_request")
        generation = self._scope(call, body)
        if (body["mode"] not in MODES or not isinstance(body["req_digest"], str)
                or not _HEX.fullmatch(body["req_digest"]) or not isinstance(body["mail_nonce"], str)
                or not _NONCE.fullmatch(body["mail_nonce"]) or not isinstance(body["mail_key_id"], str)
                or not _ID.fullmatch(body["mail_key_id"])):
            raise DeliveryRefused("bad_request")
        checked_at = self.clock()  # Earliest read time; I/O latency never lengthens the lease.
        b = self._binding_for(call, generation)
        if body["mail_key_id"] != b["mail_key_id"]:
            raise DeliveryRefused("key_mismatch")
        auth = await self._authority(b)
        issued_at = checked_at // 1000
        now = self.clock()
        if now < checked_at or now >= (issued_at + 60) * 1000:
            raise DeliveryRefused("authority_check_expired")
        jti = secrets.token_hex(16)
        claim = self.store.claim_issuance(call.install_id, call.generation, body["mail_nonce"],
                                          body["req_digest"], jti, body["mode"], now)
        if claim == "nonce_reused":
            raise DeliveryRefused("nonce_reused")
        if self.store.consume_budget(call.install_id, now, install_limit=self.install_budget,
                                     global_limit=self.global_budget) != "ok":
            raise DeliveryRefused("issue_budget")
        claims = {"iss": self.issuer, "aud": DELIVERY_PURPOSE, "token_use": DELIVERY_PURPOSE, "sub": b["user_id"],
                  "jti": jti, "iat": issued_at, "nbf": issued_at, "exp": issued_at + 60,
                  "mode": body["mode"], "install_id": call.install_id, "generation": call.generation,
                  "device_id": b["device_id"], "route_origin": b["route_origin"], "route_path": ROUTE_PATH,
                  "method": "POST", "req_digest": body["req_digest"], "mail_nonce": body["mail_nonce"],
                  "mail_key_id": b["mail_key_id"], "checked_at_ms": checked_at,
                  "record_version": auth.record_version, "binding_jti": b["grant_jti"]}
        return {"capability": self.keyring.signing_key().sign(claims), "jti": jti, "exp": claims["exp"]}

    # ---- lease ------------------------------------------------------------------------

    async def lease(self, call: ServiceCall, body: dict) -> dict:
        if not isinstance(body, dict) or set(body) != {"install_id", "generation"}:
            raise DeliveryRefused("bad_request")
        generation = self._scope(call, body)
        checked_at = self.clock()
        b = self._binding_for(call, generation)
        auth = await self._authority(b)
        now = self.clock()
        if now < checked_at or now >= checked_at + 60_000:
            raise DeliveryRefused("authority_check_expired")
        return {"install_id": call.install_id, "generation": call.generation, "checked_at_ms": checked_at,
                "record_version": auth.record_version, "lease_until_ms": checked_at + LEASE_MS}

    # ---- revoke -----------------------------------------------------------------------

    def revoke(self, call: ServiceCall, body: dict) -> dict:
        if not isinstance(body, dict) or set(body) != {"install_id", "generation"}:
            raise DeliveryRefused("bad_request")
        generation = self._scope(call, body)
        if call.key_id not in self.service_keys.keys:
            raise DeliveryRefused("service_key_mismatch")
        now = self.clock()
        key_hash = self.service_keys.public_key_sha256(call.key_id)

        def decide(cur):
            if cur is None:
                return ({"generation": generation, "state": "revoked", "generation_floor": generation,
                         "service_key_id": call.key_id, "service_public_key_sha256": key_hash,
                         "bound_at_ms": None, "revoked_at_ms": now}, "fenced")
            if cur.get("service_key_id") != call.key_id or cur.get("service_public_key_sha256") != key_hash:
                return None, "service_key_mismatch"
            floor = max(cur.get("generation_floor", 0), generation)
            if cur.get("generation", 0) > generation:
                # A newer generation is bound: record the floor, never touch the newer binding.
                if floor == cur.get("generation_floor", 0):
                    return None, "already_superseded"
                return {**cur, "generation_floor": floor}, "already_superseded"
            return {**cur, "state": "revoked", "generation_floor": floor,
                    "revoked_at_ms": cur.get("revoked_at_ms") or now}, "revoked"

        result = self.store.mutate_binding(call.install_id, decide)
        if result == "service_key_mismatch":
            raise DeliveryRefused(result)
        return {"install_id": call.install_id, "generation": call.generation, "state": result}
