"""Atomic JSON state writes (MC-946).

Every writer of a `data/` state record used to be a bare
`path.write_text(json.dumps(...))`. That is truncate-then-write: the target
is zero-length from the moment the file opens until the last byte lands, so
anything that kills the process in between leaves a partial file that no
longer parses.

That window is not hypothetical. `/api/system/restart` deliberately spawns
the replacement server BEFORE stopping the old one, and a watchdog
`os._exit(2)`s the old process 10s later regardless of what it is doing
(mc/blueprints/system_routes.py — the spawn-first order is a fix for a real
deadlock and is not the thing to change). So for up to ten seconds two MC
processes are alive and both write these files. A record truncated in that
window vanishes from the dashboard — `load_projects()` skips what it cannot
parse — and a truncated agent log reads back as "no history", which the
startup transcript backfill then makes permanent by overwriting it with
synthesized rows.

Serialize first, write a temp file in the SAME directory, flush + fsync,
then `os.replace` onto the target. `os.replace` is atomic on NTFS and POSIX
alike, so a concurrent reader sees the whole old file or the whole new one,
never half of either.

Leaf module: stdlib only, no Flask, no `mc.state` — importable from a
standalone repair script as cheaply as from a blueprint.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path, obj: Any, encoding: str = 'utf-8', **dumps_kw) -> None:
    """Replace `path` with `obj` serialized as JSON, atomically.

    `dumps_kw` is passed straight to `json.dumps` (callers keep their
    existing `indent=2, ensure_ascii=False`).

    Serialization happens BEFORE the target is touched, so an object that
    cannot be serialized raises with the previous file still intact. That
    is the point: a failed write must lose the new data, never the old.

    Temp naming is `.{name}.{random}.tmp{pid}`, three deliberate parts:
      - `.tmp{pid}` tail matches `mc.core.sweep_orphan_tmpfiles`, so a crash
        between write and replace strands a file the startup sweep already
        knows how to clean up;
      - the random middle keeps two processes writing the same target (the
        restart overlap above) off each other's temp file;
      - it does not end in `.json`, so a stranded temp under
        `data/projects/` is never picked up by `load_projects()`'s `*.json`
        glob (CLAUDE.md, "LOAD-BEARING RULE — DATA_DIR pollution").

    `tempfile.mkstemp` rather than `NamedTemporaryFile`: on Windows the
    latter holds an exclusive handle on a file we then have to reopen and
    rename (the same reason `mc/mcp.py` uses mkstemp).
    """
    path = Path(path)
    text = json.dumps(obj, **dumps_kw)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix=f'.{path.name}.',
                               suffix=f'.tmp{os.getpid()}')
    try:
        with os.fdopen(fd, 'w', encoding=encoding, newline='') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
