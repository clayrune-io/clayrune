"""Unit 6 immutable continuation snapshots and real delivery-attempt receipts."""
import re
import sqlite3

STATEMENTS = (
    '''CREATE TABLE IF NOT EXISTS mail_continuations (
       thread_hash TEXT PRIMARY KEY, snapshot TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS mail_turn_receipts (
       event_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL UNIQUE,
       project_id TEXT NOT NULL, session_id TEXT NOT NULL,
       provider TEXT NOT NULL, native_id TEXT NOT NULL, project_generation INTEGER NOT NULL,
       started INTEGER NOT NULL DEFAULT 0, accepted INTEGER NOT NULL DEFAULT 0,
       completed INTEGER NOT NULL DEFAULT 0)''',
)


def migrate(db: sqlite3.Connection, prior: int) -> None:
    for sql in STATEMENTS:
        if prior < 3:
            db.execute(sql)
        normalized = ' '.join(sql.replace('IF NOT EXISTS ', '').split())
        name = re.search(r'CREATE TABLE (\w+)', normalized)
        assert name is not None
        row = db.execute('SELECT sql FROM sqlite_master WHERE name=?', (name[1],)).fetchone()
        if row is None or ' '.join(row[0].split()) != normalized:
            raise sqlite3.DatabaseError('malformed mail continuation schema')


def cleanup(db: sqlite3.Connection, now: float) -> None:
    """Question content has the binding's lifetime, including explicit revocation."""
    db.execute('DELETE FROM mail_continuations WHERE thread_hash NOT IN '
               '(SELECT b.thread_hash FROM thread_bindings b JOIN registrations r '
               'ON r.registration_id=b.registration_id WHERE b.revoked_at IS NULL '
               'AND b.expires_at>? AND r.enabled=1 AND r.generation=b.owner_generation)', (now,))
    db.execute('DELETE FROM mail_turn_receipts WHERE event_id NOT IN (SELECT event_id FROM events)')
