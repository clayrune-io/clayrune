"""Cross-cutting pure helpers (MODERNIZATION_PLAN.md Phase 0).

Moved VERBATIM from server.py; server.py keeps `from mc.core import ...`
shims so every existing call site is unchanged. The single permitted edit:
_log reads the log level via `state.CONFIG` (the live alias server.py binds
at boot) instead of the bare CONFIG global.

This module must never import server.py.
"""

import builtins as _builtins
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import request

from mc import state

# ── Logging shim (IMPROVEMENT_PLAN_V2.md P2-3) ───────────────────────────────
# Single chokepoint for the ~100 diagnostic _log()s. Deliberately
# _log()-signature-compatible: *args + **kw pass straight through, so the
# `_log(` → `_log(` sweep is purely mechanical and behavior-IDENTICAL at
# the default level ('info' shows everything info+). Set `log_level` to
# 'warn'/'error' to quiet the chatter, or 'debug' for more. Levels are
# advisory — a bare `_log("...")` is 'info'; pass level='warn'/'error' at
# noteworthy call sites over time (opportunistic, not a sweep).
_LOG_LEVELS = {'debug': 10, 'info': 20, 'warn': 30, 'error': 40}


def _log(*args, level='info', **kw):
    """_log()-compatible, level-gated. Default level keeps current output
    exactly (info threshold ≤ info). flush defaults True (most existing
    call sites already pass flush=True; making it the default is harmless
    and keeps subprocess-interleaved logs ordered)."""
    threshold = _LOG_LEVELS.get(str(state.CONFIG.get('log_level', 'info')).lower(), 20)
    if _LOG_LEVELS.get(level, 20) < threshold:
        return
    kw.setdefault('flush', True)
    try:
        _builtins.print(*args, **kw)
    except UnicodeEncodeError:
        # A cp1252 stdout (the redirected server console on Windows) cannot
        # encode e.g. an arrow, and print() RAISES. _log runs inside completion
        # paths, so that raise used to abort its caller: clayrune.log holds
        # "[runtime-completion] agent-log write failed: 'charmap' codec can't
        # encode character '→'" -- the agent-log row and the workflow/
        # spawner wake after it were both skipped. A log line must never be
        # able to cancel the work it is describing.
        try:
            _builtins.print(*(str(a).encode('ascii', 'backslashreplace').decode('ascii')
                              for a in args), **kw)
        except Exception:
            pass


def _atomic_write_text(path, text, encoding='utf-8'):
    """Write via temp-file + os.replace so a crash mid-write can't leave a
    torn MEMORY.md/archive (SPEC §3.A.MID atomicity). Same-dir temp so
    os.replace is atomic on the same filesystem. The replace is retried on a
    Windows sharing violation -- a concurrent READER of the target makes the
    bare call fail there; see mc/atomic_json.replace_with_retry."""
    from mc.atomic_json import replace_with_retry
    path = Path(path)
    tmp = path.with_name(f'.{path.name}.tmp{os.getpid()}')
    tmp.write_text(text, encoding=encoding)
    replace_with_retry(tmp, path)


def sweep_orphan_tmpfiles(roots, max_age_hours=24):
    """Delete orphaned temp files left behind by crashed writers.

    Two families: same-dir atomic-write temps (`.{name}.tmp{pid}`, see
    _atomic_write_text — a crash between write and os.replace strands one,
    e.g. data/.mc_child_pids.json.tmp49260 found 2026-07-11) under each
    root, and stale `clayrune-sysprompt-*.txt` spawn-context files in the
    OS temp dir (their normal cleanup rides on proc.wait(), which a hard
    MC kill skips). Age-gated so a live in-flight write is never swept.
    Best-effort; returns the number of files removed.
    """
    import re
    import tempfile
    import time as _t
    cutoff = _t.time() - max_age_hours * 3600
    removed = 0
    pat = re.compile(r'^\..+\.tmp\d+$')
    candidates = []
    for root in roots:
        try:
            root = Path(root)
            if root.is_dir():
                candidates.extend(
                    p for p in root.rglob('.*.tmp*') if pat.match(p.name))
        except Exception as e:
            _log(f"[tmp-sweep] scan of {root} failed: {e}")
    try:
        candidates.extend(
            Path(tempfile.gettempdir()).glob('clayrune-sysprompt-*.txt'))
    except Exception as e:
        _log(f"[tmp-sweep] temp-dir scan failed: {e}")
    for f in candidates:
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        _log(f"[tmp-sweep] removed {removed} orphaned temp file(s)")
    return removed


def _harden_secret_perms(path) -> None:
    """Best-effort: restrict a secret file (provider API keys, VAPID/Firebase
    keys, LAN passcode hash, mobile-pairing token) to the owning user only.
    POSIX → chmod 0600; Windows → strip ACL inheritance and grant only the
    current user. Never raises — a perms failure must not break the write."""
    p = str(path)
    try:
        if os.name == 'nt':
            import getpass
            user = os.environ.get('USERNAME') or getpass.getuser()
            subprocess.run(
                ['icacls', p, '/inheritance:r', '/grant:r', f'{user}:F'],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        else:
            os.chmod(p, 0o600)
    except Exception:
        pass


def time_ago(ts_str):
    if not ts_str:
        return 'never'
    try:
        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        secs = int((now - ts).total_seconds())
        if secs < 60:      return f'{secs}s ago'
        if secs < 3600:  return f'{secs // 60}m ago'
        if secs < 86400: return f'{secs // 3600}h ago'
        return f'{secs // 86400}d ago'
    except (ValueError, TypeError, AttributeError):
        return ts_str


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


class TimestampedLines(list):
    """A list that keeps a parallel `.ts` list of each item's PRODUCTION-time
    ISO timestamp in lock-step, automatically, through append/extend/+=/
    insert/slicing/pop/del/clear/slice-assignment.

    Used for `session['log_lines']` (MC-954 day dividers, agent_routes.py /
    agent_runtime.py). Earlier version stamped a line's timestamp at
    `/agent/stream`'s SSE chokepoint the first time ANY client happened to
    poll it — which meant a line produced by an unattended run with nobody's
    chat open got stamped with whenever a human later opened it, hours or
    days after it was actually produced (found in review of d7a2062: an
    overnight steward run showed every line as "Today" at the hour Ron
    opened the chat, with no day divider for the night it actually ran).

    A list subclass makes correctness structural instead of a matter of
    remembering to stamp at ~100 scattered `.append()` call sites across two
    files: every call site keeps working unmodified — `append()`/`extend()`
    stamp `now_iso()` automatically unless the caller passes a known
    historical `ts=` (used by the transcript-replay/revive/dispatch-resume
    paths, which know each line's real source date). `None` in `.ts` means
    "no date known for this line" (never guessed); it is never silently
    turned into "now" by a later reader.
    """

    def __init__(self, iterable=(), ts=None):
        items = list(iterable)
        list.__init__(self, items)
        if ts is not None:
            ts = list(ts)
            if len(ts) != len(items):
                raise ValueError('TimestampedLines: ts length must match items length')
            self.ts = ts
        else:
            self.ts = [now_iso() for _ in items]

    def append(self, item, ts=None):
        list.append(self, item)
        self.ts.append(ts if ts is not None else now_iso())

    def extend(self, iterable, ts=None):
        items = list(iterable)
        list.extend(self, items)
        if ts is not None:
            ts = list(ts)
            if len(ts) != len(items):
                raise ValueError('TimestampedLines.extend: ts length must match items length')
            self.ts.extend(ts)
        else:
            self.ts.extend(now_iso() for _ in items)

    def __iadd__(self, other):
        self.extend(other)
        return self

    def insert(self, index, item, ts=None):
        list.insert(self, index, item)
        norm = index if index >= 0 else max(0, len(self) - 1 + index)
        self.ts.insert(norm, ts if ts is not None else now_iso())

    def pop(self, index=-1):
        val = list.pop(self, index)
        self.ts.pop(index)
        return val

    def clear(self):
        list.clear(self)
        self.ts.clear()

    def __delitem__(self, key):
        list.__delitem__(self, key)
        del self.ts[key]

    def __getitem__(self, key):
        if isinstance(key, slice):
            return TimestampedLines(list.__getitem__(self, key), ts=self.ts[key])
        return list.__getitem__(self, key)

    def __setitem__(self, key, value):
        if isinstance(key, slice):
            values = list(value)
            list.__setitem__(self, key, values)
            # A slice-assign replaces content wholesale with no per-item ts
            # from the caller (no call site does this today) — stamp as
            # produced now, same convention as a bare append with no ts.
            self.ts[key] = [now_iso() for _ in values]
        else:
            list.__setitem__(self, key, value)
            norm = key if key >= 0 else len(self) + key
            if 0 <= norm < len(self.ts):
                self.ts[norm] = now_iso()

    def copy(self):
        return TimestampedLines(self, ts=self.ts)


BACKLOG_STATUS_HISTORY_CAP = 100


def record_backlog_status_change(item, new_status, by='user', ts=None):
    """Append a status transition to a backlog item's `status_history`.

    Status and done_at are overwritten in place, so without this a reopen
    erased the closure date and left no trace the item had ever been closed
    (MC-871, 2026-09-12: reopened after a wontdo, done_at silently cleared).
    Ticket numbers only advance, so reopening an old item instead of filing a
    new one is fine only if the item can tell its own story. No-op when the
    status is unchanged. The prior done_at rides along on the entry because
    the caller is about to clear it. Returns True when an entry was written.
    """
    old_status = item.get('status')
    if new_status == old_status:
        return False
    entry = {'ts': ts or now_iso(), 'from': old_status, 'to': new_status, 'by': by}
    if item.get('done_at'):
        entry['prior_done_at'] = item['done_at']
    history = item.setdefault('status_history', [])
    history.append(entry)
    if len(history) > BACKLOG_STATUS_HISTORY_CAP:
        _log(f"[backlog] status_history for {item.get('id')} capped at "
             f"{BACKLOG_STATUS_HISTORY_CAP}; dropping {len(history) - BACKLOG_STATUS_HISTORY_CAP} oldest")
        del history[:-BACKLOG_STATUS_HISTORY_CAP]
    return True


def file_type(filename):
    """Return a simple type hint for UI rendering."""
    ext = Path(filename).suffix.lower()
    images = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp'}
    if ext in images:
        return 'image'
    if ext == '.pdf':
        return 'pdf'
    return 'file'


def _is_loopback_request() -> bool:
    ra = (request.remote_addr or '').strip().lower()
    if ra in ('127.0.0.1', '::1', 'localhost'):
        return True
    # IPv4-mapped IPv6 loopback (e.g. ::ffff:127.0.0.1)
    return ra.startswith('::ffff:127.')


def path_is_within(candidate, base) -> bool:
    """True if `candidate` IS `base` or lives anywhere under it, comparing
    fully-resolved paths (so `..`, symlinks and case (Windows) can't dodge the
    check). Both accept str or Path. Returns False on any resolution error
    (unreadable/malformed path) rather than raising — callers use this as a
    safety gate, not a correctness assertion, so a path that can't even be
    resolved is treated as "not inside", never as "block by default"."""
    try:
        c = Path(candidate).resolve()
        b = Path(base).resolve()
    except Exception:
        return False
    if os.name == 'nt':
        return str(c).lower() == str(b).lower() or str(c).lower().startswith(str(b).lower() + os.sep)
    return c == b or str(c).startswith(str(b) + os.sep)
