"""Delivery telemetry — which memory actually reaches a prompt.

`DAVE_DESIGN.md` §9 phase 4. Residency is the only scarce resource in the vault
(§5: 21.9 KB resident against a 1.1 MB corpus), and today nothing measures which
units earn their place. MC-892's eviction attempt failed its safety review for
exactly this reason: with no delivery signal, the only lever was an editor's
judgement, and 29–30 of the 67 lines it proposed removing had no surviving
delivery channel at all. The fix is not a better editor. It is a **cache keyed
on retrieval**: promote what gets delivered, demote what never does, delete
nothing.

Three things this module is deliberately NOT:

1. **It is not a mover.** Nothing here promotes, demotes, or edits a note. It
   counts. The residency decision is a separate step with a human-visible
   report, for the same reason `positions_review` reports and never edits — an
   unattended process that rewrites what every prompt says is the authority
   guard in a different hat.
2. **It is not load-bearing.** Every entry point swallows and logs. A telemetry
   failure must never cost an agent its read floor; the floor is the only
   retrieval channel that actually runs (agents open a memory file in 5% of
   sessions).
3. **It is not keyed on a filename.** `MEMORY_ARCHIVE.md` is ~2.5k separately
   ranked lines and `MEMORY.md#managed` is a few hundred entries; keying on the
   container would score every line by its neighbours' luck. Identity is the
   unit's own content hash, so a line keeps its history when the file around it
   is rewritten and loses it when the line itself changes — which is correct,
   because an edited line is a different claim.

The sidecar is JSON in the MEMORY dir, never under `DATA_DIR` (`load_projects`
treats every `*.json` there as a project and a stray one 500s both restart
endpoints). `_mem_corpus` globs `*.md`, so a `.json` beside the notes is inert.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from mc import state
from mc.core import _atomic_write_text, _log

# Beside the notes, not among them.
STATS_FILE = 'delivery_stats.json'

_lock = threading.Lock()

# A head long enough to identify a line in a report without turning the sidecar
# into a second copy of the archive.
_HEAD = 120


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def enabled():
    try:
        return bool(state.CONFIG.get('delivery_telemetry_enabled', True))
    except Exception:
        return True


def _stats_path(project) -> Path:
    from mc import memory as _mem
    base = _mem._get_memory_path(project)
    if not base:
        raise ValueError(f'project {(project or {}).get("id")!r} has no memory path')
    return Path(base).parent / STATS_FILE


def read_stats(project):
    """The counters. Never raises — an unreadable sidecar reads as empty."""
    try:
        p = _stats_path(project)
        if not p.is_file():
            return {}
        d = json.loads(p.read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except Exception as e:
        _log(f'[delivery] stats unreadable: {e}')
        return {}


def _write(project, stats):
    p = _stats_path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(p, json.dumps(stats, indent=2, sort_keys=True) + '\n')


def record(project, hits, *, context='read_floor', corpus_size=None):
    """Count one delivery event: `hits` are the units that reached a prompt.

    `hits` carry the internal `uid`/`cls` keys, so this must be called from
    inside the search before they are stripped for the public result shape.

    The task counter is the denominator that makes a zero meaningful. Without
    it "this note was never delivered" is unreadable: never over three tasks is
    noise, never over three hundred is a demotion.
    """
    if not enabled():
        return
    try:
        with _lock:
            stats = read_stats(project) or {}
            stats.setdefault('version', 1)
            stats.setdefault('since', _now_iso())
            units = stats.setdefault('units', {})
            ctx = stats.setdefault('contexts', {})
            ctx[context] = int(ctx.get(context, 0) or 0) + 1
            stats['tasks'] = int(stats.get('tasks', 0) or 0) + 1
            if corpus_size:
                stats['corpus_size'] = int(corpus_size)
            now = _now_iso()
            for h in (hits or []):
                uid = h.get('uid') or h.get('file')
                if not uid:
                    continue
                rec = units.get(uid)
                if not rec:
                    rec = {'file': h.get('file'), 'cls': h.get('cls'),
                           'head': (h.get('head') or h.get('snippet') or '')[:_HEAD],
                           'n': 0, 'via': 0, 'first': now}
                    units[uid] = rec
                rec['n'] = int(rec.get('n', 0) or 0) + 1
                # A link-reached hit is counted separately: it tells us whether
                # the [[wikilink]] layer earns its keep, and a note that ONLY
                # ever arrives by expansion is a different residency case from
                # one the ranker finds on its own.
                if h.get('via'):
                    rec['via'] = int(rec.get('via', 0) or 0) + 1
                rec['last'] = now
            stats['updated'] = now
            _write(project, stats)
    except Exception as e:
        _log(f'[delivery] record failed for {(project or {}).get("id")}: {e}')


def summary(project, corpus_uids=None):
    """A read-only view for reports: delivered units, and what never arrived.

    `corpus_uids` is an optional {uid: (file, cls, head)} map of what EXISTS —
    the never-delivered set is the interesting half and it cannot be derived
    from the counters alone, which only know what did arrive.
    """
    stats = read_stats(project)
    units = dict(stats.get('units') or {})
    tasks = int(stats.get('tasks', 0) or 0)
    delivered = sorted(
        ({'uid': k, **v} for k, v in units.items()),
        key=lambda r: (-int(r.get('n', 0) or 0), str(r.get('file') or '')))
    never = []
    if corpus_uids:
        for uid, meta in corpus_uids.items():
            if uid in units:
                continue
            f, cls, head = (list(meta) + [None, None, None])[:3]
            never.append({'uid': uid, 'file': f, 'cls': cls,
                          'head': (head or '')[:_HEAD], 'n': 0})
        never.sort(key=lambda r: (str(r.get('cls') or ''), str(r.get('file') or '')))
    return {'tasks': tasks, 'since': stats.get('since'),
            'updated': stats.get('updated'),
            'contexts': stats.get('contexts') or {},
            'delivered': delivered, 'never': never,
            'n_delivered': len(delivered), 'n_never': len(never)}
