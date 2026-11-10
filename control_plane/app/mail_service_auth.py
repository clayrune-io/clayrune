"""Service-to-CP request authentication for mail delivery (MC-1166 7d-2b, contract section 2, D1).

The agent-mail Worker signs each CP call with its existing Ed25519 service key
using the Worker's canonical-array scheme (`workers/agent-mail/signing.js`),
with the signed `direction` fixed to `service-to-cp`. This module verifies that
signature. It is not a Cloudflare Access token, not a dashboard credential and
not the device key.

Trusted keys come only from reviewed deployment config
(`CLAYRUNE_MAIL_SERVICE_KEYS`, a JSON array of `{key_id, public_key_b64u}`);
nothing about the key is ever read from the request. The nonce claim is durable
and made only after the signature verifies, so a forged request cannot burn a
caller's nonce (the `signing.js` rule).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .mail_enrollment_transcript import unb64u

SKEW_MS = 300_000
DIRECTION = "service-to-cp"
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_GEN = re.compile(r"[1-9][0-9]{0,14}")
_NONCE = re.compile(r"[a-f0-9]{32}")
_HEX = re.compile(r"[a-f0-9]{64}")
_STAMP = re.compile(r"[1-9][0-9]{12}")
_PATH = re.compile(r"/[A-Za-z0-9/_-]{1,200}")


class ServiceAuthRefused(Exception):
    """Closed-code refusal. `code` is a constant; no request content is ever in it."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ServiceCall:
    key_id: str
    install_id: str
    generation: str  # decimal string, exactly as signed
    nonce: str
    timestamp: int
    digest: str
    path: str


@dataclass(frozen=True)
class ServiceKeyring:
    keys: dict  # key_id -> raw 32-byte Ed25519 public key

    def public_key_sha256(self, key_id: str) -> str:
        return hashlib.sha256(self.keys[key_id]).hexdigest()


def parse_service_keys(raw: str | None) -> ServiceKeyring:
    """Parse reviewed config. Unset, malformed or duplicate entries refuse (no keys = no auth)."""
    if not isinstance(raw, str) or not raw.strip():
        raise ServiceAuthRefused("service_keys_not_configured")
    try:
        entries = json.loads(raw)
    except ValueError:
        raise ServiceAuthRefused("service_keys_invalid") from None
    if not isinstance(entries, list) or not entries:
        raise ServiceAuthRefused("service_keys_invalid")
    keys: dict = {}
    for e in entries:
        if (not isinstance(e, dict) or set(e) != {"key_id", "public_key_b64u"}
                or not isinstance(e["key_id"], str) or not _ID.fullmatch(e["key_id"])
                or e["key_id"] in keys):
            raise ServiceAuthRefused("service_keys_invalid")
        try:
            keys[e["key_id"]] = unb64u(e["public_key_b64u"], 32)
        except ValueError:
            raise ServiceAuthRefused("service_keys_invalid") from None
    return ServiceKeyring(keys)


def canonical_array(call_fields: Mapping[str, str]) -> bytes:
    """The exact bytes the Worker signs (`signing.js` `canonical`); ASCII JSON array."""
    m = call_fields
    return json.dumps(["clayrune-agent-mail", m["version"], m["direction"], m["key_id"], m["install_id"],
                       m["generation"], m["method"], m["path"], m["timestamp"], m["nonce"], m["digest"]],
                      separators=(",", ":"), ensure_ascii=True).encode("ascii")


def verify_service_call(headers: Mapping[str, str], body: bytes, *, method: str, path: str,
                        keyring: ServiceKeyring,
                        claim_nonce: Callable[[str, str, int], bool],
                        clock_ms: Callable[[], int] = lambda: int(time.time() * 1000)) -> ServiceCall:
    """Verify one signed service-to-CP request or raise ServiceAuthRefused.

    `claim_nonce(key_id, nonce, expires_ms)` must be a durable, atomic
    create-if-absent returning True only for the first claim. A storage
    failure inside it propagates (an operational failure is not an auth result).
    """
    h = lambda f: headers.get("x-mail-" + f, "")
    version, direction, key_id, install, gen, stamp, nonce, digest = (
        h(f) for f in ("version", "direction", "key-id", "install-id", "generation",
                       "timestamp", "nonce", "digest"))
    if (version != "1" or direction != DIRECTION or method != "POST" or not _PATH.fullmatch(path)
            or not _ID.fullmatch(key_id) or not _ID.fullmatch(install) or not _GEN.fullmatch(gen)
            or not _STAMP.fullmatch(stamp) or not _NONCE.fullmatch(nonce) or not _HEX.fullmatch(digest)):
        raise ServiceAuthRefused("bad_signature_fields")
    public = keyring.keys.get(key_id)
    if public is None:
        raise ServiceAuthRefused("unknown_service_key")
    fresh = lambda: abs(clock_ms() - int(stamp)) <= SKEW_MS
    if not fresh():
        raise ServiceAuthRefused("stale_signature")
    if hashlib.sha256(body).hexdigest() != digest:
        raise ServiceAuthRefused("bad_digest")
    try:
        sig = unb64u(h("signature"), 64)
        Ed25519PublicKey.from_public_bytes(public).verify(sig, canonical_array({
            "version": version, "direction": direction, "key_id": key_id, "install_id": install,
            "generation": gen, "method": method, "path": path, "timestamp": stamp,
            "nonce": nonce, "digest": digest}))
    except (ValueError, InvalidSignature):
        raise ServiceAuthRefused("bad_signature") from None
    if not fresh():  # verification may finish late
        raise ServiceAuthRefused("stale_signature")
    if not claim_nonce(key_id, nonce, int(stamp) + SKEW_MS + 1000):
        raise ServiceAuthRefused("replayed_nonce")
    if not fresh():  # a slow durable claim never admits an expired window
        raise ServiceAuthRefused("stale_signature")
    return ServiceCall(key_id, install, gen, nonce, int(stamp), digest, path)


class RefusalBucket:
    """One bounded bucket shared by every unauthenticated refusal (worker.js:42-47 pattern).

    Applied only on refusal, so abuse can neither lock out a valid signature
    nor consume an install's allowance. In-process by design: it bounds log and
    CPU amplification, it is not an authority.
    """

    def __init__(self, limit: int = 10_000, window_ms: int = 60_000,
                 clock_ms: Callable[[], int] = lambda: int(time.time() * 1000)):
        self.limit, self.window_ms, self.clock_ms = limit, window_ms, clock_ms
        self._start, self._count = 0, 0

    def allow(self) -> bool:
        now = self.clock_ms()
        if now - self._start >= self.window_ms:
            self._start, self._count = now, 0
        self._count += 1
        return self._count <= self.limit
