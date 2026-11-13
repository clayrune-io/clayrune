"""Server-owned recovery and pull-client lifecycle (MC-1166 unit 24). Default OFF.

One daemon thread, started only when ``agent_mail_supervisor_enabled`` is True,
runs ``Recovery.run`` on a bounded interval. A pass that reports a held store or
a failed step backs off exponentially (capped); a clean pass resets it. Start and
stop are idempotent. A failing pass never kills the thread, and a live thread is
never reported as healthy on its own. A configured client must also have a live
worker and current durable scan evidence. Activity timestamps are durable client
evidence; reason transitions carry the time this supervisor observed them.

Only this owner starts/stops the factory's cached client. Transport reconnect,
frame replay and signed ack completion remain in PullClient. A halted worker is
not blindly restarted: a changed lifecycle/pin scope, an observed vault unlock,
or an explicit stop/start permits another attempt. Shutdown shares one deadline
between client cancellation and the recovery thread join. A pending cancellation
retains ownership and blocks a replacement worker. Import/construction is inert.

Installing the read-only ownership reader is separate from, and does not depend
on, the supervisor flag: it only reads durable question owners, so the question
channel can recover them before eligibility even with every mail flag off.
No agent-facing enable/approve surface exists; the flag is config-only.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from mc import agent_mail_ownership as ownership
from mc import question_channel_retention as retention
from mc import state
from mc.agent_mail_enrollment_keys import EnrollmentRefused, SealReference
from mc.agent_mail_pull_client import ERRORS, HALT
from mc.agent_mail_recovery import REASONS, Recovery
from mc.core import _log

FLAG = 'agent_mail_supervisor_enabled'
INTERVAL_KEY = 'agent_mail_supervisor_interval_s'
DEFAULT_INTERVAL_S = 60.0
MAX_BACKOFF_S = 900.0
_CODES = REASONS | ERRORS | HALT | {
    'client_already_running', 'client_recomposition_required', 'client_exited',
    'client_start_failed', 'client_stop_failed', 'cancellation_pending',
    'cancel_wakeup_failed', 'hello_timeout', 'pong_timeout', 'connection_closed',
    'close_frame_failed', 'socket_close_failed', 'disconnect_state_failed',
    'worker_failed', 'state_write_failed', 'lock_release_failed', 'local_refused',
    'thread_start_failed', 'thread_shutdown_pending', 'worker_stalled',
    'status_failed', 'clock_failed', 'stopped',
}


def _code(value: Any, fallback: str = 'step_failed') -> str:
    return value if isinstance(value, str) and value in _CODES else fallback


class Supervisor:
    def __init__(self, recovery: Recovery, *, config: dict[str, Any] | None = None,
                 clock: Callable[[], float] = time.time, interval_s: float = DEFAULT_INTERVAL_S,
                 max_backoff_s: float = MAX_BACKOFF_S, factory: Any = None):
        self.recovery, self._config, self.clock = recovery, config, clock
        self.interval_s, self.max_backoff_s, self.factory = interval_s, max_backoff_s, factory
        self._lock = threading.RLock()
        self._transition = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.Lock()
        self.last_attempt: float | None = None
        self.last_success: float | None = None
        self.failures = 0
        self.report: dict[str, Any] = {}
        self.last_tick: float | None = None
        self._started_at: float | None = None
        self._stalled = False
        self.last_failure: dict[str, Any] | None = None
        self.failure_count = 0
        self._recent_failures: list[dict[str, Any]] = []
        self._client: Any = None
        self._owns_client = False
        self._client_started = False
        self._client_scope: tuple[Any, ...] | None = None
        self._initial_epoch = 0
        self._client_run = 0
        self._client_snapshot: dict[str, Any] = {'state': 'not_configured', 'running': False}
        self.last_client_event: dict[str, Any] | None = None
        self._client_event: dict[str, Any] = {}
        self._client_failure: tuple[Any, ...] | None = None

    def _timestamp(self) -> float:
        try:
            return self.clock()
        except Exception as exc:
            # Recording a broken injected clock must itself remain observable.
            now = time.time()
            failure = {'source': 'clock', 'reason': 'clock_failed', 'at': now}
            with self._lock:
                self.last_failure = failure
                self.failure_count += 1
                self._recent_failures = (self._recent_failures + [failure])[-20:]
            _log(f'[agent-mail] supervisor clock failed: {type(exc).__name__}', flush=True)
            return now

    def _failed(self, source: str, reason: str, exc: Exception | None = None) -> None:
        reason = _code(reason or None)
        failure = {'source': source, 'reason': reason, 'at': self._timestamp()}
        with self._lock:
            self.last_failure = failure
            self.failure_count += 1
            self._recent_failures = (self._recent_failures + [failure])[-20:]
        detail = f' ({type(exc).__name__})' if exc is not None else ''
        _log(f'[agent-mail] supervisor {source} failed: {reason}{detail}', flush=True)

    @property
    def config(self) -> dict[str, Any]:
        return state.CONFIG if self._config is None else self._config

    def enabled(self) -> bool:
        return self.config.get(FLAG) is True

    # --- one bounded pass ---------------------------------------------------
    def tick(self) -> dict[str, Any]:
        """Never raises. Overlapping ticks (two callers) are skipped, not queued."""
        if not self._tick_lock.acquire(blocking=False):
            return {'ok': False, 'steps': [], 'skipped': 'tick_in_progress'}
        try:
            attempted = self._timestamp()
            try:
                report = self.recovery.run()
                bad = report.get('ok') is not True
                for step in report['steps']:
                    if step['state'] in ('failed', 'held'):
                        self._failed('recovery', _code(step.get('reason')))
            except Exception as exc:  # defence in depth: run() already isolates steps
                self._failed('recovery', 'step_failed', exc)
                report = {'ok': False, 'steps': [{'name': 'supervisor', 'state': 'failed',
                                                  'reason': 'step_failed', 'count': 0}]}
                bad = True
            with self._lock:
                self.failures = self.failures + 1 if bad else 0
                if not bad:
                    self.last_success = attempted
                self.report, self.last_attempt = report, attempted
                self.last_tick = self._timestamp()
            return report
        finally:
            self._tick_lock.release()

    def delay(self) -> float:
        if not self.failures:
            return self.interval_s
        return min(self.max_backoff_s, self.interval_s * (2 ** min(self.failures, 16)))

    # --- thread ownership ------------------------------------------------------
    def start(self) -> bool:
        """True if THIS call started the thread. False when disabled or running."""
        with self._transition:
            return self._start()

    def _start(self) -> bool:
        with self._lock:
            if (not self.enabled() or (self._thread is not None and self._thread.is_alive())
                    or (self._owns_client and self._client.running)):
                return False
            self._stop.clear()
            self._client_started = False
            self.last_attempt = self.last_tick = None
            self.report = {}
            self._started_at = self._timestamp()
            self._stalled = False
            self._thread = threading.Thread(target=self._loop, name='agent-mail-supervisor', daemon=True)
            try:
                self._thread.start()
            except Exception as exc:
                self._thread = None
                self._stop.set()
                self._failed('thread', 'thread_start_failed', exc)
                return False
            return True

    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                if self.enabled():
                    try:
                        self._drive_client()
                    except Exception as exc:
                        reason = _code(getattr(exc, 'reason', None) or
                                       (str(exc) if isinstance(exc, EnrollmentRefused) else None),
                                       'client_start_failed')
                        self._failed('client', reason, exc)
                        stopped = self._cancel_client(0)
                        self._client_snapshot = {'state': 'held' if stopped else 'stopping',
                                                 'running': not stopped, 'reason': reason}
                    if self._stop.is_set():
                        break
                    self.tick()
                else:
                    self._cancel_client(0)
                if self._stop.wait(self.delay()):
                    break
        except Exception as exc:
            self._failed('thread', 'worker_failed', exc)
            self._stop.set()
            self._cancel_client(0)

    def _drive_client(self) -> None:
        if self.factory is None:
            return
        try:
            client = self.factory.pull_client()  # Checked shared store, never bootstrap.
        except Exception as exc:
            if getattr(exc, 'reason', None) == 'not_enrolled':
                stopped = self._cancel_client(0)
                self._client_snapshot = {'state': 'not_configured' if stopped else 'stopping',
                                         'running': not stopped,
                                         'reason': 'not_enrolled'}
                return
            raise
        with client.store._read() as db:
            db.execute('BEGIN')
            account = db.execute('''SELECT a.*,g.service_key_id,g.service_public_key,
                g.manifest_sequence,g.manifest_digest FROM mail_pull_accounts a
                LEFT JOIN mail_pull_generations g ON g.install_id=a.install_id AND g.generation=a.generation
                WHERE a.install_id=?''',
                                 (client.install_id,)).fetchone()
            if account is None:
                raise EnrollmentRefused('not_enrolled')
            current = db.execute('SELECT state FROM mail_pull_generations WHERE install_id=? AND generation=?',
                                 (client.install_id, account['generation'])).fetchone()
            fenced = db.execute("""SELECT 1 FROM mail_pull_fences WHERE install_id=? AND generation=?
                AND (mode='revoke' OR flow_id=?) LIMIT 1""",
                (client.install_id, account['generation'], account['seal_flow_id'])).fetchone()
            previous = db.execute('SELECT epoch FROM mail_pull_connections WHERE install_id=?',
                                  (client.install_id,)).fetchone()
        reason = ('fenced' if fenced else 'local_disabled' if current is None or
                  current['state'] != 'current' or account['state'] != 'active' or
                  account['local_enabled'] != 1 else '')
        if not reason:
            seal = client.seal.status(SealReference(account['seal_flow_id'], client.install_id,
                                                    account['generation'], account['key_id']))
            reason = '' if seal == 'ready' else _code(seal, 'vault_locked')
        if reason:
            stopped = self._cancel_client(0)
            with self._lock:
                if stopped:
                    self._client_started = False
                self._client_snapshot = {'state': 'held' if stopped else 'stopping',
                                         'running': not stopped, 'reason': reason}
            return
        scope = tuple(account[k] for k in ('install_id', 'generation', 'owner_generation',
            'key_id', 'seal_flow_id', 'service_key_id', 'service_public_key',
            'manifest_sequence', 'manifest_digest'))
        if self._client_scope != scope and self._owns_client:
            if not self._cancel_client(0):
                return
            self._client_started = False
        with self._lock:
            if self._stop.is_set():
                return
            if not self._client_started:
                if client.running:
                    raise EnrollmentRefused('client_already_running')
                global _client_owner
                with _client_owner_lock:
                    if _client_owner is not None and _client_owner is not self:
                        raise EnrollmentRefused('client_already_running')
                    _client_owner = self
                # Publish and start under the same lock used by stop's snapshot.
                # stop can never miss a newly started worker.
                self._client = client
                try:
                    client.start()
                except Exception:
                    # The loop logs and surfaces the original start exception.
                    with _client_owner_lock:
                        if _client_owner is self:
                            _client_owner = None
                    raise
                self._owns_client = self._client_started = True
                self._client_scope = scope
                self._client_run += 1
                self._initial_epoch = previous['epoch'] if previous is not None else 0
        self._sample_client()

    def _cancel_client(self, timeout: float) -> bool:
        with self._lock:
            client = self._client if self._owns_client else None
        if client is None:
            return True
        try:
            stopped = client.stop(timeout=max(0.0, timeout)) is True
        except Exception as exc:
            self._failed('shutdown', 'client_stop_failed', exc)
            return False
        with self._lock:
            if stopped:
                self._owns_client = False
                self._client_started = False
                global _client_owner
                with _client_owner_lock:
                    if _client_owner is self:
                        _client_owner = None
            self._client_snapshot = {'state': 'stopped' if stopped else 'stopping',
                                     'running': not stopped, 'reason': 'stopped' if stopped else 'cancellation_pending'}
            self.last_client_event = {**self._client_snapshot, 'observed_at': self._timestamp()}
            self._client_event = self._client_snapshot
        if not stopped:
            self._failed('shutdown', 'cancellation_pending')
        return stopped

    def _sample_client(self) -> None:
        with self._lock:
            client, owned, scope = self._client, self._owns_client, self._client_scope
            run, initial_epoch = self._client_run, self._initial_epoch
        if client is None or not owned:
            return
        running: bool | None = None
        try:
            running, notice = client.running, _code(client.reason, 'worker_failed')
            with client.store._read() as db:
                row = db.execute('''SELECT generation,owner_generation,epoch,reauth_ms,scan_complete,
                    last_activity_ms,failures,next_attempt_ms,reason FROM mail_pull_connections
                    WHERE install_id=?''', (client.install_id,)).fetchone()
            evidence = dict(row) if row is not None else {}
            reason = _code(evidence.get('reason', ''))
            fresh = (scope is not None and row is not None and
                     (row['generation'], row['owner_generation']) == scope[1:3] and
                     row['epoch'] > initial_epoch and row['reauth_ms'] is not None and
                     row['reauth_ms'] > self._timestamp() * 1000)
            if not running:
                reason = 'stopped' if self._stop.is_set() else notice or 'client_exited'
                phase = 'stopped' if self._stop.is_set() else 'held'
            elif fresh and not reason:
                phase = 'connected' if evidence['scan_complete'] else 'scanning'
            else:
                phase = 'reconnecting' if reason else 'connecting'
            snapshot = {'state': phase, 'running': running, 'reason': reason,
                        'last_notice': notice, **{k: evidence.get(k) for k in
                        ('epoch', 'last_activity_ms', 'failures', 'next_attempt_ms')}}
            signature = (notice, reason, evidence.get('epoch'), evidence.get('failures'))
            with self._lock:
                if run != self._client_run or not self._owns_client:
                    return  # A status read must not overwrite a newer lifecycle.
                new_notice = notice != self._client_event.get('last_notice', '')
                self._client_snapshot = snapshot
                if snapshot != self._client_event:
                    self.last_client_event = {**snapshot, 'observed_at': self._timestamp()}
                    self._client_event = snapshot
                if ((reason or (notice if new_notice else '')) and (reason or notice) != 'stopped'
                        and signature != self._client_failure):
                    self._client_failure = signature
                    self._failed('client', reason or notice)
        except Exception as exc:
            self._failed('client_status', 'status_failed', exc)
            with self._lock:
                self._client_snapshot = {'state': 'held', 'running': running, 'reason': 'status_failed'}

    def stop(self, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + max(0.0, timeout)
        if not self._transition.acquire(timeout=max(0.0, deadline - time.monotonic())):
            self._failed('shutdown', 'thread_shutdown_pending')
            return
        try:
            self._stop_until(deadline)
        finally:
            self._transition.release()

    def _stop_until(self, deadline: float) -> None:
        with self._lock:
            thread = self._thread
            self._stop.set()
        stopped = self._cancel_client(max(0.0, deadline - time.monotonic()))
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            if stopped and thread is self._thread and (thread is None or not thread.is_alive()):
                self._thread = None
            elif thread is not None and thread.is_alive():
                self._failed('shutdown', 'thread_shutdown_pending')

    # --- metadata for 7e ------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Bounded metadata only: counts and closed codes."""
        self._sample_client()
        alive = self._thread is not None and self._thread.is_alive()
        with self._lock:
            attempted, report = self.last_attempt, self.report
            activity = self.last_tick if self.last_tick is not None else self._started_at
            stale = (alive and activity is not None and
                     self._timestamp() - activity > max(5.0, 2 * self.delay()))
            if stale and not self._stalled:
                self._failed('thread', 'worker_stalled')
            self._stalled = stale
        steps = report.get('steps', [])
        first_bad = next((s for s in steps if s['state'] not in ('ok', 'skipped')), None)
        if not self.enabled():
            overall = 'off'
        elif attempted is None:
            overall = 'starting' if alive else 'stopped'
        elif not alive and self.enabled():
            overall = 'dead'
        else:
            overall = 'ok' if report.get('ok') else 'degraded'
        with self._lock:
            client = dict(self._client_snapshot)
            if ((alive and self._stop.is_set()) or
                    (client.get('running') and (self._stop.is_set() or not self.enabled()))):
                overall = 'stopping'
            elif overall == 'ok' and client['state'] not in ('not_configured', 'connected'):
                overall = 'degraded'
            if stale and overall not in ('off', 'stopping'):
                overall = 'degraded'
        out: dict[str, Any] = {
            'state': overall, 'thread_alive': alive, 'enabled': self.enabled(),
            'last_attempt': attempted, 'last_success': self.last_success,
            'consecutive_failures': self.failures, 'next_delay_s': self.delay(),
            'reason': 'worker_stalled' if stale else first_bad['reason'] if first_bad else client.get('reason', ''),
            'steps': steps, 'tick_stale': stale,
            'holds': ownership.held(), 'retention': retention.snapshot(),
            'last_tick': self.last_tick, 'client': client,
            'last_client_event': dict(self.last_client_event) if self.last_client_event else None,
            'last_failure': dict(self.last_failure) if self.last_failure else None,
            'failure_count': self.failure_count,
            'recent_failures': [dict(f) for f in self._recent_failures]}
        if self.factory is not None:
            try:
                out['store'] = self.factory.health()
                store = self.factory.open_store()
                out['ownership'] = ({'rows': ownership.count(store),
                                     'capacity': ownership.capacity_from(self.config)}
                                    if store is not None else {'rows': 0})
            except Exception as exc:
                self._failed('ownership_status', 'status_failed', exc)
                out['store'] = {'state': 'held', 'reason': 'status_failed'}
                out['ownership'] = {'rows': -1}
                out['state'] = 'degraded' if alive else out['state']
                out['reason'] = 'status_failed'
                out['last_failure'] = dict(self.last_failure) if self.last_failure else None
                out['failure_count'] = self.failure_count
                out['recent_failures'] = [dict(f) for f in self._recent_failures]
        return out


_supervisor: Supervisor | None = None
_install_lock = threading.Lock()
_client_owner: Supervisor | None = None
_client_owner_lock = threading.Lock()


def get() -> Supervisor | None:
    return _supervisor


def start_default(app: Any = None) -> Supervisor:
    """Server boot hook: install the read-only ownership reader, then start the
    supervisor if (and only if) its flag is on. Safe to call twice."""
    global _supervisor
    from mc import agent_mail_factory
    with _install_lock:
        if _supervisor is None:
            factory = agent_mail_factory.get()
            _supervisor = Supervisor(_default_recovery(factory, app), factory=factory,
                                     interval_s=_interval())
        ownership.install_reader(ownership.factory_reader(_supervisor.factory))
        sup = _supervisor
    sup.start()
    return sup


def stop_default() -> None:
    sup = _supervisor
    if sup is not None:
        sup.stop()


def _interval() -> float:
    value = state.CONFIG.get(INTERVAL_KEY)
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 5:
        return float(value)
    return DEFAULT_INTERVAL_S


def _default_recovery(factory: Any, app: Any) -> Recovery:
    """Inbound laundering/wake run only while the ingress flag is on; each call
    builds against the shared checked store and never creates one."""
    def _on() -> bool:
        return state.CONFIG.get('agent_mail_ingress_enabled') is True

    def launder() -> dict[str, str]:
        from mc.agent_mail_launder import MailLaunder
        return MailLaunder(factory.require_store(), enabled=_on()).process_next()

    def wake() -> dict[str, str]:
        from mc.agent_mail_wake import MailWake
        return MailWake(factory.require_store(), enabled=_on()).process_next(app)

    def wake_reconcile() -> None:
        from mc.agent_mail_wake import MailWake
        if _on():
            MailWake(factory.require_store(), enabled=True).reconcile()

    return Recovery(factory, launder=launder, wake=wake, wake_reconcile=wake_reconcile)
