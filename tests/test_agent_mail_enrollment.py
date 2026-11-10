"""Unit 7b synthetic lifecycle. Fixed fixture seeds; no generated/live keys."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import subprocess
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from mc.agent_mail_enrollment import Enrollment, OwnershipReceipt, PendingIdentity, Prerequisites
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal, b64
from mc.agent_mail_enrollment_revocations import CancelledAssignment
from mc.agent_mail_enrollment_state import ManifestCAS, initialize
from mc.agent_mail_ingress import MailIngress
from mc.agent_mail_service_manifest import ManifestRefused, ManifestState
from mc.agent_mail_store import MailStore, MailStoreCorrupt, MailStoreError

NOW = 1794249600000
OWNER = 'owner@example.test'
ORIGIN = 'https://mail.example.test'
ROOT = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
PUBLIC = b64(ROOT.public_key().public_bytes_raw())
SEED = bytes(range(32, 64))
SECRET = b'f' * 32
MASTER = b'm' * 32
TOKEN = 'opaque-thread-token'


def manifest(sequence=1, public=PUBLIC):
    payload = ['clayrune-mail-service-manifest', '1', ORIGIN, str(sequence), str(NOW - 1000),
               str(NOW + 100000), 'service1', public, str(NOW - 500), str(NOW + 200000), []]
    raw = json.dumps(payload, separators=(',', ':')).encode()
    return payload, b64(ROOT.sign(raw))


class FakeAdapter:
    def __init__(self):
        self.begins, self.verifies, self.revokes = [], [], []
        self.fail_begin = self.fail_verify = self.fail_revoke = False
        self.begin_hook = self.verify_hook = lambda: None
        self.receipt_hook = lambda value: value
        self.pending_hook = lambda value: value
        self.assignments, self.cancelled, self.cancels = {}, set(), []
        self.fail_cancel = False

    def begin(self, context, secret):
        self.begins.append((context.copy(), secret))
        candidate = json.loads(context['candidate'])
        pending = self.pending_hook(PendingIdentity(context['flow'], context['install'], context['generation'],
                                    context['owner'], context['public_key'], candidate['fingerprint']))
        if context['flow'] in self.cancelled:
            raise OSError('flow cancelled')
        if not self.fail_begin:
            self.assignments[context['flow']] = CancelledAssignment(pending.install, pending.generation)
        self.begin_hook()
        if self.fail_begin:
            raise OSError('untrusted remote secret must never escape')
        return pending

    def verify(self, context, secret, code):
        self.verifies.append((context.copy(), secret, code))
        self.verify_hook()
        if self.fail_verify:
            raise OSError('untrusted remote secret must never escape')
        candidate = json.loads(context['candidate'])
        return self.receipt_hook(OwnershipReceipt(context['flow'], context['install'], context['generation'],
                                context['owner'], context['public_key'], candidate['fingerprint'], NOW + 300000))

    def revoke(self, install, generation):
        self.revokes.append((install, generation))
        if self.fail_revoke:
            raise OSError('untrusted remote secret must never escape')

    def cancel_flow(self, flow):
        self.cancels.append(flow)
        if self.fail_cancel:
            raise OSError('untrusted remote secret must never escape')
        self.cancelled.add(flow)
        return self.assignments.get(flow)


@pytest.fixture
def kit(tmp_path):
    class Kit:
        def __init__(self):
            self.adapter = FakeAdapter()
            self.master = MASTER
            self.now = NOW
            self.ready = Prerequisites(True, True, True)
            self.creations = 0
            self.path = tmp_path / 'outside-repo' / 'mail.sqlite3'
            self.folder = tmp_path / 'outside-repo' / 'identities'

        def material(self):
            self.creations += 1
            return SEED if self.creations == 1 else bytes([self.creations]) * 32, SECRET

        def core(self, enabled=True):
            return Enrollment(MailStore(self.path, clock=lambda: self.now / 1000),
                              IdentitySeal(self.folder, key=lambda: self.master), adapter=self.adapter,
                              material=self.material, prerequisites=lambda: self.ready, install_id='install1',
                              root_public_key=PUBLIC, service_origin=ORIGIN, enabled=enabled)

        def pending(self):
            core = self.core()
            core.start(OWNER, *manifest())
            core.confirm(core.review())
            core.verify('copying-code')
            return core

        def active(self):
            core = self.pending()
            core.enable()
            return core
    return Kit()


def test_disabled_no_lifecycle_writes(kit):
    core = kit.core(enabled=False)
    with pytest.raises(EnrollmentRefused, match='enrollment_disabled'):
        core.start(OWNER, *manifest())
    with core.store._read() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'mail_enrollment_%'").fetchone()
    assert kit.creations == 0 and kit.adapter.begins == []


def test_durable_intent_before_remote_and_restart_reuses_identity(kit):
    core = kit.core()
    def hook():
        other = kit.core()
        assert other.status()['stage'] == 'pending'
        assert other.status()['public_key']
        assert not other.status()['enabled']
    kit.adapter.begin_hook = hook
    core.start(OWNER, *manifest())
    first = core.status()
    kit.core().start(OWNER, *manifest())
    kit.core().resume()
    assert kit.creations == 1
    assert kit.core().status() == first
    assert len({ctx['flow'] for ctx, _ in kit.adapter.begins}) == 1
    assert all(secret == SECRET for _, secret in kit.adapter.begins)


@pytest.mark.parametrize('missing', ['tunnel', 'claude_extraction', 'machine_custody'])
def test_ownership_alone_stays_delivery_pending_and_current_prereqs_required(kit, missing):
    core = kit.pending()
    assert core.status()['stage'] == 'delivery_pending' and not core.status()['enabled']
    kit.ready = replace(kit.ready, **{missing: False})
    with pytest.raises(EnrollmentRefused, match='prerequisites_pending'):
        core.enable()
    assert core.store.registration_state('install1')['enabled'] == 0
    with core.store._read() as db:
        assert not db.execute('SELECT * FROM mail_service_pins').fetchone()
    kit.ready = Prerequisites(True, True, True)
    assert core.enable()['enabled']


def test_exact_human_confirmation_before_pin_and_ownership(kit):
    core = kit.core()
    core.start(OWNER, *manifest())
    disclosure = core.review()
    for field in disclosure:
        forged = dict(disclosure, **{field: 'forged'})
        with pytest.raises(EnrollmentRefused, match='confirmation_mismatch'):
            core.confirm(forged)
    with pytest.raises(EnrollmentRefused, match='confirmation_required'):
        core.verify('code')
    with pytest.raises(EnrollmentRefused, match='ownership_required'):
        core.enable()
    assert kit.adapter.verifies == []
    with core.store._read() as db:
        assert not db.execute('SELECT * FROM mail_service_pins').fetchone()


@pytest.mark.parametrize('field', ['flow', 'install', 'generation', 'owner', 'public_key', 'service_fingerprint'])
def test_remote_receipt_cannot_cross_scope(kit, field):
    core = kit.core()
    core.start(OWNER, *manifest())
    core.confirm(core.review())
    kit.adapter.receipt_hook = lambda receipt: replace(receipt, **{field: 'wrong'})
    with pytest.raises(EnrollmentRefused, match='ownership_scope_mismatch'):
        core.verify('code')
    assert core.status()['stage'] == 'pending'


@pytest.mark.parametrize('master', [None, b'x' * 32])
def test_vault_locked_or_replaced_refuses_no_silent_rekey(kit, master):
    core = kit.pending()
    old_files = {p.name: p.read_bytes() for p in kit.folder.iterdir()}
    kit.master = master
    for operation in (core.resume, core.enable, core.review):
        with pytest.raises(EnrollmentRefused):
            operation()
    assert {p.name: p.read_bytes() for p in kit.folder.iterdir()} == old_files
    assert kit.creations == 1
    assert not core.status()['enabled']
    kit.master = MASTER  # ordinary passphrase rewrap preserves the master key
    assert core.enable()['enabled']


def test_first_enrollment_locked_preserves_intent_without_material(kit):
    kit.master = None
    core = kit.core()
    with pytest.raises(EnrollmentRefused, match='vault_locked_or_unconfigured'):
        core.start(OWNER, *manifest())
    assert core.status()['stage'] == 'preparing' and kit.creations == 0
    assert not kit.folder.exists() and kit.adapter.begins == []
    kit.master = MASTER
    assert kit.core().resume()['stage'] == 'pending'
    assert kit.creations == 1


@pytest.mark.parametrize('damage', ['empty', 'json', 'ciphertext', 'missing', 'version'])
def test_corrupt_or_lost_seal_never_regenerates(kit, damage):
    core = kit.pending()
    path = next(kit.folder.glob('*.sealed'))
    if damage == 'missing':
        path.unlink()
    elif damage in {'empty', 'json'}:
        path.write_text('' if damage == 'empty' else '{}')
    else:
        value = json.loads(path.read_text())
        value[damage] = 7 if damage == 'version' else b64(b'z' * 80)
        path.write_text(json.dumps(value))
    with pytest.raises(EnrollmentRefused, match='identity_unavailable'):
        kit.core().resume()
    assert kit.creations == 1


def test_seed_never_persisted_plaintext_or_returned_in_status(kit):
    core = kit.pending()
    for path in kit.path.parent.rglob('*'):
        if path.is_file():
            raw = path.read_bytes()
            assert SEED not in raw and SECRET not in raw
            assert b64(SEED).encode() not in raw and b64(SECRET).encode() not in raw
    assert not {'flow', 'owner', 'code', 'seed', 'secret'} & core.status().keys()
    row = kit.adapter.begins[0][0]
    with pytest.raises(EnrollmentRefused, match='identity_unavailable'):
        core.seal.public(row['flow'], row['install'], row['generation'] + 1, row['key_id'])


def test_seal_write_failure_no_remote_no_success_and_retry(kit, monkeypatch):
    from mc import agent_mail_enrollment_keys as keys
    original = keys.write_json_atomic
    def fail(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr(keys, 'write_json_atomic', fail)
    core = kit.core()
    with pytest.raises(OSError):
        core.start(OWNER, *manifest())
    assert core.status()['stage'] == 'preparing' and not kit.adapter.begins
    monkeypatch.setattr(keys, 'write_json_atomic', original)
    assert core.resume()['stage'] == 'pending'


def test_seal_committed_before_db_failure_is_reopened_not_recreated(kit):
    core = kit.core()
    with core.store._tx() as db:
        db.execute("CREATE TRIGGER fail_public BEFORE UPDATE OF public_key ON mail_enrollment_flow BEGIN SELECT RAISE(ABORT,'write fault'); END")
    with pytest.raises(MailStoreError):
        core.start(OWNER, *manifest())
    assert kit.creations == 1 and not kit.adapter.begins
    assert len(list(kit.folder.glob('*.sealed'))) == 1
    with core.store._tx() as db:
        db.execute('DROP TRIGGER fail_public')
    assert kit.core().resume()['stage'] == 'pending'
    assert kit.creations == 1


@pytest.mark.parametrize('operation', ['begin', 'verify', 'revoke'])
def test_remote_failure_has_closed_reason_and_durable_retry(kit, operation):
    core = kit.core()
    if operation == 'begin':
        kit.adapter.fail_begin = True
        with pytest.raises(EnrollmentRefused, match='remote_begin_retryable'):
            core.start(OWNER, *manifest())
        kit.adapter.fail_begin = False
        assert kit.core().resume()['stage'] == 'pending'
    elif operation == 'verify':
        core.start(OWNER, *manifest())
        core.confirm(core.review())
        kit.adapter.fail_verify = True
        with pytest.raises(EnrollmentRefused, match='remote_verify_retryable'):
            core.verify('code')
        kit.adapter.fail_verify = False
        assert kit.core().verify('code')['stage'] == 'delivery_pending'
    else:
        core = kit.active()
        core.disable()
        kit.adapter.fail_revoke = True
        with pytest.raises(EnrollmentRefused, match='remote_revoke_retryable'):
            core.retry_revocations()
        assert kit.core().status()['revocations_pending'] == 1
        kit.adapter.fail_revoke = False
        assert kit.core().retry_revocations()['revocations_pending'] == 0
        assert kit.core().retry_revocations()['revocations_pending'] == 0
        assert len(kit.adapter.revokes) == 2  # failure and successful retry only


def test_disable_atomic_closes_authority_revokes_events_and_queues_remote(kit):
    core = kit.active()
    core.store.bind_thread('install1', TOKEN, 'p', 's')
    core.store.insert_event(event_id='event1', registration_id='install1', thread_token=TOKEN,
                            message_id='mid1', body='private body')
    result = core.disable()
    assert result['stage'] == 'disabled' and not result['enabled'] and result['revocations_pending'] == 1
    with core.store._read() as db:
        event = db.execute('SELECT state,body FROM events').fetchone()
        assert tuple(event) == ('revoked', None)
        assert db.execute('SELECT revoked_at FROM thread_bindings').fetchone()[0] is not None
    assert core.store.registration_state('install1')['generation'] == 2
    assert not kit.adapter.revokes  # immediate local close precedes remote work
    core.disable()
    assert core.store.registration_state('install1')['generation'] == 2


def test_disable_write_failure_rolls_back_authority_and_revocation(kit):
    core = kit.active()
    with core.store._tx() as db:
        db.execute("CREATE TRIGGER fail_disable BEFORE UPDATE ON registrations BEGIN SELECT RAISE(ABORT,'fault'); END")
    with pytest.raises(MailStoreError):
        core.disable()
    assert core.status()['enabled'] and core.status()['revocations_pending'] == 0


def test_pin_failure_rolls_back_activation(kit, monkeypatch):
    core = kit.pending()
    def fail(self, pin, **kwargs):
        raise OSError('write fault')
    monkeypatch.setattr(MailIngress, 'pin_service', fail)
    with pytest.raises(OSError):
        core.enable()
    assert not core.status()['enabled'] and core.status()['stage'] == 'delivery_pending'


@pytest.mark.parametrize('operation', ['begin', 'verify'])
def test_remote_response_racing_disable_never_reenables(kit, operation):
    core = kit.core()
    if operation == 'verify':
        core.start(OWNER, *manifest())
        core.confirm(core.review())
    setattr(kit.adapter, operation + '_hook', lambda: kit.core().disable())
    with pytest.raises(EnrollmentRefused, match='lifecycle_raced'):
        if operation == 'begin':
            core.start(OWNER, *manifest())
        else:
            core.verify('code')
    assert not kit.core().status()['enabled']
    assert kit.core().status()['revocations_pending'] == 1


def test_concurrent_reopen_has_one_identity_across_store_instances(kit):
    core = kit.core()
    kit.master = None
    with pytest.raises(EnrollmentRefused):
        core.start(OWNER, *manifest())
    kit.master = MASTER
    cores = [kit.core() for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda c: c.resume(), cores))
    assert all(r['public_key'] == results[0]['public_key'] for r in results)
    assert kit.creations == 1


@pytest.mark.parametrize('change', ['owner', 'key', 'repin'])
def test_identity_changes_require_revocation_and_do_not_adopt_old_threads(kit, change):
    core = kit.active()
    old_key = core.status()['public_key']
    core.store.bind_thread('install1', TOKEN, 'p', 's')
    new_owner = 'other@example.test' if change == 'owner' else OWNER
    if change != 'key':
        with pytest.raises(EnrollmentRefused, match='disable_before_identity_change'):
            core.start(new_owner, *manifest(sequence=2 if change == 'repin' else 1))
    # Even identical synthetic seed does not let old flow/generation be adopted.
    result = core.change_identity(new_owner, *manifest(sequence=2 if change == 'repin' else 1))
    assert result['generation'] == 2 and result['stage'] == 'pending' and not result['enabled']
    assert kit.adapter.revokes == [('install1', 1)]
    assert kit.creations == 2
    assert len({ctx['flow'] for ctx, _ in kit.adapter.begins}) == 2
    core.confirm(core.review())
    core.verify('new-code')
    core.enable()
    assert core.status()['public_key'] != old_key
    with core.store._read() as db:
        assert core.store._validate_target(db, 'install1', hashlib.sha256(TOKEN.encode()).hexdigest(), kit.now / 1000)
        assert len(db.execute('SELECT * FROM mail_service_pins').fetchall()) == 2


def test_new_ceremony_waits_for_remote_revocation(kit):
    core = kit.active()
    core.disable()
    with pytest.raises(EnrollmentRefused, match='revocation_pending'):
        core.start(OWNER, *manifest())
    assert kit.creations == 1


def test_vault_replacement_recovery_is_only_new_human_ceremony(kit):
    core = kit.active()
    kit.master = b'n' * 32
    with pytest.raises(EnrollmentRefused, match='identity_unavailable'):
        core.resume()
    assert core.change_identity(OWNER, *manifest())['generation'] == 2
    assert kit.creations == 2 and kit.adapter.revokes == [('install1', 1)]


def test_manifest_cas_restart_rollback_conflict_and_race(kit):
    core = kit.core()
    cas = core.manifests
    first = ManifestState(1, 'a' * 64)
    second = ManifestState(2, 'b' * 64)
    assert cas.compare_and_set(ORIGIN, None, first)
    assert kit.core().manifests.load(ORIGIN) == first
    assert not kit.core().manifests.compare_and_set(ORIGIN, None, second)
    assert cas.compare_and_set(ORIGIN, first, second)
    for value in (first, ManifestState(2, 'c' * 64)):
        with pytest.raises(EnrollmentRefused, match='manifest_rollback'):
            cas.compare_and_set(ORIGIN, second, value)


def test_concurrent_manifest_cas_one_winner(kit):
    cores = [kit.core(), kit.core()]
    barrier = threading.Barrier(2)
    def update(pair):
        n, core = pair
        old = core.manifests.load(ORIGIN)
        barrier.wait()
        return core.manifests.compare_and_set(ORIGIN, old, ManifestState(n + 1, str(n) * 64))
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, enumerate(cores))) == [False, True]


def test_corrupt_manifest_floor_no_reset(kit):
    core = kit.core()
    with core.store._tx() as db:
        db.execute('INSERT INTO mail_enrollment_manifests VALUES(?,1,?)', (ORIGIN, 'broken'))
    with pytest.raises(EnrollmentRefused, match='manifest_state_corrupt'):
        kit.core().start(OWNER, *manifest())
    assert kit.creations == 0


def test_schema_marker_missing_or_corrupt_refused(kit):
    core = kit.core()
    with core.store._tx() as db:
        db.execute('DELETE FROM mail_enrollment_version')
    with pytest.raises(EnrollmentRefused, match='lifecycle_schema_corrupt'):
        kit.core()


def test_corrupt_db_never_replaced(kit):
    kit.core()
    kit.path.write_bytes(b'corrupt')
    with pytest.raises(MailStoreCorrupt):
        kit.core()
    assert kit.path.read_bytes() == b'corrupt'


def test_expired_or_newer_manifest_cannot_activate_old_confirmation(kit):
    core = kit.pending()
    kit.now = NOW + 100001
    with pytest.raises(EnrollmentRefused, match='manifest_stale'):
        core.enable()
    kit.now = NOW
    newer = kit.core()
    with pytest.raises(EnrollmentRefused, match='disable_before_identity_change'):
        newer.start(OWNER, *manifest(2))
    with pytest.raises(EnrollmentRefused, match='manifest_stale'):
        core.enable()


def test_service_manifest_bad_signature_never_changes_pin(kit):
    core = kit.core()
    payload, _ = manifest()
    with pytest.raises(ManifestRefused, match='manifest_signature'):
        core.start(OWNER, payload, b64(b'0' * 64))
    assert core.status()['stage'] == 'not_enrolled' and kit.creations == 0


@pytest.mark.parametrize('expiry', [NOW - 1, NOW, NOW + 300001, True, 'never'])
def test_expired_or_unbounded_ownership_receipt_refused(kit, expiry):
    core = kit.core()
    core.start(OWNER, *manifest())
    core.confirm(core.review())
    kit.adapter.receipt_hook = lambda value: replace(value, expires_at_ms=expiry)
    with pytest.raises(EnrollmentRefused, match='ownership_expired'):
        core.verify('code')
    assert core.status()['stage'] == 'pending' and not core.status()['enabled']


def test_ownership_lease_rechecked_after_slow_pin_write(kit, monkeypatch):
    core = kit.pending()
    original = MailIngress.pin_service
    def slow_pin(self, pin, **kwargs):
        result = original(self, pin, **kwargs)
        kit.now = NOW + 300001
        return result
    monkeypatch.setattr(MailIngress, 'pin_service', slow_pin)
    with pytest.raises(EnrollmentRefused, match='ownership_expired'):
        core.enable()
    assert not core.status()['enabled']
    with core.store._read() as db:
        assert not db.execute('SELECT * FROM mail_service_pins').fetchone()


def test_superseded_remote_receipt_cannot_adopt_new_generation(kit):
    core = kit.core()
    core.start(OWNER, *manifest())
    core.confirm(core.review())
    kit.adapter.verify_hook = lambda: kit.core().change_identity('new@example.test', *manifest())
    with pytest.raises(EnrollmentRefused, match='lifecycle_raced'):
        core.verify('code')
    assert core.status()['generation'] == 2 and core.status()['stage'] == 'pending'
    with core.store._read() as db:
        row = db.execute('SELECT ownership,confirmed FROM mail_enrollment_flow').fetchone()
        assert tuple(row) == (0, 0)


def test_enable_and_disable_serialize_across_independent_connections(kit):
    core = kit.pending()
    other = kit.core()
    entered, release = threading.Event(), threading.Event()
    def blocking_probe():
        entered.set()
        assert release.wait(5)
        return kit.ready
    core.prerequisites = blocking_probe
    with ThreadPoolExecutor(max_workers=2) as pool:
        enabling = pool.submit(core.enable)
        assert entered.wait(5)
        disabling = pool.submit(other.disable)
        release.set()
        enabling.result(timeout=5)
        disabling.result(timeout=5)
    assert not core.status()['enabled'] and core.status()['stage'] == 'disabled'
    assert core.status()['revocations_pending'] == 1


def test_cross_process_manifest_cas_observes_committed_winner(kit):
    core = kit.core()
    code = '''import sys
from mc.agent_mail_store import MailStore
from mc.agent_mail_enrollment_state import ManifestCAS
from mc.agent_mail_service_manifest import ManifestState
cas=ManifestCAS(MailStore(sys.argv[1]))
print(cas.compare_and_set('https://mail.example.test', None, ManifestState(1, 'a'*64)))
'''
    result = subprocess.run([sys.executable, '-c', code, str(kit.path)], capture_output=True,
                            text=True, timeout=20, cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'True'
    assert not core.manifests.compare_and_set(ORIGIN, None, ManifestState(2, 'b' * 64))


def test_external_revoke_prevents_remote_retry_before_signing(kit):
    core = kit.pending()
    core.store.revoke_registration('install1')
    count = len(kit.adapter.begins)
    with pytest.raises(EnrollmentRefused, match='generation_changed'):
        core.resume()
    assert len(kit.adapter.begins) == count


def test_service_selected_install_persists_before_human_confirmation(kit):
    core = kit.core()
    kit.adapter.pending_hook = lambda value: replace(value, install='opaque_service_install')
    assert core.start(OWNER, *manifest())['stage'] == 'pending'
    assert kit.core().review()['install'] == 'opaque_service_install'
    assert kit.creations == 1
    kit.core().resume()
    core.confirm(core.review())
    core.verify('code')
    assert core.enable()['enabled']
    assert core.store.registration_state('opaque_service_install')['enabled']
    assert not core.store.registration_state('install1')['enabled']
    assert core.disable()['revocations_pending'] == 1
    core.retry_revocations()
    assert kit.adapter.revokes == [('opaque_service_install', 1)]


def test_failed_service_begin_cannot_be_confirmed_as_local_reservation(kit):
    core = kit.core()
    kit.adapter.fail_begin = True
    with pytest.raises(EnrollmentRefused, match='remote_begin_retryable'):
        core.start(OWNER, *manifest())
    with pytest.raises(EnrollmentRefused, match='service_reservation_pending'):
        core.review()
    with pytest.raises(EnrollmentRefused, match='service_reservation_pending'):
        core.confirm({})


def test_retry_cannot_change_persisted_service_identity(kit):
    core = kit.core()
    core.start(OWNER, *manifest())
    kit.adapter.pending_hook = lambda value: replace(value, install='replacement')
    with pytest.raises(EnrollmentRefused, match='pending_identity_changed'):
        core.resume()
    assert kit.core().review()['install'] == 'install1'
    assert kit.creations == 1


def test_manifest_cas_write_fault_propagates_without_identity_or_remote(kit):
    core = kit.core()
    with core.store._tx() as db:
        db.execute("CREATE TRIGGER fail_manifest BEFORE INSERT ON mail_enrollment_manifests BEGIN SELECT RAISE(ABORT,'fault'); END")
    with pytest.raises(MailStoreError):
        core.start(OWNER, *manifest())
    assert core.manifests.load(ORIGIN) is None
    assert not kit.adapter.begins and kit.creations == 0


def test_full_http_ceremony_uses_human_gate_and_never_returns_secrets(kit, monkeypatch):
    from flask import Flask
    from mc import state
    from mc.blueprints import agent_mail_enrollment_routes as routes, local_auth
    monkeypatch.setitem(state.CONFIG, routes.FLAG, True)
    monkeypatch.setattr(state, 'agent_sessions', {})
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: True)
    proofs = []
    def proof(value):
        proofs.append(value)
        return value == 'fixture-human'
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', proof)
    app = Flask(__name__)
    app.register_blueprint(routes.create_blueprint(factory=kit.core))
    client = app.test_client()
    def post(action, **value):
        result = client.post(routes.BASE + '/' + action, json={'passcode': 'fixture-human', **value})
        assert result.status_code == 200, result.json
        assert result.headers['Cache-Control'] == 'no-store'
        assert not {'flow', 'seed', 'code', 'flow_secret', 'secret', 'passcode'} & result.json.keys()
        assert b64(SEED) not in result.text and b64(SECRET) not in result.text
        return result.json
    payload, signature = manifest()
    post('start', owner=OWNER, manifest=payload, signature=signature)
    disclosure = post('review')
    post('confirm', confirmation=disclosure)
    assert post('verify', code='private-copying-code')['stage'] == 'delivery_pending'
    assert post('enable')['enabled']
    assert client.get(routes.BASE).json['enabled']
    assert not post('disable')['enabled']
    post('retry-revocations')
    for action in ('owner', 'key', 'repin'):
        post(action, owner=OWNER, manifest=payload, signature=signature)
    assert proofs == ['fixture-human'] * 10


def test_adapter_cannot_smuggle_free_text_through_refusal_class(kit):
    core = kit.core()
    def injected():
        raise EnrollmentRefused('private secret must never appear')
    kit.adapter.begin_hook = injected
    with pytest.raises(EnrollmentRefused, match='^remote_begin_retryable$'):
        core.start(OWNER, *manifest())


def test_material_failure_is_redacted_and_never_publishes_identity(kit):
    core = kit.core()
    def injected():
        raise ValueError('private seed must never appear')
    core.material = injected
    with pytest.raises(EnrollmentRefused, match='^identity_material_unavailable$'):
        core.start(OWNER, *manifest())
    assert not kit.adapter.begins and core.status()['stage'] == 'preparing'
