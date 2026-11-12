"""Internal mail identity seal. No vault entries, HTTP export or fallback key.

Callers serialize creation with the lifecycle database's BEGIN IMMEDIATE.
The immutable file is written before the key-ready database commit. An orphan
from a failed commit is reopened, never overwritten or regenerated.
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.secrets_store import peek_passphrase_key


class EnrollmentRefused(ValueError):
    """Code-owned reason only; never log an adapter exception or secret."""


@dataclass(frozen=True)
class SealReference:
    """Public metadata only; persist this binding before any remote operation."""

    flow: str
    install: str
    generation: int
    key_id: str


@dataclass(frozen=True)
class PreparedRotation:
    """Candidate evidence, never a grant of authority or an active-key pointer."""

    current: SealReference
    candidate: SealReference
    current_public_key: str
    candidate_public_key: str


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


class IdentitySeal:
    # Pull contract 3.2 retains the version-1 seed + flow-secret format/AAD.
    # Future formats require an explicit reader; never relabel old ciphertext.
    VERSION = 1

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
        write_json_atomic(path, {'version': self.VERSION, 'nonce': b64(nonce), 'ciphertext': b64(ciphertext)})
        # Flush the rename on POSIX too. Windows replace is the platform's
        # atomic publication primitive; the file was fsynced by the writer.
        if os.name != 'nt':
            fd = os.open(self.folder, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return b64(Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw())

    def _read(self, flow: str, install: str, generation: int, key_id: str, *,
              observable: bool = False) -> bytes:
        try:
            master = self._master()
        except EnrollmentRefused as exc:
            if observable and str(exc) == 'vault_locked_or_unconfigured':
                raise EnrollmentRefused('vault_locked') from None
            raise
        try:
            value = json.loads(read_text_with_retry(self._path(flow)), object_pairs_hook=self._unique_fields)
            if (not isinstance(value, dict) or set(value) != {'version', 'nonce', 'ciphertext'}
                    or type(value['version']) is not int):
                raise ValueError()
            if value['version'] != self.VERSION:
                if observable:
                    raise EnrollmentRefused('version_unsupported')
                raise ValueError()
            nonce = self._decode(value['nonce'])
            ciphertext = self._decode(value['ciphertext'])
            if len(nonce) != 12 or len(ciphertext) != 80:
                raise ValueError()
            raw = AESGCM(master).decrypt(nonce, ciphertext, self._aad(flow, install, generation, key_id))
            if len(raw) != 64:
                raise ValueError()
            return raw
        except EnrollmentRefused:
            raise
        except FileNotFoundError:
            reason = 'seal_missing' if observable else 'identity_unavailable_reenrollment_required'
            raise EnrollmentRefused(reason) from None
        except (ValueError, KeyError, TypeError, InvalidTag):
            # Key replacement and corruption are indistinguishable deliberately.
            # No automatic identity recovery or master-key creation.
            raise EnrollmentRefused('identity_unavailable_reenrollment_required') from None
        except OSError:
            if observable:
                raise EnrollmentRefused('storage_unavailable') from None
            raise

    @staticmethod
    def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError()
        return value

    @staticmethod
    def _decode(value: object) -> bytes:
        if not isinstance(value, str):
            raise ValueError()
        raw = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
        if b64(raw) != value:
            raise ValueError()
        return raw

    @staticmethod
    def _reference(ref: SealReference) -> None:
        if (not isinstance(ref.flow, str) or not re.fullmatch('[0-9a-f]{32}', ref.flow)
                or not isinstance(ref.install, str) or not re.fullmatch('[0-9a-f]{32}', ref.install)
                or type(ref.generation) is not int or not 1 <= ref.generation <= 999_999_999_999_999
                or not isinstance(ref.key_id, str) or not re.fullmatch('[A-Za-z0-9_-]{1,128}', ref.key_id)):
            raise EnrollmentRefused('invalid_request')

    @staticmethod
    def _authorize(authorize: Callable[[], bool]) -> None:
        # This is an internal injected gate, not a request-supplied boolean or
        # proof to the service. Existing passcode/human checks remain upstream.
        try:
            allowed = authorize()
        except Exception:
            raise EnrollmentRefused('human_required') from None
        if allowed is not True:
            raise EnrollmentRefused('human_required')

    def _pull_read(self, ref: SealReference) -> bytes:
        self._reference(ref)
        return self._read(ref.flow, ref.install, ref.generation, ref.key_id, observable=True)

    def status(self, ref: SealReference) -> str:
        """Read-only local reason; never creates a key or retains signing material."""
        try:
            self._pull_read(ref)
        except EnrollmentRefused as exc:
            return str(exc)
        return 'ready'

    def sign_pull(self, ref: SealReference, transcript: bytes) -> str:
        """Return only a signature. Recheck the unlocked vault on EVERY call."""
        raw = self._pull_read(ref)
        return b64(Ed25519PrivateKey.from_private_bytes(raw[:32]).sign(transcript))

    def generate_pull(self, ref: SealReference, *, authorize: Callable[[], bool],
                      expected_public_key: str | None) -> str:
        """New human ceremony only, serialized by the caller's lifecycle transaction.

        Persist the reference in that transaction. Reopen an orphan seal after a
        failed database commit; never overwrite one. Pass None ONLY for a new
        uncommitted ceremony. Once the public key is durable, pass it on every
        retry: a lost seal then stays held rather than generating a replacement.
        """
        self._reference(ref)
        self._authorize(authorize)
        # Read first so corruption/unknown versions are not mistaken for absence.
        if expected_public_key is not None or self._path(ref.flow).exists():
            raw = self._pull_read(ref)
            public = b64(Ed25519PrivateKey.from_private_bytes(raw[:32]).public_key().public_bytes_raw())
            if expected_public_key is not None and public != expected_public_key:
                raise EnrollmentRefused('operation_conflict')
            return public
        try:
            return self.create(ref.flow, ref.install, ref.generation, ref.key_id,
                               lambda: (os.urandom(32), os.urandom(32)))
        except EnrollmentRefused as exc:
            if str(exc) == 'vault_locked_or_unconfigured':
                raise EnrollmentRefused('vault_locked') from None
            raise

    @staticmethod
    def _rotation(current: SealReference, candidate: SealReference) -> None:
        IdentitySeal._reference(current)
        IdentitySeal._reference(candidate)
        if (candidate.install != current.install or candidate.generation != current.generation + 1
                or candidate.flow == current.flow or candidate.key_id == current.key_id):
            raise EnrollmentRefused('invalid_request')

    def prepare_rotation(self, current: SealReference, candidate: SealReference, *,
                         authorize: Callable[[], bool],
                         expected_public_key: str | None) -> PreparedRotation:
        """Stage a fresh seal; old key/generation remains authoritative until commit.

        No key-loss fallback: callers must use the separately mailbox-verified
        recovery flow when current cannot be opened. Both keys can prove their
        possession while rotation is pending. No seed is cached in the result.
        """
        self._rotation(current, candidate)
        self._authorize(authorize)
        raw = self._pull_read(current)
        old = b64(Ed25519PrivateKey.from_private_bytes(raw[:32]).public_key().public_bytes_raw())
        new = self.generate_pull(candidate, authorize=lambda: True, expected_public_key=expected_public_key)
        if old == new:
            raise EnrollmentRefused('operation_conflict')
        return PreparedRotation(current, candidate, old, new)

    def commit_rotation(self, rotation: PreparedRotation, *,
                        commit: Callable[[PreparedRotation], bool]) -> None:
        """Explicit local transaction seam, called AFTER verified service evidence.

        commit must recheck durable human authorization, verified old-mailbox /
        old+new-key evidence, and CAS the current reference to candidate while
        fencing old generations/threads and clearing active consent atomically.
        It returns literal True only for that committed result (or exact replay).
        False means stale_generation; storage errors propagate. Never perform
        network I/O in this callback. Candidate files survive a failed commit.

        Seals remain immutable: retained old-flow metadata proof is required by
        contract 6.1, but grants no operational rights. The lifecycle database,
        not the ability to decrypt a seal, owns current/fenced authority.
        """
        self._rotation(rotation.current, rotation.candidate)
        for ref, expected in ((rotation.current, rotation.current_public_key),
                              (rotation.candidate, rotation.candidate_public_key)):
            raw = self._pull_read(ref)
            public = b64(Ed25519PrivateKey.from_private_bytes(raw[:32]).public_key().public_bytes_raw())
            if public != expected:
                raise EnrollmentRefused('operation_conflict')
        if rotation.current_public_key == rotation.candidate_public_key:
            raise EnrollmentRefused('operation_conflict')
        if commit(rotation) is not True:
            raise EnrollmentRefused('stale_generation')

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
