"""
device_keys — Ed25519 device keypair management via OS keystore.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

The device private key proves "this enrolled user/device" in attestation
(`02-attestation-protocol.md` §3.4). It must never leave the user's PC.

Storage:
- OS keystore via the `keyring` library
  - Windows: Credential Manager (WinVaultKeyring)
  - macOS:   Keychain
  - Linux:   Secret Service / KWallet
- Six entries under service name from `config.KEYSTORE_SERVICE`:
  device_priv_b64 / device_pub_b64 / device_id / username / hostname / enrollment_token

Key encoding:
- Private key = raw 32-byte Ed25519 seed (NOT PEM), base64-encoded for storage
- Public key  = raw 32-byte Ed25519 point, base64-encoded
- Signatures  = 64 bytes (Ed25519 detached)

Failure modes:
- Keystore not configured / locked → KeystoreUnavailable
- Identity not yet enrolled       → load_identity() returns None (not an error)
- Corrupted entries               → load_device_priv() returns None + logs warning
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PrivateFormat,
    PublicFormat,
    NoEncryption,
)

import keyring
from keyring.errors import KeyringError, KeyringLocked, NoKeyringError

from . import config, identity_mirror

log = logging.getLogger(__name__)

# ─── Public types ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeviceIdentity:
    """All non-secret fields produced by enrollment. Excludes the private key.

    The private key is loaded separately on demand via load_device_priv() so
    callers handling identity (e.g. UI status) never accidentally hold the
    secret in memory longer than needed.
    """

    device_id: str
    device_pub_b64: str
    username: str
    hostname: str
    enrollment_token: str


class KeystoreUnavailable(RuntimeError):
    """Raised when the OS keystore can't be reached at all."""


# ─── Internals ───────────────────────────────────────────────────────────────


def _entry_name(logical: str) -> str:
    """logical key name -> keyring entry name (per config.KEYSTORE_KEYS)."""
    try:
        return config.KEYSTORE_KEYS[logical]
    except KeyError as e:
        raise KeyError(f"Unknown keystore logical key: {logical!r}") from e


def _set(logical: str, value: str) -> None:
    name = _entry_name(logical)
    try:
        keyring.set_password(config.KEYSTORE_SERVICE, name, value)
    except (KeyringLocked, NoKeyringError) as e:
        raise KeystoreUnavailable(f"OS keystore unavailable: {e}") from e
    except KeyringError as e:
        raise KeystoreUnavailable(f"Keystore write failed for {name}: {e}") from e


def _get(logical: str) -> Optional[str]:
    name = _entry_name(logical)
    try:
        return keyring.get_password(config.KEYSTORE_SERVICE, name)
    except (KeyringLocked, NoKeyringError) as e:
        raise KeystoreUnavailable(f"OS keystore unavailable: {e}") from e
    except KeyringError as e:
        log.warning("keystore read failed for %s: %s", name, e)
        return None


def _del(logical: str) -> None:
    name = _entry_name(logical)
    try:
        keyring.delete_password(config.KEYSTORE_SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        # Already absent — that's fine.
        pass
    except (KeyringLocked, NoKeyringError) as e:
        raise KeystoreUnavailable(f"OS keystore unavailable: {e}") from e
    except KeyringError as e:
        log.warning("keystore delete failed for %s: %s", name, e)


# ─── Public API ──────────────────────────────────────────────────────────────


def generate_keypair() -> tuple[str, str]:
    """Generate a fresh Ed25519 keypair.

    Returns (pub_b64, priv_b64) — both base64 of raw 32-byte values.

    The result is **not** persisted. Call store_identity() once enrollment
    succeeds to commit it to the keystore.
    """
    priv = Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return (
        base64.b64encode(pub_raw).decode("ascii"),
        base64.b64encode(priv_raw).decode("ascii"),
    )


def store_identity(identity: DeviceIdentity, device_priv_b64: str) -> None:
    """Persist an enrolled identity + its private key to the OS keystore.

    All-or-nothing: if any write fails, attempt rollback so we don't leave a
    half-enrolled state behind.
    """
    written: list[str] = []
    try:
        # Keys to write, in order. Private key last so a failure earlier
        # leaves nothing usable behind.
        for logical, value in (
            ("device_pub",       identity.device_pub_b64),
            ("device_id",        identity.device_id),
            ("username",         identity.username),
            ("hostname",         identity.hostname),
            ("enrollment_token", identity.enrollment_token),
            ("device_priv",      device_priv_b64),
        ):
            _set(logical, value)
            written.append(logical)
    except Exception:
        # Best-effort rollback; raise the original error.
        for logical in written:
            try:
                _del(logical)
            except Exception:
                pass
        raise

    # Write-through mirror into Clayrune's own vault (identity_mirror.py) so
    # a wiped OS credential store — the 2026-09-08 incident — isn't a single
    # point of failure for this identity. The keystore write above is
    # authoritative; this is redundancy only and must never fail enrollment.
    identity_mirror.write({
        "device_pub":       identity.device_pub_b64,
        "device_id":        identity.device_id,
        "username":         identity.username,
        "hostname":         identity.hostname,
        "enrollment_token": identity.enrollment_token,
        "device_priv":      device_priv_b64,
    })


def load_identity() -> Optional[DeviceIdentity]:
    """Return the persisted identity if all required fields are present.

    Returns None if not enrolled. Does NOT load or expose the private key.

    If a field is genuinely missing (`_get` returns falsy — NOT the
    KeystoreUnavailable exception, which is a read failure and propagates
    untouched) this falls back to the Clayrune vault mirror
    (`identity_mirror.py`). Conflating the two would defeat the whole point:
    `enrolled=false, error_code=None` must mean "really not enrolled," and
    `error_code=tunnel_keystore_unavailable` must mean "can't tell right
    now" — see provider_impl.status().
    """
    fields = {}
    for logical in ("device_pub", "device_id", "username", "hostname", "enrollment_token"):
        v = _get(logical)
        if not v:
            return _restore_from_mirror()
        fields[logical] = v
    return DeviceIdentity(
        device_id=fields["device_id"],
        device_pub_b64=fields["device_pub"],
        username=fields["username"],
        hostname=fields["hostname"],
        enrollment_token=fields["enrollment_token"],
    )


def _restore_from_mirror() -> Optional[DeviceIdentity]:
    """One or more keystore fields are genuinely absent. Check the vault
    mirror; if it has a complete identity, restore it INTO the keystore
    (self-heal) so subsequent reads don't depend on the mirror forever, then
    return it. Returns None if the mirror has nothing usable either — that
    is a real "not enrolled."
    """
    mirrored = identity_mirror.read()
    if mirrored is None:
        return None
    identity = DeviceIdentity(
        device_id=mirrored["device_id"],
        device_pub_b64=mirrored["device_pub"],
        username=mirrored["username"],
        hostname=mirrored["hostname"],
        enrollment_token=mirrored["enrollment_token"],
    )
    try:
        store_identity(identity, mirrored["device_priv"])
        log.warning("keystore identity missing; restored from Clayrune vault mirror")
    except KeystoreUnavailable as e:
        log.warning("keystore identity missing; mirror found but restore failed: %s", e)
    return identity


def load_device_priv() -> Optional[bytes]:
    """Return the raw 32-byte private key seed if enrolled, else None.

    Callers should not hold this in memory longer than necessary. Falls back
    to the vault mirror the same way load_identity() does — a caller (e.g.
    sign()) may ask for the private key without having called
    load_identity() first.
    """
    s = _get("device_priv")
    if not s:
        mirrored = identity_mirror.read()
        s = mirrored.get("device_priv") if mirrored else None
        if s:
            log.warning("device_priv missing from keystore; using Clayrune vault mirror")
            try:
                _set("device_priv", s)
            except KeystoreUnavailable:
                pass
    if not s:
        return None
    try:
        raw = base64.b64decode(s, validate=True)
    except Exception as e:
        log.warning("device_priv decode failed: %s", e)
        return None
    if len(raw) != 32:
        log.warning("device_priv unexpected length: %d (want 32)", len(raw))
        return None
    return raw


def is_enrolled() -> bool:
    """Cheap check: are all required identity fields present?

    Used by the provider's is_enabled() / status() — must not raise on the
    "happy negative" path (no keystore entries yet).
    """
    try:
        return load_identity() is not None
    except KeystoreUnavailable:
        # Treat keystore failure as "not enrolled" for status purposes; the
        # next attempt to actually use the keystore will surface the real error.
        return False


def clear_identity() -> None:
    """Wipe all keystore entries for this service.

    Best-effort: keeps deleting other entries even if one delete fails. Used
    when the user clicks "Disconnect this PC" or after enrollment rollback.
    """
    last_err: Optional[Exception] = None
    for logical in ("device_priv", "enrollment_token", "hostname",
                    "username", "device_id", "device_pub"):
        try:
            _del(logical)
        except Exception as e:
            last_err = e
    # Clear the mirror too: without this, a disconnected/revoked device
    # would self-heal itself back on the next load_identity() call via
    # _restore_from_mirror(), silently undoing an explicit disconnect.
    identity_mirror.clear()
    if last_err is not None:
        # Don't raise — the surface area where this matters (disconnect)
        # benefits more from "we tried" than from a hard failure. Log only.
        log.warning("clear_identity completed with at least one error: %s", last_err)


def sign(message: bytes) -> bytes:
    """Sign `message` with the persisted device private key.

    Returns a 64-byte Ed25519 signature.
    Raises:
      - KeystoreUnavailable if keystore can't be read
      - RuntimeError if no key is enrolled
    """
    raw = load_device_priv()
    if raw is None:
        raise RuntimeError("No device private key in keystore — not enrolled")
    priv = Ed25519PrivateKey.from_private_bytes(raw)
    return priv.sign(message)


def verify_with_pub(pub_b64: str, message: bytes, signature: bytes) -> bool:
    """Verify `signature` over `message` under `pub_b64`. Used in tests.

    Returns False on bad signature; raises ValueError on bad pubkey encoding.
    """
    try:
        pub_raw = base64.b64decode(pub_b64, validate=True)
    except Exception as e:
        raise ValueError(f"Bad pubkey base64: {e}") from e
    if len(pub_raw) != 32:
        raise ValueError(f"Pubkey must be 32 bytes, got {len(pub_raw)}")
    pub = Ed25519PublicKey.from_public_bytes(pub_raw)
    try:
        pub.verify(signature, message)
        return True
    except InvalidSignature:
        return False
