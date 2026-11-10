"""Additive unit-4 schema, in the unit-1 database (no second custody store)."""
from __future__ import annotations

import re
import sqlite3

VERSION = 3

_STATEMENTS = (
    '''CREATE TABLE IF NOT EXISTS mail_service_pins (
       registration_id TEXT NOT NULL, generation INTEGER NOT NULL,
       key_id TEXT NOT NULL, public_key TEXT NOT NULL, route_path TEXT NOT NULL,
       PRIMARY KEY(registration_id,generation))''',
    '''CREATE TABLE IF NOT EXISTS mail_transport_threads (
       registration_id TEXT NOT NULL, generation INTEGER NOT NULL,
       thread_id TEXT NOT NULL, thread_hash TEXT NOT NULL,
       PRIMARY KEY(registration_id,generation,thread_id),
       UNIQUE(registration_id,generation,thread_hash))''',
    '''CREATE TABLE IF NOT EXISTS mail_ingress_nonces (
       direction TEXT NOT NULL, registration_id TEXT NOT NULL,
       generation INTEGER NOT NULL, key_id TEXT NOT NULL, nonce TEXT NOT NULL,
       expires_at INTEGER NOT NULL,
       PRIMARY KEY(direction,registration_id,generation,key_id,nonce))''',
    'CREATE INDEX IF NOT EXISTS mail_nonce_expiry ON mail_ingress_nonces(expires_at)',
    '''CREATE TABLE IF NOT EXISTS mail_ingress_receipts (
       event_id TEXT PRIMARY KEY, envelope_digest TEXT NOT NULL,
       body_digest TEXT NOT NULL, expires_at INTEGER NOT NULL)''',
)


def migrate(db: sqlite3.Connection) -> None:
    """Serialize migrations with other opens and runtime consent/receive writes."""
    db.execute('BEGIN IMMEDIATE')
    try:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (1, 2, VERSION):
            raise sqlite3.DatabaseError('malformed agent mail ingress schema version')
        if version == 1:
            for statement in _STATEMENTS:
                db.execute(statement)
        # A v2 inbox losing its replay/custody table must fail closed rather
        # than recreate an empty one. Validate SQL constraints as well as columns.
        for statement in _STATEMENTS:
            normalized = ' '.join(statement.replace('IF NOT EXISTS ', '').split())
            name = re.search(r'CREATE (?:TABLE|INDEX) (\w+)', normalized)
            assert name is not None
            row = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name[1],)).fetchone()
            if row is None or ' '.join(row[0].split()) != normalized:
                raise sqlite3.DatabaseError('malformed agent mail ingress schema')
        from mc.agent_mail_wake_schema import migrate as migrate_wake
        migrate_wake(db, version)
        db.execute(f'PRAGMA user_version={VERSION}')
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise
