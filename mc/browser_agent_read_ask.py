"""Ask at the moment of need (backlog b1e1b23c, MC-1059 piece A).

`read-digest` refuses an agent whose profile is off or whose site is off the list. That used
to end the exchange: the agent reported failure and the user learned nothing. Now, when the
caller is a known agent session, the refusal also posts a card into THAT agent's chat
(mc/browser_agent_read_card.py) so the user can answer where they are already looking.

The agent's only power here is to be refused, and the refusal produces the card. This module
never grants anything: `ask` opens a request and posts a line. The grant is made by
`browser_agent_read_request_routes`, which refuses an unattended caller and checks the
dashboard passcode before it touches the policy or mints a pass.

Who the caller is comes from the OS, not the request: `mc.caller_attribution` maps the loopback
peer to the agent session it descends from, and a body `session_id` is honoured only when that
attribution finds nobody (a human's own curl, or a box without psutil). An attributed caller
cannot name another session's chat to put a card in, or another session's pass to spend.
Residual, the same one `caller_attribution` documents: a fully detached process is
unattributed and can claim a session id; the worst it gets is a card in that chat and, if the
user then taps Allow once, one laundered read of the named site.
"""
from __future__ import annotations

from typing import Any

from flask import request

from mc import browser_agent_read as policy
from mc import browser_agent_read_card as card
from mc import browser_agent_read_requests as requests_store
from mc import caller_attribution, state
from mc.blueprints import browser_routes as br
from mc.core import _log

_MAX_NAME = 60


def caller_session_id(claimed: Any) -> str | None:
    """The agent session making this request: the attributed one when attribution can name
    it, else the id the body claimed, else None. Never raises."""
    try:
        env = request.environ
        att = caller_attribution.attribute_caller(
            request.remote_addr or '', env.get('REMOTE_PORT'), env.get('SERVER_PORT'),
            caller_attribution.managed_roots(state.agent_sessions, state.tracked_processes))
        if att.status == caller_attribution.ATTRIBUTED and att.session_id:
            return att.session_id
    except Exception as e:
        _log(f"[browser-agent-read] caller attribution failed: {e!r}", flush=True)
    return claimed.strip() if isinstance(claimed, str) and claimed.strip() else None


def _agent_name(session: dict[str, Any]) -> str:
    ch = session.get('character') if isinstance(session.get('character'), dict) else {}
    name = str(ch.get('agent_name') or state.CONFIG.get('agent_name') or 'An agent')
    return ' '.join(name.split())[:_MAX_NAME] or 'An agent'


def ask(profile: str, url: Any, session_id: str | None) -> str | None:
    """Open a request for this refused read and post its card. Returns a sentence for the
    refusal's `guidance` saying what happened, or None when no card was posted (unknown
    session, a profile that does not exist, an address that is not a plain https host)."""
    if not session_id:
        return None
    session = state.agent_sessions.get(session_id)
    host = policy.url_host(url)
    if not isinstance(session, dict) or not host or not br.named_profile_exists(profile):
        return None
    rec, why = requests_store.open_request(
        session_id=session_id, project_id=str(session.get('project_id') or ''),
        agent_name=_agent_name(session), profile=profile, domain=host)
    if rec is None:
        if why == 'rate_limited':
            return (f"A request to read {host} through '{profile}' was already shown to the "
                    f"user in this chat. Do not ask again now; try again on a later turn.")
        return None
    if not card.post(session, rec['id']):
        return None
    _log(f"[browser-agent-read] request {rec['id']} posted: session={session_id[:12]} "
         f"profile={profile} host={host}", flush=True)
    return (f"A request to read {host} through '{profile}' was shown to the user in this "
            f"chat. Do not retry now; try again on a later turn, after they have answered.")
