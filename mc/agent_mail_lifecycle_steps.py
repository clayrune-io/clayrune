"""Recovery-pass work for lease renewal, proof push and activation (MC-1166 unit 7d-2d, contract 4.8, 5.4).

Each handler returns `(state, reason, count)` and never raises: the recovery pass wraps it in one
`Step`. Nothing here enables, pins, changes stage, unlocks the vault, generates a key, or advances
a human-authorized flow; renewal only moves `ownership_expires_ms` forward, proof push only
repeats a challenge the human already armed, activation only re-sends what `enable()` already
committed. Reports are closed codes and counts, never remote text.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.core import _log

RENEW_BELOW_MS = 150_000
RENEW_MIN_INTERVAL_S = 120.0
PROOF_MIN_INTERVAL_S = 30.0
PENDING_CAP_MS = 24 * 3600 * 1000   # initial value; delivery_pending cannot renew forever
BACKOFF_START_S = 30.0
BACKOFF_MAX_S = 3600.0
LEASE_MS = 300_000
_TABLE = 'mail_lifecycle_renewal'   # never the mail_enrollment_ prefix (exact-set schema check)

_TRANSPORT = frozenset({'remote_unreachable', 'remote_not_acknowledged', 'remote_response_unsigned',
                        'remote_response_invalid'})

Result = tuple[str, str, int]


class Backoff:
    """Per work item: 30 s doubling to 1 h after a failure; `floor_s` between any two attempts."""

    def __init__(self, clock: Callable[[], float], floor_s: float = 0.0):
        self.clock, self.floor_s = clock, floor_s
        self._items: dict[str, tuple[int, float]] = {}   # key -> consecutive failures, next attempt at

    def ready(self, key: str) -> bool:
        item = self._items.get(key)
        return item is None or self.clock() >= item[1]

    def attempt(self, key: str) -> None:
        failures = self._items.get(key, (0, 0.0))[0]
        self._items[key] = (failures, self.clock() + self.floor_s)

    def fail(self, key: str) -> None:
        failures = self._items.get(key, (0, 0.0))[0] + 1
        delay = min(BACKOFF_START_S * 2 ** (failures - 1), BACKOFF_MAX_S)
        self._items[key] = (failures, self.clock() + max(delay, self.floor_s))

    def ok(self, key: str) -> None:
        self._items[key] = (0, self._items.get(key, (0, 0.0))[1])


def _reason(exc: BaseException, allowed: frozenset[str], default: str) -> str:
    text = str(exc) if isinstance(exc, EnrollmentRefused) else ''
    if text == 'vault_locked_or_unconfigured':
        return 'vault_locked'
    # Transport-shaped failures are one retryable class; the specific code stays in the client's exception.
    return text if text in allowed and text not in _TRANSPORT else default


class LifecycleSteps:
    def __init__(self, client: Any, *, clock: Callable[[], float] = time.time,
                 allowed_reasons: frozenset[str] = frozenset()):
        self.client, self.clock, self.allowed = client, clock, allowed_reasons
        self._renew_backoff = Backoff(clock, RENEW_MIN_INTERVAL_S)
        self._proof_backoff = Backoff(clock, PROOF_MIN_INTERVAL_S)
        self._activation_backoff = Backoff(clock)

    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    @staticmethod
    def _flow(db: Any) -> dict | None:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_enrollment_flow'").fetchone():
            return None
        row = db.execute('SELECT * FROM mail_enrollment_flow WHERE slot=1').fetchone()
        return dict(row) if row else None

    # --- renewal (5.4) -------------------------------------------------------------------------------
    def renew(self, store: Any) -> Result:
        with store._read() as db:
            row = self._flow(db)
        if (row is None or row['stage'] not in ('delivery_pending', 'active')
                or not row['confirmed'] or not row['ownership']):
            return 'ok', 'ok', 0
        now = self._now_ms()
        if row['ownership_expires_ms'] - now >= RENEW_BELOW_MS:
            return 'ok', 'ok', 0
        if row['stage'] == 'delivery_pending' and not self._within_pending_cap(store, row, now):
            return 'pending', 'renewal_cap', 0
        if not self._renew_backoff.ready('renew'):
            return 'skipped', 'backoff', 0
        self._renew_backoff.attempt('renew')
        try:
            self.client.renew()
        except Exception as exc:
            reason = _reason(exc, self.allowed, 'remote_renew_retryable')
            if reason == 'vault_locked':
                return 'skipped', 'vault_locked', 0   # not a remote failure: do not escalate backoff
            self._renew_backoff.fail('renew')
            _log(f'[agent-mail] lease renewal failed: {reason}', flush=True)
            return 'pending', reason if reason in self.allowed else 'remote_renew_retryable', 0
        self._renew_backoff.ok('renew')
        return 'ok', 'ok', 1

    def _within_pending_cap(self, store: Any, row: dict, now: int) -> bool:
        """started_ms = first observation, estimated as the verify time (a fresh lease is 300 s)."""
        with store._tx() as db:
            db.execute(f'CREATE TABLE IF NOT EXISTS {_TABLE} (flow TEXT NOT NULL, epoch INTEGER NOT NULL, '
                       'started_ms INTEGER NOT NULL, PRIMARY KEY(flow, epoch))')
            db.execute(f'INSERT OR IGNORE INTO {_TABLE} VALUES(?,?,?)',
                       (row['flow'], row['epoch'], row['ownership_expires_ms'] - LEASE_MS))
            started = db.execute(f'SELECT started_ms FROM {_TABLE} WHERE flow=? AND epoch=?',
                                 (row['flow'], row['epoch'])).fetchone()[0]
        return now - started < PENDING_CAP_MS

    # --- proof push + activation (4.8) ---------------------------------------------------------------
    def activation(self, store: Any) -> Result:
        with store._read() as db:
            row = self._flow(db)
            proof = activations = None
            if row is not None:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_delivery_proof'").fetchone():
                    proof = db.execute('SELECT state,expires_ms FROM mail_delivery_proof WHERE install=? AND generation=?',
                                       (row['install'], row['generation'])).fetchone()
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_delivery_activation'").fetchone():
                    activations = [dict(r) for r in db.execute(
                        "SELECT * FROM mail_delivery_activation WHERE state='pending' AND install=? AND "
                        'generation=? AND epoch=?', (row['install'], row['generation'], row['epoch']))]
        if row is None:
            return 'ok', 'ok', 0
        state, reason, count = 'ok', 'ok', 0
        if (row['stage'] == 'delivery_pending' and proof is not None and proof['state'] in ('armed', 'proved')
                and self._now_ms() < proof['expires_ms']):
            outcome = self._proof(proof['state'])
            if outcome is not None:
                state, reason = 'pending', outcome
        if activations and row['stage'] == 'active':
            for item in activations:
                key = f"activate:{item['install']}:{item['generation']}:{item['epoch']}"
                if not self._activation_backoff.ready(key):
                    state, reason = 'pending', 'backoff'
                    continue
                self._activation_backoff.attempt(key)
                try:
                    self.client.activate(item['receipt_digest'])
                except Exception as exc:
                    code = _reason(exc, self.allowed, 'remote_activation_retryable')
                    if code != 'vault_locked':
                        self._activation_backoff.fail(key)
                    _log(f'[agent-mail] activation failed: {code}', flush=True)
                    state, reason = 'pending', code if code in self.allowed else 'remote_activation_retryable'
                    continue
                # ONLY a verified, scope-equal, service-signed answer reaches this line.
                with store._tx() as db:
                    db.execute("UPDATE mail_delivery_activation SET state='acked' WHERE install=? AND "
                               "generation=? AND epoch=? AND state='pending'",
                               (item['install'], item['generation'], item['epoch']))
                self._activation_backoff.ok(key)
                count += 1
        return state, reason, count

    def _proof(self, local_state: str) -> str | None:
        """Refresh the cached signed Worker status, and push the armed challenge only when the
        Worker holds no live proof for this generation (a re-arm would erase a verified one)."""
        if not self._proof_backoff.ready('proof'):
            return None
        self._proof_backoff.attempt('proof')
        try:
            status = self.client.status()
            worker_state = status['proof']['state'] if isinstance(status.get('proof'), dict) else 'none'
            if local_state == 'armed' and worker_state in ('none', 'invalidated'):
                self.client.proof_arm()
        except Exception as exc:
            code = _reason(exc, self.allowed, 'remote_proof_retryable')
            if code == 'vault_locked':
                return 'vault_locked'
            self._proof_backoff.fail('proof')
            _log(f'[agent-mail] proof push failed: {code}', flush=True)
            return code if code in self.allowed else 'remote_proof_retryable'
        self._proof_backoff.ok('proof')
        return None
