"""Unit 7d-2c: proof of machine custody, local half. Fixed fixture seeds; synthetic clocks; no network.

Run with the application tests (NOT with control_plane/tests: their `tests.conftest` collide).
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Flask
import pytest

from test_agent_mail_enrollment import FakeAdapter, MASTER, NOW, ORIGIN, OWNER, PUBLIC, SEED, SECRET, manifest
from mc import state
from mc.agent_mail_delivery_proof import (BODY_KEYS, PROOF_DIRECTION, DeliveryProof, receipt_array, receipt_bytes)
from mc.agent_mail_delivery_schema import REQUIRED, initialize as init_delivery
from mc.agent_mail_enrollment import Enrollment, Prerequisites
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal, b64
from mc.agent_mail_enrollment_state import initialize as init_enrollment
from mc.agent_mail_ingress import MailIngress
from mc.agent_mail_service_manifest import ManifestState
from mc.agent_mail_signing import IngressRefused
from mc.agent_mail_store import MailStore
from mc.blueprints.agent_mail_routes import FLAG, ROUTE, create_blueprint

SVC = Ed25519PrivateKey.from_private_bytes(bytes(range(64, 96)))
SVC_PUBLIC = b64(SVC.public_key().public_bytes_raw())
MAIL = Ed25519PrivateKey.from_private_bytes(SEED)
ROUTE_ORIGIN = 'https://device.example.test'
VECTORS = Path(__file__).resolve().parents[1] / 'workers' / 'agent-mail' / 'test' / 'fixtures' / 'proof-vectors.json'


@pytest.fixture
def kit(tmp_path):
    class Kit:
        def __init__(self):
            self.adapter, self.master, self.now, self.evidence = FakeAdapter(), MASTER, NOW, None
            self.ready, self.route, self.creations = Prerequisites(True, True, True), ROUTE_ORIGIN, 0
            self.path = tmp_path / 'outside-repo' / 'mail.sqlite3'
            self.folder = tmp_path / 'outside-repo' / 'identities'
            self.reads = []

        def material(self):
            self.creations += 1
            return SEED, SECRET

        def worker_evidence(self):
            self.reads.append(1)
            return self.evidence

        def core(self, delivery=True, **kwargs):
            store = MailStore(self.path, clock=lambda: self.now / 1000)
            seal = IdentitySeal(self.folder, key=lambda: self.master)
            proof = DeliveryProof(store, seal, root_public_key=PUBLIC, service_origin=ORIGIN,
                                  route_origin=lambda: self.route, worker_evidence=self.worker_evidence,
                                  enabled=True, **kwargs) if delivery else None
            return Enrollment(store, seal, adapter=self.adapter, material=self.material,
                              prerequisites=lambda: self.ready, install_id='install1', root_public_key=PUBLIC,
                              service_origin=ORIGIN, enabled=True, delivery=proof)

        def pending(self, **kwargs):
            core = self.core(**kwargs)
            core.start(OWNER, *manifest(public=SVC_PUBLIC))
            core.confirm(core.review())
            core.verify('copying-code')
            return core

        def armed(self, **kwargs):
            core = self.pending(**kwargs)
            core.arm_proof()
            return core
    return Kit()


def row(core):
    with core.store._read() as db:
        value = db.execute('SELECT * FROM mail_delivery_proof').fetchone()
    return dict(value) if value else None


def count(core, table):
    with core.store._read() as db:
        return db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]


def proof_request(core, *, stamp=NOW, nonce=None, service_nonce=None, direction=PROOF_DIRECTION,
                  body_over=None, signer=SVC, drop=()):
    proof = row(core)
    body = {'kind': 'enrollment-proof', 'version': '1', 'challenge': proof['challenge'],
            'install_id': proof['install'], 'generation': str(proof['generation']),
            'manifest_sequence': str(proof['manifest_sequence']), 'service_key_id': proof['service_key_id'],
            'service_public_key_sha256': proof['service_public_key_sha256'], 'mail_key_id': proof['mail_key_id'],
            'route_origin': proof['route_origin'], 'owner_address_sha256': proof['owner_address_sha256'],
            'service_nonce': service_nonce or uuid.uuid4().hex}
    body.update(body_over or {})
    for key in drop:
        del body[key]
    raw = json.dumps(body, separators=(',', ':')).encode()
    values = dict(version='1', direction=direction, key_id='service1', install_id='install1',
                  generation=str(proof['generation']), method='POST', path=ROUTE, timestamp=str(stamp),
                  nonce=nonce or uuid.uuid4().hex, digest=hashlib.sha256(raw).hexdigest())
    canonical = json.dumps(['clayrune-agent-mail', *values.values()], separators=(',', ':')).encode()
    headers = {'x-mail-' + k.replace('_', '-'): v for k, v in values.items() if k not in ('method', 'path')}
    headers['x-mail-signature'] = b64(signer.sign(canonical))
    headers['Content-Type'] = 'application/json'
    return headers, raw


def good_evidence(core):
    proof = row(core)
    return {'install_id': 'install1', 'generation': proof['generation'], 'state': 'verified',
            'receipt_digest': proof['receipt_digest'], 'expires_ms': proof['expires_ms']}


def prove(kit, core, **kwargs):
    headers, raw = proof_request(core, **kwargs)
    receipt = core.delivery.receive_proof(headers, raw)
    kit.evidence = good_evidence(core)
    return receipt


# --- arm -----------------------------------------------------------------------------------------
# 7e-1 added bounded panel metadata to status(); these keys legitimately track the proof/clock, so the
# "arm/prove never moves enrollment state" comparisons look at the enrollment fields only.
PANEL_KEYS = ('now_ms', 'prerequisites', 'mail', 'proof')


def enrollment_status(core):
    return {k: v for k, v in core.status().items() if k not in PANEL_KEYS}


def test_arm_writes_one_row_and_never_pins_enables_or_moves_stage(kit):
    core = kit.pending()
    before = enrollment_status(core)
    result = core.arm_proof()
    proof = row(core)
    assert result['state'] == 'armed' and proof['state'] == 'armed'
    assert enrollment_status(core) == before and before['stage'] == 'delivery_pending'
    assert count(core, 'mail_service_pins') == 0
    assert core.store.registration_state('install1')['enabled'] == 0
    assert len(proof['challenge']) == 64 and len(proof['proof_id']) == 32
    assert proof['expires_ms'] == min(NOW + 600_000, proof['ownership_expires_ms']) == NOW + 300000
    assert proof['route_origin'] == ROUTE_ORIGIN
    assert proof['owner_address_sha256'] == hashlib.sha256(OWNER.encode()).hexdigest()


def test_arm_requires_confirmed_unexpired_ownership_and_a_route(kit):
    core = kit.core()
    core.start(OWNER, *manifest(public=SVC_PUBLIC))
    with pytest.raises(EnrollmentRefused, match='ownership_required'):
        core.arm_proof()
    core.confirm(core.review())
    with pytest.raises(EnrollmentRefused, match='ownership_required'):
        core.arm_proof()
    core.verify('copying-code')
    kit.route = None
    with pytest.raises(EnrollmentRefused, match='route_origin_unavailable'):
        core.arm_proof()
    kit.route = 'http://device.example.test'
    with pytest.raises(EnrollmentRefused, match='route_origin_unavailable'):
        core.arm_proof()
    kit.route = ROUTE_ORIGIN
    kit.now = NOW + 300000
    with pytest.raises(EnrollmentRefused, match='ownership_expired'):
        core.arm_proof()
    assert row(core) is None


def test_arm_without_a_delivery_object_is_unavailable(kit):
    core = kit.pending(delivery=False)
    with pytest.raises(EnrollmentRefused, match='delivery_proof_unavailable'):
        core.arm_proof()


def test_rearm_replaces_the_armed_row_and_old_challenge_dies(kit):
    core = kit.armed()
    first = row(core)
    core.arm_proof()
    second = row(core)
    assert second['challenge'] != first['challenge'] and count(core, 'mail_delivery_proof') == 1
    headers, raw = proof_request(core)
    body = json.loads(raw)
    body['challenge'] = first['challenge']
    stale_headers, stale_raw = proof_request(core, body_over={'challenge': first['challenge']})
    with pytest.raises(IngressRefused, match='proof_mismatch'):
        core.delivery.receive_proof(stale_headers, stale_raw)
    assert core.delivery.receive_proof(headers, raw)['challenge'] == second['challenge']


def test_stale_manifest_refuses_arm(kit):
    core = kit.pending()
    assert core.manifests.compare_and_set(ORIGIN, ManifestState(1, core.manifests.load(ORIGIN).digest),
                                          ManifestState(2, 'b' * 64))
    with pytest.raises(EnrollmentRefused, match='manifest_stale'):
        core.arm_proof()


# --- receive -------------------------------------------------------------------------------------
def test_receipt_is_signed_by_the_mail_key_over_the_contract_array_and_commits_proved(kit):
    core = kit.armed()
    proof = row(core)
    headers, raw = proof_request(core, service_nonce='cd' * 16)
    receipt = core.delivery.receive_proof(headers, raw)
    assert set(receipt) == {'custody', 'install_id', 'generation', 'challenge', 'proof_id', 'signature'}
    assert receipt['custody'] == 'proof' and receipt['generation'] == 1
    signed = receipt_bytes('install1', 1, proof['challenge'], 'cd' * 16, ROUTE_ORIGIN, 1, proof['proof_id'])
    MAIL.public_key().verify(base64_decode(receipt['signature']), signed)
    after = row(core)
    assert after['state'] == 'proved' and after['receipt_digest'] == hashlib.sha256(signed).hexdigest()


def base64_decode(value):
    import base64
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def test_proof_cannot_create_mail_thread_wake_pin_or_consent(kit):
    core = kit.armed()
    tables = ['events', 'mail_service_pins', 'mail_transport_threads', 'mail_ingress_receipts', 'thread_bindings']
    before = {t: count(core, t) for t in tables}
    registration = dict(core.store.registration_state('install1'))
    stage = enrollment_status(core)
    prove(kit, core)
    assert {t: count(core, t) for t in tables} == before == {t: 0 for t in tables}
    assert dict(core.store.registration_state('install1')) == registration and registration['enabled'] == 0
    assert enrollment_status(core) == stage and stage['stage'] == 'delivery_pending'


def test_http_nonce_replay_is_refused_and_second_receipt_commits_nothing_new(kit):
    core = kit.armed()
    headers, raw = proof_request(core, nonce='a' * 32)
    core.delivery.receive_proof(headers, raw)
    digest = row(core)['receipt_digest']
    with pytest.raises(IngressRefused, match='replayed_nonce'):
        core.delivery.receive_proof(headers, raw)
    assert row(core)['receipt_digest'] == digest
    assert count(core, 'mail_ingress_nonces') == 1


@pytest.mark.parametrize('over,code', [
    ({'challenge': 'f' * 64}, 'proof_mismatch'), ({'generation': '2'}, 'proof_mismatch'),
    ({'install_id': 'other'}, 'proof_mismatch'), ({'route_origin': 'https://evil.example.test'}, 'proof_mismatch'),
    ({'mail_key_id': 'other'}, 'proof_mismatch'), ({'owner_address_sha256': '0' * 64}, 'proof_mismatch'),
    ({'service_key_id': 'service2'}, 'proof_mismatch'), ({'manifest_sequence': '2'}, 'proof_mismatch'),
    ({'service_public_key_sha256': '1' * 64}, 'proof_mismatch'), ({'kind': 'enrollment-proof2'}, 'proof_mismatch'),
    ({'service_nonce': 'short'}, 'proof_mismatch'), ({'extra': 'x'}, 'bad_proof_body'),
    ({'generation': 1}, 'bad_proof_body'),
])
def test_every_body_field_must_equal_the_armed_row(kit, over, code):
    core = kit.armed()
    headers, raw = proof_request(core, body_over=over)
    with pytest.raises(IngressRefused, match=code):
        core.delivery.receive_proof(headers, raw)
    assert row(core)['state'] == 'armed' and count(core, 'mail_ingress_nonces') == 0


def test_missing_body_key_and_non_object_bodies_refuse(kit):
    core = kit.armed()
    headers, raw = proof_request(core, drop=('challenge',))
    with pytest.raises(IngressRefused, match='bad_proof_body'):
        core.delivery.receive_proof(headers, raw)
    for body in (b'[]', b'not json', b'\xff\xfe'):
        h, _ = proof_request(core)
        h['x-mail-digest'] = hashlib.sha256(body).hexdigest()
        with pytest.raises(IngressRefused):
            core.delivery.receive_proof(h, body)


@pytest.mark.parametrize('case', ['direction', 'signer', 'stamp', 'key_id', 'install'])
def test_signature_domain_and_fields_are_enforced(kit, case):
    core = kit.armed()
    kwargs = {'direction': 'service-to-install'} if case == 'direction' else {}
    if case == 'signer':
        kwargs['signer'] = Ed25519PrivateKey.from_private_bytes(bytes(range(96, 128)))
    if case == 'stamp':
        kwargs['stamp'] = NOW - 300001
    headers, raw = proof_request(core, **kwargs)
    if case == 'key_id':
        headers['x-mail-key-id'] = 'service2'
    if case == 'install':
        headers['x-mail-install-id'] = 'install2'
    with pytest.raises(IngressRefused):
        core.delivery.receive_proof(headers, raw)
    assert row(core)['state'] == 'armed'


def test_ordinary_mail_verifier_refuses_a_proof_request(kit):
    core = kit.armed()
    headers, raw = proof_request(core)
    with pytest.raises(IngressRefused):
        MailIngress(core.store).receive(headers, raw)
    assert count(core, 'events') == 0 and count(core, 'mail_ingress_nonces') == 0


def test_unarmed_expired_and_invalidated_proofs_refuse(kit):
    core = kit.armed()
    headers, raw = proof_request(core)
    kit.now = row(core)['expires_ms']
    with pytest.raises(IngressRefused, match='proof_not_armed'):
        core.delivery.receive_proof(headers, raw)


def test_no_row_means_proof_not_armed(kit):
    core = kit.pending()
    # Build a request against a row that does not exist: arm, copy, then delete.
    core.arm_proof()
    headers, raw = proof_request(core)
    with core.store._tx() as db:
        db.execute('DELETE FROM mail_delivery_proof')
    with pytest.raises(IngressRefused, match='proof_not_armed'):
        core.delivery.receive_proof(headers, raw)


def test_epoch_change_invalidates_and_blocks_a_late_proof(kit):
    core = kit.armed()
    headers, raw = proof_request(core)
    core.disable()
    assert row(core)['state'] == 'invalidated'
    with pytest.raises(IngressRefused, match='proof_not_armed'):
        core.delivery.receive_proof(headers, raw)


def test_epoch_bump_alone_blocks_a_row_that_was_not_invalidated(kit):
    core = kit.armed()
    headers, raw = proof_request(core)
    with core.store._tx() as db:
        db.execute('UPDATE mail_delivery_proof SET epoch=epoch+1')
    with pytest.raises(IngressRefused, match='proof_not_armed'):
        core.delivery.receive_proof(headers, raw)


def test_manifest_sequence_change_blocks_the_proof(kit):
    core = kit.armed()
    headers, raw = proof_request(core)
    digest = core.manifests.load(ORIGIN).digest
    assert core.manifests.compare_and_set(ORIGIN, ManifestState(1, digest), ManifestState(2, 'c' * 64))
    with pytest.raises(IngressRefused, match='manifest_stale'):
        core.delivery.receive_proof(headers, raw)


def test_generation_change_blocks_the_proof(kit):
    core = kit.armed()
    headers, raw = proof_request(core)
    with core.store._tx() as db:
        db.execute('UPDATE mail_delivery_proof SET generation=generation+1')
    with pytest.raises(IngressRefused, match='proof_not_armed'):
        core.delivery.receive_proof(headers, raw)


def test_ownership_expiry_stops_the_proof(kit):
    core = kit.armed()
    headers, raw = proof_request(core)
    kit.now = NOW + 300000
    with pytest.raises(IngressRefused):
        core.delivery.receive_proof(headers, raw)


def test_vault_locked_is_503_signal_and_commits_nothing(kit):
    core = kit.armed()
    headers, raw = proof_request(core)
    kit.master = None
    with pytest.raises(EnrollmentRefused, match='vault_locked_or_unconfigured'):
        core.delivery.receive_proof(headers, raw)
    assert row(core)['state'] == 'armed' and count(core, 'mail_ingress_nonces') == 0
    kit.master = MASTER
    assert core.delivery.receive_proof(headers, raw)['custody'] == 'proof'  # same request, nonce unburnt


def test_lost_response_converges_on_the_latest_receipt(kit):
    core = kit.armed()
    first = prove(kit, core, service_nonce='aa' * 16)
    stale = good_evidence(core)
    second = prove(kit, core, service_nonce='bb' * 16)
    assert first['signature'] != second['signature'] and row(core)['state'] == 'proved'
    assert core.delivery.custody_in_tx(core.store._read().__enter__(), core._row(core.store._read().__enter__()),
                                       good_evidence(core)) is True
    # A Worker that only ever verified the first (lost second response) does not match.
    with core.store._read() as db:
        flow = core._row(db)
        assert core.delivery.custody_in_tx(db, flow, stale) is False
        assert core.delivery.custody_in_tx(db, flow, good_evidence(core)) is True


# --- projection and enable() ---------------------------------------------------------------------
def enable_refused(core):
    with pytest.raises(EnrollmentRefused, match='prerequisites_pending'):
        core.enable()
    assert core.store.registration_state('install1')['enabled'] == 0 and count(core, 'mail_service_pins') == 0


def test_injected_machine_custody_true_does_not_bypass_proof(kit):
    core = kit.pending()
    assert kit.ready.machine_custody is True
    enable_refused(core)
    core.arm_proof()
    enable_refused(core)


@pytest.mark.parametrize('mutate', [
    lambda e: None, lambda e: 'verified', lambda e: {**e, 'state': 'invalidated'},
    lambda e: {**e, 'state': 'armed'}, lambda e: {**e, 'receipt_digest': '0' * 64},
    lambda e: {**e, 'generation': 2}, lambda e: {**e, 'install_id': 'other'},
    lambda e: {**e, 'expires_ms': NOW}, lambda e: {**e, 'extra': 1},
    lambda e: {k: v for k, v in e.items() if k != 'state'}, lambda e: {**e, 'expires_ms': str(NOW + 1)},
])
def test_only_a_matching_verified_worker_status_sets_machine_custody(kit, mutate):
    core = kit.armed()
    prove(kit, core)
    kit.evidence = mutate(good_evidence(core))
    enable_refused(core)


def test_bare_200_login_html_redirect_and_edge_health_are_not_evidence(kit):
    core = kit.armed()
    headers, raw = proof_request(core)
    core.delivery.receive_proof(headers, raw)
    for junk in ('<html>login</html>', {'status': 200}, {'ok': True}, 200, b'OK', [], {'state': 'verified'}):
        kit.evidence = junk
        enable_refused(core)


def test_local_proof_alone_is_not_enough_and_neither_is_worker_alone(kit):
    core = kit.armed()
    prove(kit, core)
    kit.evidence = None
    enable_refused(core)
    other = kit.armed()  # re-arm: local row back to 'armed' with a new challenge
    kit.evidence = {'install_id': 'install1', 'generation': 1, 'state': 'verified',
                    'receipt_digest': 'f' * 64, 'expires_ms': NOW + 100}
    enable_refused(other)


def test_enable_with_proof_consumes_it_and_queues_one_pending_activation(kit):
    core = kit.armed()
    receipt_digest = None
    prove(kit, core)
    receipt_digest = row(core)['receipt_digest']
    assert core.enable()['enabled'] is True
    proof = row(core)
    assert proof['state'] == 'consumed'
    with core.store._read() as db:
        activation = dict(db.execute('SELECT * FROM mail_delivery_activation').fetchone())
    assert (activation['install'], activation['generation'], activation['state'],
            activation['receipt_digest']) == ('install1', 1, 'pending', receipt_digest)
    assert activation['epoch'] == 1 and count(core, 'mail_service_pins') == 1


def test_consumed_proof_cannot_be_reused_rearmed_or_replayed(kit):
    core = kit.armed()
    headers, raw = proof_request(core)
    prove(kit, core)
    core.enable()
    with pytest.raises(IngressRefused):
        core.delivery.receive_proof(headers, raw)
    with pytest.raises(EnrollmentRefused, match='proof_consumed'):
        core.arm_proof()
    with pytest.raises(IngressRefused, match='proof_not_armed'):
        core.delivery.receive_proof(*proof_request(core))


def test_disable_after_proof_invalidates_it_and_blocks_enable(kit):
    core = kit.armed()
    prove(kit, core)
    core.disable()
    assert row(core)['state'] == 'invalidated'
    with pytest.raises(EnrollmentRefused):
        core.enable()


def test_proof_expiry_blocks_enable(kit):
    core = kit.armed()
    prove(kit, core)
    with core.store._tx() as db:  # shorten the proof alone: manifest and ownership lease stay valid
        db.execute('UPDATE mail_delivery_proof SET expires_ms=?', (NOW + 50_000,))
    kit.now = NOW + 50_000
    kit.evidence = good_evidence(core)
    with pytest.raises(EnrollmentRefused, match='prerequisites_pending'):
        core.enable()
    kit.now = NOW + 49_999
    kit.evidence = good_evidence(core)
    assert core.enable()['enabled'] is True


def test_enable_reads_worker_evidence_outside_its_transaction(kit):
    core = kit.armed()
    prove(kit, core)
    probes = []

    def reader():
        conn = sqlite3.connect(kit.path, timeout=0.2)
        try:
            conn.execute('BEGIN IMMEDIATE')  # raises 'database is locked' if enable() holds its write lock
            probes.append('free')
            conn.execute('ROLLBACK')
        finally:
            conn.close()
        return kit.evidence
    core.delivery.worker_evidence = reader
    assert core.enable()['enabled'] is True
    # enable() reads once itself; the status() it returns reads the cached evidence once more (7e-1).
    assert probes and set(probes) == {'free'}


def test_without_delivery_enable_is_unchanged_and_never_reads_evidence(kit):
    core = kit.pending(delivery=False)
    assert core.enable()['enabled'] is True
    assert kit.reads == []
    with core.store._read() as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'mail_delivery_proof' not in tables


# --- schema --------------------------------------------------------------------------------------
def test_disabled_construction_creates_nothing(kit):
    core = kit.core()
    kit.path.unlink()
    store = MailStore(kit.path, clock=lambda: NOW / 1000)
    DeliveryProof(store, IdentitySeal(kit.folder, key=lambda: MASTER), root_public_key=PUBLIC,
                  service_origin=ORIGIN, route_origin=lambda: ROUTE_ORIGIN)
    with store._read() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'mail\\_delivery\\_%' ESCAPE '\\'").fetchone()
    with pytest.raises(EnrollmentRefused, match='delivery_proof_disabled'):
        DeliveryProof(store, IdentitySeal(kit.folder), root_public_key=PUBLIC, service_origin=ORIGIN,
                      route_origin=lambda: ROUTE_ORIGIN).snapshot_evidence()
    assert core is not None


def test_tables_use_their_own_prefix_and_leave_the_enrollment_exact_set_alone(kit):
    core = kit.pending()
    with core.store._read() as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert REQUIRED <= names
    assert {n for n in names if n.startswith('mail_enrollment_')} == {
        'mail_enrollment_version', 'mail_enrollment_flow', 'mail_enrollment_revocations',
        'mail_enrollment_manifests'}
    init_enrollment(core.store)
    init_delivery(core.store)  # both initializers are idempotent on a healthy store


@pytest.mark.parametrize('damage', ['DROP TABLE mail_delivery_activation', 'UPDATE mail_delivery_version SET version=9',
                                    'CREATE TABLE mail_delivery_extra(x INTEGER)',
                                    'INSERT INTO mail_delivery_version VALUES(1)'])
def test_a_damaged_delivery_schema_fails_closed_and_is_not_recreated(kit, damage):
    core = kit.pending()
    with core.store._tx() as db:
        db.execute(damage)
    with pytest.raises(EnrollmentRefused, match='delivery_schema_corrupt'):
        init_delivery(core.store)


def test_unmarked_leftover_delivery_table_fails_closed(tmp_path):
    store = MailStore(tmp_path / 'x.sqlite3', clock=lambda: NOW / 1000)
    with store._tx() as db:
        db.execute('CREATE TABLE mail_delivery_proof(x INTEGER)')
    with pytest.raises(EnrollmentRefused, match='delivery_schema_corrupt'):
        init_delivery(store)


# --- route ---------------------------------------------------------------------------------------
def client(kit, monkeypatch, core, *, proof_factory='core', flag=True):
    monkeypatch.setitem(state.CONFIG, FLAG, flag)
    app = Flask(__name__)
    app.config['TESTING'] = True
    factory = (lambda: core.delivery) if proof_factory == 'core' else proof_factory
    app.register_blueprint(create_blueprint(kit.path.parent / 'projects', store_factory=lambda: core.store,
                                            proof_factory=factory))
    return app.test_client()


def test_route_proof_roundtrip_returns_the_receipt_and_stays_default_off(kit, monkeypatch):
    core = kit.armed()
    headers, raw = proof_request(core)
    off = client(kit, monkeypatch, core, flag=False).post(ROUTE, headers=headers, data=raw)
    assert off.status_code == 404 and row(core)['state'] == 'armed'
    c = client(kit, monkeypatch, core)
    result = c.post(ROUTE, headers=headers, data=raw)
    assert result.status_code == 200 and result.get_json()['custody'] == 'proof'
    assert result.headers['Cache-Control'] == 'no-store' and row(core)['state'] == 'proved'
    replay = c.post(ROUTE, headers=headers, data=raw)
    assert replay.status_code == 401 and replay.get_json() == {'error': 'replayed_nonce'}


def test_route_without_a_proof_factory_is_503_and_commits_nothing(kit, monkeypatch):
    core = kit.armed()
    headers, raw = proof_request(core)
    result = client(kit, monkeypatch, core, proof_factory=None).post(ROUTE, headers=headers, data=raw)
    assert result.status_code == 503 and result.get_json() == {'error': 'proof_unavailable'}
    assert row(core)['state'] == 'armed' and count(core, 'mail_ingress_nonces') == 0


def test_route_vault_locked_is_503_vault_locked(kit, monkeypatch):
    core = kit.armed()
    headers, raw = proof_request(core)
    kit.master = None
    result = client(kit, monkeypatch, core).post(ROUTE, headers=headers, data=raw)
    assert result.status_code == 503 and result.get_json() == {'error': 'vault_locked'}
    assert row(core)['state'] == 'armed'


def test_route_proof_direction_is_decided_before_the_pin_lookup(kit, monkeypatch):
    core = kit.armed()
    assert count(core, 'mail_service_pins') == 0  # no pin exists, yet the proof is served
    headers, raw = proof_request(core)
    assert client(kit, monkeypatch, core).post(ROUTE, headers=headers, data=raw).status_code == 200
    ordinary, body = proof_request(core, direction='service-to-install', nonce='9' * 32)
    refused = client(kit, monkeypatch, core).post(ROUTE, headers=ordinary, data=body)
    assert refused.status_code == 401 and refused.get_json() == {'error': 'pin_required'}


@pytest.mark.parametrize('case', ['query', 'encoded', 'type'])
def test_route_http_bounds_apply_to_proof_requests_too(kit, monkeypatch, case):
    core = kit.armed()
    headers, raw = proof_request(core)
    url, options = ROUTE, {}
    if case == 'query':
        url += '?x=1'
    elif case == 'encoded':
        options['environ_overrides'] = {'RAW_URI': '/api/agent-mail/%69ngress'}
    else:
        headers['Content-Type'] = 'text/plain'
    assert client(kit, monkeypatch, core).post(url, headers=headers, data=raw, **options).status_code == 400
    assert row(core)['state'] == 'armed'


def test_enrollment_route_has_a_human_gated_proof_action():
    from mc.blueprints import agent_mail_enrollment_routes as routes
    assert 'proof' in routes.ENABLE_ACTIONS


# --- cross-language vectors ----------------------------------------------------------------------
def build_vectors():
    fields = dict(install='install1', generation=1, challenge='ab' * 32, service_nonce='cd' * 16,
                  route_origin=ROUTE_ORIGIN, manifest_sequence=1, proof_id='ef' * 16)
    raw = receipt_bytes(*fields.values())
    service_sha = hashlib.sha256(SVC.public_key().public_bytes_raw()).hexdigest()
    owner_sha = hashlib.sha256(OWNER.encode()).hexdigest()
    body = json.dumps({'kind': 'enrollment-proof', 'version': '1', 'challenge': fields['challenge'],
                       'install_id': 'install1', 'generation': '1', 'manifest_sequence': '1',
                       'service_key_id': 'service1', 'service_public_key_sha256': service_sha,
                       'mail_key_id': 'mail_flow1', 'route_origin': ROUTE_ORIGIN,
                       'owner_address_sha256': owner_sha, 'service_nonce': fields['service_nonce']},
                      separators=(',', ':'))
    return {'_meta': 'Python-built by tests/test_agent_mail_u7d2_proof.py; fixed fixture seeds; public material only.',
            'mail_public_key': b64(MAIL.public_key().public_bytes_raw()),
            'receipt': {'fields': fields, 'array_json': raw.decode(), 'signature': b64(MAIL.sign(raw)),
                        'digest': hashlib.sha256(raw).hexdigest()},
            'body': {'inputs': {'challenge': fields['challenge'], 'install_id': 'install1', 'generation': '1',
                                'key_id': 'mail_flow1', 'route_origin': ROUTE_ORIGIN, 'manifest_sequence': '1',
                                'service_key_id': 'service1', 'service_public_key_sha256': service_sha,
                                'owner_address_sha256': owner_sha, 'service_nonce': fields['service_nonce']},
                     'json': body}}


def test_checked_in_vectors_match_what_python_builds():
    expected = json.dumps(build_vectors(), indent=1, sort_keys=True) + '\n'
    assert VECTORS.read_text(encoding='utf-8').replace('\r\n', '\n') == expected


def test_receipt_array_is_ascii_strings_only():
    array = receipt_array('install1', 1, 'ab' * 32, 'cd' * 16, ROUTE_ORIGIN, 1, 'ef' * 16)
    assert all(isinstance(v, str) and v.isascii() for v in array) and len(array) == 9


def test_python_accepts_the_golden_body_the_worker_builds():
    vectors = build_vectors()
    inputs = vectors['body']['inputs']
    armed = {'install': 'install1', 'generation': 1, 'manifest_sequence': 1, 'challenge': inputs['challenge'],
             'service_key_id': 'service1', 'service_public_key_sha256': inputs['service_public_key_sha256'],
             'mail_key_id': 'mail_flow1', 'route_origin': ROUTE_ORIGIN,
             'owner_address_sha256': inputs['owner_address_sha256']}
    body = DeliveryProof._body(vectors['body']['json'].encode(), armed)
    assert set(body) == BODY_KEYS
