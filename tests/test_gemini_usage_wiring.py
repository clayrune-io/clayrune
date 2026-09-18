"""W4 item 4: usage/token figures for Gemini.

`GeminiRuntime.parse_event`'s TURN_END branch always read real token counts
off the `result` event's `stats` object (live-confirmed shape:
`{"total_tokens":N,"input_tokens":N,"output_tokens":N,...}`, same fixture
family as tests/fixtures/gemini_live_stream_2026-09-18.jsonl) — but
`_read_stream` never stored that payload anywhere, so `session['usage']`
stayed unset and `capabilities().emits_usage` was `False`: an honest
description of this bug, not of the CLI. Live-verified after the fix (real
`GeminiRuntime().dispatch()`): `session['usage']` populated with
`total_tokens`/`input_tokens`/`output_tokens` matching the CLI's own report.
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.agent_runtime import GeminiRuntime, SessionHandle  # noqa: E402


class _FakeProc:
    def __init__(self, lines, rc=0, pid=424242):
        self.stdout = iter(lines)
        self._rc = rc
        self.pid = pid

    def wait(self):
        return self._rc

    def poll(self):
        return self._rc


def _drive(lines):
    proc = _FakeProc(lines, rc=0)
    session = {'log_lines': [], 'proc': proc}
    handle = SessionHandle(
        mc_session_id='sid-1', provider='gemini', mode='A',
        project_path='/p', project_id='proj1', session_dict=session,
        meta={'callbacks': {}},
    )
    GeminiRuntime()._read_stream(proc, handle)
    return session


class TestGeminiUsageWiring:
    def test_turn_end_stores_usage_on_the_session(self):
        stats = {'total_tokens': 34302, 'input_tokens': 34295, 'output_tokens': 7}
        lines = [
            json.dumps({'type': 'init', 'session_id': 's1'}) + '\n',
            json.dumps({'type': 'message', 'role': 'assistant',
                       'content': 'hi', 'delta': True}) + '\n',
            json.dumps({'type': 'result', 'status': 'success', 'stats': stats}) + '\n',
        ]
        session = _drive(lines)
        assert session.get('usage') == stats

    def test_capabilities_emits_usage_is_true(self):
        caps = GeminiRuntime().capabilities()
        assert caps.emits_usage is True
        # Unaffected by this fix — the CLI genuinely never emits either.
        assert caps.emits_cost is False
        assert caps.emits_num_turns is False

    def test_no_result_event_leaves_usage_unset_not_zeroed(self):
        """'unknown stays unknown' — a turn that errors before any `result`
        event must not report a fabricated zero-usage figure."""
        lines = [
            json.dumps({'type': 'init', 'session_id': 's1'}) + '\n',
            json.dumps({'type': 'message', 'role': 'assistant',
                       'content': 'partial', 'delta': True}) + '\n',
        ]
        session = _drive(lines)
        assert 'usage' not in session
