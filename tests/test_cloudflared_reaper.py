"""Orphaned-connector reaping for the bundled cloudflared.

The explicit teardown in `system_routes._graceful_stop_all()` only runs on
/api/system/restart. Any other exit — crash, Task-Manager kill, power cut —
left cloudflared running, and nothing ever reaped it: 24 connectors on one
tunnel had accumulated by 2026-08-08 (910 MB, 96 live QUIC sessions to CF's
edge), so Cloudflare was load-balancing real traffic across 23 dead servers'
leftovers.

The dangerous half of the fix is the PID ledger: killing by remembered PID is
only safe if a recycled PID can never be killed. That is what most of this
file tests.
"""
import json

import pytest

from mc_remote import cloudflared as cf


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    p = tmp_path / 'cloudflared_pids.json'
    monkeypatch.setattr(cf, '_PID_LEDGER', p)
    return p


def test_ledger_roundtrip_dedupes_and_sorts(ledger):
    cf._ledger_write([333, 111, 222, 222])
    assert cf._ledger_read() == [111, 222, 333]
    cf._ledger_add(444)
    assert cf._ledger_read() == [111, 222, 333, 444]
    cf._ledger_drop(222)
    assert cf._ledger_read() == [111, 333, 444]


def test_ledger_read_survives_missing_and_corrupt_file(ledger):
    assert cf._ledger_read() == []          # never written
    ledger.write_text('not json{{')
    assert cf._ledger_read() == []          # corrupt → empty, not a crash
    ledger.write_text('{"pid": 1}')
    assert cf._ledger_read() == []          # wrong shape → empty


def test_reap_never_kills_a_recycled_pid(ledger, monkeypatch):
    """THE load-bearing guarantee: a ledger PID the OS has since handed to
    another program must be dropped, never killed."""
    cf._ledger_write([4242])
    monkeypatch.setattr(cf, '_is_cloudflared_pid', lambda pid: False)

    def _boom(pid):
        raise AssertionError(f'killed a non-cloudflared pid: {pid}')

    monkeypatch.setattr(cf, '_kill_pid', _boom)

    assert cf.reap_orphans() == 0
    assert cf._ledger_read() == []          # stale entry pruned


def test_reap_kills_recorded_connectors_and_prunes(ledger, monkeypatch):
    cf._ledger_write([11, 22, 33])
    monkeypatch.setattr(cf, '_is_cloudflared_pid', lambda pid: pid in (11, 22, 33))
    killed = []
    monkeypatch.setattr(cf, '_kill_pid', lambda pid: (killed.append(pid), True)[1])

    assert cf.reap_orphans() == 3
    assert sorted(killed) == [11, 22, 33]
    assert cf._ledger_read() == []


def test_reap_keeps_our_own_connector(ledger, monkeypatch):
    """start() reaps BEFORE spawning, but swap_token()/restart paths must be
    able to spare the connector they still own."""
    cf._ledger_write([11, 99])
    monkeypatch.setattr(cf, '_is_cloudflared_pid', lambda pid: True)
    killed = []
    monkeypatch.setattr(cf, '_kill_pid', lambda pid: (killed.append(pid), True)[1])

    assert cf.reap_orphans(keep_pid=99) == 1
    assert killed == [11]
    assert cf._ledger_read() == [99]        # kept, and still tracked


def test_reap_retains_pids_it_failed_to_kill(ledger, monkeypatch):
    """A kill that fails must stay in the ledger so the next start retries —
    dropping it would leak the connector permanently."""
    cf._ledger_write([11])
    monkeypatch.setattr(cf, '_is_cloudflared_pid', lambda pid: True)
    monkeypatch.setattr(cf, '_kill_pid', lambda pid: False)

    assert cf.reap_orphans() == 0
    assert cf._ledger_read() == [11]


def test_is_cloudflared_pid_rejects_bad_pids():
    assert cf._is_cloudflared_pid(0) is False
    assert cf._is_cloudflared_pid(-1) is False
    assert cf._is_cloudflared_pid(999_999_999) is False


def test_ledger_write_is_atomic(ledger):
    """Written via a temp file + os.replace, so a crash mid-write can't leave a
    truncated ledger that reads as 'no orphans'."""
    cf._ledger_write([1, 2, 3])
    assert json.loads(ledger.read_text()) == [1, 2, 3]
    assert not ledger.with_suffix('.tmp').exists()
