"""One schedule fire must dispatch exactly once (MC-909).

MEASURED 2026-08-30: schedule 99f1ae73 ([Position review], weekly) fired once
at 11:00:22Z and produced TWO full sweeps 112ms apart — sessions 39c29aaf0ba0
(.133814Z) and a9c1f5f6f634 (.245999Z). Neither knew about the other; they
raced on the position-review sidecar and cleared a flag that was already
waiting on a human (MC-910).

The second dispatcher was a second Clayrune instance, which the port guard let
boot beside the live one (MC-908, fixed separately). _load_schedules /
_save_schedules are an unlocked read-modify-write on one JSON file, so both
instances read the same due `next_run` and both fired. These tests pin the
claim layer that makes that impossible even with two schedulers running.
"""
import multiprocessing
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc.blueprints import scheduler_routes as sr


@pytest.fixture
def claims_dir(tmp_path, monkeypatch):
    d = tmp_path / '.schedule_claims'
    monkeypatch.setattr(sr, 'CLAIMS_DIR', d)
    return d


def test_first_claim_wins_second_loses(claims_dir):
    """The exact 2026-08-30 shape: same schedule, same slot, twice."""
    slot = '2026-08-30T11:00:00Z'
    assert sr._claim_slot('99f1ae73', slot) is True
    assert sr._claim_slot('99f1ae73', slot) is False


def test_a_later_slot_of_the_same_schedule_still_fires(claims_dir):
    """The guard must stop duplicates, not the next legitimate cadence tick."""
    assert sr._claim_slot('99f1ae73', '2026-08-30T11:00:00Z') is True
    assert sr._claim_slot('99f1ae73', '2026-09-06T11:00:00Z') is True


def test_different_schedules_do_not_block_each_other(claims_dir):
    slot = '2026-08-30T11:00:00Z'
    assert sr._claim_slot('99f1ae73', slot) is True
    assert sr._claim_slot('deadbeef', slot) is True


def test_claim_fails_open_when_the_dir_is_unusable(tmp_path, monkeypatch):
    """A missed scheduled run is worse than the rare duplicate this guards
    against, so an unwritable claims dir must dispatch, not skip."""
    blocker = tmp_path / 'not-a-dir'
    blocker.write_text('x')                      # mkdir() over a file raises
    monkeypatch.setattr(sr, 'CLAIMS_DIR', blocker / 'claims')
    assert sr._claim_slot('99f1ae73', '2026-08-30T11:00:00Z') is True


def test_missing_id_or_slot_never_blocks(claims_dir):
    assert sr._claim_slot('', '2026-08-30T11:00:00Z') is True
    assert sr._claim_slot('99f1ae73', '') is True


def _worker(args):
    """Runs in a SEPARATE PROCESS — the actual failure mode."""
    claims, sched_id, slot = args
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from mc.blueprints import scheduler_routes as _sr
    _sr.CLAIMS_DIR = Path(claims)
    return _sr._claim_slot(sched_id, slot)


@pytest.mark.skipif(sys.platform == 'darwin', reason='spawn start-method cost')
def test_two_processes_racing_one_slot_yield_exactly_one_dispatch(tmp_path):
    """Two live instances, one slot. O_CREAT|O_EXCL is a single atomic syscall,
    so this holds across processes — which a lock in one process would not."""
    claims = str(tmp_path / '.schedule_claims')
    args = [(claims, '99f1ae73', '2026-08-30T11:00:00Z')] * 8
    with multiprocessing.Pool(4) as pool:
        results = pool.map(_worker, args)
    assert sum(bool(r) for r in results) == 1, results


def test_prune_drops_old_markers_but_keeps_recent(claims_dir):
    import time
    claims_dir.mkdir(parents=True, exist_ok=True)
    old, new = claims_dir / 'old.claim', claims_dir / 'new.claim'
    old.write_text('x'); new.write_text('x')
    stale = time.time() - 30 * 86400
    os.utime(old, (stale, stale))
    sr._prune_claims(keep_days=7)
    assert not old.exists()
    assert new.exists()
