"""Additive pull custody extension to the supplied checked MailStore (unit 15).

Frozen SQL: AGENT_MAIL_PULL_CONTRACT section 9. No store opening, default path,
factory wiring, identity creation or import-time activity. Composition owns the
external marker/seal checks and supplies the SAME guarded store outside the repo.

Call initialize(store) only on explicit authorized first setup/upgrade. Call
validate(store) on runtime opens: it never installs or repairs missing objects.
Both check all extension SQL objects, marker rows and database integrity under
one snapshot; initialization uses the store's write lock/BEGIN IMMEDIATE and
never commits independently. Base application_id/user_version stay unchanged.
Future extension versions require a separately reviewed migration; v1 refuses
anything other than the exact frozen schema. Data loss with unchanged, valid SQL
cannot be inferred from a version marker; external custody guards remain required.
"""
from __future__ import annotations

import re
import sqlite3

from mc.agent_mail_store import APPLICATION_ID, MailStore, MailStoreCorrupt

VERSION = 1
_BASE_VERSION = 3

# Literal frozen column order matters: Signup uses positional INSERTs.
_SCHEMA = """
CREATE TABLE mail_pull_version (
  slot INTEGER PRIMARY KEY CHECK(slot=1), version INTEGER NOT NULL CHECK(version=1)
);
INSERT INTO mail_pull_version VALUES(1,1);
CREATE TABLE mail_pull_accounts (
  install_id TEXT NOT NULL PRIMARY KEY CHECK(length(install_id)=32),
  generation INTEGER NOT NULL CHECK(generation>0), owner_generation INTEGER NOT NULL CHECK(owner_generation>0),
  service_origin TEXT NOT NULL, address TEXT NOT NULL, owner_address TEXT NOT NULL,
  key_id TEXT NOT NULL, seal_flow_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','verified','active','disabled','frozen','deleted','held')),
  local_enabled INTEGER NOT NULL DEFAULT 0 CHECK(local_enabled IN (0,1)),
  consent_version TEXT NOT NULL, consent_epoch INTEGER NOT NULL CHECK(consent_epoch>0),
  reason TEXT NOT NULL DEFAULT '', updated_ms INTEGER NOT NULL
);
CREATE UNIQUE INDEX mail_pull_one_enabled ON mail_pull_accounts(local_enabled) WHERE local_enabled=1;
CREATE TABLE mail_pull_generations (
  install_id TEXT NOT NULL REFERENCES mail_pull_accounts(install_id),
  generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  key_id TEXT NOT NULL, seal_flow_id TEXT NOT NULL,
  manifest_sequence INTEGER NOT NULL, manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
  service_key_id TEXT NOT NULL, service_public_key TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('current','fenced','held')),
  created_ms INTEGER NOT NULL, fenced_ms INTEGER,
  PRIMARY KEY(install_id,generation)
);
CREATE UNIQUE INDEX mail_pull_current ON mail_pull_generations(install_id) WHERE state='current';
CREATE TABLE mail_pull_flows (
  flow_id TEXT NOT NULL PRIMARY KEY CHECK(length(flow_id)=32),
  install_id TEXT NOT NULL REFERENCES mail_pull_accounts(install_id),
  purpose TEXT NOT NULL CHECK(purpose IN ('signup','owner_change','key_recovery','resume','rotate','delete')),
  expected_generation INTEGER NOT NULL, expected_owner_generation INTEGER NOT NULL,
  candidate_key_id TEXT NOT NULL, seal_flow_id TEXT NOT NULL,
  input_digest TEXT NOT NULL CHECK(length(input_digest)=64),
  human_authorized_ms INTEGER NOT NULL, consent_epoch INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','verified','enable_pending','completed','cancelled','expired','held')),
  expires_ms INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '',
  result_json TEXT
);
CREATE INDEX mail_pull_flow_due ON mail_pull_flows(state,expires_ms,flow_id);
CREATE TABLE mail_pull_threads (
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  thread_id TEXT NOT NULL, thread_hash TEXT NOT NULL UNIQUE CHECK(length(thread_hash)=64),
  project_id TEXT NOT NULL, session_id TEXT NOT NULL,
  continuation_digest TEXT NOT NULL CHECK(length(continuation_digest)=64),
  thread_expires_ms INTEGER NOT NULL, dedupe_until_ms INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('active','revoked')),
  PRIMARY KEY(install_id,generation,thread_id),
  FOREIGN KEY(install_id,generation) REFERENCES mail_pull_generations(install_id,generation)
);
CREATE INDEX mail_pull_thread_conversation ON mail_pull_threads(project_id,session_id,thread_expires_ms);
CREATE TABLE mail_pull_receipts (
  event_id TEXT NOT NULL PRIMARY KEY CHECK(length(event_id)=64),
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  thread_id TEXT NOT NULL, cloud_seq INTEGER NOT NULL CHECK(cloud_seq>0),
  incoming_hash TEXT NOT NULL CHECK(length(incoming_hash)=64),
  event_digest TEXT NOT NULL CHECK(length(event_digest)=64),
  body_digest TEXT NOT NULL CHECK(length(body_digest)=64),
  local_receipt_id TEXT NOT NULL UNIQUE, local_event_id TEXT NOT NULL UNIQUE,
  received_ms INTEGER NOT NULL, expires_ms INTEGER NOT NULL, dedupe_until_ms INTEGER NOT NULL,
  committed_ms INTEGER NOT NULL, cloud_receipt_id TEXT, cloud_committed_ms INTEGER,
  disposition TEXT NOT NULL CHECK(disposition IN ('local_received','expired','revoked','terminal')),
  UNIQUE(install_id,generation,thread_id,incoming_hash), UNIQUE(install_id,cloud_seq),
  FOREIGN KEY(install_id,generation,thread_id) REFERENCES mail_pull_threads(install_id,generation,thread_id)
);
CREATE INDEX mail_pull_receipt_expiry ON mail_pull_receipts(dedupe_until_ms,event_id);
CREATE INDEX mail_pull_receipt_order ON mail_pull_receipts(install_id,cloud_seq);
CREATE TABLE mail_pull_ack_outbox (
  event_id TEXT NOT NULL PRIMARY KEY REFERENCES mail_pull_receipts(event_id),
  local_receipt_id TEXT NOT NULL UNIQUE, lease_id TEXT NOT NULL,
  event_digest TEXT NOT NULL CHECK(length(event_digest)=64),
  body_digest TEXT NOT NULL CHECK(length(body_digest)=64),
  state TEXT NOT NULL CHECK(state IN ('pending','held')),
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt_ms INTEGER NOT NULL,
  created_ms INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX mail_pull_ack_due ON mail_pull_ack_outbox(state,next_attempt_ms,event_id);
CREATE TABLE mail_pull_fences (
  op_id TEXT NOT NULL PRIMARY KEY CHECK(length(op_id)=32),
  install_id TEXT NOT NULL, generation INTEGER NOT NULL, flow_id TEXT NOT NULL,
  fence_token TEXT NOT NULL CHECK(length(fence_token)=64),
  mode TEXT NOT NULL CHECK(mode IN ('cancel','revoke')),
  state TEXT NOT NULL CHECK(state IN ('pending','acked')),
  receipt_id TEXT, created_ms INTEGER NOT NULL, next_attempt_ms INTEGER NOT NULL,
  UNIQUE(install_id,generation,flow_id,mode)
);
CREATE INDEX mail_pull_fence_due ON mail_pull_fences(state,next_attempt_ms,op_id);
CREATE TABLE mail_pull_disable_ops (
  op_id TEXT NOT NULL PRIMARY KEY CHECK(length(op_id)=32),
  install_id TEXT NOT NULL REFERENCES mail_pull_accounts(install_id),
  generation INTEGER NOT NULL CHECK(generation>0), owner_generation INTEGER NOT NULL CHECK(owner_generation>0),
  flow_id TEXT NOT NULL CHECK(length(flow_id)=32),
  input_digest TEXT NOT NULL CHECK(length(input_digest)=64),
  state TEXT NOT NULL CHECK(state IN ('local_off','held')),
  reason TEXT NOT NULL DEFAULT '', result_json TEXT NOT NULL, created_ms INTEGER NOT NULL
);
CREATE INDEX mail_pull_disable_scope ON mail_pull_disable_ops(install_id,generation,owner_generation,flow_id,op_id);
CREATE TABLE mail_pull_connections (
  install_id TEXT NOT NULL PRIMARY KEY REFERENCES mail_pull_accounts(install_id),
  generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  epoch INTEGER NOT NULL DEFAULT 0, connection_id TEXT,
  authenticated_ms INTEGER, reauth_ms INTEGER,
  scan_after_seq INTEGER NOT NULL DEFAULT 0, scan_complete INTEGER NOT NULL DEFAULT 0 CHECK(scan_complete IN (0,1)),
  last_activity_ms INTEGER, next_attempt_ms INTEGER NOT NULL DEFAULT 0,
  failures INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE mail_pull_status (
  install_id TEXT NOT NULL PRIMARY KEY REFERENCES mail_pull_accounts(install_id),
  observed_ms INTEGER NOT NULL, generation INTEGER NOT NULL, owner_generation INTEGER NOT NULL,
  status_json TEXT NOT NULL, response_digest TEXT NOT NULL CHECK(length(response_digest)=64)
);
"""
_CREATE = tuple(s.strip() for s in _SCHEMA.split(';') if s.strip().startswith('CREATE '))

# SQLite's implicit UNIQUE/PRIMARY KEY indexes are objects too. Their SQL is
# NULL; explicit indexes and constraints are checked against the literal DDL.
_AUTO_COUNTS = {
    'accounts': 1, 'generations': 1, 'flows': 1, 'threads': 2,
    'receipts': 5, 'ack_outbox': 2, 'fences': 2, 'disable_ops': 1, 'connections': 1, 'status': 1,
}


class PullSchemaCorrupt(MailStoreCorrupt):
    """Closed local hold reason; never leaks SQL, row content or SQLite text."""

    reason = 'schema_corrupt'

    def __init__(self) -> None:
        super().__init__(self.reason)


def _normalized(sql: str) -> str:
    return ' '.join(sql.split())


def _expected() -> dict[str, tuple[str, str, str | None]]:
    objects: dict[str, tuple[str, str, str | None]] = {}
    for sql in _CREATE:
        match = re.match(r'CREATE (TABLE|(?:UNIQUE )?INDEX) (\w+)', sql)
        assert match is not None
        kind, name = match.groups()
        if kind == 'TABLE':
            table = name
        else:
            target = re.search(r' ON (\w+)', sql)
            assert target is not None
            table = target[1]
        objects[name] = ('table' if kind == 'TABLE' else 'index', table, _normalized(sql))
    for suffix, count in _AUTO_COUNTS.items():
        table = 'mail_pull_' + suffix
        for ordinal in range(1, count + 1):
            objects[f'sqlite_autoindex_{table}_{ordinal}'] = ('index', table, None)
    return objects


def _objects(db: sqlite3.Connection) -> dict[str, tuple[str, str, str | None]]:
    return {name: (kind, table, _normalized(sql) if sql is not None else None)
            for kind, name, table, sql in db.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE lower(name) GLOB 'mail_pull_*' OR lower(tbl_name) GLOB 'mail_pull_*'")}


def _base(db: sqlite3.Connection) -> None:
    if (db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
            or db.execute('PRAGMA user_version').fetchone()[0] != _BASE_VERSION
            or db.execute('PRAGMA journal_mode').fetchone()[0] != 'wal'
            or db.execute('PRAGMA synchronous').fetchone()[0] != 2):
        raise PullSchemaCorrupt()
    if [tuple(row) for row in db.execute('PRAGMA integrity_check')] != [('ok',)]:
        raise PullSchemaCorrupt()


def _validate(db: sqlite3.Connection) -> None:
    if _objects(db) != _expected():
        raise PullSchemaCorrupt()
    if [tuple(row) for row in db.execute('SELECT slot,version FROM mail_pull_version')] != [(1, VERSION)]:
        raise PullSchemaCorrupt()
    # Existing MailStore connections do not enable FK enforcement. Check the
    # extension explicitly without changing that connection policy or legacy rows.
    for suffix in ('generations', 'flows', 'threads', 'receipts', 'ack_outbox', 'disable_ops', 'connections', 'status'):
        if db.execute(f'PRAGMA foreign_key_check(mail_pull_{suffix})').fetchone() is not None:
            raise PullSchemaCorrupt()


def initialize(store: MailStore) -> None:
    """Explicit additive upgrade or validated no-op, never a partial repair.

    An entirely absent extension may be installed; any preexisting pull object
    requires the COMPLETE valid v1 extension. Lost tables/marker/indexes, changed
    constraints and unsupported versions remain held. DDL + marker commit once.
    Storage errors propagate through the store's observable failure handling.
    """
    with store._tx() as db:
        _base(db)
        if not _objects(db):
            for sql in _CREATE:
                db.execute(sql)
            db.execute('INSERT INTO mail_pull_version VALUES(1,?)', (VERSION,))
        _validate(db)


def validate(store: MailStore) -> None:
    """Validate a runtime snapshot without writes, migration or missing-row repair."""
    with store._read() as db:
        db.execute('BEGIN')
        try:
            _base(db)
            _validate(db)
        finally:
            db.execute('ROLLBACK')
