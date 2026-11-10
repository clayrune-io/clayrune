"""Explicit mail-purpose ES256 key. No env fallback, key generation or JWKS route."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from .mail_enrollment_transcript import b64u

PURPOSE = "clayrune-mail-enrollment"
TOKEN_TYPE = PURPOSE + "+jwt"


@dataclass(frozen=True)
class MailGrantKey:
    kid: str
    private_key: ec.EllipticCurvePrivateKey
    purpose: str

    def __post_init__(self):
        if (self.purpose != PURPOSE or not re.fullmatch(r"mail-grant-[A-Za-z0-9_-]{1,100}", self.kid)
                or not isinstance(self.private_key, ec.EllipticCurvePrivateKey)
                or not isinstance(self.private_key.curve, ec.SECP256R1)):
            raise ValueError("mail_grant_key_required")

    def public_jwk(self) -> dict:
        p = self.private_key.public_key().public_numbers()
        return {"kid": self.kid, "kty": "EC", "crv": "P-256", "alg": "ES256", "use": "sig",
                "x": b64u(p.x.to_bytes(32, "big")), "y": b64u(p.y.to_bytes(32, "big"))}

    def sign(self, claims: dict) -> str:
        if claims.get("aud") != PURPOSE or claims.get("token_use") != PURPOSE:
            raise ValueError("wrong_signing_purpose")
        header = {"alg": "ES256", "typ": TOKEN_TYPE, "kid": self.kid}
        encode = lambda v: b64u(json.dumps(v, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        data = f"{encode(header)}.{encode(claims)}"
        der = self.private_key.sign(data.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return f"{data}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
