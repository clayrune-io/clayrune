"""Token-free disable custody; schema proof only, not the W02 adapter.

All account/denial writes below are synthetic transactions in temporary stores.
No factory bootstrap, keys, transport, socket, or actual disable is exercised.
"""
import hashlib
import json
import sqlite3

import pytest

from mc.agent_mail_factory import GuardedMailStore
from mc.agent_mail_pull_schema import PullSchemaCorrupt, initialize, validate


INSTALL, FLOW, OP = 'a' * 32, 'b' * 32, 'c' * 32
INPUT = hashlib.sha256(json.dumps({'op_id': OP}, separators=(',', ':')).encode()).hexdigest()
RESULT = json.dumps({'enabled': False, 'revocation_pending': True,
                     'reason': 'fence_token_missing'}, separators=(',', ':'))
INSERT = '''INSERT INTO mail_pull_disable_ops
    (op_id,install_id,generation,owner_generation,flow_id,input_digest,state,reason,result_json,created_ms)
    VALUES(?,?,?,?,?,?,?,?,?,?)'''
RECORD = (OP, INSTALL, 1, 1, FLOW, INPUT, 'held', 'fence_token_missing', RESULT, 1000)


@pytest.fixture
def store(tmp_path):
    store = GuardedMailStore(tmp_path / 'agent_mail.sqlite3', creating=True)
    initialize(store)
    with store._tx() as db:
        db.execute('''INSERT INTO mail_pull_accounts VALUES(
            ?,1,1,'https://mail.example.com','public@example.com','owner@example.com',
            'key',?,'active',1,'v1',1,'',1000)''', (INSTALL, FLOW))
    return store


def snapshot(store):
    with store._read() as db:
        schema = [tuple(r) for r in db.execute('SELECT * FROM sqlite_master ORDER BY name')]
        rows = {r['name']: [tuple(v) for v in db.execute('SELECT * FROM "' + r['name'] + '"')]
                for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return schema, rows


def test_tokenless_local_off_and_exact_operation_survive_reopen(store):
    # The absent flow/token is deliberate: a flow FK would prevent this hold.
    with store._tx() as db:
        db.execute("UPDATE mail_pull_accounts SET local_enabled=0,state='held',"
                   "reason='fence_token_missing',consent_epoch=2")
        db.execute(INSERT, RECORD)
    reopened = GuardedMailStore(store.path)
    validate(reopened)
    initialize(reopened)  # existing operation is preserved, never reset
    with reopened._read() as db:
        assert tuple(db.execute('SELECT * FROM mail_pull_disable_ops').fetchone()) == RECORD
        assert db.execute('SELECT local_enabled FROM mail_pull_accounts').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM mail_pull_fences').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM mail_pull_flows').fetchone()[0] == 0
        assert 'fence_token' not in [r['name'] for r in db.execute('PRAGMA table_info(mail_pull_disable_ops)')]


@pytest.mark.parametrize('field,value', [(1, 'd' * 32), (2, 2), (3, 2),
                                       (4, 'e' * 32), (5, 'f' * 64)])
def test_op_primary_key_preserves_original_scope_after_later_generation(store, field, value):
    with store._tx() as db:
        db.execute(INSERT, RECORD)
        # Synthetic independently authorized later state, not a recovery API.
        db.execute('UPDATE mail_pull_accounts SET generation=2,owner_generation=2,seal_flow_id=?',
                   ('e' * 32,))
        db.execute('INSERT INTO mail_pull_accounts SELECT ?,generation,owner_generation,'
                   'service_origin,address,owner_address,key_id,seal_flow_id,state,0,'
                   'consent_version,consent_epoch,reason,updated_ms FROM mail_pull_accounts', ('d' * 32,))
    changed = list(RECORD)
    changed[field] = value
    before = snapshot(store)
    with store._tx() as db:
        with pytest.raises(sqlite3.IntegrityError, match='UNIQUE constraint failed: mail_pull_disable_ops.op_id'):
            db.execute(INSERT, changed)
    assert snapshot(store) == before
    with store._read() as db:
        assert tuple(db.execute('SELECT * FROM mail_pull_disable_ops').fetchone()) == RECORD
        assert tuple(db.execute('SELECT generation,local_enabled FROM mail_pull_accounts '
                                'WHERE install_id=?', (INSTALL,)).fetchone()) == (2, 1)
    # W02 must read/compare this immutable record and map mismatches to
    # operation_conflict BEFORE denial; SQL custody alone is not that adapter.
    validate(store)


def test_local_off_and_operation_rollback_together(store):
    before = snapshot(store)
    with pytest.raises(RuntimeError, match='synthetic commit failure'):
        with store._tx() as db:
            db.execute('UPDATE mail_pull_accounts SET local_enabled=0')
            db.execute(INSERT, RECORD)
            raise RuntimeError('synthetic commit failure')
    assert snapshot(store) == before


@pytest.mark.parametrize('field,value', [(0, None), (0, 'a' * 31), (1, None),
    (2, 0), (3, 0), (4, None), (4, 'a' * 31), (5, None), (5, 'a' * 63),
    (6, 'active'), (7, None), (8, None), (9, None)])
def test_required_identity_and_result_constraints(store, field, value):
    changed = list(RECORD)
    changed[field] = value
    with store._tx() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(INSERT, changed)


def test_local_off_result_does_not_require_a_fence(store):
    result = json.dumps({'enabled': False, 'revocation_pending': True, 'reason': 'local_disabled'})
    record = (*RECORD[:6], 'local_off', 'local_disabled', result, 1000)
    with store._tx() as db:
        db.execute(INSERT, record)
    validate(store)


@pytest.mark.parametrize('damage', ['table', 'index', 'column', 'constraint', 'orphan'])
def test_validate_and_initialize_refuse_disable_operation_drift_without_repair(store, damage):
    with store._tx() as db:
        if damage == 'table':
            db.execute('DROP TABLE mail_pull_disable_ops')
        elif damage == 'index':
            db.execute('DROP INDEX mail_pull_disable_scope')
        elif damage == 'column':
            db.execute('ALTER TABLE mail_pull_disable_ops ADD COLUMN surprise TEXT')
        elif damage == 'constraint':
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name='mail_pull_disable_ops'").fetchone()[0]
            db.execute('DROP TABLE mail_pull_disable_ops')
            db.execute(sql.replace('CHECK(generation>0)', 'CHECK(generation>=0)'))
            db.execute('CREATE INDEX mail_pull_disable_scope ON '
                       'mail_pull_disable_ops(install_id,generation,owner_generation,flow_id,op_id)')
        else:
            record = list(RECORD)
            record[1] = 'f' * 32
            db.execute(INSERT, record)  # connections do not enforce FK; validate must
    before = snapshot(store)
    for operation in (validate, initialize):
        with pytest.raises(PullSchemaCorrupt, match='^schema_corrupt$'):
            operation(store)
        assert snapshot(store) == before
