"""Tunnel watchdog — recovery, honest status, and the down alert.

Guards the 2026-08-14 outage: remote access was down for an hour, nothing
restarted it, and nobody was told. The user only noticed because they had a
second way in — on a trip, that would have been days.

The bug was that the watchdog reacted to an EDGE (alive -> dead) starting from
`was_alive = None`. A tunnel that was already dead the first time it looked
recorded "dead" and never fired again. These tests pin the level-triggered
behaviour that replaced it, because the failure is completely silent: everything
keeps running, the loop keeps polling, and the status keeps looking plausible.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc_remote import tunnel_supervisor as ts  # noqa: E402


class FakeCloudflared:
    """Stands in for the cloudflared process handle."""

    def __init__(self, alive: bool):
        self._alive = alive
        self.last_error_value = None

    def is_alive(self) -> bool:
        return self._alive

    def last_error(self):
        return self.last_error_value


@pytest.fixture
def sup(monkeypatch):
    """A supervisor whose loops we drive by hand — no threads, no network."""
    s = ts.TunnelSupervisor()
    monkeypatch.setattr(ts, "_WATCHDOG_INTERVAL_S", 0.01)
    return s


def _run_watchdog_ticks(sup, n: int):
    """Run exactly n watchdog iterations, then cancel."""
    import threading

    def stopper():
        # Let the loop turn n times at ~10ms each, then cancel it.
        time.sleep(0.01 * (n + 2))
        sup._cancel.set()

    t = threading.Thread(target=stopper, daemon=True)
    t.start()
    sup._watchdog_loop()
    t.join(timeout=1)


# ── The regression itself ────────────────────────────────────────────────────

def test_already_dead_at_startup_still_triggers_recovery(sup, monkeypatch):
    """THE BUG. Dead before the watchdog ever saw it alive.

    The old edge-triggered loop needed a True->False transition, so this case —
    supervisor starts (or restarts) while cloudflared is already gone — never
    asked for a re-issue. It polled a corpse every 5s indefinitely.
    """
    monkeypatch.setattr(ts.cloudflared, "get", lambda: FakeCloudflared(alive=False))
    sup._wake_attest.clear()

    _run_watchdog_ticks(sup, 3)

    assert sup._wake_attest.is_set(), (
        "watchdog never asked the attest loop to re-issue — a tunnel that was "
        "already dead when the loop started is exactly the silent case"
    )
    assert sup._state.cloudflared_down_since is not None


def test_stays_armed_while_it_stays_down(sup, monkeypatch):
    """Recovery must keep being requested, not fire once and give up."""
    monkeypatch.setattr(ts.cloudflared, "get", lambda: FakeCloudflared(alive=False))
    _run_watchdog_ticks(sup, 2)
    first = sup._state.cloudflared_down_since
    assert first is not None

    # A consumer clears the flag (the attest loop does this when it wakes).
    sup._wake_attest.clear()
    sup._cancel.clear()
    _run_watchdog_ticks(sup, 2)

    assert sup._wake_attest.is_set(), "watchdog stopped re-arming while still down"
    assert sup._state.cloudflared_down_since == pytest.approx(first, abs=1.0), \
        "down_since restarted mid-outage — downtime would read as shorter than it is"


def test_recovery_clears_the_down_state(sup, monkeypatch):
    cf = FakeCloudflared(alive=False)
    monkeypatch.setattr(ts.cloudflared, "get", lambda: cf)
    _run_watchdog_ticks(sup, 2)
    assert sup._state.cloudflared_down_since is not None

    cf._alive = True
    sup._cancel.clear()
    _run_watchdog_ticks(sup, 2)
    assert sup._state.cloudflared_down_since is None
    assert sup._state.down_alert_sent_at is None


def test_healthy_tunnel_never_asks_for_recovery(sup, monkeypatch):
    monkeypatch.setattr(ts.cloudflared, "get", lambda: FakeCloudflared(alive=True))
    sup._wake_attest.clear()
    _run_watchdog_ticks(sup, 3)
    assert not sup._wake_attest.is_set()
    assert sup._state.cloudflared_down_since is None


# ── Honest status ────────────────────────────────────────────────────────────

def test_status_reports_downtime_not_a_stale_healthy_timestamp(sup, monkeypatch):
    """`last_seen` is the last GOOD attestation, so it keeps looking healthy
    while the tunnel is dead. `down_seconds` is the field that tells the truth,
    and `online` must be False regardless of how good the attestation looked."""
    import datetime as dt

    now = dt.datetime.now(dt.timezone.utc)
    ok = ts.attestation.AttestationOk(
        tunnel_token="t", tunnel_token_id="tid", tunnel_token_expires_at=now,
        next_attestation_after=now, caps={}, directives=[], received_at=now,
    )
    sup._state.last_attestation = ok
    sup._state.cloudflared_down_since = time.time() - 3600
    monkeypatch.setattr(ts.cloudflared, "get", lambda: FakeCloudflared(alive=False))

    st = sup.status()
    assert st["online"] is False, "attestation looked fine, but the tunnel is dead"
    assert st["cloudflared_alive"] is False
    assert st["down_seconds"] >= 3500, f"downtime not surfaced: {st['down_seconds']}"
    assert st["error_code"] == "tunnel_cloudflared_down"
    assert st["last_seen"] is not None, "last_seen still reads healthy — the trap"


def test_status_has_no_downtime_while_up(sup, monkeypatch):
    monkeypatch.setattr(ts.cloudflared, "get", lambda: FakeCloudflared(alive=True))
    assert sup.status()["down_seconds"] is None


# ── Down alert ───────────────────────────────────────────────────────────────

def test_no_alert_before_the_threshold(sup, monkeypatch):
    sent = []
    monkeypatch.setattr(sup, "_send_down_alert", lambda *a, **k: sent.append(a))
    sup._maybe_alert_down(time.time() - 30)     # 30s down, threshold is 600s
    time.sleep(0.05)
    assert sent == []


def test_alert_fires_once_per_outage(sup, monkeypatch):
    sent = []
    monkeypatch.setattr(sup, "_send_down_alert", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(ts, "_DOWN_ALERT_AFTER_S", 1.0)
    down_since = time.time() - 60

    for _ in range(5):
        sup._maybe_alert_down(down_since)
    time.sleep(0.2)

    assert len(sent) == 1, (
        f"expected exactly one mail for one outage, got {len(sent)} — an alert "
        f"every watchdog tick is one the user learns to ignore"
    )


def test_next_outage_is_gated_by_the_cooldown(sup, monkeypatch):
    """A tunnel flapping just past the threshold must not mail every cycle."""
    sent = []
    monkeypatch.setattr(sup, "_send_down_alert", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(ts, "_DOWN_ALERT_AFTER_S", 1.0)

    sup._maybe_alert_down(time.time() - 60)
    time.sleep(0.15)
    assert len(sent) == 1

    # Tunnel recovers, then dies again immediately.
    sup._state.down_alert_sent_at = None
    sup._maybe_alert_down(time.time() - 60)
    time.sleep(0.15)
    assert len(sent) == 1, "second outage inside the cooldown still mailed"

    # ...but once the cooldown lapses, a genuine new outage does alert.
    sup._last_alert_wall = time.time() - (ts._DOWN_ALERT_COOLDOWN_S + 1)
    sup._maybe_alert_down(time.time() - 60)
    time.sleep(0.15)
    assert len(sent) == 2, "cooldown expired but the alert stayed suppressed"


def test_alert_body_is_actionable_and_never_raises(sup):
    """The mail must survive a machine with no mailer configured, and must tell
    someone reading it on a phone what to actually do."""
    captured = {}

    class FakeRun:
        def __call__(self, cmd, **kw):
            captured['cmd'] = cmd
            return None

    import subprocess as sp
    orig = sp.run
    sp.run = FakeRun()
    try:
        sup._send_down_alert(3600, "ronl.clayrune.io")
    finally:
        sp.run = orig

    cmd = captured.get('cmd')
    if cmd:  # only asserted when the mailer script exists in this checkout
        joined = " ".join(cmd)
        assert "--subject" in joined and "--body" in joined
        body = cmd[cmd.index("--body") + 1]
        assert "ronl.clayrune.io" in body
        assert "Reconnect" in body, "alert should say how to fix it"
