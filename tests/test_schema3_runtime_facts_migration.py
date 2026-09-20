"""Dedicated schema-3 to v4 runtime-facts migration matrix."""
import hashlib
import sqlite3

import pytest

from mc.conversation_store import ConversationStore, SchemaError


def _v3(tmp_path):
    store = ConversationStore(tmp_path / 'state.sqlite')
    store.create_lifecycle_conversation('p', 'c', engine={'provider': 'codex'}, event_id='create')
    with store._connection(write=True) as db:
        db.execute('DROP TABLE runtime_launch_facts')
        db.execute('PRAGMA user_version=3')
        db.execute("INSERT INTO capture_sources(project_id,conversation_id,attempt_id,privacy_generation,owner_epoch,provider,format_version,source_id,incarnation) VALUES('p','c','a',0,0,'codex','fixture','source','one')")
        db.execute("INSERT INTO capture_spans(project_id,conversation_id,attempt_id,source_sequence,source_id,incarnation,frame_digest,frame_json) VALUES('p','c','a',0,'source','one','digest','{}')")
    return store


def _shape(store):
    with sqlite3.connect(store.db_path) as db:
        tables = tuple(sorted(r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")))
        rows = tuple(db.execute("SELECT project_id,conversation_id,attempt_id,source_id,incarnation FROM capture_spans")) if 'capture_spans' in tables else ()
        return db.execute('PRAGMA user_version').fetchone()[0], tables, rows


def test_successful_real_v3_backup_and_v4_preserve_rows(tmp_path):
    store = _v3(tmp_path); before = _shape(store); backup = tmp_path / 'backup.sqlite'
    store.migrate_schema3(backup_path=backup)
    assert _shape(store)[0] == 4
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_launch_facts'").fetchone()
        assert tuple(db.execute("SELECT project_id,conversation_id,attempt_id,source_id,incarnation FROM capture_spans")) == before[2]
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM runtime_launch_facts").fetchone()[0] == 0


def test_existing_backup_and_source_backup_refuse_without_mutation(tmp_path):
    store = _v3(tmp_path); original = _shape(store); backup = tmp_path / 'backup.sqlite'; backup.write_bytes(b'keep')
    with pytest.raises(ValueError): store.migrate_schema3(backup_path=backup)
    with pytest.raises(ValueError): store.migrate_schema3(backup_path=store.db_path)
    assert _shape(store) == original and backup.read_bytes() == b'keep'


@pytest.mark.parametrize('mutation', ['extra', 'missing', 'wrong_column'])
def test_malformed_v3_refuses_before_backup(tmp_path, mutation):
    store = _v3(tmp_path)
    with sqlite3.connect(store.db_path) as db:
        if mutation == 'extra': db.execute('CREATE TABLE extra(x TEXT)')
        elif mutation == 'missing': db.execute('DROP TABLE capture_spans')
        else: db.execute('ALTER TABLE requests RENAME TO requests_old'); db.execute('CREATE TABLE requests(project_id INTEGER)')
        db.execute('PRAGMA user_version=3')
    with pytest.raises(SchemaError): store.migrate_schema3(backup_path=tmp_path / 'no-backup.sqlite')
    assert _shape(store)[0] == 3 and not (tmp_path / 'no-backup.sqlite').exists()


@pytest.mark.parametrize('version', [2, 4])
def test_wrong_version_refuses(tmp_path, version):
    store = _v3(tmp_path)
    with sqlite3.connect(store.db_path) as db: db.execute(f'PRAGMA user_version={version}')
    with pytest.raises(SchemaError): store.migrate_schema3(backup_path=tmp_path / 'backup.sqlite')


def test_second_migration_refuses(tmp_path):
    store = _v3(tmp_path); store.migrate_schema3(backup_path=tmp_path / 'first.sqlite')
    with pytest.raises(SchemaError): store.migrate_schema3(backup_path=tmp_path / 'second.sqlite')


def test_normal_v3_access_refuses_without_upgrade(tmp_path):
    store = _v3(tmp_path)
    with pytest.raises(SchemaError): store.lifecycle_state('p', 'c')
    assert _shape(store)[0] == 3


def test_backup_helper_failure_preserves_v3_and_retry_succeeds(tmp_path, monkeypatch):
    store = _v3(tmp_path)
    monkeypatch.setattr(store, '_backup_consistent', lambda path: (_ for _ in ()).throw(OSError('backup fault')))
    with pytest.raises(OSError, match='backup fault'):
        store.migrate_schema3(backup_path=tmp_path / 'failed.sqlite')
    assert _shape(store)[0] == 3
    monkeypatch.undo()
    store.migrate_schema3(backup_path=tmp_path / 'retry.sqlite')
    assert _shape(store)[0] == 4


def test_runtime_table_create_failure_rolls_back_after_valid_backup(tmp_path, monkeypatch):
    store = _v3(tmp_path)
    original = store._create_runtime_launch_facts
    monkeypatch.setattr(store, '_create_runtime_launch_facts', lambda db: (_ for _ in ()).throw(RuntimeError('create fault')))
    backup = tmp_path / 'valid-v3.sqlite'
    with pytest.raises(RuntimeError, match='create fault'):
        store.migrate_schema3(backup_path=backup)
    assert _shape(store)[0] == 3
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_launch_facts'").fetchone()
    monkeypatch.setattr(store, '_create_runtime_launch_facts', original)
    store.migrate_schema3(backup_path=tmp_path / 'retry.sqlite')
    assert _shape(store)[0] == 4


def test_v4_runtime_table_columns_fk_and_check_are_exact(tmp_path):
    store = _v3(tmp_path)
    store.migrate_schema3(backup_path=tmp_path / 'backup.sqlite')
    with sqlite3.connect(store.db_path) as db:
        columns = [r[1] for r in db.execute('PRAGMA table_info(runtime_launch_facts)')]
        assert columns == ['project_id','conversation_id','attempt_id','provider','project_path','mc_session_id','requested_engine_json','incognito','source_id','source_incarnation','format_version','native_session_id','transcript_path']
        assert {r[2] for r in db.execute('PRAGMA foreign_key_list(runtime_launch_facts)')} == {'lifecycle_attempts'}
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='runtime_launch_facts'").fetchone()[0]
        assert 'CHECK(incognito=0)' in sql


def test_inner_backup_copy_failure_cleans_owned_artifact_and_retries(tmp_path, monkeypatch):
    store = _v3(tmp_path); backup = tmp_path / 'copy-failed.sqlite'; original = store._copy_backup
    def fail(reader, destination):
        raise OSError('copy fault')
    monkeypatch.setattr(store, '_copy_backup', fail)
    with pytest.raises(OSError, match='copy fault'):
        store.migrate_schema3(backup_path=backup)
    assert not backup.exists() and _shape(store)[0] == 3
    monkeypatch.setattr(store, '_copy_backup', original)
    store.migrate_schema3(backup_path=backup)
    assert _shape(store)[0] == 4


def test_v3_capture_identity_unique_and_no_foreign_keys_are_validated(tmp_path):
    store = _v3(tmp_path)
    with sqlite3.connect(store.db_path) as db:
        assert len(db.execute('PRAGMA foreign_key_list(capture_sources)').fetchall()) == 0
        assert len(db.execute('PRAGMA foreign_key_list(capture_spans)').fetchall()) == 0
        assert any(r[2] for r in db.execute('PRAGMA index_list(capture_sources)'))
    store.migrate_schema3(backup_path=tmp_path / 'backup.sqlite')
    with sqlite3.connect(store.db_path) as db:
        fk = list(db.execute('PRAGMA foreign_key_list(runtime_launch_facts)'))
        assert {r[3] for r in fk} == {'project_id','conversation_id','attempt_id'}
        assert {r[4] for r in fk} == {'project_id','conversation_id','attempt_id'}
        assert [r[5] for r in db.execute('PRAGMA table_info(runtime_launch_facts)')][:3] == [1,2,3]
