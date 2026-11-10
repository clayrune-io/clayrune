"""Delivery-capability signing key: a purpose key of its own (MC-1166 7d-2b, contract 1.1).

Modeled on `mail_grant_signing.py`. P-256 only, kid `mail-delivery-*`, purpose
`clayrune-mail-delivery`, never the enrollment-grant key and never a session key.
The keyring is built from an explicitly supplied JSON string
(`CLAYRUNE_MAIL_DELIVERY_KEYS`); there is NO ephemeral fallback and NO key
generation here. Generating and provisioning a real key is a Ron-gated step.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from .mail_enrollment_transcript import b64u

PURPOSE = "clayrune-mail-delivery"
TOKEN_TYPE = PURPOSE + "+jwt"
KID_PATTERN = r"mail-delivery-[A-Za-z0-9_-]{1,100}"
TOKEN_LIFETIME_S = 60


class DeliveryKeyError(ValueError):
    """Safe constant reason; never carries key material."""


@dataclass(frozen=True)
class MailDeliveryKey:
    kid: str
    private_key: ec.EllipticCurvePrivateKey
    purpose: str

    def __post_init__(self):
        if (self.purpose != PURPOSE or not re.fullmatch(KID_PATTERN, self.kid)
                or not isinstance(self.private_key, ec.EllipticCurvePrivateKey)
                or not isinstance(self.private_key.curve, ec.SECP256R1)):
            raise DeliveryKeyError("mail_delivery_key_required")

    def public_jwk(self) -> dict:
        p = self.private_key.public_key().public_numbers()
        return {"kid": self.kid, "kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig",
                "x": b64u(p.x.to_bytes(32, "big")), "y": b64u(p.y.to_bytes(32, "big"))}

    def sign(self, claims: dict) -> str:
        if claims.get("aud") != PURPOSE or claims.get("token_use") != PURPOSE:
            raise DeliveryKeyError("wrong_signing_purpose")
        header = {"alg": "ES256", "typ": TOKEN_TYPE, "kid": self.kid}
        encode = lambda v: b64u(json.dumps(v, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        data = f"{encode(header)}.{encode(claims)}"
        der = self.private_key.sign(data.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return f"{data}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


@dataclass(frozen=True)
class DeliveryKeyring:
    """Every key is published; the first `active` one signs (jwt_es256 rotation discipline)."""
    entries: tuple[tuple[MailDeliveryKey, bool], ...]

    def __post_init__(self):
        kids = [k.kid for k, _ in self.entries]
        if not self.entries or len(set(kids)) != len(kids) or not any(a for _, a in self.entries):
            raise DeliveryKeyError("mail_delivery_keyring_invalid")

    def signing_key(self) -> MailDeliveryKey:
        return next(k for k, active in self.entries if active)

    def jwks(self) -> dict:
        return {"keys": [k.public_jwk() for k, _ in self.entries]}


def parse_delivery_keys(raw: str | None) -> DeliveryKeyring:
    """Build a keyring from `[{"kid","pem","active"}]`. Missing/empty config refuses.

    There is deliberately no default: an unset secret means delivery is not
    configured, never "generate one so it works".
    """
    if not isinstance(raw, str) or not raw.strip():
        raise DeliveryKeyError("mail_delivery_keys_not_configured")
    try:
        entries = json.loads(raw)
    except ValueError:
        raise DeliveryKeyError("mail_delivery_keys_invalid") from None
    if not isinstance(entries, list) or not entries:
        raise DeliveryKeyError("mail_delivery_keys_invalid")
    out = []
    for e in entries:
        if (not isinstance(e, dict) or set(e) - {"kid", "pem", "active"}
                or not isinstance(e.get("kid"), str) or not isinstance(e.get("pem"), str)):
            raise DeliveryKeyError("mail_delivery_keys_invalid")
        try:
            key = serialization.load_pem_private_key(e["pem"].encode("utf-8"), password=None)
        except (ValueError, TypeError):
            raise DeliveryKeyError("mail_delivery_keys_invalid") from None
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise DeliveryKeyError("mail_delivery_keys_invalid")
        out.append((MailDeliveryKey(e["kid"], key, PURPOSE), bool(e.get("active", True))))
    return DeliveryKeyring(tuple(out))
