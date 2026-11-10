"""Bounded process-local LRU of validated snapshots, never canonical events.

The store's shared lock protects every operation. Entries are private copies;
the absolute checked path and its nanosecond mtime/size identify a validated version.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
from pathlib import Path
import sys
from typing import Any

Fingerprint = tuple[int, int]


def _size(value: Any, seen: set[int]) -> int:
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_size(k, seen) + _size(v, seen) for k, v in value.items())
    elif isinstance(value, list):
        size += sum(_size(v, seen) for v in value)
    return size


class SnapshotCache:
    """Cap retained snapshot heap at 32 MiB and entry metadata at 4096 files."""

    def __init__(self, *, max_bytes: int = 32 * 1024 * 1024, max_entries: int = 4096):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.bytes = 0
        self.entries: OrderedDict[Path, tuple[Fingerprint, dict[str, Any], int]] = OrderedDict()

    def discard(self, path: Path) -> None:
        previous = self.entries.pop(path, None)
        if previous is not None:
            self.bytes -= previous[2]

    def get(self, path: Path, fingerprint: Fingerprint) -> dict[str, Any] | None:
        entry = self.entries.get(path)
        if entry is None:
            return None
        if entry[0] != fingerprint:
            self.discard(path)
            return None
        self.entries.move_to_end(path)
        return copy.deepcopy(entry[1])

    def put(self, path: Path, fingerprint: Fingerprint, snapshot: dict[str, Any],
            *, evict: bool = True) -> None:
        self.discard(path)
        snapshot = copy.deepcopy(snapshot)
        size = _size(snapshot, set())
        if size > self.max_bytes or self.max_entries < 1:
            return
        # A bulk scan larger than the budget must not evict every resident in
        # sequence. Admit misses only into free space; direct reads retain LRU.
        if not evict and (self.bytes + size > self.max_bytes
                          or len(self.entries) >= self.max_entries):
            return
        while self.entries and (self.bytes + size > self.max_bytes
                                or len(self.entries) >= self.max_entries):
            self.discard(next(iter(self.entries)))
        self.entries[path] = (fingerprint, snapshot, size)
        self.bytes += size
