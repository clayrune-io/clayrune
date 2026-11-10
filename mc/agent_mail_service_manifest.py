"""Offline-root verification of mail-service pin candidates, never automatic repinning.

Unit 7b owns human consent and persistent sequence CAS storage outside the repo.
No roots, signing keys, network clients or ingress mutations are created here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class ManifestRefused(ValueError):
    """Safe reason code for a failed service-key candidate."""


@dataclass(frozen=True)
class ManifestState:
    sequence: int
    digest: str


class ManifestStateStore(Protocol):
    def load(self, origin: str) -> ManifestState | None: ...

    def compare_and_set(self, origin: str, expected: ManifestState | None, value: ManifestState) -> bool:
        """Durable, atomic, cross-process CAS; raise on corruption/I/O failure."""
        ...


@dataclass(frozen=True)
class ServiceKeyCandidate:
    origin: str
    sequence: int
    key_id: str
    public_key: str
    fingerprint: str
    retired_key_ids: tuple[str, ...]
    expires_at_ms: int


def _decode(value: str, size: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ManifestRefused("manifest_encoding")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if (len(raw) != size
            or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value):
        raise ManifestRefused("manifest_encoding")
    return raw


def verify_service_manifest(payload: list[object], signature: str, *, root_public_key: str,
                            service_origin: str, store: ManifestStateStore, now_ms: int,
                            enabled: bool = False) -> ServiceKeyCandidate:
    """Verify canonical array and persist rollback floor before returning a candidate.

    Payload: [purpose,version,origin,sequence,issued_ms,expires_ms,key_id,
              Ed25519_public_key,activate_ms,retire_ms,sorted_retired_key_ids].
    All scalar entries are ASCII strings. Strict array encoding avoids JSON
    object ordering/number/duplicate-key ambiguity across languages.
    """
    if enabled is not True:
        raise ManifestRefused("manifest_disabled")
    if (not re.fullmatch(r"https://(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", service_origin)
            or type(now_ms) is not int or now_ms < 0):
        raise ManifestRefused("manifest_configuration")
    if not isinstance(payload, list) or len(payload) != 11:
        raise ManifestRefused("manifest_shape")
    scalar = payload[:10]
    if any(not isinstance(s, str) or not re.fullmatch(r"[\x20-\x7e]{1,512}", s) for s in scalar):
        raise ManifestRefused("manifest_shape")
    # Runtime type validation above; explicit conversion keeps the typed API narrow.
    purpose, version, origin, seq, issued, expires, kid, public, activate, retire = map(str, scalar)
    retired = payload[10]
    if (purpose != "clayrune-mail-service-manifest" or version != "1" or origin != service_origin
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", kid)
            or not re.fullmatch(r"[1-9][0-9]{0,14}", seq)
            or any(not re.fullmatch(r"[1-9][0-9]{12}", s) for s in (issued, expires, activate, retire))
            or not isinstance(retired, list) or len(retired) > 100
            or any(not isinstance(k, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", k) for k in retired)):
        raise ManifestRefused("manifest_fields")
    retired_ids = tuple(str(k) for k in retired)
    if list(retired_ids) != sorted(set(retired_ids)) or kid in retired_ids:
        raise ManifestRefused("manifest_retired_key")
    if not (int(issued) <= now_ms < int(expires) <= int(retire)
            and int(issued) <= int(activate) <= now_ms < int(retire)):
        raise ManifestRefused("manifest_time")
    public_bytes = _decode(public, 32)
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(raw) > 16384:
        raise ManifestRefused("manifest_size")
    try:
        Ed25519PublicKey.from_public_bytes(_decode(root_public_key, 32)).verify(_decode(signature, 64), raw)
    except InvalidSignature as e:
        raise ManifestRefused("manifest_signature") from e
    state = ManifestState(int(seq), hashlib.sha256(raw).hexdigest())
    old = store.load(origin)  # Storage errors propagate; never reset the rollback floor.
    if old is not None and (type(old.sequence) is not int or old.sequence < 1
                            or not re.fullmatch(r"[a-f0-9]{64}", old.digest)):
        raise ManifestRefused("manifest_state_corrupt")
    if old is not None and (state.sequence < old.sequence
                            or state.sequence == old.sequence and state.digest != old.digest):
        raise ManifestRefused("manifest_rollback")
    # CAS also for an equal sequence: a concurrent higher manifest must prevent
    # returning the now-stale candidate. The store must compare before writing.
    if not store.compare_and_set(origin, old, state):
        raise ManifestRefused("manifest_state_raced")
    return ServiceKeyCandidate(origin, int(seq), kid, public, hashlib.sha256(public_bytes).hexdigest(),
                               retired_ids, int(expires))
