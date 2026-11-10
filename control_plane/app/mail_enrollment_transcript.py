"""Mail-specific ASCII transcript, shared contract with enrollment-transcript.js."""
from __future__ import annotations

import base64
import hashlib
import json
import re

FIELDS = (
    "purpose", "version", "service_origin", "flow_id", "challenge_id", "challenge_nonce",
    "expires_ms", "install_id", "expected_generation", "device_id", "device_public_key",
    "mail_key_id", "mail_public_key", "owner_address_sha256", "tunnel_uuid", "route_origin",
    "route_path", "manifest_sequence", "service_key_id", "service_public_key_sha256",
)


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def unb64u(value: str, size: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("bad_encoding")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(raw) != size or b64u(raw) != value:
        raise ValueError("bad_encoding")
    return raw


def origin(value: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(
        r"https://(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", value))


def canonical_r(r: list[str]) -> bytes:
    if (not isinstance(r, list) or len(r) != len(FIELDS)
            or any(not isinstance(s, str) or not re.fullmatch(r"[\x20-\x7e]{1,512}", s) for s in r)):
        raise ValueError("bad_transcript")
    m = dict(zip(FIELDS, r))
    if (m["purpose"] != "clayrune-mail-enrollment" or m["version"] != "1"
            or not origin(m["service_origin"]) or not origin(m["route_origin"])
            or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", m[k]) for k in (
                "flow_id", "challenge_id", "install_id", "device_id", "mail_key_id", "service_key_id"))
            or not re.fullmatch(r"[a-f0-9]{64}", m["challenge_nonce"])
            or not re.fullmatch(r"[1-9][0-9]{12}", m["expires_ms"])
            or not re.fullmatch(r"0|[1-9][0-9]{0,14}", m["expected_generation"])
            or not re.fullmatch(r"[1-9][0-9]{0,14}", m["manifest_sequence"])
            or any(not re.fullmatch(r"[a-f0-9]{64}", m[k]) for k in (
                "owner_address_sha256", "service_public_key_sha256"))
            or not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", m["tunnel_uuid"])
            or m["route_path"] != "/api/agent-mail/ingress"):
        raise ValueError("bad_transcript")
    unb64u(m["device_public_key"], 32)
    unb64u(m["mail_public_key"], 32)
    return json.dumps(r, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def r_digest(r: list[str]) -> str:
    return hashlib.sha256(canonical_r(r)).hexdigest()
