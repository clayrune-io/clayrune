"""P76 (MC-1179): Disable during an in-flight attestation must leave no tunnel up.

`stop()` set the cancel flag and stopped the connector, but the attestation
thread only looked at the flag at the top of its loop. A request already in
flight when the user clicked Disable came back, called `swap_token` (which
starts cloudflared) and only then noticed the cancel. The dashboard said off
while the public hostname kept serving. No real tunnel and no control-plane
call here: attest_once and cloudflared are fakes.
"""
from __future__ import annotations

import datetime as _dt
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc_remote import attestation  # noqa: E402
from mc_remote import tunnel_supervisor as ts  # noqa: E402


class FakeConnector:
    def __init__(self):
        self.alive = False
        self.starts = 0
        self.stops = 0
        self._lock = threading.Lock()

    def swap_token(self, token):
        with self._lock:
            self.alive = True
            self.starts += 1

    def stop(self, *, timeout=3.0):
        with self._lock:
            self.alive = False
            self.stops += 1

    def is_alive(self):
        return self.alive

    def last_error(self):
        return None


def _ok():
    now = _dt.datetime.now(_dt.timezone.utc)
    return attestation.AttestationOk(
        tunnel_token="fake-token", tunnel_token_id="tid",
        tunnel_token_expires_at=now + _dt.timedelta(seconds=900),
        next_attestation_after=now + _dt.timedelta(seconds=600),
        caps=None, directives=[], received_at=now,
    )


def test_stop_during_attestation_leaves_connector_down(monkeypatch):
    conn = FakeConnector()
    in_flight = threading.Event()
    release = threading.Event()

    def slow_attest(*, session, cp_base_url):
        in_flight.set()
        release.wait(timeout=5)
        return _ok()

    monkeypatch.setattr(ts.cloudflared, "get", lambda: conn)
    monkeypatch.setattr(ts.attestation, "attest_once", slow_attest)
    monkeypatch.setattr(ts.device_keys, "load_identity", lambda: object())
    monkeypatch.setattr(ts, "_WATCHDOG_INTERVAL_S", 0.01)

    sup = ts.TunnelSupervisor()
    sup.start(cp_base_url="http://unused.invalid")
    assert in_flight.wait(timeout=2), "attestation never started"

    stopper = threading.Thread(target=sup.stop, kwargs={"timeout": 5}, daemon=True)
    stopper.start()
    time.sleep(0.1)          # stop() has set cancel and stopped the (idle) connector
    release.set()            # the in-flight attestation now comes back OK
    stopper.join(timeout=8)
    assert not stopper.is_alive(), "stop() did not return"

    assert not sup.is_running()
    assert conn.alive is False, (
        "supervisor reports off but the connector was started by the "
        "in-flight attestation and left running"
    )
