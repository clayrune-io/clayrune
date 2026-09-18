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
    def test_build_command_adds_c_overrides_when_file_exists(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'codex': FAKE_HOOKS_PATH})
        rt = CodexRuntime()
        rt._bin_cache = 'codex'
        cmd = rt.build_command()
        joined = ' '.join(cmd)
        assert f"hooks='{FAKE_HOOKS_PATH}'" in joined
        assert 'bypass_hook_trust=true' in joined

    def test_build_command_omits_c_overrides_when_not_generated_yet(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'codex': None})
        rt = CodexRuntime()
        rt._bin_cache = 'codex'
        cmd = rt.build_command()
        assert not any('hooks=' in c for c in cmd)
        assert not any('bypass_hook_trust' in c for c in cmd)

    def test_c_overrides_present_on_resume_branch_too(self, monkeypatch):
        _patch_launch_file(monkeypatch, {'codex': FAKE_HOOKS_PATH})
        rt = CodexRuntime()
        rt._bin_cache = 'codex'
        cmd = rt.build_command(resume_id='abc123')
        joined = ' '.join(cmd)
        assert 'hooks=' in joined
        assert 'bypass_hook_trust=true' in joined


class TestGeminiDispatchInjection:
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


class TestNothingWrittenToRealClayruneHome:
    def test_launch_file_if_exists_reads_real_home_by_default_but_tests_never_call_it_unpatched(self):
        # Documents the contract these tests all rely on: launch_file_if_exists
        # with no override reads the REAL ~/.clayrune/hooks/ — every test above
        # monkeypatches agent_runtime._guardrail_launch_file specifically so
        # none of them ever touch it, whether or not this box has ever
        # generated real guardrail files.
        result = launch_file_if_exists('claude')
        assert result is None or result.is_file()  # real filesystem state, not asserted either way
