"""Checked Q1-A service-review storage (pull contract 13.1), deliberately unwired.

Supply the SAME guarded external MailStore used by the factory. Only explicit
authorized initialize() installs absent objects; validate() never repairs them.
The legacy ManifestCAS origin floor is retained without initializing Enrollment.
All DDL shares the store's BEGIN IMMEDIATE transaction and base identity remains
unchanged. No paths, connections, keys, network or startup work are created here.

These are structural checks, not service authority: W06 must verify signatures,
recompute derived evidence, recheck config/time/floor and atomically commit floor,
acceptance and replay operation in one transaction. Expired/superseded receipts
remain valid storage. W18 owns factory bootstrap/pristine-state integration.
"""
from __future__ import annotations

import base64
import json
import re
import sqlite3

from mc.agent_mail_manifest_fetch import valid_service_origin
from mc.agent_mail_service_manifest import canonical_manifest_bytes
from mc.agent_mail_store import APPLICATION_ID, MailStore, MailStoreCorrupt

VERSION = 1
_SCHEMA = """
CREATE TABLE mail_service_review_version (
  slot INTEGER PRIMARY KEY CHECK(slot=1),
  version INTEGER NOT NULL CHECK(version=1)
);
CREATE TABLE mail_service_review_acceptance (
  slot INTEGER PRIMARY KEY CHECK(slot=1),
  op_id TEXT NOT NULL UNIQUE CHECK(length(op_id)=32),
  trust_revision INTEGER NOT NULL CHECK(trust_revision>0),
  service_origin TEXT NOT NULL, root_public_key TEXT NOT NULL,
  manifest_json TEXT NOT NULL CHECK(length(manifest_json)<=16384),
  signature TEXT NOT NULL, manifest_sequence INTEGER NOT NULL CHECK(manifest_sequence>0),
  manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
  service_key_id TEXT NOT NULL, service_public_key TEXT NOT NULL,
  service_fingerprint TEXT NOT NULL CHECK(length(service_fingerprint)=64),
  accepted_ms INTEGER NOT NULL CHECK(accepted_ms>0),
  expires_ms INTEGER NOT NULL CHECK(expires_ms>accepted_ms)
);
CREATE TABLE mail_service_review_operations (
  op_id TEXT NOT NULL PRIMARY KEY CHECK(length(op_id)=32),
  input_digest TEXT NOT NULL CHECK(length(input_digest)=64),
  trust_revision INTEGER NOT NULL CHECK(trust_revision>0),
  manifest_digest TEXT NOT NULL CHECK(length(manifest_digest)=64),
  service_origin TEXT NOT NULL, root_public_key TEXT NOT NULL,
  service_fingerprint TEXT NOT NULL CHECK(length(service_fingerprint)=64),
  accepted_ms INTEGER NOT NULL CHECK(accepted_ms>0),
  expires_ms INTEGER NOT NULL CHECK(expires_ms>accepted_ms)
);
"""
# Exact existing Enrollment DDL; never change/rebuild this origin-keyed floor.
_FLOOR = """CREATE TABLE mail_enrollment_manifests (
                origin TEXT PRIMARY KEY, sequence INTEGER NOT NULL, digest TEXT NOT NULL)"""
_CREATE = tuple(sql.strip() for sql in _SCHEMA.split(';') if sql.strip())
_PUBLIC = ('op_id', 'trust_revision', 'manifest_digest', 'service_origin',
           'root_public_key', 'service_fingerprint', 'accepted_ms', 'expires_ms')


class ServiceReviewSchemaCorrupt(MailStoreCorrupt):
    """Closed local storage hold; SQL, evidence and exception text stay private."""

    reason = 'schema_corrupt'

    def __init__(self) -> None:
        super().__init__(self.reason)


def _normalized(sql: str) -> str:
    return ' '.join(sql.split())


def _expected(statements: tuple[str, ...]) -> dict[str, tuple[str, str, str | None]]:
    objects: dict[str, tuple[str, str, str | None]] = {}
    for sql in statements:
        name = sql.split()[2]
        objects[name] = ('table', name, _normalized(sql))
        if name != 'mail_service_review_version':
            objects[f'sqlite_autoindex_{name}_1'] = ('index', name, None)
    return objects


def _objects(db: sqlite3.Connection, *, floor: bool = False) -> dict[str, tuple[str, str, str | None]]:
    clause = ("lower(name)='mail_enrollment_manifests' OR lower(tbl_name)='mail_enrollment_manifests'"
              if floor else "lower(name) GLOB 'mail_service_review_*' OR lower(tbl_name) GLOB 'mail_service_review_*'")
    return {name: (kind, table, _normalized(sql) if sql is not None else None)
            for kind, name, table, sql in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master WHERE ' + clause)}


def _base(db: sqlite3.Connection) -> None:
    if (db.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
            or db.execute('PRAGMA user_version').fetchone()[0] != 3
            or db.execute('PRAGMA journal_mode').fetchone()[0] != 'wal'
            or db.execute('PRAGMA synchronous').fetchone()[0] != 2
            or [tuple(row) for row in db.execute('PRAGMA integrity_check')] != [('ok',)]):
        raise ServiceReviewSchemaCorrupt()


def _integer(value: object, ceiling: int) -> bool:
    return type(value) is int and 0 < value <= ceiling


def _matches(pattern: str, value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _encoded(value: object, size: int) -> bool:
    if not _matches(r'[A-Za-z0-9_-]{' + str((size * 8 + 5) // 6) + '}', value):
        return False
    assert isinstance(value, str)
    raw = base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    return len(raw) == size and base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii') == value


def _floor(db: sqlite3.Connection) -> None:
    if _objects(db, floor=True) != _expected((_FLOOR,)):
        raise ServiceReviewSchemaCorrupt()
    for row in db.execute('SELECT origin,sequence,digest FROM mail_enrollment_manifests'):
        # Match ManifestCAS's positive SQLite integer floor, including old rows.
        if (not valid_service_origin(row[0]) or type(row[1]) is not int or row[1] < 1
                or not _matches('[a-f0-9]{64}', row[2])):
            raise ServiceReviewSchemaCorrupt()


def _public_row(row: sqlite3.Row) -> None:
    if (not _matches('[a-f0-9]{32}', row['op_id'])
            or not _integer(row['trust_revision'], 999_999_999_999_999)
            or not valid_service_origin(row['service_origin'])
            or not _encoded(row['root_public_key'], 32)
            or any(not _matches('[a-f0-9]{64}', row[key]) for key in ('manifest_digest', 'service_fingerprint'))
            or not _integer(row['accepted_ms'], 9_007_199_254_740_991)
            or not _integer(row['expires_ms'], 9_007_199_254_740_991)
            or row['expires_ms'] <= row['accepted_ms']):
        raise ServiceReviewSchemaCorrupt()


def _manifest_shape(value: object) -> bool:
    if not isinstance(value, str) or not value.isascii() or not 0 < len(value) <= 16384:
        return False
    try:
        payload = json.loads(value)
    except (ValueError, RecursionError):
        return False
    # Bounded scalar array only, before canonicalization (no deep object walk).
    return (isinstance(payload, list) and len(payload) == 11
            and all(_matches(r'[\x20-\x7e]{1,512}', item) for item in payload[:10])
            and isinstance(payload[10], list) and len(payload[10]) <= 100
            and all(_matches('[A-Za-z0-9_-]{1,128}', item) for item in payload[10])
            and canonical_manifest_bytes(payload).decode('ascii') == value)


def _validate(db: sqlite3.Connection) -> None:
    _floor(db)
    if (_objects(db) != _expected(_CREATE)
            or [tuple(row) for row in db.execute('SELECT slot,version FROM mail_service_review_version')] != [(1, VERSION)]):
        raise ServiceReviewSchemaCorrupt()
    acceptance = db.execute('SELECT * FROM mail_service_review_acceptance').fetchall()
    if len(acceptance) > 1 or any(type(row['slot']) is not int or row['slot'] != 1 for row in acceptance):
        raise ServiceReviewSchemaCorrupt()
    seen = False
    for row in db.execute('SELECT * FROM mail_service_review_operations'):
        seen = True
        _public_row(row)
        if not _matches('[a-f0-9]{64}', row['input_digest']):
            raise ServiceReviewSchemaCorrupt()
    if not acceptance:
        if seen:
            raise ServiceReviewSchemaCorrupt()
        return
    row = acceptance[0]
    _public_row(row)
    if (not _integer(row['manifest_sequence'], 999_999_999_999_999)
            or not _matches('[A-Za-z0-9_-]{1,128}', row['service_key_id'])
            or not _encoded(row['service_public_key'], 32)
            or not _encoded(row['signature'], 64)
            or not _manifest_shape(row['manifest_json'])):
        raise ServiceReviewSchemaCorrupt()
    operation = db.execute('SELECT * FROM mail_service_review_operations WHERE op_id=?', (row['op_id'],)).fetchone()
    if operation is None or any(operation[key] != row[key] for key in _PUBLIC):
        raise ServiceReviewSchemaCorrupt()
    if db.execute('SELECT 1 FROM mail_enrollment_manifests WHERE origin=?', (row['service_origin'],)).fetchone() is None:
        raise ServiceReviewSchemaCorrupt()


def initialize(store: MailStore) -> None:
    """Explicit atomic bootstrap or checked no-op; no partial-state repair.

    Install the floor alone on a fresh store, never dormant Enrollment tables.
    A missing floor alongside legacy objects or installed review objects is
    damaged state, not a fresh setup. Existing floors are never written here.
    """
    with store._tx() as db:
        _base(db)
        review = _objects(db)
        if not _objects(db, floor=True):
            legacy = db.execute("SELECT 1 FROM sqlite_master WHERE lower(name) GLOB 'mail_enrollment_*' "
                                "OR lower(tbl_name) GLOB 'mail_enrollment_*' LIMIT 1").fetchone()
            if review or legacy:
                raise ServiceReviewSchemaCorrupt()
            db.execute(_FLOOR)
        _floor(db)
        if not review:
            for sql in _CREATE:
                db.execute(sql)
            db.execute('INSERT INTO mail_service_review_version VALUES(1,?)', (VERSION,))
        _validate(db)


def validate(store: MailStore) -> None:
    """Read-only snapshot check; never creates schema or grants review authority."""
    with store._read() as db:
        db.execute('BEGIN')
        try:
            _base(db)
            _validate(db)
        finally:
            db.execute('ROLLBACK')
