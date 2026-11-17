"""Who owns an `active` hivemind workstream, and what happens when the owner is gone (MC-1179 OS25).

The orchestrator loop collects a worker only when its session is still in
``agent_sessions`` and ended ``completed`` or ``error``. A session that is
absent (server restart) or ``stopped`` (a person stopped the chat) matched
neither, so its workstream stayed ``active`` for ever: it counted against
``max_concurrent_workers`` and kept the run from ever finishing. Likewise pause
and stop parked workstreams as ``paused`` and nothing turned them back.

Kept out of ``hivemind_routes.py`` on purpose: this is a separate concern with
its own tests, and the routes file only calls into it.
"""
from __future__ import annotations

from typing import Any, Mapping

# A restart empties agent_sessions for a moment before anything is revived, and
# a finished session can be pruned before the loop's next 10 s tick; the owner
# must be seen missing for this long before the workstream is taken back.
GONE_GRACE_SECONDS = 60.0


def reclaim_gone_owner(ws: dict, sessions: Mapping[str, Any], now: float, *,
                       max_retries: int, roster: bool) -> str | None:
    """One loop pass over an `active` workstream whose owner may be gone.

    Mutates ``ws`` and returns what the caller must persist, or None when
    nothing changed: 'cleared' (owner is back, marker dropped), 'noted'
    (owner first seen missing), 'requeued', 'failed' or 'held' (taken back).
    A session that ended `completed` or `error` is not gone: the loop's own
    collection handles it.
    """
    sid = ws.get('current_agent_session_id')
    if ws.get('status') != 'active' or not sid:
        return None
    session = sessions.get(sid)
    if session is not None and session.get('status') != 'stopped':
        return 'cleared' if ws.pop('owner_gone_since', None) is not None else None
    since = ws.get('owner_gone_since')
    if not isinstance(since, (int, float)):
        ws['owner_gone_since'] = now
        return 'noted'
    if now - since < GONE_GRACE_SECONDS:
        return None
    ws.pop('owner_gone_since', None)
    ws['last_agent_session_id'] = sid
    ws['current_agent_session_id'] = None
    if roster:
        # A roster worker owns a worktree and a branch: an unattended retry
        # could orphan commits, so the workstream waits for a person.
        ws['status'] = 'blocked'
        ws['failure_reason'] = 'worker_gone'
        return 'held'
    retries = ws.get('retry_count', 0)
    if retries < max_retries:
        ws['retry_count'] = retries + 1
        ws['status'] = 'pending'
        ws.pop('failure_reason', None)
        return 'requeued'
    ws['status'] = 'failed'
    ws['failure_reason'] = 'worker_gone'
    return 'failed'
