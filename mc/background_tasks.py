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
guardian ask. The CLI's lists are authoritative for WHAT is running. Whether the agent
chose to wait on it is read from the tool_use input (MC-946): a Bash call the
CLI moved to the background after its 120s timeout carries no
`run_in_background`, the agent has moved on (measured 2026-10-05, a stray
`python -` held the child's final answer for 11 min and the spawner got the
reply to the later kill-wake instead), so such a task neither holds the
spawner callback nor re-arms it when it ends. Only tasks the agent started
deliberately, and tasks whose origin is unknown, hold it.
"""
from __future__ import annotations

import time as _time
from typing import Any, Dict, List

# Session keys owned here.
TASKS_KEY = '_bg_tasks'                     # task_id -> {description, task_type, since}
DEFERRED_KEY = '_notify_deferred_bg_since'  # epoch: spawner callback held since
INTERIM_KEY = '_bg_notify_interim_sent'     # callback already fired by the wait cap
RESUME_PENDING_KEY = '_bg_resume_pending'   # a notification arrived while idle
TOOL_USES_KEY = '_bg_tool_uses'             # tool_use id -> {name, bg} (bounded)
AUTO_IDS_KEY = '_bg_auto_task_ids'          # task ids the CLI moved on timeout
LAST_END_AUTO_KEY = '_bg_last_end_auto'     # the last task_notification was one
AUTO_WAKE_KEY = '_bg_auto_wake_pending'     # an auto-moved task ended while idle

DEFAULT_WAIT_MAX_MINUTES = 120

# Tools whose foreground call the CLI can move to the background on timeout.
_SHELL_TOOLS = frozenset({'Bash', 'PowerShell'})
_TOOL_USE_BOUND = 64


def _tasks(session: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    t = session.get(TASKS_KEY)
    if not isinstance(t, dict):
        t = {}
        session[TASKS_KEY] = t
    return t


def note_tool_use(session: Dict[str, Any], block: Dict[str, Any]) -> None:
    """Remember whether a tool_use asked for `run_in_background`, keyed by its
    id, so a later `task_started` can be told apart: deliberate, or moved to
    the background by the CLI's timeout."""
    if not isinstance(block, dict) or not block.get('id'):
        return
    inp = block.get('input')
    uses = session.get(TOOL_USES_KEY)
    if not isinstance(uses, dict):
        uses = {}
        session[TOOL_USES_KEY] = uses
    uses[str(block['id'])] = {
        'name': str(block.get('name') or ''),
        'bg': bool(isinstance(inp, dict) and inp.get('run_in_background')),
    }
    for k in list(uses)[:-_TOOL_USE_BOUND]:
        uses.pop(k, None)


def _moved_on_timeout(session: Dict[str, Any], tool_use_id: Any) -> bool:
    """True only for a shell call we SAW without `run_in_background`. An
    unknown id, or a non-shell tool (Monitor, a background subagent), is not
    classified: it keeps holding the callback, as before MC-946."""
    use = (session.get(TOOL_USES_KEY) or {}).get(str(tool_use_id or ''))
    return bool(use) and use['name'] in _SHELL_TOOLS and not use['bg']


def _auto_ids(session: Dict[str, Any]) -> Dict[str, bool]:
    ids = session.get(AUTO_IDS_KEY)
    if not isinstance(ids, dict):
        ids = {}
        session[AUTO_IDS_KEY] = ids
    return ids


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
                for k in ('tool_use_id', 'auto'):
                    if k in prev:
                        fresh[tid][k] = prev[k]
        session[TASKS_KEY] = fresh
        return 'changed'
    if sub == 'task_started':
        # Foreground tools emit task_started too (is_backgrounded: false,
        # measured) — those end inside the turn and must not be tracked.
        if not msg.get('is_backgrounded') or not msg.get('task_id'):
            return ''
        tid = str(msg['task_id'])
        entry = tasks.setdefault(tid, {
            'description': str(msg.get('description') or ''),
            'task_type': str(msg.get('task_type') or ''),
            'since': now,
        })
        # background_tasks_changed usually lands first and carries no
        # tool_use_id; this event is where the task learns where it came from.
        if msg.get('tool_use_id'):
            entry['tool_use_id'] = str(msg['tool_use_id'])
            entry['auto'] = _moved_on_timeout(session, msg['tool_use_id'])
            if entry['auto']:
                _auto_ids(session)[tid] = True
        return 'changed'
    if sub == 'task_updated':
        patch = msg.get('patch') or {}
        if isinstance(patch, dict) and patch.get('status') not in (None, 'running', 'pending'):
            tasks.pop(str(msg.get('task_id') or ''), None)
            return 'changed'
        return ''
    if sub == 'task_notification':
        tid = str(msg.get('task_id') or '')
        tasks.pop(tid, None)
        # task_updated already popped the task, so the ids set is what
        # remembers it was a timeout move.
        ids = _auto_ids(session)
        session[LAST_END_AUTO_KEY] = tid in ids
        ids.pop(tid, None)
        return 'notification'
    return ''


def open_tasks(session: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    t = session.get(TASKS_KEY)
    return t if isinstance(t, dict) else {}


def held_tasks(session: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The open tasks the spawner callback waits for: everything except a
    shell call the CLI moved to the background on timeout (MC-946)."""
    return {k: v for k, v in open_tasks(session).items() if not v.get('auto')}


def waiting_label(session: Dict[str, Any]) -> str:
    """'waiting on background task: ...' while a callback is held on a genuine
    background job and the session sits between turns, else ''. Shown on the
    Floor figure and in the chat header, where an idle child with a held
    callback otherwise looks like nothing is running."""
    if session.get('status') != 'idle' or not session.get(DEFERRED_KEY):
        return ''
    held = held_tasks(session)
    if not held:
        return ''
    return f"waiting on background task: {describe(session, tasks=held)}"


def describe(session: Dict[str, Any], limit: int = 3,
             tasks: Dict[str, Dict[str, Any]] | None = None) -> str:
    """Short human label for the open tasks, for chat status lines."""
    items = list((open_tasks(session) if tasks is None else tasks).items())
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
    session.pop(AUTO_WAKE_KEY, None)
    session.pop(AUTO_IDS_KEY, None)
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
