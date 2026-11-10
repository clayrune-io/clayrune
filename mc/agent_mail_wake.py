"""Disabled local inbox -> existing follow-up integration (MC-1166 unit 6).

Enrollment/scheduling stays in unit 7. Nothing here sends or fetches mail.
"""
from __future__ import annotations

import json
from typing import Any

from mc.agent_mail_extract import WARNING
from mc.agent_mail_questions import consume, matching
from mc.agent_mail_screen import screen_text
from mc.agent_mail_store import (MailStore, SUBMITTED, SUBMITTING, TURN_COMPLETED,
                                 UNCERTAIN, hash_thread_token)
from mc.agent_mail_target import MailHeld, resolve, snapshot, validate
from mc.core import _log


class MailWake:
    def __init__(self, store: MailStore, *, enabled: bool = False):
        self.store = store
        self.enabled = enabled

    def bind(self, thread_token: str, *, question: dict[str, Any] | None = None) -> None:
        """Trusted pre-send seam. Never infer missing legacy identity at receipt."""
        from mc.blueprints import agent_routes as ar
        thread = self.store.get_thread(thread_token)
        if thread is None:
            raise MailHeld('thread_unavailable')
        pid, sid = thread['project_id'], thread['session_id']
        with ar.get_manager(pid).lock:
            project = ar.load_project(pid)
            session = ar.agent_sessions.get(sid)
            if not project or not session or session.get('project_id') != pid:
                raise MailHeld('session_unavailable')
            if thread['kind'] == 'question':
                if not question or matching(session, question) is None:
                    raise MailHeld('question_snapshot_required')
            elif question is not None:
                raise MailHeld('question_kind_mismatch')
            bound = snapshot(project, session, question)
            validate(project, session, bound, busy=False)
            encoded = json.dumps(bound, sort_keys=True, separators=(',', ':'))
            if len(encoded.encode()) > 32768:
                raise MailHeld('snapshot_oversize')
            with self.store._tx() as db:
                reason = self.store._validate_target(db, thread['registration_id'],
                                                      hash_thread_token(thread_token), self.store.clock())
                if reason:
                    raise MailHeld(reason)
                previous = db.execute('SELECT snapshot FROM mail_continuations WHERE thread_hash=?',
                                      (thread['thread_hash'],)).fetchone()
                if previous and previous['snapshot'] != encoded:
                    raise MailHeld('continuation_binding_conflict')
                if previous is None and (db.execute('SELECT 1 FROM events WHERE thread_hash=?',
                        (thread['thread_hash'],)).fetchone() or db.execute(
                        'SELECT 1 FROM mail_transport_threads WHERE thread_hash=?',
                        (thread['thread_hash'],)).fetchone()):
                    raise MailHeld('continuation_binding_too_late')
                db.execute('INSERT OR IGNORE INTO mail_continuations VALUES(?,?)',
                           (thread['thread_hash'], encoded))

    def settle(self, event_id: str) -> None:
        """Only durable positive pipe/argv receipts can reconcile uncertainty."""
        with self.store._read() as db:
            receipt = db.execute('SELECT * FROM mail_turn_receipts WHERE event_id=?', (event_id,)).fetchone()
        if receipt is None or not receipt['accepted']:
            return
        event = self.store.get_event(event_id)
        if event is None:
            return
        evidence = {'turn_id': receipt['attempt_id'], 'delivery_attempt_id': receipt['attempt_id'],
                    'source': 'mail_exact_followup', 'accepted': True}
        try:
            if event['state'] == UNCERTAIN:
                self.store.reconcile_uncertain(event_id, evidence)
            elif event['state'] == SUBMITTING:
                with self.store._read() as db:
                    claim = db.execute('SELECT fence_token FROM events WHERE event_id=?', (event_id,)).fetchone()
                self.store.mark_submitted(event_id, claim['fence_token'], evidence)
            current = self.store.get_event(event_id)
            if current and current['state'] == SUBMITTED and receipt['completed']:
                self.store.complete_turn(event_id, receipt['attempt_id'])
        except ValueError:
            # Revocation/expiry or another matching callback can win between
            # public CAS operations. Never recreate or replay an event here.
            current = self.store.get_event(event_id)
            if current and current['state'] not in ('revoked', 'expired', TURN_COMPLETED, SUBMITTED):
                raise

    def accepted(self, event_id: str, attempt: str, session: dict[str, Any] | None) -> None:
        with self.store._tx() as db:
            changed = db.execute('UPDATE mail_turn_receipts SET accepted=1 '
                                 'WHERE event_id=? AND attempt_id=? AND started=1', (event_id, attempt)).rowcount
            if changed != 1:
                raise MailHeld('receipt_identity_changed')
        self.settle(event_id)
        if session is not None:
            self.consume_question(event_id, attempt, session)

    def consume_question(self, event_id: str, attempt: str, session: dict[str, Any], *,
                         bound: dict[str, Any] | None = None, text: str | None = None) -> None:
        from mc.blueprints import agent_routes as ar
        with ar.get_manager(session['project_id']).lock:
            if session.get('_delivery_attempt_id') != attempt:
                return
            with self.store._tx() as db:
                row = db.execute('SELECT e.*,c.snapshot FROM events e JOIN mail_continuations c '
                                 'ON c.thread_hash=e.thread_hash WHERE event_id=?', (event_id,)).fetchone()
                if row is None or row['state'] not in (SUBMITTED, TURN_COMPLETED):
                    return
                if self.store._validate_target(db, row['registration_id'], row['thread_hash'], self.store.clock()):
                    return
                bound = bound or json.loads(row['snapshot'])
                envelope = json.loads(row['laundered'] or '{}')
                if text is None:
                    text = envelope.get('reply_text')
                if text is not None and bound is not None:
                    consume(session, bound.get('question'), text)

    def reconcile(self) -> None:
        """Reconcile stored receipts; a terminal log row alone never accepts mail."""
        self.store.reconcile()
        with self.store._read() as db:
            rows = db.execute('SELECT * FROM mail_turn_receipts WHERE accepted=1').fetchall()
        for row in rows:
            if not row['completed']:
                from mc.blueprints import agent_routes as ar
                from mc.agent_mail_target import native
                with ar.get_manager(row['project_id']).lock:
                    project = ar.load_project(row['project_id'])
                    if project is not None:
                        ar._assert_runtime_project_generation(row['project_id'], row['project_generation'])
                        logs = ar._load_agent_log(row['project_id'], strict=True)
                        done = any(e.get('session_id') == row['session_id']
                                   and e.get('delivery_attempt_id') == row['attempt_id']
                                   and e.get('status') == 'completed'
                                   and (e.get('provider') or 'claude') == row['provider']
                                   and native(e) == row['native_id'] for e in logs)
                        if done:
                            with self.store._tx() as db:
                                db.execute('UPDATE mail_turn_receipts SET completed=1 WHERE event_id=? AND attempt_id=?',
                                           (row['event_id'], row['attempt_id']))
            self.settle(row['event_id'])

    def hold(self, event: dict[str, Any], reason: str) -> None:
        """A concurrent revoke/expiry may already have settled this claim."""
        with self.store._tx() as db:
            row = db.execute('SELECT * FROM events WHERE event_id=?', (event['event_id'],)).fetchone()
            if (row is None or row['state'] != SUBMITTING
                    or row['fence_token'] != event['fence_token']):
                return
            if reason == 'session_busy':
                self.store._move(db, event['event_id'], SUBMITTING, 'ready', reason='deferred',
                                 fence=event['fence_token'], next_attempt=self.store.clock() + 1)
            else:
                self.store._fail_locked(db, row, reason, False)

    def process_next(self, app: Any) -> dict[str, str]:
        if self.enabled is not True:
            return {'state': 'disabled', 'reason': 'wake_disabled'}
        from mc import agent_mail_continuation as continuation
        from mc.blueprints import agent_routes as ar
        self.reconcile()
        event = self.store.claim_for_submission()
        if event is None:
            return {'state': 'idle', 'reason': ''}
        eid, fence = event['event_id'], event['fence_token']
        scope = None
        try:
            with self.store._read() as db:
                row = db.execute('SELECT snapshot FROM mail_continuations WHERE thread_hash=?',
                                 (event['thread_hash'],)).fetchone()
            if row is None:
                raise MailHeld('continuation_binding_missing')
            bound = json.loads(row['snapshot'])
            envelope = event['laundered']
            expected = {'version', 'source', 'warning', 'reply_text', 'ambiguous_or_mixed',
                        'quoted_content_present', 'forwarded_content_present', 'event_id'}
            if (not isinstance(envelope, dict) or envelope.keys() != expected
                    or type(envelope.get('version')) is not int or envelope.get('version') != 1
                    or envelope.get('event_id') != eid
                    or envelope.get('source') != 'owner_mail_laundered'
                    or envelope.get('warning') != WARNING or envelope.get('ambiguous_or_mixed') is not False
                    or any(type(envelope.get(key)) is not bool for key in
                           ('quoted_content_present', 'forwarded_content_present'))
                    or not isinstance(envelope.get('reply_text'), str)
                    or screen_text(envelope['reply_text'])):
                raise MailHeld('laundered_envelope_invalid')
            project = ar.load_project(event['project_id'])
            if project is None:
                raise MailHeld('project_unavailable')
            sid, source = resolve(project, bound)
            scope = continuation.Continuation(self, event, bound, project, sid, source)
            late = bound.get('question') and matching(source, bound['question']) is None
            message = ('[Owner mail reply' + ('; late question reply' if late else '')
                       + f'; event {eid}]\n' + WARNING + '\n\n' + envelope['reply_text'])
            with continuation.activate(scope), app.test_request_context(
                    f"/api/project/{project['id']}/agent/followup", method='POST',
                    json={'session_id': sid, 'message': message, '_durable_delivery_ack': True}):
                ar.agent_followup(project['id'])
            if scope.started and not scope.waited:
                from mc.agent_mail_pipe import ACK_SECONDS
                scope.done.wait(ACK_SECONDS)
            if not scope.accepted_receipt:
                if scope.started:
                    scope.unknown()
                else:
                    self.hold(event, 'followup_not_submitted')
            self.settle(eid)
        except MailHeld as exc:
            if scope is not None and scope.started:
                scope.unknown()
            else:
                self.hold(event, str(exc))
        except Exception as exc:
            _log(f'[agent-mail] follow-up event {eid}: {type(exc).__name__}', flush=True)
            if scope is not None and scope.started:
                scope.unknown()
            else:
                self.hold(event, 'followup_failed')
        result = self.store.get_event(eid)
        return {'event_id': eid, 'state': result['state'] if result else 'unavailable',
                'reason': result['reason'] if result else 'event_unavailable'}
