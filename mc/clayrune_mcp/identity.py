"""SERVER-side caller binding for the `clayrune` MCP tools.

The MCP process is launched knowing only the PROJECT it belongs to. It does not
carry, and no tool argument can supply, a session identity. Before any
mutating or owner-scoped tool acts, it asks Clayrune "who is calling?" and
Clayrune answers from the OS: `mc.caller_attribution` maps the loopback socket
to a PID and walks its parents to the agent CLI Clayrune itself spawned.

`resolve_caller` is the verification, kept pure (no flask, no globals) so the
boundary is testable on its own:

  attributed + session known + session in the launch-bound project -> OK
  anything else (unattributed, lookup unavailable, wrong project)  -> refused

Fails closed. An environment label alone is never accepted as proof.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple

from mc.caller_attribution import ATTRIBUTED, UNATTRIBUTED, Attribution


def _label(sess: Mapping[str, Any], sid: str) -> str:
    character = sess.get('character')
    if isinstance(character, dict):
        name = character.get('agent_name')
        if isinstance(name, str) and name.strip():
            return name.strip()[:60]
    return sid[:8]


def resolve_caller(att: Attribution, bound_project: str,
                   agent_sessions: Mapping[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """(http_status, payload) for POST /api/clayrune-mcp/identity."""
    if not bound_project:
        return 400, {'error': 'project_id required'}
    if att.status != ATTRIBUTED:
        why = ('the caller is not a process Clayrune launched for an agent session'
               if att.status == UNATTRIBUTED else
               f'cannot tell which session is calling ({att.detail})')
        return 403, {'error': f'identity refused: {why}'}
    sid = att.session_id
    sess = agent_sessions.get(sid)
    if not isinstance(sess, dict):
        return 403, {'error': 'identity refused: the calling session is no longer live'}
    if sess.get('project_id') != bound_project:
        # The server was launched for one project; its parent session belongs
        # to another. Never act across that line.
        return 403, {'error': 'identity refused: the calling session does not belong '
                              'to the project this server was launched for'}
    return 200, {'ok': True, 'session_id': sid, 'project_id': bound_project,
                 'label': _label(sess, sid)}
