"""Frozen local SQL, additive custody, fail-closed opens and actual Signup.

All stores/seals are synthetic and temporary. No factory wiring, service,
provider, socket, real vault, config changes or live mail are exercised.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import pytest

from mc.agent_mail_enrollment_keys import IdentitySeal
from mc.agent_mail_enrollment_state import initialize as initialize_enrollment
from mc.agent_mail_factory import GuardedMailStore, StoreHeld
from mc.agent_mail_pull_schema import PullSchemaCorrupt, VERSION, initialize, validate
from mc.agent_mail_store import APPLICATION_ID, MailStoreCorrupt, MailStoreError
from test_agent_mail_signup import Harness, OWNER, ADDRESS, MASTER

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = (ROOT / 'docs/AGENT_MAIL_PULL_CONTRACT.md').read_text(encoding='utf-8')
FROZEN_SQL = CONTRACT.split('## 9. NEW local schema:', 1)[1].split('```sql\n', 1)[1].split('```', 1)[0]


def objects(db):
    return [tuple(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE lower(name) GLOB 'mail_pull_*' OR lower(tbl_name) GLOB 'mail_pull_*' ORDER BY name")]


def snapshot(store):
    with store._read() as db:
        schema = [tuple(row) for row in db.execute(
            'SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name')]
        tables = [row[1] for row in schema if row[0] == 'table']
        rows = {name: [tuple(row) for row in db.execute('SELECT * FROM "' + name + '"')]
                for name in tables}
        return schema, rows


@pytest.fixture
def store(tmp_path):
    return GuardedMailStore(tmp_path / 'agent_mail.sqlite3', creating=True)


@pytest.fixture
def installed(store):
    initialize(store)
    return store


def test_fresh_exact_frozen_sql_and_base_identity_unchanged(store):
    initialize(store)
    with sqlite3.connect(':memory:') as expected, store._read() as actual:
        expected.executescript(FROZEN_SQL)
        normalize = lambda rows: [(kind, name, table, ' '.join(sql.split()) if sql else None)
                                  for kind, name, table, sql in rows]
        assert normalize(objects(actual)) == normalize(objects(expected))
        assert actual.execute('PRAGMA application_id').fetchone()[0] == APPLICATION_ID
        assert actual.execute('PRAGMA user_version').fetchone()[0] == 3
        assert actual.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
        assert actual.execute('PRAGMA synchronous').fetchone()[0] == 2
        assert [tuple(row) for row in actual.execute('SELECT * FROM mail_pull_version')] == [(1, VERSION)]
    validate(store)


def test_upgrade_preserves_legacy_enrollment_disabled_and_uncertain_custody(store):
    initialize_enrollment(store)
    store.register('legacy')
    store.bind_thread('legacy', 'token', 'project', 'session')
    result = store.insert_event(event_id='old-event', registration_id='legacy', thread_token='token',
                                message_id='old@example.com', body='held reply')
    assert result.status == 'accepted'
    with store._tx() as db:
        db.execute("UPDATE events SET state='uncertain',reason='unknown_submission'")
        db.execute('UPDATE registrations SET enabled=0')
    before_schema, before_rows = snapshot(store)
    initialize(store)
    after_schema, after_rows = snapshot(store)
    assert set(before_schema) <= set(after_schema)
    assert all(after_rows[table] == rows for table, rows in before_rows.items())
    assert all(not rows for name, rows in after_rows.items()
               if name.startswith('mail_pull_') and name != 'mail_pull_version')
    validate(store)


def populate(store):
    """Complete custody graph, including receipt/outbox and held cancellation."""
    with store._tx() as db:
        db.execute("""INSERT INTO mail_pull_accounts VALUES(
            ?,1,1,'https://mail.example.com','public@example.com','owner@example.com',
            'key',?,'held',0,'v1',1,'vault_locked',1000)""", ('a' * 32, 'b' * 32))
        db.execute("""INSERT INTO mail_pull_generations VALUES(
            ?,1,1,'key',?,1,?,'service','public','held',1000,NULL)""",
                   ('a' * 32, 'b' * 32, 'c' * 64))
        db.execute("""INSERT INTO mail_pull_flows VALUES(
            ?,?,'signup',1,1,'key',?,?,1000,1,'held',2000,'vault_locked','{}')""",
                   ('b' * 32, 'a' * 32, 'b' * 32, 'd' * 64))
        db.execute("""INSERT INTO mail_pull_threads VALUES(
            ?,1,1,'thread',?,'project','session',?,9000,10000,'revoked')""",
                   ('a' * 32, 'e' * 64, 'f' * 64))
        db.execute("""INSERT INTO mail_pull_receipts VALUES(
            ?,?,1,1,'thread',1,?,?,?,'local-receipt','local-event',1000,9000,10000,
            1001,NULL,NULL,'local_received')""",
                   ('0' * 64, 'a' * 32, '1' * 64, '2' * 64, '3' * 64))
        db.execute("""INSERT INTO mail_pull_ack_outbox VALUES(
            ?,'local-receipt','lease',?,?,'held',2,2000,1000,'vault_locked')""",
                   ('0' * 64, '2' * 64, '3' * 64))
        db.execute("""INSERT INTO mail_pull_fences VALUES(
            ?,?,1,?,?,'revoke','pending',NULL,1000,2000)""",
                   ('4' * 32, 'a' * 32, 'b' * 32, '5' * 64))
        db.execute("""INSERT INTO mail_pull_connections VALUES(
            ?,1,1,2,NULL,NULL,NULL,1,0,1000,2000,3,'connection_lost')""", ('a' * 32,))
        db.execute("INSERT INTO mail_pull_status VALUES(?,1000,1,1,'{}',?)", ('a' * 32, '6' * 64))


def test_rerun_and_reopen_are_no_op_and_preserve_every_pull_row(installed):
    populate(installed)
    before = snapshot(installed)
    with installed._read() as db:
        schema_version = db.execute('PRAGMA schema_version').fetchone()[0]
    initialize(installed)
    reopened = GuardedMailStore(installed.path)
    initialize(reopened)
    validate(reopened)
    assert snapshot(reopened) == before
    with reopened._read() as db:
        assert db.execute('PRAGMA schema_version').fetchone()[0] == schema_version


@pytest.mark.parametrize('entry', [line.split()[2] for line in FROZEN_SQL.splitlines()
                                if line.startswith('CREATE TABLE ')] + [
    'mail_pull_one_enabled', 'mail_pull_current', 'mail_pull_flow_due',
    'mail_pull_thread_conversation', 'mail_pull_receipt_expiry', 'mail_pull_receipt_order',
    'mail_pull_ack_due', 'mail_pull_fence_due'])
def test_every_lost_object_is_refused_without_repair(installed, entry):
    with installed._tx() as db:
        kind = db.execute('SELECT type FROM sqlite_master WHERE name=?', (entry,)).fetchone()[0]
        db.execute('DROP ' + kind + ' ' + entry)
    before = snapshot(installed)
    for operation in (initialize, validate):
        with pytest.raises(PullSchemaCorrupt, match='^schema_corrupt$'):
            operation(installed)
        assert snapshot(installed) == before


@pytest.mark.parametrize('ddl', [
    'CREATE TABLE mail_pull_accounts(install_id TEXT)',
    'CREATE TABLE MAIL_PULL_FUTURE(value TEXT)',
    'CREATE VIEW mail_pull_accounts AS SELECT 1 AS install_id',
    'CREATE INDEX mail_pull_foreign ON registrations(registration_id)',
])
def test_partial_or_unknown_namespace_never_becomes_an_empty_extension(store, ddl):
    with store._tx() as db:
        db.execute(ddl)
    before = snapshot(store)
    with pytest.raises(PullSchemaCorrupt):
        initialize(store)
    assert snapshot(store) == before


@pytest.mark.parametrize('ddl', [
    'ALTER TABLE mail_pull_accounts ADD COLUMN surprise TEXT',
    'CREATE INDEX arbitrary_name ON mail_pull_receipts(body_digest)',
    'CREATE TRIGGER arbitrary_name AFTER INSERT ON mail_pull_accounts BEGIN SELECT 1; END',
    'CREATE TABLE mail_pull_future(value TEXT)',
    'CREATE VIEW mail_pull_future AS SELECT 1',
])
def test_extra_columns_indexes_triggers_views_and_tables_are_refused(installed, ddl):
    with installed._tx() as db:
        db.execute(ddl)
    before = snapshot(installed)
    with pytest.raises(PullSchemaCorrupt):
        initialize(installed)
    with pytest.raises(PullSchemaCorrupt):
        validate(installed)
    assert snapshot(installed) == before


@pytest.mark.parametrize('old,new', [
    ('CHECK(version=1)', 'CHECK(version>=1)'),
    ('CHECK(local_enabled IN (0,1))', 'CHECK(local_enabled IN (0,1,2))'),
    ('local_receipt_id TEXT NOT NULL UNIQUE', 'local_receipt_id TEXT NOT NULL'),
    ("WHERE state='current'", "WHERE state='held'"),
    ('REFERENCES mail_pull_accounts(install_id)', 'REFERENCES registrations(registration_id)'),
    ('ON mail_pull_ack_outbox(state,next_attempt_ms,event_id)', 'ON mail_pull_ack_outbox(state,event_id,next_attempt_ms)'),
])
def test_changed_constraints_fk_uniqueness_and_index_sql_refused(store, old, new):
    assert old in FROZEN_SQL
    altered = FROZEN_SQL.replace(old, new)
    with store._tx() as db:
        for sql in altered.split(';'):
            if sql.strip():
                db.execute(sql)
    before = snapshot(store)
    with pytest.raises(PullSchemaCorrupt):
        initialize(store)
    with pytest.raises(PullSchemaCorrupt):
        validate(store)
    assert snapshot(store) == before


@pytest.mark.parametrize('marker', [[], [(1, 0)], [(1, 2)], [(2, 1)], [(1, 1), (2, 1)]])
def test_missing_wrong_or_future_marker_rows_are_never_reset(installed, marker):
    with installed._tx() as db:
        db.execute('PRAGMA ignore_check_constraints=ON')
        db.execute('DELETE FROM mail_pull_version')
        db.executemany('INSERT INTO mail_pull_version VALUES(?,?)', marker)
    before = snapshot(installed)
    with pytest.raises(PullSchemaCorrupt):
        initialize(installed)
    with pytest.raises(PullSchemaCorrupt):
        validate(installed)
    assert snapshot(installed) == before


@pytest.mark.parametrize('pragma', ['application_id=0', 'user_version=2', 'user_version=4'])
def test_base_identity_or_version_mismatch_cannot_be_upgraded(store, pragma):
    with store._tx() as db:
        db.execute('PRAGMA ' + pragma)
    before = snapshot(store)
    with pytest.raises(PullSchemaCorrupt):
        initialize(store)
    assert snapshot(store) == before


def test_orphaned_custody_rows_are_not_silently_accepted(installed):
    populate(installed)
    with installed._tx() as db:
        db.execute('DELETE FROM mail_pull_receipts')
    before = snapshot(installed)
    with pytest.raises(PullSchemaCorrupt):
        initialize(installed)
    with pytest.raises(PullSchemaCorrupt):
        validate(installed)
    assert snapshot(installed) == before


def test_runtime_validation_never_installs_even_when_entire_extension_is_absent(store):
    before = snapshot(store)
    with pytest.raises(PullSchemaCorrupt):
        validate(store)
    assert snapshot(store) == before


@pytest.mark.parametrize('raw', [b'', b'\x00' * 4096, b'not a SQLite database'])
def test_empty_nul_and_corrupt_files_fail_closed_without_replacement(tmp_path, raw):
    path = tmp_path / 'agent_mail.sqlite3'
    path.write_bytes(raw)
    with pytest.raises(MailStoreCorrupt):
        GuardedMailStore(path)
    assert path.read_bytes() == raw


def test_guarded_store_missing_after_open_stays_missing(installed):
    installed.path.unlink()
    for operation in (initialize, validate):
        with pytest.raises(StoreHeld, match='store_missing'):
            operation(installed)
        assert not installed.path.exists()


@pytest.mark.parametrize('failure', ['ddl', 'marker', 'commit'])
def test_failure_rolls_back_entire_extension_and_allows_explicit_retry(store, monkeypatch, failure):
    before = snapshot(store)
    original_connect = store._connect

    def failing_connect():
        db = original_connect()

        def authorize(action, first, second, *_):
            if ((failure == 'ddl' and action == sqlite3.SQLITE_CREATE_TABLE and first == 'mail_pull_receipts')
                    or (failure == 'marker' and action == sqlite3.SQLITE_INSERT and first == 'mail_pull_version')
                    or (failure == 'commit' and action == sqlite3.SQLITE_TRANSACTION and first == 'COMMIT')):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorize)
        return db

    with monkeypatch.context() as m:
        m.setattr(store, '_connect', failing_connect)
        with pytest.raises(MailStoreError):
            initialize(store)
    assert snapshot(store) == before
    initialize(store)
    validate(store)


def test_concurrent_initializers_use_existing_serialized_write_transaction(store):
    second = GuardedMailStore(store.path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(initialize, candidate) for candidate in (store, second)]
        assert [future.result() for future in results] == [None, None]
    validate(store)


def test_initializer_uses_store_transaction_and_validation_is_read_only(store, monkeypatch):
    statements = []
    original_connect = store._connect

    def tracked_connect():
        db = original_connect()
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(store, '_connect', tracked_connect)
    initialize(store)
    assert statements[0] == 'BEGIN IMMEDIATE'
    assert statements[-1] == 'COMMIT'
    statements.clear()
    initialize(store)
    assert not any(sql.startswith(('CREATE', 'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'DROP')) for sql in statements)
    statements.clear()
    validate(store)
    assert statements[0] == 'BEGIN' and statements[-1] == 'ROLLBACK'
    assert not any(sql.startswith(('CREATE', 'INSERT', 'UPDATE', 'DELETE', 'ALTER', 'DROP')) for sql in statements)


def test_unit19_start_and_restart_use_production_schema_with_its_real_doubles(tmp_path):
    h = Harness(tmp_path / 'fixture')
    # Harness supplies independent crypto/service doubles. Its frozen-SQL fixture
    # DB is unused: Signup below receives a fresh production-initialized store.
    h.folder = tmp_path / 'production'
    h.store = GuardedMailStore(h.folder / 'agent_mail.sqlite3', creating=True)
    h.seal = IdentitySeal(h.folder / 'identity', key=lambda: MASTER)
    initialize(h.store)
    assert h.client().status()['state'] == 'not_enrolled'
    result = h.client().start(OWNER, 'v1')
    assert result['state'] == 'pending' and result['address'] == ADDRESS
    assert result['enabled'] is False
    validate(h.store)
    before = snapshot(h.store)
    initialize(h.store)
    assert snapshot(h.store) == before
    h.remote_state = 'verified'
    assert h.restart().resume()['state'] == 'verified'
    validate(h.store)
    assert h.row('mail_pull_accounts')['local_enabled'] == 0
    assert h.count('mail_pull_flows') == h.count('mail_pull_generations') == 1
    assert h.count('mail_pull_fences') == 0
    assert h.calls[-1][0] == '/v1/pull/signup/status'
