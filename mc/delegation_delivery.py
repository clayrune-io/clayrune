"""Durable delivery for child-agent completion notifications.

The child completion is written to the outbox before any transport is started.
The receiver writes the immutable event to its inbox before acknowledging it.
Neither side launches an agent: delivery and parent processing are separate.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.request
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional


MAX_ATTEMPTS = 8
LEASE_SECONDS = 30.0
BASE_BACKOFF_SECONDS = 1.0


class DeliveryDeferred(RuntimeError):
    """Parent is busy; safe to retry the same intent later."""


class DeliveryBlocked(RuntimeError):
    """Human/provider intervention is required; never fresh-dispatch."""


class DeliveryUncertain(RuntimeError):
    """A provider submission may have happened; never replay automatically."""


class DeliveryStore:
    """SQLite-backed child outbox and parent inbox.

    A connection is opened per operation, making close/reopen and process
    restart ordinary cases. ``BEGIN IMMEDIATE`` makes claim/ack idempotent.
    """

    def __init__(self, path: Path, *, max_attempts: int = MAX_ATTEMPTS,
                 clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max(1, int(max_attempts))
        self.clock = clock
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @contextmanager
    def _db(self):
        db = self._connect()
        try:
            yield db
        finally:
            db.close()

    def _init(self) -> None:
        with self._db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS outbox (
              event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
              parent_session_id TEXT NOT NULL, payload TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
              fence_token TEXT NOT NULL DEFAULT '', recovery_required INTEGER NOT NULL DEFAULT 0,
              last_error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS outbox_ready ON outbox(state, next_attempt, lease_until);
            CREATE TABLE IF NOT EXISTS inbox (
              event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
              parent_session_id TEXT NOT NULL, payload TEXT NOT NULL,
              state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
              fence_token TEXT NOT NULL DEFAULT '', recovery_required INTEGER NOT NULL DEFAULT 0,
              submit_evidence TEXT NOT NULL DEFAULT '',
              last_error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
              accepted_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS inbox_ready ON inbox(state, next_attempt, lease_until);
            CREATE TABLE IF NOT EXISTS completion_sources (
              event_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
              parent_session_id TEXT NOT NULL, payload TEXT NOT NULL,
              created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turn_allocations (
              child_session_id TEXT PRIMARY KEY, next_turn INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS revocations (
              identity TEXT PRIMARY KEY, project_id TEXT NOT NULL,
              session_id TEXT NOT NULL DEFAULT '', event_id TEXT NOT NULL DEFAULT '',
              scope TEXT NOT NULL, created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS revocations_project ON revocations(project_id);
            CREATE INDEX IF NOT EXISTS revocations_session ON revocations(project_id,session_id);
            CREATE TABLE IF NOT EXISTS project_generations (
              project_id TEXT PRIMARY KEY, generation INTEGER NOT NULL
            );
            """)
            # Upgrade databases created by the first draft without destructive
            # rewrites. SQLite has no ADD COLUMN IF NOT EXISTS.
            for table, column, definition in (
                ('outbox', 'fence_token', "TEXT NOT NULL DEFAULT ''"),
                ('outbox', 'recovery_required', "INTEGER NOT NULL DEFAULT 0"),
                ('inbox', 'fence_token', "TEXT NOT NULL DEFAULT ''"),
                ('inbox', 'recovery_required', "INTEGER NOT NULL DEFAULT 0"),
                ('inbox', 'submit_evidence', "TEXT NOT NULL DEFAULT ''"),
            ):
                columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                if column not in columns:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

    @staticmethod
    def _payload(project_id: str, parent_session_id: str,
                 payload: dict[str, Any]) -> str:
        # Canonical JSON is immutable and makes duplicate delivery byte-stable.
        envelope = {'project_id': project_id,
                    'parent_session_id': parent_session_id,
                    'payload': payload}
        if 'delivery_generation' in payload:
            envelope['delivery_generation'] = payload['delivery_generation']
        return json.dumps(envelope, ensure_ascii=False,
                           sort_keys=True, separators=(',', ':'))

    @staticmethod
    def _child_from_payload(encoded: str) -> str:
        try:
            value = json.loads(encoded).get('payload', {})
            return str(value.get('child_session_id') or '') if isinstance(value, dict) else ''
        except (TypeError, ValueError, AttributeError):
            return ''

    @staticmethod
    def _revoked(db: sqlite3.Connection, project_id: str, parent_session_id: str,
                 event_id: str = '', encoded: str = '') -> bool:
        generation = db.execute('SELECT generation FROM project_generations WHERE project_id=?',
                                (project_id,)).fetchone()
        current_generation = int(generation['generation']) if generation else 1
        try:
            requested_generation = json.loads(encoded).get('delivery_generation')
        except (TypeError, ValueError, AttributeError):
            requested_generation = None
        if current_generation > 1 and requested_generation != current_generation:
            return True
        if requested_generation is not None and requested_generation != current_generation:
            return True
        if db.execute('SELECT 1 FROM revocations WHERE identity=?',
                      (f'project:{project_id}',)).fetchone():
            return True
        child = DeliveryStore._child_from_payload(encoded)
        for sid in (parent_session_id, child):
            if sid and db.execute('SELECT 1 FROM revocations WHERE identity=?',
                                  (f'session:{project_id}:{sid}',)).fetchone():
                return True
        return bool(event_id and db.execute('SELECT 1 FROM revocations WHERE identity=?',
                                            (f'event:{project_id}:{event_id}',)).fetchone())

    def _revoke(self, project_id: str, *, session_id: str = '', event_id: str = '') -> None:
        """Purge delivery payloads and retain only a minimal replay tombstone."""
        if not project_id:
            raise ValueError('project identity required')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            now = self.clock()
            if not session_id and not event_id:
                current = db.execute('SELECT generation FROM project_generations WHERE project_id=?',
                                     (project_id,)).fetchone()
                next_generation = (int(current['generation']) + 1) if current else 2
                db.execute('INSERT INTO project_generations(project_id,generation) VALUES(?,?) '
                           'ON CONFLICT(project_id) DO UPDATE SET generation=excluded.generation',
                           (project_id, next_generation))
            if session_id:
                identity, scope = f'session:{project_id}:{session_id}', 'session'
            elif event_id:
                identity, scope = f'event:{project_id}:{event_id}', 'event'
            else:
                identity, scope = f'project:{project_id}', 'project'
            db.execute('INSERT OR IGNORE INTO revocations(identity,project_id,session_id,event_id,scope,created_at) VALUES(?,?,?,?,?,?)',
                       (identity, project_id, session_id, event_id, scope, now))
            for table in ('outbox', 'inbox', 'completion_sources'):
                rows = db.execute(f'SELECT event_id,parent_session_id,payload FROM {table} WHERE project_id=?',
                                  (project_id,)).fetchall()
                for row in rows:
                    if event_id:
                        matches = row['event_id'] == event_id
                    elif session_id:
                        matches = (row['parent_session_id'] == session_id or
                                   self._child_from_payload(row['payload']) == session_id)
                    else:
                        matches = True
                    if matches:
                        db.execute(f'DELETE FROM {table} WHERE event_id=? AND project_id=?',
                                   (row['event_id'], project_id))
                        db.execute('INSERT OR IGNORE INTO revocations(identity,project_id,session_id,event_id,scope,created_at) VALUES(?,?,?,?,?,?)',
                                   (f'event:{project_id}:{row["event_id"]}', project_id, session_id, row['event_id'], 'event', now))
            if not session_id and not event_id:
                for table in ('outbox', 'inbox', 'completion_sources'):
                    db.execute(f'DELETE FROM {table} WHERE project_id=?', (project_id,))
            db.execute('COMMIT')

    def revoke_session(self, project_id: str, session_id: str) -> None:
        if not session_id:
            raise ValueError('session identity required')
        self._revoke(project_id, session_id=session_id)

    def revoke_event(self, project_id: str, event_id: str) -> None:
        if not event_id:
            raise ValueError('event identity required')
        self._revoke(project_id, event_id=event_id)

    def revoke_project(self, project_id: str) -> None:
        self._revoke(project_id)

    def project_is_revoked(self, project_id: str) -> bool:
        if not project_id:
            raise ValueError('project identity required')
        with self._db() as db:
            return db.execute('SELECT 1 FROM revocations WHERE identity=?',
                              (f'project:{project_id}',)).fetchone() is not None

    def recreate_project(self, project_id: str) -> None:
        """Explicitly open a deleted id; historical event tombstones survive."""
        if not project_id:
            raise ValueError('project identity required')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT generation FROM project_generations WHERE project_id=?',
                                 (project_id,)).fetchone()
            next_generation = (int(current['generation']) + 1) if current else 1
            db.execute('INSERT INTO project_generations(project_id,generation) VALUES(?,?) '
                       'ON CONFLICT(project_id) DO UPDATE SET generation=excluded.generation',
                       (project_id, next_generation))
            db.execute("DELETE FROM revocations WHERE identity=? AND scope='project'",
                       (f'project:{project_id}',))
            db.execute('COMMIT')

    def project_generation(self, project_id: str) -> int:
        if not project_id:
            raise ValueError('project identity required')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT generation FROM project_generations WHERE project_id=?',
                             (project_id,)).fetchone()
            generation = int(row['generation']) if row else 1
            if row is None:
                db.execute('INSERT INTO project_generations(project_id,generation) VALUES(?,?)',
                           (project_id, generation))
            db.execute('COMMIT')
            return generation

    def enqueue(self, event_id: str, project_id: str, parent_session_id: str,
                payload: dict[str, Any]) -> bool:
        if not event_id or not project_id or not parent_session_id:
            return False
        now = self.clock()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            encoded = self._payload(project_id, parent_session_id, payload)
            if self._revoked(db, project_id, parent_session_id, event_id, encoded):
                db.execute('ROLLBACK')
                return False
            old = db.execute("SELECT project_id,parent_session_id,payload FROM outbox WHERE event_id=?",
                             (event_id,)).fetchone()
            if old is not None:
                if (old['project_id'], old['parent_session_id'], old['payload']) != (project_id, parent_session_id, encoded):
                    db.execute('ROLLBACK')
                    raise ValueError('conflicting completion event identity')
                db.execute('COMMIT')
                return False
            cur = db.execute(
                "INSERT OR IGNORE INTO outbox(event_id,project_id,parent_session_id,payload,created_at)"
                " VALUES(?,?,?,?,?)",
                (event_id, project_id, parent_session_id, encoded, now))
            db.execute('COMMIT')
            return cur.rowcount == 1

    def accept(self, event_id: str, project_id: str, parent_session_id: str,
               payload: dict[str, Any]) -> bool:
        """Persist receipt atomically; True only for a newly accepted event."""
        if not event_id or not project_id or not parent_session_id:
            raise ValueError('event identity and routing are required')
        now = self.clock()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            encoded = self._payload(project_id, parent_session_id, payload)
            if self._revoked(db, project_id, parent_session_id, event_id, encoded):
                db.execute('ROLLBACK')
                raise DeliveryBlocked('delivery identity has been revoked')
            trusted = db.execute("SELECT project_id,parent_session_id,payload FROM outbox WHERE event_id=?",
                                (event_id,)).fetchone()
            if trusted is None or (trusted['project_id'], trusted['parent_session_id'], trusted['payload']) != (project_id, parent_session_id, encoded):
                db.execute('ROLLBACK')
                raise ValueError('completion event is not a trusted dispatched child')
            old = db.execute("SELECT project_id,parent_session_id,payload FROM inbox WHERE event_id=?",
                             (event_id,)).fetchone()
            if old is not None:
                if (old['project_id'], old['parent_session_id'], old['payload']) != (project_id, parent_session_id, encoded):
                    db.execute('ROLLBACK')
                    raise ValueError('conflicting completion event identity')
                db.execute('COMMIT')
                return False
            cur = db.execute(
                "INSERT OR IGNORE INTO inbox(event_id,project_id,parent_session_id,payload,created_at,accepted_at)"
                " VALUES(?,?,?,?,?,?)",
                (event_id, project_id, parent_session_id, encoded, now, now))
            db.execute('COMMIT')
            return cur.rowcount == 1

    def _claim(self, table: str) -> Optional[dict[str, Any]]:
        now = self.clock()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(
                f"SELECT * FROM {table} WHERE state='pending' AND next_attempt<=? "
                "AND lease_until<=? ORDER BY created_at LIMIT 1", (now, now)).fetchone()
            if row is None:
                db.execute('COMMIT')
                return None
            lease = now + LEASE_SECONDS
            token = uuid.uuid4().hex
            new_state = 'dispatch_intent' if table == 'inbox' else 'pending'
            changed = db.execute(
                f"UPDATE {table} SET lease_until=?,fence_token=?,state=? "
                "WHERE event_id=? AND state='pending' AND lease_until<=?",
                (lease, token, new_state, row['event_id'], now)).rowcount
            if changed != 1:
                db.execute('ROLLBACK')
                return None
            db.execute('COMMIT')
            out = dict(row)
            out['fence_token'] = token
            out['state'] = new_state
            return out

    def claim_outbox(self) -> Optional[dict[str, Any]]:
        return self._claim('outbox')

    def claim_inbox(self) -> Optional[dict[str, Any]]:
        return self._claim('inbox')

    def revalidate_claim(self, table: str, event_id: str, token: str) -> bool:
        """Fence an attempt immediately before action, extending its lease."""
        now = self.clock()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute(
                f"UPDATE {table} SET lease_until=? WHERE event_id=? AND fence_token=? "
                "AND state=? AND lease_until>?",
                (now + LEASE_SECONDS, event_id, token,
                 'dispatch_intent' if table == 'inbox' else 'pending', now)).rowcount
            db.execute('COMMIT')
            return changed == 1

    def _finish(self, table: str, event_id: str, token: str, ok: bool,
                error: str = '', forced_state: str = '') -> None:
        now = self.clock()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(f"SELECT attempts FROM {table} WHERE event_id=?",
                             (event_id,)).fetchone()
            if row is None:
                db.execute('COMMIT')
                return
            attempts = int(row['attempts']) + 1
            if ok:
                state, next_at, detail = 'delivered' if table == 'outbox' else 'processed', now, ''
            elif forced_state:
                state = forced_state
                next_at = now + (min(300.0, BASE_BACKOFF_SECONDS * (2 ** (attempts - 1)))
                                if state == 'pending' else now)
                detail = error[:1000]
            elif attempts >= self.max_attempts:
                state, next_at, detail = 'recovery_required', now, error[:1000]
            else:
                state, next_at, detail = 'pending', now + min(300.0, BASE_BACKOFF_SECONDS * (2 ** (attempts - 1))), error[:1000]
            db.execute(f"UPDATE {table} SET state=?,attempts=?,next_attempt=?,lease_until=0,last_error=?,recovery_required=? "
                       "WHERE event_id=? AND fence_token=? AND state=?",
                       (state, attempts, next_at, detail,
                        1 if state in ('uncertain', 'recovery_required') else 0,
                        event_id, token, 'dispatch_intent' if table == 'inbox' else 'pending'))
            db.execute('COMMIT')

    def ack_outbox(self, event_id: str, token: str) -> None:
        self._finish('outbox', event_id, token, True)

    def finish_outbox(self, event_id: str, token: str, error: str) -> None:
        self._finish('outbox', event_id, token, False, error)

    def ack_inbox(self, event_id: str, token: str) -> None:
        self._finish('inbox', event_id, token, True)

    def submit_inbox(self, event_id: str, token: str, evidence: dict[str, Any]) -> None:
        """Record that parent submission was accepted; not task success."""
        # A durable Mode-B handoff must carry proof that the real stdin write
        # completed.  The legacy marker remains valid for Mode-A parents.
        write_ack = evidence.get('stdin_write_ack')
        if write_ack is not None and write_ack not in ('written', 'legacy'):
            raise ValueError('parent submission lacks a successful stdin write acknowledgment')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE inbox SET state='submitted',lease_until=0,last_error=?,submit_evidence=? "
                       "WHERE event_id=? AND fence_token=? AND state='dispatch_intent'",
                       ('parent submission accepted; task result not verified',
                        json.dumps(evidence, sort_keys=True), event_id, token))
            db.execute('COMMIT')

    def finish_inbox(self, event_id: str, token: str, error: str,
                     *, state: str = '') -> None:
        self._finish('inbox', event_id, token, False, error, state)

    def retry(self, table: str, event_id: str, project_id: str, *, reviewed: bool = False) -> None:
        """Explicit operator recovery for blocked/uncertain/recovery rows."""
        if table not in ('outbox', 'inbox'):
            raise ValueError('invalid delivery table')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute(f"SELECT state FROM {table} WHERE event_id=? AND project_id=?",
                             (event_id, project_id)).fetchone()
            if row is None:
                db.execute('COMMIT')
                return
            if row['state'] in ('uncertain', 'recovery_required') and not reviewed:
                db.execute('ROLLBACK')
                raise ValueError('reviewed resolution required before retrying uncertain delivery')
            db.execute(f"UPDATE {table} SET state='pending',recovery_required=0,next_attempt=?,lease_until=0,last_error='' WHERE event_id=? AND project_id=? AND state IN ('blocked','uncertain','recovery_required')",
                       (self.clock(), event_id, project_id))
            db.execute('COMMIT')

    def record_completion_source(self, event_id: str, project_id: str,
                                 parent_session_id: str, payload: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            encoded = self._payload(project_id, parent_session_id, payload)
            if self._revoked(db, project_id, parent_session_id, event_id, encoded):
                db.execute('ROLLBACK')
                return
            old = db.execute('SELECT project_id,parent_session_id,payload FROM completion_sources WHERE event_id=?', (event_id,)).fetchone()
            if old is not None and (old['project_id'], old['parent_session_id'], old['payload']) != (project_id, parent_session_id, encoded):
                db.execute('ROLLBACK')
                raise ValueError('conflicting completion source')
            db.execute('INSERT OR IGNORE INTO completion_sources(event_id,project_id,parent_session_id,payload,created_at) VALUES(?,?,?,?,?)',
                       (event_id, project_id, parent_session_id, encoded, self.clock()))
            db.execute('COMMIT')

    def recover_sources(self) -> int:
        recovered = 0
        with self._db() as db:
            rows = db.execute('SELECT * FROM completion_sources ORDER BY created_at').fetchall()
        for row in rows:
            with self._db() as db:
                if self._revoked(db, row['project_id'], row['parent_session_id'],
                                 row['event_id'], row['payload']):
                    continue
            raw = json.loads(row['payload'])
            if self.enqueue(row['event_id'], row['project_id'], row['parent_session_id'], raw['payload']):
                recovered += 1
        return recovered

    def allocate_turn(self, child_session_id: str) -> int:
        if not child_session_id:
            raise ValueError('child session id required')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT next_turn FROM turn_allocations WHERE child_session_id=?', (child_session_id,)).fetchone()
            turn = int(row['next_turn']) if row else 1
            db.execute('INSERT INTO turn_allocations(child_session_id,next_turn) VALUES(?,?) ON CONFLICT(child_session_id) DO UPDATE SET next_turn=excluded.next_turn', (child_session_id, turn + 1))
            db.execute('COMMIT')
            return turn

    def reconcile(self) -> int:
        """Turn expired inbox dispatch intents into explicit uncertainty."""
        now = self.clock()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            cur = db.execute("UPDATE inbox SET state='uncertain',recovery_required=1,lease_until=0,last_error=? "
                             "WHERE state='dispatch_intent' AND lease_until<?",
                             ('provider submission status unknown after restart', now))
            db.execute('COMMIT')
            return cur.rowcount

    def status(self, table: str, event_id: str, project_id: str) -> Optional[dict[str, Any]]:
        if table not in ('outbox', 'inbox'):
            raise ValueError('invalid delivery table')
        with self._db() as db:
            row = db.execute(f"SELECT * FROM {table} WHERE event_id=? AND project_id=?",
                             (event_id, project_id)).fetchone()
            return dict(row) if row else None

    def payload_usage(self, project_id: str) -> dict[str, Any]:
        """Aggregate logical delivery payload usage for one project.

        ``payload`` is stored as SQLite TEXT.  Casting it to BLOB before
        LENGTH counts the encoded UTF-8 bytes without materialising payload
        rows in Python.  This deliberately excludes agent-log/native
        transcripts and any install-wide SQLite/WAL size.
        """
        if not project_id:
            raise ValueError('project identity required')
        with self._db() as db:
            rows = db.execute(
                """SELECT 'completion_sources' AS table_name, COUNT(*) AS row_count,
                          COALESCE(SUM(LENGTH(CAST(payload AS BLOB))), 0) AS payload_bytes
                     FROM completion_sources WHERE project_id=?
                   UNION ALL
                   SELECT 'outbox' AS table_name, COUNT(*) AS row_count,
                          COALESCE(SUM(LENGTH(CAST(payload AS BLOB))), 0) AS payload_bytes
                     FROM outbox WHERE project_id=?
                   UNION ALL
                   SELECT 'inbox' AS table_name, COUNT(*) AS row_count,
                          COALESCE(SUM(LENGTH(CAST(payload AS BLOB))), 0) AS payload_bytes
                     FROM inbox WHERE project_id=?""",
                (project_id, project_id, project_id),
            ).fetchall()
        tables = {
            row['table_name']: {
                'row_count': int(row['row_count']),
                'payload_bytes': int(row['payload_bytes']),
            }
            for row in rows
        }
        return {
            'tables': tables,
            'row_count': sum(item['row_count'] for item in tables.values()),
            'payload_bytes': sum(item['payload_bytes'] for item in tables.values()),
        }

    def list_recovery_status(self, project_id: str, *, limit: int = 50,
                             offset: int = 0) -> dict[str, Any]:
        """List recoverable delivery state without exposing completion payloads."""
        if not project_id:
            raise ValueError('project identity required')
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        states = ('pending', 'blocked', 'uncertain', 'recovery_required')
        placeholders = ','.join('?' for _ in states)
        with self._db() as db:
            params = [project_id, *states, project_id, *states]
            total = db.execute(
                f"SELECT COUNT(*) FROM (SELECT event_id FROM outbox "
                f"WHERE project_id=? AND state IN ({placeholders}) UNION ALL "
                f"SELECT event_id FROM inbox WHERE project_id=? AND state IN ({placeholders}))",
                params).fetchone()[0]
            rows = db.execute(
                f"SELECT 'outbox' AS table_name,event_id,parent_session_id,state,attempts,"
                f"next_attempt,last_error,recovery_required,created_at FROM outbox "
                f"WHERE project_id=? AND state IN ({placeholders}) UNION ALL "
                f"SELECT 'inbox' AS table_name,event_id,parent_session_id,state,attempts,"
                f"next_attempt,last_error,recovery_required,created_at FROM inbox "
                f"WHERE project_id=? AND state IN ({placeholders}) "
                "ORDER BY created_at DESC, table_name ASC, event_id DESC LIMIT ? OFFSET ?",
                [*params, limit, offset]).fetchall()
        def safe_reason(state: str, raw: str) -> tuple[str, str]:
            if state == 'uncertain':
                return 'submission_outcome_unknown', 'Submission outcome is unknown; review required.'
            if state == 'blocked':
                return 'parent_unavailable', 'Parent is unavailable or busy.'
            if state == 'recovery_required':
                return 'recovery_required', 'Retry limit reached; explicit recovery is required.'
            if raw:
                return 'delivery_failed', 'Delivery attempt failed; explicit recovery may be required.'
            return 'awaiting_delivery', 'Awaiting delivery.'
        return {
            'items': [{
                'table': row['table_name'],
                'event_id': row['event_id'],
                'parent_session_id': row['parent_session_id'],
                'state': row['state'],
                'attempts': int(row['attempts']),
                'next_attempt': row['next_attempt'],
                'reason_code': safe_reason(row['state'], row['last_error'])[0],
                'reason': safe_reason(row['state'], row['last_error'])[1],
                'recovery_required': bool(row['recovery_required']),
                'created_at': row['created_at'],
            } for row in rows],
            'total': int(total), 'limit': limit, 'offset': offset,
        }

    def recover_outbox(self, entries: list[dict[str, Any]], project_id: str) -> int:
        """Rebuild missing notifications from the durable agent log source."""
        recovered = 0
        for entry in entries:
            parent = (entry.get('spawned_by_session_id') or '').strip()
            child = (entry.get('session_id') or '').strip()
            if (not parent or not child or entry.get('incognito') or
                    entry.get('status') not in ('completed', 'error', 'stopped')):
                continue
            exact = entry.get('delegation_completion')
            if not isinstance(exact, dict):
                # Pre-contract rows have only a truncated display summary and
                # are not safe to reinterpret as immutable completion events.
                continue
            event_id = exact.get('event_id') or event_id_for_turn(
                child, int(entry.get('delegation_turn', 1)))
            if self.enqueue(event_id, project_id, parent, exact):
                recovered += 1
        return recovered


def event_id_for_turn(child_session_id: str, turn_number: int) -> str:
    return f'{child_session_id}:turn:{max(1, int(turn_number))}'


def callback_payload(child: dict[str, Any], summary: str, event_id: str) -> dict[str, Any]:
    character = child.get('character')
    if isinstance(character, dict):
        who = character.get('agent_name') or character.get('display_name') or character.get('name') or 'agent'
    else:
        who = character or child.get('provider') or 'agent'
    result = {'event_id': event_id, 'child_session_id': child.get('session_id', ''),
            'who': who, 'status': child.get('status', 'unknown'),
            'task': child.get('task', ''), 'summary': summary or '',
            'provider': child.get('provider', 'claude'),
            'model': child.get('agent_model') or child.get('model') or '',
            'message': (f"[dispatched agent finished] {who} (session {child.get('session_id', '')[:12]}) "
                        f"ended with status={child.get('status', 'unknown')}.\n\nTask: {child.get('task', '')}\n\n"
                        f"Its final message:\n{summary or ''}\n\nThis is the callback you asked for at dispatch. Continue "
                        "the work it was part of -- do not re-dispatch it.")}
    if '_delivery_generation' in child:
        result['delivery_generation'] = child['_delivery_generation']
    return result


def drain_once(store: DeliveryStore, *, send_outbox: Callable[[dict[str, Any]], None],
               process_inbox: Callable[[dict[str, Any]], Any]) -> int:
    """Drain at most one item of each kind; safe to call concurrently."""
    # Restart may occur after a fresh claim was persisted but before the next
    # loop tick. Reconcile every drain, not only once at boot.
    store.reconcile()
    store.recover_sources()
    done = 0
    row = store.claim_outbox()
    if row:
        try:
            send_outbox(row)
            store.ack_outbox(row['event_id'], row['fence_token'])
            done += 1
        except Exception as exc:
            store.finish_outbox(row['event_id'], row['fence_token'], str(exc))
    row = store.claim_inbox()
    if row:
        try:
            evidence = process_inbox(row) or {'event_id': row['event_id']}
            store.submit_inbox(row['event_id'], row['fence_token'], evidence)
            done += 1
        except Exception as exc:
            state = 'blocked' if isinstance(exc, DeliveryBlocked) else ('pending' if isinstance(exc, DeliveryDeferred) else 'uncertain')
            store.finish_inbox(row['event_id'], row['fence_token'], str(exc), state=state)
    return done
