"""A software WebAuthn authenticator for the passkey unit tests.

Builds the byte-level `navigator.credentials.create()` result (authenticator
data, a "none" attestation object, client data JSON) with a real P-256 key, so
the server's verification runs against genuine structures. It is a protocol
fixture, not an authenticator trial: it proves the server accepts and refuses the
right bytes, and claims nothing about a real device, a biometric, or the browser.
Slice 3 of docs/PASSKEYS_SPEC.md is where real authenticators get tried.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

FLAG_UP, FLAG_UV, FLAG_BE, FLAG_BS, FLAG_AT = 0x01, 0x04, 0x08, 0x10, 0x40


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


class FakeAuthenticator:
    def __init__(self, credential_id: bytes | None = None):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = credential_id or os.urandom(32)

    def _cose_key(self) -> bytes:
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1,
                            -2: nums.x.to_bytes(32, 'big'),
                            -3: nums.y.to_bytes(32, 'big')})

    def create(self, options: dict, origin: str, *, rp_id: str | None = None,
               uv: bool = True, up: bool = True, backup_eligible: bool = False,
               backup_state: bool = False, cross_origin: bool = False,
               top_origin: str | None = None, ceremony_type: str = 'webauthn.create',
               challenge: str | None = None, sign_count: int = 0,
               transports: list | None = None) -> dict:
        rp = rp_id if rp_id is not None else options['rp']['id']
        flags = FLAG_AT
        flags |= FLAG_UP if up else 0
        flags |= FLAG_UV if uv else 0
        flags |= FLAG_BE if backup_eligible else 0
        flags |= FLAG_BS if backup_state else 0
        auth_data = (hashlib.sha256(rp.encode('utf-8')).digest()
                     + bytes([flags]) + sign_count.to_bytes(4, 'big')
                     + bytes(16)                                   # aaguid
                     + len(self.credential_id).to_bytes(2, 'big')
                     + self.credential_id + self._cose_key())
        client = {'type': ceremony_type,
                  'challenge': challenge if challenge is not None else options['challenge'],
                  'origin': origin, 'crossOrigin': cross_origin}
        if top_origin:
            client['topOrigin'] = top_origin
        att = cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': auth_data})
        return {
            'id': b64url(self.credential_id), 'rawId': b64url(self.credential_id),
            'type': 'public-key', 'authenticatorAttachment': 'platform',
            'clientExtensionResults': {},
            'response': {
                'clientDataJSON': b64url(json.dumps(client).encode('utf-8')),
                'attestationObject': b64url(att),
                'transports': transports if transports is not None else ['internal'],
            },
        }

    def get(self, options: dict, origin: str, *, rp_id: str | None = None,
            uv: bool = True, up: bool = True, sign_count: int = 0,
            cross_origin: bool = False, top_origin: str | None = None,
            ceremony_type: str = 'webauthn.get', challenge: str | None = None,
            bad_signature: bool = False, key=None) -> dict:
        """The `navigator.credentials.get()` result for `options`, signed with
        this authenticator's key (or `key`, for a stranger's). `sign_count`
        defaults to 0, which is what synced credentials report."""
        rp = rp_id if rp_id is not None else options['rpId']
        flags = (FLAG_UP if up else 0) | (FLAG_UV if uv else 0)
        auth_data = (hashlib.sha256(rp.encode('utf-8')).digest()
                     + bytes([flags]) + sign_count.to_bytes(4, 'big'))
        client = {'type': ceremony_type,
                  'challenge': challenge if challenge is not None else options['challenge'],
                  'origin': origin, 'crossOrigin': cross_origin}
        if top_origin:
            client['topOrigin'] = top_origin
        client_json = json.dumps(client).encode('utf-8')
        signer = key if key is not None else self.key
        sig = signer.sign(auth_data + hashlib.sha256(client_json).digest(),
                          ec.ECDSA(hashes.SHA256()))
        if bad_signature:
            sig = sig[:-1] + bytes([sig[-1] ^ 0xFF])
        return {
            'id': b64url(self.credential_id), 'rawId': b64url(self.credential_id),
            'type': 'public-key', 'authenticatorAttachment': 'platform',
            'clientExtensionResults': {},
            'response': {
                'clientDataJSON': b64url(client_json),
                'authenticatorData': b64url(auth_data),
                'signature': b64url(sig),
            },
        }
