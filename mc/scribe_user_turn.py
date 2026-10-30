"""Reduce a user-role transcript turn to what the human typed, for the Scribe.

`_scribe_render_lines` (mc/memory.py) emits `USER:` lines since the 2026-10-29
continuity repair, and the extractor is told USER outranks ASSISTANT. That
makes the USER branch the one place a stale injection is most expensive:
`strip_injected_preamble` peels MC's blocks only while they LEAD the turn, so a
mid-turn roll (`{handoff}\n\n{state}\n\n{ROLL_MESSAGE}`, agent_routes) whose
handoff lacked its footer, or whose replay left a fragment ahead of the state
block, reached the extractor whole: the first-message restatement and the
replayed history arrived under the strongest label, which is the exact loop
the repair exists to break. Measured 2026-10-29 over the last 300 transcripts:
74 of 713 USER turns still carried the state block after the leading strip,
73 of them with the `--- Original task` line; 2 carried a full handoff
replay (and, inside it, STANDING POSITIONS / RELEVANT MEMORY / REPLY SHAPE
blocks); 202 were `[dispatched agent finished]` callbacks.

Positional, not anchored: text BEFORE a marker is kept (a queued human
message can precede the state block), everything from the first marker on is
dropped. One cut covers the roll sentence and the `Original task` line too,
since both only ever follow one of these headers.
"""
import mc.agent_runtime as _ar

# Once one of these begins, the rest of the turn is never the human's words:
# replayed history, roll state, or a delegation callback that MC composed.
CUT_MARKERS = (
    '=== Mid-task rollover state ===',
    '=== Prior conversation, started on',
    '[dispatched agent finished]',
)


def clean_user_turn(text: str) -> str:
    """The human-typed part of a user turn, or '' when there is none.

    Strips the leading injections first (so a marker that led the turn is
    gone before the positional cut), cuts at the earliest remaining marker,
    then strips again: the cut can expose a leading block the first pass
    could not reach. Never raises."""
    out = _ar.strip_injected_preamble(text or '')
    cut = min((i for i in (out.find(m) for m in CUT_MARKERS) if i >= 0), default=-1)
    if cut >= 0:
        out = _ar.strip_injected_preamble(out[:cut])
    if not out or _ar.is_nonuser_message(out):
        return ''
    return out
