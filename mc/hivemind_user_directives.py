"""A person's directives to the orchestrator reach its next prompt (MC-1179 OS26 a).

`POST /api/hivemind/<id>/intervene` with the default target appends a
`directive` row addressed to `orchestrator` to the bus. The orchestrator is a
short-lived session whose prompt carried the goal, workstreams, synthesis and
decisions and no bus message, so nothing ever read it.

A row is a person's only if it carries `via == VIA`, a field the human-only
`intervene` route writes and `bus/post` never copies from a request (it also
refuses `from == 'user'` and `type == 'directive'`). `from` and `type` are
caller-chosen on `bus/post`, so they prove nothing. Rows written before this
stamp existed have no `via` and are not delivered. They are listed with
their time, newest last, so the orchestrator can tell a directive it already
acted on from a new one; the prompt carries the last `LIMIT` of them rather
than a read cursor, because an orchestrator run is one short task.
"""
from __future__ import annotations

from typing import Any

LIMIT = 10
_MAX_CHARS = 600
VIA = 'intervene'


def is_from_person(msg: dict) -> bool:
    """True for a row the human-only intervene route wrote."""
    return msg.get('via') == VIA and msg.get('from') == 'user'


def directives(api: Any, hivemind_id: str) -> list[dict]:
    rows = api._hm_read_bus_messages(hivemind_id, last_n=0)
    mine = [m for m in rows if is_from_person(m) and m.get('to') == 'orchestrator'
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
