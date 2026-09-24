"""Smoke tests for all non-claude AgentRuntime subclasses.

Tests verify:
1. build_command() output matches each CLI's documented invocation
2. parse_event() correctly normalizes each provider's JSONL/text output
3. capabilities() returns the correct flags for each provider
4. health_check() works without a live binary (not-installed path)
5. Registry: all 7 providers are registered at import time

These tests are standalone — no server.py, Flask, or live binary required.
Providers not installed on this machine are tested via the not-installed path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import agent_runtime
from mc.agent_runtime import (
    EventType,
    CodexRuntime,
    OpenCodeRuntime,
    GooseRuntime,
    AiderRuntime,
    KiroRuntime,
    GeminiRuntime,
)


def _write_codex_rollout(root, thread_id, cwd, messages, dt='2026-09-07T12-53-28'):
    """Write a rollout fixture in Codex's REAL on-disk layout.

    Layout + record shapes verified live against codex-cli 0.153.4 (today's
    installed version — the audit's numbers were taken against 0.151.0, so
    this was re-probed rather than trusted; see
    docs/research/CODEX_PARITY_AUDIT.md §0): line 1 is always a `session_meta`
    record carrying `cwd`; message turns are `response_item`/`message` records
    with a `role` and a `content[]` of `{type, text}` blocks — a real rollout
    was inspected byte-for-byte (`codex exec --json` run in a scratch dir) to
    confirm both.

    `messages` is [(role, text), ...]; only 'user'/'assistant' roles matter to
    list_sessions(), 'developer' rows exercise the non-user-role skip (Codex
    interleaves plugin/hook preambles as message turns the same as a real
    reply, unlike Claude's separate content-block shape).
    """
    date = dt.split('T')[0]
    y, m, d = date.split('-')
    day_dir = root / y / m / d
    day_dir.mkdir(parents=True, exist_ok=True)
    f = day_dir / f'rollout-{dt}-{thread_id}.jsonl'
    lines = [json.dumps({
        'timestamp': f'{dt}Z', 'ordinal': 0, 'type': 'session_meta',
        'payload': {'session_id': thread_id, 'id': thread_id, 'cwd': cwd,
                    'originator': 'codex_exec', 'cli_version': '0.153.4',
                    'source': 'exec'},
    })]
    for i, (role, text) in enumerate(messages, start=1):
        block_type = 'input_text' if role == 'user' else 'output_text'
        lines.append(json.dumps({
            'timestamp': f'{dt}Z', 'ordinal': i, 'type': 'response_item',
            'payload': {'type': 'message', 'id': f'item_{i}', 'role': role,
                        'content': [{'type': block_type, 'text': text}]},
        }))
    f.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return f


# ─────────────────────────────────────────────────────────────────────────────
# Registry: all 8 providers registered
# ─────────────────────────────────────────────────────────────────────────────


def test_all_providers_registered():
    names = {r.name for r in agent_runtime.available_runtimes()}
    expected = {'claude', 'gemini', 'qwen', 'codex', 'opencode', 'goose', 'aider', 'kiro'}
    assert expected.issubset(names), f"Missing: {expected - names}"


def test_get_runtime_all_providers():
    for name in ('claude', 'gemini', 'qwen', 'codex', 'opencode', 'goose', 'aider', 'kiro'):
        rt = agent_runtime.get_runtime(name)
        assert rt.name == name


# ─────────────────────────────────────────────────────────────────────────────
# GeminiRuntime — verify session resume capability fix
# ─────────────────────────────────────────────────────────────────────────────


class TestGeminiRuntime:
    def setup_method(self):
        self.rt = GeminiRuntime()
        # Reset bin cache so resolve_binary actually searches
        self.rt._bin_cache = None

    def test_capabilities_session_resume(self):
        # gemini CLI supports --resume, so this should be True
        caps = self.rt.capabilities()
        assert caps.supports_session_resume is True, (
            "GeminiRuntime.capabilities() must set supports_session_resume=True "
            "— gemini CLI has --resume <id|latest> flag (confirmed v0.20.0)"
        )

    def test_build_command_base(self):
        # When binary not found, should still build a valid command shape
        cmd = self.rt.build_command()
        assert 'gemini' in cmd[0]
        assert '--output-format' in cmd
        assert 'stream-json' in cmd

    def test_build_command_with_extra_include_dirs(self):
        """W4/MC-947, live-verified 2026-09-18: `read_file`'s own
        `isWithinRoot` workspace check refused an attachment path outside
        the project root (this repo's real `data/uploads/`, for any project
        whose own root isn't an ancestor of it) — an agent that could not
        see a pasted image then burned turns on `run_shell_command` trying
        to inspect the binary file directly instead. `--include-directories`
        is the CLI's own documented fix; live re-test with the SAME image
        one level outside the dispatch cwd, plus this flag pointed at its
        real parent directory, made `read_file` succeed and the model
        correctly describe the image ("PURPLE ELEPHANT" in purple, matching
        the actual drawn content, not a guess)."""
        cmd = self.rt.build_command(
            extra_include_dirs=['C:/Users/levir/AppData/Local/Temp'])
        assert '--include-directories' in cmd
        idx = cmd.index('--include-directories')
        assert cmd[idx + 1] == 'C:/Users/levir/AppData/Local/Temp'

    def test_build_command_no_include_dirs_omits_the_flag(self):
        cmd = self.rt.build_command()
        assert '--include-directories' not in cmd

    def test_parse_event_empty(self):
        assert self.rt.parse_event('') is None
        assert self.rt.parse_event('\n') is None

    def test_parse_event_plain_text(self):
        ev = self.rt.parse_event('Hello from Gemini')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert ev.payload['text'] == 'Hello from Gemini'

    def test_parse_event_stream_json_content(self):
        line = json.dumps({'type': 'content', 'text': 'Hello!'})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert ev.payload['text'] == 'Hello!'

    def test_parse_event_tool_use(self):
        line = json.dumps({'type': 'tool_use', 'name': 'read_file', 'input': {'path': '/x'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TOOL_USE
        blocks = ev.payload['blocks']
        assert blocks[0]['name'] == 'read_file'

    def test_parse_event_result(self):
        line = json.dumps({'type': 'result', 'usage': {'tokens': 100}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TURN_END

    def test_parse_event_result_error_surfaces_message(self):
        # MC-931: a `result` event with status=="error" carries the CLI's
        # real reason (quota, auth, ...) in error.message. Before that fix
        # it matched the generic 'result' branch and returned TURN_END with
        # only usage/cost — the error text was read by nothing and reached
        # neither the transcript nor explain_exit_error's tail scan, so a
        # real API error (e.g. "You have exhausted your daily quota on this
        # model.") was silently dropped and the user saw a generic
        # "exited with code 1" instead.
        #
        # VENDOR_AGNOSTIC_PROGRAM.md §4 (this real MC-931 text is quota, not
        # a generic error): the event now classifies as ALLOWANCE_EXHAUSTED,
        # one step further than MC-931's own fix — a dispatch call site can
        # tell "out of allowance" apart from "auth/network failure" the same
        # way for every vendor.
        line = json.dumps({
            'type': 'result', 'status': 'error',
            'error': {'type': 'Error',
                     'message': '[API Error: You have exhausted your daily quota on this model.]'},
            'stats': {'total_tokens': 0},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ALLOWANCE_EXHAUSTED
        assert ev.payload['verified'] is False
        assert 'exhausted your daily quota' in ev.payload['raw_ref']

    def test_capabilities_mcp(self):
        assert self.rt.capabilities().supports_mcp is True

    def test_explain_exit_error_surfaces_real_line_over_guesses(self):
        # Reproduces MC-926: real CLI output has startup noise (profiler
        # dump + YOLO banner) around the actual cause. The surfaced hint
        # must contain that real line and NOT the generic "not logged in /
        # network blocked / prompt too big" guesses.
        tail = (
            "[STARTUP] profiler dump line one\n"
            "[STARTUP] profiler dump line two\n"
            "YOLO mode is enabled. All tool calls will be automatically approved.\n"
            "Loaded cached credentials.\n"
            "This account requires setting the GOOGLE_CLOUD_PROJECT or "
            "GOOGLE_CLOUD_PROJECT_ID env var (goo.gle/gemini-cli-auth-docs#workspace-gca)"
        )
        hint = self.rt.explain_exit_error(41, tail)
        assert hint is not None
        assert "GOOGLE_CLOUD_PROJECT" in hint
        assert "Common causes" not in hint
        assert "not logged in" not in hint

    def test_explain_exit_error_falls_back_to_guesses_when_no_real_line(self):
        # No usable output at all (or only noise/markers) — the generic
        # guess is the only thing left to say, so it must still appear.
        tail = "[STARTUP] profiler dump\nYOLO mode is enabled.\n[tool: call]\n"
        hint = self.rt.explain_exit_error(41, tail)
        assert hint is not None
        assert "Common causes" in hint

    def test_explain_exit_error_no_tail_falls_back_to_guesses(self):
        hint = self.rt.explain_exit_error(41, "")
        assert hint is not None
        assert "Common causes" in hint

    def test_parse_event_non_quota_error_stays_generic_error(self):
        """Contrast case for test_parse_event_result_error_surfaces_message
        above: only quota-shaped text reclassifies as ALLOWANCE_EXHAUSTED."""
        from mc.agent_runtime import EventType
        line = json.dumps({
            'type': 'result', 'status': 'error',
            'error': {'message': 'network unreachable'},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ERROR


# ─────────────────────────────────────────────────────────────────────────────
# GeminiRuntime._probe_live_quota — MC-934: the live generateContent call
# auth_probe() spends to tell "key exists" apart from "key can serve a run".
# All network I/O is mocked (urllib.request.urlopen) — no real API call, no
# real key needed for these tests.
# ─────────────────────────────────────────────────────────────────────────────


class TestGeminiProbeLiveQuota:
    def setup_method(self):
        self.rt = GeminiRuntime()

    def test_success_reports_ok_and_unknown_tier(self):
        """A 200 tells us the call went through — Google sends no rate-limit
        headers on success, so tier stays honestly 'unknown' rather than
        inventing a number nobody sent."""
        from unittest.mock import patch

        class _Resp:
            def read(self):
                return b'{}'
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with patch('mc.agent_runtime.urllib.request.urlopen', return_value=_Resp()):
            result = self.rt._probe_live_quota('fake-key')
        assert result['reachable'] is True
        assert result['ok'] is True
        assert result['quota_status'] == 'ok'
        assert result['tier'] == 'unknown'
        assert result['invalid_key'] is False

    def test_429_quota_failure_parses_quotaid_and_tier(self):
        """The exact schema MC-932 read manually off a 429 — quotaId/quotaValue
        inside a QuotaFailure detail. A FreeTier-suffixed quotaId is the only
        reliable tier signal this API exposes."""
        import io
        import urllib.error
        from unittest.mock import patch

        body = json.dumps({
            'error': {
                'code': 429, 'status': 'RESOURCE_EXHAUSTED',
                'message': 'You exceeded your current quota.',
                'details': [{
                    '@type': 'type.googleapis.com/google.rpc.QuotaFailure',
                    'violations': [{
                        'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier',
                        'quotaValue': '20',
                    }],
                }],
            },
        }).encode('utf-8')

        class _FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__('url', 429, 'quota', {}, io.BytesIO(body))
            def read(self):
                return body

        with patch('mc.agent_runtime.urllib.request.urlopen', side_effect=_FakeHTTPError()):
            result = self.rt._probe_live_quota('fake-key')
        assert result['reachable'] is True
        assert result['ok'] is False
        assert result['quota_status'] == 'exceeded'
        assert result['tier'] == 'free'
        assert result['quota_id'] == 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'
        assert result['quota_value'] == '20'
        assert result['invalid_key'] is False

    def test_invalid_key_reported_distinctly_from_quota(self):
        import io
        import urllib.error
        from unittest.mock import patch

        body = json.dumps({
            'error': {'code': 400, 'status': 'INVALID_ARGUMENT',
                     'message': 'API key not valid. Please pass a valid API key.'},
        }).encode('utf-8')

        class _FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__('url', 400, 'bad key', {}, io.BytesIO(body))
            def read(self):
                return body

        with patch('mc.agent_runtime.urllib.request.urlopen', side_effect=_FakeHTTPError()):
            result = self.rt._probe_live_quota('fake-key')
        assert result['invalid_key'] is True
        assert result['quota_status'] == 'unknown'

    def test_network_failure_is_unreachable_not_invalid(self):
        """A DNS/timeout failure must not be reported as an invalid key — that
        would tell the user to redo something that was never the problem."""
        from unittest.mock import patch

        with patch('mc.agent_runtime.urllib.request.urlopen',
                   side_effect=OSError('getaddrinfo failed')):
            result = self.rt._probe_live_quota('fake-key')
        assert result['reachable'] is False
        assert result['invalid_key'] is False
        assert result['ok'] is False


# ─────────────────────────────────────────────────────────────────────────────
# _last_real_error_line — shared helper used by every provider's fallback
# ─────────────────────────────────────────────────────────────────────────────


class TestLastRealErrorLine:
    def test_skips_noise_and_returns_real_line(self):
        tail = (
            "[STARTUP] boot\n"
            "YOLO mode is enabled. All tool calls will be automatically approved.\n"
            "the real error text"
        )
        assert agent_runtime._last_real_error_line(tail) == "the real error text"

    def test_skips_bracketed_status_markers(self):
        tail = "the real error text\n[tool: call]\n[tool: call result]\n"
        assert agent_runtime._last_real_error_line(tail) == "the real error text"

    def test_empty_or_all_noise_returns_none(self):
        assert agent_runtime._last_real_error_line("") is None
        assert agent_runtime._last_real_error_line(
            "[STARTUP] a\nYOLO mode is enabled.\n[tool: call]\n"
        ) is None

    def test_skips_dispatcher_seed_line(self):
        # MC-931: `_dispatch_via_runtime` seeds a fresh session's log_lines
        # with "> {user_label}: {task}" so the chat shows the prompt before
        # the process produces any output. A turn that errors before
        # printing anything else left that echoed task as the last
        # non-bracketed line — the observed bug: a hint reading
        # "> Ron: Reply with exactly one short sentence..." instead of the
        # CLI's real error.
        tail = (
            "> Ron: Reply with exactly one short sentence confirming you "
            "are running. Do not use any tools.\n"
            "[gemini exited with code 1]"
        )
        assert agent_runtime._last_real_error_line(tail) is None

    def test_seed_line_skip_does_not_eat_a_real_error_after_it(self):
        tail = (
            "> Ron: Reply with one sentence.\n"
            "the real error text"
        )
        assert agent_runtime._last_real_error_line(tail) == "the real error text"

    def test_skips_punctuation_only_trailing_lines(self):
        # Live 2026-09-19 (qwen, stale OPENAI_BASE_URL -> gateway 404): the
        # error text was a multi-line HTML page wrapped in `[API Error: ... ]`,
        # so the last physical line was a bare `]` and the chat read
        # "Qwen Code error: ]".
        tail = "the real error text\n]\n}\n)\n"
        assert agent_runtime._last_real_error_line(tail) == "the real error text"
        assert agent_runtime._last_real_error_line("]\n[qwen exited with code 1]") is None


# Shape of the real qwen-code 0.23.4 `result` envelope captured 2026-09-19
# (host names generic): is_error true, error.message is a whole HTML page
# wrapped in `[API Error: 404 ... ]`, CRLF line breaks inside.
_QWEN_404_HTML = (
    '[API Error: 404 <!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML 2.0//EN">\r\n<html>\r\n'
    '<head><title>404 Not Found</title></head>\r\n<body>\r\n'
    '<center><h1>404 Not Found</h1></center>\r\n Sorry for the inconvenience.<br/>\r\n'
    '<table>\r\n<tr>\r\n<td>URL:</td>\r\n'
    '<td>https://example.invalid:28443/chat/completions</td>\r\n'
    '</tr>\r\n</table>\r\n<hr/>Powered by Tengine<hr><center>tengine</center>\r\n'
    '</body>\r\n</html>\r\n]')


class TestQwenErrorReachesTheChat:
    def _run_reader(self, stream_lines):
        import io
        import json as _json

        class _Proc:
            stdout = io.StringIO('\n'.join(_json.dumps(m) for m in stream_lines) + '\n')

            def wait(self):
                return 1
        proc = _Proc()
        session = {'log_lines': ['> Ron: hi'], 'proc': proc, 'status': 'running'}
        handle = agent_runtime.SessionHandle(
            mc_session_id='x', provider='qwen', mode='A', project_path='.',
            project_id='p', session_dict=session)
        agent_runtime._mode_a_reader(proc, handle, agent_runtime.QwenRuntime())
        return session['log_lines']

    def test_html_404_result_surfaces_status_and_url_not_a_bare_bracket(self):
        lines = self._run_reader([{
            'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
            'num_turns': 1, 'usage': {'input_tokens': 0, 'output_tokens': 0},
            'error': {'message': _QWEN_404_HTML}}])
        hint = lines[-1]
        assert hint.startswith('[hint] Qwen Code error: ')
        assert hint != '[hint] Qwen Code error: ]'
        assert '404' in hint and 'Not Found' in hint
        assert 'example.invalid:28443/chat/completions' in hint
        assert '<' not in hint and '\n' not in hint and '\r' not in hint

    def test_plain_error_text_is_left_intact(self):
        lines = self._run_reader([{
            'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
            'error': {'message': 'No auth type is selected. Use `--auth-type` <x>.'}}])
        assert lines[-1].endswith('No auth type is selected. Use `--auth-type` <x>.')

    def test_flatten_error_text_caps_length_and_keeps_non_html_angles(self):
        assert agent_runtime._flatten_error_text('expected <int> got str') == 'expected <int> got str'
        assert len(agent_runtime._flatten_error_text('x ' * 1000)) <= 603


# ─────────────────────────────────────────────────────────────────────────────
# CodexRuntime
# ─────────────────────────────────────────────────────────────────────────────


class TestCodexRuntime:
    def setup_method(self):
        self.rt = CodexRuntime()
        self.rt._bin_cache = None
        self.rt._npx_fallback = False

    def test_build_command_basic(self):
        """codex exec --json --dangerously-bypass-approvals-and-sandbox"""
        self.rt._bin_cache = 'codex'
        self.rt._npx_fallback = False
        cmd = self.rt.build_command()
        assert 'exec' in cmd
        assert '--json' in cmd
        assert '--dangerously-bypass-approvals-and-sandbox' in cmd

    def test_build_command_with_model(self):
        self.rt._bin_cache = 'codex'
        cmd = self.rt.build_command(model='o4-mini')
        assert '-m' in cmd
        idx = cmd.index('-m')
        assert cmd[idx + 1] == 'o4-mini'

    def test_build_command_resume_last(self):
        """codex exec resume --last --json"""
        self.rt._bin_cache = 'codex'
        cmd = self.rt.build_command(resume_id='last')
        assert 'exec' in cmd
        assert 'resume' in cmd
        assert '--last' in cmd
        assert '--json' in cmd

    def test_build_command_resume_specific_id(self):
        """codex exec resume <SESSION_ID> --json"""
        self.rt._bin_cache = 'codex'
        session_id = '019e4bff-aa7d-77f1-bf2c-7e7367deb2c4'
        cmd = self.rt.build_command(resume_id=session_id)
        assert session_id in cmd
        assert 'resume' in cmd

    def test_build_command_npx_fallback(self, monkeypatch):
        """When binary not found, uses npx @openai/codex prefix.

        resolve_binary() re-probes every call while sitting on the npx
        sentinel (2026-09-24 fix), so the probe itself must be mocked here
        rather than just pre-seeding the cache -- otherwise this test would
        pass by relying on the exact sticky-cache bug it should catch.
        """
        import shutil
        from pathlib import Path
        monkeypatch.setattr(shutil, 'which', lambda n: 'npx' if n.startswith('npx') else None)
        monkeypatch.setattr(agent_runtime, '_npm_global_bin_dirs', lambda: [])
        monkeypatch.setattr(Path, 'exists', lambda self: False)
        self.rt._bin_cache = None
        self.rt._npx_fallback = False
        cmd = self.rt.build_command()
        assert cmd[0] == 'npx'
        assert '@openai/codex' in cmd
        assert '--json' in cmd

    # ── mc:question wiring (parity audit item 4) ────────────────────────────
    # The universal context block TELLS Codex to emit the mc:question fence
    # (_build_agent_context), but nothing on dispatch() ever appended the
    # protocol text that explains its shape — copies GeminiRuntime.dispatch's
    # own with_mc_tool_protocol() call. End-to-end proof that a fence is
    # actually PARSED lives in test_runtime_completion_log.py (drives the
    # real _mode_a_reader); these pin that CodexRuntime feeds it one.

    def test_dispatch_appends_mc_tool_protocol_to_system_prompt(self, monkeypatch):
        captured = {}

        def _fake_mode_a_dispatch(*args, **kwargs):
            captured['kwargs'] = kwargs
            return 'HANDLE'

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', _fake_mode_a_dispatch)
        self.rt._bin_cache = 'codex'
        result = self.rt.dispatch(project_path='/p', task='do X',
                                  system_prompt='MEMORY STUFF', session_dict={})
        assert result == 'HANDLE'
        stashed = captured['kwargs']['system_prompt']
        assert agent_runtime.MC_TOOL_PROTOCOL_PROMPT in stashed
        assert 'MEMORY STUFF' in stashed

    def test_dispatch_appends_protocol_even_with_empty_system_prompt(self, monkeypatch):
        """Incognito dispatch (_dispatch_via_runtime skips context entirely)
        must still let Codex ask a question — matches GeminiRuntime, which
        calls with_mc_tool_protocol() unconditionally."""
        captured = {}
        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch',
                            lambda *a, **kw: captured.update(kw) or 'HANDLE')
        self.rt._bin_cache = 'codex'
        self.rt.dispatch(project_path='/p', task='do X', system_prompt='',
                         session_dict={})
        assert agent_runtime.MC_TOOL_PROTOCOL_PROMPT in captured['system_prompt']

    def test_write_followup_reapplies_protocol_to_stashed_system_prompt(self, monkeypatch):
        """agent_routes.py's /agent/send and /agent/interrupt refresh
        `_system_prompt` from a bare project context ahead of every followup
        (persona-continuity fix) — that rebuild knows nothing about the
        emulated question protocol, so write_followup must re-wrap whatever
        is CURRENTLY stashed, not just what dispatch() set on turn 1."""
        class _FakeStdin:
            def write(self, s): pass
            def close(self): pass

        class _FakeProc:
            stdin = _FakeStdin()
            stdout = iter([])
            def wait(self): return 0
            def poll(self): return 0

        monkeypatch.setattr(agent_runtime.subprocess, 'Popen',
                            lambda *a, **kw: _FakeProc())
        self.rt._bin_cache = 'codex'
        session = {'_system_prompt': 'REFRESHED PLAIN CONTEXT, NO PROTOCOL',
                  'log_lines': [], 'proc': None}
        handle = agent_runtime.SessionHandle(
            mc_session_id='s1', provider='codex', mode='A',
            project_path='/p', project_id='p1', session_dict=session,
            meta={'callbacks': {}})
        self.rt.write_followup(handle, 'go on')
        assert agent_runtime.MC_TOOL_PROTOCOL_PROMPT in session['_system_prompt']
        assert 'REFRESHED PLAIN CONTEXT, NO PROTOCOL' in session['_system_prompt']

    def test_parse_event_thread_started(self):
        """thread.started → INIT with thread_id"""
        line = json.dumps({'type': 'thread.started',
                           'thread_id': '019e4bff-aa7d-77f1-bf2c-7e7367deb2c4'})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.INIT
        assert ev.payload['thread_id'] == '019e4bff-aa7d-77f1-bf2c-7e7367deb2c4'

    def test_parse_event_turn_started_suppressed(self):
        """turn.started → None (suppressed internal event)"""
        line = json.dumps({'type': 'turn.started'})
        ev = self.rt.parse_event(line)
        assert ev is None

    def test_parse_event_error(self):
        """error → EventType.ERROR"""
        line = json.dumps({'type': 'error',
                           'message': 'model not supported'})
        ev = self.rt.parse_event(line, mc_session_id='abc')
        assert ev is not None
        assert ev.type == EventType.ERROR
        assert 'model not supported' in ev.payload['text']

    def test_parse_event_turn_failed(self):
        """turn.failed → EventType.ERROR"""
        line = json.dumps({'type': 'turn.failed',
                           'error': {'message': 'API error 400'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ERROR
        assert 'API error 400' in ev.payload['text']

    def test_parse_event_turn_completed(self):
        """turn.completed → TURN_END"""
        line = json.dumps({'type': 'turn.completed',
                           'usage': {'input_tokens': 10, 'output_tokens': 5}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TURN_END
        assert ev.payload['usage'] == {'input_tokens': 10, 'output_tokens': 5}

    # ── codex 0.151 item schema ────────────────────────────────────────────
    # 0.133 sent item.type='message' with a content[] array; 0.151 sends
    # 'agent_message' with a flat text, and shell calls as 'command_execution'.
    # None of those matched the old branches, so every event returned None and
    # the Mode-A reader logged the raw JSONL into the chat pane.

    def test_parse_event_agent_message_flat_text(self):
        line = json.dumps({'type': 'item.completed',
                           'item': {'id': 'item_0', 'type': 'agent_message',
                                    'text': 'Hi Ron.'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert ev.payload['text'] == 'Hi Ron.'

    def test_parse_event_command_execution_keeps_output_out_of_chat(self):
        """aggregated_output can be a whole file; it belongs in the tool block."""
        line = json.dumps({'type': 'item.completed',
                           'item': {'id': 'item_1', 'type': 'command_execution',
                                    'command': 'powershell -Command ls',
                                    'aggregated_output': 'X' * 5000,
                                    'exit_code': 0, 'status': 'completed'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TOOL_USE
        block = ev.payload['blocks'][0]
        assert block['name'] == 'shell'
        assert block['input']['command'] == 'powershell -Command ls'
        assert block['input']['exit_code'] == 0
        assert len(block['output']) == 5000
        assert 'text' not in ev.payload

    def test_parse_event_item_started_command_is_tool_use(self):
        line = json.dumps({'type': 'item.started',
                           'item': {'id': 'item_1', 'type': 'command_execution',
                                    'command': 'echo hi', 'status': 'in_progress'}})
        ev = self.rt.parse_event(line)
        assert ev is not None and ev.type == EventType.TOOL_USE

    def test_parse_event_reasoning_is_thinking(self):
        line = json.dumps({'type': 'item.completed',
                           'item': {'id': 'r0', 'type': 'reasoning',
                                    'text': 'considering options'}})
        ev = self.rt.parse_event(line)
        assert ev is not None and ev.type == EventType.THINKING

    def test_parse_event_unknown_item_type_never_returns_none(self):
        """Codex adds item types between releases; an unknown one must degrade
        to a readable tool line, not fall through and get logged as raw JSON."""
        line = json.dumps({'type': 'item.completed',
                           'item': {'id': 'z', 'type': 'some_future_item',
                                    'detail': 'x'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TOOL_USE
        assert ev.payload['blocks'][0]['name'] == 'some_future_item'

    def test_parse_event_item_completed_message(self):
        """item.completed with message → ASSISTANT_TEXT"""
        line = json.dumps({
            'type': 'item.completed',
            'item': {
                'type': 'message',
                'content': [{'type': 'output_text', 'text': 'Hello from Codex!'}],
            },
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert 'Hello from Codex!' in ev.payload['text']

    def test_parse_event_plain_text_fallback(self):
        """Non-JSON lines → ASSISTANT_TEXT"""
        ev = self.rt.parse_event('Reading prompt from stdin...')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT

    def test_parse_event_empty(self):
        assert self.rt.parse_event('') is None

    def test_capabilities(self):
        caps = self.rt.capabilities()
        assert caps.name == 'codex'
        assert caps.supports_session_resume is True
        assert caps.supports_mcp is True
        # [live, codex-cli 0.153.4] Both corrected by the parity audit (§2):
        # no ExitPlanMode-equivalent anchor exists, so a live plan-approval
        # UI had nothing behind it; `turn.completed` carries `usage` only,
        # never a cost figure. These used to assert True — pinning the
        # mis-declaration in place instead of catching it (audit §3).
        assert caps.supports_plan_mode is False
        assert caps.emits_cost is False
        assert caps.context_injection == 'file'
        assert caps.context_file_name == 'AGENTS.md'
        # W4/MC-947 (2026-09-18), offline proof (Codex out of allowance):
        # dispatch() calls with_mc_tool_protocol() and runs through the
        # SAME shared _mode_a_reader turn-end mc:question scan Qwen uses
        # (live-verified there) — no Codex-specific divergence in that path.
        assert caps.supports_ask_user_question is True

    def test_health_check_not_installed(self, monkeypatch):
        """When neither binary nor npx is found, installed=False.

        resolve_binary() probes real install dirs as well as PATH (the native
        OpenAI installer puts codex.exe somewhere `which` can't see when the
        server's PATH is stale), so a "nothing installed" box must stub the
        filesystem probe too — patching shutil.which alone no longer expresses it.
        """
        import shutil
        from pathlib import Path
        monkeypatch.setattr(shutil, 'which', lambda _: None)
        monkeypatch.setattr(Path, 'exists', lambda self: False)
        monkeypatch.setattr(agent_runtime, '_npm_global_bin_dirs', lambda: [])
        self.rt._bin_cache = None
        self.rt._npx_fallback = False
        hs = self.rt.health_check()
        assert hs.installed is False
        assert hs.auth_state.status == 'not_installed'
        assert 'npm install' in hs.install_hint

    def test_resolve_binary_finds_native_windows_install(self, monkeypatch, tmp_path):
        """The native OpenAI installer path is found even when PATH is stale.

        Regression: codex installs to %LOCALAPPDATA%\\Programs\\OpenAI\\Codex\\bin
        and appends that dir to the registry user PATH. A server process started
        before the install never inherits it, so shutil.which('codex') fails and
        MC reported a working codex as not installed.
        """
        import shutil
        if sys.platform != 'win32':
            pytest.skip('windows-only install layout')
        native = tmp_path / 'Programs' / 'OpenAI' / 'Codex' / 'bin'
        native.mkdir(parents=True)
        (native / 'codex.exe').write_text('')
        monkeypatch.setattr(shutil, 'which', lambda _: None)
        monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
        monkeypatch.setattr(agent_runtime, '_npm_global_bin_dirs', lambda: [])
        self.rt._bin_cache = None
        self.rt._npx_fallback = False
        assert self.rt.resolve_binary() == native / 'codex.exe'
        assert self.rt._npx_fallback is False

    def test_auth_state_ok_from_login_status_probe(self, monkeypatch):
        """`codex login status` is the primary signal: exit 0 means logged
        in, whatever mechanism is behind it."""
        monkeypatch.delenv('CODEX_API_KEY', raising=False)
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)
        monkeypatch.setattr(
            agent_runtime.subprocess, 'run',
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0] if a else [], returncode=0,
                stdout='Logged in using ChatGPT\n', stderr=''))
        assert self.rt._codex_auth_state() == ('ok', 'Logged in using ChatGPT')

    def test_auth_state_not_logged_in_without_credentials(self, monkeypatch, tmp_path):
        """Regression (2026-09-22): probe says not logged in, no
        CODEX_API_KEY, and a bare OPENAI_API_KEY set — must still report
        not_logged_in. This codex CLI never sends that env var as bearer
        auth (live-verified: `codex exec` fails "Missing bearer or basic
        authentication in header", not an invalid-key error), so trusting
        its mere presence hid real 401s behind a false "signed in" badge.
        """
        monkeypatch.setenv('OPENAI_API_KEY', 'sk-not-actually-usable-by-codex')
        monkeypatch.delenv('CODEX_API_KEY', raising=False)
        monkeypatch.setenv('USERPROFILE', str(tmp_path))
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setattr(
            agent_runtime.subprocess, 'run',
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0] if a else [], returncode=1,
                stdout='Not logged in\n', stderr=''))
        assert self.rt._codex_auth_state() == ('not_logged_in', None)

    def test_auth_state_env_codex_api_key_wins_when_probe_says_not_logged_in(self, monkeypatch, tmp_path):
        """CODEX_API_KEY is a real, separate auth path `codex login status`
        doesn't see (live-verified 2026-09-22: status still prints "Not
        logged in" with CODEX_API_KEY set, but `codex exec` sends it as
        bearer auth — a bad key gets `invalid_api_key` from the server, not
        "missing bearer"), so it must still win even when the probe says no.
        """
        monkeypatch.setenv('CODEX_API_KEY', 'sk-real-codex-key')
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)
        monkeypatch.setenv('USERPROFILE', str(tmp_path))
        monkeypatch.setattr(
            agent_runtime.subprocess, 'run',
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0] if a else [], returncode=1,
                stdout='Not logged in\n', stderr=''))
        assert self.rt._codex_auth_state() == ('ok', 'env:CODEX_API_KEY')

    def test_auth_state_falls_back_to_auth_json_when_probe_cannot_run(self, monkeypatch, tmp_path):
        """If the login-status probe itself can't execute (binary missing,
        spawn error, timeout), fall back to reading ~/.codex/auth.json
        directly — the detection this probe now takes priority over."""
        monkeypatch.delenv('CODEX_API_KEY', raising=False)
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)
        cdir = tmp_path / '.codex'
        cdir.mkdir()
        (cdir / 'auth.json').write_text(json.dumps({
            'OPENAI_API_KEY': None,
            'tokens': {'access_token': 'a', 'refresh_token': 'r'},
        }), encoding='utf-8')
        monkeypatch.setenv('USERPROFILE', str(tmp_path))
        monkeypatch.setenv('HOME', str(tmp_path))

        def _raise(*a, **k):
            raise FileNotFoundError('codex binary not found')
        monkeypatch.setattr(agent_runtime.subprocess, 'run', _raise)
        assert self.rt._codex_auth_state() == ('ok', 'chatgpt oauth')

    def test_health_check_surfaces_missing_codex_login(self, monkeypatch):
        monkeypatch.setattr(self.rt, 'resolve_binary', lambda: Path('/usr/local/bin/codex'))
        monkeypatch.setattr(
            agent_runtime.subprocess, 'run',
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0] if a else [], returncode=0,
                stdout='codex-cli 0.153.0', stderr=''))
        monkeypatch.setattr(self.rt, '_codex_auth_state', lambda: ('not_logged_in', None))
        assert self.rt.health_check().auth_state.status == 'not_logged_in'

    def test_health_check_npx_fallback_not_counted_as_installed(self, monkeypatch):
        """F10 (clean-VM run 2026-09-18): a machine with npm but no codex CLI
        at all showed installed=True / "not signed in" with an empty
        binary_path in the first-run chooser, because the npx fallback (a
        per-dispatch `npx --yes @openai/codex`, never a persistent install)
        counted as installed. It must not: the chooser's Install button is
        how a user notices codex was never actually installed. Dispatch is
        unaffected — it calls _cmd_prefix()/resolve_binary() directly, not
        this flag.

        resolve_binary() re-probes every call while sitting on the npx
        sentinel (2026-09-24 fix), so the probe itself must be mocked here
        rather than just pre-seeding the cache.
        """
        import shutil
        from pathlib import Path
        monkeypatch.setattr(shutil, 'which', lambda n: 'npx' if n.startswith('npx') else None)
        monkeypatch.setattr(agent_runtime, '_npm_global_bin_dirs', lambda: [])
        monkeypatch.setattr(Path, 'exists', lambda self: False)
        self.rt._bin_cache = None
        self.rt._npx_fallback = False
        monkeypatch.setattr(
            agent_runtime.subprocess, 'run',
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0] if a else [], returncode=0,
                stdout='codex-cli 0.153.0', stderr=''))
        monkeypatch.setattr(self.rt, '_codex_auth_state', lambda: ('not_logged_in', None))
        hs = self.rt.health_check()
        assert hs.installed is False
        assert hs.binary_path is None

    def test_npx_fallback_uses_absolute_path(self, monkeypatch):
        """npx is npx.cmd on Windows; CreateProcess can't launch it by bare name.

        Regression: the bare 'npx' fallback died with WinError 2, which surfaced
        as a bogus "CLI not found" instead of running the package.
        """
        import shutil
        from pathlib import Path
        monkeypatch.setattr(Path, 'exists', lambda self: False)
        monkeypatch.setattr(agent_runtime, '_npm_global_bin_dirs', lambda: [])
        monkeypatch.setattr(
            shutil, 'which',
            lambda n: r'C:\npm\npx.cmd' if n.startswith('npx') else None)
        self.rt._bin_cache = None
        self.rt._npx_fallback = False
        self.rt._npx_path = ''
        assert self.rt.resolve_binary() is None
        assert self.rt._npx_fallback is True
        assert self.rt._cmd_prefix() == [r'C:\npm\npx.cmd', '--yes', '@openai/codex']

    def test_resolve_binary_reprobes_after_npx_fallback_when_binary_appears(self, monkeypatch, tmp_path):
        """Regression 2026-09-24: '__npx__' must not be a sticky cached miss.

        Root cause (clean-VM run, Ron): npm installs run in the background
        after health_check() already cached _bin_cache='__npx__' (npx was
        present, codex wasn't yet). Every later resolve_binary() call
        short-circuited on that sentinel and returned None forever, even
        after %APPDATA%\\npm\\codex.cmd existed on disk -- so
        /api/agent/providers kept reporting installed=False, binary_path=None
        after a real, successful npm install.
        """
        import shutil
        if sys.platform != 'win32':
            pytest.skip('windows-only install layout')
        monkeypatch.setattr(agent_runtime, '_npm_global_bin_dirs', lambda: [])
        # Isolate every fixed candidate dir resolve_binary() probes so this
        # test can't see the real dev machine's own codex/npx installs.
        appdata = tmp_path / 'AppData' / 'Roaming'
        local_appdata = tmp_path / 'AppData' / 'Local'
        monkeypatch.setenv('APPDATA', str(appdata))
        monkeypatch.setenv('LOCALAPPDATA', str(local_appdata))
        monkeypatch.setenv('USERPROFILE', str(tmp_path))

        # First probe: neither codex nor npx present -> real miss, never cached.
        monkeypatch.setattr(shutil, 'which', lambda _: None)
        self.rt._bin_cache = None
        self.rt._npx_fallback = False
        assert self.rt.resolve_binary() is None
        assert self.rt._bin_cache is None

        # npx shows up (npm is installed) -> falls back to the npx sentinel.
        monkeypatch.setattr(
            shutil, 'which',
            lambda n: r'C:\npm\npx.cmd' if n.startswith('npx') else None)
        assert self.rt.resolve_binary() is None
        assert self.rt._bin_cache == '__npx__'
        assert self.rt._npx_fallback is True

        # codex gets installed mid-session via `npm install -g @openai/codex`.
        npm_dir = appdata / 'npm'
        npm_dir.mkdir(parents=True)
        codex_cmd = npm_dir / 'codex.cmd'
        codex_cmd.write_text('')

        # Must re-probe and find the real binary, not trust the stale sentinel.
        result = self.rt.resolve_binary()
        assert result == codex_cmd
        assert self.rt._bin_cache == str(codex_cmd)
        assert self.rt._npx_fallback is False

    def test_transcript_path_missing_session(self):
        assert self.rt.transcript_path('/some/path', '') is None

    # ── real rollout layout (docs/research/CODEX_PARITY_AUDIT.md §0) ──────────
    # `test_transcript_path_missing_session` above only asserts the empty-id
    # case — the audit's own point: that single missing positive assertion is
    # why `transcript_path()` pointed at a directory layout that has NEVER
    # existed (`~/.codex/sessions/<thread_id>/transcript.jsonl`) for months
    # without a red test. These exercise the REAL layout end to end.

    def test_transcript_path_resolves_real_rollout_layout(self, tmp_path, monkeypatch):
        root = tmp_path / 'sessions'
        thread_id = '01a07d6e-cc73-7011-bb97-f2b08136fb87'
        cwd = str(tmp_path / 'project')
        f = _write_codex_rollout(root, thread_id, cwd,
                                 [('user', 'hi'), ('assistant', 'hello')])
        monkeypatch.setattr(agent_runtime, '_CODEX_HOME', root)
        assert self.rt.transcript_path(cwd, thread_id) == f
        # No project_path given: still resolves off the thread id alone (the
        # id is embedded in the filename — no file content needs reading).
        assert self.rt.transcript_path('', thread_id) == f

    def test_transcript_path_scoped_by_cwd_rejects_wrong_project(self, tmp_path, monkeypatch):
        """Same thread id asked for from a DIFFERENT project's path must not
        silently hand back another project's transcript."""
        root = tmp_path / 'sessions'
        thread_id = 'thread-a'
        _write_codex_rollout(root, thread_id, str(tmp_path / 'project_a'), [('user', 'hi')])
        monkeypatch.setattr(agent_runtime, '_CODEX_HOME', root)
        assert self.rt.transcript_path(str(tmp_path / 'project_b'), thread_id) is None

    def test_transcript_path_missing_thread_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent_runtime, '_CODEX_HOME', tmp_path / 'sessions')
        assert self.rt.transcript_path(str(tmp_path), 'no-such-thread') is None

    def test_list_sessions_scopes_by_cwd_and_parses_turns(self, tmp_path, monkeypatch):
        root = tmp_path / 'sessions'
        proj = str(tmp_path / 'myproj')
        other = str(tmp_path / 'otherproj')
        _write_codex_rollout(
            root, 'thread-1', proj,
            [('developer', 'plugin hook noise — not a user turn'),
             ('user', 'first question'),
             ('assistant', 'first answer'),
             ('user', 'second question'),
             ('assistant', 'second answer')],
            dt='2026-09-07T08-00-00')
        _write_codex_rollout(root, 'thread-2', other, [('user', 'unrelated project chat')],
                             dt='2026-09-07T09-00-00')
        monkeypatch.setattr(agent_runtime, '_CODEX_HOME', root)
        rows = self.rt.list_sessions(proj, limit=5)
        assert len(rows) == 1, rows
        row = rows[0]
        assert row['session_id'] == 'thread-1'
        assert row['turns'] == 2
        assert row['first_user'] == 'first question'
        assert row['last_user'] == 'second question'

    def test_list_sessions_sorts_newest_first_and_respects_limit(self, tmp_path, monkeypatch):
        root = tmp_path / 'sessions'
        proj = str(tmp_path / 'myproj')
        import os
        import time as _t
        f_old = _write_codex_rollout(root, 'older', proj, [('user', 'old chat')],
                                     dt='2026-09-01T08-00-00')
        f_new = _write_codex_rollout(root, 'newer', proj, [('user', 'new chat')],
                                     dt='2026-09-07T08-00-00')
        # Filesystem mtime, not the encoded filename timestamp, is what
        # list_sessions() sorts on — pin both explicitly so the assertion
        # can't flake on two writes landing within the same mtime tick.
        now = _t.time()
        os.utime(f_old, (now - 3600, now - 3600))
        os.utime(f_new, (now, now))
        monkeypatch.setattr(agent_runtime, '_CODEX_HOME', root)
        rows = self.rt.list_sessions(proj, limit=1)
        assert len(rows) == 1
        assert rows[0]['session_id'] == 'newer'

    def test_list_sessions_no_project_path_returns_empty(self):
        assert self.rt.list_sessions('', limit=5) == []

    def test_live_probe_events(self):
        """Live probe: codex exec --json emits thread.started as first event.

        This test runs only when npx is available and is marked as slow.
        It verifies the actual JSONL format from the running binary.
        """
        import shutil
        import os
        if os.environ.get('MC_LIVE_CLI_TESTS') != '1':
            pytest.skip('launches a real codex session; set MC_LIVE_CLI_TESTS=1')
        if not shutil.which('npx'):
            pytest.skip('npx not available on this machine')

        # We just verify the first line (thread.started) without needing auth
        import subprocess
        rt = CodexRuntime()
        rt._bin_cache = None
        cmd = rt._cmd_prefix() + ['exec', '--json',
                                   '--dangerously-bypass-approvals-and-sandbox']
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True,
                encoding='utf-8', errors='replace',
            )
            proc.stdin.write('echo ok\n')
            proc.stdin.close()
            first_line = proc.stdout.readline()
            proc.kill()
            proc.wait()
        except Exception as e:
            pytest.skip(f'codex exec failed: {e}')

        if not first_line.strip():
            pytest.skip('no output from codex exec')

        try:
            msg = json.loads(first_line.strip())
        except json.JSONDecodeError:
            pytest.fail(f'First line not JSON: {first_line!r}')

        assert msg.get('type') == 'thread.started', f'Expected thread.started, got: {msg}'
        assert 'thread_id' in msg, f'Expected thread_id in: {msg}'

    # ── Allowance exhaustion (VENDOR_AGNOSTIC_PROGRAM.md §4) ────────────────
    # CODEX_REAL_USAGE_LIMIT_EVENT is the REAL line captured 2026-09-18 from
    # ~/.codex/sessions/2026/09/17/rollout-2026-09-17T22-19-23-01a0b2f4-*.jsonl
    # (ordinal 82) — not an invented fixture.

    CODEX_REAL_USAGE_LIMIT_EVENT = (
        '{"timestamp":"2026-09-18T05:21:28.415Z","ordinal":82,"type":"event_msg",'
        '"payload":{"type":"task_complete","turn_id":"01a0b2f4-81a1-7202-87ed-'
        '35b5637c0e4c","last_agent_message":null,"error":{"message":"You\'ve hit '
        'your usage limit. Visit https://chatgpt.com/codex/settings/usage to '
        'purchase more credits or try again at Sep 24th, 2026 7:58 AM.",'
        '"codex_error_info":"usage_limit_exceeded"},"started_at":1789708763,'
        '"completed_at":1789708888,"duration_ms":124861,'
        '"time_to_first_token_ms":5276}}'
    )

    def test_parse_event_real_captured_usage_limit_is_allowance_exhausted(self):
        from mc.agent_runtime import EventType
        ev = self.rt.parse_event(self.CODEX_REAL_USAGE_LIMIT_EVENT)
        assert ev is not None
        assert ev.type == EventType.ALLOWANCE_EXHAUSTED
        assert ev.payload['limit_kind'] == 'usage_limit'
        assert ev.payload['verified'] is True
        assert ev.payload['resets_at_display'] == 'Sep 24th, 2026 7:58 AM'

    def test_parse_event_usage_limit_dotted_error_shape_also_recognized(self):
        """`codex exec --json`'s translated error/turn.failed shape, in case a
        session-level failure is ever surfaced that way instead of as the
        rollout's native event_msg/task_complete (unconfirmed either way
        without spending Codex allowance — both shapes are handled)."""
        from mc.agent_runtime import EventType
        line = json.dumps({
            'type': 'error',
            'error': {'message': "try again at Sep 24th, 2026 7:58 AM.",
                      'codex_error_info': 'usage_limit_exceeded'},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ALLOWANCE_EXHAUSTED

    def test_explain_exit_error_usage_limit_not_misread_as_auth_error(self):
        """Regression: this exact text was live-misclassified 2026-09-18
        05:21:29 in data/logs/clayrune.log as "Codex isn't authenticated" —
        one second after the real usage_limit_exceeded event above — because
        the real message contains "chatgpt" (in the settings URL) and the
        auth-hint check used to run before the quota check. The structured
        parse_event branch above now intercepts this before it ever reaches
        free text, but explain_exit_error is fixed too as the fallback net."""
        tail = ("[codex error] You've hit your usage limit. Visit "
                "https://chatgpt.com/codex/settings/usage to purchase more "
                "credits or try again at Sep 24th, 2026 7:58 AM.")
        hint = self.rt.explain_exit_error(1, tail)
        assert hint is not None
        assert "isn't authenticated" not in hint
        assert "allowance" in hint.lower()


# ─────────────────────────────────────────────────────────────────────────────
# QwenRuntime — qwen-code CLI, a gemini-cli fork whose --output-format
# stream-json is Claude-Code-shaped (live-verified 2026-09-15, qwen-code
# 0.23.4). build_command/parse_event fixtures below are the real events
# captured from that machine, not invented shapes.
# ─────────────────────────────────────────────────────────────────────────────


class TestQwenRuntime:
    def setup_method(self):
        self.rt = agent_runtime.QwenRuntime()
        self.rt._bin_cache = None

    def test_build_command_basic(self):
        self.rt._bin_cache = 'qwen'
        cmd = self.rt.build_command()
        assert cmd[0] == 'qwen'
        assert '--output-format' in cmd
        assert cmd[cmd.index('--output-format') + 1] == 'stream-json'
        assert '--include-partial-messages' in cmd
        assert '--yolo' in cmd
        # --bare was dropped 2026-09-18 (W2): it also disabled hooks, with
        # no CLI-flag channel to pass a hook config through bare mode.
        # --allowed-mcp-server-names with a sentinel that matches no real
        # server takes over closing the native-MCP leak --bare used to
        # close (see QwenRuntime.build_command's docstring for the live
        # leak re-probe against this repo's real .mcp.json).
        assert '--bare' not in cmd
        assert '--allowed-mcp-server-names' in cmd
        idx = cmd.index('--allowed-mcp-server-names')
        assert cmd[idx + 1] == agent_runtime._QWEN_MCP_DENY_SENTINEL
        assert '--chat-recording' in cmd
        assert '--resume' not in cmd

    def test_build_command_with_model(self):
        self.rt._bin_cache = 'qwen'
        cmd = self.rt.build_command(model='qwen3-coder-plus')
        assert '--model' in cmd
        idx = cmd.index('--model')
        assert cmd[idx + 1] == 'qwen3-coder-plus'

    def test_build_command_resume(self):
        self.rt._bin_cache = 'qwen'
        session_id = '325a5df7-b352-47cb-ba1f-86aecd7409de'
        cmd = self.rt.build_command(resume_id=session_id)
        assert '--resume' in cmd
        idx = cmd.index('--resume')
        assert cmd[idx + 1] == session_id
        # --chat-recording is required on every respawn for --resume to work
        # at all (the CLI's own --help text) — flags don't persist.
        assert '--chat-recording' in cmd

    def test_build_command_with_extra_include_dirs(self):
        """W4/MC-947, live-verified 2026-09-18: `read_file`'s own
        `isWithinRoot` workspace check refused an attachment path outside
        the project root (this repo's real `data/uploads/`, for any project
        whose own root isn't an ancestor of it). `--include-directories`
        widens the workspace; live re-test with the SAME image one level
        outside the dispatch cwd, plus this flag pointed at its parent, made
        `read_file` succeed and the model correctly describe the image."""
        self.rt._bin_cache = 'qwen'
        cmd = self.rt.build_command(
            extra_include_dirs=['C:/Users/levir/AppData/Local/Temp'])
        assert '--include-directories' in cmd
        idx = cmd.index('--include-directories')
        assert cmd[idx + 1] == 'C:/Users/levir/AppData/Local/Temp'

    def test_build_command_no_include_dirs_omits_the_flag(self):
        self.rt._bin_cache = 'qwen'
        cmd = self.rt.build_command()
        assert '--include-directories' not in cmd

    def test_build_command_with_explicit_mcp_config_allowlists_exactly_it(self):
        """W4/MC-947, live-verified 2026-09-18 against real qwen-code 0.23.4
        in this repo (a real .mcp.json declaring filesystem+browser): with
        `--mcp-config` declaring only "filesystem" and `--allowed-mcp-server-
        names filesystem`, the live session's `system`/`init` envelope
        showed `mcp_servers: [{"name":"filesystem","status":"connected"}]`
        and `mcp__filesystem__*` tools present — "browser" (real, in this
        repo's own `.mcp.json`, but NOT in the declared set) never loaded.
        Qwen has no --strict-mcp-config; --allowed-mcp-server-names is what
        keeps native discovery from leaking anything not explicitly named."""
        self.rt._bin_cache = 'qwen'
        mcp_json = json.dumps({'mcpServers': {'filesystem': {'command': 'npx', 'args': []}}})
        cmd = self.rt.build_command(mcp_config_json=mcp_json)
        assert '--mcp-config' in cmd
        idx = cmd.index('--mcp-config')
        assert cmd[idx + 1] == mcp_json
        assert '--allowed-mcp-server-names' in cmd
        aidx = cmd.index('--allowed-mcp-server-names')
        # Exactly one name — nothing else leaking through from elsewhere.
        assert cmd[aidx + 1:] == ['filesystem']

    def test_build_command_with_multiple_declared_servers(self):
        self.rt._bin_cache = 'qwen'
        mcp_json = json.dumps({'mcpServers': {
            'zeta': {'command': 'x'}, 'alpha': {'command': 'y'}}})
        cmd = self.rt.build_command(mcp_config_json=mcp_json)
        aidx = cmd.index('--allowed-mcp-server-names')
        # Sorted for determinism, not dict insertion order.
        assert cmd[aidx + 1:aidx + 3] == ['alpha', 'zeta']

    def test_build_command_with_empty_declared_set_falls_back_to_deny_all(self):
        """An explicitly empty {"mcpServers": {}} (a project that opted in
        but selected nothing) must still reach the deny-all sentinel, not an
        empty --allowed-mcp-server-names (which could mean "no restriction"
        to the CLI rather than "restrict to nothing")."""
        self.rt._bin_cache = 'qwen'
        cmd = self.rt.build_command(mcp_config_json=json.dumps({'mcpServers': {}}))
        assert '--mcp-config' in cmd
        idx = cmd.index('--allowed-mcp-server-names')
        assert cmd[idx + 1] == agent_runtime._QWEN_MCP_DENY_SENTINEL

    def test_build_command_with_malformed_mcp_json_fails_closed(self):
        """Malformed input must never fall through to native discovery —
        same deny-all contract as no config at all."""
        self.rt._bin_cache = 'qwen'
        cmd = self.rt.build_command(mcp_config_json='not valid json{{{')
        idx = cmd.index('--allowed-mcp-server-names')
        assert cmd[idx + 1] == agent_runtime._QWEN_MCP_DENY_SENTINEL

    def test_no_fixed_model_catalog(self):
        """Can't verify Alibaba Coding/Token-Plan model ids against a live
        call on this box (no DashScope credential) — empty catalog falls back
        to Custom-only rather than inventing ids."""
        assert self.rt.model_choices() == []

    # ── mc:question wiring — same pattern as CodexRuntime's own tests ───────

    def test_dispatch_appends_mc_tool_protocol_to_system_prompt(self, monkeypatch):
        captured = {}

        def _fake_mode_a_dispatch(*args, **kwargs):
            captured['kwargs'] = kwargs
            # A real SessionHandle-shaped stand-in: dispatch() stashes
            # `_mcp_config_json` onto `handle.session_dict` after this call
            # returns (W4/MC-947), so the fake needs that attribute too.
            return agent_runtime.SessionHandle(
                mc_session_id='sid', provider='qwen', mode='A',
                project_path='/p', project_id='', session_dict={})

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', _fake_mode_a_dispatch)
        self.rt._bin_cache = 'qwen'
        result = self.rt.dispatch(project_path='/p', task='do X',
                                  system_prompt='MEMORY STUFF', session_dict={})
        assert result.mc_session_id == 'sid'
        stashed = captured['kwargs']['system_prompt']
        assert agent_runtime.MC_TOOL_PROTOCOL_PROMPT in stashed
        assert 'MEMORY STUFF' in stashed

    def test_dispatch_env_extra_has_suppress_warning(self, monkeypatch):
        captured = {}

        def _fake(runtime, cmd, full_prompt, project_path, project_id, task,
                 mc_sid, session_dict, incognito, env_extra, *rest, **kw):
            captured['env_extra'] = env_extra
            return agent_runtime.SessionHandle(
                mc_session_id='sid', provider='qwen', mode='A',
                project_path='/p', project_id='', session_dict={})

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', _fake)
        self.rt._bin_cache = 'qwen'
        self.rt.dispatch(project_path='/p', task='do X', session_dict={})
        assert captured['env_extra'].get('QWEN_CODE_SUPPRESS_YOLO_WARNING') == '1'

    # ── parse_event — fixtures are REAL captured events, not invented ───────

    def test_parse_event_init(self):
        line = json.dumps({
            'type': 'system', 'subtype': 'init',
            'session_id': 'da425c0c-6ca6-4b2e-a185-379de4b0dd8c',
            'model': 'gemini-3.5-flash-lite', 'qwen_code_version': '0.23.4',
            'mcp_servers': [], 'tools': ['read_file', 'run_shell_command'],
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.INIT
        assert ev.session_id == 'da425c0c-6ca6-4b2e-a185-379de4b0dd8c'
        # payload['session_id'] (not just the AgentEvent's own field) is what
        # the shared _mode_a_reader reads to set provider_session_id — a
        # live-caught bug (resume was permanently unreachable without this).
        assert ev.payload['session_id'] == 'da425c0c-6ca6-4b2e-a185-379de4b0dd8c'
        assert ev.payload['model'] == 'gemini-3.5-flash-lite'

    def test_parse_event_assistant_text(self):
        line = json.dumps({
            'type': 'assistant', 'session_id': 's1',
            'message': {'role': 'assistant',
                       'content': [{'type': 'text', 'text': 'pong'}]},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        # Flat 'text', NOT 'blocks' — the shared _mode_a_reader's
        # ASSISTANT_TEXT branch reads payload['text'] directly (same shape
        # Codex/Gemini use); a 'blocks'-shaped payload here would fall back
        # to the raw JSON line instead of the actual reply (live-caught bug).
        assert ev.payload['text'] == 'pong'

    def test_parse_event_tool_use_run_shell_command(self):
        """Live-captured shape: tool name is 'run_shell_command', field is
        'command' — same as Claude's own Bash tool's field name."""
        line = json.dumps({
            'type': 'assistant', 'session_id': 's1',
            'message': {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': 'call_1', 'name': 'run_shell_command',
                 'input': {'command': 'echo hi', 'description': 'test'}},
            ]},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TOOL_USE
        block = ev.payload['blocks'][0]
        assert block['name'] == 'run_shell_command'
        assert block['input']['command'] == 'echo hi'

    def test_parse_event_user_tool_result_suppressed(self):
        """type:'user' (tool_result echo) must return None — the shared
        _mode_a_reader has no USER_MESSAGE/TOOL_RESULT branch, so surfacing
        it would dump raw JSON into the chat via its catch-all else."""
        line = json.dumps({
            'type': 'user', 'session_id': 's1',
            'message': {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': 'call_1',
                 'is_error': False, 'content': 'hi'},
            ]},
        })
        assert self.rt.parse_event(line) is None

    def test_parse_event_stream_event_suppressed(self):
        """Partial-message deltas (--include-partial-messages) are internal —
        ClaudeRuntime's own parser ignores them the same way."""
        line = json.dumps({'type': 'stream_event', 'session_id': 's1',
                           'event': {'type': 'content_block_delta',
                                    'delta': {'type': 'text_delta', 'text': 'p'}}})
        assert self.rt.parse_event(line) is None

    def test_parse_event_result_success(self):
        line = json.dumps({
            'type': 'result', 'subtype': 'success', 'session_id': 's1',
            'is_error': False, 'num_turns': 1,
            'usage': {'input_tokens': 10, 'output_tokens': 1},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TURN_END
        assert ev.payload['num_turns'] == 1
        assert ev.payload['usage'] == {'input_tokens': 10, 'output_tokens': 1}

    def test_parse_event_result_error(self):
        """Live-captured shape: is_error + nested error.message (an invalid
        model id returning a 404 from the underlying API)."""
        line = json.dumps({
            'type': 'result', 'subtype': 'error_during_execution', 'session_id': 's1',
            'is_error': True,
            'error': {'message': 'models/bogus-model is not found'},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ERROR
        assert 'not found' in ev.payload['text']

    def test_parse_event_quota_error_is_allowance_exhausted(self):
        """VENDOR_AGNOSTIC_PROGRAM.md §4 — no real captured Qwen exhaustion
        sample exists on this box; this is the same text-heuristic
        agent_routes.py's own quota-log scraper already uses, explicitly
        unverified (mc.allowance_state._generic_text_exhaustion)."""
        line = json.dumps({
            'type': 'result', 'is_error': True,
            'error': {'message': '429 Resource has been exhausted (rate limit)'},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ALLOWANCE_EXHAUSTED
        assert ev.payload['verified'] is False

    def test_parse_event_non_json_fallback(self):
        """The 'Warning: running headless...' banner (when the suppression
        env var isn't honored for any reason) must degrade to plain text,
        never crash the parser."""
        ev = self.rt.parse_event('Warning: running headless with --yolo...')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT

    # ── auth state — 'ok' | 'not_logged_in', never a guess ──────────────────

    def test_auth_state_not_logged_in_with_clean_env(self, monkeypatch, tmp_path):
        for var in ('DASHSCOPE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
                    'GEMINI_API_KEY'):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv('USERPROFILE', str(tmp_path))
        monkeypatch.setenv('HOME', str(tmp_path))
        status, method = self.rt._qwen_auth_state()
        assert status == 'not_logged_in'
        assert method is None

    def test_auth_state_dashscope_env(self, monkeypatch, tmp_path):
        # Isolate USERPROFILE/HOME: _settings_auth_env() is checked FIRST as
        # of 2026-09-18 (settings.json wins over a generic env var — see its
        # docstring), so this test must not see this box's own REAL
        # ~/.qwen/settings.json or it stops testing the env-var fallback
        # path at all.
        monkeypatch.setenv('USERPROFILE', str(tmp_path))
        monkeypatch.setenv('HOME', str(tmp_path))
        monkeypatch.setenv('DASHSCOPE_API_KEY', 'sk-test')
        status, method = self.rt._qwen_auth_state()
        assert status == 'ok'
        assert method == 'env:DASHSCOPE_API_KEY'

    def test_auth_state_falls_back_to_gemini_oauth(self, monkeypatch, tmp_path):
        """Live-observed on this box: with no qwen-specific credential
        configured anywhere, `qwen` still answered by silently reusing
        gemini-cli's own OAuth cache. Must report 'ok' with a distinct
        method string, never mistaken for real Qwen/DashScope auth."""
        for var in ('DASHSCOPE_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY',
                    'GEMINI_API_KEY'):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv('USERPROFILE', str(tmp_path))
        monkeypatch.setenv('HOME', str(tmp_path))
        gdir = tmp_path / '.gemini'
        gdir.mkdir()
        (gdir / 'oauth_creds.json').write_text(
            json.dumps({'refresh_token': 'rt'}), encoding='utf-8')
        status, method = self.rt._qwen_auth_state()
        assert status == 'ok'
        assert method == 'fallback:gemini-oauth'

    def test_health_check_flags_google_only_credential(self, monkeypatch):
        """A Google-only credential authenticates the qwen CLI but serves GEMINI.

        Measured 2026-09-15 on this box: with only GEMINI_API_KEY set,
        `qwen "..."` answered using gemini-3.5-flash-lite (its own telemetry
        names the model) and `-m qwen3-coder-plus` 404'd against Google's
        v1beta endpoint. health_check must still report 'ok' — the CLI does
        run — but MUST carry error_text, or the first-run chooser and the
        Settings provider card render a plain green "signed in" for a
        provider that is not running Qwen at all.
        """
        monkeypatch.setattr(self.rt, 'resolve_binary',
                            lambda: Path('/usr/local/bin/qwen'))
        monkeypatch.setattr(
            agent_runtime.subprocess, 'run',
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0] if a else [], returncode=0, stdout='0.23.4', stderr=''))
        for method in ('env:GEMINI_API_KEY', 'fallback:gemini-oauth'):
            monkeypatch.setattr(self.rt, '_qwen_auth_state',
                                lambda m=method: ('ok', m))
            health = self.rt.health_check()
            assert health.auth_state.status == 'ok'
            assert health.auth_state.error_text,                 f'{method} must not render as bare green'
            assert 'Gemini' in health.auth_state.error_text
            assert 'DASHSCOPE_API_KEY' in health.auth_state.error_text

        monkeypatch.setattr(self.rt, '_qwen_auth_state',
                            lambda: ('ok', 'env:DASHSCOPE_API_KEY'))
        assert self.rt.health_check().auth_state.error_text is None,             'a real Qwen credential must stay unflagged'

    def test_health_check_not_installed(self, monkeypatch):
        monkeypatch.setattr(self.rt, 'resolve_binary', lambda: None)
        health = self.rt.health_check()
        assert health.installed is False
        assert health.auth_state.status == 'not_installed'
        assert '@qwen-code/qwen-code' in health.install_hint

    # ── transcript_path — ~/.qwen/projects/<lowercased-encoded>/chats/<id>.jsonl

    def test_transcript_path_lowercases_encoded_dir(self, monkeypatch, tmp_path):
        monkeypatch.setenv('USERPROFILE', str(tmp_path))
        monkeypatch.setenv('HOME', str(tmp_path))
        project_path = str(tmp_path / 'Some_Project' / '.clayrune')
        Path(project_path).mkdir(parents=True)
        encoded = agent_runtime.ClaudeRuntime._encode_project_path(project_path)
        assert encoded is not None
        chats_dir = (tmp_path / '.qwen' / 'projects'
                    / encoded.replace('_', '-').replace('.', '-').lower() / 'chats')
        chats_dir.mkdir(parents=True)
        (chats_dir / 'sess1.jsonl').write_text('{}', encoding='utf-8')
        found = self.rt.transcript_path(project_path, 'sess1')
        assert found is not None
        assert found.is_file()

    def test_transcript_path_missing_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv('USERPROFILE', str(tmp_path))
        monkeypatch.setenv('HOME', str(tmp_path))
        assert self.rt.transcript_path(str(tmp_path), 'no-such-session') is None

    def test_capabilities(self):
        caps = self.rt.capabilities()
        assert caps.name == 'qwen'
        assert caps.supports_mode_a is True
        assert caps.supports_mode_b is False
        assert caps.supports_session_resume is True
        # W4/MC-947 (2026-09-18): dispatch()/build_command() now accept
        # mcp_config_json and declare exactly that set via --mcp-config +
        # --allowed-mcp-server-names — genuinely True, not the old
        # deny-everything-only behavior. See TestQwenMcpConfigInjection.
        assert caps.supports_mcp is True
        assert caps.oneshot_supported is True

    def test_explain_exit_error_known_libuv_crash(self):
        """Live-reproduced 2026-09-15 (twice: a fresh dispatch and a --resume
        respawn) — qwen-code 0.23.4 crashes with a native libuv assertion
        during its own teardown, AFTER already emitting the correct reply.
        The hint must say the reply is still valid, not just 'exited with
        code N' (which would look like the answer above it was garbage)."""
        tail = ('OK.\nAssertion failed: !(handle->flags & UV_HANDLE_CLOSING), '
               'file src\\win\\async.c, line 76')
        hint = self.rt.explain_exit_error(3221226505, tail)
        assert hint is not None
        assert 'still valid' in hint


# ─────────────────────────────────────────────────────────────────────────────
# CodexRuntime.render_transcript_for_scribe — parity audit item 1, "Scribe on
# Codex". mc.memory._scribe_extract used to be Claude-only end to end
# (hardcoded get_runtime('claude')), so extraction never even looked for a
# Codex transcript. The rollout is NOT Claude's {message: {content: [...]}}
# shape, so the adaptation lives here, at the runtime boundary, rather than
# as a special-case inside the Scribe. Record shapes below are taken from a
# real byte-for-byte census of every rollout on the audit machine
# (2026-09-07, codex-cli 0.153.4) — see the method's own docstring.
# ─────────────────────────────────────────────────────────────────────────────

def _append_jsonl(path, records):
    with open(path, 'a', encoding='utf-8') as fh:
        for rec in records:
            fh.write(json.dumps(rec) + '\n')


class TestCodexRenderTranscriptForScribe:
    def setup_method(self):
        self.rt = CodexRuntime()

    def test_renders_user_and_assistant_messages(self, tmp_path):
        root = tmp_path / 'sessions'
        f = _write_codex_rollout(root, 'th-1', str(tmp_path),
                                 [('user', 'read the config'),
                                  ('assistant', 'reading it now')])
        out = self.rt.render_transcript_for_scribe(f)
        assert 'USER: read the config' in out
        assert 'ASSISTANT: reading it now' in out

    def test_developer_role_preambles_are_dropped(self, tmp_path):
        """Codex injects plugin/hook preambles as developer-role messages —
        same shape as a real reply. They must not reach the summarizer."""
        root = tmp_path / 'sessions'
        f = _write_codex_rollout(root, 'th-2', str(tmp_path),
                                 [('developer', 'plugin hook noise'),
                                  ('user', 'real question')])
        out = self.rt.render_transcript_for_scribe(f)
        assert 'plugin hook noise' not in out
        assert 'USER: real question' in out

    def test_renders_reasoning_summary_as_thinking(self, tmp_path):
        root = tmp_path / 'sessions'
        f = _write_codex_rollout(root, 'th-3', str(tmp_path), [('user', 'hi')])
        _append_jsonl(f, [{
            'timestamp': '2026-09-07T12-53-29Z', 'type': 'response_item',
            'payload': {'type': 'reasoning', 'content': None,
                       'encrypted_content': 'gAAA...opaque',
                       'summary': [{'type': 'summary_text',
                                   'text': 'Considering search options'}]},
        }])
        out = self.rt.render_transcript_for_scribe(f)
        assert 'THINKING: Considering search options' in out
        assert 'gAAA...opaque' not in out  # never leak the encrypted blob

    def test_reasoning_with_no_summary_drops_silently(self, tmp_path):
        """Reasoning-encryption-on turns carry summary: [] — this is a real,
        expected content drop, not a parse failure; must not raise or empty
        out the whole render."""
        root = tmp_path / 'sessions'
        f = _write_codex_rollout(root, 'th-4', str(tmp_path), [('user', 'hi')])
        _append_jsonl(f, [{
            'timestamp': '2026-09-07T12-53-29Z', 'type': 'response_item',
            'payload': {'type': 'reasoning', 'content': None,
                       'encrypted_content': 'gAAA...opaque', 'summary': []},
        }, {
            'timestamp': '2026-09-07T12-53-30Z', 'type': 'response_item',
            'payload': {'type': 'message', 'role': 'assistant',
                       'content': [{'type': 'output_text', 'text': 'done'}]},
        }])
        out = self.rt.render_transcript_for_scribe(f)
        assert 'THINKING' not in out
        assert 'ASSISTANT: done' in out

    def test_renders_function_call_and_output(self, tmp_path):
        root = tmp_path / 'sessions'
        f = _write_codex_rollout(root, 'th-5', str(tmp_path), [('user', 'hi')])
        _append_jsonl(f, [{
            'timestamp': '2026-09-07T12-53-29Z', 'type': 'response_item',
            'payload': {'type': 'function_call', 'name': 'shell',
                       'arguments': '{"command":["bash","-lc","ls -la"]}',
                       'call_id': 'call_1'},
        }, {
            'timestamp': '2026-09-07T12-53-30Z', 'type': 'response_item',
            'payload': {'type': 'function_call_output', 'call_id': 'call_1',
                       'output': '{"output":"total 0\\n","metadata":{"exit_code":0}}'},
        }])
        out = self.rt.render_transcript_for_scribe(f)
        assert 'ACTION shell: {"command":["bash","-lc","ls -la"]}' in out
        assert 'RESULT: {"output":"total 0' in out

    def test_renders_custom_tool_call_apply_patch(self, tmp_path):
        """custom_tool_call's `input` is a raw string (apply_patch's own diff
        format) — NOT JSON, unlike function_call's `arguments`."""
        root = tmp_path / 'sessions'
        f = _write_codex_rollout(root, 'th-6', str(tmp_path), [('user', 'hi')])
        _append_jsonl(f, [{
            'timestamp': '2026-09-07T12-53-29Z', 'type': 'response_item',
            'payload': {'type': 'custom_tool_call', 'name': 'apply_patch',
                       'status': 'completed', 'call_id': 'call_2',
                       'input': '*** Begin Patch\n*** Add File: x.py\n+pass\n'},
        }, {
            'timestamp': '2026-09-07T12-53-30Z', 'type': 'response_item',
            'payload': {'type': 'custom_tool_call_output', 'call_id': 'call_2',
                       'output': '{"output":"Success.\\n","metadata":{"exit_code":0}}'},
        }])
        out = self.rt.render_transcript_for_scribe(f)
        assert 'ACTION apply_patch: *** Begin Patch' in out
        assert 'RESULT: {"output":"Success.' in out

    def test_giant_tool_result_is_capped(self, tmp_path):
        root = tmp_path / 'sessions'
        f = _write_codex_rollout(root, 'th-7', str(tmp_path), [('user', 'hi')])
        huge = 'x' * 5000
        _append_jsonl(f, [{
            'timestamp': '2026-09-07T12-53-29Z', 'type': 'response_item',
            'payload': {'type': 'function_call_output', 'call_id': 'c',
                       'output': huge},
        }])
        out = self.rt.render_transcript_for_scribe(f)
        assert 'chars elided' in out
        assert len(out) < 5000

    def test_session_meta_and_event_msg_records_produce_no_lines(self, tmp_path):
        """Line 1 is always session_meta; event_msg carries turn-boundary/
        token-count bookkeeping. Neither is renderable conversation content."""
        root = tmp_path / 'sessions'
        f = _write_codex_rollout(root, 'th-8', str(tmp_path), [])
        _append_jsonl(f, [{
            'timestamp': '2026-09-07T12-53-29Z', 'type': 'event_msg',
            'payload': {'type': 'task_started', 'turn_id': 't1'},
        }])
        out = self.rt.render_transcript_for_scribe(f)
        assert out == ''

    def test_missing_file_returns_none_not_empty_string(self, tmp_path):
        """None (unparseable/absent) must be distinguishable from '' (a real,
        empty-but-valid render) — the caller falls back to log_lines on None."""
        assert self.rt.render_transcript_for_scribe(tmp_path / 'nope.jsonl') is None

    def test_base_runtime_default_is_unsupported(self):
        """The ABC default — every provider without an override (Gemini,
        opencode, goose, aider, kiro) declines rather than guesses."""
        from mc.agent_runtime import AgentRuntime

        class _Bare(AgentRuntime):
            name = 'bare'
            def resolve_binary(self): return None
            def health_check(self): return None
            def capabilities(self): return None
            def dispatch(self, **kw): raise NotImplementedError
            def write_followup(self, *a, **kw): raise NotImplementedError
            def interrupt(self, *a, **kw): raise NotImplementedError
            def stop(self, *a, **kw): raise NotImplementedError

        assert _Bare().render_transcript_for_scribe(Path('/nope')) is None


# ─────────────────────────────────────────────────────────────────────────────
# OpenCodeRuntime
# ─────────────────────────────────────────────────────────────────────────────


class TestOpenCodeRuntime:
    def setup_method(self):
        self.rt = OpenCodeRuntime()
        self.rt._bin_cache = None

    def test_build_command_basic(self):
        """opencode run --format json"""
        self.rt._bin_cache = 'opencode'
        cmd = self.rt.build_command()
        assert 'opencode' in cmd[0]
        assert 'run' in cmd
        assert '--format' in cmd
        assert 'json' in cmd

    def test_build_command_resume_last(self):
        """opencode run --format json --continue"""
        self.rt._bin_cache = 'opencode'
        cmd = self.rt.build_command(resume_id='last')
        assert '--continue' in cmd

    def test_build_command_resume_specific(self):
        """opencode run --format json --session <ID>"""
        self.rt._bin_cache = 'opencode'
        cmd = self.rt.build_command(resume_id='abc123')
        assert '--session' in cmd
        assert 'abc123' in cmd

    def test_parse_event_session(self):
        line = json.dumps({'type': 'session', 'properties': {'id': 'sess-abc', 'model': 'claude-3'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.INIT
        assert ev.session_id == 'sess-abc'

    def test_parse_event_assistant_message(self):
        line = json.dumps({
            'type': 'message',
            'role': 'assistant',
            'content': [{'type': 'text', 'text': 'Hello from OpenCode!'}],
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert 'Hello from OpenCode!' in ev.payload['text']

    def test_parse_event_user_message_ignored(self):
        line = json.dumps({'type': 'message', 'role': 'user', 'content': 'hi'})
        ev = self.rt.parse_event(line)
        assert ev is None

    def test_parse_event_done(self):
        line = json.dumps({'type': 'done', 'info': {'cost': 0.002, 'usage': {}}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TURN_END
        assert ev.payload['cost_usd'] == 0.002

    def test_parse_event_error(self):
        line = json.dumps({'type': 'error', 'error': {'message': 'rate limit'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ERROR

    def test_capabilities(self):
        caps = self.rt.capabilities()
        assert caps.name == 'opencode'
        assert caps.supports_session_resume is True
        assert caps.supports_mcp is True
        assert caps.emits_cost is True

    def test_health_check_not_installed(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, 'which', lambda _: None)
        self.rt._bin_cache = None
        hs = self.rt.health_check()
        assert hs.installed is False


# ─────────────────────────────────────────────────────────────────────────────
# GooseRuntime
# ─────────────────────────────────────────────────────────────────────────────


class TestGooseRuntime:
    def setup_method(self):
        self.rt = GooseRuntime()
        self.rt._bin_cache = None

    def test_build_command_basic(self):
        """goose run --no-session --output-format stream-json"""
        self.rt._bin_cache = 'goose'
        cmd = self.rt.build_command()
        assert 'goose' in cmd[0]
        assert 'run' in cmd
        assert '--no-session' in cmd
        assert '--output-format' in cmd
        assert 'stream-json' in cmd

    def test_build_command_with_system_prompt(self):
        """goose run --system TEXT --no-session --output-format stream-json"""
        self.rt._bin_cache = 'goose'
        cmd = self.rt.build_command(system_prompt='You are a coding assistant.')
        assert '--system' in cmd
        idx = cmd.index('--system')
        assert 'coding assistant' in cmd[idx + 1]

    def test_build_command_with_model(self):
        self.rt._bin_cache = 'goose'
        cmd = self.rt.build_command(model='openai/gpt-4o')
        assert '--model' in cmd
        idx = cmd.index('--model')
        assert cmd[idx + 1] == 'openai/gpt-4o'

    def test_parse_event_plain_text(self):
        ev = self.rt.parse_event('Analyzing your code...')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT

    def test_parse_event_init(self):
        line = json.dumps({'type': 'init', 'session_id': 'goose-sess-1', 'model': 'gpt-4o'})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.INIT
        assert ev.payload['session_id'] == 'goose-sess-1'

    def test_parse_event_assistant_message(self):
        line = json.dumps({
            'type': 'message',
            'role': 'assistant',
            'content': [{'type': 'text', 'text': 'Hello from Goose!'}],
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT

    def test_parse_event_tool_use(self):
        line = json.dumps({'type': 'tool_use', 'name': 'bash', 'input': {'cmd': 'ls'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TOOL_USE
        assert ev.payload['blocks'][0]['name'] == 'bash'

    def test_parse_event_result(self):
        line = json.dumps({'type': 'result', 'usage': {}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TURN_END

    def test_parse_event_error(self):
        line = json.dumps({'type': 'error', 'message': 'provider not configured'})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ERROR

    def test_capabilities(self):
        caps = self.rt.capabilities()
        assert caps.name == 'goose'
        assert caps.supports_mcp is True
        assert caps.supports_session_resume is True
        assert caps.context_injection == 'flag'
        assert caps.emits_cost is False  # goose doesn't emit cost

    def test_health_check_not_installed(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, 'which', lambda _: None)
        self.rt._bin_cache = None
        hs = self.rt.health_check()
        assert hs.installed is False
        assert 'goose' in hs.install_hint.lower()


# ─────────────────────────────────────────────────────────────────────────────
# AiderRuntime
# ─────────────────────────────────────────────────────────────────────────────


class TestAiderRuntime:
    def setup_method(self):
        self.rt = AiderRuntime()
        self.rt._bin_cache = None

    def test_build_command_basic(self):
        """aider --no-stream --yes --no-auto-commits"""
        self.rt._bin_cache = 'aider'
        cmd = self.rt.build_command()
        assert 'aider' in cmd[0]
        assert '--no-stream' in cmd
        assert '--yes' in cmd
        assert '--no-auto-commits' in cmd

    def test_build_command_with_model(self):
        self.rt._bin_cache = 'aider'
        cmd = self.rt.build_command(model='claude-3-5-sonnet-20241022')
        assert '--model' in cmd
        idx = cmd.index('--model')
        assert cmd[idx + 1] == 'claude-3-5-sonnet-20241022'

    def test_build_command_dry_run(self):
        self.rt._bin_cache = 'aider'
        cmd = self.rt.build_command(dry_run=True)
        assert '--dry-run' in cmd

    def test_parse_event_plain_text(self):
        """Aider plain text → ASSISTANT_TEXT for every non-empty line"""
        ev = self.rt.parse_event('Applying changes to auth.py...')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert ev.payload['text'] == 'Applying changes to auth.py...'

    def test_parse_event_empty(self):
        assert self.rt.parse_event('') is None

    def test_parse_event_tokens_line(self):
        """Aider token/cost lines are surfaced as ASSISTANT_TEXT"""
        ev = self.rt.parse_event('Tokens: 1234 sent, 567 received. Cost: $0.01')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT

    def test_capabilities(self):
        caps = self.rt.capabilities()
        assert caps.name == 'aider'
        assert caps.supports_session_resume is False
        assert caps.supports_mcp is False
        assert caps.supports_plan_mode is True  # via --dry-run
        assert caps.emits_usage is False
        assert caps.context_injection == 'file'
        assert caps.context_file_name == '.aider.conf.yml'

    def test_transcript_path_missing_file(self):
        """Returns None when .aider.chat.history.md doesn't exist"""
        result = self.rt.transcript_path('/nonexistent/path', 'any')
        assert result is None

    def test_transcript_path_existing_file(self, tmp_path):
        """Returns path when .aider.chat.history.md exists"""
        hist = tmp_path / '.aider.chat.history.md'
        hist.write_text('# Aider history\n')
        result = self.rt.transcript_path(str(tmp_path), 'any')
        assert result == hist

    def test_health_check_not_installed(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, 'which', lambda _: None)
        self.rt._bin_cache = None
        hs = self.rt.health_check()
        assert hs.installed is False
        assert 'pip install' in hs.install_hint


# ─────────────────────────────────────────────────────────────────────────────
# KiroRuntime
# ─────────────────────────────────────────────────────────────────────────────


class TestKiroRuntime:
    def setup_method(self):
        self.rt = KiroRuntime()
        self.rt._bin_cache = None

    def test_build_command_basic(self):
        """kiro-cli --no-interactive --trust-all-tools"""
        self.rt._bin_cache = 'kiro-cli'
        cmd = self.rt.build_command()
        assert 'kiro-cli' in cmd[0]
        assert '--no-interactive' in cmd
        assert '--trust-all-tools' in cmd

    def test_parse_event_plain_text(self):
        ev = self.rt.parse_event('Analyzing repository structure...')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT

    def test_parse_event_json_rpc_metadata(self):
        """JSON-RPC notification _kiro.dev/metadata → ASSISTANT_TEXT"""
        line = json.dumps({
            'jsonrpc': '2.0',
            'method': '_kiro.dev/metadata',
            'params': {'text': 'Processing your request...'},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert 'Processing' in ev.payload['text']

    def test_parse_event_json_rpc_error(self):
        """JSON-RPC error → ERROR"""
        line = json.dumps({
            'jsonrpc': '2.0',
            'id': 1,
            'error': {'code': -32600, 'message': 'Invalid request'},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ERROR
        assert 'Invalid request' in ev.payload['text']

    def test_parse_event_session_new(self):
        """session/new response → INIT"""
        line = json.dumps({
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'session/new',
            'result': {'session_id': 'kiro-sess-abc'},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.INIT

    def test_parse_event_empty(self):
        assert self.rt.parse_event('') is None

    def test_capabilities(self):
        caps = self.rt.capabilities()
        assert caps.name == 'kiro'
        assert caps.supports_mcp is True
        assert caps.supports_plan_mode is False
        assert caps.supports_session_resume is False
        assert caps.emits_cost is False

    def test_health_check_not_installed(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, 'which', lambda _: None)
        self.rt._bin_cache = None
        hs = self.rt.health_check()
        assert hs.installed is False
        assert 'kiro' in hs.install_hint.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Cross-provider: all runtimes pass base contract checks
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize('provider_name', [
    'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro',
])
def test_capabilities_name_matches_runtime_name(provider_name):
    rt = agent_runtime.get_runtime(provider_name)
    caps = rt.capabilities()
    assert caps.name == provider_name, (
        f"{rt.__class__.__name__}.capabilities().name must be {provider_name!r}"
    )


@pytest.mark.parametrize('provider_name', [
    'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro',
])
def test_parse_event_empty_returns_none(provider_name):
    rt = agent_runtime.get_runtime(provider_name)
    assert rt.parse_event('') is None
    assert rt.parse_event('\n') is None


@pytest.mark.parametrize('provider_name', [
    'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro',
])
def test_parse_event_plain_text_returns_assistant_event(provider_name):
    rt = agent_runtime.get_runtime(provider_name)
    ev = rt.parse_event('Some plain text from the agent')
    assert ev is not None
    assert ev.type == EventType.ASSISTANT_TEXT
    assert ev.provider == provider_name


@pytest.mark.parametrize('provider_name', [
    'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro',
])
def test_claude_regression_default_runtime(provider_name):
    """Claude runtime is still accessible after registering all other providers."""
    claude_rt = agent_runtime.get_runtime('claude')
    assert claude_rt.name == 'claude'
    # Claude's capabilities are unchanged
    caps = claude_rt.capabilities()
    assert caps.supports_mode_b is True
    assert caps.mode_b_kind == 'native'
    assert caps.supports_plan_mode is True
    assert caps.supports_session_resume is True


# ─────────────────────────────────────────────────────────────────────────────
# Followup amnesia fix — _compose_respawn_prompt re-injects system context
# ─────────────────────────────────────────────────────────────────────────────


def test_compose_respawn_prompt_reinjects_system_context():
    """Regression: Mode-A followups must re-prepend the dispatch-time system
    prompt, not just a log tail. Without this, every provider except claude
    loses MEMORY / AGENT_RULES / CLAYRUNE_API after turn 1."""
    session = {
        '_system_prompt': 'SYSTEM-CONTEXT-MARKER: rules and memory here',
        'log_lines': ['prior assistant output line'],
    }
    out = agent_runtime._compose_respawn_prompt(session, 'the new user message')
    assert 'SYSTEM-CONTEXT-MARKER' in out, 'system context dropped on followup'
    assert 'prior assistant output line' in out, 'prior-turn tail dropped'
    assert out.rstrip().endswith('the new user message')
    # System context comes first, user message last.
    assert out.index('SYSTEM-CONTEXT-MARKER') < out.index('the new user message')


def test_compose_respawn_prompt_no_system_prompt():
    """When no system prompt was stashed, the prompt is still well-formed."""
    session = {'log_lines': ['some output']}
    out = agent_runtime._compose_respawn_prompt(session, 'hello')
    assert out.rstrip().endswith('hello')
    assert 'some output' in out


def test_compose_respawn_prompt_empty_session():
    """Empty session dict — followup degrades to just the message."""
    out = agent_runtime._compose_respawn_prompt({}, 'just the message')
    assert out == 'just the message'


# ─────────────────────────────────────────────────────────────────────────────
# Gemini auth detection — OAuth credentials, not just GEMINI_API_KEY
# ─────────────────────────────────────────────────────────────────────────────


def test_gemini_auth_state_env_key(monkeypatch):
    """GEMINI_API_KEY present → ok via env."""
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    status, method, err = agent_runtime.get_runtime('gemini')._gemini_auth_state()
    assert status == 'ok'
    assert method == 'env:GEMINI_API_KEY'
    assert err is None


def test_gemini_auth_state_oauth_creds(monkeypatch, tmp_path):
    """Regression: cached OAuth credentials count as signed in — the card
    must not show 'status unknown' when ~/.gemini/oauth_creds.json exists."""
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    gdir = tmp_path / '.gemini'
    gdir.mkdir()
    (gdir / 'oauth_creds.json').write_text(
        json.dumps({'access_token': 'a', 'refresh_token': 'r'}), encoding='utf-8')
    (gdir / 'google_accounts.json').write_text(
        json.dumps({'active': 'user@example.com'}), encoding='utf-8')
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    status, method, err = agent_runtime.get_runtime('gemini')._gemini_auth_state()
    assert status == 'ok'
    assert method == 'oauth (user@example.com)'
    assert err is None


def test_gemini_auth_state_not_logged_in(monkeypatch, tmp_path):
    """No env key and no OAuth creds → not_logged_in with a helpful hint."""
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    status, method, err = agent_runtime.get_runtime('gemini')._gemini_auth_state()
    assert status == 'not_logged_in'
    assert method is None
    assert err and 'GEMINI_API_KEY' in err


def test_gemini_health_check_oauth_only_is_unverified(monkeypatch, tmp_path):
    """F9 (clean-VM run 2026-09-18): a cached oauth_creds.json only proves a
    credential was ONCE issued — Google now refuses personal-account OAuth
    for Gemini Code Assist ("This client is no longer supported... migrate
    to the Antigravity suite") while the file on disk still looks valid.
    health_check() must not upgrade that local evidence to a verified 'ok'.
    """
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    gdir = tmp_path / '.gemini'
    gdir.mkdir()
    (gdir / 'oauth_creds.json').write_text(
        json.dumps({'access_token': 'a', 'refresh_token': 'r'}), encoding='utf-8')
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    rt = GeminiRuntime()  # fresh instance — this test relies on a clean _auth_cache
    rt._bin_cache = str(tmp_path / 'gemini')
    (tmp_path / 'gemini').write_text('')
    monkeypatch.setattr(
        agent_runtime.subprocess, 'run',
        lambda *a, **k: subprocess.CompletedProcess(
            args=a[0] if a else [], returncode=0, stdout='0.59.0', stderr=''))
    hs = rt.health_check()
    assert hs.auth_state.status == 'unverified'
    assert 'GEMINI_API_KEY' in (hs.auth_state.error_text or '')


def test_gemini_explain_exit_error_detects_oauth_rejection(monkeypatch):
    """A real dispatch failure carrying Google's exact refusal text must
    both explain itself to the user AND stamp the cache (F9) so the NEXT
    health_check() reports oauth_rejected instead of a stale 'ok'/'unverified'."""
    rt = GeminiRuntime()  # fresh instance — never share _auth_cache with other tests
    log_tail = (
        'FetchError: This client is no longer supported for Gemini Code '
        'Assist for individuals. Please migrate to the Antigravity suite.'
    )
    msg = rt.explain_exit_error(1, log_tail)
    assert msg and 'GEMINI_API_KEY' in msg
    cached = rt.auth_status()
    assert cached['status'] == 'oauth_rejected'
    assert cached['ok'] is False


# ─────────────────────────────────────────────────────────────────────────────
# F11 (clean-VM run 2, 2026-09-18): a CLI-native "Use Gemini API key" login
# stores the key in the OS keychain + ~/.gemini/settings.json's
# security.auth.selectedType — invisible to both prior evidence sources
# (env var, oauth_creds.json).
# ─────────────────────────────────────────────────────────────────────────────


def test_gemini_auth_state_keychain_api_key(monkeypatch, tmp_path):
    """settings.json selectedType == 'gemini-api-key' (no oauth_creds.json,
    no env var) must count as signed in, distinctly labeled from oauth."""
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    gdir = tmp_path / '.gemini'
    gdir.mkdir()
    (gdir / 'settings.json').write_text(
        json.dumps({'security': {'auth': {'selectedType': 'gemini-api-key'}}}),
        encoding='utf-8')
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    status, method, err = agent_runtime.get_runtime('gemini')._gemini_auth_state()
    assert status == 'ok'
    assert method == 'keychain:gemini-api-key'
    assert err is None


def test_gemini_health_check_keychain_api_key_is_ok_not_unverified(monkeypatch, tmp_path):
    """Unlike the OAuth path (F9), a keychain-native API key must NOT be
    downgraded to 'unverified' by health_check() — it gets the same local
    trust as an env-var key; auth_probe() (not health_check) is where it
    gets actually verified, via a real CLI call."""
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    gdir = tmp_path / '.gemini'
    gdir.mkdir()
    (gdir / 'settings.json').write_text(
        json.dumps({'security': {'auth': {'selectedType': 'gemini-api-key'}}}),
        encoding='utf-8')
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    rt = GeminiRuntime()  # fresh instance — clean _auth_cache
    rt._bin_cache = str(tmp_path / 'gemini')
    (tmp_path / 'gemini').write_text('')
    monkeypatch.setattr(
        agent_runtime.subprocess, 'run',
        lambda *a, **k: subprocess.CompletedProcess(
            args=a[0] if a else [], returncode=0, stdout='0.59.0', stderr=''))
    hs = rt.health_check()
    assert hs.auth_state.status == 'ok'
    assert hs.auth_state.method == 'keychain:gemini-api-key'


def test_gemini_auth_probe_keychain_api_key_success(monkeypatch, tmp_path):
    """auth_probe() must spend a real gemini CLI call for a keychain-native
    key (never an HTTP call — there is no key value to send) and report ok
    on success, never reading/logging the key itself."""
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    gdir = tmp_path / '.gemini'
    gdir.mkdir()
    (gdir / 'settings.json').write_text(
        json.dumps({'security': {'auth': {'selectedType': 'gemini-api-key'}}}),
        encoding='utf-8')
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    rt = GeminiRuntime()
    rt._bin_cache = str(tmp_path / 'gemini')
    (tmp_path / 'gemini').write_text('')
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout='OK', stderr='')

    monkeypatch.setattr(agent_runtime.subprocess, 'run', fake_run)
    state = rt.auth_probe()
    assert state['ok'] is True
    assert state['status'] == 'ok'
    assert state['method'] == 'keychain:gemini-api-key'
    assert len(calls) == 1
    cmd = calls[0]
    assert '--allowed-mcp-server-names' in cmd and '__clayrune_none__' in cmd
    assert '-p' in cmd


def test_gemini_auth_probe_keychain_api_key_failure_surfaces_cli_error(monkeypatch, tmp_path):
    """A dead/expired keychain key must surface the CLI's own error text,
    not a generic failure."""
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    gdir = tmp_path / '.gemini'
    gdir.mkdir()
    (gdir / 'settings.json').write_text(
        json.dumps({'security': {'auth': {'selectedType': 'gemini-api-key'}}}),
        encoding='utf-8')
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    rt = GeminiRuntime()
    rt._bin_cache = str(tmp_path / 'gemini')
    (tmp_path / 'gemini').write_text('')

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd, returncode=1, stdout='', stderr='Error: API key not valid')

    monkeypatch.setattr(agent_runtime.subprocess, 'run', fake_run)
    state = rt.auth_probe()
    assert state['ok'] is False
    assert 'API key not valid' in (state['error_text'] or '')


# ─────────────────────────────────────────────────────────────────────────────
# Per-provider model catalogs (composer "Model" picker)
# ─────────────────────────────────────────────────────────────────────────────


def test_every_runtime_exposes_a_model_catalog(monkeypatch, tmp_path):
    """model_choices() is what the composer's Model picker renders. Shape must
    hold for every runtime — a bad entry would break the picker for all."""
    from conftest import stub_codex_models_cache
    stub_codex_models_cache(monkeypatch, tmp_path)
    for rt in agent_runtime.available_runtimes():
        for entry in rt.model_choices():
            assert isinstance(entry, tuple) and len(entry) == 2, (rt.name, entry)
            mid, label = entry
            assert mid and label, (rt.name, entry)


def test_kiro_catalog_empty_because_it_has_no_model_flag():
    """kiro-cli's build_command ignores `model`, so offering a picker would be
    a dead control. Empty catalog is what hides it."""
    rt = KiroRuntime()
    assert rt.model_choices() == []
    assert '--model' not in rt.build_command(model='gpt-5')


def test_model_catalogs_do_not_cross_providers(monkeypatch, tmp_path):
    """The bug this guards: a project's `agent_model` is always a claude id, and
    it used to be forwarded verbatim to every runtime — `codex -m claude-opus-5`
    fails at the CLI. model_supported() is the gate that stops the inherit."""
    from conftest import stub_codex_models_cache
    stub_codex_models_cache(monkeypatch, tmp_path)
    codex = agent_runtime.get_runtime('codex')
    assert not codex.model_supported('claude-opus-5')
    assert codex.model_supported('gpt-5.6-sol')

    claude = agent_runtime.get_runtime('claude')
    assert claude.model_supported('claude-opus-5')
    assert not claude.model_supported('gpt-5')

    # OpenCode addresses models as provider/model — a bare id is not one of ours.
    oc = agent_runtime.get_runtime('opencode')
    assert oc.model_supported('anthropic/claude-sonnet-5')
    assert not oc.model_supported('claude-sonnet-5')


# ── Codex live model-catalog cache (GPT-6, MC dynamic-catalog change) ─────────

def test_codex_catalog_reads_the_live_cache_ordered_by_priority(monkeypatch, tmp_path):
    """model_choices() must read ~/.codex/models_cache.json (stubbed here, per
    AGENT_RULES.md — tests never touch the real one), ordered by `priority`,
    excluding 'hide'-visibility entries, with a readable label built from
    display_name."""
    from conftest import stub_codex_models_cache
    stub_codex_models_cache(monkeypatch, tmp_path)
    codex = agent_runtime.get_runtime('codex')
    assert codex.model_choices() == [
        ('gpt-6-astra', 'GPT-6 Astra'),
        ('gpt-6-sol', 'GPT-6 Sol'),
        ('gpt-6-luna', 'GPT-6 Luna'),
        ('gpt-5.6-sol', 'GPT-5.6 Sol'),
        ('gpt-5.6-terra', 'GPT-5.6 Terra'),
        ('gpt-5.6-luna', 'GPT-5.6 Luna'),
        ('gpt-5.5', 'GPT-5.5'),
    ]


def test_codex_catalog_caches_by_file_mtime(monkeypatch, tmp_path):
    """A second call must not re-read the file while mtime is unchanged, and
    must pick up an edit once mtime moves."""
    from conftest import stub_codex_models_cache
    path = stub_codex_models_cache(monkeypatch, tmp_path)
    codex = agent_runtime.get_runtime('codex')
    first = codex.model_choices()
    assert first[0] == ('gpt-6-astra', 'GPT-6 Astra')

    # Same mtime: edit the bytes but don't touch the clock — must still see
    # the cached (stale) result.
    stat = path.stat()
    new_models = [{'slug': 'gpt-7-nova', 'display_name': 'GPT-7-Nova',
                   'visibility': 'list', 'priority': 1}]
    path.write_text(json.dumps({'models': new_models}), encoding='utf-8')
    os.utime(path, (stat.st_atime, stat.st_mtime))
    assert codex.model_choices() == first

    # New mtime: must reparse.
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))
    assert codex.model_choices() == [('gpt-7-nova', 'GPT-7 Nova')]


@pytest.mark.parametrize('missing,models', [
    (True, None),           # no cache file at all
    (False, None),          # cache file present, `models` field malformed
    (False, []),            # cache file present, no 'list'-visibility entries
])
def test_codex_catalog_falls_back_to_static_list_on_bad_cache(monkeypatch, tmp_path, missing, models):
    from conftest import stub_codex_models_cache
    stub_codex_models_cache(monkeypatch, tmp_path, models=models, missing=missing)
    codex = agent_runtime.get_runtime('codex')
    assert codex.model_choices() == codex.MODEL_CHOICES
    assert codex.MODEL_CHOICES[0] == ('gpt-6-astra', 'GPT-6 Astra')
    assert ('gpt-5.4', 'GPT-5.4') not in codex.MODEL_CHOICES
    assert ('gpt-5.4-mini', 'GPT-5.4 Mini') not in codex.MODEL_CHOICES


def test_codex_model_supported_still_accepts_retired_ids(monkeypatch, tmp_path):
    """Catalog membership is a display concern, not a whitelist (base-class
    contract at AgentRuntime.MODEL_CHOICES' own docstring): gpt-5.4/-mini
    dropped out of the live catalog but a stored pin/config value naming them
    must not suddenly start being treated as unrecognized."""
    from conftest import stub_codex_models_cache
    stub_codex_models_cache(monkeypatch, tmp_path)
    codex = agent_runtime.get_runtime('codex')
    assert codex.model_supported('gpt-5.4')
    assert codex.model_supported('gpt-5.4-mini')
    assert not codex.model_supported('')
    assert not codex.model_supported('claude-opus-5')


def test_codex_tier_heads_follow_gpt6_naming(monkeypatch, tmp_path):
    from conftest import stub_codex_models_cache
    stub_codex_models_cache(monkeypatch, tmp_path)
    codex = agent_runtime.get_runtime('codex')
    assert codex.tier_family('gpt-6-astra') == 'best'
    assert codex.tier_family('gpt-6-sol') == 'balanced'
    assert codex.tier_family('gpt-6-luna') == 'fast'
    # -terra is a variant, not a tier; bare/unsuffixed ids occupy no tier.
    assert codex.tier_family('gpt-5.6-terra') == ''
    assert codex.tier_family('gpt-5.5') == ''
    assert codex.latest_for('best') == 'gpt-6-astra'
    assert codex.latest_for('balanced') == 'gpt-6-sol'
    assert codex.latest_for('fast') == 'gpt-6-luna'


def test_codex_latest_for_falls_back_to_tier_aliases_when_cache_unreadable(monkeypatch, tmp_path):
    from conftest import stub_codex_models_cache
    stub_codex_models_cache(monkeypatch, tmp_path, missing=True)
    codex = agent_runtime.get_runtime('codex')
    assert codex.latest_for('best') == codex.TIER_ALIASES['best']
    assert codex.latest_for('balanced') == codex.TIER_ALIASES['balanced']
    assert codex.latest_for('fast') == codex.TIER_ALIASES['fast']


def test_codex_is_stale_pin_flags_prior_generation_balanced_pin(monkeypatch, tmp_path):
    """A pin from before GPT-6 (gpt-5.6-sol, the old 'balanced' head) must be
    flagged stale against today's catalog; the current head must not be."""
    from conftest import stub_codex_models_cache
    from mc.engine_selection import is_stale_pin
    stub_codex_models_cache(monkeypatch, tmp_path)
    assert is_stale_pin('codex', 'gpt-5.6-sol') is True
    assert is_stale_pin('codex', 'gpt-6-sol') is False


def test_model_supported_rejects_empty(monkeypatch, tmp_path):
    from conftest import stub_codex_models_cache
    stub_codex_models_cache(monkeypatch, tmp_path)
    assert not agent_runtime.get_runtime('codex').model_supported('')


@pytest.mark.parametrize('provider,flag', [
    ('gemini', '--model'), ('codex', '-m'), ('opencode', '--model'),
    ('goose', '--model'), ('aider', '--model'),
])
def test_catalog_ids_reach_the_cli_flag(provider, flag, monkeypatch, tmp_path):
    """Every catalogued id must actually survive into the spawn command."""
    if provider == 'codex':
        from conftest import stub_codex_models_cache
        stub_codex_models_cache(monkeypatch, tmp_path)
    rt = agent_runtime.get_runtime(provider)
    mid = rt.model_choices()[0][0]
    cmd = rt.build_command(model=mid)
    assert flag in cmd and cmd[cmd.index(flag) + 1] == mid


def test_session_model_survives_a_respawn():
    """Mode A respawns the CLI per turn and --model is not sticky, so
    write_followup re-reads the model off the session dict. Without this a chat
    started on a chosen model silently reverted to the CLI default at turn 2."""
    handle = agent_runtime.SessionHandle(
        mc_session_id='abc', provider='codex', mode='A',
        project_path='.', project_id='p',
        session_dict={'agent_model': 'gpt-5.6-sol'},
    )
    assert agent_runtime.AgentRuntime.session_model(handle) == 'gpt-5.6-sol'
    # Absent/garbage session dicts must degrade to "provider default", not raise.
    empty = agent_runtime.SessionHandle(
        mc_session_id='abc', provider='codex', mode='A',
        project_path='.', project_id='p', session_dict={})
    assert agent_runtime.AgentRuntime.session_model(empty) == ''


class TestModeAReaderProtocolNoise:
    """The Mode-A reader must not print a provider's protocol JSON at the user.

    Regression: `parse_event` returning None meant "log this line verbatim", so
    every event a runtime deliberately suppressed (codex's `turn.started`) or
    did not yet know about landed in the chat pane as raw JSONL — including
    `command_execution` items carrying a whole file's worth of
    `aggregated_output`.
    """

    def test_protocol_json_is_recognised(self):
        assert agent_runtime._is_protocol_json('{"type":"turn.started"}') is True
        assert agent_runtime._is_protocol_json('  {"a": 1}  ') is True

    def test_non_json_output_is_not_protocol(self):
        """Stray human-readable output must still reach the log."""
        assert agent_runtime._is_protocol_json('Reading prompt from stdin...') is False
        assert agent_runtime._is_protocol_json('Traceback (most recent call last):') is False
        assert agent_runtime._is_protocol_json('') is False
        assert agent_runtime._is_protocol_json('{not json}') is False
        # A JSON array is not an event envelope.
        assert agent_runtime._is_protocol_json('[1, 2, 3]') is False


# ─────────────────────────────────────────────────────────────────────────────
# MC-931 follow-up: opencode/goose/aider/kiro built the whole persona+task
# payload into argv (prompt_via_stdin=False) — the same 8191-char Windows
# command-line hazard the gemini fix (this file's TestGeminiRuntime) already
# addressed. opencode and goose have a documented stdin form for the task
# text; aider has a --message-file form; kiro has neither and gets a hard
# pre-flight guard instead of a mangled command line.
# ─────────────────────────────────────────────────────────────────────────────


BIG_PROMPT = 'x' * 9000  # over Windows' ~8191-char cmd.exe limit


class TestWinArgvLengthGuard:
    def test_noop_under_limit(self):
        agent_runtime._guard_win_argv_length(['prog', '--flag', 'short'], 'test')

    def test_raises_over_limit_on_windows(self, monkeypatch):
        monkeypatch.setattr(sys, 'platform', 'win32')
        with pytest.raises(RuntimeError, match='over Windows'):
            agent_runtime._guard_win_argv_length(['prog', BIG_PROMPT], 'the thing')

    def test_noop_off_windows_even_over_limit(self, monkeypatch):
        monkeypatch.setattr(sys, 'platform', 'linux')
        agent_runtime._guard_win_argv_length(['prog', BIG_PROMPT], 'the thing')


class TestOpenCodeArgvSafety:
    def setup_method(self):
        self.rt = OpenCodeRuntime()
        self.rt._bin_cache = 'opencode'

    def test_dispatch_big_task_goes_via_stdin_not_argv(self, monkeypatch):
        captured = {}

        def fake_mode_a_dispatch(runtime, cmd, full_prompt, *args, **kwargs):
            captured['cmd'] = cmd
            captured['full_prompt'] = full_prompt
            captured['prompt_via_stdin'] = kwargs.get('prompt_via_stdin')
            return object()

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', fake_mode_a_dispatch)
        self.rt.dispatch(project_path='.', task=BIG_PROMPT, system_prompt='persona',
                         mc_session_id='t1', project_id='p1')
        assert captured['prompt_via_stdin'] is True
        assert not any(BIG_PROMPT in part for part in captured['cmd'])
        assert BIG_PROMPT in captured['full_prompt']

    def test_oneshot_big_prompt_uses_input_not_argv(self, monkeypatch):
        import subprocess as sp

        captured = {}

        def fake_run(cmd, **kwargs):
            captured['cmd'] = cmd
            captured['input'] = kwargs.get('input')
            class R:
                stdout = ''
            return R()

        monkeypatch.setattr(sp, 'run', fake_run)
        self.rt.oneshot(prompt=BIG_PROMPT)
        assert not any(BIG_PROMPT in part for part in captured['cmd'])
        assert captured['input'] == BIG_PROMPT


class TestGooseArgvSafety:
    def setup_method(self):
        self.rt = GooseRuntime()
        self.rt._bin_cache = 'goose'

    def test_dispatch_big_task_goes_via_stdin_not_argv(self, monkeypatch):
        captured = {}

        def fake_mode_a_dispatch(runtime, cmd, full_prompt, *args, **kwargs):
            captured['cmd'] = cmd
            captured['prompt_via_stdin'] = kwargs.get('prompt_via_stdin')
            return object()

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', fake_mode_a_dispatch)
        self.rt.dispatch(project_path='.', task=BIG_PROMPT, system_prompt='short persona',
                         mc_session_id='t1', project_id='p1')
        assert captured['prompt_via_stdin'] is True
        assert '--instructions' in captured['cmd']
        idx = captured['cmd'].index('--instructions')
        assert captured['cmd'][idx + 1] == '-'
        assert not any(BIG_PROMPT in part for part in captured['cmd'])

    def test_dispatch_raises_when_system_prompt_alone_is_too_big(self, monkeypatch):
        """--system has no stdin/file form in the real goose CLI (verified against
        block/goose's cli.rs InputOptions/Run struct) — a huge persona there must
        fail loudly, not get silently mangled by Windows."""
        monkeypatch.setattr(sys, 'platform', 'win32')

        def fake_mode_a_dispatch(*args, **kwargs):
            pytest.fail('should never reach _mode_a_dispatch — guard must raise first')

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', fake_mode_a_dispatch)
        with pytest.raises(RuntimeError, match='over Windows'):
            self.rt.dispatch(project_path='.', task='short task',
                             system_prompt=BIG_PROMPT,
                             mc_session_id='t1', project_id='p1')

    def test_oneshot_big_task_uses_input_not_argv(self, monkeypatch):
        import subprocess as sp

        captured = {}

        def fake_run(cmd, **kwargs):
            captured['cmd'] = cmd
            captured['input'] = kwargs.get('input')
            class R:
                stdout = ''
            return R()

        monkeypatch.setattr(sp, 'run', fake_run)
        self.rt.oneshot(prompt=BIG_PROMPT)
        assert '--instructions' in captured['cmd']
        assert not any(BIG_PROMPT in part for part in captured['cmd'])
        assert captured['input'] == BIG_PROMPT


class TestAiderArgvSafety:
    def setup_method(self):
        self.rt = AiderRuntime()
        self.rt._bin_cache = 'aider'

    def test_dispatch_big_task_goes_via_message_file_not_argv(self, monkeypatch, tmp_path):
        captured = {}

        def fake_mode_a_dispatch(runtime, cmd, full_prompt, *args, **kwargs):
            captured['cmd'] = cmd
            return object()

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', fake_mode_a_dispatch)
        self.rt.dispatch(project_path='.', task=BIG_PROMPT,
                         mc_session_id='t1', project_id='p1')
        cmd = captured['cmd']
        assert '--message' not in cmd
        assert '--message-file' in cmd
        msg_file = cmd[cmd.index('--message-file') + 1]
        assert not any(BIG_PROMPT in part for part in cmd)
        assert Path(msg_file).read_text(encoding='utf-8') == BIG_PROMPT

    def test_write_followup_big_message_goes_via_message_file(self, monkeypatch):
        import subprocess as sp

        captured = {}

        def fake_popen(cmd, **kwargs):
            captured['cmd'] = cmd
            class P:
                def poll(self):
                    return 0
            return P()

        monkeypatch.setattr(sp, 'Popen', fake_popen)
        monkeypatch.setattr(agent_runtime.threading, 'Thread',
                            lambda *a, **k: type('T', (), {'start': lambda self: None})())

        handle = agent_runtime.SessionHandle(
            mc_session_id='t1', provider='aider', mode='A',
            project_path='.', project_id='p1',
            session_dict={'log_lines': [], '_system_prompt': ''},
        )
        self.rt.write_followup(handle, BIG_PROMPT)
        cmd = captured['cmd']
        assert '--message' not in cmd
        assert '--message-file' in cmd
        msg_file = cmd[cmd.index('--message-file') + 1]
        assert BIG_PROMPT in Path(msg_file).read_text(encoding='utf-8')

    def test_oneshot_big_prompt_goes_via_message_file(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured['cmd'] = cmd
            class R:
                stdout = ''
            return R()

        import subprocess as sp
        monkeypatch.setattr(sp, 'run', fake_run)
        self.rt.oneshot(prompt=BIG_PROMPT)
        cmd = captured['cmd']
        assert '--message-file' in cmd
        assert not any(BIG_PROMPT in part for part in cmd)


class TestKiroArgvSafety:
    """kiro-cli's headless prompt is positional-only — no --prompt-file, no
    stdin form (piped stdin is documented as extra context, not a prompt
    replacement). There is no fix but a hard guard; verify it actually fires
    instead of silently building an oversized command line."""

    def setup_method(self):
        self.rt = KiroRuntime()
        self.rt._bin_cache = 'kiro-cli'

    def test_dispatch_raises_on_oversized_prompt(self, monkeypatch):
        monkeypatch.setattr(sys, 'platform', 'win32')

        def fake_mode_a_dispatch(*args, **kwargs):
            pytest.fail('should never reach _mode_a_dispatch — guard must raise first')

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', fake_mode_a_dispatch)
        with pytest.raises(RuntimeError, match='over Windows'):
            self.rt.dispatch(project_path='.', task=BIG_PROMPT,
                             mc_session_id='t1', project_id='p1')

    def test_write_followup_raises_on_oversized_prompt(self, monkeypatch):
        monkeypatch.setattr(sys, 'platform', 'win32')
        handle = agent_runtime.SessionHandle(
            mc_session_id='t1', provider='kiro', mode='A',
            project_path='.', project_id='p1',
            session_dict={'log_lines': [], '_system_prompt': BIG_PROMPT},
        )
        with pytest.raises(RuntimeError, match='over Windows'):
            self.rt.write_followup(handle, 'a short followup message')

    def test_oneshot_raises_on_oversized_prompt(self, monkeypatch):
        monkeypatch.setattr(sys, 'platform', 'win32')
        with pytest.raises(RuntimeError, match='over Windows'):
            self.rt.oneshot(prompt=BIG_PROMPT)

    def test_small_prompt_is_unaffected(self, monkeypatch):
        """The guard must not false-positive on the common case."""
        monkeypatch.setattr(sys, 'platform', 'win32')
        captured = {}

        def fake_mode_a_dispatch(runtime, cmd, full_prompt, *args, **kwargs):
            captured['cmd'] = cmd
            return object()

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', fake_mode_a_dispatch)
        self.rt.dispatch(project_path='.', task='say hi',
                         mc_session_id='t1', project_id='p1')
        assert 'say hi' in ' '.join(captured['cmd'])
