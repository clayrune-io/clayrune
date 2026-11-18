"""A person's directives to the orchestrator reach its next prompt (MC-1179 OS26 a).

`POST /api/hivemind/<id>/intervene` with the default target appends a
`directive` row addressed to `orchestrator` to the bus. The orchestrator is a
short-lived session whose prompt carried the goal, workstreams, synthesis and
decisions and no bus message, so nothing ever read it.

The rows come only from the human-only `intervene` route (`from == 'user'`),
so the block is a person's words, not another agent's. They are listed with
their time, newest last, so the orchestrator can tell a directive it already
acted on from a new one; the prompt carries the last `LIMIT` of them rather
than a read cursor, because an orchestrator run is one short task.
"""
from __future__ import annotations

from typing import Any

LIMIT = 10
_MAX_CHARS = 600


def directives(api: Any, hivemind_id: str) -> list[dict]:
    rows = api._hm_read_bus_messages(hivemind_id, last_n=0)
    mine = [m for m in rows if m.get('from') == 'user' and m.get('to') == 'orchestrator'
            and m.get('type') == 'directive' and str(m.get('content') or '').strip()]
    return mine[-LIMIT:]


def block(api: Any, hivemind_id: str) -> str:
    """Prompt text for the orchestrator, or '' when the person said nothing."""
    rows = directives(api, hivemind_id)
    if not rows:
        return ''
    lines = '\n'.join(f"  - [{str(m.get('timestamp', ''))[:16]}] {str(m['content']).strip()[:_MAX_CHARS]}"
                      for m in rows)
    return ('DIRECTIVES FROM THE PERSON RUNNING THIS HIVEMIND (oldest first; follow them '
            'in this task where they apply):\n' + lines + '\n\n')
