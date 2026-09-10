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
"""

from mc.state import agent_sessions


def is_unattended_caller(project_id: str | None = None) -> bool:
    """True if a live, running agent session (other than a plain human/manual
    chat) could be the process making this request right now.

    `project_id`: scope the check to sessions dispatched against that
    project. Pass None when the route has no natural project scope (or the
    decision is global, like a secret or a config key) — every running
    session anywhere then counts, the conservative "one witness taints the
    candidate" OR the learning-safety rails use elsewhere.
    """
    for s in agent_sessions.values():
        if s.get('status') != 'running':
            continue
        if project_id is not None and s.get('project_id') != project_id:
            continue
        if s.get('trigger_type') != 'manual':
            return True
    return False
