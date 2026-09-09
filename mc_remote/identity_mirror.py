"""
identity_mirror — redundant copy of the device-enrollment identity, so the
OS credential store is not a single point of failure for remote access.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

## Why this exists

Root cause of the 2026-09-08 outage (session 977afb9529eb): a Windows
servicing reboot (KB5124008) emptied the ENTIRE Credential Manager user
vault at 22:47:14 — both %APPDATA%\\Microsoft\\Credentials and
%LOCALAPPDATA%\\Microsoft\\Credentials, confirmed by matching mtimes and
`cmdkey /list` returning NONE. `device_keys` stores the six enrollment
fields there and nowhere else, so the wipe silently un-enrolled the device.

## Why this is NOT built on mc/secrets_store.py's agent-facing vault

`mc.secrets_store` is explicitly the vault an AGENT may read from —
`GET /api/secrets` lists every entry's metadata and `{{secret:name}}` /
`resolve_placeholders()` will substitute any of them into a command line an
agent runs. Device-enrollment material (an Ed25519 private key that proves
"this PC" to the control plane) must never be reachable that way. So this
module reuses none of `secrets_store`'s agent-facing surface (`set_secret`,
`get_secret_value`, `list_secrets`, the placeholder patterns) — only the
AES-256-GCM primitive and the `~/.clayrune` home-dir convention, kept in a
file of its own that no route or placeholder ever reads.

## Why this has its OWN encryption key, never the OS keyring

`secrets_store.load_master_key()` prefers the OS keyring (same Windows
Credential Manager the incident wiped) and only falls back to a 0600 key
file if the keyring already had nothing *the first time it was asked*. A
device already using the keyring backend would have its secrets-vault master
key live ONLY in Credential Manager — exactly as vulnerable to a vault wipe
as the thing this module exists to route around. So the mirror's key is
ALWAYS a 0600 file under `~/.clayrune/` (see `_load_or_create_key`) and never
touches `keyring` at all. That is what makes this a real second copy instead
of the same single point of failure with an extra step.

## Contract

- `write(fields)` persists the six enrollment values. Best-effort: it must
  never raise, because enrollment succeeding is more important than the
  mirror succeeding.
- `read()` returns the six values, or None if there is no usable mirror
  (absent, corrupt, or partially undecryptable — a half-identity is not
  restored).
- `clear()` removes the mirror. Called on explicit disconnect, so a revoked
  device doesn't get silently re-enrolled from a stale mirror on next start.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path
from typing import Optional

from mc.secrets_store import clayrune_home, _aesgcm, _write_private_text

log = logging.getLogger(__name__)

# The six logical fields device_keys.py manages. Matches config.KEYSTORE_KEYS'
# logical-name side (not the keyring entry-name side — this file has its own
# format and isn't a keyring shim).
FIELDS = (
    "device_pub", "device_id", "username", "hostname",
    "enrollment_token", "device_priv",
)

_MIRROR_VERSION = 1


def mirror_path() -> Path:
    return clayrune_home() / "remote_identity_mirror.json"


def _key_path() -> Path:
    return clayrune_home() / "remote_identity_mirror.key"


def _load_or_create_key() -> bytes:
    """File-only key, deliberately never stored in the OS keyring.

    See the module docstring: the mirror's entire purpose is to survive an
    OS-credential-store wipe, so its own key must not live there either.
    """
    p = _key_path()
    try:
        if p.is_file():
            encoded = p.read_text(encoding="utf-8").strip()
            if encoded:
                return base64.b64decode(encoded)
    except OSError as e:
        log.warning("identity mirror key read failed: %s", e)
    raw = os.urandom(32)
    _write_private_text(p, base64.b64encode(raw).decode("ascii"))
    return raw


def write(fields: dict) -> None:
    """Seal and persist `fields` (logical-name -> value). Best-effort.

    Called by device_keys.store_identity() as write-through redundancy —
    the OS keystore write is authoritative; this is a backup copy only, and
    a failure here must never fail enrollment itself.
    """
    try:
        key = _load_or_create_key()
        sealed = {}
        for name in FIELDS:
            value = fields.get(name)
            if not value:
                continue
            nonce = os.urandom(12)
            ct = _aesgcm(key).encrypt(nonce, value.encode("utf-8"), name.encode("utf-8"))
            sealed[name] = {
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ct).decode("ascii"),
            }
        data = {"version": _MIRROR_VERSION, "fields": sealed}
        _write_private_text(mirror_path(), json.dumps(data, indent=2))
    except Exception as e:
        log.warning("identity mirror write failed (enrollment unaffected): %s", e)


def read() -> Optional[dict]:
    """Return all six fields, or None if there's no complete, decryptable
    mirror. A partially-corrupt mirror is treated as absent rather than
    restoring a half-identity that would fail signing later."""
    p = mirror_path()
    try:
        if not p.is_file():
            return None
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("identity mirror read failed: %s", e)
        return None

    try:
        key = _load_or_create_key()
    except Exception as e:
        log.warning("identity mirror key unavailable: %s", e)
        return None

    sealed = raw.get("fields") if isinstance(raw, dict) else None
    if not isinstance(sealed, dict):
        return None

    out: dict = {}
    for name in FIELDS:
        rec = sealed.get(name)
        if not isinstance(rec, dict):
            return None
        try:
            nonce = base64.b64decode(rec["nonce"])
            ct = base64.b64decode(rec["ciphertext"])
            out[name] = _aesgcm(key).decrypt(nonce, ct, name.encode("utf-8")).decode("utf-8")
        except Exception as e:
            log.warning("identity mirror field '%s' undecryptable: %s", name, e)
            return None
    if not all(out.get(name) for name in FIELDS):
        return None
    return out


def clear() -> None:
    """Remove the mirror. Best-effort; called on explicit disconnect so a
    revoked device isn't silently self-healed back from a stale copy."""
    try:
        mirror_path().unlink(missing_ok=True)
    except Exception as e:
        log.warning("identity mirror clear failed: %s", e)
