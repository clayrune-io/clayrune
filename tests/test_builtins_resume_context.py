"""BUILTINS-2: capture real runtime spawn inputs without vendor requests."""
import io
from types import SimpleNamespace

import pytest

from mc import agent_runtime as ar, claude_resume_snapshot as snapshot
from mc import launch_marker, state
from mc.resume_context import resume_prompt, strip_resume_context


class Stdin(io.StringIO):
    def close(self):
        pass  # keep captured bytes available to assertions


class Thread:
    def __init__(self, target, args=(), name='', **kwargs):
        self.target, self.args, self.name = target, args, name

    def start(self):
        if 'stdin' in self.name:
            self.target(*self.args)


@pytest.fixture
def spawns(monkeypatch):
    captured = []

    def popen(cmd, **kwargs):
        proc = SimpleNamespace(stdin=Stdin(), stdout=iter([]), pid=999999,
                               poll=lambda: 0, wait=lambda: 0)
        captured.append((cmd, proc))
        return proc

    monkeypatch.setattr(ar.subprocess, 'Popen', popen)
    monkeypatch.setattr(ar.threading, 'Thread', Thread)
    monkeypatch.setattr(ar, '_codex_fence_self_test', lambda: (True, 'test'))
    monkeypatch.setattr(ar.CodexRuntime, '_gate_model', lambda self, model: (model, ''))
    monkeypatch.setattr(ar, '_inject_guardrail_env', lambda *args: None)
    monkeypatch.setattr(ar, '_inject_gemini_env', lambda env, *args: env)
    monkeypatch.setattr(ar.QwenRuntime, '_settings_auth_env', lambda self: {})
    # Gemini has a separate stdin helper; capture the same pipe boundary.
    monkeypatch.setattr(ar.GeminiRuntime, '_write_prompt_async',
                        staticmethod(lambda proc, prompt, sid: proc.stdin.write(prompt)))
    return captured


@pytest.mark.parametrize('engine', ['codex', 'qwen', 'gemini'])
@pytest.mark.parametrize('cold', [True, False], ids=['cold-revive', 'warm-followup'])
def test_changed_persona_and_rules_reach_native_resume(engine, cold, spawns, monkeypatch):
    runtime = ar.get_runtime(engine)
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: engine)
    session = {'_system_prompt': 'PERSONA_ALPHA RULES_ALPHA',
               'provider_session_id': 'native-thread', 'log_lines': [],
               'trigger_type': 'manual', 'proc': None}
    if cold:
        # Each cold revival receives freshly built instructions from routes.
        for context in ('PERSONA_ALPHA RULES_ALPHA', 'PERSONA_BRAVO RULES_BRAVO'):
            runtime.dispatch(project_path='.', project_id='p', task='next task',
                             system_prompt=context, resume_id='native-thread',
                             session_dict=session, mc_session_id='test')
    else:
        handle = ar.SessionHandle(mc_session_id='test', provider=engine, mode='A',
                                  project_path='.', project_id='p', session_dict=session)
        runtime.write_followup(handle, 'next task')
        # Routes refresh the stash with _fresh_context_for before each send.
        session['_system_prompt'] = 'PERSONA_BRAVO RULES_BRAVO'
        runtime.write_followup(handle, 'next task')
    first, second = [proc.stdin.getvalue() for _, proc in spawns]
    assert 'PERSONA_ALPHA RULES_ALPHA' in first
    assert 'PERSONA_BRAVO RULES_BRAVO' in second
    assert 'PERSONA_ALPHA' not in second
    assert 'CURRENT CLAYRUNE CONTEXT' in second
    assert second.endswith('next task')
    for cmd, _ in spawns:
        assert 'native-thread' in cmd  # keep native continuity, never freshen


def test_empty_context_does_not_recover_older_instructions():
    assert resume_prompt('', 'next task') == 'next task'
    assert resume_prompt('  ', 'next task') == 'next task'


def test_saved_user_message_keeps_real_text_and_newlines():
    prompt = resume_prompt('CURRENT PERSONA', 'task\n\n---\n\nmore task')
    assert strip_resume_context(prompt) == 'task\n\n---\n\nmore task'
    assert ar._strip_codex_system_prefix(prompt.replace('\n', '\r\n')) == (
        'task\r\n\r\n---\r\n\r\nmore task')
    assert strip_resume_context('ordinary user message') == 'ordinary user message'


def test_quoted_boundaries_in_persona_and_task_are_not_message_boundaries():
    context = "Persona quotes === THE USER'S MESSAGE === in its examples."
    task = "Explain === THE USER'S MESSAGE ===\n\n---\n\nand keep this text."
    assert strip_resume_context(resume_prompt(context, task)) == task


def test_unchanged_context_keeps_the_same_prefix_for_new_messages():
    assert resume_prompt('CURRENT RULES', 'one')[:-3] == resume_prompt('CURRENT RULES', 'two')[:-3]


@pytest.mark.parametrize('flag', ['-r', '--resume', '-c', '--continue', '--resume=thread'])
def test_snapshot_policy_opt_in_only_for_resume(flag):
    cmd = ['claude', flag, 'thread', '--settings', 'fence.json']
    assert snapshot.launch_args(cmd, {}) is cmd
    out = snapshot.launch_args(cmd, {snapshot.CONFIG_KEY: True})
    assert out[-2:] == ['--system-prompt-snapshot', 'off']
    assert out[:-2] == cmd
    assert snapshot.launch_args(out, {snapshot.CONFIG_KEY: True}) == out


def test_snapshot_does_not_apply_to_fresh_dispatch():
    cmd = ['claude', '-p']
    assert snapshot.launch_args(cmd, {snapshot.CONFIG_KEY: True}) is cmd


@pytest.mark.parametrize('existing', [
    ['--system-prompt-snapshot', 'on'], ['--system-prompt-snapshot=on']])
def test_snapshot_enabled_overrides_previous_value(existing):
    out = snapshot.launch_args(['claude', '-r', 'thread', *existing],
                               {snapshot.CONFIG_KEY: True})
    assert out == ['claude', '-r', 'thread', '--system-prompt-snapshot', 'off']


@pytest.mark.parametrize('trigger', ['manual', 'dispatch'])
def test_shared_boundary_applies_snapshot_before_fence(trigger, monkeypatch):
    from mc import fence_fail_closed
    seen = []
    monkeypatch.setitem(state.CONFIG, snapshot.CONFIG_KEY, True)
    monkeypatch.setattr(launch_marker, '_fence_enabled', lambda: True)
    monkeypatch.setattr(fence_fail_closed, 'launch_cmd',
                        lambda cmd, *args: seen.append(cmd) or cmd + ['fence-tested'])
    out = launch_marker.launch_cmd(['claude', '--resume', 'thread'], trigger, '.')
    assert '--system-prompt-snapshot' in out
    assert bool(seen) == (trigger == 'dispatch')
    if seen:
        assert seen[0][-2:] == ['--system-prompt-snapshot', 'off']
        assert out[-1] == 'fence-tested'
