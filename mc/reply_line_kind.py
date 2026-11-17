"""Which `log_lines` entries are Clayrune's own status lines, not reply text.

`_collect_trailing_reply_text` (mc/agent_runtime.py) walks `log_lines` backward
to rebuild the final reply. It has to stop at the lines Clayrune itself writes
between and after the model's text, and it must NOT stop at a reply line that
merely begins with '[' (a Markdown link, a JSON fragment). The old test was
`startswith('[')`, which did both wrongly (MC-1179 A1): a link line was
dropped, so the saved summary and a workflow step's result became an earlier
message or a fragment.

A status line is one of:
  * a single bracket pair from start to finish: `[interrupted]`,
    `[gemini exited with code 1]` (the MC-935 rule, `_MARKER_ONLY_RE` in
    mc/agent_runtime.py); or
  * a labelled line whose label Clayrune writes itself, followed by payload:
    `[tool: Read] path`, `[hint] text`, `[qwen error] text`,
    `[codex warn] text`, `[mcp-sync] text`.

Anything else that begins with '[' is the model's text.
"""
import re

# Same pattern as `_MARKER_ONLY_RE` in mc/agent_runtime.py (a test pins them
# equal; this module cannot import agent_runtime, which imports it).
MARKER_ONLY_RE = re.compile(r'^\[[^\[\]]*\]$')

# Labels Clayrune puts in front of a payload: the tool-activity line
# (`_format_tool_activity`), the exit hint, `[<engine> <severity>]` from the
# shared and Gemini readers, and the MCP-sync notice.
_LABELLED_RE = re.compile(
    r'^\[(?:tool: [^\[\]]*'
    r'|hint'
    r'|mcp-sync'
    r'|[a-z][a-z0-9_.-]* (?:error|warn|warning|notice|info))\]')


def is_status_line(stripped: str) -> bool:
    """True for a Clayrune status line; `stripped` is already `.strip()`ed."""
    if not stripped.startswith('['):
        return False
    return bool(MARKER_ONLY_RE.match(stripped) or _LABELLED_RE.match(stripped))
