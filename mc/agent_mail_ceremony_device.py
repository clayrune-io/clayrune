"""Device-identity seam for the mail enrollment ceremony (MC-1166 unit 7d-2e-3, contract 3.1).

The ceremony transcript names the enrolled device (id, public key, tunnel, route) and the device
key signs it. Those facts live in `mc_remote`, which is proprietary and which `mc/` may only
reach through a lazy import inside a function (as `mc/blueprints/remote_routes.py` does). This
module is the ONE place in the mail code that does so; everything else takes a `DeviceIdentity`.

`keystore_identity()` is the default provider. It raises the closed code
`device_identity_unavailable` for every way the identity can be missing: `mc_remote` not
importable, keystore locked or unreachable, device not enrolled, no tunnel id recorded yet,
malformed values. Nothing here generates a key, writes the keystore or makes a network call.
"""
from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Callable, Protocol

from mc.agent_mail_enrollment_keys import EnrollmentRefused, b64

DEVICE_UNAVAILABLE = 'device_identity_unavailable'
_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
_UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
_HOST = re.compile(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?')


class DeviceIdentity(Protocol):
    @property
    def device_id(self) -> str: ...
    @property
    def device_public_key(self) -> str: ...   # base64url, unpadded (converted from standard base64)
    @property
    def tunnel_uuid(self) -> str: ...         # non-secret CF tunnel id, from the attestation reply (E1)
    @property
    def route_origin(self) -> str: ...        # 'https://' + hostname

    def sign(self, message: bytes) -> bytes: ...   # Ed25519, 64 bytes


def device_key_b64url(std_b64: str) -> str:
    """Keystore `device_pub_b64` is STANDARD base64; the transcript needs strict unpadded base64url
    of the same 32 raw bytes (contract 0.3). Anything else is refused, never repaired."""
    try:
        raw = base64.b64decode(std_b64, validate=True)
    except (ValueError, TypeError, binascii.Error):
        raise EnrollmentRefused(DEVICE_UNAVAILABLE) from None
    if len(raw) != 32:
        raise EnrollmentRefused(DEVICE_UNAVAILABLE)
    return b64(raw)


@dataclass(frozen=True)
class LocalDeviceIdentity:
    """Validated values plus a signer. Construction is the validation: a bad field raises."""
    device_id: str
    device_public_key: str
    tunnel_uuid: str
    route_origin: str
    _signer: Callable[[bytes], bytes]

    def __post_init__(self) -> None:
        if (not isinstance(self.device_id, str) or not _ID.fullmatch(self.device_id)
                or not isinstance(self.tunnel_uuid, str) or not _UUID.fullmatch(self.tunnel_uuid)
                or not isinstance(self.route_origin, str) or not self.route_origin.startswith('https://')
                or not _HOST.fullmatch(self.route_origin[len('https://'):])
                or not callable(self._signer)):
            raise EnrollmentRefused(DEVICE_UNAVAILABLE)
        device_key_b64url_check(self.device_public_key)

    def sign(self, message: bytes) -> bytes:
        try:
            signature = self._signer(bytes(message))
        except Exception:
            raise EnrollmentRefused(DEVICE_UNAVAILABLE) from None
        if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
            raise EnrollmentRefused(DEVICE_UNAVAILABLE)
        return bytes(signature)


def device_key_b64url_check(value: str) -> None:
    """`value` must already be strict unpadded base64url of 32 bytes."""
    try:
        raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)) if isinstance(value, str) else b''
    except (ValueError, binascii.Error):
        raw = b''
    if len(raw) != 32 or b64(raw) != value:
        raise EnrollmentRefused(DEVICE_UNAVAILABLE)


def keystore_identity() -> DeviceIdentity:
    """The enrolled device from the OS keystore. `mc_remote` is imported HERE, lazily, so a build
    without it (or a flag-off install) never loads it."""
    try:
        from mc_remote import device_keys  # proprietary; lazy by rule (mc_remote/PROPRIETARY.md)
        identity = device_keys.load_identity()
        tunnel_uuid = device_keys.load_tunnel_uuid()
    except Exception:
        raise EnrollmentRefused(DEVICE_UNAVAILABLE) from None
    if identity is None or not tunnel_uuid:
        raise EnrollmentRefused(DEVICE_UNAVAILABLE)
    return LocalDeviceIdentity(identity.device_id, device_key_b64url(identity.device_pub_b64), tunnel_uuid,
                               'https://' + str(identity.hostname).lower(), device_keys.sign)
