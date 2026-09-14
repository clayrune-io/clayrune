"""Single source of truth for "is the caller of this HTTP request an
unattended agent session?" — docs/_review/2026-09-10_security.md F1/F5.

Every route in this codebase runs on an unauthenticated localhost surface
that every dispatched agent shares (they all run `--dangerously-skip-
permissions`, and the steward fence explicitly allows mutating `curl` to
`_LOCAL_HOSTS`). A handful of routes implement a decision CLAUDE.md reserves
for a human — vault writes, skill promotion, operator config — and the only
thing standing between "human clicked a button" and "agent POSTed the same
URL" used to be that nobody had written the check. `backup_routes.py` grew
one first (commit `ad6b07a`, fixing the header-nothing-sends bug this module
now generalizes); this is that same function, lifted so it has exactly one
definition instead of drifting into a second copy the way the 50-note cap
did.

This never reads anything the caller sends (no header, no body field — any
of those are exactly what an agent process can forge). Instead it asks: is
there a LIVE session, currently mid-turn (`status == 'running'`), that could
be the one making this very HTTP call right now? A Bash-tool `curl` to a
Clayrune route can only exist because some Claude CLI session is executing a
tool call at this instant, so if such a session is running and its recorded
`trigger_type` isn't `'manual'`, this request is presumed to be that
session's own tool call.

Fails CLOSED: a running session whose `trigger_type` is missing or blank
(e.g. a revived session — some revive paths don't carry it) is treated as
unattended, not given the lenient `'manual'` default other display-only
call sites use.

2026-09-14 (UNATTENDED_AGENT_PERMISSIONS_AUDIT §7): the "any non-manual
session anywhere counts" heuristic below was a real, live bug, not just a
conservative-by-design tradeoff — it made this decision about THE WHOLE
SERVER instead of THE CALLER. `PUT /api/config`
(`settings_routes.update_config`) and every secrets-vault write call this
with `project_id=None` specifically because a config key or a credential
isn't scoped to one project, so ANY agent running anywhere — Dave hiring a
helper, a hivemind fan-out, one of this project's own schedules — made
Ron's own dashboard PUT get refused with a 403, for as long as that
unrelated session stayed `running`. Adding `trigger_type='dispatch'`
(UNATTENDED_AGENT_PERMISSIONS_AUDIT §4) made this materially more frequent:
agent-to-agent dispatch is common, dashboard config edits are not rare
either, and the two now collide often instead of rarely.

Fixed by checking the REQUEST first: a browser Origin header is the same
structural signal `workflow_routes._is_agent_caller` already uses to prove
a call came from the SPA and not an agent's Bash/curl tool (that route has
no session/CSRF layer either — the whole local API surface is
localhost-trust, MC-914). A request carrying it is presumed human and is
NEVER refused by this function, no matter what else is running on the
server. Every dispatched agent's own curl call has no Origin header — this
narrows the check, it does not weaken it: a genuine agent caller (no
Origin) still hits the exact same running-session logic as before.
`has_request_context()` guards the many existing unit tests that call this
function directly with no Flask app/request context at all — those keep
testing the session-only logic unchanged.
"""

from flask import has_request_context, request

from mc.state import agent_sessions


def is_unattended_caller(project_id: str | None = None) -> bool:
    """True if a live, running agent session (other than a plain human/manual
    chat) could be the process making this request right now.

    `project_id`: scope the check to sessions dispatched against that
    project. Pass None when the route has no natural project scope (or the
    decision is global, like a secret or a config key) — every running
    session anywhere then counts, the conservative "one witness taints the
    candidate" OR the learning-safety rails use elsewhere.

    A request carrying the browser Origin header is never refused — see the
    module docstring above for why this is a fix, not a loosening.
    """
    if has_request_context() and request.headers.get('Origin'):
        return False
    for s in agent_sessions.values():
        if s.get('status') != 'running':
            continue
        if project_id is not None and s.get('project_id') != project_id:
            continue
        if s.get('trigger_type') != 'manual':
            return True
    return False
