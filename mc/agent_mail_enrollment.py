"""Human-owned local enrollment lifecycle; injected adapters only (unit 7b).

No production factory, network client, key generator, unlock or startup worker.
Remote adapters must be reviewed before wiring: ownership never enables mail.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
import re
from typing import Callable, Protocol
import uuid

from mc.agent_mail_delivery_proof import DeliveryProof
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal
from mc.agent_mail_enrollment_manifest import candidate_from_evidence, manifest_digest
from mc.agent_mail_enrollment_revocations import RevocationAdapter, Revocations
from mc.agent_mail_enrollment_state import ManifestCAS, initialize
from mc.agent_mail_ingress import MailIngress
from mc.agent_mail_lifecycle_fence import LifecycleFences
from mc.agent_mail_service_manifest import ServiceKeyCandidate, verify_service_manifest
from mc.agent_mail_signing import ID, ServicePin
from mc import agent_mail_status_view as status_view
from mc.agent_mail_store import MailStore
from mc.core import _log

ROUTE = '/api/agent-mail/ingress'


@dataclass(frozen=True)
class Prerequisites:
    tunnel: bool = False
    claude_extraction: bool = False
    machine_custody: bool = False

    def ready(self) -> bool:
        return all(v is True for v in (self.tunnel, self.claude_extraction, self.machine_custody))


@dataclass(frozen=True)
class PendingIdentity:
    flow: str
    install: str
    generation: int
    owner: str
    public_key: str
    service_fingerprint: str


@dataclass(frozen=True)
class OwnershipReceipt:
    flow: str
    install: str
    generation: int
    owner: str
    public_key: str
    service_fingerprint: str
    expires_at_ms: int


class EnrollmentAdapter(RevocationAdapter, Protocol):
    """Synthetic in 7b. Real adapter must enforce 7a scopes/leases/idempotency.

    Each remote call is keyed by flow+generation. cancel_flow durably fences
    delayed requests and resolves the actual assignment before revoke.
    OwnershipReceipt is authenticated evidence, never a caller JSON object.
    """
    def begin(self, context: dict, flow_secret: bytes) -> PendingIdentity: ...
    def verify(self, context: dict, flow_secret: bytes, code: str) -> OwnershipReceipt: ...
    def revoke(self, install: str, generation: int) -> None: ...


class Enrollment:
    def __init__(self, store: MailStore, seal: IdentitySeal, *, adapter: EnrollmentAdapter,
                 material: Callable[[], tuple[bytes, bytes]], prerequisites: Callable[[], Prerequisites],
                 install_id: str, root_public_key: str, service_origin: str, enabled: bool = False,
                 delivery: DeliveryProof | None = None, fences: LifecycleFences | None = None):
        self.store, self.seal, self.adapter = store, seal, adapter
        # Optional fence tokens (7d-2d). None keeps flows token-less, exactly as before.
        self.fences = fences
        # Optional proof of machine custody (7d-2c). None keeps machine_custody on the injected probe.
        self.delivery = delivery
        self.material, self.prerequisites = material, prerequisites
        self.install_id, self.root, self.origin = install_id, root_public_key, service_origin
        self.enabled = enabled
        if not ID.fullmatch(install_id):
            raise EnrollmentRefused('invalid_install')
        # Disabled construction is side-effect free.
        if enabled:
            initialize(store)
        self.manifests = ManifestCAS(store)
        self.revocations = Revocations(store, adapter)

    def _enabled(self) -> None:
        if self.enabled is not True:
            raise EnrollmentRefused('enrollment_disabled')

    @staticmethod
    def _row(db) -> dict | None:
        row = db.execute('SELECT * FROM mail_enrollment_flow WHERE slot=1').fetchone()
        if row is None:
            return None
        value = dict(row)
        if (value['stage'] not in {'preparing', 'pending', 'delivery_pending', 'active', 'disabled'}
                or type(value['generation']) is not int or value['generation'] < 1
                or type(value['epoch']) is not int or value['epoch'] < 1
                or type(value['seal_generation']) is not int or value['seal_generation'] < 1
                or value['assigned'] not in (0, 1) or value['remote_started'] not in (0, 1)
                or value['confirmed'] not in (0, 1) or value['ownership'] not in (0, 1)):
            raise EnrollmentRefused('lifecycle_state_corrupt')
        return value

    def _candidate(self, row: dict) -> ServiceKeyCandidate:
        return candidate_from_evidence(row, root=self.root, origin=self.origin)

    def status(self) -> dict:
        self._enabled()
        # Cached worker evidence is read BEFORE the read transaction, like enable().
        evidence = None
        if self.delivery is not None:
            try:
                evidence = self.delivery.snapshot_evidence()
            except Exception as exc:
                _log(f'[agent-mail] status evidence read failed: {type(exc).__name__}', flush=True)
        with self.store._read() as db:
            row = self._row(db)
            pending = db.execute('SELECT COUNT(*) FROM mail_enrollment_revocations WHERE done=0').fetchone()[0]
            reg = db.execute('SELECT * FROM registrations WHERE registration_id=?',
                             (row['install'] if row else self.install_id,)).fetchone()
            extra = self._status_extra(db, row, evidence)
        if not row:
            return {'stage': 'not_enrolled', 'enabled': False, 'revocations_pending': pending, **extra}
        # The kill switch must still acknowledge closure if old evidence is
        # corrupt/expired. A disabled row discloses no trusted service pin.
        fingerprint = '' if row['stage'] == 'disabled' else self._candidate(row).fingerprint
        # No flow id, owner mailbox, code, seed or flow secret in read status.
        return {'stage': row['stage'], 'enabled': bool(reg and reg['enabled'] and
                    reg['generation'] == row['generation'] and row['stage'] == 'active'),
                'generation': row['generation'], 'public_key': row['public_key'],
                'service_fingerprint': fingerprint, 'revocations_pending': pending, **extra}

    def _status_extra(self, db, row: dict | None, evidence: dict | None) -> dict:
        """Bounded 7e panel metadata (see agent_mail_status_view). Never raises."""
        now_ms = int(self.store.clock() * 1000)
        try:
            prerequisites = self.prerequisites()
        except Exception as exc:
            _log(f'[agent-mail] status prerequisite probe failed: {type(exc).__name__}', flush=True)
            prerequisites = Prerequisites()
        if self.delivery is not None and row is not None and row['stage'] != 'disabled':
            # Same rule as enable(): only local proof + Worker evidence count. Unreadable = not ready.
            try:
                custody = self.delivery.custody_in_tx(db, row, evidence)
            except Exception as exc:
                _log(f'[agent-mail] status custody read failed: {type(exc).__name__}', flush=True)
                custody = False
            prerequisites = replace(prerequisites, machine_custody=custody)
        probes = status_view.prerequisite_view(prerequisites)
        out: dict = {'now_ms': now_ms, 'prerequisites': probes, 'mail': status_view.mail_counts(db)}
        if row is not None:
            out.update({'owner_hint': status_view.owner_hint(row['owner']), 'assigned': bool(row['assigned']),
                        'remote_started': bool(row['remote_started']), 'confirmed': bool(row['confirmed']),
                        'ownership': bool(row['ownership']),
                        'ownership_expires_ms': row['ownership_expires_ms'] if row['ownership'] else 0,
                        'proof': status_view.proof_view(db, row['install'], row['generation'])})
        return out

    def start(self, owner: str, payload: list[object], signature: str) -> dict:
        self._enabled()
        if not isinstance(owner, str) or not re.fullmatch(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}', owner) or len(owner) > 254:
            raise EnrollmentRefused('invalid_owner')
        candidate = verify_service_manifest(payload, signature, root_public_key=self.root,
                    service_origin=self.origin, store=self.manifests,
                    now_ms=int(self.store.clock() * 1000), enabled=True)
        evidence = json.dumps({'payload': payload, 'signature': signature}, sort_keys=True)
        digest = manifest_digest(payload)
        # Human start authorizes an intent, but never remotely publishes a key
        # until its seal AND its public identity have committed.
        with self.store._tx() as db:
            row = self._row(db)
            if db.execute('SELECT 1 FROM mail_enrollment_revocations WHERE done=0').fetchone():
                raise EnrollmentRefused('revocation_pending')
            if row and row['stage'] != 'disabled':
                if row['owner'] != owner or self._candidate(row) != candidate:
                    raise EnrollmentRefused('disable_before_identity_change')
            else:
                if row is None and self.seal.folder.exists() and any(self.seal.folder.glob('*.sealed')):
                    raise EnrollmentRefused('lifecycle_state_missing')
                install = row['install'] if row else self.install_id
                reg = db.execute('SELECT * FROM registrations WHERE registration_id=?', (install,)).fetchone()
                if reg is None:
                    db.execute('INSERT INTO registrations VALUES(?,1,0,?)', (install, self.store.clock()))
                    generation = 1
                else:
                    if reg['enabled']:
                        raise EnrollmentRefused('registration_conflict')
                    generation = reg['generation']
                flow = uuid.uuid4().hex
                epoch = row['epoch'] + 1 if row else 1
                db.execute('INSERT OR REPLACE INTO mail_enrollment_flow '
                           '(slot,flow,install,generation,owner,key_id,stage,epoch,candidate,seal_identity,'
                           'seal_generation,manifest,manifest_digest,manifest_checked_ms) '
                           'VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (flow, install, generation, owner, 'mail_' + flow,
                            'preparing', epoch, json.dumps(asdict(candidate), sort_keys=True), self.install_id,
                            generation, evidence, digest, int(self.store.clock() * 1000)))
                # Same transaction as the flow row and before remote_started=1: any begin is cancellable.
                if self.fences is not None:
                    self.fences.create_in_tx(db, flow)
        return self.resume()

    def _context(self, *, prepare: bool = False) -> dict:
        with self.store._tx() as db:
            row = self._row(db)
            if not row or row['stage'] == 'disabled':
                raise EnrollmentRefused('enrollment_required')
            self._current(db, row)
            args = (row['flow'], row['seal_identity'], row['seal_generation'], row['key_id'])
            if row['stage'] == 'preparing' and prepare:
                public = self.seal.create(*args, self.material)
                db.execute("UPDATE mail_enrollment_flow SET public_key=?,stage='pending' WHERE slot=1", (public,))
                row['public_key'], row['stage'] = public, 'pending'
            if self.seal.public(*args) != row['public_key']:
                raise EnrollmentRefused('identity_mismatch')
            return row

    def _current(self, db, expected: dict) -> dict:
        row = self._row(db)
        if (not row or row['flow'] != expected['flow'] or row['epoch'] != expected['epoch']
                or row['install'] != expected['install']
                or row['generation'] != expected['generation']
                or any(row[key] != expected[key] for key in
                       ('manifest', 'manifest_digest', 'candidate', 'confirmed_digest'))
                or row['stage'] != expected['stage'] or row['stage'] == 'disabled'):
            raise EnrollmentRefused('lifecycle_raced')
        reg = db.execute('SELECT * FROM registrations WHERE registration_id=?', (row['install'],)).fetchone()
        if reg is None or reg['generation'] != row['generation']:
            raise EnrollmentRefused('generation_changed')
        return row

    def _proof(self, row: dict) -> bytes:
        # Only the dedicated adapter receives the flow secret. Actual dual-key
        # transcript and machine-custody signing stay gated for 7c integration.
        return self.seal.flow_secret(row['flow'], row['seal_identity'], row['seal_generation'], row['key_id'])

    def resume(self) -> dict:
        self._enabled()
        row = self._context(prepare=True)
        if row['stage'] == 'pending':
            proof = self._proof(row)
            with self.store._tx() as db:
                self._current(db, row)
                self._fresh_candidate(row)
                # Crash before/after the remote call remains cancellable by
                # stable flow, without guessing a service install identity.
                db.execute('UPDATE mail_enrollment_flow SET remote_started=1 WHERE slot=1')
            try:
                pending = self.adapter.begin(dict(row), proof)
            except Exception:
                raise EnrollmentRefused('remote_begin_retryable') from None
            with self.store._tx() as db:
                self._current(db, row)
                candidate = self._candidate(row)
                if (not isinstance(pending, PendingIdentity) or not isinstance(pending.install, str)
                        or not ID.fullmatch(pending.install)
                        or type(pending.generation) is not int or pending.generation < 1
                        or pending != PendingIdentity(row['flow'], pending.install, pending.generation,
                                row['owner'], row['public_key'], candidate.fingerprint)):
                    raise EnrollmentRefused('pending_scope_mismatch')
                if row['assigned'] and (pending.install, pending.generation) != (row['install'], row['generation']):
                    raise EnrollmentRefused('pending_identity_changed')
                if pending.install != row['install']:
                    # Initial reservation is only a candidate; it grants no
                    # ownership. Persist the service-selected opaque identity
                    # before disclosure/verification. Keep the disabled local
                    # reservation as a tombstone; never adopt another inbox.
                    if db.execute('SELECT 1 FROM registrations WHERE registration_id=?', (pending.install,)).fetchone():
                        raise EnrollmentRefused('registration_conflict')
                    db.execute('INSERT INTO registrations VALUES(?,?,0,?)',
                               (pending.install, pending.generation, self.store.clock()))
                elif pending.generation != row['generation']:
                    # Never lower/reuse a preexisting inbox's authority fence.
                    raise EnrollmentRefused('pending_scope_mismatch')
                db.execute('UPDATE mail_enrollment_flow SET install=?,generation=?,assigned=1 WHERE slot=1',
                           (pending.install, pending.generation))
        return self.status()

    def confirm(self, confirmation: dict[str, object]) -> dict:
        self._enabled()
        row = self._context()
        exact = self._review(row)
        if confirmation != exact:
            raise EnrollmentRefused('confirmation_mismatch')
        with self.store._tx() as db:
            self._current(db, row)
            self._fresh_candidate(row)
            db.execute('UPDATE mail_enrollment_flow SET confirmed=1,confirmed_digest=manifest_digest WHERE slot=1')
        return self.status()

    def review(self) -> dict:
        """Human-gated disclosure only; no code or flow secret."""
        self._enabled()
        row = self._context()
        if not row['assigned']:
            raise EnrollmentRefused('service_reservation_pending')
        return self._review(row)

    def _review(self, row: dict) -> dict:
        if not row['assigned']:
            raise EnrollmentRefused('service_reservation_pending')
        candidate = self._candidate(row)
        return {'owner': row['owner'], 'install': row['install'], 'generation': row['generation'],
                 'public_key': row['public_key'], 'service_fingerprint': candidate.fingerprint,
                 'service_origin': candidate.origin, 'manifest_sequence': candidate.sequence,
                 'manifest_digest': row['manifest_digest']}

    def change_identity(self, owner: str, payload: list[object], signature: str) -> dict:
        """Owner/key/repin all revoke first, then start a separate ceremony.

        No old key is needed to disable: lost/replaced vault keys require this
        human path. No automatic recovery is attempted by status/resume.
        """
        self.disable()
        self.retry_revocations()
        return self.start(owner, payload, signature)

    def verify(self, code: str) -> dict:
        self._enabled()
        row = self._context()
        if not row['confirmed'] or row['stage'] not in {'pending', 'delivery_pending'}:
            raise EnrollmentRefused('confirmation_required')
        if not isinstance(code, str) or not re.fullmatch('[A-Za-z0-9_-]{1,256}', code):
            raise EnrollmentRefused('invalid_code')
        candidate = self._candidate(row)
        self._fresh_candidate(row)
        try:
            receipt = self.adapter.verify(dict(row), self._proof(row), code)
        except Exception:
            raise EnrollmentRefused('remote_verify_retryable') from None
        if not isinstance(receipt, OwnershipReceipt):
            raise EnrollmentRefused('ownership_scope_mismatch')
        expected = OwnershipReceipt(row['flow'], row['install'], row['generation'], row['owner'],
                                    row['public_key'], candidate.fingerprint, receipt.expires_at_ms)
        if receipt != expected:
            raise EnrollmentRefused('ownership_scope_mismatch')
        now_ms = int(self.store.clock() * 1000)
        if type(receipt.expires_at_ms) is not int or not now_ms < receipt.expires_at_ms <= now_ms + 300000:
            raise EnrollmentRefused('ownership_expired')
        with self.store._tx() as db:
            self._current(db, row)
            self._fresh_candidate(row)
            if int(self.store.clock() * 1000) >= receipt.expires_at_ms:
                raise EnrollmentRefused('ownership_expired')
            db.execute("UPDATE mail_enrollment_flow SET ownership=1,stage='delivery_pending',ownership_expires_ms=? WHERE slot=1",
                       (receipt.expires_at_ms,))
        return self.status()

    def _fresh_candidate(self, row: dict) -> ServiceKeyCandidate:
        candidate = self._candidate(row)
        floor = self.manifests.load(candidate.origin)
        if (floor is None or floor.sequence != candidate.sequence or floor.digest != row['manifest_digest']
                or int(self.store.clock() * 1000) >= candidate.expires_at_ms):
            raise EnrollmentRefused('manifest_stale')
        return candidate

    def arm_proof(self) -> dict:
        """Human-gated, local. Writes one proof row; never pins, enables or changes stage."""
        self._enabled()
        if self.delivery is None:
            raise EnrollmentRefused('delivery_proof_unavailable')
        row = self._context()
        with self.store._tx() as db:
            current = self._current(db, row)
            if not current['confirmed'] or not current['ownership'] or current['stage'] not in ('delivery_pending', 'active'):
                raise EnrollmentRefused('ownership_required')
            if int(self.store.clock() * 1000) >= current['ownership_expires_ms']:
                raise EnrollmentRefused('ownership_expired')
            candidate = self._fresh_candidate(current)
            return self.delivery.arm_in_tx(db, current, candidate)

    def enable(self) -> dict:
        self._enabled()
        row = self._context()
        # Cached, signature-verified Worker status, read BEFORE the transaction: no I/O inside it.
        evidence = self.delivery.snapshot_evidence() if self.delivery is not None else None
        with self.store._tx() as db:
            current = self._current(db, row)
            if not current['confirmed'] or not current['ownership']:
                raise EnrollmentRefused('ownership_required')
            if int(self.store.clock() * 1000) >= current['ownership_expires_ms']:
                raise EnrollmentRefused('ownership_expired')
            self._fresh_candidate(current)
            # Trusted current probes, never booleans from a request body.
            prerequisites = self.prerequisites()
            if self.delivery is not None:
                # Proof REPLACES the injected machine_custody value: only local proof + Worker evidence count.
                prerequisites = replace(prerequisites, machine_custody=self.delivery.custody_in_tx(db, current, evidence))
            if not prerequisites.ready():
                raise EnrollmentRefused('prerequisites_pending')
            candidate = self._candidate(current)
            db.execute('UPDATE registrations SET enabled=1,updated_at=? WHERE registration_id=?',
                       (self.store.clock(), row['install']))
            MailIngress(self.store).pin_service(ServicePin(row['install'], row['generation'],
                        candidate.key_id, candidate.public_key, ROUTE), transaction=db)
            # A slow pin write or probe cannot extend the accepted lease.
            if int(self.store.clock() * 1000) >= current['ownership_expires_ms']:
                raise EnrollmentRefused('ownership_expired')
            self._fresh_candidate(current)
            if self.delivery is not None:
                self.delivery.consume_in_tx(db, current)
            db.execute("UPDATE mail_enrollment_flow SET stage='active' WHERE slot=1")
        return self.status()

    def disable(self) -> dict:
        self._enabled()
        with self.store._tx() as db:
            row = self._row(db)
            if row and row['stage'] != 'disabled':
                # Queue remote revocation and cancel local send/follow-up in
                # ONE transaction. Failure cannot acknowledge a partial disable.
                if row['remote_started'] or row['assigned']:
                    self.revocations.queue(db, row)
                    self.store.revoke_registration(row['install'], transaction=db)
                # A never-sent intent has no remote identity or local delivery
                # authority. Its epoch fences stale work; generation is unused.
                if self.delivery is not None:
                    self.delivery.invalidate_in_tx(db, row['install'])
                db.execute("UPDATE mail_enrollment_flow SET stage='disabled',epoch=epoch+1 WHERE slot=1")
        return self.status()

    def retry_revocations(self) -> dict:
        self._enabled()
        self.revocations.retry()
        return self.status()
