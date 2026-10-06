"""A turn that said nothing and did nothing is a failure, not a completion.

Backlog d37d0183 (2026-10-05): a qwen run (local qwen3:8b) ended its turn with
zero tool calls and only an ```mc:todo``` block. The shared reader saw rc == 0
and wrote status 'completed'; the chat showed nothing; the spawner was told
"ended with status=completed" with an empty final message. Nothing in the
status told Ron the model had returned nothing.

This module owns the one definition of "empty" so every engine's turn-end
decision (`_mode_a_reader`, `GeminiRuntime._read_stream`, the two Claude
stream readers in agent_routes) asks the same question:

  empty  =  no visible reply text once mc:* control blocks are stripped
            AND no tool call was made this turn
            AND the turn is not paused on an mc:question / AskUserQuestion.

A turn that only called tools is NOT empty (that is a legitimate way to end).
A turn paused on a question is NOT empty (it is waiting, not finished).

Pure and import-free of agent_runtime / agent_routes so both can import it.
"""
from __future__ import annotations

from typing import Any, Optional

# Session keys. `EMPTY_KEY` holds the reason string while the latest turn is
# flagged; absent otherwise. It is rewritten (set or popped) at every turn-end
# decision and cleared when a new turn starts, so a stale flag cannot make a
# healthy turn read as empty.
EMPTY_KEY = '_empty_turn'
TOOL_COUNT_KEY = '_turn_tool_calls'

_REASON = 'the model returned no reply text and made no tool calls'
_REASON_CONTROL_ONLY = ('the model returned no reply text and made no tool '
                        'calls (only mc control blocks)')


def note_tool_call(session: dict) -> None:
    """Count one tool call against the turn in progress (Claude readers; the
    shared Mode-A reader counts in a local, its process is one turn)."""
    session[TOOL_COUNT_KEY] = int(session.get(TOOL_COUNT_KEY) or 0) + 1


def begin_turn(session: dict) -> None:
    """A new turn is starting: drop the previous turn's flag and counter."""
    session.pop(EMPTY_KEY, None)
    session.pop(TOOL_COUNT_KEY, None)


def empty_reason(*, visible_text: str, tool_calls: int, paused: bool,
                 had_control_blocks: bool = False,
                 other_output: bool = False) -> Optional[str]:
    """The reason string when this turn was empty, else None.

    `visible_text` is the turn's assistant text with mc:* blocks already
    stripped. `paused` is `apply_mc_tool_blocks(...)['paused']`.
    `other_output` is True when the reader surfaced any non-JSON / unmodelled
    line to the chat: plain-text CLIs (aider, goose, ...) deliver their reply
    that way rather than as an assistant-text event, so it must count.
    """
    if paused or other_output or tool_calls > 0 or (visible_text or '').strip():
        return None
    return _REASON_CONTROL_ONLY if had_control_blocks else _REASON


def line_for(reason: str) -> str:
    """The bracketed chat line. Bracketed so `_collect_trailing_reply_text` and
    `_last_reply_text` never mistake it for the model's reply."""
    return f'[ended with no reply — {reason}]'


def mark(session: dict, reason: Optional[str], *, set_error: bool) -> bool:
    """Record the turn-end verdict on the session. Returns True if flagged.

    `set_error` makes the session status 'error' (process-per-turn readers,
    where status is decided here). A Mode-B turn boundary passes False: the
    process is alive and resumable, so the status stays 'idle' and the flag
    plus the chat line carry the signal.
    """
    session.pop(TOOL_COUNT_KEY, None)
    if not reason:
        session.pop(EMPTY_KEY, None)
        return False
    session[EMPTY_KEY] = reason
    session.setdefault('log_lines', []).append(line_for(reason))
    if set_error:
        session['status'] = 'error'
    return True


def callback_view(child: dict[str, Any], summary: str, status: str) -> tuple[str, str]:
    """(summary, status) for the spawner callback payload. Unchanged unless the
    child's latest turn was flagged empty, in which case the status reads
    'error' and an empty summary is replaced by the reason."""
    reason = child.get(EMPTY_KEY)
    if not reason:
        return summary, status
    return (summary or line_for(reason)), 'error'
