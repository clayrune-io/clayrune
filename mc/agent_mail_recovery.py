"""Bounded pull recovery (MC-1166 unit 23), with no transport ownership.

Reports contain closed reason codes, counts and ages only. PullClient alone
replays frames and settles signed ack_committed; recovery never clears an ack.
Only existing cancel/revoke intents are retried through verified ServiceClient.
No consent, keys, pin, generation or flow is created or advanced here. No store
transaction spans network/signing work. Construction and import perform no I/O.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import re
import threading
import time
from typing import Any, Callable

from mc import agent_mail_deletion as deletion
from mc import agent_mail_ownership as ownership
from mc import question_channel_retention as retention
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_lifecycle_steps import BACKOFF_START_S, BACKOFF_MAX_S
from mc.agent_mail_outbound import cleanup as outbound_cleanup
from mc.core import _log

STEPS = ('store', 'deletion', 'revocations', 'flows', 'pull', 'acks', 'inbound', 'cleanup', 'retention')
REASONS = frozenset({
    '', 'ok', 'not_enrolled', 'store_held', 'step_failed', 'remote_adapter_unavailable',
    'remote_cancel_retryable', 'remote_revoke_retryable', 'remote_cancel_scope_mismatch',
    'lifecycle_state_corrupt', 'ownership_expired', 'delivery_pending', 'backoff',
    'stage_disabled', 'vault_locked', 'deletion_pending', 'worker_reconcile_failed',
    # 7d-2d lifecycle (closed codes only; remote text never reaches a report)
    'remote_renew_retryable', 'remote_activation_retryable', 'remote_proof_retryable', 'renewal_cap',
    'remote_unreachable', 'remote_not_acknowledged', 'remote_response_unsigned', 'remote_response_invalid',
    'remote_scope_mismatch', 'fence_token_missing', 'manifest_stale', 'lifecycle_raced', 'ownership_expired',
    # store holds (see agent_mail_factory.StoreHeld)
    'store_missing', 'store_corrupt', 'store_version', 'store_unavailable', 'marker_missing',
    'marker_corrupt', 'marker_version', 'marker_inconsistent', 'seal_missing', 'not_configured',
    'schema_corrupt', 'deployment_unconfigured', 'service_key_changed', 'response_unverified',
    'registration_inactive', 'registration_frozen', 'stale_generation', 'fenced', 'revoked',
    'connection_lost', 'service_unavailable', 'storage_unavailable', 'request_limit', 'abuse_limit',
    'unauthorized', 'vault_locked_or_unconfigured', 'activation_pending', 'flow_pending', 'flow_expired',
    'flow_held', 'pull_disconnected', 'scan_pending', 'local_disabled', 'ack_pending', 'ack_held',
    'ack_inconsistent', 'revocation_pending', 'recovery_busy',
})
MAX_PASSES = 5
MAX_BATCH = 100
# Reuse public retry limits; keep the retained legacy module reachable to the
# frozen app until its separately scoped removal. No lifecycle step is called.
BACKOFF_START_MS, BACKOFF_MAX_MS = int(BACKOFF_START_S * 1000), int(BACKOFF_MAX_S * 1000)


@dataclass(frozen=True)
class Step:
    name: str
    state: str   # ok | pending | held | skipped | failed
    reason: str = ''
    count: int = 0
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {'name': self.name, 'state': self.state, 'reason': self.reason, 'count': self.count}
        if self.details is not None:
            result['details'] = self.details
        return result


def _code(exc: BaseException, default: str = 'step_failed') -> str:
    reason = getattr(exc, 'reason', None) or (str(exc) if isinstance(exc, EnrollmentRefused) else '')
    return reason if isinstance(reason, str) and reason and reason in REASONS else default


class Recovery:
    """One synchronous pass; transport ownership stays with PullClient.

    max_passes caps both fence attempts and each local worker callback. A failed
    fence is scheduled durably BEFORE I/O, so a crash/restart cannot tight-loop.
    Attempts reset after restart, but the persisted deadline still applies.
    No recovery path removes an ack, creates consent, or advances a flow.
    """

    def __init__(self, factory: Any, *, clock: Callable[[], float] = time.time,
                 launder: Callable[[], dict[str, str]] | None = None,
                 wake: Callable[[], dict[str, str]] | None = None,
                 wake_reconcile: Callable[[], None] | None = None,
                 retire: Callable[[], dict[str, int]] = retention.retire,
                 max_passes: int = MAX_PASSES):
        if type(max_passes) is not int or not 1 <= max_passes <= MAX_BATCH:
            raise ValueError('max_passes must be between 1 and 100')
        self.factory, self.clock = factory, clock
        self.launder, self.wake, self.wake_reconcile = launder, wake, wake_reconcile
        self.retire, self.max_passes = retire, max_passes
        self._attempts: OrderedDict[str, int] = OrderedDict()
        self._lock = threading.Lock()

    def run(self) -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {'ok': False, 'steps': [Step('store', 'pending', 'recovery_busy').as_dict()]}
        try:
            return self._run()
        finally:
            self._lock.release()

    def _run(self) -> dict[str, Any]:
        steps: list[Step] = []
        store = None
        # Keep the read-only question owner lookup available even for a legacy
        # store held by pull validation. This neither adopts nor upgrades it.
        ownership.install_reader(ownership.factory_reader(self.factory))
        try:
            store = self.factory.require_pull_store()
            steps.append(Step('store', 'ok', 'ok'))
        except Exception as exc:
            reason = _code(exc, 'store_unavailable')
            state = 'skipped' if reason == 'not_enrolled' else 'held'
            steps.append(Step('store', state, reason))
            if state == 'held':
                self._notice('store', reason)
        steps.append(self._safe('deletion', self._deletion))
        for name, fn in (('revocations', self._revocations), ('flows', self._flows),
                         ('pull', self._pull), ('acks', self._acks),
                         ('inbound', self._inbound), ('cleanup', self._cleanup)):
            steps.append(self._safe(name, fn, store) if store is not None else
                         Step(name, 'skipped', 'store_held' if steps[0].state == 'held' else 'not_enrolled'))
        steps.append(self._safe('retention', self._retention))
        return {'steps': [s.as_dict() for s in steps],
                'ok': all(s.state in ('ok', 'skipped') for s in steps)}

    @staticmethod
    def _notice(name: str, reason: str) -> None:
        # Code-owned names + allowlisted reasons only; never exception bodies.
        _log(f'[agent-mail] recovery step {name} failed: {reason}', flush=True)

    def _safe(self, name: str, fn: Callable[..., Step], *args: Any) -> Step:
        try:
            result = fn(*args)
        except Exception as exc:
            result = Step(name, 'failed', _code(exc))
        if result.state in ('failed', 'held') or (result.state == 'pending' and
                                                result.reason == 'deletion_pending'):
            self._notice(name, result.reason)
        return result

    def _deletion(self) -> Step:
        if not self.factory.paths.root.exists():
            return Step('deletion', 'ok', 'ok')
        result = deletion.apply_intents(self.factory)
        return (Step('deletion', 'pending', 'deletion_pending', result['pending'])
                if result['pending'] else Step('deletion', 'ok', 'ok', result['applied']))

    def _revocations(self, store: Any) -> Step:
        now = int(self.clock() * 1000)
        with store._read() as db:
            rows = [dict(r) for r in db.execute("""SELECT * FROM mail_pull_fences
                WHERE state='pending' AND next_attempt_ms<=?
                ORDER BY next_attempt_ms,created_ms,op_id LIMIT ?""", (now, self.max_passes))]
        failures: list[str] = []
        done = 0
        for row in rows:
            try:
                if not self._claim_fence(store, row, now):
                    continue
                # Factory validates the SAME store. No transaction spans review,
                # transport, signing or response verification.
                client = self.factory.service_client()
                if client.store is not store:
                    raise EnrollmentRefused('store_held')
                reply = client.fence(row['op_id'])
                self._settle_fence(store, row, reply)
                self._attempts.pop(row['op_id'], None)
                done += 1
            except Exception as exc:
                reason = _code(exc)
                self._notice('revocations', reason)
                failures.append(reason)
        pending = None
        try:
            with store._read() as db:
                pending = db.execute("SELECT COUNT(*) FROM mail_pull_fences WHERE state='pending'").fetchone()[0]
        except Exception as exc:
            reason = _code(exc)
            self._notice('revocation_count', reason)
            failures.append(reason)
        detail = {'pending_count': pending, 'failures': failures}
        if failures:
            return Step('revocations', 'failed', failures[0], done, detail)
        return Step('revocations', 'pending' if pending else 'ok',
                    'revocation_pending' if pending else 'ok', done, detail)

    def _claim_fence(self, store: Any, row: dict[str, Any], now: int) -> bool:
        attempt = min(self._attempts.get(row['op_id'], 0) + 1, 8)
        delay = min(BACKOFF_START_MS * 2 ** (attempt - 1), BACKOFF_MAX_MS)
        with store._tx() as db:
            changed = db.execute("""UPDATE mail_pull_fences SET next_attempt_ms=?
                WHERE op_id=? AND install_id=? AND generation=? AND flow_id=?
                AND fence_token=? AND mode=? AND state='pending' AND next_attempt_ms=?""",
                (now + delay, *(row[k] for k in ('op_id', 'install_id', 'generation', 'flow_id',
                  'fence_token', 'mode', 'next_attempt_ms')))).rowcount
        if changed != 1:
            return False
        row['next_attempt_ms'] = now + delay
        self._attempts[row['op_id']] = attempt
        self._attempts.move_to_end(row['op_id'])
        if len(self._attempts) > 1000:
            self._attempts.popitem(last=False)
        return True

    @staticmethod
    def _settle_fence(store: Any, row: dict[str, Any], reply: dict[str, Any]) -> None:
        # ServiceClient verifies the signature and response scope. Check again at
        # this composition boundary before persisting the exact returned receipt.
        if (type(reply) is not dict or reply.keys() !=
                {'v', 'code', 'request_id', 'install_id', 'generation', 'state', 'fence_receipt'}
                or type(reply['v']) is not int or reply['v'] != 1 or reply['code'] != 'fenced'
                or type(reply['generation']) is not int
                or (reply['request_id'], reply['install_id'], reply['generation']) !=
                (row['op_id'], row['install_id'], row['generation'])
                or reply['state'] not in {'fenced', 'already_fenced', 'already_superseded'}
                or not isinstance(reply['fence_receipt'], str)
                or re.fullmatch('[a-f0-9]{32}', reply['fence_receipt']) is None):
            raise EnrollmentRefused('response_unverified')
        with store._tx() as db:
            changed = db.execute("""UPDATE mail_pull_fences SET state='acked',receipt_id=?
                WHERE op_id=? AND install_id=? AND generation=? AND flow_id=? AND fence_token=?
                AND mode=? AND state='pending' AND next_attempt_ms=?""",
                (reply['fence_receipt'], *(row[k] for k in ('op_id', 'install_id', 'generation',
                  'flow_id', 'fence_token', 'mode', 'next_attempt_ms')))).rowcount
            if changed != 1:
                raise EnrollmentRefused('lifecycle_raced')

    def _flows(self, store: Any) -> Step:
        # Reporting is intentionally read-only. Unit 19b owns activation retries.
        with store._read() as db:
            rows = db.execute("""SELECT state,COUNT(*) AS n,MIN(expires_ms) AS expires_ms FROM mail_pull_flows
                WHERE state NOT IN ('completed','cancelled','expired')
                GROUP BY state""").fetchall()
        now = int(self.clock() * 1000)
        for state, reason in (('held', 'flow_held'), ('enable_pending', 'activation_pending')):
            count = sum(r['n'] for r in rows if r['state'] == state)
            if count:
                return Step('flows', 'held' if state == 'held' else 'pending', reason, count)
        if rows:
            reason = 'flow_expired' if any(r['expires_ms'] <= now for r in rows) else 'flow_pending'
            return Step('flows', 'pending', reason, sum(r['n'] for r in rows))
        return Step('flows', 'ok', 'ok')

    def _pull(self, store: Any) -> Step:
        with store._read() as db:
            db.execute('BEGIN')  # One read snapshot; _read closes/rolls it back.
            accounts = db.execute('SELECT * FROM mail_pull_accounts LIMIT 2').fetchall()
            if not accounts:
                return Step('pull', 'skipped', 'not_enrolled')
            if len(accounts) != 1:
                raise EnrollmentRefused('schema_corrupt')
            a = accounts[0]
            g = db.execute('SELECT * FROM mail_pull_generations WHERE install_id=? AND generation=?',
                           (a['install_id'], a['generation'])).fetchone()
            if (g is None or any(g[k] != a[k] for k in ('owner_generation', 'key_id', 'seal_flow_id'))):
                raise EnrollmentRefused('schema_corrupt')
            if db.execute("""SELECT 1 FROM mail_pull_fences WHERE install_id=? AND generation=?
                AND (mode='revoke' OR flow_id=?) LIMIT 1""",
                (a['install_id'], a['generation'], a['seal_flow_id'])).fetchone():
                return Step('pull', 'held', 'fenced')
            if a['state'] != 'active' or a['local_enabled'] != 1 or g['state'] != 'current':
                return Step('pull', 'held', 'local_disabled')
            c = db.execute('SELECT * FROM mail_pull_connections WHERE install_id=?',
                           (a['install_id'],)).fetchone()
        if (c is None or (c['generation'], c['owner_generation']) !=
                (a['generation'], a['owner_generation']) or c['epoch'] <= 0 or
                c['connection_id'] is None or c['reauth_ms'] is None or
                c['reauth_ms'] <= int(self.clock() * 1000)):
            return Step('pull', 'pending', 'pull_disconnected')
        # This is durable scan evidence, never a live connectivity assertion.
        if not c['scan_complete']:
            return Step('pull', 'pending', 'scan_pending')
        return Step('pull', 'ok', 'ok')

    def _acks(self, store: Any) -> Step:
        with store._read() as db:
            db.execute('BEGIN')  # Ack completion may commit concurrently.
            invalid = db.execute("""SELECT 1 FROM mail_pull_ack_outbox o
                LEFT JOIN mail_pull_receipts r USING(event_id)
                WHERE r.event_id IS NULL OR o.local_receipt_id!=r.local_receipt_id
                OR o.event_digest!=r.event_digest OR o.body_digest!=r.body_digest
                OR r.cloud_receipt_id IS NOT NULL
                UNION ALL SELECT 1 FROM mail_pull_receipts r
                LEFT JOIN mail_pull_ack_outbox o USING(event_id)
                WHERE r.cloud_receipt_id IS NULL AND o.event_id IS NULL LIMIT 1""").fetchone()
            rows = db.execute("""SELECT CASE WHEN o.state='held' OR a.install_id IS NULL
                OR a.state!='active' OR a.local_enabled!=1 OR a.generation!=r.generation
                OR a.owner_generation!=r.owner_generation OR g.state IS NULL OR g.state!='current'
                OR EXISTS(SELECT 1 FROM mail_pull_fences f WHERE f.install_id=r.install_id
                    AND f.generation=r.generation AND (f.mode='revoke' OR f.flow_id=g.seal_flow_id))
                THEN 'held' ELSE 'pending' END AS state,
                COUNT(*) AS n,MIN(o.created_ms) AS oldest FROM mail_pull_ack_outbox o
                LEFT JOIN mail_pull_receipts r USING(event_id)
                LEFT JOIN mail_pull_accounts a ON a.install_id=r.install_id
                LEFT JOIN mail_pull_generations g ON g.install_id=r.install_id AND g.generation=r.generation
                GROUP BY 1""").fetchall()
        now = int(self.clock() * 1000)
        counts = {r['state']: r['n'] for r in rows}
        ages = {r['state']: max(0, now - r['oldest']) for r in rows}
        detail = {'pending_count': counts.get('pending', 0), 'held_count': counts.get('held', 0),
                  'oldest_age_ms': max(ages.values(), default=0),
                  'oldest_pending_age_ms': ages.get('pending', 0),
                  'oldest_held_age_ms': ages.get('held', 0)}
        count = sum(counts.values())
        if invalid:
            return Step('acks', 'held', 'ack_inconsistent', count, detail)
        if counts.get('held'):
            return Step('acks', 'held', 'ack_held', count, detail)
        return Step('acks', 'pending' if count else 'ok', 'ack_pending' if count else 'ok', count, detail)

    def _inbound(self, store: Any) -> Step:
        # Preserve existing local custody work; each callback fails independently.
        failures: list[str] = []
        done = 0
        disabled = False
        reconcile = self._safe('local_reconcile', lambda: self._reconcile(store))
        if reconcile.state == 'failed':
            failures.append(reconcile.reason)
        for name, fn in (('launder', self.launder), ('wake', self.wake)):
            if fn is None:
                continue
            try:
                for _ in range(self.max_passes):
                    result = fn()
                    if result.get('state') in ('idle', 'disabled'):
                        disabled |= result.get('state') == 'disabled'
                        break
                    if result.get('state') not in ('ready', 'submitted', 'turn_completed', 'held', 'blocked', 'retryable',
                                                   'rejected', 'expired', 'revoked', 'uncertain', 'failed'):
                        raise EnrollmentRefused('step_failed')
                    if result.get('state') in ('failed', 'held', 'blocked', 'retryable', 'uncertain'):
                        raise EnrollmentRefused('step_failed')
                    done += 1
            except Exception as exc:
                reason = _code(exc)
                self._notice(name, reason)
                failures.append(reason)
        if self.wake_reconcile is not None:
            try:
                self.wake_reconcile()
            except Exception as exc:
                reason = _code(exc)
                self._notice('wake_reconcile', reason)
                failures.append(reason)
        if failures:
            return Step('inbound', 'failed', failures[0], done, {'failures': failures})
        if disabled or (self.launder is None and self.wake is None):
            return Step('inbound', 'skipped', 'stage_disabled', done)
        return Step('inbound', 'ok', 'ok', done)

    @staticmethod
    def _reconcile(store: Any) -> Step:
        store.reconcile()
        return Step('local_reconcile', 'ok', 'ok')

    def _cleanup(self, store: Any) -> Step:
        # No sends; neither operation clears a pull ack or ownership tombstone.
        failures: list[str] = []
        done = 0
        for name, fn in (('purge', store.purge_terminal), ('outbound_cleanup', lambda: outbound_cleanup(store))):
            try:
                done += fn() or 0
            except Exception as exc:
                reason = _code(exc)
                self._notice(name, reason)
                failures.append(reason)
        return (Step('cleanup', 'failed', failures[0], done, {'failures': failures}) if failures
                else Step('cleanup', 'ok', 'ok', done))

    def _retention(self) -> Step:
        result = self.retire()
        return Step('retention', 'ok', 'ok', result.get('retired', 0))
