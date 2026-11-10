"""Fingerprint-only terminal markers; guarded by the store's shared lock.

One entry per observed terminal path, independent of snapshot LRU limits.
No snapshots/events are retained. History costs one path and two integers per
file; missing paths are forgotten when directly read. No run files are removed.
"""
from __future__ import annotations

from pathlib import Path

from mc.test_run_snapshots import Fingerprint


class TerminalIndex:
    def __init__(self) -> None:
        self.entries: dict[Path, Fingerprint] = {}

    def matches(self, path: Path, fingerprint: Fingerprint) -> bool:
        if self.entries.get(path) == fingerprint:
            return True
        self.discard(path)
        return False

    def discard(self, path: Path) -> None:
        self.entries.pop(path, None)

    def remember(self, path: Path, fingerprint: Fingerprint) -> None:
        self.entries[path] = fingerprint
