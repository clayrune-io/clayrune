"""MC-1179 SF30: per-run locks and the in-memory state of open test runs.

The store used to hold one process-wide lock across every project while it
parsed, replayed and rewrote a whole run record. Two small pieces replace it:

* ``RunLocks``: one re-entrant lock per run file, created on demand and dropped
  when nobody holds or waits for it, so the table never outgrows the runs in
  use. Two runs, in any projects, never wait for each other.
* ``OpenRegistry``: a bounded LRU of the reducer state of runs that are being
  written. The reducer is what ``replay`` would rebuild from the whole history
  on every write; keeping it lets a write apply only its own batch. An evicted
  or externally modified run is simply rebuilt from disk on its next write.

Leaf module: no import of the store, so the store can own both.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Fingerprint = tuple[int, int]
MAX_OPEN_RUNS = 16


class RunLocks:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._table: dict[Path, list[Any]] = {}

    @contextmanager
    def hold(self, path: Path) -> Generator[None]:
        with self._guard:
            slot = self._table.get(path)
            if slot is None:
                slot = self._table[path] = [threading.RLock(), 0]
            slot[1] += 1
        try:
            with slot[0]:
                yield
        finally:
            with self._guard:
                slot[1] -= 1
                if slot[1] == 0 and self._table.get(path) is slot:
                    del self._table[path]


@dataclass
class OpenRun:
    """State of one run as of its last write.

    ``reducer`` is None for a run that is already terminal: nothing more can be
    applied to it. ``truncate_to`` is set when loading found a torn last line;
    the next append cuts the tail back to that length first.
    """

    enrollment: dict[str, Any]
    reducer: Any
    snapshot: dict[str, Any]
    fingerprint: Fingerprint
    truncate_to: int | None = None


class OpenRegistry:
    def __init__(self, max_entries: int = MAX_OPEN_RUNS) -> None:
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self.entries: OrderedDict[Path, OpenRun] = OrderedDict()

    def get(self, path: Path) -> OpenRun | None:
        with self._lock:
            run = self.entries.get(path)
            if run is not None:
                self.entries.move_to_end(path)
            return run

    def put(self, path: Path, run: OpenRun) -> None:
        with self._lock:
            self.entries[path] = run
            self.entries.move_to_end(path)
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)

    def discard(self, path: Path) -> None:
        with self._lock:
            self.entries.pop(path, None)
