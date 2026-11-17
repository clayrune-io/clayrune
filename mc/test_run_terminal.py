"""Fingerprint-only terminal markers; thread-safe (MC-1179 SF30: no shared store lock).

One entry per observed terminal path, independent of snapshot LRU limits.
No snapshots/events are retained. History costs one path and two integers per
file; missing paths are forgotten when directly read. The retention sweep
(mc/test_run_retention.py) is the only thing that removes run files.
"""
from __future__ import annotations

import threading
from pathlib import Path

from mc.test_run_snapshots import Fingerprint


class TerminalIndex:
    def __init__(self) -> None:
        self.entries: dict[Path, Fingerprint] = {}
        self._lock = threading.Lock()

    def known(self, path: Path, fingerprint: Fingerprint) -> bool:
        """Like ``matches`` but never forgets a stale entry."""
        with self._lock:
            return self.entries.get(path) == fingerprint

    def matches(self, path: Path, fingerprint: Fingerprint) -> bool:
        with self._lock:
            if self.entries.get(path) == fingerprint:
                return True
            self.entries.pop(path, None)
            return False

    def discard(self, path: Path) -> None:
        with self._lock:
            self.entries.pop(path, None)

    def remember(self, path: Path, fingerprint: Fingerprint) -> None:
        with self._lock:
            self.entries[path] = fingerprint
