"""Proof of machine custody on the fixed ingress route (MC-1166 unit 7d-2c, contract section 4).

Arm / receive / invalidate / projection. The proof path can never create mail: no event, thread,
wake, pin, consent or registration write happens here. The only authority it produces is the
`machine_custody` projection that `Enrollment.enable()` reads, and only when BOTH the local proof
row and a signature-verified Worker status agree (4.6). No network client, key generator, unlock
or startup worker lives here; the Worker status reader is injected (its real adapter is 7d-2d).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mc.agent_mail_delivery_schema import initialize
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal
from mc.agent_mail_enrollment_manifest import candidate_from_evidence
from mc.agent_mail_enrollment_state import ManifestCAS
from mc.agent_mail_envelope import MAX_REQUEST_BYTES
from mc.agent_mail_service_manifest import ServiceKeyCandidate
from mc.agent_mail_signing import (HEX, ID, IngressRefused, SKEW_MS, ServicePin, VerifiedRequest,
                                   unbase64url)
from mc.agent_mail_store import MailStore

ROUTE = '/api/agent-mail/ingress'
PROOF_DIRECTION = 'service-to-install-proof'
RECEIPT_PURPOSE = 'clayrune-agent-mail-proof'
PROOF_TTL_MS = 600_000
BODY_KEYS = frozenset({'kind', 'version', 'challenge', 'install_id', 'generation', 'manifest_sequence',
                       'service_key_id', 'service_public_key_sha256', 'mail_key_id', 'route_origin',
                       'owner_address_sha256', 'service_nonce'})
EVIDENCE_KEYS = frozenset({'install_id', 'generation', 'state', 'receipt_digest', 'expires_ms'})
_ORIGIN = re.compile(r'https://(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?')
_NONCE = re.compile(r'[a-f0-9]{32}')
_STAGES = frozenset({'delivery_pending', 'active'})


def receipt_array(install: str, generation: int, challenge: str, service_nonce: str, route_origin: str,
                  manifest_sequence: int, proof_id: str) -> list[str]:
    """Every element an ASCII string: json.dumps(separators=(',', ':')) equals JS JSON.stringify."""
    return [RECEIPT_PURPOSE, '1', install, str(generation), challenge, service_nonce, route_origin,
            str(manifest_sequence), proof_id]


def receipt_bytes(*args: Any) -> bytes:
    return json.dumps(receipt_array(*args), separators=(',', ':')).encode('ascii')


def verify_proof(headers: Mapping[str, str], body: bytes, pin: ServicePin, *, method: str, path: str,
                 clock_ms: Callable[[], int]) -> VerifiedRequest:
    """Ed25519 verification in the proof signature domain.

    The ordinary `agent_mail_signing.verify` is deliberately not edited: it hard-refuses every
    direction but `service-to-install`, so a proof request can never be ingested as mail.
    """
    pin.validate()
    fields = ['version', 'direction', 'key-id', 'install-id', 'generation', 'timestamp', 'nonce', 'digest']
    version, direction, key, install, gen, stamp, nonce, digest = [headers.get('x-mail-' + f, '') for f in fields]
    if (version != '1' or direction != PROOF_DIRECTION or key != pin.key_id or install != pin.install_id
            or gen != str(pin.generation) or method != 'POST' or path != pin.route_path
            or not re.fullmatch(r'[1-9][0-9]{12}', stamp) or not _NONCE.fullmatch(nonce)
            or not HEX.fullmatch(digest)):
        raise IngressRefused('bad_signature_fields')
    verified = VerifiedRequest(pin, int(stamp), nonce)
    verified.check_fresh(clock_ms)
    if hashlib.sha256(body).hexdigest() != digest:
        raise IngressRefused('bad_digest')
    canonical = json.dumps(['clayrune-agent-mail', version, direction, key, install, gen, method, path,
                            stamp, nonce, digest], separators=(',', ':')).encode()
    try:
        Ed25519PublicKey.from_public_bytes(unbase64url(pin.public_key, 32)).verify(
            unbase64url(headers.get('x-mail-signature', ''), 64), canonical)
    except InvalidSignature:
        raise IngressRefused('bad_signature') from None
    verified.check_fresh(clock_ms)
    return verified


class DeliveryProof:
    """`worker_evidence` must return the LAST signature-verified Worker status for this install
    (a cached read, never a fetch) or None. An unsigned status, HTTP 200, edge health page, login
    HTML or redirect is not evidence and must never be returned from it."""

    def __init__(self, store: MailStore, seal: IdentitySeal, *, root_public_key: str, service_origin: str,
                 route_origin: Callable[[], str | None],
                 worker_evidence: Callable[[], Mapping[str, Any] | None] = lambda: None,
                 token: Callable[[int], str] = secrets.token_hex, enabled: bool = False):
        self.store, self.seal, self.root, self.origin = store, seal, root_public_key, service_origin
        self.route_origin, self.worker_evidence, self.token = route_origin, worker_evidence, token
        self.enabled = enabled
        # Disabled construction is side-effect free.
        if enabled:
            initialize(store)
        self.manifests = ManifestCAS(store)

    def _enabled(self) -> None:
        if self.enabled is not True:
            raise EnrollmentRefused('delivery_proof_disabled')

    def _now_ms(self) -> int:
        return int(self.store.clock() * 1000)

    @staticmethod
    def _flow(db: sqlite3.Connection) -> dict | None:
        row = db.execute('SELECT * FROM mail_enrollment_flow WHERE slot=1').fetchone()
        return dict(row) if row else None

    @staticmethod
    def _proof(db: sqlite3.Connection, install: str, generation: int) -> dict | None:
        row = db.execute('SELECT * FROM mail_delivery_proof WHERE install=? AND generation=?',
                         (install, generation)).fetchone()
        return dict(row) if row else None

    def _candidate(self, flow: dict) -> ServiceKeyCandidate:
        return candidate_from_evidence(flow, root=self.root, origin=self.origin)

    def _fresh(self, flow: dict) -> ServiceKeyCandidate:
        # Same semantics as Enrollment._fresh_candidate: the CURRENT floor and an unexpired manifest.
        candidate = self._candidate(flow)
        floor = self.manifests.load(candidate.origin)
        if (floor is None or floor.sequence != candidate.sequence or floor.digest != flow['manifest_digest']
                or self._now_ms() >= candidate.expires_at_ms):
            raise EnrollmentRefused('manifest_stale')
        return candidate

    # --- arm (called by Enrollment.arm_proof inside its transaction) ----------------------------
    def arm_in_tx(self, db: sqlite3.Connection, flow: dict, candidate: ServiceKeyCandidate) -> dict:
        self._enabled()
        route = self.route_origin()
        if not isinstance(route, str) or not _ORIGIN.fullmatch(route):
            raise EnrollmentRefused('route_origin_unavailable')
        now = self._now_ms()
        if now >= flow['ownership_expires_ms']:
            raise EnrollmentRefused('ownership_expired')
        old = self._proof(db, flow['install'], flow['generation'])
        if old and old['state'] == 'consumed':
            raise EnrollmentRefused('proof_consumed')
        expires = min(now + PROOF_TTL_MS, flow['ownership_expires_ms'])
        proof_id = self.token(16)
        # Replacing an armed row invalidates it: the PK holds one row per install+generation.
        db.execute('INSERT OR REPLACE INTO mail_delivery_proof (install,generation,flow,epoch,challenge,proof_id,'
                   'manifest_sequence,manifest_digest,service_key_id,service_public_key_sha256,mail_key_id,'
                   'route_origin,owner_address_sha256,ownership_expires_ms,armed_ms,expires_ms,state,receipt_digest) '
                   "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'armed','')",
                   (flow['install'], flow['generation'], flow['flow'], flow['epoch'], self.token(32), proof_id,
                    candidate.sequence, flow['manifest_digest'], candidate.key_id, candidate.fingerprint,
                    flow['key_id'], route, hashlib.sha256(flow['owner'].encode()).hexdigest(),
                    flow['ownership_expires_ms'], now, expires))
        return {'state': 'armed', 'proof_id': proof_id, 'expires_ms': expires}

    def armed(self, install: str, generation: int) -> dict | None:
        """What the 7d-2d lifecycle client sends to the Worker's proof-arm (challenge included)."""
        self._enabled()
        with self.store._read() as db:
            row = self._proof(db, install, generation)
        return row if row and row['state'] == 'armed' and self._now_ms() < row['expires_ms'] else None

    # --- invalidate (inside the transaction that causes it) --------------------------------------
    @staticmethod
    def invalidate_in_tx(db: sqlite3.Connection, install: str) -> None:
        db.execute("UPDATE mail_delivery_proof SET state='invalidated' WHERE install=? AND state IN ('armed','proved')",
                   (install,))

    # --- receive ---------------------------------------------------------------------------------
    def receive_proof(self, headers: Mapping[str, str], raw: bytes, *, method: str = 'POST',
                      path: str = ROUTE) -> dict[str, Any]:
        """Validate a proof request and commit the receipt. Raises IngressRefused (closed codes) or
        EnrollmentRefused('vault_locked_or_unconfigured') when the receipt cannot be signed."""
        self._enabled()
        if len(raw) > MAX_REQUEST_BYTES:
            raise IngressRefused('body_limit')
        install = headers.get('x-mail-install-id', '')
        if not ID.fullmatch(install):
            raise IngressRefused('bad_signature_fields')
        try:
            return self._receive(install, headers, raw, method, path)
        except EnrollmentRefused as exc:
            if str(exc) == 'vault_locked_or_unconfigured':
                raise
            # Evidence/manifest/identity problems are refusals of THIS request, in the route's one class.
            raise IngressRefused(str(exc)) from None

    def _receive(self, install: str, headers: Mapping[str, str], raw: bytes, method: str, path: str) -> dict:
        with self.store._read() as db:
            flow = self._flow(db)
            armed = self._proof(db, install, flow['generation']) if flow else None
        if not flow or flow['install'] != install or flow['stage'] not in _STAGES or armed is None:
            raise IngressRefused('proof_not_armed')
        self._check_armed(flow, armed)
        candidate = self._fresh(flow)
        pin = ServicePin(install, flow['generation'], candidate.key_id, candidate.public_key, ROUTE)
        signed = verify_proof(headers, raw, pin, method=method, path=path,
                              clock_ms=self._now_ms)
        body = self._body(raw, armed)
        if self.seal.public(flow['flow'], flow['seal_identity'], flow['seal_generation'],
                            flow['key_id']) != flow['public_key']:
            raise IngressRefused('identity_mismatch')
        signed_bytes = receipt_bytes(install, flow['generation'], armed['challenge'], body['service_nonce'],
                                     armed['route_origin'], armed['manifest_sequence'], armed['proof_id'])
        # Sign BEFORE any write: a locked vault answers 503 and commits nothing (never auto-unlock).
        signature = self.seal.prove(flow['flow'], flow['seal_identity'], flow['seal_generation'],
                                    flow['key_id'], signed_bytes)[0]
        digest = hashlib.sha256(signed_bytes).hexdigest()
        with self.store._tx() as db:
            now_ms = self._now_ms()
            current = self._flow(db)
            row = self._proof(db, install, flow['generation'])
            if (not current or current['flow'] != flow['flow'] or current['epoch'] != flow['epoch']
                    or current['stage'] not in _STAGES or current['manifest_digest'] != flow['manifest_digest']
                    or current['install'] != install or current['generation'] != flow['generation']
                    or row is None):
                raise IngressRefused('lifecycle_raced')
            self._check_armed(current, row)
            self._fresh(current)
            db.execute('DELETE FROM mail_ingress_nonces WHERE expires_at<=?', (now_ms,))
            inserted = db.execute('INSERT OR IGNORE INTO mail_ingress_nonces VALUES(?,?,?,?,?,?)',
                                  (PROOF_DIRECTION, install, pin.generation, pin.key_id, signed.nonce,
                                   signed.timestamp + SKEW_MS + 1))
            if inserted.rowcount != 1:
                raise IngressRefused('replayed_nonce')
            signed.check_fresh(self._now_ms)
            # `proved` re-signs over THIS request's fresh service_nonce: a retry after a lost response
            # converges on the latest receipt, and Worker/local digests agree once a retry lands.
            db.execute("UPDATE mail_delivery_proof SET state='proved',receipt_digest=? WHERE install=? "
                       'AND generation=? AND flow=? AND epoch=?', (digest, install, flow['generation'],
                                                                   flow['flow'], flow['epoch']))
        signed.check_fresh(self._now_ms)
        return {'custody': 'proof', 'install_id': install, 'generation': flow['generation'],
                'challenge': armed['challenge'], 'proof_id': armed['proof_id'], 'signature': signature}

    def _check_armed(self, flow: dict, proof: dict) -> None:
        candidate = self._candidate(flow)
        if (proof['state'] not in ('armed', 'proved') or self._now_ms() >= proof['expires_ms']
                or proof['flow'] != flow['flow'] or proof['epoch'] != flow['epoch']
                or proof['install'] != flow['install'] or proof['generation'] != flow['generation']
                or proof['manifest_digest'] != flow['manifest_digest']
                or proof['manifest_sequence'] != candidate.sequence
                or proof['service_key_id'] != candidate.key_id
                or proof['service_public_key_sha256'] != candidate.fingerprint
                or proof['mail_key_id'] != flow['key_id']):
            raise IngressRefused('proof_not_armed')

    @staticmethod
    def _body(raw: bytes, armed: dict) -> dict[str, str]:
        try:
            value = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise IngressRefused('bad_proof_body') from None
        if (not isinstance(value, dict) or set(value) != BODY_KEYS
                or any(not isinstance(v, str) for v in value.values())):
            raise IngressRefused('bad_proof_body')
        expected = {'kind': 'enrollment-proof', 'version': '1', 'install_id': armed['install'],
                    'generation': str(armed['generation']), 'manifest_sequence': str(armed['manifest_sequence']),
                    'service_key_id': armed['service_key_id'],
                    'service_public_key_sha256': armed['service_public_key_sha256'],
                    'mail_key_id': armed['mail_key_id'], 'route_origin': armed['route_origin'],
                    'owner_address_sha256': armed['owner_address_sha256']}
        if (any(value[k] != v for k, v in expected.items())
                or not hmac.compare_digest(value['challenge'].encode(), armed['challenge'].encode())
                or not _NONCE.fullmatch(value['service_nonce'])):
            raise IngressRefused('proof_mismatch')
        return value

    # --- projection (4.6) ------------------------------------------------------------------------
    def snapshot_evidence(self) -> dict | None:
        """Read the cached signed Worker status. Call OUTSIDE any transaction."""
        self._enabled()
        value = self.worker_evidence()
        if (not isinstance(value, Mapping) or set(value) != EVIDENCE_KEYS
                or type(value['expires_ms']) is not int or type(value['generation']) is not int
                or not all(isinstance(value[k], str) for k in ('install_id', 'state', 'receipt_digest'))):
            return None
        return dict(value)

    def custody_in_tx(self, db: sqlite3.Connection, flow: dict, evidence: dict | None) -> bool:
        """True only when the local proof is `proved`, unexpired and current for this flow/epoch/
        manifest/generation AND the Worker evidence (verified, same digest, same scope) is unexpired.
        Pure reads on the caller's connection: no I/O."""
        self._enabled()
        proof = self._proof(db, flow['install'], flow['generation'])
        if proof is None or evidence is None:
            return False
        now = self._now_ms()
        try:
            candidate = self._candidate(flow)
        except EnrollmentRefused:
            return False
        return (proof['state'] == 'proved' and now < proof['expires_ms'] and proof['receipt_digest'] != ''
                and proof['flow'] == flow['flow'] and proof['epoch'] == flow['epoch']
                and proof['manifest_digest'] == flow['manifest_digest']
                and proof['manifest_sequence'] == candidate.sequence
                and evidence['state'] == 'verified' and evidence['install_id'] == flow['install']
                and evidence['generation'] == flow['generation']
                and hmac.compare_digest(evidence['receipt_digest'].encode(), proof['receipt_digest'].encode())
                and now < evidence['expires_ms'])

    def consume_in_tx(self, db: sqlite3.Connection, flow: dict) -> None:
        """Called inside enable()'s transaction: proof -> consumed, activation -> pending (4.8)."""
        proof = self._proof(db, flow['install'], flow['generation'])
        if proof is None or proof['state'] != 'proved':
            raise EnrollmentRefused('prerequisites_pending')
        db.execute("UPDATE mail_delivery_proof SET state='consumed' WHERE install=? AND generation=?",
                   (flow['install'], flow['generation']))
        db.execute("INSERT INTO mail_delivery_activation VALUES(?,?,?,'pending',?,?)",
                   (flow['install'], flow['generation'], flow['epoch'], proof['receipt_digest'], self._now_ms()))
