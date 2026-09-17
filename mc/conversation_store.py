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
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from mc.conversation_contract import validate_content

SCHEMA_VERSION = 1
APPLICATION_ID = 1129464654


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

    @property
    def payload(self) -> Any:
        return json.loads(self.payload_json)


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
            db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        elif version != SCHEMA_VERSION or app != APPLICATION_ID or tables != {'conversations', 'events', 'requests'}:
            raise SchemaError(f'Unsupported conversation schema: version={version}, application={app}')

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
        return ConversationEvent(row['sequence'], row['event_id'], row['attempt_id'], row['kind'], row['timestamp'], row['payload_json'])

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
            rows = db.execute('SELECT * FROM events WHERE project_id=? AND conversation_id=? AND sequence>? ORDER BY sequence LIMIT ?', (project_id, conversation_id, after, limit)).fetchall()
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
            row = self._conversation(db, project_id, conversation_id, include_deleted=True)
            if bool(row['deleted']) != deleted:
                # Restoring visibility must never reactivate a pre-deletion worker.
                db.execute('UPDATE conversations SET deleted=?,active_attempt=NULL WHERE project_id=? AND conversation_id=?', (int(deleted), project_id, conversation_id))

    def delete_conversation(self, project_id: str, conversation_id: str) -> None:
        """Tombstone, not erasure. Physical retention/deletion belongs to policy."""
        self._set_deleted(project_id, conversation_id, True)

    def restore_conversation(self, project_id: str, conversation_id: str) -> None:
        self._set_deleted(project_id, conversation_id, False)
