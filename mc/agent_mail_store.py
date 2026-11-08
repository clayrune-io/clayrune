"""Local durable inbox for agent mail (MC-1166 build unit 1 of 7).

Spec: docs/AGENT_MAIL_SPEC.md ("Offline queue, replay and local durable
inbox", "Data, privacy and deletion contract", D4, "Inbound hardening").
Dedicated SQLite file, OUTSIDE ``data/projects/`` (``DATA_DIR`` pollution
rule): use :func:`default_path`. Nothing here is wired to a route, flag or
server start; later units call it.

Conventions follow ``mc/delegation_delivery.py``: a connection per
operation, ``BEGIN IMMEDIATE`` for every write, WAL + ``synchronous=FULL``
so ``insert_event`` returns only after the commit is durable, fence-token
leases, and a crash after a possible provider write becomes ``uncertain``,
never an automatic replay.

What it guarantees (each pinned in tests/test_agent_mail_store.py):

* Event states ``worker_pending -> local_received -> laundering -> ready ->
  submitting -> submitted -> turn_completed`` plus ``retryable``,
  ``blocked``, ``uncertain``, ``rejected``, ``expired``, ``revoked``.
  Every move goes through ``TRANSITIONS``; an illegal one raises.
  (``worker_pending`` is Worker-side custody; a local row starts at
  ``local_received``.)
* Dedupe key = (registration, thread, incoming Message-ID) with a
  bounded-content digest. Same key + same digest resolves to the first
  event. Same key + different digest is a visible ``blocked`` hold. Equal
  text under two Message-IDs is two events.
* Receipt order per conversation (project, session) is the commit order
  (``seq``). A later event cannot be claimed for submission while an
  earlier one is still unsettled.
* Expiry, queue exhaustion and revocation always leave a visible terminal
  row or a metadata rejection record, never a silent delete. Terminal rows
  drop their content and keep metadata until ``purge_terminal``. A
  `submitted` row whose completion never arrives becomes a visible `expired`
  after ``completion_window_seconds``; revocation, session and thread
  deletion purge the bodies of `submitted` rows too (state and metadata
  stay, so the in-flight turn can still complete).
* Reconciliation settles a claim only after its lease has run out; it never
  touches a live lease, so a second process starting up cannot break the
  first one's valid acceptance.
* The body-size bound holds on every insert path: an oversize body is never
  stored, whatever else holds the event.
* A corrupt, foreign or zero-byte store file raises ``MailStoreCorrupt``
  and the file is never replaced; the instance then refuses all work.
* The raw thread token is never stored, only its SHA-256.
* Rejection records hold time, a closed reason code and the registration
  id. No body, sender or token.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, NoReturn, Optional

from mc.core import _log


SCHEMA_VERSION = 1
APPLICATION_ID = 0x4D434D4C  # 'MCML'
DB_FILENAME = 'agent_mail.sqlite3'

# --- event vocabulary ------------------------------------------------------
WORKER_PENDING = 'worker_pending'
LOCAL_RECEIVED = 'local_received'
LAUNDERING = 'laundering'
READY = 'ready'
SUBMITTING = 'submitting'
SUBMITTED = 'submitted'
TURN_COMPLETED = 'turn_completed'
RETRYABLE = 'retryable'
BLOCKED = 'blocked'
UNCERTAIN = 'uncertain'
REJECTED = 'rejected'
EXPIRED = 'expired'
REVOKED = 'revoked'

STATES = (WORKER_PENDING, LOCAL_RECEIVED, LAUNDERING, READY, SUBMITTING, SUBMITTED,
          TURN_COMPLETED, RETRYABLE, BLOCKED, UNCERTAIN, REJECTED, EXPIRED, REVOKED)
TERMINAL_STATES = frozenset({TURN_COMPLETED, REJECTED, EXPIRED, REVOKED})

# Explicit allowed-transition table. Anything not listed is refused.
TRANSITIONS: dict[str, frozenset[str]] = {
    WORKER_PENDING: frozenset({LOCAL_RECEIVED, REJECTED, EXPIRED, REVOKED}),
    LOCAL_RECEIVED: frozenset({LAUNDERING, BLOCKED, REJECTED, EXPIRED, REVOKED}),
    LAUNDERING: frozenset({READY, RETRYABLE, BLOCKED, REJECTED, REVOKED}),
    READY: frozenset({SUBMITTING, BLOCKED, REJECTED, EXPIRED, REVOKED}),
    # submitting -> ready is a release with nothing handed to the provider
    # (session busy). A possible handoff only ever goes to submitted/uncertain.
    SUBMITTING: frozenset({SUBMITTED, READY, RETRYABLE, BLOCKED, UNCERTAIN, REVOKED}),
    SUBMITTED: frozenset({TURN_COMPLETED, EXPIRED}),
    RETRYABLE: frozenset({LAUNDERING, SUBMITTING, BLOCKED, REJECTED, EXPIRED, REVOKED}),
    BLOCKED: frozenset({LOCAL_RECEIVED, READY, REJECTED, EXPIRED, REVOKED}),
    UNCERTAIN: frozenset({READY, SUBMITTED, REJECTED, EXPIRED, REVOKED}),
    TURN_COMPLETED: frozenset(),
    REJECTED: frozenset(),
    EXPIRED: frozenset(),
    REVOKED: frozenset(),
}

# Earlier events in these states hold back later ones in the same conversation.
_UNSETTLED = (WORKER_PENDING, LOCAL_RECEIVED, LAUNDERING, READY, SUBMITTING,
              RETRYABLE, BLOCKED, UNCERTAIN)
_EXPIRABLE = (LOCAL_RECEIVED, READY, RETRYABLE, BLOCKED, UNCERTAIN)
# Cancelled by revocation. `submitted` was already handed over and stays.
_CANCELLABLE = (WORKER_PENDING, LOCAL_RECEIVED, LAUNDERING, READY, SUBMITTING,
                RETRYABLE, BLOCKED, UNCERTAIN)

# Closed set of metadata-only rejection reasons: nothing free-form can carry
# content into the rejection log.
REJECTION_REASONS = frozenset({
    # Refusals made by this store.
    'unknown_registration', 'unknown_thread', 'thread_expired', 'thread_revoked',
    'registration_disabled', 'stale_generation', 'queue_full', 'event_id_conflict',
    # Edge decisions the receiver may report so one log shows them all.
    'not_owner', 'auth_failed', 'bad_token', 'malformed', 'oversize_mime',
    'loop_mail', 'unsupported_content',
})

_CODE_RE = re.compile(r'^[a-z0-9_]{1,48}$')


@dataclass(frozen=True)
class MailStoreConfig:
    """Operating limits. Defaults are D4's PROVISIONAL starting values
    (Marlow, 2026-11-08), pending a capacity check; they are config, not law."""
    queue_ttl_seconds: float = 30 * 86400.0        # offline queue / hold retention
    completion_window_seconds: float = 86400.0     # submitted -> turn_completed, else expired
    thread_ttl_seconds: float = 90 * 86400.0
    terminal_retention_seconds: float = 30 * 86400.0  # dedupe + terminal metadata
    max_body_bytes: int = 64 * 1024                # inbound text kept in custody
    max_reply_bytes: int = 16 * 1024               # extracted reply_text
    max_active_events: int = 600                   # per registration (20/day x 30d)
    lease_seconds: float = 30.0
    max_attempts: int = 8
    base_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 300.0


class MailStoreError(RuntimeError):
    """Storage failed. Visible; delivery does not continue silently."""


class MailStoreCorrupt(MailStoreError):
    """The store file is damaged or is not ours. Never auto-replaced."""


class IllegalTransition(ValueError):
    """The move is not in ``TRANSITIONS`` (or the event is not where expected)."""


class StaleClaim(RuntimeError):
    """The fence token no longer owns the event."""


@dataclass(frozen=True)
class InsertResult:
    """``ack`` is True when the caller may tell the Worker custody is settled
    and its copy may go: accepted, duplicate, held, or refused with nothing to
    deliver. It is False only when the event was NOT taken and the Worker must
    keep it (queue_full, event_id_conflict)."""
    status: str            # accepted | duplicate | held | refused
    event_id: str          # the first event for duplicates
    state: str
    reason: str
    ack: bool


def is_allowed(src: str, dst: str) -> bool:
    return dst in TRANSITIONS.get(src, frozenset())


def hash_thread_token(token: str) -> str:
    if not isinstance(token, str) or not token:
        raise ValueError('thread token required')
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def default_path(data_dir: Any) -> Path:
    """Sibling of ``DATA_DIR``, never inside it (``load_projects`` reads every
    ``*.json`` under ``data/projects/``)."""
    return Path(data_dir).parent / DB_FILENAME


def _code(value: str) -> str:
    if not isinstance(value, str) or not _CODE_RE.match(value):
        raise ValueError('reason must be a short [a-z0-9_] code, never free text')
    return value


def _bounded_digest(body: str, limit: int) -> tuple[str, int]:
    """(digest, byte length) over the bounded content: length plus at most
    `limit` bytes, so an oversize body still yields a stable comparison key."""
    raw = body.encode('utf-8')
    return hashlib.sha256(str(len(raw)).encode() + b'\n' + raw[:limit]).hexdigest(), len(raw)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS registrations (
  registration_id TEXT PRIMARY KEY, generation INTEGER NOT NULL,
  enabled INTEGER NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS thread_bindings (
  thread_hash TEXT PRIMARY KEY, registration_id TEXT NOT NULL,
  project_id TEXT NOT NULL, session_id TEXT NOT NULL,
  owner_generation INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'note',
  created_at REAL NOT NULL, expires_at REAL NOT NULL, revoked_at REAL
);
CREATE INDEX IF NOT EXISTS thread_bindings_session ON thread_bindings(project_id, session_id);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
  registration_id TEXT NOT NULL, thread_hash TEXT NOT NULL,
  project_id TEXT NOT NULL, session_id TEXT NOT NULL, generation INTEGER NOT NULL,
  message_id TEXT NOT NULL DEFAULT '', dedupe_key TEXT NOT NULL,
  body_digest TEXT NOT NULL, body_len INTEGER NOT NULL,
  state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',
  attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
  lease_until REAL NOT NULL DEFAULT 0, fence_token TEXT NOT NULL DEFAULT '',
  body TEXT, laundered TEXT, submit_evidence TEXT NOT NULL DEFAULT '',
  turn_id TEXT NOT NULL DEFAULT '', conflict_of TEXT NOT NULL DEFAULT '',
  received_at REAL NOT NULL, state_at REAL NOT NULL, expires_at REAL NOT NULL,
  terminal_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS events_dedupe ON events(dedupe_key, body_digest);
CREATE INDEX IF NOT EXISTS events_conversation ON events(project_id, session_id, seq);
CREATE INDEX IF NOT EXISTS events_state ON events(state, next_attempt);
CREATE INDEX IF NOT EXISTS events_registration ON events(registration_id, state);
CREATE TABLE IF NOT EXISTS rejections (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL,
  reason TEXT NOT NULL, registration_id TEXT NOT NULL DEFAULT ''
);
"""


class MailStore:
    """SQLite-backed local mail inbox. One instance per process is the intent;
    several are safe (every write is ``BEGIN IMMEDIATE``)."""

    def __init__(self, path: Path, *, config: Optional[MailStoreConfig] = None,
                 clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.config = config or MailStoreConfig()
        self.clock = clock
        self._lock = threading.RLock()
        self._failure: Optional[MailStoreError] = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._open_checked()

    # --- connection + failure handling ------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('PRAGMA busy_timeout=5000')
        return conn

    def _raise_storage(self, exc: sqlite3.Error) -> NoReturn:
        code = getattr(exc, 'sqlite_errorcode', None)
        base = (code & 0xFF) if isinstance(code, int) else None
        text = str(exc).lower()
        if base in (11, 26) or 'malformed' in text or 'not a database' in text:
            err: MailStoreError = MailStoreCorrupt(
                f'agent mail store is damaged ({type(exc).__name__}); delivery stopped, '
                f'the file was not replaced')
            self._failure = err  # latched: this instance does no more work
            _log(f'[agent-mail] store corrupt, delivery stopped: {type(exc).__name__}',
                 flush=True)
            raise err from exc
        _log(f'[agent-mail] store operation failed: {type(exc).__name__}', flush=True)
        raise MailStoreError(f'agent mail store operation failed ({type(exc).__name__})') from exc

    def _check_usable(self) -> None:
        if self._failure is not None:
            raise self._failure

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One BEGIN IMMEDIATE transaction. Returns to the caller only after
        COMMIT, i.e. durable. Our own exceptions roll back and propagate."""
        self._check_usable()
        with self._lock:
            db: Optional[sqlite3.Connection] = None
            try:
                db = self._connect()
                db.execute('BEGIN IMMEDIATE')
                try:
                    yield db
                except BaseException:
                    if db.in_transaction:
                        db.execute('ROLLBACK')
                    raise
                db.execute('COMMIT')
            except sqlite3.Error as exc:
                self._raise_storage(exc)
            finally:
                if db is not None:
                    db.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        self._check_usable()
        db: Optional[sqlite3.Connection] = None
        try:
            db = self._connect()
            yield db
        except sqlite3.Error as exc:
            self._raise_storage(exc)
        finally:
            if db is not None:
                db.close()

    def _open_checked(self) -> None:
        """Open an existing store or create a fresh one. A file that exists but
        is not a healthy mail store raises; it is never recreated."""
        existed = self.path.exists()
        if existed:
            wal = Path(str(self.path) + '-wal')
            wal_has_data = wal.exists() and wal.stat().st_size > 0
            # Raw header read BEFORE sqlite touches the file: opening a foreign
            # database to inspect it would switch it to WAL. A live WAL can hold
            # the header pages, so the raw check only runs without one.
            if not wal_has_data:
                if self.path.stat().st_size == 0:
                    self._fail_open('agent mail store file is empty (zero bytes); '
                                    'refusing to start from nothing')
                with open(self.path, 'rb') as fh:
                    head = fh.read(100)
                if len(head) < 100 or not head.startswith(b'SQLite format 3\x00'):
                    self._fail_open('agent mail store file is not a SQLite database')
                if int.from_bytes(head[68:72], 'big') != APPLICATION_ID:
                    self._fail_open('file is not an agent mail store')
        try:
            db = self._connect()
            try:
                check = db.execute('PRAGMA quick_check').fetchone()
                if not check or str(check[0]).lower() != 'ok':
                    raise MailStoreCorrupt('agent mail store failed its integrity check')
                app = db.execute('PRAGMA application_id').fetchone()[0]
                version = db.execute('PRAGMA user_version').fetchone()[0]
                tables = db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
                if app == 0 and version == 0 and tables == 0:
                    if existed:
                        raise MailStoreCorrupt('agent mail store file holds no schema; '
                                               'refusing to start from nothing')
                    db.executescript(_SCHEMA)
                    db.execute(f'PRAGMA application_id={APPLICATION_ID}')
                    db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                elif app != APPLICATION_ID:
                    raise MailStoreCorrupt('file is not an agent mail store')
                elif version > SCHEMA_VERSION:
                    raise MailStoreError(f'store schema v{version} is newer than this code '
                                         f'(v{SCHEMA_VERSION}); not downgrading')
                else:
                    db.executescript(_SCHEMA)  # IF NOT EXISTS: idempotent
            finally:
                db.close()
        except MailStoreError as exc:
            self._failure = exc
            raise
        except sqlite3.Error as exc:
            try:
                self._raise_storage(exc)
            except MailStoreError as wrapped:
                self._failure = wrapped
                raise

    def _fail_open(self, message: str) -> NoReturn:
        err = MailStoreCorrupt(message)
        self._failure = err
        _log(f'[agent-mail] {message}; delivery stopped, file left untouched', flush=True)
        raise err

    def health(self) -> dict[str, Any]:
        """Visible storage state for a status surface."""
        if self._failure is not None:
            return {'ok': False, 'error': str(self._failure),
                    'corrupt': isinstance(self._failure, MailStoreCorrupt)}
        return {'ok': True, 'error': '', 'corrupt': False}

    # --- registrations + thread bindings -----------------------------------
    def register(self, registration_id: str) -> int:
        """Create (generation 1, enabled) if new; returns the current generation."""
        if not registration_id:
            raise ValueError('registration id required')
        with self._tx() as db:
            row = db.execute('SELECT generation FROM registrations WHERE registration_id=?',
                             (registration_id,)).fetchone()
            if row is not None:
                return int(row['generation'])
            db.execute('INSERT INTO registrations VALUES(?,?,?,?)',
                       (registration_id, 1, 1, self.clock()))
            return 1

    def registration_state(self, registration_id: str) -> Optional[dict[str, Any]]:
        with self._read() as db:
            row = db.execute('SELECT * FROM registrations WHERE registration_id=?',
                             (registration_id,)).fetchone()
            return dict(row) if row else None

    def revoke_registration(self, registration_id: str) -> int:
        """Disable, bump the generation and cancel every queued event. Returns
        the number cancelled. Bindings from older generations stay unusable
        after a later `enable_registration`."""
        with self._tx() as db:
            row = db.execute('SELECT generation FROM registrations WHERE registration_id=?',
                             (registration_id,)).fetchone()
            if row is None:
                raise ValueError('unknown registration')
            now = self.clock()
            db.execute('UPDATE registrations SET enabled=0,generation=generation+1,updated_at=? '
                       'WHERE registration_id=?', (now, registration_id))
            db.execute('UPDATE thread_bindings SET revoked_at=? WHERE registration_id=? '
                       'AND revoked_at IS NULL', (now, registration_id))
            return self._cancel(db, 'registration_id=?', (registration_id,), 'registration_revoked')

    def enable_registration(self, registration_id: str) -> int:
        """Re-enable after a revoke. Returns the (already bumped) generation."""
        with self._tx() as db:
            row = db.execute('SELECT generation FROM registrations WHERE registration_id=?',
                             (registration_id,)).fetchone()
            if row is None:
                raise ValueError('unknown registration')
            db.execute('UPDATE registrations SET enabled=1,updated_at=? WHERE registration_id=?',
                       (self.clock(), registration_id))
            return int(row['generation'])

    def bind_thread(self, registration_id: str, thread_token: str, project_id: str,
                    session_id: str, *, kind: str = 'note',
                    expires_at: Optional[float] = None) -> bool:
        """Bind a thread token HASH to project/session at the registration's
        current owner generation. The token itself is not stored. True if new;
        an identical re-bind is a no-op; a different target is refused."""
        if not (registration_id and project_id and session_id):
            raise ValueError('registration, project and session are required')
        thash = hash_thread_token(thread_token)
        with self._tx() as db:
            reg = db.execute('SELECT generation,enabled FROM registrations WHERE registration_id=?',
                             (registration_id,)).fetchone()
            if reg is None or not reg['enabled']:
                raise ValueError('registration is unknown or disabled')
            old = db.execute('SELECT * FROM thread_bindings WHERE thread_hash=?', (thash,)).fetchone()
            if old is not None:
                if (old['registration_id'], old['project_id'], old['session_id'],
                        old['owner_generation']) != (registration_id, project_id, session_id,
                                                     reg['generation']):
                    raise ValueError('thread token is already bound to a different target')
                return False
            now = self.clock()
            db.execute('INSERT INTO thread_bindings VALUES(?,?,?,?,?,?,?,?,NULL)',
                       (thash, registration_id, project_id, session_id, reg['generation'], kind,
                        now, expires_at if expires_at is not None
                        else now + self.config.thread_ttl_seconds))
            return True

    def get_thread(self, thread_token: str) -> Optional[dict[str, Any]]:
        """Binding for a token (looked up by hash). The token is never in it."""
        with self._read() as db:
            row = db.execute('SELECT * FROM thread_bindings WHERE thread_hash=?',
                             (hash_thread_token(thread_token),)).fetchone()
            return dict(row) if row else None

    def revoke_thread(self, thread_token: str) -> int:
        thash = hash_thread_token(thread_token)
        with self._tx() as db:
            db.execute('UPDATE thread_bindings SET revoked_at=? WHERE thread_hash=? '
                       'AND revoked_at IS NULL', (self.clock(), thash))
            return self._cancel(db, 'thread_hash=?', (thash,), 'thread_revoked')

    def revoke_session(self, project_id: str, session_id: str) -> int:
        """Session closed/deleted: revoke its threads and cancel its queue."""
        with self._tx() as db:
            db.execute('UPDATE thread_bindings SET revoked_at=? WHERE project_id=? '
                       'AND session_id=? AND revoked_at IS NULL',
                       (self.clock(), project_id, session_id))
            return self._cancel(db, 'project_id=? AND session_id=?',
                                (project_id, session_id), 'session_revoked')

    # --- internal transitions ----------------------------------------------
    def _move(self, db: sqlite3.Connection, event_id: str, expect: str, new: str, *,
              reason: str = '', error: str = '', next_attempt: Optional[float] = None,
              attempts: Optional[int] = None, lease: float = 0.0, token: str = '',
              fence: Optional[str] = None, **extra: Any) -> None:
        """The only place `events.state` changes. Refuses moves outside
        TRANSITIONS and refuses if the row is not in `expect` (and, when
        `fence` is given, not owned by that token)."""
        if not is_allowed(expect, new):
            raise IllegalTransition(f'{expect} -> {new} is not an allowed move')
        now = self.clock()
        sets = ['state=?', 'state_at=?', 'reason=?', 'last_error=?', 'lease_until=?', 'fence_token=?']
        args: list[Any] = [new, now, reason, error[:300], lease, token]
        if next_attempt is not None:
            sets.append('next_attempt=?')
            args.append(next_attempt)
        if attempts is not None:
            sets.append('attempts=?')
            args.append(attempts)
        for col, val in extra.items():
            sets.append(f'{col}=?')
            args.append(val)
        if new in TERMINAL_STATES:
            # Terminal rows keep metadata for dedupe/audit and drop content.
            sets += ['body=NULL', 'laundered=NULL', 'terminal_at=?']
            args.append(now)
        where, wargs = 'event_id=? AND state=?', [event_id, expect]
        if fence is not None:
            where += ' AND fence_token=?'
            wargs.append(fence)
        cur = db.execute(f"UPDATE events SET {','.join(sets)} WHERE {where}", args + wargs)
        if cur.rowcount != 1:
            current = db.execute('SELECT state FROM events WHERE event_id=?', (event_id,)).fetchone()
            if current is None:
                raise IllegalTransition('unknown event')
            if fence is not None and current['state'] == expect:
                raise StaleClaim('fence token does not own this event')
            raise IllegalTransition(f"event is {current['state']}, expected {expect}")

    def _cancel(self, db: sqlite3.Connection, where: str, args: tuple, reason: str) -> int:
        """Revoke every cancellable event matching `where` (content purged)."""
        marks = ','.join('?' * len(_CANCELLABLE))
        rows = db.execute(f'SELECT event_id,state FROM events WHERE {where} AND state IN ({marks})',
                          args + _CANCELLABLE).fetchall()
        for row in rows:
            self._move(db, row['event_id'], row['state'], REVOKED, reason=reason)
        # `submitted` was already handed over, so the row stays (its turn may
        # still complete) but the raw and laundered bodies go now.
        return len(rows) + self._purge_submitted_content(db, where, args, reason)

    def _purge_submitted_content(self, db: sqlite3.Connection, where: str, args: tuple,
                                 reason: str) -> int:
        """Drop the bodies of `submitted` rows matching `where`; state and
        metadata stay. Returns the number of rows that still held content."""
        return db.execute(
            f'UPDATE events SET body=NULL,laundered=NULL,reason=?,state_at=? '
            f'WHERE {where} AND state=? AND (body IS NOT NULL OR laundered IS NOT NULL)',
            (reason, self.clock(), *args, SUBMITTED)).rowcount

    def _backoff(self, attempts: int) -> float:
        return min(self.config.max_backoff_seconds,
                   self.config.base_backoff_seconds * (2 ** max(0, attempts - 1)))

    def _fail_locked(self, db: sqlite3.Connection, row: sqlite3.Row, reason: str,
                     retryable: bool) -> str:
        """Failure of a claimed stage: retryable with backoff, or a visible hold
        once attempts run out. Returns the new state."""
        attempts = int(row['attempts']) + 1
        if retryable and attempts < self.config.max_attempts:
            self._move(db, row['event_id'], row['state'], RETRYABLE, reason=reason,
                       attempts=attempts, next_attempt=self.clock() + self._backoff(attempts))
            return RETRYABLE
        self._move(db, row['event_id'], row['state'], BLOCKED,
                   reason=reason if not retryable else 'retry_exhausted',
                   error=reason if retryable else '', attempts=attempts)
        return BLOCKED

    # --- ingest ---------------------------------------------------------------
    def insert_event(self, *, event_id: str, registration_id: str, thread_token: str,
                     message_id: str, body: str) -> InsertResult:
        """Durably take custody of one authenticated inbound event.

        Returns only after the row is committed, so a True ``ack`` is safe to
        report to the Worker. Never raises for a bad event, only for a storage
        failure (then nothing is acked)."""
        if not (event_id and registration_id and isinstance(body, str)):
            raise ValueError('event id, registration and body are required')
        thash = hash_thread_token(thread_token)
        cfg = self.config
        digest, blen = _bounded_digest(body, cfg.max_body_bytes)
        mid = (message_id or '').strip()
        dedupe_key = (hashlib.sha256(f'{registration_id}\x1f{thash}\x1f{mid}'.encode()).hexdigest()
                      if mid else f'noid:{event_id}')
        with self._tx() as db:
            now = self.clock()
            same_id = db.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone()
            if same_id is not None:
                if (same_id['dedupe_key'], same_id['body_digest']) == (dedupe_key, digest):
                    return InsertResult('duplicate', event_id, same_id['state'], '', True)
                self._reject(db, 'event_id_conflict', registration_id)
                return InsertResult('refused', event_id, same_id['state'], 'event_id_conflict', False)
            if mid:
                twins = db.execute('SELECT * FROM events WHERE dedupe_key=? ORDER BY seq',
                                   (dedupe_key,)).fetchall()
                for twin in twins:
                    if twin['body_digest'] == digest:
                        return InsertResult('duplicate', twin['event_id'], twin['state'], '', True)
            else:
                twins = []
            refusal = self._validate_target(db, registration_id, thash, now)
            if refusal:
                self._reject(db, refusal, registration_id)
                return InsertResult('refused', event_id, '', refusal, True)
            binding = db.execute('SELECT * FROM thread_bindings WHERE thread_hash=?', (thash,)).fetchone()
            reg = db.execute('SELECT generation FROM registrations WHERE registration_id=?',
                             (registration_id,)).fetchone()
            marks = ','.join('?' * len(TERMINAL_STATES | {SUBMITTED}))
            active = db.execute(
                f'SELECT COUNT(*) FROM events WHERE registration_id=? AND state NOT IN ({marks})',
                (registration_id, *sorted(TERMINAL_STATES | {SUBMITTED}))).fetchone()[0]
            if active >= cfg.max_active_events:
                self._reject(db, 'queue_full', registration_id)
                _log(f'[agent-mail] queue full for a registration ({active} active); '
                     f'event {event_id} refused, not acked', flush=True)
                return InsertResult('refused', event_id, '', 'queue_full', False)
            hold, kept, conflict_of = '', body, ''
            if twins:
                hold, conflict_of = 'conflicting_message_id', twins[0]['event_id']
            elif not mid:
                hold = 'missing_message_id'
            elif not body.strip():
                hold = 'empty_body'
            if blen > cfg.max_body_bytes:
                # The bound holds on every path, whatever else holds the event:
                # an oversize body is never stored, so it can never reach
                # laundering. Never a silent partial instruction either.
                hold, kept = 'oversize_body', None
            state = BLOCKED if hold else LOCAL_RECEIVED
            db.execute(
                'INSERT INTO events(event_id,registration_id,thread_hash,project_id,session_id,'
                'generation,message_id,dedupe_key,body_digest,body_len,state,reason,body,'
                'conflict_of,received_at,state_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (event_id, registration_id, thash, binding['project_id'], binding['session_id'],
                 reg['generation'], mid, dedupe_key, digest, blen, state, hold, kept, conflict_of,
                 now, now, now + cfg.queue_ttl_seconds))
            if hold:
                _log(f'[agent-mail] event {event_id} held: {hold}', flush=True)
            return InsertResult('held' if hold else 'accepted', event_id, state, hold, True)

    def _validate_target(self, db: sqlite3.Connection, registration_id: str, thash: str,
                         now: float) -> str:
        reg = db.execute('SELECT generation,enabled FROM registrations WHERE registration_id=?',
                         (registration_id,)).fetchone()
        if reg is None:
            return 'unknown_registration'
        binding = db.execute('SELECT * FROM thread_bindings WHERE thread_hash=?', (thash,)).fetchone()
        if binding is None or binding['registration_id'] != registration_id:
            return 'unknown_thread'
        if binding['revoked_at'] is not None:
            return 'thread_revoked'
        if binding['expires_at'] <= now:
            return 'thread_expired'
        if not reg['enabled']:
            return 'registration_disabled'
        if binding['owner_generation'] != reg['generation']:
            return 'stale_generation'
        return ''

    def _reject(self, db: sqlite3.Connection, reason: str, registration_id: str = '') -> None:
        db.execute('INSERT INTO rejections(at,reason,registration_id) VALUES(?,?,?)',
                   (self.clock(), reason, registration_id))

    def record_rejection(self, reason: str, registration_id: str = '') -> None:
        """Metadata-only record (time, reason code, registration id)."""
        if reason not in REJECTION_REASONS:
            raise ValueError('unknown rejection reason code')
        with self._tx() as db:
            self._reject(db, reason, registration_id)

    def list_rejections(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._read() as db:
            return [dict(r) for r in db.execute(
                'SELECT id,at,reason,registration_id FROM rejections ORDER BY id DESC LIMIT ?',
                (max(1, int(limit)),)).fetchall()]

    # --- reconcile: leases, revocation backstop, expiry -------------------------
    def reconcile(self, *, startup: bool = False) -> dict[str, int]:
        """Settle everything time or restart has overtaken, in one transaction.

        * `submitting` past its lease -> `uncertain`: a provider write may have
          happened, so no auto-replay. A live lease is never touched, at
          startup or otherwise: another process may own it.
        * `laundering` past its lease -> `retryable` (toolless; safe to redo).
        * events of a revoked/stale registration or revoked thread -> `revoked`;
          `submitted` ones keep their state but lose their content.
        * events past their retention deadline -> visible `expired`; `submitted`
          ones past ``completion_window_seconds`` likewise.
        """
        with self._tx() as db:
            return self._reconcile_locked(db, startup)

    def _reconcile_locked(self, db: sqlite3.Connection, startup: bool = False) -> dict[str, int]:
        now = self.clock()
        out = {'uncertain': 0, 'retryable': 0, 'revoked': 0, 'expired': 0}
        # Only an expired lease is orphaned. At startup another process sharing
        # this file may hold a live claim; `startup` only names the cause.
        for row in db.execute("SELECT * FROM events WHERE state=? AND lease_until<=?",
                              (SUBMITTING, now)).fetchall():
            self._move(db, row['event_id'], SUBMITTING, UNCERTAIN,
                       reason='restart_during_submit' if startup else 'lease_expired_during_submit',
                       attempts=int(row['attempts']) + 1)
            out['uncertain'] += 1
            _log(f"[agent-mail] event {row['event_id']} -> uncertain: submission was in "
                 f"flight; not replayed without review", flush=True)
        for row in db.execute("SELECT * FROM events WHERE state=? AND lease_until<=?",
                              (LAUNDERING, now)).fetchall():
            self._fail_locked(db, row, 'lease_expired_during_laundering', True)
            out['retryable'] += 1
        marks = ','.join('?' * len(_CANCELLABLE))
        stale = db.execute(
            'SELECT e.event_id,e.state FROM events e '
            'LEFT JOIN registrations r ON r.registration_id=e.registration_id '
            'LEFT JOIN thread_bindings b ON b.thread_hash=e.thread_hash '
            f'WHERE e.state IN ({marks}) AND (r.registration_id IS NULL OR r.enabled=0 '
            'OR r.generation!=e.generation OR b.revoked_at IS NOT NULL OR b.thread_hash IS NULL)',
            _CANCELLABLE).fetchall()
        for row in stale:
            self._move(db, row['event_id'], row['state'], REVOKED, reason='revoked_or_stale')
            out['revoked'] += 1
        # Backstop for `submitted` rows of a revoked/stale registration or thread.
        self._purge_submitted_content(
            db, 'event_id IN (SELECT e.event_id FROM events e '
            'LEFT JOIN registrations r ON r.registration_id=e.registration_id '
            'LEFT JOIN thread_bindings b ON b.thread_hash=e.thread_hash '
            'WHERE r.registration_id IS NULL OR r.enabled=0 OR r.generation!=e.generation '
            'OR b.revoked_at IS NOT NULL OR b.thread_hash IS NULL)', (), 'revoked_or_stale')
        emarks = ','.join('?' * len(_EXPIRABLE))
        for row in db.execute(f'SELECT event_id,state FROM events WHERE state IN ({emarks}) '
                              'AND expires_at<=?', (*_EXPIRABLE, now)).fetchall():
            self._move(db, row['event_id'], row['state'], EXPIRED, reason='retention_expired')
            out['expired'] += 1
            _log(f"[agent-mail] event {row['event_id']} expired from {row['state']}; "
                 f"content purged, tombstone kept", flush=True)
        for row in db.execute('SELECT event_id FROM events WHERE state=? AND state_at<=?',
                              (SUBMITTED, now - self.config.completion_window_seconds)).fetchall():
            self._move(db, row['event_id'], SUBMITTED, EXPIRED, reason='completion_window_expired')
            out['expired'] += 1
            _log(f"[agent-mail] event {row['event_id']} expired: submitted but no turn "
                 f"completion within the window; content purged, tombstone kept", flush=True)
        return out

    def purge_terminal(self) -> int:
        """The only code that deletes event rows: terminal tombstones older than
        the disclosed retention window, plus old rejection records."""
        cutoff = self.clock() - self.config.terminal_retention_seconds
        marks = ','.join('?' * len(TERMINAL_STATES))
        with self._tx() as db:
            n = db.execute(f'DELETE FROM events WHERE state IN ({marks}) AND terminal_at<=?',
                           (*sorted(TERMINAL_STATES), cutoff)).rowcount
            db.execute('DELETE FROM rejections WHERE at<=?', (cutoff,))
            return n

    # --- claims -------------------------------------------------------------------
    def _claim(self, db: sqlite3.Connection, row: Optional[sqlite3.Row], to_state: str
               ) -> Optional[dict[str, Any]]:
        if row is None:
            return None
        token = uuid.uuid4().hex
        self._move(db, row['event_id'], row['state'], to_state, lease=self.clock() + self.config.lease_seconds,
                   token=token)
        claimed = db.execute('SELECT * FROM events WHERE event_id=?', (row['event_id'],)).fetchone()
        return self._event_dict(claimed, include_content=True, include_fence=True)

    def claim_for_laundering(self) -> Optional[dict[str, Any]]:
        """Oldest due event awaiting extraction -> `laundering` under a lease."""
        with self._tx() as db:
            self._reconcile_locked(db)
            now = self.clock()
            row = db.execute(
                'SELECT * FROM events WHERE next_attempt<=? AND body IS NOT NULL AND '
                '(state=? OR (state=? AND laundered IS NULL)) ORDER BY seq LIMIT 1',
                (now, LOCAL_RECEIVED, RETRYABLE)).fetchone()
            return self._claim(db, row, LAUNDERING)

    def claim_for_submission(self) -> Optional[dict[str, Any]]:
        """Oldest due, extracted event -> `submitting` under a lease, but only
        if nothing earlier in the SAME conversation is still unsettled, so
        receipt order is preserved. A hold or an uncertain event in front keeps
        everything behind it queued until it is reviewed."""
        marks = ','.join('?' * len(_UNSETTLED))
        with self._tx() as db:
            self._reconcile_locked(db)
            now = self.clock()
            row = db.execute(
                'SELECT e.* FROM events e WHERE e.next_attempt<=? AND e.laundered IS NOT NULL AND '
                'e.state IN (?,?) AND NOT EXISTS (SELECT 1 FROM events p WHERE '
                'p.project_id=e.project_id AND p.session_id=e.session_id AND p.seq<e.seq '
                f'AND p.state IN ({marks})) ORDER BY e.seq LIMIT 1',
                (now, READY, RETRYABLE, *_UNSETTLED)).fetchone()
            return self._claim(db, row, SUBMITTING)

    def revalidate_claim(self, event_id: str, token: str) -> bool:
        """Re-check immediately before acting: still ours, lease live, and the
        registration/thread/generation still permit it. Extends the lease. A
        revoked registration cancels the event and returns False."""
        with self._tx() as db:
            now = self.clock()
            row = db.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone()
            if (row is None or row['fence_token'] != token or row['state'] not in (LAUNDERING, SUBMITTING)
                    or row['lease_until'] <= now):
                return False
            self._reconcile_locked(db)
            row = db.execute('SELECT state FROM events WHERE event_id=?', (event_id,)).fetchone()
            if row['state'] not in (LAUNDERING, SUBMITTING):
                return False
            db.execute('UPDATE events SET lease_until=? WHERE event_id=? AND fence_token=?',
                       (now + self.config.lease_seconds, event_id, token))
            return True

    def complete_laundering(self, event_id: str, token: str, envelope: dict[str, Any]) -> str:
        """Store the extracted envelope and move to `ready`. An oversize or
        malformed `reply_text` becomes a visible `blocked` hold instead."""
        if not isinstance(envelope, dict) or not isinstance(envelope.get('reply_text'), str):
            raise ValueError('envelope must be a dict with a str reply_text')
        encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        with self._tx() as db:
            if len(envelope['reply_text'].encode('utf-8')) > self.config.max_reply_bytes:
                self._move(db, event_id, LAUNDERING, BLOCKED, reason='reply_oversize', fence=token)
                return BLOCKED
            self._move(db, event_id, LAUNDERING, READY, fence=token, laundered=encoded)
            return READY

    def release_claim(self, event_id: str, token: str, *, delay_seconds: float = 0.0) -> None:
        """Submission claim given back with NOTHING handed to the provider (the
        session is busy). Back to `ready`; not counted as an attempt."""
        with self._tx() as db:
            self._move(db, event_id, SUBMITTING, READY, reason='deferred', fence=token,
                       next_attempt=self.clock() + max(0.0, delay_seconds))

    def fail(self, event_id: str, token: str, reason: str, *, retryable: bool) -> str:
        """A claimed stage failed with NO possible provider write. Retryable ->
        backoff (a visible `blocked` hold once attempts run out); otherwise a
        `blocked` hold with `reason` (e.g. 'injection_flagged')."""
        _code(reason)
        with self._tx() as db:
            row = db.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone()
            if row is None or row['state'] not in (LAUNDERING, SUBMITTING):
                raise IllegalTransition('event is not in a claimed stage')
            if row['fence_token'] != token:
                raise StaleClaim('fence token does not own this event')
            return self._fail_locked(db, row, reason, retryable)

    def mark_uncertain(self, event_id: str, token: str, reason: str) -> None:
        """A provider write may have happened and was not confirmed."""
        _code(reason)
        with self._tx() as db:
            row = db.execute('SELECT attempts FROM events WHERE event_id=?', (event_id,)).fetchone()
            attempts = int(row['attempts']) + 1 if row else None
            self._move(db, event_id, SUBMITTING, UNCERTAIN, reason=reason, fence=token,
                       attempts=attempts)

    @staticmethod
    def _check_evidence(evidence: dict[str, Any]) -> str:
        if not isinstance(evidence, dict):
            raise ValueError('acceptance evidence must be a dict')
        turn_id = evidence.get('turn_id')
        if not isinstance(turn_id, str) or not turn_id:
            raise ValueError('submitted needs positive turn acceptance evidence (turn_id)')
        if evidence.get('queued') or evidence.get('in_memory_queue'):
            raise ValueError('an enqueue-only result is not provider/turn acceptance')
        return turn_id

    def mark_submitted(self, event_id: str, token: str, evidence: dict[str, Any]) -> None:
        turn_id = self._check_evidence(evidence)
        with self._tx() as db:
            self._move(db, event_id, SUBMITTING, SUBMITTED, fence=token, turn_id=turn_id,
                       submit_evidence=json.dumps(evidence, sort_keys=True, default=str))

    def complete_turn(self, event_id: str, turn_id: str) -> None:
        """Lifecycle completion. Only the turn that accepted the event counts."""
        with self._tx() as db:
            row = db.execute('SELECT state,turn_id FROM events WHERE event_id=?', (event_id,)).fetchone()
            if row is None:
                raise IllegalTransition('unknown event')
            if row['state'] == SUBMITTED and row['turn_id'] != turn_id:
                raise ValueError('turn_id does not match the turn that accepted this event')
            self._move(db, event_id, row['state'], TURN_COMPLETED, reason='turn_completed')

    # --- review actions (a human or an explicit reconcile, never a timer) ----------
    def retry_uncertain(self, event_id: str, *, reviewed: bool = False) -> None:
        if not reviewed:
            raise ValueError('reviewed resolution required before retrying uncertain delivery')
        with self._tx() as db:
            self._move(db, event_id, UNCERTAIN, READY, reason='reviewed_retry', next_attempt=self.clock())

    def reconcile_uncertain(self, event_id: str, evidence: dict[str, Any]) -> None:
        """Existing turn/provider evidence proves the handoff happened."""
        turn_id = self._check_evidence(evidence)
        with self._tx() as db:
            self._move(db, event_id, UNCERTAIN, SUBMITTED, reason='reconciled', turn_id=turn_id,
                       submit_evidence=json.dumps(evidence, sort_keys=True, default=str))

    def release_hold(self, event_id: str) -> str:
        """Owner/operator released a `blocked` hold. Resumes at laundering if no
        envelope exists yet, otherwise at submission. Returns the new state."""
        with self._tx() as db:
            row = db.execute('SELECT state,laundered,body FROM events WHERE event_id=?',
                             (event_id,)).fetchone()
            if row is None:
                raise IllegalTransition('unknown event')
            if row['state'] != BLOCKED:
                raise IllegalTransition(f"event is {row['state']}, expected {BLOCKED}")
            if row['laundered'] is None and row['body'] is None:
                raise ValueError('nothing to release: the content was not kept (oversize); reject it')
            target = READY if row['laundered'] is not None else LOCAL_RECEIVED
            self._move(db, event_id, BLOCKED, target, reason='hold_released', next_attempt=self.clock())
            return target

    def reject_event(self, event_id: str, reason: str) -> None:
        """Review decision to drop an event for good (content purged)."""
        _code(reason)
        with self._tx() as db:
            row = db.execute('SELECT state FROM events WHERE event_id=?', (event_id,)).fetchone()
            if row is None:
                raise IllegalTransition('unknown event')
            self._move(db, event_id, row['state'], REJECTED, reason=reason)

    # --- reads -------------------------------------------------------------------------
    @staticmethod
    def _event_dict(row: sqlite3.Row, *, include_content: bool,
                    include_fence: bool = False) -> dict[str, Any]:
        out = dict(row)
        if not include_fence:
            out.pop('fence_token', None)   # a lease secret, only a claimant sees it
        if not include_content:
            out.pop('body', None)
            out.pop('laundered', None)
        elif out.get('laundered'):
            out['laundered'] = json.loads(out['laundered'])
        return out

    def get_event(self, event_id: str, *, include_content: bool = False) -> Optional[dict[str, Any]]:
        """Metadata by default. Content is opt-in: it is not ordinary logging data."""
        with self._read() as db:
            row = db.execute('SELECT * FROM events WHERE event_id=?', (event_id,)).fetchone()
            return self._event_dict(row, include_content=include_content) if row else None

    def list_events(self, *, states: Optional[tuple[str, ...]] = None,
                    registration_id: str = '', limit: int = 200) -> list[dict[str, Any]]:
        sql, args = 'SELECT * FROM events WHERE 1=1', []
        if states:
            sql += f" AND state IN ({','.join('?' * len(states))})"
            args += list(states)
        if registration_id:
            sql += ' AND registration_id=?'
            args.append(registration_id)
        with self._read() as db:
            rows = db.execute(sql + ' ORDER BY seq LIMIT ?', (*args, max(1, int(limit)))).fetchall()
            return [self._event_dict(r, include_content=False) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._read() as db:
            return {r['state']: r['n'] for r in db.execute(
                'SELECT state, COUNT(*) AS n FROM events GROUP BY state')}
