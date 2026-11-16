"""Forward an owner mail reply to the SPAWNER when its dispatched chat is gone (MC-1177).

A dispatched agent's worktree is removed after merge, so ``target.validate``
raises ``worktree_unavailable`` for any later reply to its thread and the mail
was held forever with nobody told. Decided 2026-11-16: when that is the ONLY
reason, deliver the reply into the chat that dispatched the agent instead.

Runs strictly AFTER the inbound checks (token, verified owner, DMARC, screen
hold, size limits), because ``MailWake.process_next`` only sees an event once
those passed. It never widens what the owner's mail may do: the spawner gets
the same exact-conversation guard as any mail wake, built from the spawner's
OWN live session (its cwd, provider, persona), never the original snapshot.
Any refusal returns False and the caller keeps today's hold unchanged.
"""
from __future__ import annotations

from typing import Any

from mc.agent_mail_continuation import Continuation, activate
from mc.agent_mail_target import MailHeld, resolve, snapshot
from mc.core import _log

# Only this reason is forwardable. Every other hold reason (closed session,
# persona changed, quota, ...) means the mail was refused for a safety reason
# that the spawner would not cure.
FORWARDABLE_REASON = 'worktree_unavailable'


def _clean(text: str) -> str:
    return ' '.join(str(text).split())[:60]


def forwarded_header(bound: dict[str, Any]) -> str:
    who = _clean((bound.get('character') or {}).get('name') or '') or 'a dispatched agent'
    return (f"[Email reply from the project owner to {who} (session {_clean(bound['session_id'])}), "
            "whose chat can no longer resume; forwarded to you as its spawner. "
            "Verified sender + this email's one-time token]")


def spawner_session_id(project: dict[str, Any], bound: dict[str, Any]) -> str:
    """Who dispatched the bound session, from the durable lineage; '' if unknown."""
    from mc.blueprints import agent_routes as ar
    pid, sid = project['id'], bound['session_id']
    with ar.get_manager(pid).lock:
        live = ar.agent_sessions.get(sid)
        row = next((r for r in ar._load_agent_log(pid, strict=True)
                    if r.get('session_id') == sid), None)
        spawner = (ar._row_spawned_by(row, live) or '').strip()
    return '' if spawner == sid else spawner


def try_forward(wake: Any, app: Any, event: dict[str, Any], bound: dict[str, Any],
                project: dict[str, Any], reason: str) -> bool:
    """True when the reply was handed to the spawner and the event is settled or
    UNCERTAIN (the caller must not hold it); False to keep the hold. Never raises."""
    if reason != FORWARDABLE_REASON:
        return False
    scope: Continuation | None = None
    try:
        from mc.blueprints import agent_routes as ar
        spawner = spawner_session_id(project, bound)
        if not spawner:
            return False
        with ar.get_manager(project['id']).lock:
            live = ar.agent_sessions.get(spawner)
            if live is None or live.get('project_id') != project['id']:
                _log(f"[agent-mail] spawner fallback: {spawner[:12]} not a live session of this project",
                     flush=True)
                return False
            spawner_bound = snapshot(project, live)
            sid, source = resolve(project, spawner_bound)
        scope = Continuation(wake, event, spawner_bound, project, sid, source)
        message = forwarded_header(bound) + '\n\n' + event['laundered']['reply_text']
        with activate(scope), app.test_request_context(
                f"/api/project/{project['id']}/agent/followup", method='POST',
                json={'session_id': sid, 'message': message, '_durable_delivery_ack': True}):
            ar.agent_followup(project['id'])
        if scope.started and not scope.waited:
            from mc.agent_mail_pipe import ACK_SECONDS
            scope.done.wait(ACK_SECONDS)
        if not scope.accepted_receipt:
            if scope.started:
                scope.unknown()
                return True
            return False
        wake.settle(event['event_id'])
        _log(f"[agent-mail] event {event['event_id'][:12]} forwarded to spawner {sid[:12]} "
             f"(bound chat {bound['session_id'][:12]}: {reason})", flush=True)
        return True
    except MailHeld as exc:
        _log(f"[agent-mail] spawner fallback refused: {exc}", flush=True)
    except Exception as exc:
        _log(f'[agent-mail] spawner fallback failed: {type(exc).__name__}: {exc}', flush=True)
    if scope is not None and scope.started:
        scope.unknown()
        return True
    return False
