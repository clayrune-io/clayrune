"""The turns a rollover / cross-provider handoff replays
(`agent_routes._build_handoff_context`).

A rolled-over session's first user turn is `[Now ...]` + the previous handoff
+ the new message. Replaying it raw under the transcript reader's 5000-char
head cap kept the nested handoff and cut the message off: on 2026-11-25 a turn
whose message started at char 5,099 lost it entirely
(docs/_journal/rollover-drops-inflight-turn.md).

So: read user turns uncapped, strip what Clayrune injected, and only then cap,
keeping the END of the text and saying how much was dropped.
"""
from typing import List, Tuple

from mc import agent_runtime as _agent_runtime

USER_TURN_CAP = 5000


def clean_turns(turns: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """`(role, text)` turns with each user turn reduced to what follows the
    injected preamble. A user turn that was nothing but injection is dropped;
    other roles pass through unchanged."""
    out: List[Tuple[str, str]] = []
    for role, text in turns:
        if role == 'user':
            text = _agent_runtime.strip_injected_preamble(text)
            omitted = len(text) - USER_TURN_CAP
            if omitted > 0:
                text = f'[... {omitted} chars omitted]\n{text[-USER_TURN_CAP:]}'
        if text:
            out.append((role, text))
    return out
