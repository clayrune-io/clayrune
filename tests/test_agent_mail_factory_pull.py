"""Unit 22: checked bootstrap and lazy pull composition, synthetic stores only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import subprocess
import sqlite3
import sys

import pytest

from mc import agent_mail_factory as fac
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_pull_schema import validate
from mc.agent_mail_service_client import HttpsTransport
from test_agent_mail_signup import Harness, MASTER, NOW, ORIGIN, OWNER


def factory(tmp_path, **kwargs):
    return fac.MailFactory(tmp_path / 'mail', clock=lambda: NOW / 1000,
                           key=lambda: MASTER, config={fac.ORIGIN_KEY: ORIGIN}, **kwargs)


def populated(tmp_path):
    # Reuse independently signed signup responses; all factory components use
    # the factory's checked store, never the fixture's independently opened DB.
    h = Harness(tmp_path / 'fake')
    f = factory(tmp_path, pull_transport=h.transport,
                reviewed_service=lambda: (h.pin, h.manifest_digest))
    h.store = f.bootstrap(authorize=lambda: True)
    h.seal = f.seal()
    result = f.signup(authorize=lambda: True).start(OWNER, 'v1')
    assert result['state'] == 'pending', result
    return f, h, result['install_id']


def test_construct_and_all_runtime_accessors_never_bootstrap_or_start(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('constructor/runtime miss must not perform external work')
    monkeypatch.setattr(fac.PullClient, 'start', forbidden)
    monkeypatch.setattr(fac.HttpsTransport, '__call__', forbidden)
    f = factory(tmp_path)
    assert f.health() == {'state': 'not_configured', 'reason': 'not_enrolled'}
    assert f.seal() is f.seal()
    for action in (f.require_pull_store, lambda: f.signup(authorize=lambda: True),
                   lambda: f.owner_change(authorize=lambda: True), f.service_client, f.pull_client,
                   lambda: f.pull_receiver(generation=1, owner_generation=1, epoch=1)):
        with pytest.raises(fac.StoreHeld, match='not_enrolled'):
            action()
    assert not f.paths.root.exists()


def test_import_and_default_singleton_have_no_io_or_thread_start(tmp_path):
    code = '''
import sys, threading
def forbidden(*args, **kwargs):
    raise AssertionError('unexpected thread')
threading.Thread.start = forbidden
def audit(event, args):
    if event in {'socket.connect', 'socket.bind', 'subprocess.Popen', 'sqlite3.connect'}:
        raise AssertionError(event)
sys.addaudithook(audit)
from mc.agent_mail_factory import get
assert get() is get()
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('allowed', [False, None, 1, 'yes'])
def test_bootstrap_requires_exact_human_gate_before_any_file(tmp_path, allowed):
    f = factory(tmp_path)
    with pytest.raises(EnrollmentRefused, match='human_required'):
        f.bootstrap(authorize=lambda: allowed)
    assert not f.paths.root.exists()


def test_bootstrap_authorization_exception_is_closed_and_writes_nothing(tmp_path):
    f = factory(tmp_path)
    def broken():
        raise RuntimeError('not for logs')
    with pytest.raises(EnrollmentRefused, match='^human_required$'):
        f.bootstrap(authorize=broken)
    assert not f.paths.root.exists()


def test_bootstrap_creates_exact_extension_without_identity_or_configuration(tmp_path):
    f = factory(tmp_path)
    original = dict(f.config)
    store = f.bootstrap(authorize=lambda: True)
    validate(store)
    assert store is f.require_pull_store() is f.bootstrap(authorize=lambda: True)
    assert json.loads(f.paths.marker.read_text())['state'] == 'ready'
    assert f.config == original and not f.paths.identity.exists()
    with store._read() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute('SELECT count(*) FROM mail_pull_accounts').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM registrations').fetchone()[0] == 0
    assert factory(tmp_path).require_pull_store().path == store.path


def test_existing_legacy_store_requires_explicit_additive_bootstrap(tmp_path):
    f = factory(tmp_path)
    store = f.open_store(create=True)
    store.register('legacy-install')
    with pytest.raises(fac.StoreHeld, match='schema_corrupt'):
        f.require_pull_store()
    with store._read() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_pull_version'").fetchone()
    assert f.bootstrap(authorize=lambda: True) is store
    with store._read() as db:
        assert db.execute('SELECT count(*) FROM registrations').fetchone()[0] == 1
        assert not db.execute('SELECT 1 FROM mail_pull_accounts').fetchone()


@pytest.mark.parametrize('damage,reason', [
    ('missing', 'store_missing'), ('unmarked', 'marker_missing'),
    ('marker', 'marker_corrupt'), ('foreign', 'store_corrupt'),
    ('newer', 'store_version'), ('partial', 'schema_corrupt'),
    ('version', 'schema_corrupt'), ('index', 'schema_corrupt'),
])
def test_bootstrap_and_runtime_never_repair_held_store(tmp_path, damage, reason):
    f = factory(tmp_path)
    f.bootstrap(authorize=lambda: True)
    if damage == 'missing':
        f.paths.store.unlink()
    elif damage == 'unmarked':
        f.paths.marker.unlink()
    elif damage == 'marker':
        f.paths.marker.write_text('bad json')
    elif damage == 'foreign':
        f.paths.store.write_bytes(b'not SQLite' * 100)
    else:
        with sqlite3.connect(f.paths.store) as db:
            db.execute({'newer': 'PRAGMA user_version=999',
                        'partial': 'DROP TABLE mail_pull_ack_outbox',
                        'version': 'DELETE FROM mail_pull_version',
                        'index': 'DROP INDEX mail_pull_fence_due'}[damage])
    before = f.paths.store.read_bytes() if f.paths.store.exists() else None
    for fresh in (f, factory(tmp_path)):
        for action in (fresh.require_pull_store, lambda: fresh.bootstrap(authorize=lambda: True)):
            with pytest.raises(fac.StoreHeld) as exc:
                action()
            assert exc.value.reason == reason
    assert (f.paths.store.read_bytes() if f.paths.store.exists() else None) == before


def test_creating_marker_with_pull_authority_stays_held(tmp_path):
    f, h, _ = populated(tmp_path)
    f._write_marker('creating')
    for action in (f.open_store, lambda: f.bootstrap(authorize=lambda: True)):
        with pytest.raises(fac.StoreHeld, match='marker_inconsistent'):
            action()
    assert json.loads(f.paths.marker.read_text())['state'] == 'creating'
    assert h.calls


def test_creating_marker_with_partial_empty_extension_cannot_be_finalized(tmp_path):
    f = factory(tmp_path)
    f.bootstrap(authorize=lambda: True)
    with f.require_store()._tx() as db:
        db.execute('DROP TABLE mail_pull_ack_outbox')
    f._write_marker('creating')
    with pytest.raises(fac.StoreHeld, match='schema_corrupt'):
        factory(tmp_path).open_store()
    assert json.loads(f.paths.marker.read_text())['state'] == 'creating'


def test_failed_extension_commit_is_observable_and_no_partial_objects(tmp_path, monkeypatch):
    f = factory(tmp_path)
    store = f.open_store(create=True)
    original = fac.initialize_pull
    def fail(store):
        with store._tx() as db:
            db.execute('CREATE TABLE mail_pull_partial(x)')
            raise OSError('synthetic write failure')
    monkeypatch.setattr(fac, 'initialize_pull', fail)
    with pytest.raises(fac.StoreHeld, match='store_unavailable'):
        f.bootstrap(authorize=lambda: True)
    with store._read() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name GLOB 'mail_pull_*'").fetchone()
    monkeypatch.setattr(fac, 'initialize_pull', original)
    assert f.bootstrap(authorize=lambda: True) is store


def test_composition_shares_custody_seal_clock_and_durable_install(tmp_path, monkeypatch):
    f, h, install = populated(tmp_path)
    calls = list(h.calls)
    monkeypatch.setattr(fac.PullClient, 'start', lambda _: pytest.fail('started'))
    # Config's old identity is never pull authority.
    f.config[fac.INSTALL_KEY] = 'legacy-identity'
    components = [f.signup(authorize=lambda: False), f.owner_change(authorize=lambda: False),
                  f.service_client(), f.pull_client(),
                  f.pull_receiver(generation=1, owner_generation=1, epoch=2)]
    assert all(c.store is h.store for c in components)
    assert all(c.seal is f.seal() for c in components[:-1])
    assert all(c.install_id == install for c in components[1:])
    assert components[-1].epoch == 2
    assert all(c.reviewed_service == f.reviewed_service for c in components)
    assert components[0].clock_ms() == NOW == components[1].clock_ms()
    assert components[2].clock() == NOW / 1000
    assert not components[3].running and components[3]._socket is None
    assert h.calls == calls
    with ThreadPoolExecutor(max_workers=4) as executor:
        assert all(client is components[3] for client in executor.map(lambda _: f.pull_client(), range(8)))


def test_signup_human_gate_is_per_adapter_and_rechecked(tmp_path):
    f, h, _ = populated(tmp_path)
    allowed = True
    signup = f.signup(authorize=lambda: allowed)
    calls = list(h.calls)
    allowed = False
    assert signup.start(OWNER, 'v1')['reason'] == 'human_required'
    assert f.signup(authorize=lambda: False).start(OWNER, 'v1')['reason'] == 'human_required'
    assert f.owner_change(authorize=lambda: False).start('a' * 32)['reason'] == 'human_required'
    assert h.calls == calls


def test_reviewed_service_is_revalidated_per_use_and_origin_bound(tmp_path):
    f, h, _ = populated(tmp_path)
    calls = []
    def review():
        calls.append(1)
        return h.pin, h.manifest_digest
    f._reviewed_service = review
    assert f.reviewed_service() == f.reviewed_service() == (h.pin, h.manifest_digest)
    assert calls == [1, 1]
    h.pin = replace(h.pin, origin='https://other.example.com')
    with pytest.raises(EnrollmentRefused, match='service_key_changed'):
        f.reviewed_service()
    f._reviewed_service = None
    with pytest.raises(EnrollmentRefused, match='service_key_changed'):
        f.reviewed_service()


def test_empty_store_has_no_service_identity_or_implicit_config_identity(tmp_path):
    f = factory(tmp_path)
    f.bootstrap(authorize=lambda: True)
    f.config[fac.INSTALL_KEY] = 'f' * 32
    for action in (f.service_client, f.pull_client, lambda: f.owner_change(authorize=lambda: True)):
        with pytest.raises(fac.StoreHeld, match='not_enrolled'):
            action()
    signup = f.signup(authorize=lambda: True)
    assert isinstance(signup.transport, HttpsTransport)
    assert signup.transport.origin == ORIGIN
    assert signup.start(OWNER, 'v1')['reason'] == 'service_key_changed'
    assert not f.paths.identity.exists()


def test_committed_missing_seal_stays_held_on_cached_fresh_and_bootstrap(tmp_path):
    f, h, _ = populated(tmp_path)
    f.pull_client()
    for seal in f.paths.identity.glob('*.sealed'):
        seal.unlink()
    calls = list(h.calls)
    for fresh in (f, factory(tmp_path)):
        for action in (fresh.require_pull_store, fresh.pull_client,
                       lambda: fresh.bootstrap(authorize=lambda: True)):
            with pytest.raises(fac.StoreHeld, match='seal_missing'):
                action()
    assert not list(f.paths.identity.glob('*.sealed')) and h.calls == calls


def test_locked_vault_does_not_hold_store_or_construct_signing_material(tmp_path):
    f, _, _ = populated(tmp_path)
    f.key = lambda: None
    # A fresh composition uses the live locked-key callback, not a retained key.
    locked = fac.MailFactory(f.paths.root, clock=f.clock, key=lambda: None, config=f.config)
    assert locked.require_pull_store().path == f.paths.store
    assert locked.service_client().seal is locked.seal()
    assert locked.signup(authorize=lambda: False).status()['reason'] == 'vault_locked'


def test_durable_preparation_without_seal_is_recoverable_only_before_public_key(tmp_path, monkeypatch):
    h = Harness(tmp_path / 'fake')
    f = factory(tmp_path, pull_transport=h.transport,
                reviewed_service=lambda: (h.pin, h.manifest_digest))
    h.store, h.seal = f.bootstrap(authorize=lambda: True), f.seal()
    original = h.seal.generate_pull
    class Crash(BaseException):
        pass
    def crash(*args, **kwargs):
        raise Crash()
    monkeypatch.setattr(h.seal, 'generate_pull', crash)
    with pytest.raises(Crash):
        f.signup(authorize=lambda: True).start(OWNER, 'v1')
    assert not h.calls and not f.paths.identity.exists()
    assert factory(tmp_path).require_pull_store().path == h.store.path
    monkeypatch.setattr(h.seal, 'generate_pull', original)
    assert f.signup(authorize=lambda: False).resume()['state'] == 'pending'
    assert h.calls and list(f.paths.identity.glob('*.sealed'))


def test_client_never_silently_replaced_after_custody_identity_changes(tmp_path):
    f, _, _ = populated(tmp_path)
    client = f.pull_client()
    client.install_id = 'f' * 32
    with pytest.raises(fac.StoreHeld, match='client_recomposition_required'):
        f.pull_client()
    assert f._pull_client is client


def test_client_origin_changes_require_owner_recomposition(tmp_path):
    f, h, _ = populated(tmp_path)
    client = f.pull_client()
    f.config[fac.ORIGIN_KEY] = 'https://new.example.com'
    with h.store._tx() as db:
        db.execute('UPDATE mail_pull_accounts SET service_origin=?', (f.config[fac.ORIGIN_KEY],))
    with pytest.raises(fac.StoreHeld, match='client_recomposition_required'):
        f.pull_client()
    assert f._pull_client is client


def test_runtime_rechecks_cached_schema_and_origin_before_returning_client(tmp_path):
    f, h, _ = populated(tmp_path)
    client = f.pull_client()
    f.config[fac.ORIGIN_KEY] = 'https://other.example.com'
    with pytest.raises(fac.StoreHeld, match='schema_corrupt'):
        f.pull_client()
    f.config[fac.ORIGIN_KEY] = ORIGIN
    with h.store._tx() as db:
        db.execute('DROP INDEX mail_pull_receipt_expiry')
    with pytest.raises(fac.StoreHeld, match='schema_corrupt'):
        f.pull_client()
    assert f._pull_client is client
