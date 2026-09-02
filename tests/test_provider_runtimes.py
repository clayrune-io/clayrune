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
        assert caps.supports_plan_mode is True
        assert caps.emits_cost is True
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
