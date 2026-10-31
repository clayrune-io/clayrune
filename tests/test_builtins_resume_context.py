"""BUILTINS-2: capture real runtime spawn inputs without vendor requests."""
import io
from types import SimpleNamespace

import pytest

from mc import agent_runtime as ar, claude_resume_snapshot as snapshot
from mc import launch_marker, state
from mc.resume_context import (
    context_sha, followup_prompt, resume_prompt, strip_resume_context,
    write_context_prompt,
)


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


@pytest.mark.parametrize('engine', ['codex', 'qwen', 'gemini'])
@pytest.mark.parametrize('resume_id', ['', 'native-thread'], ids=['fresh', 'cold-resume'])
def test_dispatch_records_context_and_unchanged_followups_are_bare(
        engine, resume_id, spawns, monkeypatch):
    runtime = ar.get_runtime(engine)
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: engine)
    # Include text Gemini strips so the fingerprint must use composed context.
    context = 'PERSONA_ALPHA RULES_ALPHA\nCurrent task: stale'
    handle = runtime.dispatch(project_path='.', project_id='p', task='first task',
                              system_prompt=context, resume_id=resume_id,
                              mc_session_id='test')
    session = handle.session_dict
    assert session['_ctx_delivered_sha'] == context_sha(session['_system_prompt'])
    session['provider_session_id'] = 'native-thread'
    for message in ('second task', 'third task'):
        # Same route refresh as a real followup, before provider composition.
        session['_system_prompt'] = context
        runtime.write_followup(handle, message)
        assert spawns[-1][1].stdin.getvalue() == message
        assert 'native-thread' in spawns[-1][0]


@pytest.mark.parametrize('engine', ['codex', 'qwen', 'gemini'])
def test_rules_change_is_sent_once_then_followups_are_bare(engine, spawns, monkeypatch):
    runtime = ar.get_runtime(engine)
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: engine)
    handle = runtime.dispatch(project_path='.', task='first task',
                              system_prompt='PERSONA_ALPHA RULES_ALPHA')
    session = handle.session_dict
    session['provider_session_id'] = 'native-thread'
    original_sha = session['_ctx_delivered_sha']
    for message in ('unchanged', 'changed', 'unchanged again'):
        context = ('PERSONA_ALPHA RULES_ALPHA' if message == 'unchanged'
                   else 'PERSONA_ALPHA RULES_BRAVO')
        session['_system_prompt'] = context
        runtime.write_followup(handle, message)
        prompt = spawns[-1][1].stdin.getvalue()
        if message == 'changed':
            assert prompt == resume_prompt(runtime.with_mc_tool_protocol(context), message)
            assert session['_ctx_delivered_sha'] != original_sha
        else:
            assert prompt == message


@pytest.mark.parametrize('engine', ['codex', 'qwen', 'gemini'])
def test_restart_without_fingerprint_sends_context_once(engine, spawns, monkeypatch):
    runtime = ar.get_runtime(engine)
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: engine)
    session = {'_system_prompt': 'CURRENT RULES', 'provider_session_id': 'native-thread',
               'log_lines': [], 'proc': None}
    handle = ar.SessionHandle(mc_session_id='test', provider=engine, mode='A',
                              project_path='.', project_id='p', session_dict=session)
    assert '_ctx_delivered_sha' not in session
    runtime.write_followup(handle, 'after restart')
    context = runtime.with_mc_tool_protocol('CURRENT RULES')
    assert spawns[-1][1].stdin.getvalue() == resume_prompt(context, 'after restart')
    assert session['_ctx_delivered_sha'] == context_sha(context)
    runtime.write_followup(handle, 'next task')
    assert spawns[-1][1].stdin.getvalue() == 'next task'


@pytest.mark.parametrize('engine', ['codex', 'qwen', 'gemini'])
def test_incognito_keeps_only_existing_tool_protocol(engine, spawns, monkeypatch):
    runtime = ar.get_runtime(engine)
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: engine)
    handle = runtime.dispatch(project_path='.', task='first task', incognito=True)
    session = handle.session_dict
    assert session['_ctx_delivered_sha'] == context_sha(ar.MC_TOOL_PROTOCOL_PROMPT)
    session['provider_session_id'] = 'native-thread'
    session['_system_prompt'] = ''  # incognito context refreshed by routes
    runtime.write_followup(handle, 'private task')
    assert spawns[-1][1].stdin.getvalue() == 'private task'


@pytest.mark.parametrize('engine', ['codex', 'qwen', 'gemini'])
def test_missing_native_id_still_sends_context(engine, spawns, monkeypatch):
    runtime = ar.get_runtime(engine)
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: engine)
    handle = runtime.dispatch(project_path='.', task='first task', system_prompt='RULES')
    runtime.write_followup(handle, 'next task')
    assert 'RULES' in spawns[-1][1].stdin.getvalue()
    assert '--resume' not in spawns[-1][0]


@pytest.mark.parametrize('engine', ['codex', 'qwen', 'gemini'])
def test_failed_context_delivery_is_retried(engine, spawns, monkeypatch):
    runtime = ar.get_runtime(engine)
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: engine)
    original_popen = ar.subprocess.Popen

    class BrokenStdin:
        def write(self, prompt):
            pass

        def close(self):
            raise BrokenPipeError('flush failed')

    def broken_popen(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        proc.stdin = BrokenStdin()
        return proc

    monkeypatch.setattr(ar.subprocess, 'Popen', broken_popen)
    handle = runtime.dispatch(project_path='.', task='first task', system_prompt='RULES')
    session = handle.session_dict
    assert '_ctx_delivered_sha' not in session
    assert any('prompt delivery failed: BrokenPipeError' in line
               for line in session['log_lines'])
    monkeypatch.setattr(ar.subprocess, 'Popen', original_popen)
    session['provider_session_id'] = 'native-thread'
    runtime.write_followup(handle, 'retry context')
    assert 'CURRENT CLAYRUNE CONTEXT' in spawns[-1][1].stdin.getvalue()
    assert session['_ctx_delivered_sha'] == context_sha(session['_system_prompt'])
    runtime.write_followup(handle, 'next task')
    assert spawns[-1][1].stdin.getvalue() == 'next task'


def test_empty_context_does_not_recover_older_instructions():
    assert resume_prompt('', 'next task') == 'next task'
    assert resume_prompt('  ', 'next task') == 'next task'
    session = {'_ctx_delivered_sha': context_sha('OLD RULES')}
    for context in ('', '  '):
        prompt = followup_prompt(session, context, 'next task')
        assert prompt == 'next task'
        write_context_prompt(Stdin(), prompt, session, context)
        assert session['_ctx_delivered_sha'] == context_sha('OLD RULES')


def test_bare_or_old_prompt_does_not_record_changed_context():
    session = {'_ctx_delivered_sha': context_sha('OLD RULES')}
    for prompt in ('bare task', resume_prompt('OLD RULES', 'retry task')):
        write_context_prompt(Stdin(), prompt, session, 'NEW RULES')
        assert session['_ctx_delivered_sha'] == context_sha('OLD RULES')


def test_saved_user_message_keeps_real_text_and_newlines():
    prompt = resume_prompt('CURRENT PERSONA', 'task\n\n---\n\nmore task')
    assert strip_resume_context(prompt) == 'task\n\n---\n\nmore task'
    assert ar._strip_codex_system_prefix(prompt.replace('\n', '\r\n')) == (
        'task\r\n\r\n---\r\n\r\nmore task')
    assert strip_resume_context('ordinary user message') == 'ordinary user message'
    assert ar._strip_codex_system_prefix('task\n\n---\n\nmore task') == (
        'task\n\n---\n\nmore task')
    assert ar._strip_codex_system_prefix('--- SYSTEM ---\nold context\n\n---\n\ntask') == 'task'


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
