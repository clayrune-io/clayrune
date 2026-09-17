"""Clayrune-owned, provider-neutral conversation journal (foundation only).

The composition root supplies a state path OUTSIDE project-record DATA_DIR.
No native transcript parsing, credentials, secret-bearing environment/config,
or retention policy belongs here. Callers must supply approved conversation
content, not arbitrary diagnostic dumps. Incognito bypasses persistence entirely.
SQLite transactions serialize writers across instances/processes; events are
append-only and returned as frozen records with independently decoded payloads.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Callable
from uuid import uuid4

from mc.conversation_contract import validate_content
from mc import execution_lifecycle as lifecycle

SCHEMA_VERSION = 3
APPLICATION_ID = 1129464654
_LEGACY_TABLES = {'conversations', 'events', 'requests'}
_LIFECYCLE_TABLES = {'lifecycle_conversations', 'lifecycle_requests', 'lifecycle_attempts',
                     'lifecycle_engine_changes', 'lifecycle_event_meta',
                     'capture_sources', 'capture_spans'}


class ConversationStoreError(RuntimeError):
    pass


class SchemaError(ConversationStoreError):
    pass


class ConversationUnavailable(ConversationStoreError):
    pass


class StaleAttempt(ConversationStoreError):
    pass


class EventConflict(ConversationStoreError):
    pass


class LaunchUncertain(ConversationStoreError):
    """Creation may have happened. Inspect/reconcile; never blindly relaunch."""


@dataclass(frozen=True)
class AttemptToken:
    project_id: str
    conversation_id: str
    attempt_id: str


@dataclass(frozen=True)
class ConversationEvent:
    sequence: int
    event_id: str
    attempt_id: str
    kind: str
    timestamp: str
    payload_json: str
    protocol_version: int = 0
    disposition: str = 'legacy'

    @property
    def payload(self) -> Any:
        return json.loads(self.payload_json)


@dataclass(frozen=True)
class HistorySnapshot:
    """Captured history boundary, not an attestation of derivation completeness."""
    project_id: str
    conversation_id: str
    privacy_generation: int
    high_water: int


@dataclass(frozen=True)
class HistoryChunk:
    sequence: int
    event_id: str
    attempt_id: str
    kind: str
    timestamp: str
    protocol_version: int
    disposition: str
    offset: int
    total_bytes: int
    data: bytes

    @property
    def next_offset(self) -> int:
        return self.offset + len(self.data)

    @property
    def complete(self) -> bool:
        return self.next_offset == self.total_bytes


@dataclass(frozen=True)
class SourceCursor:
    project_id: str
    conversation_id: str
    attempt_id: str
    provider: str
    format_version: str
    source_id: str
    incarnation: str
    cursor_sequence: int
    cursor_digest: str
    privacy_generation: int
    owner_epoch: int
    sealed: bool = False
    eof_sequence: int | None = None
    eof_digest: str | None = None
    eof_exit_status: int | None = None
    eof_event_id: str | None = None


def _id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError('IDs must be nonempty strings of at most 512 characters')
    if any(ord(c) < 32 for c in value):
        raise ValueError('IDs cannot contain control characters')
    return value


def _json(value: Any) -> str:
    def validate(item: Any) -> None:
        if isinstance(item, dict):
            if not all(isinstance(k, str) for k in item):
                raise ValueError('JSON object keys must be strings')
            for child in item.values():
                validate(child)
        elif isinstance(item, list):
            for child in item:
                validate(child)
        elif item is not None and not isinstance(item, (str, int, float, bool)):
            raise ValueError('Payload must contain only JSON values')
    validate(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).absolute()

    @staticmethod
    def _schema(db: sqlite3.Connection, *, initialize: bool = False) -> None:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        app = db.execute('PRAGMA application_id').fetchone()[0]
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if version == 0 and app == 0 and not tables and initialize:
            db.execute('CREATE TABLE conversations (project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, active_attempt TEXT, deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, PRIMARY KEY(project_id, conversation_id))')
            db.execute('CREATE TABLE events (project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, sequence INTEGER NOT NULL, event_id TEXT NOT NULL, attempt_id TEXT NOT NULL, kind TEXT NOT NULL, timestamp TEXT NOT NULL, payload_json TEXT NOT NULL, PRIMARY KEY(project_id, conversation_id, sequence), UNIQUE(project_id, conversation_id, event_id), FOREIGN KEY(project_id, conversation_id) REFERENCES conversations(project_id, conversation_id))')
            db.execute('CREATE TABLE requests (project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, request_id TEXT NOT NULL, input_json TEXT NOT NULL, PRIMARY KEY(project_id, conversation_id, request_id), FOREIGN KEY(project_id, conversation_id) REFERENCES conversations(project_id, conversation_id))')
            db.execute(f'PRAGMA application_id={APPLICATION_ID}')
            ConversationStore._create_lifecycle_schema(db)
            ConversationStore._create_capture_schema(db)
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        elif app != APPLICATION_ID or not (
                (version == 1 and tables == _LEGACY_TABLES)
                or (version == 2 and tables == _LEGACY_TABLES | (_LIFECYCLE_TABLES - {'capture_sources', 'capture_spans'}))
                or (version == 3 and tables == _LEGACY_TABLES | _LIFECYCLE_TABLES)):
            raise SchemaError(f'Unsupported conversation schema: version={version}, application={app}')

    @staticmethod
    def _create_lifecycle_schema(db: sqlite3.Connection) -> None:
        db.execute('CREATE TABLE lifecycle_conversations (project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, revision INTEGER NOT NULL, requested_engine_key TEXT NOT NULL, settings_revision INTEGER NOT NULL, owner_id TEXT NOT NULL, owner_epoch INTEGER NOT NULL, privacy_generation INTEGER NOT NULL, deleted INTEGER NOT NULL, active_attempt TEXT, high_water INTEGER NOT NULL, covered_through INTEGER NOT NULL, coverage_revision INTEGER NOT NULL, coverage_complete INTEGER NOT NULL, PRIMARY KEY(project_id,conversation_id), FOREIGN KEY(project_id,conversation_id) REFERENCES conversations(project_id,conversation_id))')
        db.execute('CREATE TABLE lifecycle_requests (project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, request_id TEXT NOT NULL, content_key TEXT NOT NULL, origin TEXT NOT NULL, engine_key TEXT NOT NULL, settings_revision INTEGER NOT NULL, user_message_json TEXT NOT NULL, provenance_json TEXT NOT NULL, ordinal INTEGER NOT NULL, PRIMARY KEY(project_id,conversation_id,request_id), FOREIGN KEY(project_id,conversation_id) REFERENCES lifecycle_conversations(project_id,conversation_id))')
        db.execute('CREATE TABLE lifecycle_attempts (project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, attempt_id TEXT NOT NULL, request_id TEXT NOT NULL, owner_epoch INTEGER NOT NULL, privacy_generation INTEGER NOT NULL, engine_key TEXT NOT NULL, settings_revision INTEGER NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL, native_handle TEXT, ordinal INTEGER NOT NULL, PRIMARY KEY(project_id,conversation_id,attempt_id), FOREIGN KEY(project_id,conversation_id,request_id) REFERENCES lifecycle_requests(project_id,conversation_id,request_id))')
        db.execute('CREATE TABLE lifecycle_engine_changes (project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, settings_revision INTEGER NOT NULL, requested_engine_key TEXT NOT NULL, consent_reference TEXT NOT NULL, PRIMARY KEY(project_id,conversation_id,settings_revision), FOREIGN KEY(project_id,conversation_id) REFERENCES lifecycle_conversations(project_id,conversation_id))')
        db.execute('CREATE TABLE lifecycle_event_meta (project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, sequence INTEGER NOT NULL, protocol_version INTEGER NOT NULL, disposition TEXT NOT NULL, PRIMARY KEY(project_id,conversation_id,sequence), FOREIGN KEY(project_id,conversation_id,sequence) REFERENCES events(project_id,conversation_id,sequence))')

    @staticmethod
    def _create_capture_schema(db: sqlite3.Connection) -> None:
        db.execute('CREATE TABLE capture_sources (project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, attempt_id TEXT NOT NULL, privacy_generation INTEGER NOT NULL, owner_epoch INTEGER NOT NULL, provider TEXT NOT NULL, format_version TEXT NOT NULL, source_id TEXT NOT NULL, incarnation TEXT NOT NULL, cursor_sequence INTEGER NOT NULL DEFAULT -1, cursor_digest TEXT NOT NULL DEFAULT "", sealed INTEGER NOT NULL DEFAULT 0, eof_sequence INTEGER, eof_digest TEXT, eof_exit_status INTEGER, eof_event_id TEXT, PRIMARY KEY(project_id,conversation_id,attempt_id), UNIQUE(project_id,conversation_id,source_id,incarnation))')
        db.execute('CREATE TABLE capture_spans (project_id TEXT NOT NULL, conversation_id TEXT NOT NULL, attempt_id TEXT NOT NULL, source_sequence INTEGER NOT NULL, source_id TEXT NOT NULL, incarnation TEXT NOT NULL, frame_digest TEXT NOT NULL, frame_json TEXT NOT NULL, committed INTEGER NOT NULL DEFAULT 0, event_ids_json TEXT NOT NULL DEFAULT "[]", PRIMARY KEY(project_id,conversation_id,attempt_id,source_sequence))')

    def migrate_schema1(self, *, backup_path: Path) -> None:
        """Explicit offline migration. Caller quiesces writers; never overwrite backup.

        A SQLite-consistent backup is mandatory. Legacy records are preserved,
        not reinterpreted as lifecycle-managed executions. Unknown shapes fail.
        """
        backup_path = Path(backup_path).absolute()
        if backup_path == self.db_path or backup_path.exists():
            raise ValueError('Backup must be a new distinct path')
        with self._connection(write=True) as source:
            if source is None:
                raise SchemaError('No database to migrate')
            if source.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise SchemaError('Explicit migration requires schema 1')
            expected = {
                'conversations': ['project_id','conversation_id','active_attempt','deleted','created_at'],
                'events': ['project_id','conversation_id','sequence','event_id','attempt_id','kind','timestamp','payload_json'],
                'requests': ['project_id','conversation_id','request_id','input_json'],
            }
            for table, columns in expected.items():
                info = source.execute(f'PRAGMA table_info({table})').fetchall()
                if [r['name'] for r in info] != columns:
                    raise SchemaError('Unexpected schema 1 column shape')
                pk = [r['name'] for r in sorted(info, key=lambda r: r['pk']) if r['pk']]
                if pk != columns[:(2 if table == 'conversations' else 3)]:
                    raise SchemaError('Unexpected schema 1 primary key')
            indexes = source.execute('PRAGMA index_list(events)').fetchall()
            if not any(r['unique'] and [i['name'] for i in source.execute(f'PRAGMA index_info("{r["name"]}")')]
                       == ['project_id','conversation_id','event_id'] for r in indexes):
                raise SchemaError('Missing event identity uniqueness')
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation protects an existing backup even under a race.
            with backup_path.open('xb'):
                pass
            destination = sqlite3.connect(backup_path)
            backup_reader = sqlite3.connect(self.db_path.as_uri()+'?mode=ro',uri=True)
            try:
                # The outer BEGIN IMMEDIATE fences all writers across backup
                # and migration. Backup uses a separate read connection because
                # backing up a connection's active write transaction can hang.
                backup_reader.backup(destination)
            finally:
                backup_reader.close()
                destination.close()
            self._create_lifecycle_schema(source)
            self._create_capture_schema(source)
            source.execute(f'PRAGMA user_version={SCHEMA_VERSION}')

    @staticmethod
    def _require_lifecycle_schema(db: sqlite3.Connection) -> None:
        if db.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
            raise SchemaError('Explicit backed-up schema 1 migration required')

    def migrate_schema2(self, *, backup_path: Path) -> None:
        """Explicitly add capture tables to an existing schema-2 database.

        No normal read/write path upgrades a database implicitly. The backup is
        created before the new tables are installed and is never overwritten.
        """
        backup_path = Path(backup_path).absolute()
        if backup_path == self.db_path or backup_path.exists():
            raise ValueError('Backup must be a new distinct path')
        with self._connection(write=True) as source:
            if source is None or source.execute('PRAGMA user_version').fetchone()[0] != 2:
                raise SchemaError('Explicit migration requires schema 2')
            tables = {r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            expected = _LEGACY_TABLES | (_LIFECYCLE_TABLES - {'capture_sources', 'capture_spans'})
            if tables != expected:
                raise SchemaError('Unexpected schema 2 table shape')
            expected_columns = {
                'conversations': ['project_id','conversation_id','active_attempt','deleted','created_at'],
                'events': ['project_id','conversation_id','sequence','event_id','attempt_id','kind','timestamp','payload_json'],
                'requests': ['project_id','conversation_id','request_id','input_json'],
                'lifecycle_conversations': ['project_id','conversation_id','revision','requested_engine_key','settings_revision','owner_id','owner_epoch','privacy_generation','deleted','active_attempt','high_water','covered_through','coverage_revision','coverage_complete'],
                'lifecycle_requests': ['project_id','conversation_id','request_id','content_key','origin','engine_key','settings_revision','user_message_json','provenance_json','ordinal'],
                'lifecycle_attempts': ['project_id','conversation_id','attempt_id','request_id','owner_epoch','privacy_generation','engine_key','settings_revision','status','revision','native_handle','ordinal'],
                'lifecycle_engine_changes': ['project_id','conversation_id','settings_revision','requested_engine_key','consent_reference'],
                'lifecycle_event_meta': ['project_id','conversation_id','sequence','protocol_version','disposition'],
            }
            if any([r['name'] for r in source.execute(f'PRAGMA table_info({table})')] != columns
                   for table, columns in expected_columns.items()):
                raise SchemaError('Unexpected schema 2 column shape')
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            with backup_path.open('xb'):
                pass
            destination = sqlite3.connect(backup_path)
            backup_reader = sqlite3.connect(self.db_path.as_uri()+'?mode=ro', uri=True)
            try:
                backup_reader.backup(destination)
            finally:
                backup_reader.close()
                destination.close()
            self._create_capture_schema(source)
            source.execute(f'PRAGMA user_version={SCHEMA_VERSION}')

    @staticmethod
    def _legacy_only(db: sqlite3.Connection, project: str, conversation: str) -> None:
        if db.execute('PRAGMA user_version').fetchone()[0] >= 2:
            if db.execute('SELECT 1 FROM lifecycle_conversations WHERE project_id=? AND conversation_id=?', (project, conversation)).fetchone():
                raise lifecycle.LifecycleConflict('Legacy writes cannot bypass lifecycle management')

    @contextmanager
    def _connection(self, *, write: bool = False, create: bool = False) -> Iterator[sqlite3.Connection | None]:
        if not self.db_path.exists() and not create:
            yield None
            return
        if create:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        mode = 'rwc' if create else ('rw' if write else 'ro')
        db = sqlite3.connect(self.db_path.as_uri() + '?mode=' + mode, uri=True, timeout=30, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            self._schema(db, initialize=create)
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _conversation(db: sqlite3.Connection, project: str, conversation: str, include_deleted: bool = False) -> sqlite3.Row:
        row = db.execute('SELECT * FROM conversations WHERE project_id=? AND conversation_id=?', (project, conversation)).fetchone()
        if row is None or (row['deleted'] and not include_deleted):
            raise ConversationUnavailable('Conversation missing or deleted')
        return row

    @staticmethod
    def _event(row: sqlite3.Row) -> ConversationEvent:
        return ConversationEvent(row['sequence'], row['event_id'], row['attempt_id'], row['kind'], row['timestamp'], row['payload_json'], row['protocol_version'] if 'protocol_version' in row.keys() else 0, row['disposition'] if 'disposition' in row.keys() else 'legacy')

    def begin_attempt(self, project_id: str, conversation_id: str, *, user_message: dict, engine: dict, event_id: str, request_id: str | None = None, expected_attempt: AttemptToken | None = None, incognito: bool = False) -> AttemptToken | None:
        """Atomically save user input/requested engine and supersede the old attempt.

        Repeated event_id is idempotent only for the current identical attempt.
        A new event_id with the same request_id starts a retry WITHOUT repeating
        the user input. Request identity freezes the input and requested engine.
        Retries require the current same-request expected_attempt token, checked
        transactionally. Late retries cannot supersede a newer user request.
        This captures requested settings; it does not claim an observed model.
        """
        if not isinstance(incognito, bool):
            raise ValueError('incognito must be an explicit boolean')
        if incognito:
            return None
        project_id, conversation_id, event_id = map(_id, (project_id, conversation_id, event_id))
        request_id = _id(event_id if request_id is None else request_id)
        if not isinstance(user_message, dict) or not isinstance(engine, dict):
            raise ValueError('user_message and engine must be JSON objects')
        validate_content('user_message', user_message)
        input_json = _json({'user_message': user_message, 'engine': engine})
        with self._connection(write=True, create=True) as db:
            assert db is not None
            self._legacy_only(db, project_id, conversation_id)
            db.execute('INSERT OR IGNORE INTO conversations(project_id,conversation_id,created_at) VALUES(?,?,?)', (project_id, conversation_id, _now()))
            conv = self._conversation(db, project_id, conversation_id)
            request = db.execute('SELECT input_json FROM requests WHERE project_id=? AND conversation_id=? AND request_id=?', (project_id, conversation_id, request_id)).fetchone()
            if request and request['input_json'] != input_json:
                raise EventConflict('request_id already has different input or engine')
            previous = db.execute('SELECT * FROM events WHERE project_id=? AND conversation_id=? AND event_id=?', (project_id, conversation_id, event_id)).fetchone()
            if previous:
                if previous['kind'] != 'attempt_started' or json.loads(previous['payload_json']).get('request_id') != request_id:
                    raise EventConflict('event_id already has different content')
                if previous['attempt_id'] != conv['active_attempt']:
                    raise StaleAttempt('Attempt has been superseded')
                return AttemptToken(project_id, conversation_id, previous['attempt_id'])
            if request is not None:
                if (expected_attempt is None
                        or expected_attempt.project_id != project_id
                        or expected_attempt.conversation_id != conversation_id
                        or expected_attempt.attempt_id != conv['active_attempt']):
                    raise StaleAttempt('Retry requires the current attempt token')
                active = db.execute("SELECT payload_json FROM events WHERE project_id=? AND conversation_id=? AND attempt_id=? AND kind='attempt_started'", (project_id, conversation_id, expected_attempt.attempt_id)).fetchone()
                if active is None or json.loads(active['payload_json']).get('request_id') != request_id:
                    raise StaleAttempt('Retry cannot supersede a different request')
            content = {'request_id': request_id, 'engine': engine}
            if request is None:
                db.execute('INSERT INTO requests VALUES(?,?,?,?)', (project_id, conversation_id, request_id, input_json))
                content['user_message'] = user_message
            payload = _json(content)
            token = AttemptToken(project_id, conversation_id, uuid4().hex)
            db.execute('UPDATE conversations SET active_attempt=? WHERE project_id=? AND conversation_id=?', (token.attempt_id, project_id, conversation_id))
            self._insert(db, token, event_id, 'attempt_started', payload, _now())
            return token

    @staticmethod
    def _insert(db: sqlite3.Connection, token: AttemptToken, event_id: str, kind: str, payload: str, timestamp: str) -> ConversationEvent:
        sequence = db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE project_id=? AND conversation_id=?', (token.project_id, token.conversation_id)).fetchone()[0]
        db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?)', (token.project_id, token.conversation_id, sequence, event_id, token.attempt_id, kind, timestamp, payload))
        return ConversationEvent(sequence, event_id, token.attempt_id, kind, timestamp, payload)

    def append_event(self, token: AttemptToken, *, event_id: str, kind: str, payload: Any, timestamp: str | None = None) -> ConversationEvent:
        for value in (token.project_id, token.conversation_id, token.attempt_id, event_id, kind):
            _id(value)
        if kind == 'attempt_started':
            raise ValueError('attempt_started is reserved for begin_attempt')
        if timestamp is not None:
            _id(timestamp)
        encoded = _json(payload)
        with self._connection(write=True) as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            self._legacy_only(db, token.project_id, token.conversation_id)
            conv = self._conversation(db, token.project_id, token.conversation_id)
            if conv['active_attempt'] != token.attempt_id:
                raise StaleAttempt('Attempt has been superseded')
            previous = db.execute('SELECT * FROM events WHERE project_id=? AND conversation_id=? AND event_id=?', (token.project_id, token.conversation_id, event_id)).fetchone()
            if previous:
                if previous['attempt_id'] != token.attempt_id or previous['kind'] != kind or previous['payload_json'] != encoded or (timestamp is not None and previous['timestamp'] != timestamp):
                    raise EventConflict('event_id already has different content')
                return self._event(previous)
            validate_content(kind, payload)
            return self._insert(db, token, event_id, kind, encoded, timestamp or _now())

    def read_events(self, project_id: str, conversation_id: str, *, after: int = 0, limit: int = 100, include_deleted: bool = False) -> list[ConversationEvent]:
        _id(project_id)
        _id(conversation_id)
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Invalid sequence cursor or page limit')
        with self._connection() as db:
            if db is None:
                return []
            try:
                self._conversation(db, project_id, conversation_id, include_deleted)
            except ConversationUnavailable:
                return []
            if db.execute('PRAGMA user_version').fetchone()[0] >= 2:
                query = 'SELECT e.*,COALESCE(m.protocol_version,0) AS protocol_version,COALESCE(m.disposition,\'legacy\') AS disposition FROM events e LEFT JOIN lifecycle_event_meta m USING(project_id,conversation_id,sequence) WHERE project_id=? AND conversation_id=? AND sequence>? ORDER BY sequence LIMIT ?'
            else:
                query = 'SELECT * FROM events WHERE project_id=? AND conversation_id=? AND sequence>? ORDER BY sequence LIMIT ?'
            rows = db.execute(query, (project_id, conversation_id, after, limit)).fetchall()
            return [self._event(row) for row in rows]

    def get_conversation(self, project_id: str, conversation_id: str, *, include_deleted: bool = False) -> dict | None:
        _id(project_id)
        _id(conversation_id)
        with self._connection() as db:
            if db is None:
                return None
            try:
                return dict(self._conversation(db, project_id, conversation_id, include_deleted))
            except ConversationUnavailable:
                return None

    def _set_deleted(self, project_id: str, conversation_id: str, deleted: bool) -> None:
        _id(project_id)
        _id(conversation_id)
        with self._connection(write=True) as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            self._legacy_only(db, project_id, conversation_id)
            row = self._conversation(db, project_id, conversation_id, include_deleted=True)
            if bool(row['deleted']) != deleted:
                # Restoring visibility must never reactivate a pre-deletion worker.
                db.execute('UPDATE conversations SET deleted=?,active_attempt=NULL WHERE project_id=? AND conversation_id=?', (int(deleted), project_id, conversation_id))

    def delete_conversation(self, project_id: str, conversation_id: str) -> None:
        """Tombstone, not erasure. Physical retention/deletion belongs to policy."""
        self._set_deleted(project_id, conversation_id, True)

    def restore_conversation(self, project_id: str, conversation_id: str) -> None:
        self._set_deleted(project_id, conversation_id, False)

    @staticmethod
    def _load_lifecycle(db: sqlite3.Connection, project: str, conversation: str) -> lifecycle.ConversationState:
        ConversationStore._require_lifecycle_schema(db)
        row = db.execute('SELECT * FROM lifecycle_conversations WHERE project_id=? AND conversation_id=?', (project, conversation)).fetchone()
        if row is None:
            raise ConversationUnavailable('No lifecycle-managed conversation')
        values = dict(row)
        values['deleted'] = bool(values['deleted'])
        values['coverage_complete'] = bool(values['coverage_complete'])
        scope = (project, conversation)
        values['requests'] = tuple(lifecycle.Request(r['request_id'], r['content_key'], r['origin'], r['engine_key'], r['settings_revision']) for r in db.execute('SELECT * FROM lifecycle_requests WHERE project_id=? AND conversation_id=? ORDER BY ordinal', scope))
        values['attempts'] = tuple(lifecycle.Attempt(r['attempt_id'], r['request_id'], r['owner_epoch'], r['privacy_generation'], r['engine_key'], r['settings_revision'], lifecycle.AttemptStatus(r['status']), r['revision'], r['native_handle']) for r in db.execute('SELECT * FROM lifecycle_attempts WHERE project_id=? AND conversation_id=? ORDER BY ordinal', scope))
        values['engine_changes'] = tuple(lifecycle.EngineChange(r['requested_engine_key'], r['settings_revision'], r['consent_reference']) for r in db.execute('SELECT * FROM lifecycle_engine_changes WHERE project_id=? AND conversation_id=? ORDER BY settings_revision', scope))
        return lifecycle.ConversationState(**values)

    @staticmethod
    def _save_lifecycle(db: sqlite3.Connection, before: lifecycle.ConversationState | None,
                        state: lifecycle.ConversationState, request_content: dict | None = None) -> None:
        """Persist only changed relational rows, never rewrite history blobs.

        The pure reducer currently loads tuples of metadata for one conversation;
        that read cost is not claimed to be bounded for arbitrarily long runs.
        Full event text is never loaded during control transitions.
        """
        scalar = {f.name: getattr(state,f.name) for f in fields(state) if f.name not in ('requests','attempts','engine_changes')}
        columns = ','.join(scalar)
        updates = ','.join(f'{k}=excluded.{k}' for k in scalar if k not in ('project_id','conversation_id'))
        db.execute(f'INSERT INTO lifecycle_conversations ({columns}) VALUES ({",".join("?" for _ in scalar)}) ON CONFLICT(project_id,conversation_id) DO UPDATE SET {updates}', tuple(scalar.values()))
        old_requests = {r.request_id: r for r in before.requests} if before else {}
        for index, request in enumerate(state.requests):
            if request.request_id in old_requests:
                continue
            if request_content is None:
                raise ValueError('Original request content required')
            db.execute('INSERT INTO lifecycle_requests VALUES(?,?,?,?,?,?,?,?,?,?)', (state.project_id,state.conversation_id,request.request_id,request.content_key,request.origin,request.engine_key,request.settings_revision,_json(request_content['user_message']),_json(request_content['provenance']),index))
        old_attempts = {a.attempt_id: a for a in before.attempts} if before else {}
        for index, attempt in enumerate(state.attempts):
            if old_attempts.get(attempt.attempt_id) == attempt:
                continue
            db.execute('INSERT INTO lifecycle_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project_id,conversation_id,attempt_id) DO UPDATE SET status=excluded.status,revision=excluded.revision,native_handle=excluded.native_handle', (state.project_id,state.conversation_id,attempt.attempt_id,attempt.request_id,attempt.owner_epoch,attempt.privacy_generation,attempt.engine_key,attempt.settings_revision,attempt.status.value,attempt.revision,attempt.native_handle,index))
        old_changes = {c.settings_revision for c in before.engine_changes} if before else set()
        for change in state.engine_changes:
            if change.settings_revision not in old_changes:
                db.execute('INSERT INTO lifecycle_engine_changes VALUES(?,?,?,?,?)', (state.project_id,state.conversation_id,change.settings_revision,change.requested_engine_key,change.consent_reference))
        db.execute('UPDATE conversations SET deleted=?,active_attempt=? WHERE project_id=? AND conversation_id=?', (state.deleted,state.active_attempt,state.project_id,state.conversation_id))

    @staticmethod
    def _lifecycle_event(db: sqlite3.Connection, state: lifecycle.ConversationState,
                         event_id: str, kind: str, payload: dict, *, attempt_id: str = '',
                         disposition: str = 'control') -> ConversationEvent:
        _id(event_id)
        event = ConversationStore._insert(db, AttemptToken(state.project_id,state.conversation_id,attempt_id), event_id, kind, _json(payload), _now())
        if event.sequence != state.high_water:
            raise lifecycle.LifecycleConflict('Lifecycle/event sequence mismatch')
        db.execute('INSERT INTO lifecycle_event_meta VALUES(?,?,?,?,?)', (state.project_id,state.conversation_id,event.sequence,1,disposition))
        return replace(event,protocol_version=1,disposition=disposition)

    def create_lifecycle_conversation(self, project_id: str, conversation_id: str, *,
                                      engine: dict, event_id: str, incognito: bool = False) -> lifecycle.ConversationState | None:
        if type(incognito) is not bool:
            raise ValueError('incognito must be boolean')
        if incognito:
            return None
        _id(project_id); _id(conversation_id); _id(event_id)
        if not isinstance(engine, dict):
            raise ValueError('engine must be an object')
        key = _json(engine)
        engine = json.loads(key)
        with self._connection(write=True, create=True) as db:
            assert db is not None
            self._require_lifecycle_schema(db)
            if db.execute('SELECT 1 FROM conversations WHERE project_id=? AND conversation_id=?', (project_id,conversation_id)).fetchone():
                raise lifecycle.LifecycleConflict('Conversation already exists; no implicit legacy conversion')
            db.execute('INSERT INTO conversations(project_id,conversation_id,created_at) VALUES(?,?,?)', (project_id,conversation_id,_now()))
            state = lifecycle.ConversationState(conversation_id,project_id,requested_engine_key=key,high_water=1)
            self._save_lifecycle(db, None, state)
            self._lifecycle_event(db,state,event_id,'lifecycle.created',{'engine':engine})
            return state

    def lifecycle_state(self, project_id: str, conversation_id: str) -> lifecycle.ConversationState:
        _id(project_id); _id(conversation_id)
        with self._connection() as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            return self._load_lifecycle(db,project_id,conversation_id)

    def _lifecycle_apply(self, project: str, conversation: str, *, event_id: str,
                         kind: str, payload: dict,
                         reduce: Callable[[lifecycle.ConversationState], Any],
                         request_content: dict | None = None) -> tuple[lifecycle.ConversationState, Any]:
        _id(project); _id(conversation); _id(event_id)
        _json(payload)
        with self._connection(write=True) as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            before = self._load_lifecycle(db,project,conversation)
            if db.execute('SELECT 1 FROM events WHERE project_id=? AND conversation_id=? AND event_id=?',(project,conversation,event_id)).fetchone():
                raise EventConflict('Control event_id already committed; inspect state before retrying')
            reduced = reduce(before)
            state, result = reduced if isinstance(reduced, tuple) else (reduced, None)
            if state == before:
                return state, result
            state = replace(state, high_water=before.high_water + 1)
            self._save_lifecycle(db,before,state,request_content)
            payload = dict(payload)
            payload.update({'conversation_revision':state.revision,'owner_epoch':state.owner_epoch,
                            'privacy_generation':state.privacy_generation,'settings_revision':state.settings_revision})
            if kind == 'lifecycle.coverage_recorded':
                payload['coverage_revision'] = state.coverage_revision
            self._lifecycle_event(db,state,event_id,kind,payload,attempt_id=payload.get('attempt_id',''))
            return state, result

    def read_request(self, project_id: str, conversation_id: str, request_id: str) -> dict | None:
        _id(project_id); _id(conversation_id); _id(request_id)
        with self._connection() as db:
            if db is None:
                return None
            self._require_lifecycle_schema(db)
            self._conversation(db,project_id,conversation_id)
            row = db.execute('SELECT * FROM lifecycle_requests WHERE project_id=? AND conversation_id=? AND request_id=?',(project_id,conversation_id,request_id)).fetchone()
            if row is None:
                return None
            return {'request_id':request_id,'user_message':json.loads(row['user_message_json']),
                    'engine':json.loads(row['engine_key']),'provenance':json.loads(row['provenance_json']),
                    'settings_revision':row['settings_revision']}

    def accept_request(self, project_id: str, conversation_id: str, *, request_id: str,
                       user_message: dict, engine: dict, provenance: dict,
                       expected_revision: int, event_id: str) -> lifecycle.ConversationState:
        _id(request_id)
        validate_content('user_message',user_message)
        if not isinstance(engine,dict) or not isinstance(provenance,dict):
            raise ValueError('engine and provenance must be JSON objects')
        payload = json.loads(_json(dict(request_id=request_id,user_message=user_message,engine=engine,provenance=provenance)))
        origin = payload['provenance'].get('origin','unknown')
        def reduce(state):
            request = lifecycle.Request(request_id,hashlib.sha256(_json(payload).encode('utf-8')).hexdigest(),origin,_json(payload['engine']),state.settings_revision)
            return lifecycle.accept_request(state,request,expected_revision=expected_revision)
        return self._lifecycle_apply(project_id,conversation_id,event_id=event_id,kind='lifecycle.request_accepted',payload=payload,reduce=reduce,request_content=payload)[0]

    def claim_owner(self, project_id: str, conversation_id: str, *, owner_id: str,
                    expected_revision: int, event_id: str) -> tuple[lifecycle.ConversationState,lifecycle.OwnerToken]:
        _id(owner_id)
        return self._lifecycle_apply(project_id,conversation_id,event_id=event_id,kind='lifecycle.owner_claimed',payload={'owner_id':owner_id},reduce=lambda s:lifecycle.claim_owner(s,owner_id,expected_revision=expected_revision))

    def claim_attempt(self, owner: lifecycle.OwnerToken, *, request_id: str, attempt_id: str,
                      expected_revision: int, event_id: str) -> tuple[lifecycle.ConversationState,lifecycle.AttemptToken]:
        _id(request_id); _id(attempt_id)
        return self._lifecycle_apply(owner.project_id,owner.conversation_id,event_id=event_id,kind='lifecycle.attempt_claimed',payload={'request_id':request_id,'attempt_id':attempt_id},reduce=lambda s:lifecycle.claim_attempt(s,owner,request_id,attempt_id,expected_revision=expected_revision))

    def transition_attempt(self, token: lifecycle.AttemptToken, status: lifecycle.AttemptStatus, *,
                           expected_attempt_revision: int, event_id: str) -> lifecycle.ConversationState:
        return self._lifecycle_apply(token.project_id,token.conversation_id,event_id=event_id,kind='lifecycle.attempt_transitioned',payload={'attempt_id':token.attempt_id,'status':status.value,'attempt_revision':expected_attempt_revision+1},reduce=lambda s:lifecycle.transition_attempt(s,token,status,expected_attempt_revision=expected_attempt_revision))[0]

    def launch_claimed(self, token: lifecycle.AttemptToken, *, expected_attempt_revision: int,
                       event_id: str, authorize: Callable[[], None],
                       spawn: Callable[[], str]) -> lifecycle.ConversationState:
        """Consume one launch opportunity and guard bounded trusted process creation.

        OFFLINE boundary: no real transport is enabled by this method. ``authorize``
        must freshly check the exact immutable request engine/profile/environment
        without side effects; ``spawn`` creates/registers an owned process and
        promptly returns its opaque process reference (NOT a native thread ID).
        Neither callback may write through this store or await reader persistence.

        A durable SPAWNING marker is committed before the creation callback.
        Retrying this entry point cannot recreate a possibly launched process,
        even if the post-spawn transaction rolled back. Crash recovery must
        explicitly reconcile that unresolved marker. The write guard prevents
        takeover/deletion between authority validation and process creation.
        Early output must be buffered by the transport until this commit returns.
        """
        _id(event_id); _id(event_id + ':prepared')
        if not callable(authorize) or not callable(spawn):
            raise ValueError('Trusted authorization and bounded spawn callbacks required')

        def prepare(state):
            attempt = next((a for a in state.attempts if a.attempt_id == token.attempt_id), None)
            if attempt is None or attempt.status != lifecycle.AttemptStatus.LAUNCH_INTENT:
                raise lifecycle.LifecycleConflict('Launch already consumed or unavailable; reconcile first')
            authorize()
            return lifecycle.transition_attempt(state, token, lifecycle.AttemptStatus.SPAWNING,
                                                expected_attempt_revision=expected_attempt_revision)

        prepared, _ = self._lifecycle_apply(token.project_id,token.conversation_id,
            event_id=event_id + ':prepared', kind='lifecycle.attempt_transitioned',
            payload={'attempt_id':token.attempt_id,'status':'spawning',
                     'attempt_revision':expected_attempt_revision + 1}, reduce=prepare)
        failure = False
        with self._connection(write=True) as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            before = self._load_lifecycle(db,token.project_id,token.conversation_id)
            if db.execute('SELECT 1 FROM events WHERE project_id=? AND conversation_id=? AND event_id=?',
                          (token.project_id,token.conversation_id,event_id)).fetchone():
                raise EventConflict('Launch event identity already exists; no process created')
            # Compute/validate before any side effect, then hold writer ownership
            # across creation. The durable marker remains if any later step fails.
            running = lifecycle.transition_attempt(before,token,lifecycle.AttemptStatus.RUNNING,
                                                  expected_attempt_revision=expected_attempt_revision + 1)
            if before.owner_epoch != prepared.owner_epoch:
                raise lifecycle.LifecycleConflict('Owner changed before process creation')
            authorize()
            process_reference = None
            try:
                process_reference = spawn()
                _id(process_reference)
            except Exception:
                failure = True
            status = lifecycle.AttemptStatus.UNCERTAIN if failure else lifecycle.AttemptStatus.RUNNING
            state = (lifecycle.transition_attempt(before,token,status,
                       expected_attempt_revision=expected_attempt_revision + 1) if failure else running)
            state = replace(state,high_water=before.high_water + 1)
            self._save_lifecycle(db,before,state)
            payload = {'attempt_id':token.attempt_id,'status':status.value,
                       'attempt_revision':expected_attempt_revision + 2,
                       'conversation_revision':state.revision}
            if not failure:
                payload['process_reference'] = process_reference
            self._lifecycle_event(db,state,event_id,'lifecycle.attempt_transitioned',payload,
                                  attempt_id=token.attempt_id)
        if failure:
            raise LaunchUncertain('Process creation outcome is uncertain; reconcile the attempt')
        return state

    def bind_native_handle(self, token: lifecycle.AttemptToken, handle: str, *,
                           expected_attempt_revision: int, event_id: str) -> lifecycle.ConversationState:
        _id(handle)
        return self._lifecycle_apply(token.project_id,token.conversation_id,event_id=event_id,kind='lifecycle.native_bound',payload={'attempt_id':token.attempt_id,'native_handle':handle},reduce=lambda s:lifecycle.bind_native_handle(s,token,handle,expected_attempt_revision=expected_attempt_revision))[0]

    def reconcile_attempt(self, owner: lifecycle.OwnerToken, attempt_id: str, status: lifecycle.AttemptStatus, *,
                          expected_attempt_revision: int, resolution: str, event_id: str) -> lifecycle.ConversationState:
        return self._lifecycle_apply(owner.project_id,owner.conversation_id,event_id=event_id,kind='lifecycle.attempt_reconciled',payload={'attempt_id':attempt_id,'status':status.value,'resolution':resolution},reduce=lambda s:lifecycle.reconcile_attempt(s,owner,attempt_id,status,expected_attempt_revision=expected_attempt_revision,resolution=resolution))[0]

    def change_engine(self, project_id: str, conversation_id: str, *, engine: dict,
                      consent_reference: str, expected_revision: int, event_id: str) -> lifecycle.ConversationState:
        if not isinstance(engine,dict):
            raise ValueError('engine must be an object')
        engine = json.loads(_json(engine))
        return self._lifecycle_apply(project_id,conversation_id,event_id=event_id,kind='lifecycle.engine_changed',payload={'engine':engine,'consent_reference':consent_reference},reduce=lambda s:lifecycle.change_engine(s,_json(engine),consent_reference=consent_reference,expected_revision=expected_revision))[0]

    def set_lifecycle_deleted(self, project_id: str, conversation_id: str, deleted: bool, *,
                              expected_revision: int, event_id: str) -> lifecycle.ConversationState:
        return self._lifecycle_apply(project_id,conversation_id,event_id=event_id,kind='lifecycle.privacy_changed',payload={'deleted':deleted},reduce=lambda s:lifecycle.set_deleted(s,deleted,expected_revision=expected_revision))[0]

    def append_evidence(self, token: lifecycle.AttemptToken, *, event_id: str, kind: str,
                        payload: dict) -> tuple[ConversationEvent,lifecycle.EvidenceDisposition]:
        return self.append_evidence_batch(token,[(event_id,kind,payload)])[0]

    def append_evidence_batch(self, token: lifecycle.AttemptToken,
                              events: list[tuple[str,str,dict]]) -> list[tuple[ConversationEvent,lifecycle.EvidenceDisposition]]:
        """Commit all normalized evidence from a source frame, or none.

        Keep the immutable decoded batch until this method commits. Decoder
        in-memory replay suppression is not a durable source acknowledgment.
        Stable event IDs must include source identity and frame event index.
        This API does not persist or certify native-source coverage by itself.
        """
        from mc.conversation_contract import validate_protocol_event
        if not isinstance(events,list) or not events:
            raise ValueError('A nonempty evidence batch is required')
        prepared = []
        identities = set()
        for event_id,kind,payload in events:
            _id(event_id)
            if event_id in identities:
                raise EventConflict('Repeated event identity within batch')
            identities.add(event_id)
            validate_protocol_event(kind,payload,version=1)
            encoded = _json(payload)
            prepared.append((event_id,kind,json.loads(encoded),encoded))
        with self._connection(write=True) as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            return self._append_evidence_batch_db(db, token, prepared)

    def _append_evidence_batch_db(self, db: sqlite3.Connection,
                                  token: lifecycle.AttemptToken,
                                  prepared: list[tuple[str,str,dict,str]]) -> list[tuple[ConversationEvent,lifecycle.EvidenceDisposition]]:
        result = []
        state = self._load_lifecycle(db,token.project_id,token.conversation_id)
        lifecycle.evidence_disposition(state,token)
        for event_id,kind,payload,encoded in prepared:
            previous = db.execute('SELECT e.*,m.disposition,m.protocol_version FROM events e JOIN lifecycle_event_meta m USING(project_id,conversation_id,sequence) WHERE project_id=? AND conversation_id=? AND event_id=?',(token.project_id,token.conversation_id,event_id)).fetchone()
            if previous:
                if previous['attempt_id'] != token.attempt_id or previous['kind'] != kind or previous['payload_json'] != encoded:
                    raise EventConflict('Evidence identity has different content')
                result.append((self._event(previous),lifecycle.EvidenceDisposition(previous['disposition'])))
                continue
            before = state
            state,disposition = lifecycle.record_evidence(before,token,sequence=before.high_water+1)
            self._save_lifecycle(db,before,state)
            event = self._lifecycle_event(db,state,event_id,kind,payload,attempt_id=token.attempt_id,disposition=disposition.value)
            result.append((event,disposition))
        return result

    @staticmethod
    def _source_cursor(row: sqlite3.Row) -> SourceCursor:
        return SourceCursor(row['project_id'], row['conversation_id'], row['attempt_id'],
            row['provider'], row['format_version'], row['source_id'], row['incarnation'],
            row['cursor_sequence'], row['cursor_digest'], row['privacy_generation'], row['owner_epoch'],
            bool(row['sealed']), row['eof_sequence'], row['eof_digest'], row['eof_exit_status'], row['eof_event_id'])

    def bind_capture_source(self, token: lifecycle.AttemptToken, *, provider: str,
                            format_version: str, source_id: str, incarnation: str) -> SourceCursor:
        """Bind one immutable native source incarnation to an attempt."""
        for value in (provider, format_version, source_id, incarnation):
            _id(value)
        with self._connection(write=True) as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            state = self._load_lifecycle(db, token.project_id, token.conversation_id)
            if lifecycle.evidence_disposition(state, token) != lifecycle.EvidenceDisposition.AUTHORITATIVE:
                raise StaleAttempt('Only the current owner may bind a source cursor')
            row = db.execute('SELECT * FROM capture_sources WHERE project_id=? AND conversation_id=? AND attempt_id=?', (token.project_id, token.conversation_id, token.attempt_id)).fetchone()
            values = (token.project_id, token.conversation_id, token.attempt_id, token.privacy_generation,
                      token.owner_epoch, provider, format_version, source_id, incarnation)
            if row is not None:
                if tuple(row[k] for k in ('project_id','conversation_id','attempt_id','privacy_generation','owner_epoch','provider','format_version','source_id','incarnation')) != values:
                    raise EventConflict('Capture source binding conflicts with existing incarnation')
                return self._source_cursor(row)
            conflict = db.execute('SELECT 1 FROM capture_sources WHERE project_id=? AND conversation_id=? AND source_id=? AND incarnation=?', (token.project_id, token.conversation_id, source_id, incarnation)).fetchone()
            if conflict:
                raise EventConflict('Capture source incarnation already belongs to another attempt')
            db.execute('INSERT INTO capture_sources(project_id,conversation_id,attempt_id,privacy_generation,owner_epoch,provider,format_version,source_id,incarnation) VALUES(?,?,?,?,?,?,?,?,?)', values)
            return self._source_cursor(db.execute('SELECT * FROM capture_sources WHERE project_id=? AND conversation_id=? AND attempt_id=?', (token.project_id, token.conversation_id, token.attempt_id)).fetchone())

    def read_capture_cursor(self, token: lifecycle.AttemptToken) -> SourceCursor:
        with self._connection() as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            state = self._load_lifecycle(db, token.project_id, token.conversation_id)
            if lifecycle.evidence_disposition(state, token) != lifecycle.EvidenceDisposition.AUTHORITATIVE:
                raise StaleAttempt('Only the current owner may read a source cursor')
            row = db.execute('SELECT * FROM capture_sources WHERE project_id=? AND conversation_id=? AND attempt_id=?', (token.project_id, token.conversation_id, token.attempt_id)).fetchone()
            if row is None:
                raise ConversationUnavailable('Capture source is not bound')
            return self._source_cursor(row)

    def stage_capture_span(self, token: lifecycle.AttemptToken, *, source_sequence: int,
                           frame_json: str, frame_digest: str | None = None) -> None:
        if type(source_sequence) is not int or source_sequence < 0 or not isinstance(frame_json, str):
            raise ValueError('Invalid source span')
        actual_digest = hashlib.sha256(frame_json.encode('utf-8')).hexdigest()
        if frame_digest is not None and frame_digest != actual_digest:
            raise EventConflict('Source span digest does not match frame bytes')
        with self._connection(write=True) as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            state = self._load_lifecycle(db, token.project_id, token.conversation_id)
            if lifecycle.evidence_disposition(state, token) != lifecycle.EvidenceDisposition.AUTHORITATIVE:
                raise StaleAttempt('Only the current owner may stage a source span')
            source = db.execute('SELECT * FROM capture_sources WHERE project_id=? AND conversation_id=? AND attempt_id=?', (token.project_id, token.conversation_id, token.attempt_id)).fetchone()
            if source is None:
                raise ConversationUnavailable('Capture source is not bound')
            if source['sealed']:
                raise EventConflict('Capture source is sealed; new source spans are forbidden')
            if source_sequence != source['cursor_sequence'] + 1:
                raise EventConflict('Source span is not the next uncommitted sequence')
            old = db.execute('SELECT * FROM capture_spans WHERE project_id=? AND conversation_id=? AND attempt_id=? AND source_sequence=?', (token.project_id, token.conversation_id, token.attempt_id, source_sequence)).fetchone()
            if old is not None and (old['frame_digest'] != actual_digest or old['frame_json'] != frame_json):
                raise EventConflict('Staged source span conflicts')
            if old is None:
                db.execute('INSERT INTO capture_spans VALUES(?,?,?,?,?,?,?,?,?,?)', (token.project_id, token.conversation_id, token.attempt_id, source_sequence, source['source_id'], source['incarnation'], actual_digest, frame_json, 0, '[]'))

    def read_capture_span(self, token: lifecycle.AttemptToken, *, source_sequence: int) -> tuple[str, str, bool] | None:
        with self._connection() as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            state = self._load_lifecycle(db, token.project_id, token.conversation_id)
            if lifecycle.evidence_disposition(state, token) != lifecycle.EvidenceDisposition.AUTHORITATIVE:
                raise StaleAttempt('Only the current owner may advance a source cursor')
            row = db.execute('SELECT frame_digest,frame_json,committed FROM capture_spans WHERE project_id=? AND conversation_id=? AND attempt_id=? AND source_sequence=?', (token.project_id, token.conversation_id, token.attempt_id, source_sequence)).fetchone()
            return None if row is None else (row['frame_digest'], row['frame_json'], bool(row['committed']))

    def commit_capture_span(self, token: lifecycle.AttemptToken, *, source_sequence: int,
                            frame_digest: str, events: list[tuple[str,str,dict]]) -> SourceCursor:
        if type(source_sequence) is not int or source_sequence < 0:
            raise ValueError('Invalid source span')
        _id(frame_digest)
        prepared = []
        identities = set()
        from mc.conversation_contract import validate_protocol_event
        for event_id, kind, payload in events:
            _id(event_id)
            if event_id in identities:
                raise EventConflict('Repeated event identity within source span')
            identities.add(event_id)
            validate_protocol_event(kind, payload, version=1)
            encoded = _json(payload)
            prepared.append((event_id, kind, json.loads(encoded), encoded))
        event_ids_json = _json([item[0] for item in prepared])
        with self._connection(write=True) as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            state = self._load_lifecycle(db, token.project_id, token.conversation_id)
            if lifecycle.evidence_disposition(state, token) != lifecycle.EvidenceDisposition.AUTHORITATIVE:
                raise StaleAttempt('Only the current owner may advance a source cursor')
            source = db.execute('SELECT * FROM capture_sources WHERE project_id=? AND conversation_id=? AND attempt_id=?', (token.project_id, token.conversation_id, token.attempt_id)).fetchone()
            if source is None:
                raise ConversationUnavailable('Capture source is not bound')
            if source_sequence <= source['cursor_sequence']:
                span = db.execute('SELECT * FROM capture_spans WHERE project_id=? AND conversation_id=? AND attempt_id=? AND source_sequence=?', (token.project_id, token.conversation_id, token.attempt_id, source_sequence)).fetchone()
                if span is not None and span['committed'] and span['frame_digest'] == frame_digest:
                    if span['event_ids_json'] != event_ids_json:
                        raise EventConflict('Replay normalized batch identity conflicts')
                    for event_id, kind, _payload, encoded in prepared:
                        previous = db.execute('SELECT kind,payload_json FROM events WHERE project_id=? AND conversation_id=? AND event_id=?', (token.project_id, token.conversation_id, event_id)).fetchone()
                        if previous is None or previous['kind'] != kind or previous['payload_json'] != encoded:
                            raise EventConflict('Replay normalized batch conflicts')
                    return self._source_cursor(source)
                raise EventConflict('Committed source span conflicts')
            if source['sealed']:
                raise EventConflict('Capture source is sealed; advancing commit is forbidden')
            if source_sequence != source['cursor_sequence'] + 1:
                raise EventConflict('Source cursor cannot skip a span')
            staged = db.execute('SELECT * FROM capture_spans WHERE project_id=? AND conversation_id=? AND attempt_id=? AND source_sequence=?', (token.project_id, token.conversation_id, token.attempt_id, source_sequence)).fetchone()
            if staged is None or staged['frame_digest'] != frame_digest:
                raise ConversationUnavailable('Exact source span is not staged')
            if events:
                self._append_evidence_batch_db(db, token, prepared)
            db.execute('UPDATE capture_sources SET cursor_sequence=?,cursor_digest=? WHERE project_id=? AND conversation_id=? AND attempt_id=? AND cursor_sequence=?', (source_sequence, frame_digest, token.project_id, token.conversation_id, token.attempt_id, source['cursor_sequence']))
            if db.execute('SELECT changes()').fetchone()[0] != 1:
                raise StaleAttempt('Capture source claim changed')
            db.execute('UPDATE capture_spans SET committed=1,event_ids_json=? WHERE project_id=? AND conversation_id=? AND attempt_id=? AND source_sequence=?', (event_ids_json, token.project_id, token.conversation_id, token.attempt_id, source_sequence))
            return self._source_cursor(db.execute('SELECT * FROM capture_sources WHERE project_id=? AND conversation_id=? AND attempt_id=?', (token.project_id, token.conversation_id, token.attempt_id)).fetchone())

    def seal_capture_source(self, token: lifecycle.AttemptToken, *, eof_sequence: int,
                            exit_status: int | None, event_id: str) -> SourceCursor:
        """Record source EOF as evidence; never attest complete coverage."""
        _id(event_id)
        if type(eof_sequence) is not int or eof_sequence < 0:
            raise ValueError('Invalid EOF sequence')
        if exit_status is not None and type(exit_status) is not int:
            raise ValueError('exit_status must be an integer or None')
        eof_digest = hashlib.sha256(f'eof:{eof_sequence}:{exit_status}'.encode()).hexdigest()
        payload = {'name': 'capture.transport_eof', 'value': {
            'source_sequence': eof_sequence, 'exit_status': exit_status,
            'coverage_claim': False}}
        with self._connection(write=True) as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            state = self._load_lifecycle(db, token.project_id, token.conversation_id)
            if lifecycle.evidence_disposition(state, token) != lifecycle.EvidenceDisposition.AUTHORITATIVE:
                raise StaleAttempt('Only the current owner may seal a source')
            source = db.execute('SELECT * FROM capture_sources WHERE project_id=? AND conversation_id=? AND attempt_id=?', (token.project_id, token.conversation_id, token.attempt_id)).fetchone()
            if source is None:
                raise ConversationUnavailable('Capture source is not bound')
            if source['sealed']:
                if source['eof_sequence'] != eof_sequence or source['eof_digest'] != eof_digest:
                    raise EventConflict('Conflicting source EOF sequence or digest')
                if source['eof_event_id'] != event_id:
                    raise EventConflict('Conflicting source EOF event identity')
                return self._source_cursor(source)
            if eof_sequence != source['cursor_sequence'] + 1:
                raise EventConflict('EOF must follow the committed source cursor')
            self._append_evidence_batch_db(db, token, [(event_id, 'provider_observation', payload, _json(payload))])
            db.execute('UPDATE capture_sources SET sealed=1,eof_sequence=?,eof_digest=?,eof_exit_status=?,eof_event_id=? WHERE project_id=? AND conversation_id=? AND attempt_id=? AND sealed=0', (eof_sequence, eof_digest, exit_status, event_id, token.project_id, token.conversation_id, token.attempt_id))
            if db.execute('SELECT changes()').fetchone()[0] != 1:
                raise StaleAttempt('Source seal changed')
            return self._source_cursor(db.execute('SELECT * FROM capture_sources WHERE project_id=? AND conversation_id=? AND attempt_id=?', (token.project_id, token.conversation_id, token.attempt_id)).fetchone())

    def record_coverage(self, owner: lifecycle.OwnerToken, *, high_water: int, complete: bool,
                        source_reference: str, expected_revision: int, event_id: str) -> lifecycle.ConversationState:
        _id(source_reference)
        # Contiguous sequences are guaranteed by the same transaction/event writer.
        # source_reference is an audit claim, NOT independent verification that a
        # provider emitted everything. The future adapter must establish that.
        return self._lifecycle_apply(owner.project_id,owner.conversation_id,event_id=event_id,kind='lifecycle.coverage_recorded',payload={'covered_through':high_water,'complete':complete,'source_reference':source_reference},reduce=lambda s:lifecycle.record_coverage(s,owner,high_water=high_water,complete=complete,expected_revision=expected_revision))[0]

    def snapshot(self, project_id: str, conversation_id: str, *, after: int = 0) -> lifecycle.SnapshotToken:
        return lifecycle.snapshot(self.lifecycle_state(project_id,conversation_id),after=after)

    def history_snapshot(self, project_id: str, conversation_id: str) -> HistorySnapshot:
        """Fix a full-history boundary even when captured source has known gaps."""
        _id(project_id); _id(conversation_id)
        with self._connection() as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            self._conversation(db,project_id,conversation_id)
            state = self._load_lifecycle(db,project_id,conversation_id)
            return HistorySnapshot(project_id,conversation_id,state.privacy_generation,state.high_water)

    def read_history_chunk(self, snapshot: HistorySnapshot, sequence: int, *,
                           offset: int = 0, max_bytes: int = 65536) -> HistoryChunk:
        """Read bounded UTF-8 JSON bytes without loading a giant tool result.

        Reassemble bytes before decoding JSON (a boundary may split UTF-8).
        Every call validates privacy and a fixed sequence boundary in the same
        read transaction. Metadata is separate; ``max_bytes`` bounds payload
        bytes, not total HTTP framing. No text is shortened or dropped.
        This is not a storage quota, native-source coverage or erasure policy.
        """
        if not isinstance(snapshot, HistorySnapshot):
            raise ValueError('History snapshot required')
        if (type(snapshot.high_water) is not int or snapshot.high_water < 0
                or type(sequence) is not int or not 1 <= sequence <= snapshot.high_water
                or type(offset) is not int or offset < 0
                or type(max_bytes) is not int or not 1 <= max_bytes <= 1048576):
            raise ValueError('Invalid chunk bounds')
        with self._connection() as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            self._conversation(db,snapshot.project_id,snapshot.conversation_id)
            state = self._load_lifecycle(db,snapshot.project_id,snapshot.conversation_id)
            if (type(snapshot.privacy_generation) is not int
                    or type(snapshot.high_water) is not int
                    or snapshot.privacy_generation != state.privacy_generation
                    or not 0 <= snapshot.high_water <= state.high_water):
                raise lifecycle.LifecycleConflict('History snapshot revoked')
            row = db.execute('SELECT e.sequence,e.event_id,e.attempt_id,e.kind,e.timestamp,'
                'm.protocol_version,m.disposition,length(CAST(e.payload_json AS BLOB)) AS total_bytes,'
                'substr(CAST(e.payload_json AS BLOB),?,?) AS chunk FROM events e '
                'JOIN lifecycle_event_meta m USING(project_id,conversation_id,sequence) '
                'WHERE project_id=? AND conversation_id=? AND sequence=?',
                (offset+1,max_bytes,snapshot.project_id,snapshot.conversation_id,sequence)).fetchone()
            if row is None:
                raise ConversationUnavailable('History event missing')
            if offset >= row['total_bytes']:
                raise ValueError('Chunk offset is past the event payload')
            return HistoryChunk(row['sequence'],row['event_id'],row['attempt_id'],row['kind'],
                row['timestamp'],row['protocol_version'],row['disposition'],offset,row['total_bytes'],bytes(row['chunk']))

    def read_snapshot(self, token: lifecycle.SnapshotToken, *, after: int | None = None,
                       limit: int = 100) -> list[ConversationEvent]:
        cursor = token.after if after is None else after
        if type(cursor) is not int or not token.after <= cursor <= token.high_water or type(limit) is not int or not 1 <= limit <= 10000:
            raise ValueError('Invalid snapshot page')
        with self._connection() as db:
            if db is None:
                raise ConversationUnavailable('Conversation missing')
            state = self._load_lifecycle(db,token.project_id,token.conversation_id)
            lifecycle.validate_snapshot(state,token)
            rows = db.execute('SELECT e.*,m.protocol_version,m.disposition FROM events e JOIN lifecycle_event_meta m USING(project_id,conversation_id,sequence) WHERE project_id=? AND conversation_id=? AND sequence>? AND sequence<=? ORDER BY sequence LIMIT ?', (token.project_id,token.conversation_id,cursor,token.high_water,limit))
            return [self._event(row) for row in rows]
