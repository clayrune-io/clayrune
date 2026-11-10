"""Bounded, durable agent-mail recovery pass (MC-1166 unit 7d-1).

One ``Recovery.run()`` is one bounded pass over the preflight's "Recovery order"
(steps 1-3 and 5-7; step 4 only reaches an injected, already-existing signed
endpoint). Each step runs independently inside its own handler, reports one
closed reason code, and a failing step never stops the next. It creates no
identity, key, root, pin, generation or flow; it replays no human ceremony call
(start/confirm/code/enable/owner/key/repin); it never unlocks the vault; and it
holds no manager/global question lock or SQLite transaction across network,
signing, extraction or model work.

The remote cancel/revoke/status/renewal protocol belongs to unit 7d-2. Until a
real adapter exists every unresolved cancellation is RETAINED and reported as
``remote_adapter_unavailable``; no acknowledgment is ever recorded, so the
lifecycle's "no new generation until the old revocation is acknowledged" rule
holds. Expired ownership is reported and blocks sends (``Outbound._authority``).

Reports carry counts and closed codes only: no bodies, mailbox codes, secrets,
signed credentials or remote exception text.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from mc import agent_mail_deletion as deletion
from mc import agent_mail_ownership as ownership
from mc import question_channel_retention as retention
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_enrollment_revocations import Revocations
from mc.agent_mail_lifecycle_steps import Backoff, LifecycleSteps
from mc.agent_mail_outbound import cleanup as outbound_cleanup
from mc.agent_mail_remote import REMOTE_UNAVAILABLE, UnavailableRemote
from mc.core import _log

STEPS = ('store', 'deletion', 'revocations', 'flows', 'renew', 'activation', 'replay', 'inbound', 'cleanup', 'retention')
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
})
MAX_PASSES = 5
REPLAY_MIN_INTERVAL_S = 900.0  # <= 96 calls/day, well inside the shared 200/day budget


@dataclass(frozen=True)
class Step:
    name: str
    state: str   # ok | pending | held | skipped | failed
    reason: str = ''
    count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {'name': self.name, 'state': self.state, 'reason': self.reason, 'count': self.count}


def _code(exc: BaseException, default: str = 'step_failed') -> str:
    reason = getattr(exc, 'reason', None) or (str(exc) if isinstance(exc, EnrollmentRefused) else '')
    return reason if reason and reason in REASONS else default


def _table(db: Any, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


class Recovery:
    def __init__(self, factory: Any, *, clock: Callable[[], float] = time.time,
                 launder: Callable[[], dict[str, str]] | None = None,
                 wake: Callable[[], dict[str, str]] | None = None,
                 wake_reconcile: Callable[[], None] | None = None,
                 replay: Callable[[], None] | None = None,
                 lifecycle: LifecycleSteps | None = None,
                 retire: Callable[[], dict[str, int]] = retention.retire,
                 replay_interval_s: float = REPLAY_MIN_INTERVAL_S, max_passes: int = MAX_PASSES):
        self.factory, self.clock = factory, clock
        self.launder, self.wake, self.wake_reconcile, self.replay = launder, wake, wake_reconcile, replay
        # Renewal / proof push / activation exist only when a lifecycle client was injected (default off).
        self.lifecycle = lifecycle
        self._backoff = Backoff(clock)
        self.retire, self.replay_interval_s, self.max_passes = retire, replay_interval_s, max_passes
        self._last_replay: float | None = None

    # --- one pass --------------------------------------------------------
    def run(self) -> dict[str, Any]:
        steps: list[Step] = []
        store = None
        try:
            store = self.factory.open_store()
            ownership.install_reader(ownership.factory_reader(self.factory))
            steps.append(Step('store', 'skipped', 'not_enrolled') if store is None else Step('store', 'ok', 'ok'))
        except Exception as exc:
            steps.append(Step('store', 'held', _code(exc, 'store_unavailable')))
        healthy = store is not None
        # Deletion intents must replay as soon as the store can take them, and are
        # still retired when the store never existed (nothing to revoke).
        steps.append(self._safe('deletion', self._deletion))
        for name, fn in (('revocations', self._revocations), ('flows', self._flows),
                         ('renew', self._renew), ('activation', self._activation),
                         ('replay', self._replay), ('inbound', self._inbound),
                         ('cleanup', self._cleanup)):
            steps.append(self._safe(name, fn, store) if healthy else Step(name, 'skipped', 'store_held'
                         if steps[0].state == 'held' else 'not_enrolled'))
        steps.append(self._safe('retention', self._retention))
        return {'steps': [s.as_dict() for s in steps],
                'ok': all(s.state in ('ok', 'skipped') for s in steps)}

    def _safe(self, name: str, fn: Callable[..., Step], *args: Any) -> Step:
        try:
            return fn(*args)
        except Exception as exc:
            reason = _code(exc)
            _log(f'[agent-mail] recovery step {name} failed: {reason}', flush=True)
            return Step(name, 'failed', reason)

    # --- steps -------------------------------------------------------------
    def _deletion(self, *_: Any) -> Step:
        if not self.factory.paths.root.exists():
            return Step('deletion', 'ok', 'ok')
        result = deletion.apply_intents(self.factory)
        if result['pending']:
            return Step('deletion', 'pending', 'deletion_pending', result['pending'])
        return Step('deletion', 'ok', 'ok', result['applied'])

    def _revocations(self, store: Any) -> Step:
        with store._read() as db:
            if not _table(db, 'mail_enrollment_revocations'):
                return Step('revocations', 'ok', 'ok')
            pending = db.execute('SELECT COUNT(*) FROM mail_enrollment_revocations WHERE done=0').fetchone()[0]
        if not pending:
            return Step('revocations', 'ok', 'ok')
        remote = self.factory.remote
        if isinstance(remote, UnavailableRemote):
            # Retain the work; no ack exists. New-generation enable stays blocked.
            return Step('revocations', 'pending', REMOTE_UNAVAILABLE, pending)
        if not self._backoff.ready('revocations'):
            return Step('revocations', 'pending', 'backoff', pending)
        try:
            Revocations(store, remote).retry()
        except EnrollmentRefused as exc:
            self._backoff.fail('revocations')
            return Step('revocations', 'pending', _code(exc, 'remote_cancel_retryable'), pending)
        self._backoff.ok('revocations')
        return Step('revocations', 'ok', 'ok', pending)

    def _flows(self, store: Any) -> Step:
        """Report only. A human-authorized flow is never advanced from here."""
        with store._read() as db:
            if not _table(db, 'mail_enrollment_flow'):
                return Step('flows', 'ok', 'ok')
            row = db.execute('SELECT stage,ownership_expires_ms FROM mail_enrollment_flow WHERE slot=1').fetchone()
        if row is None or row['stage'] in ('disabled', 'preparing'):
            return Step('flows', 'ok', 'ok')
        if row['stage'] == 'delivery_pending':
            return Step('flows', 'pending', 'delivery_pending')
        expires = row['ownership_expires_ms']
        if row['stage'] == 'active' and type(expires) in (int, float) and self.clock() * 1000 >= expires:
            return Step('flows', 'pending', 'ownership_expired')
        return Step('flows', 'ok', 'ok')

    def _renew(self, store: Any) -> Step:
        """Ownership-lease renewal (5.4). Moves `ownership_expires_ms` forward only."""
        if self.lifecycle is None:
            return Step('renew', 'skipped', 'stage_disabled')
        state, reason, count = self.lifecycle.renew(store)
        return Step('renew', state, reason, count)

    def _activation(self, store: Any) -> Step:
        """Proof push and activation retry (4.8). Never enables; only re-sends what enable() committed."""
        if self.lifecycle is None:
            return Step('activation', 'skipped', 'stage_disabled')
        state, reason, count = self.lifecycle.activation(store)
        return Step('activation', state, reason, count)

    def _replay(self, *_: Any) -> Step:
        """Step 4: Worker replay reconcile through an injected, existing signed
        endpoint only, rate-limited far inside the shared daily budget."""
        if self.replay is None:
            return Step('replay', 'skipped', REMOTE_UNAVAILABLE)
        now = self.clock()
        if self._last_replay is not None and now - self._last_replay < self.replay_interval_s:
            return Step('replay', 'skipped', 'backoff')
        self._last_replay = now
        try:
            self.replay()
        except Exception:
            return Step('replay', 'failed', 'worker_reconcile_failed')
        return Step('replay', 'ok', 'ok')

    def _inbound(self, store: Any) -> Step:
        # Manager -> store ordering and claim fencing live inside the callees.
        store.reconcile()
        done = 0
        state, reason = 'ok', 'ok'
        for fn in (self.launder, self.wake):
            if fn is None:
                continue
            for _ in range(self.max_passes):
                result = fn()
                if result.get('state') in ('idle', 'disabled'):
                    if result.get('state') == 'disabled':
                        state, reason = 'skipped', 'stage_disabled'
                    break
                done += 1
        if self.wake_reconcile is not None:
            self.wake_reconcile()
        if self.launder is None and self.wake is None:
            return Step('inbound', 'skipped', 'stage_disabled')
        return Step('inbound', state, reason, done)

    def _cleanup(self, store: Any) -> Step:
        n = store.purge_terminal()
        outbound_cleanup(store)  # never sends, never enables
        return Step('cleanup', 'ok', 'ok', n)

    def _retention(self, *_: Any) -> Step:
        result = self.retire()
        return Step('retention', 'ok', 'ok', result.get('retired', 0))
