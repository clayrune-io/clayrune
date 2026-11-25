"""The turns a rollover / cross-provider handoff replays
(`agent_routes._build_handoff_context`).

A rolled-over session's first user turn is `[Now ...]` + the previous handoff
+ the new message. Replaying it raw under the transcript reader's 5000-char
head cap kept the nested handoff and cut the message off: on 2026-11-25 a turn
whose message started at char 5,099 lost it entirely
(docs/_journal/rollover-drops-inflight-turn.md).

So: read user turns uncapped, strip what Clayrune injected, and only then cap,
keeping BOTH ends of the text and saying how much was dropped from the middle.
A long dispatch brief states its constraints at the top and its ask at the
bottom; keeping only the end lost the constraints.
"""
from typing import List, Tuple

from mc import agent_runtime as _agent_runtime

USER_TURN_CAP = 5000
USER_HEAD_KEEP = 2000    # of USER_TURN_CAP; the rest is kept from the end


def _cap(text: str) -> str:
    omitted = len(text) - USER_TURN_CAP
    if omitted <= 0:
        return text
    return (f'{text[:USER_HEAD_KEEP]}\n[... {omitted} chars omitted from the middle]\n'
            f'{text[-(USER_TURN_CAP - USER_HEAD_KEEP):]}')


def clean_turns(turns: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """`(role, text)` turns with each user turn reduced to what follows the
    injected preamble. A user turn that was nothing but injection is dropped;
    other roles pass through unchanged."""
    out: List[Tuple[str, str]] = []
    for role, text in turns:
        if role == 'user':
            text = _cap(_agent_runtime.strip_injected_preamble(text))
        if text:
            out.append((role, text))
    return out
