"""W4 live bug: a Gemini agent replied exactly `LIVE2-OK-gemini`, but
Clayrune recorded the reply as `-OK-gemini` (mission_control agent_log entry
ts=2026-09-18T15:06:36Z, session a8e713ee56f8).

`GeminiRuntime._read_stream` and `parse_event` are NOT the bug: they append
every streamed delta fragment to `session['log_lines']` correctly, one array
element per chunk (`tests/fixtures/gemini_live_stream_2026-09-18.jsonl` is a
REAL captured `gemini --output-format stream-json` run reproducing the exact
same two-chunk split, LIVE 2026-09-18 against gemini-cli 0.59.0, live API,
not a mock).

The actual bug is downstream, in `mc/blueprints/agent_routes.py`: both
`_last_reply_text` (the MC-946 spawner-notify callback) and
`_log_agent_completion_body`'s `summary` field (the durable agent_log row)
took only the SINGLE LAST non-bracket `log_lines` entry as "the reply". That
assumption holds for Claude's block-based reader (one array element per
complete message) but not for any Mode-A provider that streams deltas
(Gemini's own `_read_stream`, and the shared `_mode_a_reader` used by
Qwen/Codex/OpenCode/Goose/Aider/Kiro) — those push one element PER CHUNK, so
"last line" silently discards every fragment before the final one.

Fixed by `_collect_trailing_reply_text` (mc/agent_runtime.py): walks
backward past trailing bracket/seed-line noise, then joins every
CONSECUTIVE real-content line in stream order — a no-op for Claude's
one-line-per-message shape, a full reconstruction for delta-chunked
providers.
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402
from mc.agent_runtime import GeminiRuntime, SessionHandle  # noqa: E402

FIXTURE = Path(__file__).parent / 'fixtures' / 'gemini_live_stream_2026-09-18.jsonl'


class _FakeProc:
    """Just enough of subprocess.Popen for `GeminiRuntime._read_stream`."""

    def __init__(self, lines, rc=0, pid=424242):
        self.stdout = iter(lines)
        self._rc = rc
        self.pid = pid

    def wait(self):
        return self._rc

    def poll(self):
        return self._rc


def _drive_real_captured_stream():
    """Feed the REAL captured stream-json lines through the REAL
    `GeminiRuntime._read_stream` and return the resulting session dict."""
    raw_lines = FIXTURE.read_text(encoding='utf-8').splitlines(keepends=True)
    assert raw_lines, 'fixture is empty — capture is missing'
    proc = _FakeProc(raw_lines, rc=0)
    session = {'log_lines': [], 'proc': proc}
    handle = SessionHandle(
        mc_session_id='sid-1', provider='gemini', mode='A',
        project_path='/p', project_id='proj1', session_dict=session,
        meta={'callbacks': {}},
    )
    runtime = GeminiRuntime()
    runtime._read_stream(proc, handle)
    return session


class TestGeminiDeltaReplyReconstruction:
    def test_fixture_really_is_a_two_chunk_split(self):
        """Sanity: the capture must actually exercise the multi-chunk case,
        or this test would pass for the wrong reason."""
        events = [json.loads(l) for l in FIXTURE.read_text(encoding='utf-8').splitlines() if l.strip()]
        deltas = [e for e in events if e.get('type') == 'message' and e.get('role') == 'assistant']
        assert len(deltas) == 2, deltas
        assert deltas[0]['content'] == 'LIVE'
        assert deltas[1]['content'] == '2-OK-gemini'

    def test_read_stream_logs_each_delta_as_its_own_line(self):
        """`_read_stream` itself is correct — this pins that it is NOT the
        bug, so a future fix does not "fix" the wrong function again."""
        session = _drive_real_captured_stream()
        assert session['log_lines'] == ['LIVE', '2-OK-gemini'], session['log_lines']

    def test_collect_trailing_reply_text_reconstructs_the_full_reply(self):
        """The actual fix: reconstructing from log_lines must recover the
        agent's real, complete reply — not just its final streamed chunk."""
        session = _drive_real_captured_stream()
        summary = agent_runtime_mod._collect_trailing_reply_text(session['log_lines'])
        assert summary == 'LIVE2-OK-gemini', summary
        # The exact observed regression, named explicitly so a future change
        # that reintroduces it is unambiguous about what broke.
        assert summary != '-OK-gemini'

    def test_three_way_split_also_reconstructs_fully(self):
        """The live split was two chunks; Gemini's own comment describes
        "sub-sentence deltas", i.e. an arbitrary number per turn. Pin that
        the fix isn't special-cased to exactly two fragments."""
        session = {'log_lines': ['Hello ', 'from ', 'Gemini.'], 'proc': None}
        summary = agent_runtime_mod._collect_trailing_reply_text(session['log_lines'])
        assert summary == 'Hello from Gemini.'

    def test_stops_at_a_preceding_turns_bracket_marker(self):
        """Must not reach back across a turn boundary (a bracketed MC status
        line, e.g. a prior `[gemini exited with code 0]`) and glue two
        different turns' text together."""
        lines = ['[gemini exited with code 0]', 'LIVE', '2-OK-gemini']
        summary = agent_runtime_mod._collect_trailing_reply_text(lines)
        assert summary == 'LIVE2-OK-gemini'

    def test_stops_at_the_dispatcher_seed_line(self):
        lines = ['> Ron: say hi', 'LIVE', '2-OK-gemini']
        summary = agent_runtime_mod._collect_trailing_reply_text(lines)
        assert summary == 'LIVE2-OK-gemini'

    def test_single_chunk_reply_unaffected(self):
        """The common case (one delta, or Claude's one-block-per-message
        shape) must behave exactly as before."""
        summary = agent_runtime_mod._collect_trailing_reply_text(['just one line'])
        assert summary == 'just one line'

    def test_empty_log_lines_returns_empty_string(self):
        assert agent_runtime_mod._collect_trailing_reply_text([]) == ''
        assert agent_runtime_mod._collect_trailing_reply_text(None) == ''


class TestAgentRoutesSummaryUsesTheSameReconstruction:
    """The two real call sites (`_last_reply_text`, `_log_agent_completion_body`)
    must both delegate to the fixed helper, not just the helper existing in
    isolation — that was the actual live-observed failure surface."""

    def test_last_reply_text_reconstructs_a_delta_chunked_reply(self):
        from mc.blueprints import agent_routes as ar
        session = {'log_lines': ['LIVE', '2-OK-gemini']}
        assert ar._last_reply_text(session) == 'LIVE2-OK-gemini'
