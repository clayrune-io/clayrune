"""Disabled, owner-only outbound notes/questions over injected transport only.

No HTTP client, enrollment authority, background loop or production factory.
An admitted attempt may finish after disable; a later attempt cannot start.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any, Callable, Protocol

from mc.agent_mail_enrollment import Enrollment, ROUTE
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_ingress import MailIngress
from mc.agent_mail_outbound_signing import SEND_PATH, headers
from mc.agent_mail_outbound_state import initialize, metadata, seal, unseal
from mc.agent_mail_questions import matching, revision
from mc.agent_mail_signing import ID
from mc.agent_mail_store import hash_thread_token
from mc.agent_mail_target import validate
from mc.agent_mail_wake import MailWake
from mc.secrets_store import peek_passphrase_key


class Transport(Protocol):
    def send(self, origin: str, path: str, headers: dict[str, str], body: bytes) -> dict[str, Any]: ...


def validate_request(request: dict[str, Any]) -> None:
    if (type(request) is not dict or set(request) != {'request_id', 'subject', 'body', 'kind'}
            or not isinstance(request['request_id'], str) or not ID.fullmatch(request['request_id'])
            or request['kind'] not in ('note', 'question')
            or not isinstance(request['subject'], str) or not request['subject'].strip()
            or len(request['subject'].encode()) > 200
            or any(ord(c) < 32 or ord(c) == 127 for c in request['subject'])
            or not isinstance(request['body'], str) or not request['body'].strip()
            or len(request['body'].encode()) > 16384 or '\0' in request['body']):
        raise EnrollmentRefused('invalid_send')


class Outbound:
    def __init__(self, enrollment: Enrollment, *, transport: Transport,
                 enabled: bool = False, key: Callable[[], bytes | None] = peek_passphrase_key):
        self.enrollment, self.store, self.transport = enrollment, enrollment.store, transport
        self.enabled, self.key = enabled, key
        if enabled:
            initialize(self.store)

    def _enabled(self) -> None:
        if self.enabled is not True:
            raise EnrollmentRefused('outbound_disabled')
        self.enrollment._enabled()

    def _key(self) -> bytes:
        key = self.key()
        if not isinstance(key, bytes) or len(key) != 32:
            raise EnrollmentRefused('vault_locked')
        return key

    def question_channel(self, project_id: str, session_id: str, question: dict,
                         preferred: str) -> str:
        """Persist notification ownership, not permission to send/enable mail.

        An installed factory must remain available for reading claims even
        while enrollment is off. Never reconstruct legacy mail bindings.
        """
        if preferred not in ('legacy', 'agent_mail'):
            raise EnrollmentRefused('invalid_channel')
        with self.store._tx() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_outbound_question_channels'").fetchone():
                return preferred  # disabled/uninitialized factory writes nothing
            key = (project_id, session_id, revision(question))
            old = db.execute('SELECT channel FROM mail_outbound_question_channels WHERE project_id=? '
                             'AND session_id=? AND revision=?', key).fetchone()
            if old:
                return old['channel']
            db.execute('INSERT INTO mail_outbound_question_channels VALUES(?,?,?,?,?)',
                       (*key, preferred, self.store.clock() + self.store.config.thread_ttl_seconds))
            return preferred

    def _authority(self, db: Any, expected: dict[str, Any] | None = None) -> dict[str, Any]:
        self._enabled()
        row = self.enrollment._row(db)
        if (not row or row['stage'] != 'active' or not row['assigned']
                or not row['confirmed'] or not row['ownership']):
            raise EnrollmentRefused('enrollment_required')
        self.enrollment._current(db, row)
        candidate = self.enrollment._fresh_candidate(row)
        if self.enrollment.seal.public(row['flow'], row['seal_identity'], row['seal_generation'], row['key_id']) != row['public_key']:
            raise EnrollmentRefused('identity_mismatch')
        reg = db.execute('SELECT enabled FROM registrations WHERE registration_id=?',
                         (row['install'],)).fetchone()
        if (not reg or not reg['enabled'] or self.store.clock() * 1000 >= row['ownership_expires_ms']):
            raise EnrollmentRefused('ownership_expired')
        if not self.enrollment.prerequisites().ready():
            raise EnrollmentRefused('prerequisites_pending')
        pin = MailIngress._pin(db, row['install'], row['generation'])
        if (pin is None or pin.public_key != candidate.public_key or pin.key_id != candidate.key_id
                or pin.route_path != ROUTE):
            raise EnrollmentRefused('pin_required')
        if expected and any(row[k] != expected[k] for k in ('install', 'generation', 'flow', 'epoch')):
            raise EnrollmentRefused('generation_changed')
        # A slow trusted prerequisite or seal read cannot extend the lease.
        if self.store.clock() * 1000 >= min(row['ownership_expires_ms'], candidate.expires_at_ms):
            raise EnrollmentRefused('ownership_expired')
        return row

    def _payload(self, row: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
        try:
            raw = unseal(row, self._key())
            value = json.loads(raw)
            if hashlib.sha256(raw).hexdigest() != row['digest']:
                raise ValueError()
            return value, raw
        except EnrollmentRefused:
            raise
        except Exception:
            raise EnrollmentRefused('outbound_payload_unavailable') from None

    def prepare(self, project_id: str, session_id: str, request: dict[str, Any], *,
                question: dict[str, Any] | None = None) -> dict[str, str]:
        self._enabled()
        validate_request(request)
        if (request['kind'] == 'question') != (question is not None):
            raise EnrollmentRefused('question_snapshot_required')
        self.reconcile()
        # Copy callers' mutable structures before persisting or binding.
        question = json.loads(json.dumps(question))
        request_digest = hashlib.sha256(json.dumps([request, question], sort_keys=True,
                                        separators=(',', ':')).encode()).hexdigest()
        row: dict[str, Any]
        with self.store._tx() as db:
            auth = self._authority(db)
            old = db.execute('SELECT * FROM mail_outbound_intents WHERE project_id=? '
                             'AND session_id=? AND request_key=?',
                             (project_id, session_id, request['request_id'])).fetchone()
            if old:
                row = dict(old)
                self._authority(db, row)
                if row['request_digest'] != request_digest:
                    raise EnrollmentRefused('intent_conflict')
            else:
                if db.execute('SELECT COUNT(*) FROM mail_outbound_intents WHERE payload IS NOT NULL').fetchone()[0] >= self.store.config.max_active_events:
                    raise EnrollmentRefused('outbound_full')
                intent, token = secrets.token_hex(16), secrets.token_hex(16)
                from mc.blueprints import agent_routes as ar
                project = ar.load_project(project_id)
                session = ar.agent_sessions.get(session_id)
                if not project or not session or session.get('project_id') != project_id:
                    raise EnrollmentRefused('session_unavailable')
                context = (f"Project: {project.get('name') or project_id}\n"
                           f"Conversation: {str(session.get('task') or session_id)[:300]}\n\n")
                body = context + request['body'] + '\n\nReply to continue this conversation. Replies cannot approve protected actions.'
                if len(body.encode()) > 16384:
                    raise EnrollmentRefused('body_limit')
                value = dict(intent_id=intent, thread_id='t_' + intent, reply_token=token,
                             subject=request['subject'], body=body, kind=request['kind'])
                raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
                now = self.store.clock()
                row = dict(intent_id=intent, request_key=request['request_id'], request_digest=request_digest,
                           project_id=project_id, session_id=session_id, install=auth['install'],
                           generation=auth['generation'], flow=auth['flow'], epoch=auth['epoch'],
                           thread_id=value['thread_id'], thread_hash=hash_thread_token(token),
                           question_revision=revision(question) if question else '',
                           digest=hashlib.sha256(raw).hexdigest(), state='draft', reason='',
                           created=now, updated=now, expires=now + self.store.config.thread_ttl_seconds)
                row['payload'] = seal(row, raw, self._key(), secrets.token_bytes(12))
                db.execute('INSERT INTO mail_outbound_intents (' + ','.join(row) + ') VALUES ('
                           + ','.join('?' for _ in row) + ')', tuple(row.values()))
        if row['state'] != 'draft':
            return metadata(row)
        value, _ = self._payload(row)
        token = value['reply_token']
        self.store.bind_thread(row['install'], token, project_id, session_id,
                               kind=request['kind'], expires_at=row['expires'])
        MailWake(self.store).bind(token, question=question)
        MailIngress(self.store).bind_transport(row['install'], row['generation'], row['thread_id'], token)
        with self.store._tx() as db:
            self._authority(db, row)
            db.execute("UPDATE mail_outbound_intents SET state='ready',updated=? WHERE intent_id=? AND state='draft'",
                       (self.store.clock(), row['intent_id']))
        return self.status(row['intent_id'])

    def reconcile(self) -> None:
        """Local metadata/content cleanup; unit 7d owns recurring invocation."""
        self._enabled()
        with self.store._tx() as db:
            now = self.store.clock()
            db.execute("UPDATE mail_outbound_intents SET payload=NULL,state='revoked',reason='consent_required' "
                       "WHERE NOT EXISTS (SELECT 1 FROM registrations r WHERE r.registration_id=install "
                       "AND r.enabled=1 AND r.generation=mail_outbound_intents.generation)")
            db.execute("UPDATE mail_outbound_intents SET payload=NULL,state='expired',reason='thread_expired' "
                       "WHERE expires<=? AND state!='revoked'", (now,))
            db.execute("UPDATE mail_outbound_intents SET state='uncertain',reason='receipt_missing' "
                       "WHERE state='sending' AND updated+60<=?", (now,))
            db.execute("DELETE FROM mail_outbound_intents WHERE payload IS NULL AND expires+?<=?",
                       (self.store.config.terminal_retention_seconds, now))
            db.execute('DELETE FROM mail_outbound_question_channels WHERE expires+?<=?',
                       (self.store.config.terminal_retention_seconds, now))

    def status(self, intent_id: str) -> dict[str, str]:
        self._enabled()
        with self.store._tx() as db:
            old = db.execute('SELECT * FROM mail_outbound_intents WHERE intent_id=?', (intent_id,)).fetchone()
            if old is None:
                raise EnrollmentRefused('unknown_intent')
            row = dict(old)
            reg = db.execute('SELECT * FROM registrations WHERE registration_id=?', (row['install'],)).fetchone()
            if not reg or not reg['enabled'] or reg['generation'] != row['generation']:
                db.execute("UPDATE mail_outbound_intents SET payload=NULL,state='revoked',reason='consent_required' WHERE intent_id=?", (intent_id,))
            elif row['expires'] <= self.store.clock():
                db.execute("UPDATE mail_outbound_intents SET payload=NULL,state='expired',reason='thread_expired' WHERE intent_id=?", (intent_id,))
            elif row['state'] == 'sending' and row['updated'] + 60 <= self.store.clock():
                db.execute("UPDATE mail_outbound_intents SET state='uncertain',reason='receipt_missing' WHERE intent_id=?", (intent_id,))
            return metadata(db.execute('SELECT * FROM mail_outbound_intents WHERE intent_id=?', (intent_id,)).fetchone())

    def send(self, intent_id: str) -> dict[str, str]:
        self._enabled()
        status = self.status(intent_id)
        if status['state'] not in ('ready', 'retryable'):
            return status
        from mc.blueprints import agent_routes as ar
        with self.store._read() as db:
            row = dict(db.execute('SELECT * FROM mail_outbound_intents WHERE intent_id=?', (intent_id,)).fetchone())
        value, raw = self._payload(row)
        # Same manager -> store ordering as unit 6. Network never holds SQLite.
        with ar.get_manager(row['project_id']).lock:
            with self.store._tx() as db:
                current = db.execute('SELECT * FROM mail_outbound_intents WHERE intent_id=?', (intent_id,)).fetchone()
                if current['state'] not in ('ready', 'retryable'):
                    return metadata(current)
                auth = self._authority(db, row)
                reason = self.store._validate_target(db, row['install'], row['thread_hash'], self.store.clock())
                bound = db.execute('SELECT snapshot FROM mail_continuations WHERE thread_hash=?', (row['thread_hash'],)).fetchone()
                mapping = db.execute('SELECT thread_hash FROM mail_transport_threads WHERE registration_id=? '
                                     'AND generation=? AND thread_id=?', (row['install'], row['generation'], row['thread_id'])).fetchone()
                if reason or bound is None or mapping is None or mapping['thread_hash'] != row['thread_hash']:
                    raise EnrollmentRefused('outbound_binding_required')
                snapshot = json.loads(bound['snapshot'])
                if (snapshot['project_id'] != row['project_id'] or snapshot['session_id'] != row['session_id']
                        or hash_thread_token(value['reply_token']) != row['thread_hash']
                        or (revision(snapshot['question']) if snapshot['question'] else '') != row['question_revision']):
                    raise EnrollmentRefused('outbound_binding_conflict')
                project = ar.load_project(row['project_id'])
                if not project:
                    raise EnrollmentRefused('project_unavailable')
                session = ar.agent_sessions.get(row['session_id'])
                if session is None:
                    raise EnrollmentRefused('session_unavailable')
                validate(project, session, snapshot, busy=False)
                if value['kind'] == 'question' and matching(session, snapshot['question']) is None:
                    raise EnrollmentRefused('question_changed')
                signed = headers(self.enrollment.seal, auth, raw, now_ms=int(self.store.clock() * 1000),
                                 nonce=secrets.token_hex(16))
                self._authority(db, row)
                origin = self.enrollment._candidate(auth).origin
                attempt = secrets.token_hex(16)
                # Signing and trusted validation can outlive a short or nearly
                # expired thread. Admission must use its CURRENT deadline and
                # target under this same transaction, before any external write.
                now = self.store.clock()
                reason = self.store._validate_target(db, row['install'], row['thread_hash'], now)
                if current['expires'] <= now or reason == 'thread_expired':
                    db.execute("UPDATE mail_outbound_intents SET state='expired',payload=NULL,"
                               "reason='thread_expired',updated=? WHERE intent_id=?", (now, intent_id))
                    return metadata(db.execute('SELECT * FROM mail_outbound_intents WHERE intent_id=?',
                                               (intent_id,)).fetchone())
                if reason:
                    raise EnrollmentRefused('outbound_binding_required')
                db.execute("UPDATE mail_outbound_intents SET state='sending',reason='',attempt=?,updated=? WHERE intent_id=?",
                           (attempt, now, intent_id))
        # The admission commit precedes any possible external write. A crash or
        # thrown/lost response can never make this row automatically send again.
        try:
            response = self.transport.send(origin, SEND_PATH, signed, raw)
            code = response.get('code')
            message_id = response.get('message_id', '')
            state, reason = 'uncertain', 'transport_uncertain'
            # Message-ID is deterministic at the Worker; never trust arbitrary
            # response text as status, headers or local routing authority.
            if code == 'accepted' and type(message_id) is str and len(message_id) <= 254:
                import re
                prefix = hashlib.sha256(f"{row['install']}:{row['generation']}:{intent_id}".encode()).hexdigest()
                if re.fullmatch(prefix + r'@[a-z0-9.-]+', message_id):
                    state, reason = 'accepted', ''
            elif code == 'send_limit':
                state, reason = 'retryable', 'send_limit'  # Worker proves no reservation/write
            if state != 'accepted':
                message_id = ''
        except Exception:
            state, reason, message_id = 'uncertain', 'transport_unavailable', ''
        with self.store._tx() as db:
            db.execute("UPDATE mail_outbound_intents SET state=?,reason=?,message_id=?,updated=? "
                       "WHERE intent_id=? AND attempt=? AND state IN ('sending','uncertain')",
                       (state, reason, message_id, self.store.clock(), intent_id, attempt))
            if state == 'accepted':
                # A disable may close local delivery while an admitted external
                # write completes. Retain its positive receipt without reopening.
                db.execute('UPDATE mail_outbound_intents SET message_id=? WHERE intent_id=? AND attempt=?',
                           (message_id, intent_id, attempt))
                db.execute("UPDATE mail_outbound_intents SET payload=NULL WHERE intent_id=? AND state='accepted'",
                           (intent_id,))
        return self.status(intent_id)
