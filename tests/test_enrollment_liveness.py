"""
Enrollment liveness — mc/blueprints/remote_routes.py.

Guards the 2026-09-08 outage: a KB5124008 servicing reboot emptied the whole
Windows Credential Manager vault, silently un-enrolling the device. Every
piece of down-alerting lived INSIDE tunnel_supervisor, and
tunnel_supervisor.start() *raises* when there's no enrolled identity — so
losing enrollment disabled the only code that would have reported it.
Nobody was told for ~8 hours.

These tests pin the loop that runs independently of the supervisor
(_enrollment_liveness_loop / _check_enrollment_liveness_once), and the
True->False-transition + KeystoreUnavailable-is-different-and-must-not-be-
conflated behaviour that makes it trustworthy instead of noisy.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import remote_routes as rr  # noqa: E402
from mc_remote import device_keys  # noqa: E402


@pytest.fixture
def liveness_path(tmp_path, monkeypatch):
    p = tmp_path / "remote_enrollment_liveness.json"
    monkeypatch.setattr(rr, "ENROLLMENT_LIVENESS_PATH", p)
    return p


@pytest.fixture(autouse=True)
def _no_real_mail(monkeypatch):
    """Safety net: if a test forgets to stub the sender, still never shell
    out to the real mailer."""
    monkeypatch.setattr(rr, "_send_enrollment_lost_alert", lambda *a, **k: None)


def _set_enrolled(monkeypatch, identity_or_none):
    monkeypatch.setattr(device_keys, "load_identity", lambda: identity_or_none)


def test_never_enrolled_device_never_alerts(liveness_path, monkeypatch):
    sent = []
    monkeypatch.setattr(rr, "_send_enrollment_lost_alert", lambda *a, **k: sent.append(a))
    _set_enrolled(monkeypatch, None)

    for _ in range(3):
        rr._check_enrollment_liveness_once()
    time.sleep(0.05)

    assert sent == [], "a device that never enrolled must never alert as 'lost'"


def test_transition_from_enrolled_to_lost_alerts(liveness_path, monkeypatch):
    sent = []
    monkeypatch.setattr(rr, "_send_enrollment_lost_alert", lambda *a, **k: sent.append(a))

    _set_enrolled(monkeypatch, object())
    rr._check_enrollment_liveness_once()
    assert sent == []

    _set_enrolled(monkeypatch, None)  # THE INCIDENT: identity is gone
    rr._check_enrollment_liveness_once()
    time.sleep(0.05)

    assert len(sent) == 1, "True->False transition did not alert"


def test_keystore_unavailable_does_not_look_like_lost_enrollment(liveness_path, monkeypatch):
    """error_code=tunnel_keystore_unavailable (a read failure) must never be
    conflated with enrolled=false/error_code=None (a genuine loss) — Bram's
    explicit distinction."""
    sent = []
    monkeypatch.setattr(rr, "_send_enrollment_lost_alert", lambda *a, **k: sent.append(a))

    _set_enrolled(monkeypatch, object())
    rr._check_enrollment_liveness_once()

    def _raise():
        raise device_keys.KeystoreUnavailable("locked")
    monkeypatch.setattr(device_keys, "load_identity", _raise)
    rr._check_enrollment_liveness_once()
    time.sleep(0.05)

    assert sent == [], "a keystore READ failure must not be treated as enrollment loss"


def test_state_survives_a_process_restart(liveness_path, monkeypatch):
    """THE INCIDENT restarted MC too (Windows rebooted the whole box). An
    in-memory-only baseline would see enrolled=False on the very first check
    of the new process and mistake 'just lost' for 'never enrolled' — never
    alerting for the one case this loop exists to catch."""
    sent = []
    monkeypatch.setattr(rr, "_send_enrollment_lost_alert", lambda *a, **k: sent.append(a))

    _set_enrolled(monkeypatch, object())
    rr._check_enrollment_liveness_once()  # baseline persisted to disk

    # Simulate a fresh process: nothing in memory carries over, only the
    # state file on disk (liveness_path is unchanged).
    _set_enrolled(monkeypatch, None)
    rr._check_enrollment_liveness_once()
    time.sleep(0.05)

    assert len(sent) == 1, "persisted baseline was not honored across a restart"


def test_permanent_loss_re_nags_after_cooldown(liveness_path, monkeypatch):
    sent = []
    monkeypatch.setattr(rr, "_send_enrollment_lost_alert", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(rr, "_ENROLLMENT_ALERT_COOLDOWN_S", 0.2)

    _set_enrolled(monkeypatch, object())
    rr._check_enrollment_liveness_once()
    _set_enrolled(monkeypatch, None)
    rr._check_enrollment_liveness_once()
    time.sleep(0.05)
    assert len(sent) == 1

    rr._check_enrollment_liveness_once()  # still inside the cooldown
    time.sleep(0.05)
    assert len(sent) == 1, "re-fired inside the cooldown window"

    time.sleep(0.25)  # cooldown lapses, identity is STILL lost
    rr._check_enrollment_liveness_once()
    time.sleep(0.05)
    assert len(sent) == 2, "did not re-nag once the cooldown lapsed for a still-lost identity"


def test_recovery_then_new_loss_alerts_again(liveness_path, monkeypatch):
    sent = []
    monkeypatch.setattr(rr, "_send_enrollment_lost_alert", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(rr, "_ENROLLMENT_ALERT_COOLDOWN_S", 0.2)

    _set_enrolled(monkeypatch, object())
    rr._check_enrollment_liveness_once()
    _set_enrolled(monkeypatch, None)
    rr._check_enrollment_liveness_once()
    time.sleep(0.05)
    assert len(sent) == 1

    _set_enrolled(monkeypatch, object())  # re-enrolled
    rr._check_enrollment_liveness_once()

    time.sleep(0.25)  # cooldown lapses while healthy
    _set_enrolled(monkeypatch, None)  # fresh loss
    rr._check_enrollment_liveness_once()
    time.sleep(0.05)

    assert len(sent) == 2, "a fresh loss after recovery must alert, not stay suppressed"
