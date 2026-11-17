"""MC-1179 SF30: which terminal run files have already been validated.

A terminal record is immutable, so replaying it to check its stored snapshot
more than once is wasted work. The in-process fingerprint markers already skip
that within one server life; this ledger carries the same fact across a restart
so the first Floor read after boot stats the files instead of replaying them.

One append-only ``_validated.jsonl`` per project folder (not ``*.json``, so no
run enumeration sees it). A line trusts one file at exactly one fingerprint
(nanosecond mtime and size): any change to the file makes the line stale and the
file is validated again. It is a cache, never a source of truth: a missing,
truncated or unreadable ledger only costs a re-validation, and it is never
needed to read a run. Terminal runs only, so no line ever goes stale by the run
changing state.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mc.atomic_json import replace_with_retry
from mc.core import _log

NAME = '_validated.jsonl'
Fingerprint = tuple[int, int]


@dataclass(frozen=True)
class Entry:
    fingerprint: Fingerprint
    run_id: str
    created_at: float
    owner_session_id: str | None
    finished_at: float


def _encode(entry: Entry) -> bytes:
    return (json.dumps({'i': entry.run_id, 'm': entry.fingerprint[0], 's': entry.fingerprint[1],
                        'c': entry.created_at, 'o': entry.owner_session_id,
                        'f': entry.finished_at}, separators=(',', ':'), allow_nan=False)
            + '\n').encode('utf-8')


def _decode(line: bytes) -> Entry | None:
    try:
        raw = json.loads(line)
        return Entry((raw['m'], raw['s']), raw['i'], raw['c'], raw['o'], raw['f'])
    except (ValueError, KeyError, TypeError):
        return None  # a torn or foreign line is a missing entry, not an error


class ValidatedLedger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tables: dict[Path, dict[str, Entry]] = {}
        self._pending: dict[Path, list[Entry]] = {}

    def _table(self, folder: Path) -> dict[str, Entry]:
        table = self._tables.get(folder)
        if table is None:
            table = {}
            try:
                data = (folder / NAME).read_bytes()
            except OSError:
                data = b''
            for line in data.split(b'\n'):
                entry = _decode(line) if line else None
                if entry is not None:
                    table[entry.run_id] = entry
            self._tables[folder] = table
        return table

    def get(self, path: Path, fingerprint: Fingerprint) -> Entry | None:
        with self._lock:
            entry = self._table(path.parent).get(path.stem)
        return entry if entry is not None and entry.fingerprint == fingerprint else None

    def add(self, path: Path, fingerprint: Fingerprint, snapshot: dict[str, Any]) -> None:
        """Queue one validated terminal snapshot; ``flush`` writes the queue."""
        entry = Entry(fingerprint, snapshot['run_id'], snapshot['created_at'],
                      snapshot['owner_session_id'],
                      snapshot['finished_at'] if snapshot['finished_at'] is not None
                      else snapshot['updated_at'])
        with self._lock:
            table = self._table(path.parent)
            if table.get(entry.run_id) == entry:
                return
            table[entry.run_id] = entry
            self._pending.setdefault(path.parent, []).append(entry)

    def forget(self, path: Path) -> None:
        """The run is gone: drop it, including a line not yet written."""
        with self._lock:
            self._table(path.parent).pop(path.stem, None)
            queued = self._pending.get(path.parent)
            if queued:
                self._pending[path.parent] = [e for e in queued if e.run_id != path.stem]

    def flush(self) -> None:
        """Append queued entries, one write per folder, no fsync. Never raises."""
        with self._lock:
            pending, self._pending = self._pending, {}
        for folder, entries in pending.items():
            try:
                data = b''.join(_encode(e) for e in entries)
                fd = os.open(folder / NAME, os.O_WRONLY | os.O_APPEND | os.O_CREAT
                             | getattr(os, 'O_BINARY', 0), 0o644)
                try:
                    os.write(fd, data)
                finally:
                    os.close(fd)
            except OSError as exc:
                _log(f'[test-run-ledger] append failed ({folder.name[:8]}): {exc}', flush=True)

    def rewrite(self, folder: Path, live: set[str]) -> None:
        """Drop entries of runs that no longer exist (after a retention sweep)."""
        with self._lock:
            table = self._table(folder)
            for run_id in [r for r in table if r not in live]:
                del table[run_id]
            data = b''.join(_encode(e) for e in table.values())
        path = folder / NAME
        try:
            if not data:
                path.unlink(missing_ok=True)
                return
            fd, tmp = tempfile.mkstemp(dir=str(folder), prefix='.' + NAME + '.',
                                       suffix=f'.tmp{os.getpid()}')
            try:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(data)
                replace_with_retry(tmp, path)
            except OSError:
                os.unlink(tmp)
                raise
        except OSError as exc:
            _log(f'[test-run-ledger] rewrite failed ({folder.name[:8]}): {exc}', flush=True)
