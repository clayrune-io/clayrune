"""Fingerprint-keyed list metadata, independent of the bounded snapshot LRU.

The store's shared lock protects these entries. One immutable entry per observed
absolute path, with no LRU/byte cap; no snapshots or events are retained. No file
is deleted. Missing paths are forgotten when observed missing by the store.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mc.test_run_snapshots import Fingerprint


@dataclass(frozen=True)
class ListEntry:
    fingerprint: Fingerprint
    created_at: float
    run_id: str
    owner_session_id: str | None


class ListIndex:
    def __init__(self) -> None:
        self.entries: dict[Path, ListEntry] = {}

    def get(self, path: Path, fingerprint: Fingerprint) -> ListEntry | None:
        entry = self.entries.get(path)
        if entry is not None and entry.fingerprint == fingerprint:
            return entry
        self.discard(path)
        return None

    def discard(self, path: Path) -> None:
        self.entries.pop(path, None)

    def remember(self, path: Path, fingerprint: Fingerprint, snapshot: dict[str, Any]) -> None:
        self.entries[path] = ListEntry(fingerprint, snapshot['created_at'],
                                       snapshot['run_id'], snapshot['owner_session_id'])
