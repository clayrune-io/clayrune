"""Who owns an `active` or `paused` hivemind workstream, and what happens when
the owner is gone (MC-1179 OS24 and OS25).

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

from typing import Any, Callable, Mapping

from mc.core import _log

# A restart empties agent_sessions for a moment before anything is revived, and
# a finished session can be pruned before the loop's next 10 s tick; the owner
# must be seen missing for this long before the workstream is taken back.
GONE_GRACE_SECONDS = 60.0

_LIVE_STATUSES = ('running', 'idle', 'waiting_for_reset')


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


def resume_paused(ws: dict, sessions: Mapping[str, Any]) -> str:
    """Give a `paused` workstream back to the loop; returns its new status.

    An owner that still exists (running, or finished while the run was paused)
    keeps the workstream `active`, so the loop collects it as it would have.
    No owner, a missing one or a stopped one means the work is not running:
    `pending`, so the loop spawns a worker.
    """
    sid = ws.get('current_agent_session_id')
    session = sessions.get(sid) if sid else None
    ws.pop('owner_gone_since', None)
    if session is not None and session.get('status') != 'stopped':
        ws['status'] = 'active'
        return 'active'
    if sid:
        ws['last_agent_session_id'] = sid
    ws['current_agent_session_id'] = None
    ws['status'] = 'pending'
    return 'pending'


def stop_worker_sessions(hivemind_id: str, sessions: Mapping[str, Any],
                         get_manager: Callable[[str], Any]) -> list[str]:
    """Stop every live worker session of one hivemind; returns their ids.

    The same steps as the agent stop route (project lock, cancel a pending
    limit-resume, mark `stopped`, kill the process tree outside the lock), so a
    stopped hivemind stops spending. Orchestrator sessions are left alone.
    """
    from mc.blueprints import agent_routes as ar

    stopped: list[str] = []
    for sid, session in list(sessions.items()):
        if (session.get('hivemind_id') != hivemind_id
                or not session.get('hivemind_ws_id')):
            continue
        project_id = session.get('project_id', '')
        proc = None
        try:
            with get_manager(project_id).lock:
                if session.get('status') not in _LIVE_STATUSES:
                    continue
                store = ar._limit_resume.store()
                if store:
                    store.cancel(sid, project_id)
                session['_limit_resume_cancelled'] = True
                proc = ar._stop_session(session, sid)
            stopped.append(sid)
            if proc is not None:
                ar._kill_proc_background(proc)
        except Exception as exc:
            _log(f'[hivemind] could not stop worker {sid} of {hivemind_id}: {exc}', flush=True)
    return stopped
