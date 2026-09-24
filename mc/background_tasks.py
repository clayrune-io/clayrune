"""Background-task tracking for Claude Mode B sessions (MC-958).

Claude Code's `run_in_background` (and a foreground Bash that outlives its
120s timeout and is moved to the background) promises "you will be notified
when it completes". Measured 2026-09-24 against claude 2.1.281 in the exact
Mode B shape (`--print --input-format stream-json --output-format
stream-json`): the CLI KEEPS that promise by itself. After the turn's
`result`, the process stays alive, and when the job ends it emits
`system/task_notification` and then runs a whole new turn on its own —
`system/init`, assistant output, a second `result` — with nobody writing to
stdin. Transcript 3a86ea9a (session 45460e95b4d5) shows four such
self-started turns in production.

What broke the promise was Clayrune, in three places:

  1. The spawner callback (`_maybe_notify_spawner`) fired on the FIRST
     `result` — the "tests running in background, will report" turn — and
     latched, so the self-started turn that carried the real answer never
     reached the spawner. That is the "callback fired with no results in it".
  2. The self-started turn ran while the session said `idle`, so nothing
     showed it working, and guardian idle-eviction (ON, 60 min) could kill
     the process — and its background job with it — mid-wait.
  3. A message sent while the turn was `running` goes through
     agent_interrupt, which kills the CLI and respawns it with `-r`. The
     background job dies with its parent; the resumed CLI reports it
     `stopped`. That is the empty output file of 2026-09-18 (session
     3aa45ebf207e, `b2f3nogwm.output`, 0 bytes). Nothing said so.

This module is the pure half: it keeps `session['_bg_tasks']` in step with
the CLI's own `system` events and answers the questions the reader and the
guardian ask. The CLI's lists are authoritative; tool_use inputs are not
parsed, because the timeout-moved-to-background case never carries
`run_in_background` in its input.
"""
from __future__ import annotations

import time as _time
from typing import Any, Dict, List

# Session keys owned here.
TASKS_KEY = '_bg_tasks'                     # task_id -> {description, task_type, since}
DEFERRED_KEY = '_notify_deferred_bg_since'  # epoch: spawner callback held since
INTERIM_KEY = '_bg_notify_interim_sent'     # callback already fired by the wait cap
RESUME_PENDING_KEY = '_bg_resume_pending'   # a notification arrived while idle

DEFAULT_WAIT_MAX_MINUTES = 120


def _tasks(session: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    t = session.get(TASKS_KEY)
    if not isinstance(t, dict):
        t = {}
        session[TASKS_KEY] = t
    return t


def note_system_event(session: Dict[str, Any], msg: Dict[str, Any],
                      now: float | None = None) -> str:
    """Update the session's open background tasks from one stream-json line.

    Returns 'notification' for a `task_notification` (a task ended and the
    CLI will tell the model about it), 'changed' when the open set changed,
    otherwise ''. Non-system envelopes are ignored.
    """
    if not isinstance(msg, dict) or msg.get('type') != 'system':
        return ''
    sub = msg.get('subtype')
    now = _time.time() if now is None else now
    tasks = _tasks(session)
    if sub == 'background_tasks_changed':
        # The CLI's full current list — replace, keeping first-seen times.
        listed = msg.get('tasks')
        if not isinstance(listed, list):
            return ''
        fresh: Dict[str, Dict[str, Any]] = {}
        for t in listed:
            if isinstance(t, dict) and t.get('task_id'):
                tid = str(t['task_id'])
                prev = tasks.get(tid) or {}
                fresh[tid] = {
                    'description': str(t.get('description') or prev.get('description') or ''),
                    'task_type': str(t.get('task_type') or prev.get('task_type') or ''),
                    'since': prev.get('since') or now,
                }
        session[TASKS_KEY] = fresh
        return 'changed'
    if sub == 'task_started':
        # Foreground tools emit task_started too (is_backgrounded: false,
        # measured) — those end inside the turn and must not be tracked.
        if not msg.get('is_backgrounded') or not msg.get('task_id'):
            return ''
        tasks.setdefault(str(msg['task_id']), {
            'description': str(msg.get('description') or ''),
            'task_type': str(msg.get('task_type') or ''),
            'since': now,
        })
        return 'changed'
    if sub == 'task_updated':
        patch = msg.get('patch') or {}
        if isinstance(patch, dict) and patch.get('status') not in (None, 'running', 'pending'):
            tasks.pop(str(msg.get('task_id') or ''), None)
            return 'changed'
        return ''
    if sub == 'task_notification':
        tasks.pop(str(msg.get('task_id') or ''), None)
        return 'notification'
    return ''


def open_tasks(session: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    t = session.get(TASKS_KEY)
    return t if isinstance(t, dict) else {}


def describe(session: Dict[str, Any], limit: int = 3) -> str:
    """Short human label for the open tasks, for chat status lines."""
    items = list(open_tasks(session).items())
    parts = [(v.get('description') or k)[:80] for k, v in items[:limit]]
    if len(items) > limit:
        parts.append(f'+{len(items) - limit} more')
    return '; '.join(parts)


def drain(session: Dict[str, Any]) -> List[str]:
    """Clear the open set and return what was in it. Call when the process
    that owned the tasks is gone: its background jobs died with it."""
    items = list(open_tasks(session).items())
    session[TASKS_KEY] = {}
    session.pop(DEFERRED_KEY, None)
    session.pop(RESUME_PENDING_KEY, None)
    return [(v.get('description') or k)[:80] for k, v in items]


def wait_expired(session: Dict[str, Any], now: float, max_minutes: Any) -> bool:
    """True once a held spawner callback has waited longer than the cap.

    The cap exists for background jobs that never end on their own (a dev
    server started with run_in_background): without it the spawner would
    never hear from the child at all. `max_minutes <= 0` means no cap.
    """
    since = session.get(DEFERRED_KEY)
    if not since:
        return False
    try:
        cap = float(max_minutes)
    except (TypeError, ValueError):
        cap = DEFAULT_WAIT_MAX_MINUTES
    if cap <= 0:
        return False
    return (now - float(since)) > cap * 60


def blocks_eviction(session: Dict[str, Any], now: float, max_minutes: Any) -> bool:
    """Idle-eviction must not kill a process that is waiting on its own
    background job — the job dies with it and the promised wake never comes.
    Bounded by the same cap, so a job that never ends cannot pin the fleet
    forever."""
    if not open_tasks(session):
        return False
    since = session.get(DEFERRED_KEY)
    if since:
        return not wait_expired(session, now, max_minutes)
    # Tasks open but no held callback (the turn is still running, or the
    # session has no spawner): measure from the oldest task instead.
    oldest = min((float(v.get('since') or now) for v in open_tasks(session).values()),
                 default=now)
    return not wait_expired({DEFERRED_KEY: oldest}, now, max_minutes)
