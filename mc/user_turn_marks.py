"""Which saved chat-buffer lines are user turns, across a JSON round trip.

A user prompt lives in `session['log_lines']` as a
`triggered_turns.UserTurnLine`: a str subclass, so the role rides on the
object's type and on nothing in its text. JSON has no such type. The adopt
checkpoint (mc/agent_shim_adopt.py `project_session`) therefore wrote every
line as a plain str, and a session restored after a server restart had no
user-turn boundaries left. `agent_routes._last_reply_text` then ran through an
unframed prompt that was not in slot zero (the queued follow-up
`_auto_dispatch_followup_locked` appends) and reported the previous reply, the
prompt and the new reply as one reply (MC-1179 R34, second review).

So the role is saved beside the lines: `indexes` names the positions of the
user turns in the list being saved, and `rewrap` puts the type back on restore.

The text of a line is deliberately NOT used to decide its role. A reply may
itself begin `> Someone: text`; a guard that matched on that shape cut real
replies and was removed for it.
"""
from __future__ import annotations

from typing import Any, Iterable, List

from mc.triggered_turns import UserTurnLine

# Key inside the checkpoint's `log_lines` object. A payload written before this
# module existed has no such key and restores with plain lines, as it always did.
KEY = 'user_turns'


def indexes(lines: Iterable[Any]) -> List[int]:
    """Positions, within `lines`, of the user turns."""
    return [i for i, line in enumerate(lines) if isinstance(line, UserTurnLine)]


def rewrap(lines: Iterable[Any], marks: Any) -> List[Any]:
    """`lines` with the marked positions turned back into `UserTurnLine`.

    Never raises. `marks` is read from disk, so anything that is not a list of
    in-range ints is skipped, and so is a mark on a line that is not a rendered
    prompt (`> label: text`): a damaged payload must not make a reply line look
    like the end of the user's turn.
    """
    out = list(lines)
    if not isinstance(marks, list):
        return out
    for i in marks:
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(out):
            continue
        line = out[i]
        if isinstance(line, str) and line.lstrip('\n').startswith('> '):
            out[i] = UserTurnLine(line)
    return out
