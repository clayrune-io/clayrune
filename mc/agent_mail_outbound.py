"""Owner-only pull sends: durable admission before verified service I/O.

Composition supplies the checked shared store and a lazy ServiceClient. Legacy
constructor inputs remain usable for ownership reads/local cleanup only; they
never grant send authority. An admitted attempt can finish after disable.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from typing import Any, Callable, Protocol

from mc import state
from mc import agent_mail_display_name as display_name
from mc.agent_mail_sender_line import sender_line
from mc.agent_mail_enrollment import Enrollment
from mc.agent_mail_enrollment_keys import EnrollmentRefused, SealReference
# Keep the separate compatibility signer reachable to frozen-app analysis;
# pull sends below never call it or select its retired route.
from mc.agent_mail_outbound_signing import SEND_PATH as _LEGACY_SEND_PATH
from mc.agent_mail_pull_schema import _validate
from mc.agent_mail_service_client import ServiceClient, ServiceNotSent, ServiceRefused, SEND, verify_pull_response
from mc.agent_mail_service_manifest import ServiceKeyCandidate
from mc.agent_mail_ownership import OwnershipHeld, capacity_from
from mc.agent_mail_outbound_state import initialize, metadata, seal, unseal
from mc.agent_mail_questions import matching, revision
from mc.agent_mail_signing import ID
from mc.agent_mail_store import MailStore, hash_thread_token
from mc.agent_mail_target import validate
from mc.agent_mail_wake import MailWake
from mc.secrets_store import peek_passphrase_key
from mc.core import _log


# A note draft only ever moves forward in the call that created it. The note
# route's only caller (tools/night-review/send_mail.py) falls back to Gmail on
# any answer but 'accepted', so a draft left behind by a failed or crashed call
# has ALREADY been delivered by the fallback. Sending it later would duplicate
# it (incident 2026-11-15: intent 7223f4ee stranded in 'draft' after MailWake.bind
# raised, while Gmail delivered the same mail). Such drafts become 'abandoned':
# payload dropped, thread binding revoked, reason kept, never sendable.
# Questions (question_revision != '') have no fallback channel and stay resumable.
ORPHAN_DRAFT_SECONDS = 60


def _held_reason(exc: BaseException) -> str:
    # MailHeld/EnrollmentRefused carry closed, code-owned reasons; anything else
    # is summarised so no exception text reaches the row or the caller.
    from mc.agent_mail_target import MailHeld
    if isinstance(exc, (MailHeld, EnrollmentRefused)) and str(exc):
        return str(exc)[:60]
    return 'binding_failed'


def _refused(reason: str) -> EnrollmentRefused:
    _log(f'[agent-mail] outbound held: {reason}', flush=True)
    return EnrollmentRefused(reason)


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
        raise _refused('invalid_send')


def cleanup(store: Any) -> None:
    """Enable-independent local cleanup: never sends, never enables. Channel
    receipts are owner tombstones and are NOT deleted here (deletion path only)."""
    with store._tx() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_outbound_intents'").fetchone():
            return
        now = store.clock()
        db.execute("UPDATE mail_outbound_intents SET payload=NULL,state='revoked',reason='consent_required' "
                   "WHERE NOT EXISTS (SELECT 1 FROM registrations r WHERE r.registration_id=install "
                   "AND r.enabled=1 AND r.generation=mail_outbound_intents.generation)")
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_pull_accounts'").fetchone():
            db.execute("UPDATE mail_outbound_intents SET payload=NULL,state='revoked',reason='consent_required' "
                       "WHERE NOT EXISTS (SELECT 1 FROM mail_pull_accounts a JOIN mail_pull_generations g "
                       "ON g.install_id=a.install_id AND g.generation=a.generation WHERE a.install_id=install "
                       "AND a.local_enabled=1 AND a.state='active' AND g.state='current' "
                       "AND a.generation=mail_outbound_intents.generation "
                       "AND a.seal_flow_id=flow AND a.consent_epoch=epoch)")
        orphans = ("SELECT thread_hash FROM mail_outbound_intents WHERE state='draft' "
                   "AND question_revision='' AND created+?<=?")
        stale = db.execute(orphans, (ORPHAN_DRAFT_SECONDS, now)).fetchall()
        if stale:
            db.execute('UPDATE thread_bindings SET revoked_at=? WHERE revoked_at IS NULL AND thread_hash IN ('
                       + orphans + ')', (now, ORPHAN_DRAFT_SECONDS, now))
            db.execute("UPDATE mail_outbound_intents SET payload=NULL,state='abandoned',reason='draft_orphaned',"
                       "updated=? WHERE state='draft' AND question_revision='' AND created+?<=?",
                       (now, ORPHAN_DRAFT_SECONDS, now))
            _log(f'[agent-mail] outbound abandoned {len(stale)} orphaned note draft(s): draft_orphaned',
                 flush=True)
        db.execute("UPDATE mail_outbound_intents SET payload=NULL,state='expired',reason='thread_expired' "
                   "WHERE expires<=? AND state NOT IN ('revoked','abandoned')", (now,))
        db.execute("UPDATE mail_outbound_intents SET state='uncertain',reason='receipt_missing' "
                   "WHERE state='sending' AND updated+60<=?", (now,))
        db.execute("DELETE FROM mail_outbound_intents WHERE payload IS NULL AND expires+?<=?",
                   (store.config.terminal_retention_seconds, now))


class Outbound:
    def __init__(self, store: MailStore | Enrollment, *, transport: Transport | None = None,
                 client: Callable[[], ServiceClient] | None = None,
                 reviewed_service: Callable[[], tuple[ServiceKeyCandidate, str]] | None = None,
                 enabled: bool | Callable[[], bool] = False,
                 key: Callable[[], bytes | None] = peek_passphrase_key):
        self.store = store if isinstance(store, MailStore) else store.store
        self.transport, self.client, self.reviewed_service = transport, client, reviewed_service
        self.enabled, self.key = enabled, key
        if (enabled() if callable(enabled) else enabled) is True:
            initialize(self.store)

    def _enabled(self) -> None:
        if (self.enabled() if callable(self.enabled) else self.enabled) is not True:
            raise _refused('outbound_disabled')

    def _key(self) -> bytes:
        key = self.key()
        if not isinstance(key, bytes) or len(key) != 32:
            raise _refused('vault_locked')
        return key

    def question_channel(self, project_id: str, session_id: str, question: dict,
                         preferred: str) -> str:
        """Persist notification ownership, not permission to send/enable mail.

        An installed factory must remain available for reading claims even
        while enrollment is off. Never reconstruct legacy mail bindings.
        """
        if preferred not in ('legacy', 'agent_mail'):
            raise _refused('invalid_channel')
        with self.store._tx() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_outbound_question_channels'").fetchone():
                return preferred  # disabled/uninitialized factory writes nothing
            key = (project_id, session_id, revision(question))
            old = db.execute('SELECT channel FROM mail_outbound_question_channels WHERE project_id=? '
                             'AND session_id=? AND revision=?', key).fetchone()
            if old:
                return old['channel']
            # Compact durable owner tombstone: never evicted by age. At capacity a
            # NEW admission is held (visible to the caller), existing owners above
            # are unaffected. Released only by session/project deletion.
            cap = capacity_from(state.CONFIG, self.store.config.max_ownership_receipts)
            if db.execute('SELECT COUNT(*) FROM mail_outbound_question_channels').fetchone()[0] >= cap:
                raise OwnershipHeld('ownership_capacity')
            db.execute('INSERT INTO mail_outbound_question_channels VALUES(?,?,?,?,?)',
                       (*key, preferred, self.store.clock() + self.store.config.thread_ttl_seconds))
            return preferred

    def _account(self, db: Any) -> dict[str, Any]:
        """Local authority only (schema, account, consent, fence): no I/O beyond ``db``."""
        _validate(db)
        rows = db.execute('''SELECT a.*,a.install_id AS install,a.seal_flow_id AS flow,
            a.consent_epoch AS epoch,g.service_key_id,g.service_public_key,g.manifest_sequence,
            g.manifest_digest,g.state AS generation_state,g.owner_generation AS generation_owner,
            g.key_id AS generation_key,g.seal_flow_id AS generation_seal
            FROM mail_pull_accounts a JOIN mail_pull_generations g ON g.install_id=a.install_id
            AND g.generation=a.generation''').fetchall()
        if len(rows) != 1:
            raise _refused('registration_inactive')
        row = dict(rows[0])
        if (row['state'] != 'active' or row['local_enabled'] != 1 or row['generation_state'] != 'current'
                or not row['owner_address'] or not row['address'] or not row['consent_version']
                or row['epoch'] <= 0):
            raise _refused('consent_required')
        if any(row[a] != row[b] for a, b in (('generation_owner', 'owner_generation'),
                ('generation_key', 'key_id'), ('generation_seal', 'flow'))):
            raise _refused('schema_corrupt')
        if db.execute('SELECT 1 FROM mail_pull_fences WHERE install_id=? AND generation=?',
                      (row['install'], row['generation'])).fetchone():
            raise _refused('fenced')
        return row

    def _review(self) -> tuple[ServiceKeyCandidate, str]:
        """Resolve the service pin. Call ONLY with no store transaction open.

        reviewed_service() may fetch the manifest and CAS its floor on this same
        store; inside ``_tx()`` that nested write waits out the busy timeout and
        comes back as service_unavailable (MC-1213). Pre-warming is not enough:
        the cache can expire mid-transaction. Local refusals and the vault go
        first, so they answer by their own name without a fetch. Nothing here
        grants authority: ``_authority`` rechecks it all inside the caller's tx.
        """
        self._enabled()
        if self.client is None or self.reviewed_service is None:
            raise _refused('legacy_retired')
        with self.store._read() as db:
            row = self._account(db)
        reason = self.client().seal.status(
            SealReference(row['flow'], row['install'], row['generation'], row['key_id']))
        if reason != 'ready':
            raise _refused(reason)
        return self.reviewed_service()

    def _authority(self, db: Any, review: tuple[ServiceKeyCandidate, str],
                   expected: dict[str, Any] | None = None) -> dict[str, Any]:
        self._enabled()
        if self.client is None:
            raise _refused('legacy_retired')
        row = self._account(db)
        pin, digest = review
        # ``review`` predates this transaction. If another refresh advanced the
        # durable floor since, it is stale; BEGIN IMMEDIATE holds it from here on.
        # (Production reviews cannot succeed without this table.)
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_enrollment_manifests'").fetchone():
            floor = db.execute('SELECT sequence,digest FROM mail_enrollment_manifests WHERE origin=?',
                               (pin.origin,)).fetchone()
            if floor is not None and tuple(floor) != (pin.sequence, digest):
                raise _refused('service_key_changed')
        if ((row['service_origin'], row['service_key_id'], row['service_public_key'],
             row['manifest_sequence'], row['manifest_digest']) !=
                (pin.origin, pin.key_id, pin.public_key, pin.sequence, digest)
                or self.store.clock() * 1000 >= pin.expires_at_ms):
            raise _refused('service_key_changed')
        client = self.client()
        if (client.store is not self.store or client.install_id != row['install']
                or client.origin != pin.origin):
            raise _refused('composition_mismatch')
        ref = SealReference(row['flow'], row['install'], row['generation'], row['key_id'])
        reason = client.seal.status(ref)
        if reason != 'ready':
            raise _refused(reason)
        reg = db.execute('SELECT enabled,generation FROM registrations WHERE registration_id=?',
                         (row['install'],)).fetchone()
        if not reg or not reg['enabled'] or reg['generation'] != row['generation']:
            raise _refused('registration_inactive')
        if expected and any(row[k] != expected[k] for k in ('install', 'generation', 'flow', 'epoch')):
            raise _refused('generation_changed')
        if self.store.clock() * 1000 >= pin.expires_at_ms:
            raise _refused('service_key_changed')
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
            raise _refused('outbound_payload_unavailable') from None

    def prepare(self, project_id: str, session_id: str, request: dict[str, Any], *,
                question: dict[str, Any] | None = None) -> dict[str, str]:
        self._enabled()
        validate_request(request)
        if (request['kind'] == 'question') != (question is not None):
            raise _refused('question_snapshot_required')
        self.reconcile()
        # Copy callers' mutable structures before persisting or binding.
        question = json.loads(json.dumps(question))
        request_digest = hashlib.sha256(json.dumps([request, question], sort_keys=True,
                                        separators=(',', ':')).encode()).hexdigest()
        row: dict[str, Any]
        created = False
        review = self._review()
        with self.store._tx() as db:
            auth = self._authority(db, review)
            old = db.execute('SELECT * FROM mail_outbound_intents WHERE project_id=? '
                             'AND session_id=? AND request_key=?',
                             (project_id, session_id, request['request_id'])).fetchone()
            if old:
                row = dict(old)
                self._authority(db, review, row)
                if row['request_digest'] != request_digest:
                    raise _refused('intent_conflict')
            else:
                if db.execute('SELECT COUNT(*) FROM mail_outbound_intents WHERE payload IS NOT NULL').fetchone()[0] >= self.store.config.max_active_events:
                    raise _refused('outbound_full')
                intent, token = secrets.token_hex(16), secrets.token_hex(16)
                from mc.blueprints import agent_routes as ar
                project = ar.load_project(project_id)
                session = ar.agent_sessions.get(session_id)
                if not project or not session or session.get('project_id') != project_id:
                    raise _refused('session_unavailable')
                context = (sender_line(project, session) + '\n'
                           f"Project: {project.get('name') or project_id}\n"
                           f"Conversation: {str(session.get('task') or session_id)[:300]}\n\n")
                body = context + request['body'] + '\n\nReply to continue this conversation.'
                if len(body.encode()) > 16384:
                    raise _refused('body_limit')
                value = dict(op_id=intent, intent_id=intent, thread_id='t_' + intent, reply_token=token,
                             subject=request['subject'], body=body, kind=request['kind'],
                             display_name=display_name.for_session(project, session))
                raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
                now = self.store.clock()
                row = dict(intent_id=intent, request_key=request['request_id'], request_digest=request_digest,
                           project_id=project_id, session_id=session_id, install=auth['install'],
                           generation=auth['generation'], flow=auth['flow'], epoch=auth['epoch'],
                           thread_id=value['thread_id'], thread_hash=hash_thread_token(token),
                           question_revision=revision(question) if question else '',
                           digest=hashlib.sha256(raw).hexdigest(), state='draft', reason='',
                           created=now, updated=now,
                           expires=int((now + self.store.config.thread_ttl_seconds) * 1000) / 1000)
                row['payload'] = seal(row, raw, self._key(), secrets.token_bytes(12))
                db.execute('INSERT INTO mail_outbound_intents (' + ','.join(row) + ') VALUES ('
                           + ','.join('?' for _ in row) + ')', tuple(row.values()))
                created = True
        if row['state'] != 'draft' or (not created and not row['question_revision']):
            # Another call's note draft (in flight, or orphaned and already sent by
            # the caller's fallback) is never resumed here. cleanup() abandons it.
            return metadata(row)
        try:
            return self._bind(row, project_id, session_id, request['kind'], question)
        except BaseException as exc:
            if not row['question_revision']:
                self._abandon(row, _held_reason(exc))
            raise

    def _abandon(self, row: dict[str, Any], reason: str, *, attempt: str = '') -> None:
        try:
            with self.store._tx() as db:
                now = self.store.clock()
                done = db.execute("UPDATE mail_outbound_intents SET payload=NULL,state='abandoned',reason=?,"
                                  "updated=? WHERE intent_id=? AND question_revision='' "
                                  "AND (state='draft' OR (state='sending' AND attempt=?))",
                                  (reason, now, row['intent_id'], attempt)).rowcount
                if done:
                    db.execute('UPDATE thread_bindings SET revoked_at=? WHERE thread_hash=? '
                               'AND revoked_at IS NULL', (now, row['thread_hash']))
            _log(f'[agent-mail] outbound abandoned: {reason}', flush=True)
        except Exception as e:
            # Still never sent by this path; cleanup() abandons drafts after
            # ORPHAN_DRAFT_SECONDS and holds admitted attempts as uncertain.
            _log(f'[agent-mail] outbound abandon failed ({reason}): {type(e).__name__}', flush=True)

    def _bind(self, row: dict[str, Any], project_id: str, session_id: str, kind: str,
              question: dict[str, Any] | None) -> dict[str, str]:
        value, _ = self._payload(row)
        token = value['reply_token']
        self.store.bind_thread(row['install'], token, project_id, session_id,
                               kind=kind, expires_at=row['expires'])
        MailWake(self.store).bind(token, question=question)
        review = self._review()
        with self.store._tx() as db:
            auth = self._authority(db, review, row)
            snapshot = db.execute('SELECT snapshot FROM mail_continuations WHERE thread_hash=?',
                                  (row['thread_hash'],)).fetchone()[0]
            thread = (row['install'], row['generation'], auth['owner_generation'], row['thread_id'],
                      row['thread_hash'], project_id, session_id, hashlib.sha256(snapshot.encode()).hexdigest(),
                      int(row['expires'] * 1000), int(row['expires'] * 1000) + 30 * 86400000, 'active')
            old = db.execute('SELECT * FROM mail_pull_threads WHERE install_id=? AND generation=? AND thread_id=?',
                             (row['install'], row['generation'], row['thread_id'])).fetchone()
            if old is not None and tuple(old) != thread:
                raise _refused('outbound_binding_conflict')
            db.execute('INSERT OR IGNORE INTO mail_pull_threads VALUES(?,?,?,?,?,?,?,?,?,?,?)', thread)
            db.execute("UPDATE mail_outbound_intents SET state='ready',updated=? WHERE intent_id=? AND state='draft'",
                       (self.store.clock(), row['intent_id']))
        return self.status(row['intent_id'])

    def reconcile(self) -> None:
        """Local metadata/content cleanup; unit 7d owns recurring invocation."""
        self._enabled()
        self.cleanup()

    def cleanup(self) -> None:
        cleanup(self.store)

    def status(self, intent_id: str) -> dict[str, str]:
        self._enabled()
        self.cleanup()
        with self.store._tx() as db:
            old = db.execute('SELECT * FROM mail_outbound_intents WHERE intent_id=?', (intent_id,)).fetchone()
            if old is None:
                raise _refused('unknown_intent')
            row = dict(old)
            reg = db.execute('SELECT * FROM registrations WHERE registration_id=?', (row['install'],)).fetchone()
            if not reg or not reg['enabled'] or reg['generation'] != row['generation']:
                db.execute("UPDATE mail_outbound_intents SET payload=NULL,state='revoked',reason='consent_required' WHERE intent_id=?", (intent_id,))
            elif row['expires'] <= self.store.clock() and row['state'] != 'abandoned':
                db.execute("UPDATE mail_outbound_intents SET payload=NULL,state='expired',reason='thread_expired' WHERE intent_id=?", (intent_id,))
            elif row['state'] == 'sending' and row['updated'] + 60 <= self.store.clock():
                db.execute("UPDATE mail_outbound_intents SET state='uncertain',reason='receipt_missing' WHERE intent_id=?", (intent_id,))
            return metadata(db.execute('SELECT * FROM mail_outbound_intents WHERE intent_id=?', (intent_id,)).fetchone())

    def send(self, intent_id: str, *, deadline: float | None = None,
             clock: Callable[[], float] = time.monotonic) -> dict[str, str]:
        self._enabled()
        status = self.status(intent_id)
        if status['state'] not in ('ready', 'retryable'):
            return status
        from mc.blueprints import agent_routes as ar
        with self.store._read() as db:
            row = dict(db.execute('SELECT * FROM mail_outbound_intents WHERE intent_id=?', (intent_id,)).fetchone())
        value, raw = self._payload(row)
        review = self._review()  # Before the manager lock and the tx; may fetch.
        # Same manager -> store ordering as unit 6. Network never holds SQLite.
        with ar.get_manager(row['project_id']).lock:
            with self.store._tx() as db:
                current = db.execute('SELECT * FROM mail_outbound_intents WHERE intent_id=?', (intent_id,)).fetchone()
                if current['state'] not in ('ready', 'retryable'):
                    return metadata(current)
                auth = self._authority(db, review, row)
                reason = self.store._validate_target(db, row['install'], row['thread_hash'], self.store.clock())
                bound = db.execute('SELECT snapshot FROM mail_continuations WHERE thread_hash=?', (row['thread_hash'],)).fetchone()
                mapping = db.execute("SELECT * FROM mail_pull_threads WHERE install_id=? "
                                     'AND generation=? AND thread_id=?', (row['install'], row['generation'], row['thread_id'])).fetchone()
                if (reason or bound is None or mapping is None or mapping['thread_hash'] != row['thread_hash']
                        or mapping['state'] != 'active' or mapping['owner_generation'] != auth['owner_generation']
                        or mapping['project_id'] != row['project_id'] or mapping['session_id'] != row['session_id']
                        or mapping['continuation_digest'] != hashlib.sha256(bound['snapshot'].encode()).hexdigest()
                        or mapping['thread_expires_ms'] != int(row['expires'] * 1000)
                        or mapping['dedupe_until_ms'] != mapping['thread_expires_ms'] + 30 * 86400000):
                    raise _refused('outbound_binding_required')
                snapshot = json.loads(bound['snapshot'])
                if (snapshot['project_id'] != row['project_id'] or snapshot['session_id'] != row['session_id']
                        or hash_thread_token(value['reply_token']) != row['thread_hash']
                        or (revision(snapshot['question']) if snapshot['question'] else '') != row['question_revision']):
                    raise _refused('outbound_binding_conflict')
                project = ar.load_project(row['project_id'])
                if not project:
                    raise _refused('project_unavailable')
                session = ar.agent_sessions.get(row['session_id'])
                if session is None:
                    raise _refused('session_unavailable')
                validate(project, session, snapshot, busy=False, wake=False)
                if value['kind'] == 'question' and matching(session, snapshot['question']) is None:
                    raise _refused('question_changed')
                assert self.client is not None
                client = self.client()
                pin, manifest = review
                nonce, stamp = secrets.token_hex(16), str(int(self.store.clock() * 1000))
                signed = {'x-mail-version': '2', 'x-mail-direction': 'install-to-pull',
                          'x-mail-install-id': auth['install'], 'x-mail-generation': str(auth['generation']),
                          'x-mail-owner-generation': str(auth['owner_generation']), 'x-mail-key-id': auth['key_id'],
                          'x-mail-nonce': nonce, 'x-mail-timestamp': stamp, 'x-mail-digest': hashlib.sha256(raw).hexdigest(),
                          'content-type': 'application/json', 'cache-control': 'no-store'}
                ref = SealReference(auth['flow'], auth['install'], auth['generation'], auth['key_id'])
                signed['x-mail-signature'] = client.seal.sign_pull(ref, json.dumps([
                    'clayrune-agent-mail-pull-http', '1', 'install-to-pull', client.origin, 'POST', SEND,
                    auth['key_id'], auth['install'], str(auth['generation']), str(auth['owner_generation']),
                    stamp, nonce, signed['x-mail-digest']], separators=(',', ':')).encode('ascii'))
                self._authority(db, review, row)  # Floor, expiry, seal and row, after signing.
                if ((pin.key_id, pin.public_key, pin.sequence, manifest) !=
                        (auth['service_key_id'], auth['service_public_key'], auth['manifest_sequence'], auth['manifest_digest'])):
                    raise _refused('service_key_changed')
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
                    raise _refused('outbound_binding_required')
                db.execute("UPDATE mail_outbound_intents SET state='sending',reason='',attempt=?,updated=? WHERE intent_id=?",
                           (attempt, now, intent_id))
        # The admission commit precedes any possible external write. A crash or
        # thrown/lost response can never make this row automatically send again.
        # Check after lock release/commit too: either can consume the remaining
        # route budget. No transport has started, so this admitted note is safe
        # to abandon. Questions have no Gmail fallback and remain unchanged.
        if not row['question_revision'] and deadline is not None and clock() > deadline:
            self._abandon(row, 'deadline_exceeded', attempt=attempt)
            return self.status(intent_id)
        accepted_expires_ms = None
        try:
            wire = client('POST', SEND, signed, raw)
            # ServiceClient authenticates the envelope; recheck request-bound
            # receipts at this boundary too, before retaining any acceptance.
            response = verify_pull_response(wire, candidate=pin, install=row['install'],
                generation=row['generation'], owner_generation=auth['owner_generation'], path=SEND,
                nonce=nonce, request_id=value['op_id'], clock_ms=lambda: int(self.store.clock() * 1000), request=value)
            code = response.get('code')
            message_id = response.get('message_id', '')
            state, reason = 'uncertain', 'transport_uncertain'
            # Message-ID is deterministic at the Worker; never trust arbitrary
            # response text as status, headers or local routing authority.
            if wire[0] == 200 and code == response.get('state') == 'accepted':
                import re
                prefix = hashlib.sha256(f"{row['install']}:{row['generation']}:{intent_id}".encode()).hexdigest()
                if re.fullmatch(prefix + r'@[a-z0-9.-]+', message_id):
                    state, reason = 'accepted', ''
                    accepted_expires_ms = response['thread_expires_ms']
            elif wire[0] == 429 and code == 'send_limit':
                state, reason = 'retryable', 'send_limit'  # Worker reserved the intent but sent nothing; same intent_id re-authorizes on retry
            if state != 'accepted':
                message_id = ''
                _log(f'[agent-mail] outbound held: {reason}', flush=True)
        except ServiceRefused as exc:
            reason = 'response_unverified' if str(exc) == 'response_unverified' else 'transport_unavailable'
            _log(f'[agent-mail] outbound held: {reason}', flush=True)
            # A connection that never opened wrote nothing (MC-1179 MB13): the
            # same intent may go again. Anything that may have written stays uncertain.
            state, message_id = ('retryable' if isinstance(exc, ServiceNotSent) else 'uncertain'), ''
        except Exception:
            _log('[agent-mail] outbound held: transport_unavailable', flush=True)
            state, reason, message_id = 'uncertain', 'transport_unavailable', ''
        with self.store._tx() as db:
            db.execute("UPDATE mail_outbound_intents SET state=?,reason=?,message_id=?,updated=?,expires=COALESCE(?,expires) "
                       "WHERE intent_id=? AND attempt=? AND state IN ('sending','uncertain')",
                       (state, reason, message_id, self.store.clock(),
                        accepted_expires_ms / 1000 if accepted_expires_ms is not None else None, intent_id, attempt))
            if accepted_expires_ms is not None:
                # Receipt and both local deadlines commit together. Updating only
                # expiry never reactivates a revoked binding or generation.
                expires_ms = accepted_expires_ms
                db.execute('UPDATE thread_bindings SET expires_at=? WHERE thread_hash=? AND registration_id=? '
                           'AND owner_generation=?',
                           (expires_ms / 1000, row['thread_hash'], row['install'], row['generation']))
                db.execute('UPDATE mail_pull_threads SET thread_expires_ms=?,dedupe_until_ms=? '
                           'WHERE install_id=? AND generation=? AND thread_id=? AND thread_hash=?',
                           (expires_ms, expires_ms + 30 * 86400000, row['install'], row['generation'],
                            row['thread_id'], row['thread_hash']))
                # A disable may close local delivery while an admitted external
                # write completes. Retain its positive receipt without reopening.
                db.execute('UPDATE mail_outbound_intents SET message_id=? WHERE intent_id=? AND attempt=?',
                           (message_id, intent_id, attempt))
                db.execute("UPDATE mail_outbound_intents SET payload=NULL WHERE intent_id=? AND state='accepted'",
                           (intent_id,))
        return self.status(intent_id)
