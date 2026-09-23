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
never half of either -- though on Windows that concurrent reader can make the
replace itself fail, and can itself be failed BY the replace, which is what
`_replace_with_retry` and `read_text_with_retry` below are for.

Leaf module: stdlib only, no Flask, no `mc.state` — importable from a
standalone repair script as cheaply as from a blueprint.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

# WinError 5 (Access is denied) / 32 (used by another process): an AV
# scanner or the Windows Search indexer briefly holding an exclusive handle
# on the just-written temp file or the target — milliseconds, not a real
# conflict. os.replace has no built-in retry for this on Windows (MC-959,
# 2026-09-18: a dispatch's project-record save hit WinError 5 mid-rename,
# the caller retried believing dispatch had failed, and the already-spawned
# child ran anyway — doubling spend). POSIX rename doesn't hit this class of
# error, so non-Windows callers should never see a retry fire in practice.
#
# MC-965 widened the SCHEDULE, not the idea. 5 attempts at 0.05s doubling
# is roughly the right wall-clock budget but far too few swings, because the
# contending handle here is a concurrent READER that reopens the file
# constantly rather than an AV scanner that holds it once: what matters is
# how many times you look, not how long you wait. Measured with 2 writer +
# 3 reader threads, attempts actually consumed per successful write were 4,
# 10 and 23 -- and two writes burned all 5 (then all 30) and were LOST. A
# ~2-5ms poll over the same ~1s budget loses none. Backoff is capped, not
# unbounded, so it starts fine and STAYS fine; a permanently locked target
# still raises, still bounded.
_RETRY_WINERRORS = (5, 32)
_RETRY_ATTEMPTS = 250
_RETRY_BASE_DELAY_S = 0.002
_RETRY_MAX_DELAY_S = 0.005


def _retry_sleep(delay: float) -> float:
    """Sleep `delay`, return the next (capped) one. One schedule for both
    the write and the read side, so they cannot drift apart."""
    time.sleep(delay)
    return min(delay * 2, _RETRY_MAX_DELAY_S)


def _replace_with_retry(tmp: str, path: Path) -> None:
    delay = _RETRY_BASE_DELAY_S
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            os.replace(tmp, path)
            return
        except OSError as e:
            if getattr(e, 'winerror', None) not in _RETRY_WINERRORS:
                raise
            if attempt == _RETRY_ATTEMPTS - 1:
                raise
            delay = _retry_sleep(delay)


# Public alias: mc/core._atomic_write_text writes MEMORY.md/archive through
# the same primitive and needs the same retry.
replace_with_retry = _replace_with_retry

# The READ side of the same race (MC-965). While a writer is mid-`os.replace`
# the target is briefly delete-pending, and a reader that opens it in that
# window gets EACCES. Measured on this box, 2 writer + 3 reader threads over
# 5s: 397 of 83,411 reads (0.48%) failed that way -- and crucially the
# exception arrives as PermissionError with `winerror` set to None (it comes
# from the CRT's _wopen, which sets errno, not a Win32 error code), so
# `_RETRY_WINERRORS` above cannot be used to select it.
#
# The rate understates the cost, because the callers caught `Exception`
# around read + parse together and could not tell a transient EACCES from
# real corruption: `_load_agent_log` QUARANTINED
# `mission_control_agent_log.json` on 2026-09-19 -- 1.16 MB, 500 rows,
# 2026-09-16..09-19 -- and it parses cleanly today. Raising OSError rather
# than returning '' is what keeps that distinction available to the caller.
def read_text_with_retry(path, encoding: str = 'utf-8',
                         attempts: int = _RETRY_ATTEMPTS) -> str:
    """`Path(path).read_text()`, retrying a transient sharing violation.

    Bounded on the same schedule as `_replace_with_retry`, then re-raises,
    so a genuinely locked file is still reported rather than hanging a
    request thread. Only PermissionError is retried: a missing file or a
    decode error is not a race and must surface immediately.
    """
    delay = _RETRY_BASE_DELAY_S
    for attempt in range(attempts):
        try:
            return Path(path).read_text(encoding=encoding)
        except PermissionError:
            if attempt == attempts - 1:
                raise
            delay = _retry_sleep(delay)
    raise AssertionError('unreachable')  # pragma: no cover


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
        _replace_with_retry(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
