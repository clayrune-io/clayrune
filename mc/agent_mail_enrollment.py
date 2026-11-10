"""Human-owned local enrollment lifecycle; injected adapters only (unit 7b).

No production factory, network client, key generator, unlock or startup worker.
Remote adapters must be reviewed before wiring: ownership never enables mail.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from typing import Callable, Protocol
import uuid

from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal
from mc.agent_mail_enrollment_state import ManifestCAS, initialize
from mc.agent_mail_ingress import MailIngress
from mc.agent_mail_service_manifest import ServiceKeyCandidate, verify_service_manifest
from mc.agent_mail_signing import ID, ServicePin
from mc.agent_mail_store import MailStore

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


class EnrollmentAdapter(Protocol):
    """Synthetic in 7b. Real adapter must enforce 7a scopes/leases/idempotency.

    Each remote call is keyed by flow+generation. revoke is idempotent and
    prevents every delayed begin/verify from reviving that generation.
    OwnershipReceipt is authenticated evidence, never a caller JSON object.
    """
    def begin(self, context: dict, flow_secret: bytes) -> PendingIdentity: ...
    def verify(self, context: dict, flow_secret: bytes, code: str) -> OwnershipReceipt: ...
    def revoke(self, install: str, generation: int) -> None: ...


class Enrollment:
    def __init__(self, store: MailStore, seal: IdentitySeal, *, adapter: EnrollmentAdapter,
                 material: Callable[[], tuple[bytes, bytes]], prerequisites: Callable[[], Prerequisites],
                 install_id: str, root_public_key: str, service_origin: str, enabled: bool = False):
        self.store, self.seal, self.adapter = store, seal, adapter
        self.material, self.prerequisites = material, prerequisites
        self.install_id, self.root, self.origin = install_id, root_public_key, service_origin
        self.enabled = enabled
        if not ID.fullmatch(install_id):
            raise EnrollmentRefused('invalid_install')
        # Disabled construction is side-effect free.
        if enabled:
            initialize(store)
        self.manifests = ManifestCAS(store)

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
                or value['confirmed'] not in (0, 1) or value['ownership'] not in (0, 1)):
            raise EnrollmentRefused('lifecycle_state_corrupt')
        return value

    @staticmethod
    def _candidate(row: dict) -> ServiceKeyCandidate:
        try:
            value = json.loads(row['candidate'])
            value['retired_key_ids'] = tuple(value['retired_key_ids'])
            return ServiceKeyCandidate(**value)
        except (ValueError, TypeError, KeyError):
            raise EnrollmentRefused('lifecycle_state_corrupt') from None

    def status(self) -> dict:
        self._enabled()
        with self.store._read() as db:
            row = self._row(db)
            pending = db.execute('SELECT COUNT(*) FROM mail_enrollment_revocations WHERE done=0').fetchone()[0]
            reg = db.execute('SELECT * FROM registrations WHERE registration_id=?',
                             (row['install'] if row else self.install_id,)).fetchone()
        if not row:
            return {'stage': 'not_enrolled', 'enabled': False, 'revocations_pending': pending}
        candidate = self._candidate(row)
        # No flow id, owner mailbox, code, seed or flow secret in read status.
        return {'stage': row['stage'], 'enabled': bool(reg and reg['enabled'] and
                    reg['generation'] == row['generation'] and row['stage'] == 'active'),
                'generation': row['generation'], 'public_key': row['public_key'],
                'service_fingerprint': candidate.fingerprint, 'revocations_pending': pending}

    def start(self, owner: str, payload: list[object], signature: str) -> dict:
        self._enabled()
        if not isinstance(owner, str) or not re.fullmatch(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}', owner) or len(owner) > 254:
            raise EnrollmentRefused('invalid_owner')
        candidate = verify_service_manifest(payload, signature, root_public_key=self.root,
                    service_origin=self.origin, store=self.manifests,
                    now_ms=int(self.store.clock() * 1000), enabled=True)
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
                           '(slot,flow,install,generation,owner,key_id,stage,epoch,candidate,seal_identity) '
                           'VALUES(1,?,?,?,?,?,?,?,?,?)',
                           (flow, install, generation, owner, 'mail_' + flow,
                            'preparing', epoch, json.dumps(asdict(candidate), sort_keys=True), self.install_id))
        return self.resume()

    def _context(self, *, prepare: bool = False) -> dict:
        with self.store._tx() as db:
            row = self._row(db)
            if not row or row['stage'] == 'disabled':
                raise EnrollmentRefused('enrollment_required')
            self._current(db, row)
            args = (row['flow'], row['seal_identity'], row['generation'], row['key_id'])
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
                or row['stage'] != expected['stage'] or row['stage'] == 'disabled'):
            raise EnrollmentRefused('lifecycle_raced')
        reg = db.execute('SELECT * FROM registrations WHERE registration_id=?', (row['install'],)).fetchone()
        if reg is None or reg['generation'] != row['generation']:
            raise EnrollmentRefused('generation_changed')
        return row

    def _proof(self, row: dict) -> bytes:
        # Only the dedicated adapter receives the flow secret. Actual dual-key
        # transcript and machine-custody signing stay gated for 7c integration.
        return self.seal.flow_secret(row['flow'], row['seal_identity'], row['generation'], row['key_id'])

    def resume(self) -> dict:
        self._enabled()
        row = self._context(prepare=True)
        if row['stage'] == 'pending':
            try:
                pending = self.adapter.begin(dict(row), self._proof(row))
            except Exception:
                raise EnrollmentRefused('remote_begin_retryable') from None
            with self.store._tx() as db:
                self._current(db, row)
                candidate = self._candidate(row)
                if (not isinstance(pending, PendingIdentity) or not ID.fullmatch(pending.install)
                        or type(pending.generation) is not int or pending.generation != row['generation']
                        or pending != PendingIdentity(row['flow'], pending.install, row['generation'],
                                row['owner'], row['public_key'], candidate.fingerprint)):
                    raise EnrollmentRefused('pending_scope_mismatch')
                if row['assigned'] and pending.install != row['install']:
                    raise EnrollmentRefused('pending_identity_changed')
                if pending.install != row['install']:
                    # Initial reservation is only a candidate; it grants no
                    # ownership. Persist the service-selected opaque identity
                    # before disclosure/verification. Keep the disabled local
                    # reservation as a tombstone; never adopt another inbox.
                    if db.execute('SELECT 1 FROM registrations WHERE registration_id=?', (pending.install,)).fetchone():
                        raise EnrollmentRefused('registration_conflict')
                    db.execute('INSERT INTO registrations VALUES(?,?,0,?)',
                               (pending.install, row['generation'], self.store.clock()))
                db.execute('UPDATE mail_enrollment_flow SET install=?,assigned=1 WHERE slot=1', (pending.install,))
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
            db.execute('UPDATE mail_enrollment_flow SET confirmed=1 WHERE slot=1')
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
                 'service_origin': candidate.origin, 'manifest_sequence': candidate.sequence}

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

    def _fresh_candidate(self, row: dict) -> None:
        candidate = self._candidate(row)
        floor = self.manifests.load(candidate.origin)
        if floor is None or floor.sequence != candidate.sequence or int(self.store.clock() * 1000) >= candidate.expires_at_ms:
            raise EnrollmentRefused('manifest_stale')

    def enable(self) -> dict:
        self._enabled()
        row = self._context()
        with self.store._tx() as db:
            current = self._current(db, row)
            if not current['confirmed'] or not current['ownership']:
                raise EnrollmentRefused('ownership_required')
            if int(self.store.clock() * 1000) >= current['ownership_expires_ms']:
                raise EnrollmentRefused('ownership_expired')
            self._fresh_candidate(current)
            # Trusted current probes, never booleans from a request body.
            if not self.prerequisites().ready():
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
            db.execute("UPDATE mail_enrollment_flow SET stage='active' WHERE slot=1")
        return self.status()

    def disable(self) -> dict:
        self._enabled()
        with self.store._tx() as db:
            row = self._row(db)
            if row and row['stage'] != 'disabled':
                # Queue remote revocation and cancel local send/follow-up in
                # ONE transaction. Failure cannot acknowledge a partial disable.
                db.execute('INSERT OR IGNORE INTO mail_enrollment_revocations VALUES(?,?,0)',
                           (row['install'], row['generation']))
                self.store.revoke_registration(row['install'], transaction=db)
                db.execute("UPDATE mail_enrollment_flow SET stage='disabled',epoch=epoch+1 WHERE slot=1")
        return self.status()

    def retry_revocations(self) -> dict:
        self._enabled()
        with self.store._read() as db:
            pending = db.execute('SELECT install,generation FROM mail_enrollment_revocations WHERE done=0').fetchall()
        for install, generation in pending:
            try:
                self.adapter.revoke(install, generation)
            except Exception:
                # The durable item remains pending. Closed observable error,
                # never the adapter's potentially secret-bearing message.
                raise EnrollmentRefused('remote_revoke_retryable') from None
            with self.store._tx() as db:
                db.execute('UPDATE mail_enrollment_revocations SET done=1 WHERE install=? AND generation=?',
                           (install, generation))
        return self.status()
