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
# Registry: all 7 providers registered
# ─────────────────────────────────────────────────────────────────────────────


def test_all_providers_registered():
    names = {r.name for r in agent_runtime.available_runtimes()}
    expected = {'claude', 'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro'}
    assert expected.issubset(names), f"Missing: {expected - names}"


def test_get_runtime_all_providers():
    for name in ('claude', 'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro'):
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
        # real reason (quota, auth, ...) in error.message. Before this fix
        # it matched the generic 'result' branch and returned TURN_END with
        # only usage/cost — the error text was read by nothing and reached
        # neither the transcript nor explain_exit_error's tail scan, so a
        # real API error (e.g. "You have exhausted your daily quota on this
        # model.") was silently dropped and the user saw a generic
        # "exited with code 1" instead.
        line = json.dumps({
            'type': 'result', 'status': 'error',
            'error': {'type': 'Error',
                     'message': '[API Error: You have exhausted your daily quota on this model.]'},
            'stats': {'total_tokens': 0},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ERROR
        assert 'exhausted your daily quota' in ev.payload['text']

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

    def test_build_command_npx_fallback(self):
        """When binary not found, uses npx @openai/codex prefix"""
        self.rt._bin_cache = '__npx__'
        self.rt._npx_fallback = True
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

    def test_auth_state_reads_chatgpt_oauth(self, monkeypatch, tmp_path):
        """`codex login` writes OAuth tokens to ~/.codex/auth.json, not an env var.

        Regression: checking only CODEX_API_KEY/OPENAI_API_KEY reported a fully
        signed-in install as 'unknown', which the settings UI shows as needing auth.
        """
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
        assert self.rt._codex_auth_state() == ('ok', 'chatgpt oauth')

    def test_auth_state_not_logged_in_without_credentials(self, monkeypatch, tmp_path):
        monkeypatch.delenv('CODEX_API_KEY', raising=False)
        monkeypatch.delenv('OPENAI_API_KEY', raising=False)
        monkeypatch.setenv('USERPROFILE', str(tmp_path))
        monkeypatch.setenv('HOME', str(tmp_path))
        assert self.rt._codex_auth_state() == ('not_logged_in', None)

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


# ─────────────────────────────────────────────────────────────────────────────
# Per-provider model catalogs (composer "Model" picker)
# ─────────────────────────────────────────────────────────────────────────────


def test_every_runtime_exposes_a_model_catalog():
    """model_choices() is what the composer's Model picker renders. Shape must
    hold for every runtime — a bad entry would break the picker for all."""
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


def test_model_catalogs_do_not_cross_providers():
    """The bug this guards: a project's `agent_model` is always a claude id, and
    it used to be forwarded verbatim to every runtime — `codex -m claude-opus-5`
    fails at the CLI. model_supported() is the gate that stops the inherit."""
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


def test_codex_catalog_matches_current_cli_models():
    """The picker must not keep retired model ids after Codex moves on."""
    codex = agent_runtime.get_runtime('codex')
    assert [model_id for model_id, _label in codex.model_choices()] == [
        # Added 2026-09-08: the live CLI (0.153.4) defaults to this, and the
        # picker had gone a release behind without anything failing.
        'gpt-6-astra',
        'gpt-5.6-sol',
        'gpt-5.6-terra',
        'gpt-5.6-luna',
        'gpt-5.5',
        'gpt-5.4',
        'gpt-5.4-mini',
    ]


def test_model_supported_rejects_empty():
    assert not agent_runtime.get_runtime('codex').model_supported('')


@pytest.mark.parametrize('provider,flag', [
    ('gemini', '--model'), ('codex', '-m'), ('opencode', '--model'),
    ('goose', '--model'), ('aider', '--model'),
])
def test_catalog_ids_reach_the_cli_flag(provider, flag):
    """Every catalogued id must actually survive into the spawn command."""
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
