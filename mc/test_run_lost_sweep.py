"""MC-1179 SF29: finish, as `lost`, runs whose owner session went away.

A run reaches a terminal state only through ``TestRunStore.finish``. When the
owner's session ended, its tool call was interrupted, or the server restarted
mid-run, no caller ever finishes it and the Floor counted it as active for good.

This module is the fourth writer of ``lost``. It never edits a record itself:
every close goes through ``store.finish``, one run at a time, so the store lock
is held per run exactly as for a normal finish. A terminal run is never
touched, and an event that arrives for a run already finished as ``lost`` is
handled by the store as for any terminal run (ignored, record unchanged).

Only runs with a named owner that is no longer a live session of the same
project are swept. An ownerless run (no owner was ever recorded) is project
work by the Floor's own definition and is left alone.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Mapping
from typing import Any

from mc.core import _log
from mc.test_run_store import TERMINAL_STATES, TestRunStore

# A non-terminal run whose owner is gone and which has recorded nothing for this
# long is finished as lost (Dave's call, 2026-11-17: 60 minutes).
LOST_AFTER_SECONDS = 60 * 60
# The Floor poll runs every 5 s; the sweep runs at most this often per project.
SWEEP_INTERVAL_SECONDS = 60

_now = time.time
_guard = threading.Lock()
_last_sweep: dict[tuple[str, str], float] = {}


def owner_unavailable(run: Mapping[str, Any], project_id: str,
                      sessions: Mapping[str, Mapping[str, Any]]) -> bool:
    """A named owner that is not a live session of this project.

    The Floor's classification and the sweep's must agree, so both use this.
    """
    sid = run['owner_session_id']
    if sid is None:
        return False
    owner = sessions.get(sid)
    return owner is None or owner.get('project_id') != project_id or owner.get('session_id') != sid


def sweep_snapshots(store: TestRunStore, project_id: str, snapshots: Iterable[Mapping[str, Any]],
                    sessions: Mapping[str, Mapping[str, Any]], now: float) -> set[str]:
    """Finish eligible runs among ``snapshots``; return the run ids closed."""
    lost: set[str] = set()
    for run in snapshots:
        if (run['project_id'] != project_id or run['state'] in TERMINAL_STATES
                or not owner_unavailable(run, project_id, sessions)
                or now - run['updated_at'] < LOST_AFTER_SECONDS):
            continue
        try:
            store.finish(project_id, run['run_id'], state='lost', observed_at=now)
        except Exception as exc:
            _log(f'[test-run-sweep] could not finish {run["run_id"]} ({project_id}): {exc}', flush=True)
            continue
        lost.add(run['run_id'])
    return lost


def _due(store: TestRunStore, project_id: str, now: float) -> bool:
    key = (str(store.root), project_id)
    with _guard:
        last = _last_sweep.get(key)
        # A clock that went backwards must not stall the sweep.
        if last is not None and 0 <= now - last < SWEEP_INTERVAL_SECONDS:
            return False
        _last_sweep[key] = now
        return True


def maybe_sweep(store: TestRunStore, project_id: str, snapshots: Iterable[dict[str, Any]],
                sessions: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Floor-read hook: sweep at most once per window, on the snapshots the read
    already holds, so the poll never walks the project's files a second time.
    Returns ``snapshots`` without the runs it closed."""
    snapshots = list(snapshots)  # read once: the caller may hand a one-pass iterable
    now = _now()
    if not _due(store, project_id, now):
        return snapshots
    lost = sweep_snapshots(store, project_id, snapshots, sessions, now)
    return [s for s in snapshots if s['run_id'] not in lost] if lost else snapshots


def startup_sweep(store: TestRunStore, projects: Iterable[Mapping[str, Any]],
                  sessions: Mapping[str, Mapping[str, Any]]) -> int:
    """Sweep every project once. Returns the number of runs finished as lost."""
    total = 0
    for room in projects:
        pid = room.get('id')
        if not pid or pid == '_incognito' or room.get('_is_incognito_project'):
            continue
        try:
            now = _now()
            _due(store, pid, now)
            total += len(sweep_snapshots(store, pid, store.active_snapshots(pid), sessions, now))
        except Exception as exc:
            _log(f'[test-run-sweep] startup sweep failed ({pid}): {exc}', flush=True)
    return total


def start_startup_sweep(storage_fn, load_projects_fn, sessions: Mapping[str, Mapping[str, Any]]) -> threading.Thread:
    """Run ``startup_sweep`` off the boot path: a cold read validates every file."""
    def run() -> None:
        try:
            lost = startup_sweep(storage_fn(), load_projects_fn() or [], sessions)
            if lost:
                _log(f'[test-run-sweep] startup: finished {lost} orphaned run(s) as lost', flush=True)
        except Exception as exc:
            _log(f'[test-run-sweep] startup sweep failed: {exc}', flush=True)

    thread = threading.Thread(target=run, name='test-run-lost-sweep', daemon=True)
    thread.start()
    return thread
