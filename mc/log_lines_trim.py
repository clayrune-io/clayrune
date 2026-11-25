"""Cap for `session['log_lines']` that never drops the human's own lines.

The stream reader used to do `log_lines = log_lines[-1500:]` once the list
passed 2000, silently. Stream events share that list with the `"> Ron: ..."`
lines the Scribe's habit classifier reads
(`memory._habit_source_statements`), so on a chat with enough output the
user's earlier statements were discarded before any terminal Scribe could see
them (MC-964, docs/_journal/b2d85e51-memory-retrieval-diagnosis.md, Bram
2026-10-15: one Ron line followed by 2001 stream lines left 0 statements).

`trim` keeps the last `keep` lines plus every user line older than that, in
original order, and logs when it drops anything. A user line is one whose
text, after leading whitespace, starts with `"> "`: both `"> Ron: msg"` and
the `"\\n> Ron: msg\\n"` shape /agent/send writes, and `"> [queued] Ron: ..."`.
"""
from mc.core import _log, TimestampedLines

DEFAULT_CAP = 2000
DEFAULT_KEEP = 1500
# Every provider's reader writes a tool call with this prefix
# (agent_runtime._format_tool_activity), so one line is one step.
TOOL_LINE_PREFIX = '[tool: '


def is_user_line(line):
    return isinstance(line, str) and line.lstrip().startswith('> ')


def is_tool_line(line):
    return isinstance(line, str) and line.startswith(TOOL_LINE_PREFIX)


def count_steps(lines):
    """Tool calls in this chat, including lines `trim` has since dropped."""
    dropped = getattr(lines, 'dropped_steps', 0)
    return dropped + sum(1 for line in list(lines or []) if is_tool_line(line))


def trim(lines, cap=DEFAULT_CAP, keep=DEFAULT_KEEP, *, label=''):
    """Return `lines` unchanged when len <= cap, else the last `keep` lines
    plus every older user line. Keeps a TimestampedLines' `.ts` in lock-step.
    Never raises: on any fault returns the plain tail slice (the old
    behaviour) after logging, so a bug here cannot stall the stream reader."""
    try:
        if len(lines) <= cap:
            return lines
        cut = len(lines) - keep
        pinned = [i for i in range(cut) if is_user_line(lines[i])]
        dropped = cut - len(pinned)
        keep_idx = pinned + list(range(cut, len(lines)))
        items = [lines[i] for i in keep_idx]
        ts = getattr(lines, 'ts', None)
        out = (TimestampedLines(items, ts=[ts[i] for i in keep_idx])
               if ts is not None and len(ts) == len(lines) else items)
        if isinstance(out, TimestampedLines):
            # Kept lines are the tail plus user lines, so every tool line
            # before the cut is dropped. Carried so the step count never falls.
            out.dropped_steps = (getattr(lines, 'dropped_steps', 0)
                                 + sum(1 for i in range(cut) if is_tool_line(lines[i])))
        _log(f"[log-lines] {label or 'session'}: trimmed {dropped} of {len(lines)} "
             f"lines (kept last {keep} + {len(pinned)} older user lines)")
        return out
    except Exception as e:
        _log(f"[log-lines] trim failed ({e}); falling back to tail slice")
        return lines[-keep:]
