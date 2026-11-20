"""Which `log_lines` entries are Clayrune's own status lines, not reply text.

`_collect_trailing_reply_text` (mc/agent_runtime.py) walks `log_lines` backward
to rebuild the final reply. It has to stop at the lines Clayrune itself writes
between and after the model's text, and it must NOT stop at a reply line that
merely begins with '[' (a Markdown link, a JSON fragment). The old test was
`startswith('[')`, which did both wrongly (MC-1179 A1): a link line was
dropped, so the saved summary and a workflow step's result became an earlier
message or a fragment. The first rewrite (one bracket pair with nothing
inside) swung the other way: `[stdin write error: [Errno 22] Invalid
argument]` has brackets inside, so it became reply text and the saved summary
was the error line.

A line is Clayrune's own status line when, after `.strip()`, it starts with '['
and is one of:
  1. ONE bracket expression that spans the whole line: the first '[' is closed
     by the line's last character, nested brackets allowed.
     `[exited with code 1]`, `[stream error: [Errno 22] bad]`,
     `[question NOT sent: ... [see log]]`. This is what every
     `[<text>]` status line Clayrune appends looks like, whatever exception
     text it embeds, as long as that text is balanced.
  2. A label Clayrune writes itself, then a payload that may hold anything:
     `[tool: X] path`, `[denied: X] command`, `[hint] text`,
     `[<engine> error|warn|...] text`, `[mcp-sync] text`,
     `[coverage] text`, `[context trim] text`, `[test verdict] text`.
  3. A `[<known status head> ...]` line that ends in ']' but whose embedded
     exception text has an unbalanced bracket (`[stream error: bad [ here]`),
     which rule 1 cannot see. The heads are the forms that interpolate
     exception or user text; the list is `_UNBALANCED_HEADS`.

A new status line should be written as `[<text>]` (one bracket expression),
or as `[label] payload` with a label added to `_LABELLED_RE`. If it embeds
text that can hold an unbalanced bracket, add its head to `_UNBALANCED_HEADS`.

Anything else that begins with '[' is the model's text: `[Docs](http://x) are
here`, `[1, 2, 3] is the list`, `[docs](x)`, a JSON fragment that does not
close on the last character. Still misread as status (the safer side: a
status line is skipped, so the worst case is the saved summary falls back to
the previous message, never an error line saved as the reply): a model line
that is itself exactly one bracket expression (`[x]`, `[1, 2, 3]`,
`[{"a": 1}]`), indistinguishable from a marker; and a model line that
begins with a status head word inside brackets and ends in ']'.
"""
import re

# Same pattern as `_MARKER_ONLY_RE` in mc/agent_runtime.py (a test pins them
# equal; this module cannot import agent_runtime, which imports it).
MARKER_ONLY_RE = re.compile(r'^\[[^\[\]]*\]$')

# Labels Clayrune puts in front of a payload: the tool-activity line
# (`_format_tool_activity`), the permission-denial line, the exit hint,
# `[<engine> <severity>]` from the shared and Gemini readers, the MCP-sync
# notice, the turn-end coverage advisory (`artifact_coverage.advisory_line`)
# and the context-trim notice (`_check_context_budget`). The last two were
# missing: the advisory, appended after a `[denied: ...]` line, was collected
# as the reply and the scan stopped there, so a spawner got the advisory and
# nothing else (MC-1179 A1/R34 follow-up). `test verdict` is the masked test
# run line (`mc.test_run_masking.status_line`, MC-1171).
_LABELLED_RE = re.compile(
    r'^\[(?:tool: [^\[\]]*'
    r'|denied: [^\[\]]*'
    r'|hint'
    r'|coverage'
    r'|context trim'
    r'|test verdict'
    r'|mcp-sync'
    r'|[a-z][a-z0-9_.-]* (?:error|warn|warning|notice|info))\]')

# Heads of the status lines that interpolate exception or user text, matched
# only when the line also ends in ']' (rule 3).
_UNBALANCED_HEADS_RE = re.compile(
    r'^\[(?:stream error|callback \S+ error|capture(?: EOF)? error'
    r'|stdin write error|respawn error|interrupt-resume error'
    r'|hook followup failed|backlog-sync error|Fresh restart also failed'
    r'|Image bridge failed|queued follow-up not sent|follow-up '
    r'|question (?:NOT|mail)|API credits:'
    r'|[A-Za-z][\w.-]* (?:dispatch failed|followup error|interrupt error))')


def _one_bracket_expression(s: str) -> bool:
    """True when the first '[' is closed by the last character of `s`."""
    depth = 0
    last = len(s) - 1
    for i, ch in enumerate(s):
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                return i == last
    return False


def is_status_line(stripped: str) -> bool:
    """True for a Clayrune status line; `stripped` is already `.strip()`ed."""
    if not stripped.startswith('['):
        return False
    if _one_bracket_expression(stripped) or _LABELLED_RE.match(stripped):
        return True
    return stripped.endswith(']') and bool(_UNBALANCED_HEADS_RE.match(stripped))
