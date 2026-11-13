"""Human local denial and bounded denial-only replay (MC-1166 W02).

Unwired. Composition supplies the checked factory, explicit install and trusted
human/passcode gate, plus stop() for its ALREADY owned client. No bootstrap,
identity/seal mutation, positive lifecycle action or legacy Enrollment call.
The store admission lock commits denial before stop or remote work. Original
connection identity and receipt/outbox evidence survive for W02-P settlement.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from typing import Any, Callable

from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal
from mc.agent_mail_pull_schema import _base, _validate
from mc.agent_mail_recovery import MAX_PASSES, Recovery
from mc.agent_mail_signup import H, I, MAX_INT, _need
from mc.agent_mail_status_view import pull_status
from mc.core import _log


class Disable:
    def __init__(self, factory: Any, *, install_id: str,
                 authorize: Callable[[], bool], stop: Callable[[], bool],
                 max_passes: int = MAX_PASSES):
        self.factory, self.install_id = factory, install_id
        self.authorize, self.stop = authorize, stop
        self._recovery = Recovery(factory, clock=lambda: factory.clock(), max_passes=max_passes)

    @staticmethod
    def _result(row: sqlite3.Row) -> dict[str, Any]:
        """Validate the immutable, token-free original local denial result."""
        reason = row['reason']
        _need((row['state'], reason) in {('local_off', 'local_disabled'),
                                       ('held', 'fence_token_missing')}, 'schema_corrupt')
        expected = dict(enabled=False, revocation_pending=True, reason=reason)
        # Only this exact closed result is authored here. Requiring its original
        # compact encoding also refuses duplicate keys and extra private fields.
        _need(row['result_json'] == json.dumps(expected, separators=(',', ':')), 'schema_corrupt')
        return json.loads(row['result_json'])

    @staticmethod
    def _token(row: sqlite3.Row) -> str:
        text = row['result_json']
        _need(isinstance(text, str) and len(text.encode('utf-8')) <= 16384, 'fence_token_missing')
        try:
            def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
                value = dict(items)
                _need(len(value) == len(items), 'fence_token_missing')
                return value
            meta = json.loads(text, object_pairs_hook=pairs)
        except (ValueError, TypeError, RecursionError):
            raise EnrollmentRefused('fence_token_missing') from None
        _need(type(meta) is dict, 'fence_token_missing')
        token = meta.get('fence_token')
        _need(isinstance(token, str) and H.fullmatch(token) is not None, 'fence_token_missing')
        assert isinstance(token, str)
        return token

    def _fence(self, db: sqlite3.Connection, a: sqlite3.Row, flow: sqlite3.Row,
               mode: str, op_id: str, now: int) -> None:
        _need(flow['install_id'] == a['install_id']
              and flow['expected_generation'] == a['generation']
              and flow['expected_owner_generation'] == a['owner_generation']
              and isinstance(flow['flow_id'], str) and I.fullmatch(flow['flow_id']), 'schema_corrupt')
        token = self._token(flow)
        prior = db.execute('''SELECT * FROM mail_pull_fences
            WHERE install_id=? AND generation=? AND flow_id=?''',
            (a['install_id'], a['generation'], flow['flow_id'])).fetchall()
        if prior:
            # Remote scope has ONE immutable op/mode (contract 4). Never turn
            # an earlier cancel into a revoke or invent a second operation.
            _need(len(prior) == 1 and prior[0]['op_id'] == op_id
                  and prior[0]['mode'] == mode and prior[0]['fence_token'] == token,
                  'operation_conflict')
            return
        _need(db.execute('SELECT 1 FROM mail_pull_fences WHERE op_id=?', (op_id,)).fetchone() is None,
              'operation_conflict')
        db.execute("INSERT INTO mail_pull_fences VALUES(?,?,?,?,?,?,'pending',NULL,?,?)",
                   (op_id, a['install_id'], a['generation'], flow['flow_id'], token, mode, now, now))

    def disable(self, op_id: str) -> dict[str, Any]:
        """Commit immediate local off, then stop and try existing denial intents.

        An exact operation retry is inert locally, including after a restart.
        A reused op against a different identity/generation cannot deny it.
        Missing capability is a durable local hold, never a fabricated fence.
        """
        IdentitySeal._authorize(self.authorize)
        _need(isinstance(op_id, str) and I.fullmatch(op_id)
              and isinstance(self.install_id, str) and I.fullmatch(self.install_id), 'invalid_request')
        store = self.factory.require_pull_store()  # never creates or unlocks
        now = int(store.clock() * 1000)
        _need(0 < now < MAX_INT, 'invalid_request')
        input_digest = hashlib.sha256(json.dumps({'op_id': op_id}, separators=(',', ':')).encode('utf-8')).hexdigest()
        with store._tx() as db:
            _base(db)
            _validate(db)
            a = db.execute('SELECT * FROM mail_pull_accounts WHERE install_id=?',
                           (self.install_id,)).fetchone()
            _need(a is not None, 'registration_inactive')
            assert a is not None
            prior = db.execute('SELECT * FROM mail_pull_disable_ops WHERE op_id=?', (op_id,)).fetchone()
            if prior is not None:
                # Compare BEFORE denial or remote retry, even when the original
                # operation had no flow/token and created no remote fence.
                _need(tuple(prior[k] for k in ('install_id', 'generation', 'owner_generation',
                                              'flow_id', 'input_digest')) ==
                      (self.install_id, a['generation'], a['owner_generation'],
                       a['seal_flow_id'], input_digest), 'operation_conflict')
                self._result(prior)
            g = db.execute('SELECT * FROM mail_pull_generations WHERE install_id=? AND generation=?',
                           (self.install_id, a['generation'])).fetchone()
            _need(g is not None and all(g[k] == a[k] for k in
                  ('owner_generation', 'key_id', 'seal_flow_id')), 'schema_corrupt')
            assert g is not None
            if prior is not None:
                _need(a['local_enabled'] == 0 and g['state'] == 'fenced', 'operation_conflict')
            else:
                _need(db.execute('SELECT 1 FROM mail_pull_fences WHERE op_id=?', (op_id,)).fetchone() is None,
                      'operation_conflict')
                _need(a['consent_epoch'] < MAX_INT, 'schema_corrupt')
                flows = db.execute('''SELECT * FROM mail_pull_flows WHERE install_id=?
                    AND expected_generation=? AND expected_owner_generation=?''',
                    (self.install_id, a['generation'], a['owner_generation'])).fetchall()
                primary = next((f for f in flows if f['flow_id'] == a['seal_flow_id']), None)
                # Queue every capability before changing its flow state. A
                # savepoint keeps a missing token from retaining half a plan.
                db.execute('SAVEPOINT denial_plan')
                reason = 'local_disabled'
                try:
                    _need(primary is not None, 'fence_token_missing')
                    assert primary is not None
                    mode = 'cancel' if a['state'] == 'pending' else 'revoke'
                    self._fence(db, a, primary, mode, op_id, now)
                    for flow in flows:
                        if flow['flow_id'] != primary['flow_id'] and flow['state'] in (
                                'pending', 'verified', 'enable_pending'):
                            old = db.execute('''SELECT op_id FROM mail_pull_fences
                                WHERE install_id=? AND generation=? AND flow_id=?''',
                                (self.install_id, a['generation'], flow['flow_id'])).fetchone()
                            self._fence(db, a, flow, 'cancel', old['op_id'] if old else secrets.token_hex(16), now)
                except EnrollmentRefused as exc:
                    db.execute('ROLLBACK TO denial_plan')
                    # Conflicting operation is NOT permission to mutate another
                    # scope. Missing token still commits immediate local denial.
                    if str(exc) != 'fence_token_missing':
                        raise
                    reason = 'fence_token_missing'
                finally:
                    db.execute('RELEASE denial_plan')
                db.execute('''UPDATE mail_pull_accounts SET local_enabled=0,
                    state=CASE WHEN state IN ('frozen','deleted','held') THEN state ELSE 'disabled' END,
                    consent_epoch=consent_epoch+1,reason=?,updated_ms=? WHERE install_id=?''',
                    (reason, now, self.install_id))
                db.execute("UPDATE mail_pull_generations SET state='fenced',fenced_ms=COALESCE(fenced_ms,?) "
                           'WHERE install_id=? AND generation=?', (now, self.install_id, a['generation']))
                db.execute("UPDATE mail_pull_flows SET state='cancelled',reason='local_disabled' "
                           "WHERE install_id=? AND state IN ('pending','verified','enable_pending')", (self.install_id,))
                db.execute("UPDATE mail_pull_threads SET state='revoked' WHERE install_id=?", (self.install_id,))
                reg = db.execute('SELECT enabled FROM registrations WHERE registration_id=?',
                                 (self.install_id,)).fetchone()
                if reg is not None and reg['enabled']:
                    store.revoke_registration(self.install_id, transaction=db)
                else:
                    db.execute('UPDATE thread_bindings SET revoked_at=COALESCE(revoked_at,?) '
                               'WHERE registration_id=?', (now / 1000, self.install_id))
                    store._cancel(db, 'registration_id=?', (self.install_id,), 'registration_revoked')
                # Only unadmitted outbound work can be retired. sending/uncertain
                # and positive message ids must remain eligible for settlement.
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_outbound_intents'").fetchone():
                    db.execute("UPDATE mail_outbound_intents SET state='revoked',payload=NULL,reason='consent_required' "
                               "WHERE install=? AND state IN ('ready','retryable') AND message_id=''", (self.install_id,))
                db.execute("UPDATE mail_pull_receipts SET disposition='revoked' "
                           "WHERE install_id=? AND disposition='local_received'", (self.install_id,))
                db.execute("UPDATE mail_pull_ack_outbox SET state='held',reason='local_disabled' WHERE event_id IN "
                           '(SELECT event_id FROM mail_pull_receipts WHERE install_id=?)', (self.install_id,))
                # Keep epoch/id/reauth and ack intent for W02-P; gates deny all
                # new offers, signatures, replay and submissions after this commit.
                db.execute("UPDATE mail_pull_connections SET scan_complete=0,reason='local_disabled' WHERE install_id=?",
                           (self.install_id,))
                result = dict(enabled=False, revocation_pending=True, reason=reason)
                db.execute('''INSERT INTO mail_pull_disable_ops
                    (op_id,install_id,generation,owner_generation,flow_id,input_digest,state,reason,result_json,created_ms)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (op_id, self.install_id, a['generation'], a['owner_generation'], a['seal_flow_id'],
                     input_digest, 'held' if reason == 'fence_token_missing' else 'local_off', reason,
                     json.dumps(result, separators=(',', ':')), now))
        try:
            if self.stop() is not True:
                _log('[agent-mail] disable socket_stop_pending', flush=True)
        except Exception:
            _log('[agent-mail] disable socket_stop_failed', flush=True)
        return self._retry(store)

    def retry_revocations(self) -> dict[str, Any]:
        """Manual, bounded replay ONLY of already durable denial capabilities."""
        IdentitySeal._authorize(self.authorize)
        return self._retry(self.factory.require_pull_store())

    def _retry(self, store: Any) -> dict[str, Any]:
        # Same lock as Recovery.run: concurrent clicks on this object cannot
        # race its bounded attempt map. Durable CAS also excludes other objects.
        if self._recovery._lock.acquire(blocking=False):
            try:
                self._recovery._revocations(store)
            finally:
                self._recovery._lock.release()
        view = pull_status(self.factory)
        with store._read() as db:
            missing = db.execute("SELECT 1 FROM mail_pull_accounts WHERE reason='fence_token_missing' LIMIT 1").fetchone()
        if missing:
            view.update(state='held', reason='fence_token_missing', enabled=False, revocation_pending=True)
        return view
