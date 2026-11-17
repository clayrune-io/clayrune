"""MC-1179 SF30: delete test runs that ended long ago.

Nothing ever deleted a run record: 404 MB in nine days on one install. This is
the only thing that does. A run is removed when it reached a terminal state more
than ``test_run_retention_days`` (default 14) ago. A run that is still active is
never removed by age; ending one that died is the orphan sweep's job
(mc/test_run_lost_sweep.py), and the run then ages out like any other. ``0``
keeps everything. There is no size bound.

The sweep runs once at startup, off the boot path, and then at most once a day.
It opens only files not modified inside the window: a terminal record is not
rewritten after it ends, so a recent mtime means a recent end (or a recent
write) and the file is kept without being read. For each candidate the store
checks, under that one run's lock, that the run is terminal and old and that
the file is the version it judged, then removes it. A file that cannot be read
or removed is logged and left; the sweep goes on. Records on disk are never
rewritten or converted: they are deleted or left alone.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from mc import state
from mc.core import _log
from mc.test_run_store import TestRunStore

SETTING = 'test_run_retention_days'
DEFAULT_DAYS = 14
SWEEP_INTERVAL_SECONDS = 24 * 60 * 60

_now = time.time


@dataclass
class SweepResult:
    runs: int = 0
    bytes: int = 0
    failed: int = 0


def retention_days(config: Any = None) -> int:
    """The configured window in days; 0 keeps everything. A malformed value
    falls back to the default rather than to "delete everything"."""
    config = state.CONFIG if config is None else config
    value = config.get(SETTING, DEFAULT_DAYS)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return DEFAULT_DAYS
    return value


def sweep(store: TestRunStore, days: int, now: float | None = None) -> SweepResult:
    """Remove every run that ended more than ``days`` days before ``now``."""
    result = SweepResult()
    if days <= 0:
        return result
    cutoff = (_now() if now is None else now) - days * 24 * 60 * 60
    touched = set()
    for path in list(store.run_files()):
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            freed = store.expire(path, cutoff)
        except FileNotFoundError:
            continue
        except Exception as exc:
            result.failed += 1
            _log(f'[test-run-retention] could not remove {path.stem}: {exc}', flush=True)
            continue
        if freed is not None:
            result.runs += 1
            result.bytes += freed
            touched.add(path.parent)
    for folder in touched:
        store.trim_ledger(folder)
    return result


def run_sweep(store: TestRunStore, config: Any = None) -> SweepResult:
    """One sweep, with its one log line."""
    days = retention_days(config)
    result = sweep(store, days)
    if days > 0:
        failed = f', {result.failed} failed' if result.failed else ''
        _log(f'[test-run-retention] removed {result.runs} run(s), {result.bytes} bytes '
             f'(kept {days} days{failed})', flush=True)
    return result


def start(storage_fn: Callable[[], TestRunStore], *, interval: float = SWEEP_INTERVAL_SECONDS,
          stop: threading.Event | None = None) -> threading.Thread:
    """Sweep now, off the caller's thread, then once per ``interval``."""
    stop = stop if stop is not None else threading.Event()

    def loop() -> None:
        while not stop.is_set():
            try:
                run_sweep(storage_fn())
            except Exception as exc:
                _log(f'[test-run-retention] sweep failed: {exc}', flush=True)
            stop.wait(interval)

    thread = threading.Thread(target=loop, name='test-run-retention', daemon=True)
    thread.start()
    return thread
