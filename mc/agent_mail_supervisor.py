"""Server-owned agent-mail supervisor (MC-1166 unit 7d-1). Default OFF.

One daemon thread, started only when ``agent_mail_supervisor_enabled`` is True,
runs ``Recovery.run`` on a bounded interval. A pass that reports a held store or
a failed step backs off exponentially (capped); a clean pass resets it. Start and
stop are idempotent. A failing pass never kills the thread, and a live thread is
never reported as healthy on its own: ``status()['state']`` is ``ok`` only when
the LAST pass was clean.

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
from mc.agent_mail_recovery import Recovery
from mc.core import _log

FLAG = 'agent_mail_supervisor_enabled'
INTERVAL_KEY = 'agent_mail_supervisor_interval_s'
DEFAULT_INTERVAL_S = 60.0
MAX_BACKOFF_S = 900.0


class Supervisor:
    def __init__(self, recovery: Recovery, *, config: dict[str, Any] | None = None,
                 clock: Callable[[], float] = time.time, interval_s: float = DEFAULT_INTERVAL_S,
                 max_backoff_s: float = MAX_BACKOFF_S, factory: Any = None):
        self.recovery, self._config, self.clock = recovery, config, clock
        self.interval_s, self.max_backoff_s, self.factory = interval_s, max_backoff_s, factory
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_lock = threading.Lock()
        self.last_attempt: float | None = None
        self.last_success: float | None = None
        self.failures = 0
        self.report: dict[str, Any] = {}

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
            self.last_attempt = self.clock()
            try:
                report = self.recovery.run()
            except Exception as exc:  # defence in depth: run() already isolates steps
                _log(f'[agent-mail] supervisor pass failed: {type(exc).__name__}', flush=True)
                report = {'ok': False, 'steps': [{'name': 'supervisor', 'state': 'failed',
                                                  'reason': 'step_failed', 'count': 0}]}
            bad = any(s['state'] in ('failed', 'held') for s in report['steps'])
            if bad:
                self.failures += 1
            else:
                self.failures = 0
                self.last_success = self.last_attempt
            self.report = report
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
        with self._lock:
            if not self.enabled() or (self._thread is not None and self._thread.is_alive()):
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name='agent-mail-supervisor', daemon=True)
            self._thread.start()
            return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            if self.enabled():
                self.tick()
            if self._stop.wait(self.delay()):
                break

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            thread = self._thread
            self._stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        with self._lock:
            if thread is self._thread and (thread is None or not thread.is_alive()):
                self._thread = None

    # --- metadata for 7e ------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """Bounded metadata only: counts and closed codes."""
        alive = self._thread is not None and self._thread.is_alive()
        steps = self.report.get('steps', [])
        first_bad = next((s for s in steps if s['state'] not in ('ok', 'skipped')), None)
        if not alive and not self.enabled():
            overall = 'off'
        elif self.last_attempt is None:
            overall = 'starting' if alive else 'stopped'
        elif not alive and self.enabled():
            overall = 'dead'
        else:
            overall = 'ok' if self.report.get('ok') else 'degraded'
        out: dict[str, Any] = {
            'state': overall, 'thread_alive': alive, 'enabled': self.enabled(),
            'last_attempt': self.last_attempt, 'last_success': self.last_success,
            'consecutive_failures': self.failures, 'next_delay_s': self.delay(),
            'reason': first_bad['reason'] if first_bad else '', 'steps': steps,
            'holds': ownership.held(), 'retention': retention.snapshot()}
        if self.factory is not None:
            out['store'] = self.factory.health()
            try:
                store = self.factory.open_store()
                out['ownership'] = ({'rows': ownership.count(store),
                                     'capacity': ownership.capacity_from(self.config)}
                                    if store is not None else {'rows': 0})
            except Exception:
                out['ownership'] = {'rows': -1}
        return out


_supervisor: Supervisor | None = None
_install_lock = threading.Lock()


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
