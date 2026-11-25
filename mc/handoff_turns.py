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

Stripping the nested handoff made history exactly one session deep: a chat
that rolled twice with little said in between started with nothing from before
the previous roll (2026-11-25 review, F4; 232 of 278 rolled transcripts were
under budget). `earlier_history` gives the budget the session's own turns left
unused back to the previous handoff's turns, newest first.
"""
import re
from typing import List, Tuple

from mc import agent_runtime as _agent_runtime
from mc import midturn_rollover as _midturn

USER_TURN_CAP = 5000
USER_HEAD_KEEP = 2000    # of USER_TURN_CAP; the rest is kept from the end

EARLIER_HEAD = ('--- Earlier history, carried over from the handoff this session '
                'itself started from (older than the turns below it) ---')
EARLIER_END = '--- End of earlier history ---'
_EARLIER_MIN_ROOM = 200  # below this a carried turn is a fragment, not history
# Lines of a previous handoff that frame its turns and are not turns.
_FRAME_LINE_RE = re.compile(
    r'^(?:' + re.escape(EARLIER_HEAD) + '|' + re.escape(EARLIER_END)
    + r'|\[\d+ earlier turn\(s\) omitted for length\]'
    + '|' + _agent_runtime.HANDOFF_HEADER_RE.pattern.lstrip('^')
    + '|' + re.escape(_agent_runtime.HANDOFF_FOOTER) + r')[ \t]*$', re.MULTILINE)
_TURN_BREAK_RE = re.compile(r'\n\s*\n(?=(?:User|Assistant): )')


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
            text = _cap(_agent_runtime.strip_injected_preamble(text)
                        or _carried_by_state_block(text))
        if text:
            out.append((role, text))
    return out


def _carried_by_state_block(raw: str) -> str:
    """The opening turn of a mid-task-rolled session is handoff + state block +
    a fixed sentence: all injection, so it cleaned to nothing and the turn was
    dropped, and the next handoff opened on an Assistant line without the
    message that turn was answering. Read that message, and the ones sent
    while it ran, back out of the state block."""
    return _midturn.carried_user_text(
        (raw or '').rpartition(_agent_runtime.HANDOFF_FOOTER)[2])


def _fit(turn: str, room: int) -> str:
    """`turn` ("User: ..." / "Assistant: ...") cut to `room` chars, both ends
    kept and the cut stated."""
    label, _, text = turn.partition(': ')
    note = f'\n[... about {len(turn) - room} chars omitted from the middle]\n'
    keep = room - len(label) - 2 - len(note)
    head = keep * 2 // 5
    return f'{label}: {text[:head]}{note}{text[-(keep - head):]}'


def earlier_history(turns: List[Tuple[str, str]], room: int) -> str:
    """Turns of the handoff this session itself started from, as one labelled
    section of at most `room` chars, or '' when there is no such handoff or no
    room for it. `turns` are the RAW turns (before `clean_turns`): the previous
    handoff rides in the first user turn.

    Newest first: the turns nearest this session are taken whole until one
    does not fit. A previous handoff that carried an earlier-history section of
    its own contributes those turns too, without its frame, so the section
    never nests and the budget alone bounds how far back it reaches."""
    first_user = next((text for role, text in turns if role == 'user'), '')
    lineage = _agent_runtime.handoff_lineage(first_user)
    if not lineage or room < _EARLIER_MIN_ROOM:
        return ''
    body = _FRAME_LINE_RE.sub('', lineage[2]).strip()
    older = [t.strip() for t in _TURN_BREAK_RE.split(body) if t.strip()]
    room -= len(EARLIER_HEAD) + len(EARLIER_END) + 2
    kept: List[str] = []
    for turn in reversed(older):
        cost = len(turn) + (2 if kept else 0)
        if cost > room:
            if not kept and room >= _EARLIER_MIN_ROOM:
                kept.append(_fit(turn, room))
            break
        kept.append(turn)
        room -= cost
    if not kept:
        return ''
    kept.reverse()
    return EARLIER_HEAD + '\n' + '\n\n'.join(kept) + '\n' + EARLIER_END
