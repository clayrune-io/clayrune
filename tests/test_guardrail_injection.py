"""Per-launch guardrail injection (W2 redesign) — the actual dispatch-time
wiring, as opposed to tests/test_install_hooks.py (generation) and
mc/guardrail_hooks.py's own contract. Every test here monkeypatches
`agent_runtime._guardrail_launch_file` so nothing touches the real
`~/.clayrune/hooks/` — these tests must pass identically whether or not
guardrail generation has ever run on the machine executing them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import agent_runtime
from mc.agent_runtime import ClaudeRuntime, CodexRuntime, GeminiRuntime, QwenRuntime
from mc.guardrail_hooks import launch_file_if_exists


FAKE_HOOKS_PATH = Path('C:/fake/clayrune/hooks/vendor-settings.json')


def _patch_launch_file(monkeypatch, mapping):
    """mapping: {vendor: Path or None}"""
    monkeypatch.setattr(agent_runtime, '_guardrail_launch_file',
                        lambda vendor: mapping.get(vendor))


class TestInjectGuardrailEnvHelper:
    def test_adds_env_var_when_file_exists(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'gemini': FAKE_HOOKS_PATH})
        env = {}
        agent_runtime._inject_guardrail_env('gemini', env)
        assert env['GEMINI_CLI_SYSTEM_SETTINGS_PATH'] == str(FAKE_HOOKS_PATH)

    def test_no_op_when_file_missing(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'gemini': None})
        env = {'EXISTING': '1'}
        agent_runtime._inject_guardrail_env('gemini', env)
        assert env == {'EXISTING': '1'}

    def test_no_op_for_a_vendor_with_no_env_mechanism(self, monkeypatch):
        # claude/codex inject via argv, not env — this helper must never
        # invent an env var for them even if a file happens to exist.
        _patch_launch_file(monkeypatch, {'claude': FAKE_HOOKS_PATH})
        env = {}
        agent_runtime._inject_guardrail_env('claude', env)
        assert env == {}

    def test_qwen_uses_its_own_var_name(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'qwen': FAKE_HOOKS_PATH})
        env = {}
        agent_runtime._inject_guardrail_env('qwen', env)
        assert env == {'QWEN_CODE_SYSTEM_SETTINGS_PATH': str(FAKE_HOOKS_PATH)}


class TestClaudeSettingsInjection:
    def test_build_command_adds_settings_when_file_exists(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'claude': FAKE_HOOKS_PATH})
        rt = ClaudeRuntime()
        rt.resolve_binary_str = lambda: 'claude'
        cmd = rt.build_command()
        assert '--settings' in cmd
        assert cmd[cmd.index('--settings') + 1] == str(FAKE_HOOKS_PATH)

    def test_build_command_omits_settings_when_not_generated_yet(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'claude': None})
        rt = ClaudeRuntime()
        rt.resolve_binary_str = lambda: 'claude'
        cmd = rt.build_command()
        assert '--settings' not in cmd

    def test_settings_flag_survives_on_every_build_command_call(self, monkeypatch):
        # _build_claude_flags (mc/blueprints/agent_routes.py) delegates to
        # THIS method for every dispatch/resume/followup/revive call site —
        # a single fix point here covers the legacy route path too.
        _patch_launch_file(monkeypatch, {'claude': FAKE_HOOKS_PATH})
        rt = ClaudeRuntime()
        rt.resolve_binary_str = lambda: 'claude'
        for kwargs in ({}, {'model': 'sonnet'}, {'streaming': True}, {'effort': 'high'}):
            cmd = rt.build_command(**kwargs)
            assert '--settings' in cmd, f'missing for build_command({kwargs})'


class TestCodexHooksInjection:
    """LIVE REGRESSION FIXED 2026-09-18: the first version injected
    `-c hooks='<path>'` (a generated file), which killed every Codex launch
    at config-parse time — `hooks` is a TOML table, not a file path.
    CodexRuntime now injects the hooks table INLINE via a dotted-path
    override (`mc.guardrail_hooks.codex_hook_config_args`), unconditionally
    — no generated file, so no `_guardrail_launch_file('codex')` gate to
    monkeypatch here at all; these tests exercise the real function.
    """

    def test_build_command_uses_dangerously_bypass_hook_trust_flag(self):
        rt = CodexRuntime()
        rt._bin_cache = 'codex'
        cmd = rt.build_command()
        assert '--dangerously-bypass-hook-trust' in cmd
        # The first version's broken half: a `-c bypass_hook_trust=true`
        # override is not a recognized config field at all (confirmed live
        # with --strict-config: "unknown configuration field").
        assert not any('bypass_hook_trust=' in c for c in cmd)

    def test_build_command_injects_hooks_table_inline_not_as_a_path(self):
        rt = CodexRuntime()
        rt._bin_cache = 'codex'
        cmd = rt.build_command()
        joined = ' '.join(cmd)
        assert 'hooks.PreToolUse=[{matcher="shell"' in joined
        assert 'clayrune-process-guard' in joined
        # Never the broken first-version shape (a bare path as the value).
        assert "hooks='" not in joined
        assert 'hooks.json' not in joined

    def test_c_overrides_present_on_resume_branch_too(self):
        rt = CodexRuntime()
        rt._bin_cache = 'codex'
        cmd = rt.build_command(resume_id='abc123')
        joined = ' '.join(cmd)
        assert 'hooks.PreToolUse=' in joined
        assert '--dangerously-bypass-hook-trust' in joined

    def test_injected_hooks_value_is_accepted_by_a_real_strict_config_parse(self):
        """Live-verified 2026-09-18 against the real codex.exe (0.154.0, no
        allowance): identical argv reached `usage_limit_exceeded` (past
        config parsing, into the real API) under --strict-config, which
        rejects any unrecognized field — see
        docs/GUARDRAIL_PARITY_EVIDENCE.md §4 for the exact command. This
        test pins the STRING SHAPE that was verified, so a future edit to
        codex_hook_config_args() that drifts from it is caught here instead
        of on Ron's live instance again."""
        from mc.guardrail_hooks import codex_hook_config_args
        args = codex_hook_config_args(Path('C:/repo/mc/process_guard.py'), python_exe='C:/py/python.exe')
        assert args[0] == '--dangerously-bypass-hook-trust'
        assert args[1] == '-c'
        value = args[2]
        assert value.startswith('hooks.PreToolUse=[{matcher="shell",hooks=[{type="command",command="')
        assert value.endswith('name="clayrune-process-guard"}]}]')
        # No unescaped double quote inside the command string's own value —
        # exactly the class of bug that broke Gemini (see
        # guard_shell_command's docstring).
        inner = value.split('command=', 1)[1]
        assert inner.startswith('"C:/py/python.exe \\"C:\\\\repo\\\\mc\\\\process_guard.py\\""')


class TestGeminiDispatchInjection:
    def test_build_command_includes_skip_trust(self):
        """LIVE REGRESSION FIXED 2026-09-18: a fresh Gemini 0.59 install (no
        ~/.gemini/trustedFolders.json) refused every headless launch with
        "Gemini CLI is not running in a trusted directory" — reproduced with
        the raw CLI, independent of this guardrail work. `--skip-trust` is
        the CLI's own documented fix."""
        rt = GeminiRuntime()
        rt._bin_cache = 'gemini'
        cmd = rt.build_command()
        assert '--skip-trust' in cmd

    def test_dispatch_env_includes_system_settings_path(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'gemini': FAKE_HOOKS_PATH})
        captured = {}

        class _FakeProc:
            stdin = None
            stdout = iter([])
            def poll(self): return None

        def _fake_popen(*args, **kwargs):
            captured['env'] = kwargs.get('env')
            return _FakeProc()

        monkeypatch.setattr(agent_runtime.subprocess, 'Popen', _fake_popen)
        rt = GeminiRuntime()
        rt._bin_cache = 'gemini'
        monkeypatch.setattr(rt, '_write_prompt_async', lambda *a, **kw: None)
        rt.dispatch(project_path='/p', task='do X', session_dict={})
        assert captured['env']['GEMINI_CLI_SYSTEM_SETTINGS_PATH'] == str(FAKE_HOOKS_PATH)

    def test_dispatch_env_has_no_key_when_not_generated_yet(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'gemini': None})
        captured = {}

        class _FakeProc:
            stdin = None
            stdout = iter([])
            def poll(self): return None

        def _fake_popen(*args, **kwargs):
            captured['env'] = kwargs.get('env')
            return _FakeProc()

        monkeypatch.setattr(agent_runtime.subprocess, 'Popen', _fake_popen)
        rt = GeminiRuntime()
        rt._bin_cache = 'gemini'
        monkeypatch.setattr(rt, '_write_prompt_async', lambda *a, **kw: None)
        rt.dispatch(project_path='/p', task='do X', session_dict={})
        assert 'GEMINI_CLI_SYSTEM_SETTINGS_PATH' not in captured['env']

    def test_write_followup_env_includes_system_settings_path(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'gemini': FAKE_HOOKS_PATH})
        captured = {}

        class _FakeStdin:
            def write(self, s): pass
            def close(self): pass

        class _FakeProc:
            stdin = _FakeStdin()
            stdout = iter([])
            def poll(self): return None

        def _fake_popen(*args, **kwargs):
            captured['env'] = kwargs.get('env')
            return _FakeProc()

        monkeypatch.setattr(agent_runtime.subprocess, 'Popen', _fake_popen)
        rt = GeminiRuntime()
        rt._bin_cache = 'gemini'
        monkeypatch.setattr(rt, '_write_prompt_async', lambda *a, **kw: None)
        monkeypatch.setattr(rt, '_read_stream', lambda *a, **kw: None)
        handle = agent_runtime.SessionHandle(
            mc_session_id='s1', provider='gemini', mode='A',
            project_path='/p', project_id='proj', session_dict={'log_lines': []})
        rt.write_followup(handle, 'hello')
        assert captured['env']['GEMINI_CLI_SYSTEM_SETTINGS_PATH'] == str(FAKE_HOOKS_PATH)


class TestQwenDispatchInjection:
    def test_dispatch_passes_env_with_system_settings_path_to_mode_a_dispatch(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'qwen': FAKE_HOOKS_PATH})
        captured = {}

        def _fake_mode_a_dispatch(self, cmd, full_prompt, project_path, project_id,
                                   task, mc_sid, session_dict, incognito, env,
                                   callbacks, register_process, **kw):
            captured['env'] = env
            return 'HANDLE'

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', _fake_mode_a_dispatch)
        rt = QwenRuntime()
        rt._bin_cache = 'qwen'
        rt.dispatch(project_path='/p', task='do X', session_dict={})
        assert captured['env']['QWEN_CODE_SYSTEM_SETTINGS_PATH'] == str(FAKE_HOOKS_PATH)

    def test_dispatch_env_has_no_key_when_not_generated_yet(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'qwen': None})
        captured = {}

        def _fake_mode_a_dispatch(self, cmd, full_prompt, project_path, project_id,
                                   task, mc_sid, session_dict, incognito, env,
                                   callbacks, register_process, **kw):
            captured['env'] = env
            return 'HANDLE'

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', _fake_mode_a_dispatch)
        rt = QwenRuntime()
        rt._bin_cache = 'qwen'
        rt.dispatch(project_path='/p', task='do X', session_dict={})
        assert 'QWEN_CODE_SYSTEM_SETTINGS_PATH' not in captured['env']

    def test_write_followup_env_includes_system_settings_path(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'qwen': FAKE_HOOKS_PATH})
        captured = {}

        class _FakeStdin:
            def write(self, s): pass
            def close(self): pass

        class _FakeProc:
            stdin = _FakeStdin()
            stdout = iter([])
            def wait(self): return 0
            def poll(self): return None

        def _fake_popen(*args, **kwargs):
            captured['env'] = kwargs.get('env')
            return _FakeProc()

        monkeypatch.setattr(agent_runtime.subprocess, 'Popen', _fake_popen)
        rt = QwenRuntime()
        rt._bin_cache = 'qwen'
        handle = agent_runtime.SessionHandle(
            mc_session_id='s1', provider='qwen', mode='A',
            project_path='/p', project_id='proj',
            session_dict={'log_lines': [], 'proc': None})
        rt.write_followup(handle, 'hello')
        assert captured['env']['QWEN_CODE_SYSTEM_SETTINGS_PATH'] == str(FAKE_HOOKS_PATH)


class TestQwenSettingsWinOverInheritedEnv:
    """LIVE REGRESSION (2026-09-18, third fix on this branch): Ron's real
    Windows USER environment held stale OPENAI_API_KEY/OPENAI_BASE_URL/
    OPENAI_MODEL from an earlier broken Qwen login attempt.
    ~/.qwen/settings.json held the REAL, working DashScope credential —
    but `_settings_auth_env()`'s values were only `setdefault`-ed onto the
    child env, so the broken inherited value always won and every Qwen
    dispatch 404'd. Fixed: a value `_settings_auth_env()` actually returns
    now OVERRIDES, since it represents a deliberate, Qwen-specific choice
    that must beat an ambient generic-named env var.
    """

    def _settings(self, monkeypatch, rt, values):
        monkeypatch.setattr(rt, '_settings_auth_env', lambda: dict(values))

    def test_dispatch_env_extra_overrides_a_stale_openai_base_url(self, monkeypatch):
        monkeypatch.setenv('OPENAI_BASE_URL', 'https://aliyuncs.com')
        monkeypatch.setenv('OPENAI_MODEL', 'qwen-coder-plus-latest')
        captured = {}

        def _fake_mode_a_dispatch(self, cmd, full_prompt, project_path, project_id,
                                   task, mc_sid, session_dict, incognito, env,
                                   callbacks, register_process, **kw):
            captured['env'] = env
            return 'HANDLE'

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', _fake_mode_a_dispatch)
        rt = QwenRuntime()
        rt._bin_cache = 'qwen'
        self._settings(monkeypatch, rt, {
            'OPENAI_API_KEY': 'real-dashscope-key',
            'OPENAI_BASE_URL': 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1',
            'OPENAI_MODEL': 'qwen3-coder-plus',
        })

        rt.dispatch(project_path='/p', task='do X', session_dict={})

        # The env_extra dict passed to _mode_a_dispatch carries the REAL
        # settings.json values — _mode_a_dispatch's own `env.update(env_extra)`
        # (os.environ.copy() first, env_extra second) means these win.
        assert captured['env']['OPENAI_BASE_URL'] == 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'
        assert captured['env']['OPENAI_MODEL'] == 'qwen3-coder-plus'
        assert captured['env']['OPENAI_API_KEY'] == 'real-dashscope-key'

    def test_write_followup_env_overrides_a_stale_openai_base_url(self, monkeypatch):
        monkeypatch.setenv('OPENAI_BASE_URL', 'https://aliyuncs.com')
        captured = {}

        class _FakeStdin:
            def write(self, s): pass
            def close(self): pass

        class _FakeProc:
            stdin = _FakeStdin()
            stdout = iter([])
            def wait(self): return 0
            def poll(self): return None

        def _fake_popen(*args, **kwargs):
            captured['env'] = kwargs.get('env')
            return _FakeProc()

        monkeypatch.setattr(agent_runtime.subprocess, 'Popen', _fake_popen)
        rt = QwenRuntime()
        rt._bin_cache = 'qwen'
        self._settings(monkeypatch, rt, {
            'OPENAI_BASE_URL': 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1',
        })
        handle = agent_runtime.SessionHandle(
            mc_session_id='s1', provider='qwen', mode='A',
            project_path='/p', project_id='proj',
            session_dict={'log_lines': [], 'proc': None})

        rt.write_followup(handle, 'hello')

        assert captured['env']['OPENAI_BASE_URL'] == 'https://dashscope-intl.aliyuncs.com/compatible-mode/v1'

    def test_no_settings_configured_inherits_env_unchanged(self, monkeypatch):
        """Nothing in settings.json → callers inherit the real process env,
        exactly as before this fix."""
        monkeypatch.setenv('OPENAI_BASE_URL', 'https://whatever.example.com')
        captured = {}

        def _fake_mode_a_dispatch(self, cmd, full_prompt, project_path, project_id,
                                   task, mc_sid, session_dict, incognito, env,
                                   callbacks, register_process, **kw):
            captured['env'] = env
            return 'HANDLE'

        monkeypatch.setattr(agent_runtime, '_mode_a_dispatch', _fake_mode_a_dispatch)
        rt = QwenRuntime()
        rt._bin_cache = 'qwen'
        self._settings(monkeypatch, rt, {})

        rt.dispatch(project_path='/p', task='do X', session_dict={})

        assert 'OPENAI_BASE_URL' not in captured['env']  # env_extra carries no override


class TestCodexAuthStateReportsWhatActuallyWins:
    """LIVE REGRESSION (2026-09-18, third fix on this branch): the same
    generic-OPENAI_*-name collision affects Codex's own health check, which
    checked `os.environ.get('OPENAI_API_KEY')` BEFORE reading
    ~/.codex/auth.json — so a Qwen/DashScope key left in the environment
    reported 'ok, env:OPENAI_API_KEY' even on a box logged into Codex via
    ChatGPT OAuth. Live-verified 2026-09-18 (`codex exec`, real dispatch,
    real chatgpt.com `usage_limit_exceeded` response) that a stored ChatGPT
    login is used REGARDLESS of OPENAI_API_KEY/OPENAI_BASE_URL/OPENAI_MODEL
    being present in the environment.
    """

    def test_stored_chatgpt_login_wins_over_a_stray_openai_api_key(self, monkeypatch, tmp_path):
        monkeypatch.setenv('OPENAI_API_KEY', 'sk-some-other-vendors-stray-key')
        monkeypatch.setenv('USERPROFILE', str(tmp_path))
        auth = tmp_path / '.codex' / 'auth.json'
        auth.parent.mkdir(parents=True)
        auth.write_text('{"OPENAI_API_KEY": null, "tokens": {"access_token": "real-chatgpt-token"}}',
                        encoding='utf-8')
        rt = CodexRuntime()

        status, method = rt._codex_auth_state()

        assert status == 'ok'
        assert method == 'chatgpt oauth'

    def test_falls_back_to_env_var_when_no_stored_login_exists(self, monkeypatch, tmp_path):
        monkeypatch.setenv('OPENAI_API_KEY', 'sk-a-real-configured-key')
        monkeypatch.setenv('USERPROFILE', str(tmp_path))
        rt = CodexRuntime()

        status, method = rt._codex_auth_state()

        assert status == 'ok'
        assert method == 'env:OPENAI_API_KEY'


class TestNothingWrittenToRealClayruneHome:
    def test_launch_file_if_exists_reads_real_home_by_default_but_tests_never_call_it_unpatched(self):
        # Documents the contract these tests all rely on: launch_file_if_exists
        # with no override reads the REAL ~/.clayrune/hooks/ — every test above
        # monkeypatches agent_runtime._guardrail_launch_file specifically so
        # none of them ever touch it, whether or not this box has ever
        # generated real guardrail files.
        result = launch_file_if_exists('claude')
        assert result is None or result.is_file()  # real filesystem state, not asserted either way
