"""Shared synthetic fixtures for the 7d-2b delivery tests. Test keys only, generated per run."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from control_plane.app.mail_delivery_authority import MailDeliveryAuthority
from control_plane.app.mail_delivery_signing import DeliveryKeyring, MailDeliveryKey, PURPOSE as DELIVERY_PURPOSE
from control_plane.app.mail_delivery_store import DeliveryStore
from control_plane.app.mail_enrollment_transcript import b64u, canonical_r
from control_plane.app.mail_grant_signing import MailGrantKey, PURPOSE as GRANT_PURPOSE
from control_plane.app.mail_grants import MailGrantIssuer
from control_plane.app.mail_service_auth import ServiceCall, ServiceKeyring, canonical_array, parse_service_keys
from control_plane.tests.test_mail_grants import NOW, TUNNEL, Reader

ROOT = Path(__file__).resolve().parents[2]
ISSUER = "https://authority.example.com"
SERVICE_ORIGIN = "https://mail.example.com"
KEY_ID = "service1"
INSTALL = "install1"


def raw_public(key) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def run(coro):
    return asyncio.run(coro)


def payload(token: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))


def delivery_key(kid: str = "mail-delivery-test") -> MailDeliveryKey:
    return MailDeliveryKey(kid, ec.generate_private_key(ec.SECP256R1()), DELIVERY_PURPOSE)


def sign_service_call(private, *, key_id=KEY_ID, install_id=INSTALL, generation="1", path, body: bytes,
                      now_ms: int, nonce: str | None = None, direction="service-to-cp") -> dict:
    """Mirror of workers/agent-mail/signing.js `sign`: the headers a Worker would send."""
    nonce = nonce or secrets.token_hex(16)
    digest = hashlib.sha256(body).hexdigest()
    sig = private.sign(canonical_array({"version": "1", "direction": direction, "key_id": key_id,
                                        "install_id": install_id, "generation": generation, "method": "POST",
                                        "path": path, "timestamp": str(now_ms), "nonce": nonce, "digest": digest}))
    return {"content-type": "application/json", "x-mail-version": "1", "x-mail-direction": direction,
            "x-mail-key-id": key_id, "x-mail-install-id": install_id, "x-mail-generation": generation,
            "x-mail-timestamp": str(now_ms), "x-mail-nonce": nonce, "x-mail-digest": digest,
            "x-mail-signature": b64u(sig)}


def run_node(script: str, data: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(data), text=True,
                          capture_output=True, cwd=ROOT, timeout=30)


class World:
    """A bound-able install: grant key, service key, authority reader, store, authority."""

    def __init__(self, mem_firestore):
        self.service_private = ed25519.Ed25519PrivateKey.generate()
        self.service_public = raw_public(self.service_private)
        self.service_keys = ServiceKeyring({KEY_ID: self.service_public})
        self.device = ed25519.Ed25519PrivateKey.generate()
        self.mail = ed25519.Ed25519PrivateKey.generate()
        self.reader = Reader(raw_public(self.device))
        self.grant_key = MailGrantKey("mail-grant-test", ec.generate_private_key(ec.SECP256R1()), GRANT_PURPOSE)
        self.delivery = delivery_key()
        self.keyring = DeliveryKeyring(((self.delivery, True),))
        self.t = [NOW]
        self.store = DeliveryStore(db=lambda: mem_firestore)
        self.authority = MailDeliveryAuthority(
            reader=self.reader, keyring=self.keyring, store=self.store, issuer=ISSUER,
            service_keys=self.service_keys, grant_jwks=[self.grant_key.public_jwk()], clock=lambda: self.t[0])
        self.r = ["clayrune-mail-enrollment", "1", SERVICE_ORIGIN, "flow1", "challenge1", "a" * 64,
                  str(NOW + 900_000), INSTALL, "0", "device1", b64u(raw_public(self.device)), "mail1",
                  b64u(raw_public(self.mail)), "b" * 64, TUNNEL, "https://device.example.com",
                  "/api/agent-mail/ingress", "1", KEY_ID, hashlib.sha256(self.service_public).hexdigest()]

    def grant(self, r=None) -> str:
        r = r or self.r
        raw = canonical_r(r)
        issuer = MailGrantIssuer(reader=self.reader, key=self.grant_key, issuer=ISSUER,
                                 service_origin=SERVICE_ORIGIN, enabled=True, clock=lambda: self.t[0])
        return run(issuer.issue(r, device_signature=b64u(self.device.sign(raw)),
                                mail_signature=b64u(self.mail.sign(raw))))

    def call(self, *, generation="1", install=INSTALL, key_id=KEY_ID, path="/v1/mail-delivery/issue",
             digest="d" * 64) -> ServiceCall:
        return ServiceCall(key_id, install, generation, secrets.token_hex(16), self.t[0], digest, path)

    def bind(self, *, advance_ms=1000, **kw):
        token = self.grant(kw.pop("r", None))
        self.t[0] += advance_ms
        return self.authority.bind(self.call(path="/v1/mail-delivery/bind", **kw),
                                   {"grant": token, "r": self.r})

    def issue_body(self, **over) -> dict:
        body = {"install_id": INSTALL, "generation": "1", "mode": "mail", "req_digest": "e" * 64,
                "mail_nonce": secrets.token_hex(16), "mail_key_id": "mail1"}
        body.update(over)
        return body

    def issue(self, body=None, **callkw):
        body = body or self.issue_body()
        return run(self.authority.issue(self.call(**callkw), body))
