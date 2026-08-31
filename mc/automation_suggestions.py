"""Consent-first automation suggestions — the miner, the store, and the latch.

MC-915. The agent PROPOSES a ready-to-run scheduled job; a human ACCEPTS it
(which creates the real schedule through the existing scheduler path) or
DISMISSES it (which is latched — never offered again).

WHY THIS SHAPE, and do not lose it: Clayrune's binding learning rail is that a
human must be on at least one side of every loop, and that learning may never
expand what the agent is allowed to do. A suggestion queue satisfies both BY
CONSTRUCTION — there is no code path in this module that reaches the schedules
store. Everything written here is inert until `automation_routes.accept` runs,
and that only runs from an explicit human POST. If a future edit lets a
suggestion become a live job without an explicit human action, the feature has
become the thing the rail exists to prevent.

WHERE THE STORE LIVES: `data/automation_suggestions.json` — a SIBLING of
DATA_DIR (`data/projects/`), never a member of it. `load_projects()` parses
every `*.json` under DATA_DIR as a project record, so a stray file there becomes
a malformed "project" and 500s `_get_active_restart_blockers`, taking down both
restart endpoints (the LOAD-BEARING DATA_DIR rule in CLAUDE.md).
`data/schedules.json` is the precedent this follows. Because the file never
enters DATA_DIR it needs no `EXCLUDED_SIDECAR_SUFFIXES` entry.

THE LATCH mirrors `distiller._is_suppressed` / `_suppress_artifact`, on purpose:

  * keyed by a CONTENT fingerprint, not a row id — a row id lets the same ask
    back in under a new id after a restart, which is precisely how
    `preference-1ba8d678` ended up live in ~/.claude/skills/ while sitting in
    _rejected/;
  * consulted at GENERATION time, before a candidate is written, not at render
    time — a dismissed pattern never re-enters the store at all;
  * "no" is permanent; "yes" is observable-dynamic. Acceptance is recorded for
    audit, but the live guard against re-offering an accepted suggestion is
    "does a schedule with this task already exist" — so deleting that schedule
    makes the ask proposable again, which is what un-accepting should mean.

It does NOT share the distiller's `_skill_stats.json` file: that store is keyed
`{exact}:{kind}` for distilled artifacts and is a per-project sidecar inside
DATA_DIR. The pattern is reused; the file is not.

NO import cycle: leaf modules only (mc.core, stdlib). Never imports server or a
blueprint.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import re
import threading

from mc.core import _atomic_write_text, _log, now_iso

# -- wired by server.py (see wire()) ------------------------------------------
# STORE_PATH is a server.py-owned path constant -> wired placeholder (the 1.7
# SESSION_LABELS_PATH / process_ledger precedent).
STORE_PATH: Path = None  # type: ignore[assignment]

_store_lock = threading.Lock()

STORE_VERSION = 1

# -- miner thresholds ---------------------------------------------------------
# A repeated ask only becomes a suggestion when it looks like a HABIT, not a
# burst. All three gates earn their place — measured against the real agent
# logs on this machine (20 projects, ~2,300 runs) before these numbers were
# picked, not guessed:
#
#   count + distinct days alone yielded 19 candidates, and roughly half were
#   noise: a bug report retyped nine times over three days, "can you summarize
#   where we currently stand?", a design question asked in one sitting. Every
#   one of those clustered — high count, short SPAN. Adding the span gate cut
#   19 -> 11 and removed essentially all of the noise while keeping every real
#   job (a 50x/24-day health check, a 27x regime pass, a 7x/38-day
#   diagnostic). A stricter span of 14 days bought nothing and started
#   dropping real ones.
#
# The distinction the span gate encodes: a burst is a bad afternoon; a habit
# recurs across weeks. Only the second is worth a cron row.
MIN_OCCURRENCES = 4
MIN_DISTINCT_DAYS = 3
MIN_SPAN_DAYS = 7
MIN_TASK_CHARS = 40
MAX_SUGGESTIONS_PER_PROJECT = 3

# Wrapper prefixes the dispatcher prepends to a task. They are envelope, not
# ask: hashing them in would split one habit across several fingerprints and
# would put dispatcher boilerplate in the proposed schedule's prompt.
_WRAPPER_RE = re.compile(
    r'^\s*\[(?:scheduled run|steward cycle|the user is messaging you[^\]]*)[^\]]*\]\s*',
    re.I)

# Session-control chatter. These recur constantly and mean "resume", not "run
# this on a cadence" — scheduling one would dispatch a prompt with no content.
_CHATTER = (
    'continue where we left off',
    'memory condensation',
    'continue',
    'resume',
)


# -- normalization + fingerprint ----------------------------------------------

def normalize_task(task: str | None) -> str:
    """Canonical form used for both the fingerprint and the already-scheduled
    comparison. Strips dispatcher wrappers, folds whitespace, lowercases, and
    truncates — so a habit survives incidental re-wording of the tail."""
    t = (task or '').strip()
    # A task can carry more than one wrapper ([Scheduled run ...][Steward cycle]...).
    for _ in range(3):
        stripped = _WRAPPER_RE.sub('', t)
        if stripped == t:
            break
        t = stripped
    return re.sub(r'\s+', ' ', t).strip().lower()[:200]


def fingerprint(project_id: str, task: str | None) -> str:
    """Stable content id for (project, normalized ask). The latch key.

    Content-addressed on purpose — see the module docstring. A dismissal keyed
    by a row id would expire the moment the miner regenerated the row.
    """
    payload = f'{project_id}\n{normalize_task(task)}'.encode('utf-8')
    return hashlib.sha256(payload).hexdigest()[:16]


def _is_chatter(norm: str) -> bool:
    return any(norm.startswith(c) for c in _CHATTER)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


# -- the one suggestion source: recurring MANUAL dispatch ---------------------

def mine_recurring_manual_tasks(
    project_id: str,
    entries: Iterable[dict],
    scheduled_tasks: Iterable[str] = (),
) -> list[dict]:
    """Candidate suggestions from a project's agent log. PURE — no I/O, no
    store access, no scheduler access. Takes already-loaded log entries so it
    is testable from a fixture and can never reach a live store.

    Why this source and not a curated catalog or a skill-carried blueprint: it
    is the only one that costs nothing new (the log is already on disk and
    already parsed by `_load_agent_log`), it is deterministic, and its evidence
    is checkable by the human being asked to consent — "you have run this by
    hand 12 times on 9 different days" is a fact they can dispute. A catalog
    entry is a static menu and demonstrates no learning at all.

    Only `trigger_type == 'manual'` entries count. A schedule-triggered run
    re-logs its own task text, so counting those would let a job that already
    exists argue for its own creation.
    """
    already = {normalize_task(t) for t in scheduled_tasks}
    buckets: dict[str, dict[str, Any]] = {}

    for e in entries:
        if not isinstance(e, dict):
            continue
        if (e.get('trigger_type') or '') != 'manual':
            continue
        raw = (e.get('task') or '').strip()
        norm = normalize_task(raw)
        if len(norm) < MIN_TASK_CHARS or _is_chatter(norm):
            continue
        if norm in already:
            continue
        b = buckets.setdefault(norm, {'task': raw, 'count': 0, 'days': set(),
                                      'hours': Counter(), 'first': None,
                                      'last': None})
        b['count'] += 1
        dt = _parse_ts(e.get('ts') or e.get('started_at'))
        if dt is not None:
            dt = dt.astimezone(timezone.utc)
            b['days'].add(dt.date().isoformat())
            b['hours'][dt.hour] += 1
            if b['first'] is None or dt < b['first']:
                b['first'] = dt
            if b['last'] is None or dt > b['last']:
                b['last'] = dt

    out: list[dict] = []
    for norm, b in buckets.items():
        if b['count'] < MIN_OCCURRENCES or len(b['days']) < MIN_DISTINCT_DAYS:
            continue
        first, last = b['first'], b['last']
        span = (last - first).days if (first and last) else 0
        if span < MIN_SPAN_DAYS:
            continue
        hour = b['hours'].most_common(1)[0][0] if b['hours'] else 9
        out.append({
            'id': fingerprint(project_id, b['task']),
            'source': 'recurring_manual_dispatch',
            'project_id': project_id,
            'title': _title_for(b['task']),
            'rationale': (f"Dispatched by hand {b['count']} times on "
                          f"{len(b['days'])} different days over {span} days, "
                          f"and no schedule covers it."),
            'evidence': {
                'count': b['count'],
                'distinct_days': len(b['days']),
                'span_days': span,
                'first_seen': _iso_z(first),
                'last_seen': _iso_z(last),
            },
            # The ready-to-run job. Shaped exactly like a POST /api/schedules
            # body so `accept` can hand it to the existing create path unchanged.
            'spec': {
                'project_id': project_id,
                'task': b['task'],
                'description': 'Proposed from repeated manual dispatch (MC-915).',
                'schedule_type': 'daily',
                'time': f'{hour:02d}:00',
                # Inherit the project's default persona, as a manual dispatch
                # does. Pinning one here would be the suggestion quietly making
                # a decision the human did not make.
                'character': '',
                'continue_session': False,
            },
        })

    out.sort(key=lambda s: (-s['evidence']['count'], s['title']))
    return out[:MAX_SUGGESTIONS_PER_PROJECT]


def _iso_z(dt: datetime | None) -> str:
    return dt.isoformat().replace('+00:00', 'Z') if dt is not None else ''


def _title_for(task: str) -> str:
    first = re.sub(r'\s+', ' ', (task or '').strip()).split('. ')[0]
    return (first[:77] + '...') if len(first) > 78 else first


# -- store --------------------------------------------------------------------

def _empty_store() -> dict:
    return {'version': STORE_VERSION, 'suggestions': {}, 'decisions': {}}


def _read_store() -> dict:
    if STORE_PATH is None or not STORE_PATH.exists():
        return _empty_store()
    try:
        data = json.loads(STORE_PATH.read_text(encoding='utf-8'))
    except Exception as e:
        # A corrupt store must never re-open a latched "no". Report and treat as
        # empty for READS; the write path below only ever merges, so a later
        # write cannot silently erase decisions it could not parse.
        _log(f'[automation] suggestion store unreadable, treating as empty: {e}')
        return _empty_store()
    if not isinstance(data, dict):
        _log('[automation] suggestion store is not an object, treating as empty')
        return _empty_store()
    data.setdefault('version', STORE_VERSION)
    data.setdefault('suggestions', {})
    data.setdefault('decisions', {})
    return data


def _write_store(store: dict) -> None:
    if STORE_PATH is None:
        return
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(STORE_PATH, json.dumps(store, indent=2, ensure_ascii=False))


def is_decided(fp: str) -> bool:
    """True once a human has said yes or no to this fingerprint. Consulted at
    GENERATION time so a dismissed pattern never re-enters the store."""
    with _store_lock:
        return fp in (_read_store().get('decisions') or {})


def get_decision(fp: str) -> dict | None:
    with _store_lock:
        return (_read_store().get('decisions') or {}).get(fp)


def record_decision(fp: str, decision: str, source: str = 'ui',
                    **extra: Any) -> dict:
    """Persist a human's yes/no. Durable across restarts — this IS the latch."""
    rec: dict[str, Any] = {'decision': decision, 'decided_at': now_iso(),
                           'source': source}
    rec.update(extra)
    with _store_lock:
        store = _read_store()
        store.setdefault('decisions', {})[fp] = rec
        # Decided suggestions leave the pending pool. The decision record stays.
        (store.get('suggestions') or {}).pop(fp, None)
        _write_store(store)
    return rec


def refresh_project(project_id: str, entries: Iterable[dict],
                    scheduled_tasks: Iterable[str] = ()) -> int:
    """Mine `entries` and merge NEW, UNDECIDED candidates into the store.

    Returns how many were added. Existing pending rows are left untouched so a
    suggestion's stored spec cannot drift under a human who is looking at it.
    Nothing here touches the schedules store — by design (module docstring).
    """
    candidates = mine_recurring_manual_tasks(project_id, entries, scheduled_tasks)
    added = 0
    with _store_lock:
        store = _read_store()
        sugg = store.setdefault('suggestions', {})
        decisions = store.setdefault('decisions', {})
        for c in candidates:
            fp = c['id']
            if fp in decisions or fp in sugg:
                continue
            c['created_at'] = now_iso()
            sugg[fp] = c
            added += 1
        if added:
            _write_store(store)
    return added


def prune_project(project_id: str, live_ids: Iterable[str]) -> int:
    """Drop PENDING rows for a project that this pass no longer supports — the
    user created the schedule by hand, or the log rolled past the threshold.

    Decisions are never touched: dropping one would un-latch a dismissal, which
    is the exact failure the latch exists to prevent.
    """
    keep = set(live_ids)
    with _store_lock:
        store = _read_store()
        sugg = store.setdefault('suggestions', {})
        stale = [k for k, v in sugg.items()
                 if v.get('project_id') == project_id and k not in keep]
        for k in stale:
            sugg.pop(k, None)
        if stale:
            _write_store(store)
        return len(stale)


def list_pending(project_id: str | None = None) -> list[dict]:
    with _store_lock:
        store = _read_store()
        rows = list((store.get('suggestions') or {}).values())
    if project_id:
        rows = [r for r in rows if r.get('project_id') == project_id]
    rows.sort(key=lambda r: r.get('created_at') or '', reverse=True)
    return rows


def get_pending(fp: str) -> dict | None:
    with _store_lock:
        return (_read_store().get('suggestions') or {}).get(fp)
