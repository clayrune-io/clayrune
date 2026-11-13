"""Q1-A exact review storage, guarded custody and unchanged legacy floor.

Synthetic public evidence only, temporary external databases, no service calls.
Signature/current-authority decisions belong to W06, not these schema tests.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_enrollment_state import ManifestCAS, initialize as initialize_enrollment
from mc.agent_mail_factory import GuardedMailStore, StoreHeld
from mc.agent_mail_pull_schema import initialize as initialize_pull, validate as validate_pull
from mc.agent_mail_service_manifest import ManifestState, canonical_manifest_bytes
from mc.agent_mail_service_review_schema import ServiceReviewSchemaCorrupt, VERSION, initialize, validate
from mc.agent_mail_store import APPLICATION_ID, MailStoreError

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (ROOT / 'docs/AGENT_MAIL_PULL_CONTRACT.md').read_text(encoding='utf-8')
FROZEN = CONTRACT.split('### 13.1 Q1-A:', 1)[1].split('```sql\n', 1)[1].split('```', 1)[0]
FLOOR = '''CREATE TABLE mail_enrollment_manifests (
                origin TEXT PRIMARY KEY, sequence INTEGER NOT NULL, digest TEXT NOT NULL)'''
ORIGIN = 'https://mail.example.com'
OP = 'a' * 32
PUBLIC = base64.urlsafe_b64encode(b'p' * 32).rstrip(b'=').decode('ascii')
ROOT_KEY = base64.urlsafe_b64encode(b'r' * 32).rstrip(b'=').decode('ascii')
SIGNATURE = base64.urlsafe_b64encode(b's' * 64).rstrip(b'=').decode('ascii')
MANIFEST = ['clayrune-mail-service-manifest', '1', ORIGIN, '7', '1800000000000',
            '1800000100000', 'service-key', PUBLIC, '1800000000000', '1800000200000', []]
MANIFEST_JSON = canonical_manifest_bytes(MANIFEST).decode('ascii')
DIGEST = hashlib.sha256(MANIFEST_JSON.encode('ascii')).hexdigest()
FINGERPRINT = hashlib.sha256(b'p' * 32).hexdigest()


@pytest.fixture
def store(tmp_path):
    return GuardedMailStore(tmp_path / 'agent_mail.sqlite3', creating=True)


@pytest.fixture
def installed(store):
    initialize(store)
    return store


def snapshot(store):
    with store._read() as db:
        schema = [tuple(row) for row in db.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name')]
        rows = {name: [tuple(row) for row in db.execute('SELECT * FROM "' + name.replace('"', '""') + '"')]
                for kind, name, _, _ in schema if kind == 'table'}
        return schema, rows


def objects(db):
    return [(kind, name, table, ' '.join(sql.split()) if sql else None)
            for kind, name, table, sql in db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE lower(name) GLOB 'mail_service_review_*' OR lower(tbl_name) GLOB 'mail_service_review_*' "
                "OR lower(name)='mail_enrollment_manifests' OR lower(tbl_name)='mail_enrollment_manifests' ORDER BY name")]


def populate(store):
    # One atomic representative W06-shaped commit. This does not claim review
    # authority: deliberately synthetic signature, no current clock/config.
    with store._tx() as db:
        db.execute('INSERT INTO mail_enrollment_manifests VALUES(?,?,?)', (ORIGIN, 7, DIGEST))
        db.execute('INSERT INTO mail_service_review_acceptance VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (OP, 1, ORIGIN, ROOT_KEY, MANIFEST_JSON, SIGNATURE, 7, DIGEST,
                    'service-key', PUBLIC, FINGERPRINT, 1800000000001, 1800000100000))
        db.execute('INSERT INTO mail_service_review_operations VALUES(?,?,?,?,?,?,?,?,?)',
                   (OP, 'c' * 64, 1, DIGEST, ORIGIN, ROOT_KEY, FINGERPRINT, 1800000000001, 1800000100000))


def refused_unchanged(store):
    before = snapshot(store)
    for action in (initialize, validate):
        with pytest.raises(ServiceReviewSchemaCorrupt, match='^schema_corrupt$'):
            action(store)
        assert snapshot(store) == before


def test_first_bootstrap_is_exact_frozen_sql_without_dormant_enrollment(store):
    initialize(store)
    with sqlite3.connect(':memory:') as expected, store._read() as actual:
        expected.executescript(FROZEN)
        expected.execute(FLOOR)
        assert objects(actual) == objects(expected)
        assert actual.execute('SELECT * FROM mail_service_review_version').fetchall()[0][:] == (1, VERSION)
        assert actual.execute('SELECT COUNT(*) FROM mail_service_review_acceptance').fetchone()[0] == 0
        assert actual.execute('SELECT COUNT(*) FROM mail_service_review_operations').fetchone()[0] == 0
        assert actual.execute('PRAGMA application_id').fetchone()[0] == APPLICATION_ID
        assert actual.execute('PRAGMA user_version').fetchone()[0] == 3
        assert actual.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert actual.execute('PRAGMA synchronous').fetchone()[0] == 2
        assert not actual.execute("SELECT 1 FROM sqlite_master WHERE name IN "
                                  "('mail_enrollment_flow','mail_enrollment_version','mail_enrollment_revocations')").fetchall()
    validate(store)


def test_reopen_noop_preserves_complete_legacy_and_pull_custody(store):
    initialize_enrollment(store)
    initialize_pull(store)
    store.register('legacy')
    store.bind_thread('legacy', 'thread', 'project', 'session')
    result = store.insert_event(event_id='legacy-event', registration_id='legacy', thread_token='thread',
                                message_id='legacy@example.com', body='held reply')
    assert result.status == 'accepted'
    with store._tx() as db:
        db.execute("UPDATE events SET state='uncertain',reason='unknown_submission'")
        db.execute('UPDATE registrations SET enabled=0')
        db.execute('INSERT INTO mail_enrollment_revocations VALUES(?,?,?,0,0)', ('flow', 'install', 1))
    floor = ManifestCAS(store)
    old = ManifestState(9, 'd' * 64)
    assert floor.compare_and_set(ORIGIN, None, old)
    before_schema, before_rows = snapshot(store)
    initialize(store)
    after_schema, after_rows = snapshot(store)
    assert set(before_schema) <= set(after_schema)
    assert all(after_rows[name] == values for name, values in before_rows.items())
    populate_other_origin = 'https://other.example.com'
    assert floor.compare_and_set(populate_other_origin, None, ManifestState(2, 'e' * 64))
    before = snapshot(store)
    reopened = GuardedMailStore(store.path)
    initialize(reopened)
    validate(reopened)
    validate_pull(reopened)
    assert snapshot(reopened) == before
    assert ManifestCAS(reopened).load(ORIGIN) == old


def test_installed_review_and_inert_historical_receipts_survive_reopen(installed):
    populate(installed)
    with installed._tx() as db:
        # An old operation can have an unrelated root/origin and expired time;
        # it remains replay evidence, never a selectable acceptance.
        db.execute('INSERT INTO mail_service_review_operations VALUES(?,?,?,?,?,?,?,?,?)',
                   ('b' * 32, 'd' * 64, 2, 'e' * 64, 'https://old.example.com', PUBLIC,
                    'f' * 64, 1, 2))
    # Higher floor invalidates authority in W06, not historical storage in W05.
    assert ManifestCAS(installed).compare_and_set(ORIGIN, ManifestState(7, DIGEST), ManifestState(8, 'f' * 64))
    before = snapshot(installed)
    reopened = GuardedMailStore(installed.path)
    initialize(reopened)
    validate(reopened)
    assert snapshot(reopened) == before


@pytest.mark.parametrize('ddl', [
    'CREATE TABLE mail_service_review_version(slot INTEGER)',
    'CREATE TABLE MAIL_SERVICE_REVIEW_FUTURE(value TEXT)',
    'CREATE VIEW mail_service_review_acceptance AS SELECT 1 AS slot',
    'CREATE INDEX mail_service_review_foreign ON registrations(registration_id)',
])
def test_partial_unknown_extension_refused_without_even_creating_floor(store, ddl):
    with store._tx() as db:
        db.execute(ddl)
    refused_unchanged(store)


@pytest.mark.parametrize('target', ['mail_service_review_version', 'mail_service_review_acceptance',
                                   'mail_service_review_operations', 'mail_enrollment_manifests'])
def test_each_missing_table_is_refused_without_repair(installed, target):
    with installed._tx() as db:
        db.execute('DROP TABLE ' + target)
    refused_unchanged(installed)


@pytest.mark.parametrize('ddl', [
    'ALTER TABLE mail_service_review_acceptance ADD COLUMN surprise TEXT',
    'CREATE INDEX arbitrary_name ON mail_service_review_operations(input_digest)',
    'CREATE TRIGGER arbitrary_name AFTER INSERT ON mail_service_review_operations BEGIN SELECT 1; END',
    'CREATE TABLE mail_service_review_future(value TEXT)',
    'CREATE VIEW mail_service_review_future AS SELECT 1',
    'CREATE INDEX arbitrary_floor_index ON mail_enrollment_manifests(sequence)',
    'CREATE TRIGGER arbitrary_floor_trigger AFTER INSERT ON mail_enrollment_manifests BEGIN SELECT 1; END',
    'ALTER TABLE mail_enrollment_manifests ADD COLUMN root TEXT',
])
def test_extra_objects_or_columns_refused(installed, ddl):
    with installed._tx() as db:
        db.execute(ddl)
    refused_unchanged(installed)


@pytest.mark.parametrize('old,new', [
    ('CHECK(version=1)', 'CHECK(version>=1)'),
    ('op_id TEXT NOT NULL UNIQUE', 'op_id TEXT NOT NULL'),
    ('op_id TEXT NOT NULL PRIMARY KEY', 'op_id TEXT NOT NULL'),
    ('CHECK(slot=1)', 'CHECK(slot IN (1,2))'),
    ('expires_ms>accepted_ms', 'expires_ms>=accepted_ms'),
    ('manifest_sequence INTEGER', 'manifest_sequence TEXT'),
])
def test_wrong_constraints_or_implicit_index_sets_refused(store, old, new):
    assert old in FROZEN
    with store._tx() as db:
        db.execute(FLOOR)
        for sql in FROZEN.replace(old, new).split(';'):
            if sql.strip():
                db.execute(sql)
    refused_unchanged(store)


@pytest.mark.parametrize('marker', [[], [(1, 0)], [(1, 2)], [(2, 1)], [(1, 1), (2, 1)]])
def test_wrong_or_missing_marker_refused(installed, marker):
    with installed._tx() as db:
        db.execute('PRAGMA ignore_check_constraints=ON')
        db.execute('DELETE FROM mail_service_review_version')
        db.executemany('INSERT INTO mail_service_review_version VALUES(?,?)', marker)
    refused_unchanged(installed)


@pytest.mark.parametrize('ddl', [
    'CREATE TABLE mail_enrollment_manifests(origin TEXT,sequence INTEGER,digest TEXT)',
    'CREATE TABLE mail_enrollment_manifests(origin TEXT PRIMARY KEY,sequence TEXT NOT NULL,digest TEXT NOT NULL)',
    'CREATE VIEW mail_enrollment_manifests AS SELECT 1 AS origin',
    'CREATE TABLE mail_enrollment_flow(slot INTEGER)',
    'CREATE TABLE mail_enrollment_version(version INTEGER)',
])
def test_wrong_or_lost_legacy_floor_never_repaired(store, ddl):
    with store._tx() as db:
        db.execute(ddl)
    refused_unchanged(store)


@pytest.mark.parametrize('origin,sequence,digest', [
    (ORIGIN, 0, 'c' * 64), (ORIGIN, 1.5, 'c' * 64), (ORIGIN, 1, 'C' * 64),
    (ORIGIN, 1, b'c' * 64), (None, 1, 'c' * 64), ('http://mail.example.com', 1, 'c' * 64),
])
def test_corrupt_floor_refused_without_installed_review(store, origin, sequence, digest):
    with store._tx() as db:
        db.execute(FLOOR)
        db.execute('INSERT INTO mail_enrollment_manifests VALUES(?,?,?)', (origin, sequence, digest))
    refused_unchanged(store)


@pytest.mark.parametrize('table,column,value', [
    ('acceptance', 'slot', 2), ('acceptance', 'op_id', 'A' * 32),
    ('acceptance', 'trust_revision', 1.5), ('acceptance', 'trust_revision', 10**15),
    ('acceptance', 'service_origin', ORIGIN + '/'), ('acceptance', 'root_public_key', PUBLIC + '='),
    ('acceptance', 'manifest_json', '[]'), ('acceptance', 'manifest_json', MANIFEST_JSON + ' '),
    ('acceptance', 'manifest_json', json.dumps(MANIFEST)),
    ('acceptance', 'signature', 'x' * 86), ('acceptance', 'manifest_sequence', 0),
    ('acceptance', 'manifest_digest', 'g' * 64), ('acceptance', 'service_key_id', 'not a key'),
    ('acceptance', 'service_public_key', 'x' * 43), ('acceptance', 'service_fingerprint', 'F' * 64),
    ('acceptance', 'accepted_ms', 0), ('acceptance', 'expires_ms', 1800000000001),
    ('operations', 'op_id', 'Z' * 32), ('operations', 'input_digest', 'q' * 64),
    ('operations', 'manifest_digest', 'G' * 64), ('operations', 'trust_revision', 1.5),
    ('operations', 'service_origin', 'https://MAIL.example.com'),
    ('operations', 'root_public_key', PUBLIC + '='), ('operations', 'service_fingerprint', 'Q' * 64),
    ('operations', 'accepted_ms', 1.5), ('operations', 'expires_ms', 9_007_199_254_740_992),
])
def test_bad_stored_shapes_never_validate_or_repair(installed, table, column, value):
    populate(installed)
    with installed._tx() as db:
        db.execute('PRAGMA ignore_check_constraints=ON')
        db.execute(f'UPDATE mail_service_review_{table} SET {column}=?', (value,))
    refused_unchanged(installed)


@pytest.mark.parametrize('mutation', [
    'DELETE FROM mail_service_review_operations',
    'DELETE FROM mail_service_review_acceptance',
    'DELETE FROM mail_enrollment_manifests',
    'UPDATE mail_service_review_operations SET trust_revision=2',
    'UPDATE mail_service_review_operations SET accepted_ms=1800000000002',
    "UPDATE mail_service_review_operations SET root_public_key='" + PUBLIC + "'",
])
def test_incomplete_or_mismatched_confirmation_graph_refused(installed, mutation):
    populate(installed)
    with installed._tx() as db:
        db.execute(mutation)
    refused_unchanged(installed)


def test_duplicate_acceptance_singleton_refused(installed):
    populate(installed)
    with installed._tx() as db:
        db.execute('PRAGMA ignore_check_constraints=ON')
        columns = [row[1] for row in db.execute('PRAGMA table_info(mail_service_review_acceptance)')]
        values = list(db.execute('SELECT * FROM mail_service_review_acceptance').fetchone())
        values[columns.index('slot')] = 2
        values[columns.index('op_id')] = 'b' * 32
        db.execute('INSERT INTO mail_service_review_acceptance VALUES(' + ','.join('?' for _ in values) + ')', values)
    refused_unchanged(installed)


@pytest.mark.parametrize('pragma', ['application_id=0', 'user_version=2', 'user_version=4'])
def test_wrong_base_identity_never_upgraded(store, pragma):
    with store._tx() as db:
        db.execute('PRAGMA ' + pragma)
    refused_unchanged(store)


def test_runtime_never_bootstraps_any_absent_object(store):
    before = snapshot(store)
    with pytest.raises(ServiceReviewSchemaCorrupt):
        validate(store)
    assert snapshot(store) == before


@pytest.mark.parametrize('preexisting_floor', [False, True])
@pytest.mark.parametrize('failure', ['floor', 'ddl', 'marker', 'commit'])
def test_atomic_install_rollback_preserves_floor_and_allows_explicit_retry(store, monkeypatch, preexisting_floor, failure):
    if preexisting_floor:
        initialize_enrollment(store)
        assert ManifestCAS(store).compare_and_set(ORIGIN, None, ManifestState(9, 'd' * 64))
    before = snapshot(store)
    original = store._connect

    def connect():
        db = original()

        def authorize(action, first, second, *_):
            target = 'mail_enrollment_manifests' if failure == 'floor' else 'mail_service_review_operations'
            if ((failure in ('floor', 'ddl') and action == sqlite3.SQLITE_CREATE_TABLE and first == target)
                    or (failure == 'marker' and action == sqlite3.SQLITE_INSERT and first == 'mail_service_review_version')
                    or (failure == 'commit' and action == sqlite3.SQLITE_TRANSACTION and first == 'COMMIT')):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorize)
        return db

    with monkeypatch.context() as context:
        context.setattr(store, '_connect', connect)
        if failure == 'floor' and preexisting_floor:
            # Existing floor never touched, even when CREATE would be denied.
            initialize(store)
            validate(store)
            assert ManifestCAS(store).load(ORIGIN) == ManifestState(9, 'd' * 64)
            return
        with pytest.raises(MailStoreError):
            initialize(store)
    assert snapshot(store) == before
    initialize(store)
    validate(store)


def test_existing_floor_is_immutable_and_cas_semantics_unchanged(installed):
    populate(installed)
    cas = ManifestCAS(installed)
    state = ManifestState(7, DIGEST)
    before = snapshot(installed)
    initialize(installed)
    validate(installed)
    assert snapshot(installed) == before
    assert cas.load(ORIGIN) == state
    for invalid in (ManifestState(6, DIGEST), ManifestState(7, 'e' * 64)):
        with pytest.raises(EnrollmentRefused, match='manifest_rollback'):
            cas.compare_and_set(ORIGIN, state, invalid)
        assert cas.load(ORIGIN) == state
    assert cas.compare_and_set(ORIGIN, state, ManifestState(8, 'f' * 64))
    assert not cas.compare_and_set(ORIGIN, state, state)
    initialize(installed)
    assert cas.load(ORIGIN) == ManifestState(8, 'f' * 64)


def test_bootstrap_serializes_across_independent_store_instances(store):
    second = GuardedMailStore(store.path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(initialize, item) for item in (store, second)]
        assert [future.result() for future in results] == [None, None]
    validate(store)


def test_writes_share_transaction_and_validation_is_read_only(store, monkeypatch):
    statements = []
    original = store._connect

    def connect():
        db = original()
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, '_connect', connect)
    initialize(store)
    assert statements[0] == 'BEGIN IMMEDIATE' and statements[-1] == 'COMMIT'
    for action, start, end in ((initialize, 'BEGIN IMMEDIATE', 'COMMIT'), (validate, 'BEGIN', 'ROLLBACK')):
        statements.clear()
        action(store)
        assert statements[0] == start and statements[-1] == end
        assert not any(sql.startswith(('CREATE', 'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'DROP')) for sql in statements)


def test_vanished_guarded_database_is_not_recreated(installed):
    installed.path.unlink()
    for action in (initialize, validate):
        with pytest.raises(StoreHeld, match='store_missing'):
            action(installed)
        assert not installed.path.exists()
