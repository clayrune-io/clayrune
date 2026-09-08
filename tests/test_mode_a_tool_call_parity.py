"""Codex parity audit (docs/research/CODEX_PARITY_AUDIT.md (a) item 8) +
Ron's screenshot: a Codex session rendered a wall of bare, indistinguishable
`[codex tool: shell]` lines with no command and no output, where the
equivalent Claude session shows nothing at all.

Two things were wrong in `_mode_a_reader` (mc/agent_runtime.py), the shared
stdout reader for every Mode-A provider (codex, opencode, goose, aider,
kiro):

1. **Emission** — it reduced every tool call to
   `f"[{runtime.name} tool: {tname}]"`: no argument preview, so five
   different shell calls all rendered as the exact same string. Claude's own
   reader (`_read_agent_stream` in agent_routes.py) instead calls
   `_format_tool_activity(name, input)`, which includes a short single-line
   preview (the command, the file name, the search pattern, ...) — never the
   tool's result/output.

2. **Prefix** — it prefixed the line with the provider name
   (`[codex tool: ...]`) instead of the literal `[tool: ...]` every other
   Clayrune subsystem keys off:
     - `static/js/rich-text.js` `agentLineCls()` classifies a line as
       `agent-line-tool` (and thus routes it into the "Tool call lines"
       advanced-settings toggle, `ADV_FEATURES` in index.html, default OFF)
       only when it starts with the literal string `[tool:`.
     - GeminiRuntime's own (separate) reader was already fixed to use this
       exact canonical prefix, with a comment explaining why: a
       provider-qualified prefix bypasses the toggle, the CSS chip styling,
       and the ExitPlanMode/activity-ticker logic that all key off it.
   `_mode_a_reader` was never given the same fix, so Codex/opencode/goose/
   aider/kiro tool lines fell into the *different*, always-visible
   `agent-line-status` bucket instead of the toggle-hideable
   `agent-line-tool` bucket Claude's tool lines use — the literal mechanism
   behind "Claude doesn't show these, Codex does".

This test drives the REAL `CodexRuntime.parse_event` and the REAL shared
`_mode_a_reader` over Ron's exact sequence (five consecutive shell calls
followed by one MCP tool call) and checks the emitted lines against the
same predicate the frontend uses to decide "is this a tool line" — and
against the same predicate that decides "one tool call looks different
from the next", which is what actually made the wall of lines read as
spam.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402
from mc.agent_runtime import CodexRuntime, SessionHandle  # noqa: E402


class _FakeProc:
    """Just enough of subprocess.Popen for `_mode_a_reader`."""

    def __init__(self, lines, rc=0, pid=424242):
        self.stdout = iter(lines)
        self._rc = rc
        self.pid = pid

    def wait(self):
        return self._rc

    def poll(self):
        return self._rc


def _agent_line_cls_is_tool(line: str) -> bool:
    """Port of static/js/rich-text.js `agentLineCls()`'s tool-line branch:

        if (t.startsWith('[tool:')) return 'agent-line agent-line-tool';

    A line that does NOT satisfy this falls into a DIFFERENT bucket
    (`agent-line-status` for any other bracket-wrapped line) which the
    "Tool call lines" toggle never touches — that mis-bucketing is the bug.
    """
    return line.strip().startswith('[tool:')


def _drive_codex_sequence():
    """Ron's screenshot, reconstructed as realistic Codex 0.151 JSONL:
    five distinct shell calls, then one MCP tool call, then the turn ends.
    """
    events = [
        {'type': 'thread.started', 'thread_id': 'th-1'},
        {'type': 'turn.started'},
    ]
    commands = [
        'git status',
        'git diff --stat',
        "grep -rn 'TOOL_USE' mc/agent_runtime.py",
        'pytest tests/test_provider_runtimes.py -q',
        'git log --oneline -5',
    ]
    for i, cmd in enumerate(commands):
        events.append({
            'type': 'item.completed',
            'item': {'id': f'item_{i}', 'type': 'command_execution',
                      'command': cmd, 'aggregated_output': 'ok\n',
                      'exit_code': 0, 'status': 'completed'},
        })
    events.append({
        'type': 'item.completed',
        'item': {'id': 'item_mcp', 'type': 'mcp_tool_call',
                  'name': 'search_docs', 'server': 'docs-mcp',
                  'arguments': {'query': 'tool call rendering'}},
    })
    events.append({'type': 'turn.completed', 'usage': {'input_tokens': 100,
                                                         'output_tokens': 20}})

    lines = [json.dumps(e) for e in events]
    proc = _FakeProc(lines, rc=0)
    session = {'log_lines': [], 'proc': proc}
    handle = SessionHandle(
        mc_session_id='sid-1', provider='codex', mode='A',
        project_path='/p', project_id='proj1', session_dict=session,
        meta={'callbacks': {}},
    )
    runtime = CodexRuntime()
    agent_runtime_mod._mode_a_reader(proc, handle, runtime)
    return session['log_lines']


def _bracketed_tool_lines(log_lines):
    return [l for l in log_lines if isinstance(l, str) and 'tool: ' in l
            and l.strip().startswith('[')]


class TestModeAToolLineMatchesClaude:
    def test_six_tool_calls_produce_six_tool_lines(self):
        log_lines = _drive_codex_sequence()
        tool_lines = _bracketed_tool_lines(log_lines)
        assert len(tool_lines) == 6, log_lines

    def test_tool_lines_use_canonical_claude_prefix_not_provider_qualified(self):
        """The exact regression: `[codex tool: ...]` must not appear anywhere
        — every tool line must use the SAME `[tool: ...]` prefix Claude's own
        reader emits, or the frontend's toggle/styling never sees it as a
        tool line at all."""
        log_lines = _drive_codex_sequence()
        tool_lines = _bracketed_tool_lines(log_lines)
        for line in tool_lines:
            assert not line.strip().startswith('[codex'), (
                f"provider-qualified prefix leaked through: {line!r}")
            assert _agent_line_cls_is_tool(line), (
                f"line would NOT classify as agent-line-tool, so the "
                f"default-OFF \"Tool call lines\" toggle can never hide it: "
                f"{line!r}")

    def test_five_shell_calls_are_distinguishable_not_identical_spam(self):
        """Before the fix, all five shell calls rendered as the literal same
        string `[codex tool: shell]` — a wall of identical, contentless
        lines. Claude's equivalent (`_format_tool_activity`) includes a
        short command preview so each call is legible on its own."""
        log_lines = _drive_codex_sequence()
        shell_lines = [l for l in _bracketed_tool_lines(log_lines)
                       if 'shell' in l]
        assert len(shell_lines) == 5, log_lines
        assert len(set(shell_lines)) == 5, (
            f"shell tool lines are not distinguishable: {shell_lines}")
        # And the command itself must actually be present (not merely a
        # unique-looking id) — parity with Claude's Bash preview.
        assert any('git status' in l for l in shell_lines), shell_lines
        assert any('git log' in l for l in shell_lines), shell_lines

    def test_matches_claude_own_formatter_for_the_equivalent_bash_call(self):
        """Direct parity check: feeding the same command through Claude's own
        `_format_tool_activity('Bash', ...)` and Codex's `_format_tool_activity
        ('shell', ...)` must produce the SAME shape (`[tool: <name>] <cmd>`),
        because both now go through the identical shared formatter."""
        from mc.agent_runtime import _format_tool_activity
        claude_line = _format_tool_activity('Bash', {'command': 'git status'})
        codex_line = _format_tool_activity('shell', {'command': 'git status'})
        assert claude_line == '[tool: Bash] git status'
        assert codex_line == '[tool: shell] git status'
        assert _agent_line_cls_is_tool(claude_line)
        assert _agent_line_cls_is_tool(codex_line)

    def test_no_tool_result_output_leaks_into_the_chat(self):
        """Claude's chat never shows a tool_result body; Codex's rollout
        carries the full `aggregated_output` for every shell call, and it
        must stay out of session['log_lines'] the same way."""
        log_lines = _drive_codex_sequence()
        tool_lines = _bracketed_tool_lines(log_lines)
        assert not any('ok' in l for l in tool_lines), tool_lines
