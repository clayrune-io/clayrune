"""Internal mail identity seal. No vault entries, HTTP export or fallback key.

Callers serialize creation with the lifecycle database's BEGIN IMMEDIATE.
The immutable file is written before the key-ready database commit. An orphan
from a failed commit is reopened, never overwritten or regenerated.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.secrets_store import peek_passphrase_key


class EnrollmentRefused(ValueError):
    """Code-owned reason only; never log an adapter exception or secret."""


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


class IdentitySeal:
    def __init__(self, folder: Path, *, key: Callable[[], bytes | None] = peek_passphrase_key):
        self.folder = folder
        self._key = key

    def _master(self) -> bytes:
        key = self._key()
        if key is None:
            raise EnrollmentRefused('vault_locked_or_unconfigured')
        if len(key) != 32:
            raise EnrollmentRefused('vault_key_invalid')
        return key

    def _path(self, flow: str) -> Path:
        if len(flow) != 32 or any(c not in '0123456789abcdef' for c in flow):
            raise EnrollmentRefused('invalid_flow')
        return self.folder / (flow + '.sealed')

    @staticmethod
    def _aad(flow: str, install: str, generation: int, key_id: str) -> bytes:
        return json.dumps(['clayrune-mail-identity', '1', flow, install,
                           str(generation), key_id], separators=(',', ':')).encode('ascii')

    def create(self, flow: str, install: str, generation: int, key_id: str,
               material: Callable[[], tuple[bytes, bytes]]) -> str:
        """Human ceremony only. material is an injected synthetic adapter in 7b."""
        master = self._master()
        path = self._path(flow)
        if path.exists():
            return self.public(flow, install, generation, key_id)
        try:
            seed, secret = material()
        except Exception:
            raise EnrollmentRefused('identity_material_unavailable') from None
        if not isinstance(seed, bytes) or not isinstance(secret, bytes) or len(seed) != 32 or len(secret) != 32:
            raise EnrollmentRefused('identity_material_invalid')
        aad = self._aad(flow, install, generation, key_id)
        nonce = os.urandom(12)
        ciphertext = AESGCM(master).encrypt(nonce, seed + secret, aad)
        self.folder.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, {'version': 1, 'nonce': b64(nonce), 'ciphertext': b64(ciphertext)})
        # Flush the rename on POSIX too. Windows replace is the platform's
        # atomic publication primitive; the file was fsynced by the writer.
        if os.name != 'nt':
            fd = os.open(self.folder, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return b64(Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw())

    def _read(self, flow: str, install: str, generation: int, key_id: str) -> bytes:
        master = self._master()
        try:
            value = json.loads(read_text_with_retry(self._path(flow)))
            if (not isinstance(value, dict) or set(value) != {'version', 'nonce', 'ciphertext'}
                    or type(value['version']) is not int or value['version'] != 1):
                raise ValueError()
            nonce = base64.urlsafe_b64decode(value['nonce'] + '==')
            ciphertext = base64.urlsafe_b64decode(value['ciphertext'] + '==')
            if len(nonce) != 12 or len(ciphertext) != 80:
                raise ValueError()
            raw = AESGCM(master).decrypt(nonce, ciphertext, self._aad(flow, install, generation, key_id))
            if len(raw) != 64:
                raise ValueError()
            return raw
        except (ValueError, KeyError, TypeError, InvalidTag, FileNotFoundError):
            # Key replacement and corruption are indistinguishable deliberately.
            # No automatic identity recovery or master-key creation.
            raise EnrollmentRefused('identity_unavailable_reenrollment_required') from None

    def public(self, flow: str, install: str, generation: int, key_id: str) -> str:
        raw = self._read(flow, install, generation, key_id)
        return b64(Ed25519PrivateKey.from_private_bytes(raw[:32]).public_key().public_bytes_raw())

    def flow_secret(self, flow: str, install: str, generation: int, key_id: str) -> bytes:
        """Internal enrollment adapter only, never an HTTP response."""
        return self._read(flow, install, generation, key_id)[32:]

    def prove(self, flow: str, install: str, generation: int, key_id: str,
              transcript: bytes) -> tuple[str, bytes]:
        """Internal adapter seam only: signature and flow secret, never seed."""
        raw = self._read(flow, install, generation, key_id)
        return b64(Ed25519PrivateKey.from_private_bytes(raw[:32]).sign(transcript)), raw[32:]
