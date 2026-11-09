"""Mail-only Ed25519 verification; byte contract shared with Worker signing.js."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

SKEW_MS = 300_000
ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
PATH = re.compile(r'/[A-Za-z0-9/_-]{1,200}')
HEX = re.compile(r'[a-f0-9]{64}')


class IngressRefused(ValueError):
    """Closed reason code only: never include signed input in exceptions/logs."""


def unbase64url(value: str, size: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise IngressRefused('bad_signature')
    try:
        raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    except ValueError:
        raise IngressRefused('bad_signature') from None
    if len(raw) != size or base64.urlsafe_b64encode(raw).decode().rstrip('=') != value:
        raise IngressRefused('bad_signature')
    return raw


@dataclass(frozen=True)
class ServicePin:
    install_id: str
    generation: int
    key_id: str
    public_key: str
    route_path: str

    def validate(self) -> None:
        if (not ID.fullmatch(self.install_id) or not ID.fullmatch(self.key_id)
                or type(self.generation) is not int or not 1 <= self.generation < 10**15
                or not PATH.fullmatch(self.route_path)):
            raise IngressRefused('invalid_pin')
        unbase64url(self.public_key, 32)


@dataclass(frozen=True)
class VerifiedRequest:
    pin: ServicePin
    timestamp: int
    nonce: str

    def check_fresh(self, clock_ms: Callable[[], int]) -> None:
        if abs(clock_ms() - self.timestamp) > SKEW_MS:
            raise IngressRefused('stale_signature')


def verify(headers: Mapping[str, str], body: bytes, pin: ServicePin, *,
           method: str, path: str, clock_ms: Callable[[], int]) -> VerifiedRequest:
    pin.validate()
    fields = ['version', 'direction', 'key-id', 'install-id', 'generation',
              'timestamp', 'nonce', 'digest']
    version, direction, key, install, gen, stamp, nonce, digest = [
        headers.get('x-mail-' + f, '') for f in fields]
    if (version != '1' or direction != 'service-to-install' or key != pin.key_id
            or install != pin.install_id or gen != str(pin.generation)
            or method != 'POST' or path != pin.route_path or not PATH.fullmatch(path)
            or not re.fullmatch(r'[1-9][0-9]{12}', stamp)
            or not re.fullmatch(r'[a-f0-9]{32}', nonce) or not HEX.fullmatch(digest)):
        raise IngressRefused('bad_signature_fields')
    verified = VerifiedRequest(pin, int(stamp), nonce)
    verified.check_fresh(clock_ms)
    if hashlib.sha256(body).hexdigest() != digest:
        raise IngressRefused('bad_digest')
    canonical = json.dumps(['clayrune-agent-mail', version, direction, key, install, gen,
                            method, path, stamp, nonce, digest], separators=(',', ':')).encode()
    try:
        Ed25519PublicKey.from_public_bytes(unbase64url(pin.public_key, 32)).verify(
            unbase64url(headers.get('x-mail-signature', ''), 64), canonical)
    except InvalidSignature:
        raise IngressRefused('bad_signature') from None
    verified.check_fresh(clock_ms)
    return verified
