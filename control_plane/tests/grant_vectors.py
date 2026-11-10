"""Python-signed golden vectors for the JS enrollment-grant verifier (7d-2e-1, the cross-language gate).

`build_vectors()` drives the real MailGrantIssuer, then derives mutated tokens (re-signed so the
signature is valid and only the mutated claim is wrong) and request mismatches. Keys are generated per
run and the private half is discarded, so the committed JSON holds public material only; tokens in it
are checked against the fixed clock stored beside them. Regenerate with:

    python -m control_plane.tests.grant_vectors workers/agent-mail/test/fixtures/grant-vectors.json

The 7d-2e-2 Worker unit consumes the same file (`verifyGrant(token, r, trust, () => now_ms)`).
"""
from __future__ import annotations

import json
import sys

from cryptography.hazmat.primitives.asymmetric import ec

from control_plane.app.mail_enrollment_transcript import FIELDS
from control_plane.tests.delivery_vectors import _parts, _sign_raw
from control_plane.tests.mail_delivery_support import ISSUER, SERVICE_ORIGIN, World
from control_plane.tests.test_mail_grants import NOW


def _mem():
    from control_plane.tests.conftest import MemoryFirestore
    return MemoryFirestore()


def build_vectors(mem_firestore=None) -> dict:
    w = World(mem_firestore or _mem())
    r = list(w.r)
    token = w.grant(r)
    header, claims, _ = _parts(token)
    key = w.grant_key
    now = NOW + 500
    out: list = []

    def add(name, tok, expect, request=None, now_ms=now):
        out.append({"name": name, "token": tok, "expect": expect, "r": request or r, "now_ms": now_ms})

    def mutate(name, expect="bad_grant_claims", now_ms=now, **over):
        c = dict(claims)
        for k, v in over.items():
            if v is None:
                c.pop(k, None)
            else:
                c[k] = v
        add(name, _sign_raw(key.private_key, header, c), expect, now_ms=now_ms)

    def with_r(**fields):
        copy = list(r)
        for k, v in fields.items():
            copy[FIELDS.index(k)] = v
        return copy

    add("valid grant", token, "ok")
    add("one ms before expiry", token, "ok", now_ms=claims["exp"] * 1000 - 1)
    # Request (R) mismatches: same valid token, a different transcript.
    add("different challenge id", token, "bad_grant_claims", with_r(challenge_id="challenge2"))
    add("different install id", token, "bad_grant_claims", with_r(install_id="install2"))
    add("different expected generation", token, "bad_grant_claims", with_r(expected_generation="3"))
    add("different device id", token, "bad_grant_claims", with_r(device_id="device2"))
    add("different service origin", token, "bad_grant_claims", with_r(service_origin="https://evil.example.com"))
    add("transcript already expired", token, "bad_grant_claims", with_r(expires_ms=str(now - 1)))
    # Claim mutations (valid signature, one wrong claim).
    mutate("wrong issuer", iss="https://evil.example.com")
    mutate("wrong aud", aud="clayrune-dashboard")
    mutate("delivery aud", aud="clayrune-mail-delivery")
    mutate("wrong token_use", token_use="access")
    mutate("61 s lifetime", exp=claims["iat"] + 61)
    mutate("exp equals iat", exp=claims["iat"])
    mutate("iat != nbf", nbf=claims["iat"] - 1)
    mutate("string timestamp", exp=str(claims["exp"]))
    mutate("bad sub", sub="bad sub!")
    mutate("short record_version", record_version="abc")
    mutate("missing jti", jti=None)
    mutate("r_sha256 of another transcript", r_sha256="0" * 64)
    mutate("device claim mismatch", device_id="device2")
    mutate("tunnel claim mismatch", tunnel_uuid="87654321-4321-4321-4321-cba987654321")
    mutate("route claim mismatch", route_origin="https://other.example.com")
    mutate("install claim mismatch", install_id="install2")
    mutate("generation claim mismatch", expected_generation="9")
    mutate("checked_at after the iat second", checked_at_ms=claims["iat"] * 1000 + 1000)
    mutate("checked_at before the iat second", checked_at_ms=claims["iat"] * 1000 - 1000)
    add("issued in the future", token, "bad_grant_claims", now_ms=claims["iat"] * 1000 - 1)
    add("expired", token, "bad_grant_claims", now_ms=claims["exp"] * 1000)
    add("authority check older than the 300 s lease", token, "bad_grant_claims", now_ms=claims["checked_at_ms"] + 300_000)
    # Header / signature refusals.
    h, c, s = token.split(".")
    other = ec.generate_private_key(ec.SECP256R1())
    add("unknown kid", _sign_raw(key.private_key, {**header, "kid": "mail-grant-other"}, claims), "unknown_grant_key")
    add("delivery-style kid", _sign_raw(key.private_key, {**header, "kid": "mail-delivery-test"}, claims), "unknown_grant_key")
    add("wrong typ", _sign_raw(key.private_key, {**header, "typ": "JWT"}, claims), "bad_grant_header")
    add("delivery typ", _sign_raw(key.private_key, {**header, "typ": "clayrune-mail-delivery+jwt"}, claims), "bad_grant_header")
    add("alg none", _sign_raw(key.private_key, {**header, "alg": "none"}, claims), "bad_grant_header")
    add("alg HS256", _sign_raw(key.private_key, {**header, "alg": "HS256"}, claims), "bad_grant_header")
    add("extra header field", _sign_raw(key.private_key, {**header, "jku": "https://evil.example.com/jwks"}, claims), "bad_grant_header")
    add("signed by another key under the same kid", _sign_raw(other, header, claims), "bad_grant_signature")
    add("one flipped signature char", f"{h}.{c}.{'A' if s[0] != 'A' else 'B'}{s[1:]}", "bad_grant_signature")
    add("two segments", f"{h}.{c}", "bad_grant")
    add("empty token", "", "bad_grant")
    add("oversize token", "x" * 9000, "bad_grant")
    return {"_meta": "Python-signed by control_plane.tests.grant_vectors; public material only; private key discarded.",
            "issuer": ISSUER, "service_origin": SERVICE_ORIGIN, "jwks_url": f"{ISSUER}/v1/mail-enrollment/jwks",
            "jwks": {"keys": [key.public_jwk()]}, "vectors": out}


if __name__ == "__main__":
    import google.cloud.firestore as _gfs  # type: ignore
    _gfs.transactional = lambda fn: (lambda txn: fn(txn))  # the in-memory stub has no real transactions
    with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as f:
        json.dump(build_vectors(), f, indent=1, sort_keys=True)
        f.write("\n")
