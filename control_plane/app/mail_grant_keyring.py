"""Enrollment-grant keyring (MC-1166 7d-2e-1, contract section 1.1).

Modeled on `mail_delivery_signing.DeliveryKeyring`. The keyring is built from an
explicitly supplied JSON string (`CLAYRUNE_MAIL_GRANT_KEYS`); there is NO ephemeral
fallback and NO key generation here. Generating and provisioning a real key is a
Ron-gated step. The set is the grant key's own: it shares nothing with the delivery
keys (`CLAYRUNE_MAIL_DELIVERY_KEYS`) or the session keys.

Rotation (7d-2a 1.1): add the new key unsigned (`active: false`), wait one 10-minute
JWKS cache TTL, flip `active`, and keep the old key published for one token lifetime
plus skew before removing it from the config.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .mail_grant_signing import MailGrantKey, PURPOSE


class GrantKeyError(ValueError):
    """Safe constant reason; never carries key material."""


@dataclass(frozen=True)
class MailGrantKeyring:
    """Every key is published; exactly one `active` key signs."""
    entries: tuple[tuple[MailGrantKey, bool], ...]

    def __post_init__(self):
        kids = [k.kid for k, _ in self.entries]
        if (not self.entries or len(set(kids)) != len(kids)
                or sum(1 for _, active in self.entries if active) != 1):
            raise GrantKeyError("mail_grant_keyring_invalid")

    def signing_key(self) -> MailGrantKey:
        return next(k for k, active in self.entries if active)

    def jwks(self) -> dict:
        return {"keys": [k.public_jwk() for k, _ in self.entries]}


def _key(kid: str, private_key) -> MailGrantKey:
    try:
        return MailGrantKey(kid, private_key, PURPOSE)
    except ValueError:
        raise GrantKeyError("mail_grant_keys_invalid") from None


def parse_grant_keys(raw: str | None) -> MailGrantKeyring:
    """Build a keyring from `[{"kid","pem","active"}]`. Missing/empty config refuses.

    There is deliberately no default: an unset secret means enrollment grants are
    not configured, never "generate one so it works".
    """
    if not isinstance(raw, str) or not raw.strip():
        raise GrantKeyError("mail_grant_keys_not_configured")
    try:
        entries = json.loads(raw)
    except ValueError:
        raise GrantKeyError("mail_grant_keys_invalid") from None
    if not isinstance(entries, list) or not entries:
        raise GrantKeyError("mail_grant_keys_invalid")
    out = []
    for e in entries:
        if (not isinstance(e, dict) or set(e) - {"kid", "pem", "active"}
                or not isinstance(e.get("kid"), str) or not isinstance(e.get("pem"), str)
                or not isinstance(e.get("active", True), bool)):
            raise GrantKeyError("mail_grant_keys_invalid")
        try:
            private = serialization.load_pem_private_key(e["pem"].encode("utf-8"), password=None)
        except (ValueError, TypeError):
            raise GrantKeyError("mail_grant_keys_invalid") from None
        if not isinstance(private, ec.EllipticCurvePrivateKey):
            raise GrantKeyError("mail_grant_keys_invalid")
        out.append((_key(e["kid"], private), e.get("active", True)))
    return MailGrantKeyring(tuple(out))
