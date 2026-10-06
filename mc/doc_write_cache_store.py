"""Disk persistence for the Documents-tab transcript scan cache.

`agent_runtime._DOC_WRITE_CACHE` maps transcript path -> (mtime, size, hits) so
`ClaudeRuntime.list_written_markdown()` re-parses only transcripts that changed.
It lived in memory only, so the FIRST Documents open after every server start
re-read the whole project history: measured 2026-10-05 on mission_control
(5,266 transcript files, 3.5 GB) at 8.7 s cold vs 0.18 s warm.

This module saves that cache under `~/.clayrune/` (never `data/projects/`:
anything written into DATA_DIR becomes a malformed "project", and nothing under
the repo may hold operator state) and loads it back on first use, so a restart
re-scans only transcripts whose (mtime, size) moved.

Derived data only: deleting the file, or any failure here, costs one full
rescan and never correctness. So every failure is logged and swallowed, a
corrupt/unknown-version file is ignored, and nothing here raises into a
request.

Writes are debounced onto a daemon timer thread. The request path only calls
`mark_dirty()` (a lock + a timer start); it never serializes or touches disk.

Format: {"version": 1, "entries": {"<transcript path>": [mtime, size, [hit, ...]]}}
hit = {"path", "ts", "tool", "session_id"}, exactly what the scan produces.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

SCHEMA_VERSION = 1
DEBOUNCE_SECONDS = 3.0

# Resolved lazily so tests (and HOME changes) can point it elsewhere.
CACHE_FILE: Optional[Path] = None

_lock = threading.Lock()
_loaded = False
_timer: Optional[threading.Timer] = None


def _log(msg: str) -> None:
    try:
        from mc.core import _log as core_log
        core_log(msg, flush=True)
    except Exception:
        print(msg, flush=True)


def cache_path() -> Path:
    return CACHE_FILE if CACHE_FILE is not None else Path.home() / '.clayrune' / 'doc_write_cache.json'


def _valid_hits(hits: Any) -> bool:
    return isinstance(hits, list) and all(
        isinstance(h, dict) and isinstance(h.get('path'), str) for h in hits)


def read_entries(path: Optional[Path] = None) -> Dict[str, Any]:
    """Entries from disk as {ckey: (mtime, size, hits)}. Missing file -> {};
    unreadable / corrupt / wrong-version file -> {} and a log line."""
    p = Path(path) if path is not None else cache_path()
    try:
        raw = p.read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}
    except Exception as e:
        _log(f"[doc-cache] could not read {p.name}, falling back to a full scan: {e}")
        return {}
    try:
        doc = json.loads(raw)
        if not isinstance(doc, dict) or doc.get('version') != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema {doc.get('version') if isinstance(doc, dict) else type(doc).__name__}")
        entries = doc.get('entries')
        if not isinstance(entries, dict):
            raise ValueError('entries is not an object')
    except Exception as e:
        _log(f"[doc-cache] {p.name} is corrupt or from another version, falling back to a full scan: {e}")
        return {}
    out: Dict[str, Any] = {}
    dropped = 0
    for k, v in entries.items():
        if (isinstance(k, str) and isinstance(v, list) and len(v) == 3
                and isinstance(v[0], (int, float)) and isinstance(v[1], int)
                and _valid_hits(v[2])):
            out[k] = (v[0], v[1], v[2])
        else:
            dropped += 1
    if dropped:
        _log(f"[doc-cache] dropped {dropped} malformed entries from {p.name}")
    return out


def load_into(cache: Dict[str, Any]) -> int:
    """Merge the on-disk entries into `cache` once per process (entries already
    in memory win). Returns the number of entries added; 0 on later calls."""
    global _loaded
    with _lock:
        if _loaded:
            return 0
        _loaded = True
    added = 0
    for k, v in read_entries().items():
        if k not in cache:
            cache[k] = v
            added += 1
    return added


def write_entries(entries: Dict[str, Any], path: Optional[Path] = None) -> bool:
    """Atomically write `entries` (tmp file + os.replace). Never raises."""
    p = Path(path) if path is not None else cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {'version': SCHEMA_VERSION,
               'entries': {k: [v[0], v[1], v[2]] for k, v in entries.items()}}
        text = json.dumps(doc, separators=(',', ':'))
        from mc.core import _atomic_write_text
        _atomic_write_text(p, text)
        return True
    except Exception as e:
        _log(f"[doc-cache] could not save {p.name}: {e}")
        return False


def flush(cache: Dict[str, Any]) -> bool:
    """Write `cache` now (the timer's body; also callable directly)."""
    with _lock:
        # a snapshot: the scan may be mutating `cache` on a request thread
        snap = dict(cache)
    return write_entries(snap)


def mark_dirty(cache: Dict[str, Any]) -> None:
    """Schedule one debounced save. Calls inside the window coalesce into the
    already-pending save, so a burst of scans costs a single write."""
    global _timer
    with _lock:
        if _timer is not None:
            return
        t = threading.Timer(DEBOUNCE_SECONDS, _fire, args=(cache,))
        t.daemon = True
        _timer = t
    t.start()


def _fire(cache: Dict[str, Any]) -> None:
    global _timer
    with _lock:
        _timer = None
    flush(cache)


def reset_for_tests(path: Optional[Path] = None) -> None:
    """Forget the loaded flag and any pending timer; optionally repoint the file."""
    global _loaded, _timer, CACHE_FILE
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = None
        _loaded = False
        CACHE_FILE = Path(path) if path is not None else None
