"""
tunnel_supervisor — Background attestation + cloudflared lifecycle.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Owns two concurrent loops:

    Attestation loop  (slow, default 10 min)
        ─ Calls /v1/nonce + /v1/attest
        ─ On success: starts/swaps cloudflared with the issued tunnel token
        ─ On failure: exponential backoff, online=false

    Watchdog loop     (fast, default 5s)
        ─ Polls cloudflared.is_alive()
        ─ Surfaces crashes immediately so the Settings panel reflects them
        ─ Triggers an out-of-band attestation retry if cloudflared dies

`online` semantics: True iff the last attestation succeeded AND cloudflared
is currently alive. Either condition flipping false drops `online`.

cloudflared in MC_REMOTE_LOCAL_MOCK mode is a no-op stub (see cloudflared.py)
so the full lifecycle can be exercised without a real binary or CF account.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os as _os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from . import attestation, cloudflared, config, device_keys

log = logging.getLogger(__name__)


_DEFAULT_INTERVAL_S = float(_os.environ.get("MC_REMOTE_ATTEST_INTERVAL_S", "600"))
_WATCHDOG_INTERVAL_S = float(_os.environ.get("MC_REMOTE_WATCHDOG_S", "5"))
_BACKOFF_MIN_S = 5.0
_BACKOFF_MAX_S = 60.0

# ── Down alert ───────────────────────────────────────────────────────────────
# Remote access dying is invisible by design: the phone just stops working, and
# whoever would notice is usually the person who is away. On 2026-08-14 it was
# down for an hour and the only reason it got fixed was a second way in.
#
# So: after _DOWN_ALERT_AFTER_S of continuous downtime, send one mail. The
# cooldown is deliberately long — an alert that arrives every ten minutes is one
# the user learns to swipe away, which is the same failure as no alert at all.
_DOWN_ALERT_AFTER_S = float(_os.environ.get("MC_REMOTE_DOWN_ALERT_S", "600"))
_DOWN_ALERT_COOLDOWN_S = float(_os.environ.get("MC_REMOTE_ALERT_COOLDOWN_S", "21600"))


@dataclass
class SupervisorState:
    running: bool = False
    last_attestation: Optional[attestation.AttestationResult] = None
    started_at: Optional[_dt.datetime] = None
    stopping: bool = False
    # Wall-clock (time.time()) of the first watchdog poll that found cloudflared
    # dead, cleared when it comes back. Drives both the honest `online` in
    # status() and the "remote access has been down for N minutes" alert — the
    # attestation timestamp cannot do that job, because a dead tunnel leaves the
    # LAST GOOD attestation sitting there looking healthy.
    cloudflared_down_since: Optional[float] = None
    # Set when the down-alert fires, cleared when the tunnel returns — so one
    # outage produces one mail no matter how long it lasts.
    down_alert_sent_at: Optional[float] = None


class TunnelSupervisor:
    """Single supervisor instance per MC process."""

    def __init__(self) -> None:
        self._state = SupervisorState()
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._wake_attest = threading.Event()  # forces an out-of-band attest
        self._attest_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._session: Optional[requests.Session] = None
        self._cp_base_url: Optional[str] = None
        # Cooldown clock is per-supervisor, not per-outage: a tunnel flapping
        # every 11 minutes must not mail on every cycle.
        self._last_alert_wall: Optional[float] = None

    # ─── Lifecycle ────────────────────────────────────────────────────────

    def start(self, *, cp_base_url: Optional[str] = None) -> None:
        """Spawn the attestation + watchdog threads. Idempotent."""
        with self._lock:
            if self._state.running:
                return
            if device_keys.load_identity() is None:
                raise RuntimeError("Cannot start supervisor: no enrolled identity in keystore")
            self._cp_base_url = cp_base_url
            self._cancel.clear()
            self._wake_attest.clear()
            self._session = requests.Session()
            self._state = SupervisorState(
                running=True,
                started_at=_dt.datetime.now(_dt.timezone.utc),
            )
            self._attest_thread = threading.Thread(
                target=self._attest_loop, name="mc-tunnel-attest", daemon=True,
            )
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop, name="mc-tunnel-watchdog", daemon=True,
            )
            self._attest_thread.start()
            self._watchdog_thread.start()
            log.info("tunnel supervisor started (cp=%s)",
                     cp_base_url or config.control_plane_base_url())

    def stop(self, *, timeout: float = 5.0) -> None:
        """Signal cancel, stop cloudflared, wait for threads to exit."""
        with self._lock:
            if not self._state.running:
                return
            self._state.stopping = True
        self._cancel.set()
        self._wake_attest.set()
        # Stop cloudflared OUTSIDE the lock — it may take a couple of seconds
        try:
            cloudflared.get().stop()
        except Exception as e:
            log.warning("cloudflared stop raised during supervisor.stop(): %s", e)
        for t in (self._attest_thread, self._watchdog_thread):
            if t is not None:
                t.join(timeout=timeout)
        with self._lock:
            self._state = SupervisorState()
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
                self._session = None
            self._attest_thread = None
            self._watchdog_thread = None
        log.info("tunnel supervisor stopped")

    def is_running(self) -> bool:
        with self._lock:
            return self._state.running

    def kick(self) -> None:
        """Force an out-of-band attestation on the next loop turn.

        The attest loop re-issues the tunnel token and (re)starts cloudflared on
        every successful attestation, so waking it is the repair primitive. Used
        by ensure_connected() and by the watchdog.
        """
        self._wake_attest.set()

    # ─── Status ───────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Snapshot for the Settings panel. Cheap; no I/O."""
        with self._lock:
            s = self._state
            last = s.last_attestation
            running = s.running
            down_since = s.cloudflared_down_since

        cf = cloudflared.get()
        cf_alive = False
        cf_err: Optional[str] = None
        try:
            cf_alive = cf.is_alive()
            cf_err = cf.last_error()
        except Exception as e:
            cf_err = f"cloudflared status error: {e}"

        # Online iff last attestation OK AND cloudflared currently up
        online = bool(isinstance(last, attestation.AttestationOk) and cf_alive)

        # How long the tunnel has been down, so callers don't have to infer it
        # from `last_seen` — which is the last GOOD attestation and therefore
        # keeps looking healthy for as long as the tunnel stays dead.
        down_seconds = None
        if not cf_alive and down_since is not None:
            down_seconds = max(0, int(time.time() - down_since))

        out: dict = {
            "running": running,
            "online": online,
            "cloudflared_alive": cf_alive,
            "down_seconds": down_seconds,
            "last_seen": None,
            "error_code": None,
            "error_message": None,
            "caps": None,
        }
        if isinstance(last, attestation.AttestationOk):
            out["last_seen"] = last.received_at.isoformat(timespec="seconds").replace("+00:00", "Z")
            out["caps"] = last.caps
            # If attestation OK but cloudflared down, surface that to UI
            if not cf_alive:
                out["error_code"] = "tunnel_cloudflared_down"
                out["error_message"] = (
                    cf_err or "Tunnel daemon stopped responding. Reconnecting…"
                )
        elif isinstance(last, attestation.AttestationFailure):
            out["error_code"] = last.code
            out["error_message"] = last.message
            out["last_seen"] = last.received_at.isoformat(timespec="seconds").replace("+00:00", "Z")
        return out

    # ─── Attestation loop ─────────────────────────────────────────────────

    def _attest_loop(self) -> None:
        backoff = _BACKOFF_MIN_S
        while not self._cancel.is_set():
            try:
                result = attestation.attest_once(
                    session=self._session,                 # type: ignore[arg-type]
                    cp_base_url=self._cp_base_url,
                )
            except Exception as e:
                log.exception("attestation crashed: %s", e)
                with self._lock:
                    self._state.last_attestation = attestation.AttestationFailure(
                        code="internal_error", message=str(e), http_status=0,
                        received_at=_dt.datetime.now(_dt.timezone.utc),
                    )
                self._wait_for_next(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_S)
                continue

            with self._lock:
                self._state.last_attestation = result

            if isinstance(result, attestation.AttestationOk):
                # Hand the issued tunnel token to cloudflared. start() is
                # idempotent: same token = no-op, different = restart.
                try:
                    cloudflared.get().swap_token(result.tunnel_token)
                except cloudflared.CloudflaredError as e:
                    log.error("cloudflared could not start: %s", e)
                    # We have a valid attestation, but cloudflared is unusable.
                    # status() will surface tunnel_cloudflared_down via the
                    # cf_alive=False check; backoff and retry.
                    self._wait_for_next(_BACKOFF_MIN_S)
                    continue

                backoff = _BACKOFF_MIN_S

                # Honor server-issued directives (force_logout, etc.)
                if self._handle_directives(result.directives):
                    return

                delta = (result.next_attestation_after - _dt.datetime.now(_dt.timezone.utc)).total_seconds()
                sleep_for = max(5.0, min(delta, _DEFAULT_INTERVAL_S))
            else:
                sleep_for = backoff
                backoff = min(backoff * 2, _BACKOFF_MAX_S)

            self._wait_for_next(sleep_for)

    # ─── Watchdog loop ────────────────────────────────────────────────────

    def _watchdog_loop(self) -> None:
        """Poll cloudflared health between attestations.

        Whenever cloudflared is NOT alive, wake the attestation thread for an
        out-of-band retry (which re-issues start() on the next OK).

        DOWN IS A STATE, NOT AN EDGE. This used to fire only on a True→False
        transition, starting from `was_alive = None`. So a tunnel that was
        already dead the first time the watchdog looked recorded "dead" and
        never fired again — it polled a corpse every 5s, forever, while
        `status()` reported the stale attestation and the user saw nothing.
        That is exactly how remote access stayed down for an hour on
        2026-08-14 with no recovery and no signal; a supervisor restart, or a
        crash during a re-issue, is enough to land in it.

        Re-arming on a level rather than an edge means the retry keeps being
        requested until it works. `_wake_attest` only shortcuts a sleep, and
        the attest loop has its own backoff, so a persistently-dead tunnel
        costs one attestation per backoff interval, not one per poll.
        """
        down_since: Optional[float] = None
        while not self._cancel.is_set():
            self._cancel.wait(timeout=_WATCHDOG_INTERVAL_S)
            if self._cancel.is_set():
                return
            try:
                alive = cloudflared.get().is_alive()
            except Exception:
                alive = False

            if alive:
                if down_since is not None:
                    log.info("cloudflared is back after %.0fs down", time.time() - down_since)
                down_since = None
                with self._lock:
                    self._state.cloudflared_down_since = None
                    self._state.down_alert_sent_at = None
                continue

            now = time.time()
            if down_since is None:
                down_since = now
                log.warning("cloudflared is not alive; asking attest loop to re-issue")
                with self._lock:
                    self._state.cloudflared_down_since = now
            self._wake_attest.set()
            self._maybe_alert_down(down_since)

    # ─── Down alert ───────────────────────────────────────────────────────

    def _send_down_alert(self, down_seconds: float, hostname: str = "") -> None:
        """Mail the operator that remote access has been down for a while.

        Shells out to the existing `tools/night-review/send_mail.py` rather
        than adding a second SMTP path — one mailer, one place credentials are
        read from (`~/.clayrune/night-mail.json`). Best-effort in every sense:
        an install with no mail configured just logs and moves on, and the
        subprocess is bounded so a hung SMTP can never wedge the watchdog.
        """
        mins = int(down_seconds // 60)
        host = hostname or "this machine"
        subject = f"[Clayrune] Remote access DOWN for {mins} min"
        body = (
            f"Clayrune's tunnel to {host} has been down for about {mins} minutes.\n"
            f"Remote access (phone / browser away from home) is not working.\n\n"
            f"Clayrune itself is still running and is retrying automatically every\n"
            f"few seconds — most outages recover without you doing anything.\n\n"
            f"If it is still down when you read this:\n"
            f"  1. Open Clayrune on the machine itself.\n"
            f"  2. Settings -> Remote Access, then Reconnect.\n"
            f"  3. If that fails, restart Clayrune.\n\n"
            f"You are getting this once; it will not repeat for "
            f"{int(_DOWN_ALERT_COOLDOWN_S // 3600)}h even if the outage continues.\n"
        )
        try:
            repo_root = Path(__file__).resolve().parent.parent
            mailer = repo_root / "tools" / "night-review" / "send_mail.py"
            if not mailer.exists():
                log.warning("down alert: mailer not found at %s", mailer)
                return
            subprocess.run(
                [sys.executable, str(mailer), "--subject", subject, "--body", body],
                capture_output=True, timeout=60, check=False,
            )
            log.warning("remote access down %dm — alert sent", mins)
        except Exception as e:
            log.warning("down alert could not be sent: %s", e)

    def _maybe_alert_down(self, down_since: float) -> None:
        """Fire the alert once per outage, subject to the cooldown."""
        down_for = time.time() - down_since
        if down_for < _DOWN_ALERT_AFTER_S:
            return
        with self._lock:
            if self._state.down_alert_sent_at is not None:
                return                      # already alerted for THIS outage
            last = self._last_alert_wall
            if last is not None and (time.time() - last) < _DOWN_ALERT_COOLDOWN_S:
                return                      # inside the quiet period
            self._state.down_alert_sent_at = time.time()
            self._last_alert_wall = time.time()
        hostname = ""
        try:
            identity = device_keys.load_identity()
            hostname = getattr(identity, "hostname", "") or ""
        except Exception:
            pass
        # Off-thread so a slow SMTP never delays the recovery polling that is
        # the whole point of this loop.
        threading.Thread(target=self._send_down_alert, args=(down_for, hostname),
                         daemon=True, name="mc-remote-down-alert").start()

    def _wait_for_next(self, seconds: float) -> None:
        """Cancel-aware sleep; also returns early if watchdog wakes us."""
        # Wait on either cancel or wake_attest
        end = time.time() + seconds
        while not self._cancel.is_set():
            remaining = end - time.time()
            if remaining <= 0:
                return
            if self._wake_attest.wait(timeout=min(remaining, 1.0)):
                self._wake_attest.clear()
                return

    def _handle_directives(self, directives: list[dict]) -> bool:
        """Return True if a terminal directive was processed and the loop should exit."""
        for d in directives:
            t = d.get("type")
            if t in ("force_logout", "update_required"):
                log.warning("server requested %s; supervisor exiting", t)
                self._cancel.set()
                try:
                    cloudflared.get().stop()
                except Exception:
                    pass
                return True
            # Other directives observed but non-terminal.
        return False


# ─── Singleton accessor ──────────────────────────────────────────────────────

_supervisor: Optional[TunnelSupervisor] = None
_supervisor_lock = threading.Lock()


def get() -> TunnelSupervisor:
    global _supervisor
    with _supervisor_lock:
        if _supervisor is None:
            _supervisor = TunnelSupervisor()
        return _supervisor


def maybe_start(*, cp_base_url: Optional[str] = None) -> bool:
    """Start the supervisor IF an identity is enrolled. Returns True if started.

    Safe to call repeatedly — idempotent.

    NOTE: returns False when the supervisor is ALREADY running, which says
    nothing about whether the tunnel actually works. Callers trying to *repair*
    a broken tunnel want `ensure_connected()` instead — see the note there.
    """
    if device_keys.load_identity() is None:
        return False
    sup = get()
    if sup.is_running():
        return False
    sup.start(cp_base_url=cp_base_url)
    return True


def ensure_connected(*, cp_base_url: Optional[str] = None) -> bool:
    """Make the tunnel work, whatever state it is in. Returns True if anything
    was done.

    `maybe_start()` alone is NOT a repair path: it returns early when the
    supervisor object is running, and the supervisor keeps running perfectly
    happily while cloudflared underneath it is dead. That is the exact state
    observed on 2026-08-15 — supervisor alive, cloudflared gone, and the user's
    "Reconnect" button doing nothing at all because it bottomed out in
    maybe_start(). The recovery control has to check the thing that actually
    carries traffic, not the thing that supervises it.

    Three cases:
      - not running          → start it
      - running, tunnel up   → nothing to do
      - running, tunnel down → force an out-of-band attestation, which re-issues
                               the token and restarts cloudflared
    """
    if device_keys.load_identity() is None:
        return False
    sup = get()
    if not sup.is_running():
        sup.start(cp_base_url=cp_base_url)
        return True
    try:
        alive = cloudflared.get().is_alive()
    except Exception:
        alive = False
    if alive:
        return False
    log.warning("ensure_connected: supervisor up but cloudflared is dead — forcing re-issue")
    sup.kick()
    return True
