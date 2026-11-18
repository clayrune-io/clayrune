"""Is the process asking for a secret an attended chat? (MC-1179 P10, Ron 2026-11-17)

A secret marked `allow_unattended=false` is for an attended chat only. The
server used to decide "attended" by looking up the trigger type of a session id
the CALLER supplied (the exec routes' `claude_session_id` body field, or the
`CLAUDE_CODE_SESSION_ID` variable, which a command-line prefix can set), so an
unattended cycle that named a manual chat's id was classed attended and was
handed the secret.

The server now decides from what it knows itself:

  1. `mc.caller_attribution` (the attribution /agent/dispatch and /agent/send
     use): the loopback peer port -> the owning PID -> its parent chain -> a
     process Clayrune spawned for a managed session;
  2. the `trigger_type` the server recorded on that session's own record.

Only `manual` is attended. A caller that cannot be attributed to a managed
session (not loopback, no psutil, a socket in no managed tree, a detached
process), a session the server no longer holds, and a session record with no
trigger type are all unattended for such a secret: an inability to prove
"attended" is "unattended". A person's own terminal is such a caller; they
change the secret's flag once in the vault UI (passcode asked there, never on
use) instead.

Known residual, the one `mc.caller_attribution` documents: a process an agent
launches fully detached (its parent chain broken before it reaches the session
process) is not attributed, and is therefore unattended here: this fails safe.
A caller that edits the code it runs in-process (importing `mc.secrets_store`
and passing `unattended=False` itself) is outside this check; the vault key
file is readable by every process of the user, so no check on this surface can
stop it. A passphrase-locked vault closes that: every agent read then goes
through the server.

Never raises.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from mc import caller_attribution
from mc.core import _log


def _session_for(sid: str, agent_sessions: Dict[str, Any]):
    sess = agent_sessions.get(sid)
    if sess is not None:
        return sess
    for s in list(agent_sessions.values()):
        if str((s or {}).get('session_id') or '') == sid:
            return s
    return None


def caller_unattended(request, agent_sessions: Dict[str, Any],
                      tracked_processes: Dict[int, Dict[str, Any]]) -> Tuple[bool, str]:
    """(is_unattended, reason) for the process that made `request`. Never raises."""
    try:
        env = getattr(request, 'environ', None) or {}
        att = caller_attribution.attribute_caller(
            getattr(request, 'remote_addr', '') or '',
            env.get('REMOTE_PORT'), env.get('SERVER_PORT'),
            caller_attribution.managed_roots(agent_sessions, tracked_processes))
    except Exception as e:
        _log(f'[secrets] caller attribution failed: {e!r}')
        return True, 'caller attribution failed (fail-closed)'
    if att.status != caller_attribution.ATTRIBUTED:
        return True, (f'caller not attributed to a managed session '
                      f'({att.status}: {att.detail}) (fail-closed)')
    sid = (att.session_id or '').strip()
    sess = _session_for(sid, agent_sessions) if sid else None
    if sess is None:
        return True, 'the attributed session is not held by the server (fail-closed)'
    trigger_type = str(sess.get('trigger_type') or '').strip()
    if not trigger_type:
        return True, 'the attributed session has no recorded trigger_type (fail-closed)'
    if trigger_type == 'manual':
        return False, 'attributed to a manual session'
    return True, f'attributed to a session with trigger_type={trigger_type}'


def effective_unattended(flag: bool, request, agent_sessions: Dict[str, Any],
                         tracked_processes: Dict[int, Dict[str, Any]]) -> Tuple[bool, str]:
    """OR a caller-supplied flag with the server's own decision. The flag can
    only ADD strictness: it can never remove what the attribution found."""
    if flag:
        return True, 'flag'
    return caller_unattended(request, agent_sessions, tracked_processes)
