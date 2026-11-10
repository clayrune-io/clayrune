"""Python-signed golden vectors for the JS delivery-capability verifier (7d-2b, the cross-language gate).

`build_vectors()` drives the real MailDeliveryAuthority, then derives mutated tokens (re-signed so the
signature is valid and only the mutated claim is wrong) and request mismatches. Keys are generated per
run and the private half is discarded, so the committed JSON holds public material only; tokens in it
are checked against the fixed clock stored beside them. Regenerate with:

    python -m control_plane.tests.delivery_vectors workers/agent-mail/test/fixtures/delivery-vectors.json
"""
from __future__ import annotations

import base64
import json
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from control_plane.app.mail_enrollment_transcript import b64u
from control_plane.tests.mail_delivery_support import INSTALL, ISSUER, World


def _mem():
    from control_plane.tests.conftest import MemoryFirestore
    return MemoryFirestore()


def _sign_raw(private_key, header: dict, claims: dict) -> str:
    enc = lambda v: b64u(json.dumps(v, separators=(",", ":"), sort_keys=True).encode())
    data = f"{enc(header)}.{enc(claims)}"
    r, s = decode_dss_signature(private_key.sign(data.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    return f"{data}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def _parts(token: str):
    h, c, s = token.split(".")
    dec = lambda p: json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))
    return dec(h), dec(c), s


def build_vectors(mem_firestore=None) -> dict:
    w = World(mem_firestore or _mem())
    w.bind()
    nonce, digest = "ab" * 16, "cd" * 32
    mail = w.issue(w.issue_body(mode="mail", mail_nonce=nonce, req_digest=digest))["capability"]
    proof = w.issue(w.issue_body(mode="proof", mail_nonce=nonce, req_digest=digest))["capability"]
    key = w.delivery
    header, claims, _ = _parts(mail)
    now = claims["iat"] * 1000 + 500
    base_req = {"host": "device.example.com", "method": "POST", "path": "/api/agent-mail/ingress",
                "headers": {"install_id": INSTALL, "generation": "1", "nonce": nonce, "digest": digest,
                            "key_id": "mail1", "direction": "service-to-install"}}
    out: list = []

    def add(name, token, expect, request=None, now_ms=now):
        out.append({"name": name, "token": token, "expect": expect, "request": request or base_req, "now_ms": now_ms})

    def with_req(**over):
        r = json.loads(json.dumps(base_req))
        for k, v in over.items():
            if k == "headers":
                r["headers"].update(v)
            else:
                r[k] = v
        return r

    def mutate(name, expect, now_ms=now, **over):
        c = dict(claims)
        for k, v in over.items():
            if v is None:
                c.pop(k, None)
            else:
                c[k] = v
        add(name, _sign_raw(key.private_key, header, c), expect, now_ms=now_ms)

    add("valid mail capability", mail, "ok")
    add("valid proof capability", proof, "ok", with_req(headers={"direction": "service-to-install-proof"}))
    # Request mismatches: same valid token, wrong request.
    for name, req in {
        "wrong host": with_req(host="evil.example.com"), "wrong method": with_req(method="PUT"),
        "wrong path": with_req(path="/api/agent-mail/other"), "query smuggled in path": with_req(path="/api/agent-mail/ingress?x=1"),
        "encoded path alias": with_req(path="/api/agent-mail/%69ngress"),
        "wrong digest": with_req(headers={"digest": "00" * 32}), "wrong nonce": with_req(headers={"nonce": "00" * 16}),
        "wrong generation": with_req(headers={"generation": "2"}), "wrong install": with_req(headers={"install_id": "other"}),
        "wrong key id": with_req(headers={"key_id": "mail2"}),
        "mail token on proof direction": with_req(headers={"direction": "service-to-install-proof"}),
        "unknown direction": with_req(headers={"direction": "install-to-service"}),
    }.items():
        add(name, mail, "capability_request_mismatch", req)
    add("proof token on mail direction", proof, "capability_request_mismatch")
    # Claim mutations (valid signature, one wrong claim).
    mutate("wrong issuer", "bad_capability_claims", iss="https://evil.example.com")
    mutate("wrong aud", "bad_capability_claims", aud="clayrune-dashboard")
    mutate("wrong token_use", "bad_capability_claims", token_use="access")
    mutate("61 s lifetime", "bad_capability_claims", exp=claims["iat"] + 61)
    mutate("exp equals iat", "bad_capability_claims", exp=claims["iat"])
    mutate("iat != nbf", "bad_capability_claims", nbf=claims["iat"] - 1)
    mutate("bad mode", "bad_capability_claims", mode="admin")
    mutate("wrong route_path", "bad_capability_claims", route_path="/api/agent-mail/other")
    mutate("wrong method claim", "bad_capability_claims", method="GET")
    mutate("generation zero", "bad_capability_claims", generation="0")
    mutate("route_origin not https", "bad_capability_claims", route_origin="http://device.example.com")
    mutate("bad sub", "bad_capability_claims", sub="bad sub!")
    mutate("short jti", "bad_capability_claims", jti="abc")
    mutate("string timestamp", "bad_capability_claims", exp=str(claims["exp"]))
    mutate("checked_at far from iat", "bad_capability_claims", checked_at_ms=claims["checked_at_ms"] - 2000)
    mutate("missing claim", "bad_capability_claims", device_id=None)
    mutate("extra claim", "bad_capability_claims", admin=True)
    mutate("mismatched device-bound host", "capability_request_mismatch", route_origin="https://other.example.com")
    add("issued in the future", mail, "capability_not_yet_valid", now_ms=claims["iat"] * 1000 - 1)
    add("expired", mail, "capability_expired", now_ms=claims["exp"] * 1000)
    add("one ms before expiry", mail, "ok", now_ms=claims["exp"] * 1000 - 1)
    # Signature / header refusals.
    h, c, s = mail.split(".")
    other = World(mem_firestore or _mem()).delivery  # same kid, different key material
    add("signature bit flipped", f"{h}.{c}.{'A' if s[0] != 'A' else 'B'}{s[1:]}", "bad_capability_signature")
    add("signed by other key with same kid", _sign_raw(other.private_key, header, claims), "bad_capability_signature")
    add("unknown kid", _sign_raw(key.private_key, {**header, "kid": "mail-delivery-unknown"}, claims), "unknown_capability_key")
    add("grant-style kid", _sign_raw(key.private_key, {**header, "kid": "mail-grant-test"}, claims), "bad_capability_header")
    add("session-style kid", _sign_raw(key.private_key, {**header, "kid": "cp-2026a"}, claims), "bad_capability_header")
    add("wrong typ", _sign_raw(key.private_key, {**header, "typ": "JWT"}, claims), "bad_capability_header")
    add("enrollment grant typ", _sign_raw(key.private_key, {**header, "typ": "clayrune-mail-enrollment+jwt"}, claims), "bad_capability_header")
    add("alg none", _sign_raw(key.private_key, {**header, "alg": "none"}, claims), "bad_capability_header")
    add("alg HS256", _sign_raw(key.private_key, {**header, "alg": "HS256"}, claims), "bad_capability_header")
    add("extra header field", _sign_raw(key.private_key, {**header, "jku": "https://evil.example.com/jwks"}, claims), "bad_capability_header")
    add("two segments", f"{h}.{c}", "bad_capability")
    add("empty token", "", "bad_capability")
    add("oversize token", "x" * 5000, "bad_capability")
    add("not base64 payload", f"{h}.!!!.{s}", "bad_capability")
    return {"_meta": "Python-signed by control_plane.tests.delivery_vectors; public material only; private key discarded.",
            "issuer": ISSUER, "jwks_url": f"{ISSUER}/v1/mail-delivery/jwks", "jwks": w.keyring.jwks(), "vectors": out}


if __name__ == "__main__":
    import google.cloud.firestore as _gfs  # type: ignore
    _gfs.transactional = lambda fn: (lambda txn: fn(txn))  # the in-memory stub has no real transactions
    with open(sys.argv[1], "w", encoding="utf-8", newline="\n") as f:
        json.dump(build_vectors(), f, indent=1, sort_keys=True)
        f.write("\n")
