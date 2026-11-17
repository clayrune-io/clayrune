"""MC-1179 SF30: the append-only event tail of an open test run.

A run is created as one ``<run_id>.json`` record, exactly as before. While it is
open, event batches are appended to ``<run_id>.log`` (one canonical JSON event
per line) instead of rewriting the record. When the run reaches a terminal state
the tail is folded back into the record in one atomic write and the tail is
removed, so a finished run is the same single file as it always was.

This module is file I/O only: it knows the line format and the compaction, not
the reducer or the store's locks. Callers hold the run's lock.

Crash model. A crash mid-append can leave an unterminated last line. Loading
drops exactly that line (a line without its newline was never acknowledged) and
the next append truncates it away first, so a later event is never glued onto
garbage. A complete line that is not a JSON object is corruption, not a torn
write, and raises. A crash between the compacted record landing and the tail
being removed leaves both; the record is already terminal and wins, the tail is
ignored (the loader checks the record's state, not the tail).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from mc.atomic_json import read_text_with_retry, replace_with_retry

SUFFIX = '.log'


def log_path(record_path: Path) -> Path:
    return record_path.with_suffix(SUFFIX)


def read_tail(path: Path) -> tuple[list[dict[str, Any]], int, bool]:
    """Complete events in the tail, the byte length they cover, and whether an
    unterminated last line was dropped. A missing tail is empty."""
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return [], 0, False
    end = data.rfind(b'\n') + 1
    events: list[dict[str, Any]] = []
    if end:
        for line in data[:end - 1].split(b'\n'):
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('Event log line is not an object')
            events.append(event)
    return events, end, len(data) > end


def append(path: Path, lines: list[bytes], *, truncate_to: int | None = None) -> int:
    """Append already-encoded newline-terminated lines in one write, then fsync.

    The file is opened per call (no descriptor is held across calls: a retention
    delete or an antivirus scan must never find a long-lived handle). With
    ``truncate_to`` a torn tail is cut away first. Returns the bytes written.
    """
    data = b''.join(lines)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_BINARY', 0)
    fd = os.open(path, flags, 0o644)
    try:
        if truncate_to is not None:
            os.ftruncate(fd, truncate_to)
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view):]
        os.fsync(fd)
    finally:
        os.close(fd)
    return len(data)


def remove(path: Path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def compact(record_path: Path, snapshot: dict[str, Any]) -> None:
    """Fold the tail into the record (atomic, fsynced), then drop the tail.

    The tail lines are already canonical JSON, so they are copied as bytes, not
    parsed and re-dumped: ending a run costs one pass over its bytes, not a
    parse plus a dump. The record keeps any extra keys it carries (the
    chat-anchor claim) and any events it already held (a run opened before this
    format existed). A torn last line is dropped, as on load.
    """
    record = json.loads(read_text_with_retry(record_path))
    head = {k: v for k, v in record.items() if k not in ('events', 'snapshot')}
    try:
        data = log_path(record_path).read_bytes()
    except FileNotFoundError:
        data = b''
    lines = [ln for ln in data[:data.rfind(b'\n') + 1].split(b'\n') if ln]
    held = [json.dumps(e, allow_nan=False).encode() for e in record['events']]
    body = (json.dumps(head, allow_nan=False)[:-1].encode() + b',"events":[' + b','.join(held + lines)
            + b'],"snapshot":' + json.dumps(snapshot, allow_nan=False).encode() + b'}')
    fd, tmp = tempfile.mkstemp(dir=str(record_path.parent), prefix=f'.{record_path.name}.',
                               suffix=f'.tmp{os.getpid()}')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(tmp, record_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    remove(log_path(record_path))
