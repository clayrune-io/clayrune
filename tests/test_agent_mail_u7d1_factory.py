"""Unit 7d-1: shared mail factory, external marker, held states, remote refusal.

Synthetic keys/clocks only. No network, no real mail, no generated key.
"""
import json
import sqlite3

import pytest
from flask import Flask

from test_agent_mail_enrollment import (FakeAdapter, MASTER, NOW, ORIGIN, OWNER, PUBLIC, ROOT, SECRET,
                                        SEED, manifest)
from test_agent_mail_ingress import signed
from mc import agent_mail_factory as fac
from mc import agent_mail_ownership as ownership
from mc import state
from mc.agent_mail_enrollment import Enrollment, Prerequisites
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal
from mc.agent_mail_remote import RemoteUnavailable, UnavailableRemote, UnavailableTransport, unavailable_material
from mc.agent_mail_store import DB_FILENAME, MailStore


def factory(tmp_path, **config):
    return fac.MailFactory(tmp_path / 'agent-mail', clock=lambda: NOW / 1000, key=lambda: MASTER,
                           config=config)


def enroll(f):
    """An active-stage flow using fixed seeds, built on the factory's own store."""
    store = f.open_store(create=True)
    calls = []

    def material():
        calls.append(1)
        return SEED, SECRET
    core = Enrollment(store, IdentitySeal(f.paths.identity, key=lambda: MASTER), adapter=FakeAdapter(),
                      material=material, prerequisites=lambda: Prerequisites(True, True, True),
                      install_id='install1', root_public_key=PUBLIC, service_origin=ORIGIN, enabled=True)
    core.start(OWNER, *manifest())
    return core


def held(f):
    with pytest.raises(fac.StoreHeld) as info:
        f.open_store()
    return info.value.reason


def test_never_enrolled_is_an_authoritative_miss_and_creates_nothing(tmp_path):
    f = factory(tmp_path)
    assert f.open_store() is None
    assert f.health() == {'state': 'not_configured', 'reason': 'not_enrolled'}
    with pytest.raises(fac.StoreHeld, match='not_enrolled'):
        f.require_store()
    assert not (tmp_path / 'agent-mail').exists(), 'a read must not create the folder'


def test_create_is_explicit_marker_precedes_ready_and_reopen_is_healthy(tmp_path):
    f = factory(tmp_path)
    store = f.open_store(create=True)
    assert store.path == f.paths.store and store.path.is_file()
    assert json.loads(f.paths.marker.read_text()) == {'version': 1, 'state': 'ready', 'store': DB_FILENAME}
    again = factory(tmp_path)
    assert again.health() == {'state': 'ok', 'reason': ''}
    assert again.require_store().path == f.paths.store


def test_deleted_store_stays_held_and_is_never_recreated(tmp_path):
    f = factory(tmp_path)
    f.open_store(create=True)
    for suffix in ('', '-wal', '-shm'):
        p = f.paths.store.with_name(DB_FILENAME + suffix)
        if p.exists():
            p.unlink()
    for fresh in (f, factory(tmp_path)):
        assert held(fresh) == 'store_missing'
        assert fresh.health() == {'state': 'held', 'reason': 'store_missing'}
    assert not f.paths.store.exists(), 'sqlite must not silently recreate a vanished inbox'


def test_connection_after_the_file_vanishes_is_held_not_recreated(tmp_path):
    f = factory(tmp_path)
    store = f.open_store(create=True)
    f.paths.store.unlink()
    with pytest.raises(fac.StoreHeld, match='store_missing'):
        with store._read():
            pass
    assert not f.paths.store.exists()


def test_store_without_marker_is_held_and_never_adopted(tmp_path):
    f = factory(tmp_path)
    f.paths.root.mkdir(parents=True)
    MailStore(f.paths.store)
    assert held(f) == 'marker_missing'
    assert not f.paths.marker.exists(), 'an unmarked store must not be auto-adopted'


@pytest.mark.parametrize('content,reason', [
    ('not json', 'marker_corrupt'),
    ('[]', 'marker_corrupt'),
    (json.dumps({'version': 1, 'state': 'bogus', 'store': DB_FILENAME}), 'marker_corrupt'),
    (json.dumps({'version': 1, 'state': 'ready', 'store': 'other.sqlite3'}), 'marker_corrupt'),
    (json.dumps({'version': 2, 'state': 'ready', 'store': DB_FILENAME}), 'marker_version'),
])
def test_corrupt_or_version_mismatched_marker_is_held(tmp_path, content, reason):
    f = factory(tmp_path)
    f.open_store(create=True)
    f.paths.marker.write_text(content)
    assert held(factory(tmp_path)) == reason


def test_creating_marker_without_store_means_no_authority_existed(tmp_path):
    f = factory(tmp_path)
    f.paths.root.mkdir(parents=True)
    f.paths.marker.write_text(json.dumps({'version': 1, 'state': 'creating', 'store': DB_FILENAME}))
    assert f.open_store() is None
    assert f.open_store(create=True).path.is_file()
    assert json.loads(f.paths.marker.read_text())['state'] == 'ready'


def test_creating_marker_with_real_authority_is_held(tmp_path):
    f = factory(tmp_path)
    store = f.open_store(create=True)
    store.register('installation')
    f.paths.marker.write_text(json.dumps({'version': 1, 'state': 'creating', 'store': DB_FILENAME}))
    assert held(factory(tmp_path)) == 'marker_inconsistent'


def test_corrupt_store_is_held(tmp_path):
    f = factory(tmp_path)
    f.open_store(create=True)
    f.paths.store.write_bytes(b'this is not sqlite' * 100)
    assert held(factory(tmp_path)) == 'store_corrupt'


def test_newer_schema_store_is_held(tmp_path):
    f = factory(tmp_path)
    f.open_store(create=True)
    with sqlite3.connect(f.paths.store) as db:
        db.execute('PRAGMA user_version=999')
    assert held(factory(tmp_path)) == 'store_version'


def test_missing_identity_seal_keeps_the_store_held(tmp_path):
    f = factory(tmp_path)
    core = enroll(f)
    assert core.status()['public_key']
    assert factory(tmp_path).health() == {'state': 'ok', 'reason': ''}
    for sealed in f.paths.identity.glob('*.sealed'):
        sealed.unlink()
    assert held(factory(tmp_path)) == 'seal_missing'
    assert held(f) == 'seal_missing', 'a cached healthy handle must not mask a lost seal'


def test_nothing_under_the_repo_data_dir_and_home_relative_default(tmp_path):
    root = fac.default_root()
    assert root.name == 'agent-mail'
    import os
    from mc.secrets_store import clayrune_home
    projects = os.path.join(os.environ['MC_DATA_DIR'], 'data', 'projects')
    assert clayrune_home() in root.parents
    assert not str(root).startswith(projects), 'no sidecar may live in the project-records dir'
    assert fac.MailFactory().paths.root == root


def test_legacy_data_dir_inbox_is_never_read_or_migrated(tmp_path, monkeypatch):
    from mc.blueprints.agent_mail_routes import FLAG, ROUTE, create_blueprint
    legacy = tmp_path / 'data' / DB_FILENAME
    legacy.parent.mkdir()
    old = MailStore(legacy)
    old.register('installation')
    before = legacy.read_bytes()
    fac.reset_for_tests(factory(tmp_path))
    monkeypatch.setitem(state.CONFIG, FLAG, True)
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(legacy.parent / 'projects'))
    try:
        headers, raw = signed(ROOT)
        result = app.test_client().post(ROUTE, headers=headers, data=raw)
    finally:
        fac.reset_for_tests(None)
    assert result.status_code == 503
    assert legacy.read_bytes() == before
    assert not (tmp_path / 'agent-mail').exists()


def test_deployment_identity_comes_only_from_config(tmp_path):
    f = factory(tmp_path)
    f.open_store(create=True)
    with pytest.raises(fac.StoreHeld, match='deployment_unconfigured'):
        f.enrollment()
    f2 = factory(tmp_path, agent_mail_install_id='install1', agent_mail_root_public_key=PUBLIC,
                 agent_mail_service_origin=ORIGIN)
    core = f2.enrollment()
    with pytest.raises(EnrollmentRefused, match='enrollment_disabled'):
        core.start(OWNER, *manifest())  # default off: no lifecycle write, no remote call


def test_outbound_composition_is_default_off_and_cached_per_flag_state(tmp_path):
    cfg = dict(agent_mail_install_id='install1', agent_mail_root_public_key=PUBLIC,
               agent_mail_service_origin=ORIGIN)
    f = factory(tmp_path, **cfg)
    f.open_store(create=True)
    off = f.outbound()
    assert off is f.outbound()
    with pytest.raises(EnrollmentRefused, match='outbound_disabled'):
        off.prepare('p', 's', dict(request_id='r', kind='note', subject='s', body='b'))
    f._config['agent_mail_outbound_enabled'] = True
    on = f.outbound()
    assert on is not off and on is f.outbound()


def test_remote_adapters_never_acknowledge_and_never_call_out():
    remote = UnavailableRemote()
    for call in (lambda: remote.begin({}, b''), lambda: remote.verify({}, b'', 'x'),
                 lambda: remote.cancel_flow('flow'), lambda: remote.revoke('i', 1),
                 lambda: UnavailableTransport().send('o', '/p', {}, b''), unavailable_material):
        with pytest.raises(EnrollmentRefused) as info:
            call()
        assert info.value.args and info.value.args[0] in ('remote_adapter_unavailable', 'key_material_unavailable')
    assert issubclass(RemoteUnavailable, EnrollmentRefused)


# --- ownership reads -------------------------------------------------------------
def make_owned(tmp_path):
    from mc.agent_mail_outbound_state import initialize
    f = factory(tmp_path)
    store = f.open_store(create=True)
    initialize(store)
    with store._tx() as db:
        db.execute("INSERT INTO mail_outbound_question_channels VALUES('p','s','rev1','agent_mail',1)")
        db.execute("INSERT INTO mail_outbound_question_channels VALUES('p','s','rev2','legacy',1)")
    return f, store


def test_lookup_distinguishes_miss_from_unavailable(tmp_path):
    f = factory(tmp_path)
    assert ownership.lookup(f, 'p', 's', 'r') is None, 'never enrolled is an authoritative miss'
    store = f.open_store(create=True)
    assert ownership.lookup(f, 'p', 's', 'r') is None, 'no outbound table is an authoritative miss'
    f2, store2 = make_owned(tmp_path / 'x')
    assert ownership.lookup(f2, 'p', 's', 'rev1') == 'agent_mail'
    assert ownership.lookup(f2, 'p', 's', 'rev2') == 'legacy'
    assert ownership.lookup(f2, 'p', 's', 'rev3') is None
    f2.paths.store.write_bytes(b'garbage' * 200)
    with pytest.raises(ownership.OwnershipUnavailable):
        ownership.lookup(factory(tmp_path / 'x'), 'p', 's', 'rev1')
    for suffix in ('-wal', '-shm'):
        p = f2.paths.store.with_name(DB_FILENAME + suffix)
        p.exists() and p.unlink()
    f2.paths.store.unlink()
    with pytest.raises(ownership.OwnershipUnavailable) as info:
        ownership.lookup(f2, 'p', 's', 'rev1')
    assert info.value.reason == 'store_missing'


def test_capacity_defaults_and_overrides():
    assert ownership.DEFAULT_CAPACITY == 2400
    assert ownership.capacity_from({}) == 2400
    assert ownership.capacity_from({ownership.CAPACITY_KEY: 24000}) == 24000
    for bad in (0, -1, True, '5', 1.5, None):
        assert ownership.capacity_from({ownership.CAPACITY_KEY: bad}) == 2400


def test_receipts_are_not_aged_out_by_cleanup_or_reconcile(tmp_path, monkeypatch):
    from mc.agent_mail_outbound import Outbound, cleanup
    f, store = make_owned(tmp_path)
    cleanup(store)
    assert ownership.count(store) == 2, 'enable-independent cleanup must not release owner tombstones'
    core = enroll(factory(tmp_path / 'y'))
    outbound = Outbound(core, transport=UnavailableTransport(), enabled=True, key=lambda: MASTER)
    from mc.agent_mail_outbound_state import initialize
    initialize(core.store)
    with core.store._tx() as db:
        db.execute("INSERT INTO mail_outbound_question_channels VALUES('p','s','old','agent_mail',1)")
    outbound.reconcile()
    with core.store._read() as db:
        assert db.execute('SELECT COUNT(*) FROM mail_outbound_question_channels').fetchone()[0] == 1


def test_capacity_holds_new_admissions_and_never_evicts(tmp_path, monkeypatch):
    from mc.agent_mail_outbound import Outbound
    f = factory(tmp_path)
    core = enroll(f)
    outbound = Outbound(core, transport=UnavailableTransport(), enabled=True, key=lambda: MASTER)
    from mc.agent_mail_outbound_state import initialize
    initialize(core.store)
    monkeypatch.setitem(state.CONFIG, ownership.CAPACITY_KEY, 2)
    q1, q2, q3 = (dict(questions=[dict(question=n)]) for n in ('one', 'two', 'three'))
    assert outbound.question_channel('p', 's', q1, 'agent_mail') == 'agent_mail'
    assert outbound.question_channel('p', 's', q2, 'legacy') == 'legacy'
    with pytest.raises(ownership.OwnershipHeld) as info:
        outbound.question_channel('p', 's', q3, 'agent_mail')
    assert info.value.reason == 'ownership_capacity'
    assert ownership.count(core.store) == 2, 'at capacity nothing is evicted and nothing is added'
    # an EXISTING owner is still answered at capacity, whatever is preferred now
    assert outbound.question_channel('p', 's', q1, 'legacy') == 'agent_mail'
    monkeypatch.setitem(state.CONFIG, ownership.CAPACITY_KEY, 3)
    assert outbound.question_channel('p', 's', q3, 'agent_mail') == 'agent_mail'
    assert ownership.count(core.store) == 3
