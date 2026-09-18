"""Focused checks for feature-level provider selection.

These tests use an in-memory runtime so a route/helper cannot accidentally
fall back to a Claude subprocess when an explicit foreign provider is chosen.
"""

import io
import json
from pathlib import Path

class _Result:
    def __init__(self, text):
        self.text = text


def _certified_evidence(provider, model, effort):
    """Fresh TOOL_FREE_TRANSFORM evidence for an in-memory runtime. The seam
    refuses any adapter that cannot produce this (Fenn #1)."""
    from datetime import datetime, timedelta, timezone
    from mc.execution_policy import (
        Capability, CapabilityClaim, Certification, ExecutionIdentity, Profile,
        Readiness, RequestedEngine, Support)
    now = datetime.now(timezone.utc)
    identity = ExecutionIdentity(RequestedEngine(provider, model, effort, 'test'),
                                 'fake-cli', 'test', 'a' * 64)
    readiness = Readiness(identity, Support.SUPPORTED, Support.SUPPORTED,
                          now - timedelta(seconds=1), now + timedelta(minutes=1))
    certification = Certification(
        identity, Profile.TOOL_FREE_TRANSFORM,
        tuple(CapabilityClaim(c, Support.SUPPORTED) for c in Capability),
        'test-evidence', 'test', now - timedelta(seconds=1), now + timedelta(minutes=1))
    return identity, readiness, certification


class _Runtime:
    name = 'test-provider'
    tool_free_transform_enforced = True

    def __init__(self, text='answer'):
        self.text = text
        self.calls = []

    def transform_evidence(self, *, model='', effort=''):
        return _certified_evidence(self.name, model, effort)

    def model_supported(self, model):
        return True

    def model_choices(self):
        return []

    def oneshot(self, **kwargs):
        self.calls.append(kwargs)
        return _Result(self.text)


def test_run_text_transform_forwards_selected_model_and_effort(monkeypatch):
    from mc import agent_runtime

    runtime = _Runtime('selected output')
    monkeypatch.setitem(agent_runtime._RUNTIMES, runtime.name, runtime)

    answer = agent_runtime.run_text_transform(
        runtime.name, prompt='write the artifact', model='native-pro',
        effort='high', stdin_text='source data', cwd=str(Path.cwd()))

    assert answer == 'selected output'
    assert runtime.calls[0]['model'] == 'native-pro'
    assert runtime.calls[0]['effort'] == 'high'
    assert runtime.calls[0]['stdin_text'] == 'source data'


def test_stream_text_transform_uses_one_delta_fallback(monkeypatch):
    from mc import agent_runtime

    runtime = _Runtime('one complete answer')
    monkeypatch.setitem(agent_runtime._RUNTIMES, runtime.name, runtime)

    deltas = list(agent_runtime.AgentRuntime.stream_text(
        runtime, prompt='stream this', model='native-pro', effort='low'))

    assert deltas == ['one complete answer']
    assert runtime.calls[0]['model'] == 'native-pro'
    assert runtime.calls[0]['effort'] == 'low'


def test_claude_stream_runtime_preserves_deltas_and_requested_settings(monkeypatch):
    from mc import agent_runtime

    class Pipe:
        def __init__(self):
            self.data = ''

        def write(self, value):
            self.data += value

        def flush(self):
            pass

        def close(self):
            pass

    class Proc:
        def __init__(self):
            self.stdin = Pipe()
            self.stdout = io.StringIO(
                json.dumps({'type': 'assistant', 'message': {
                    'content': [{'type': 'text', 'text': 'one '}]}}) + '\n' +
                json.dumps({'type': 'assistant', 'message': {
                    'content': [{'type': 'text', 'text': 'two'}]}}) + '\n' +
                json.dumps({'type': 'result', 'is_error': False}) + '\n')
            self.stderr = io.StringIO('')
            self.returncode = 0
            self.killed = False
            self.command = None

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True

    proc = Proc()
    monkeypatch.setattr(agent_runtime.subprocess, 'Popen',
                        lambda command, **kwargs: _capture_proc(
                            proc, command, kwargs))
    runtime = agent_runtime.ClaudeRuntime()
    monkeypatch.setattr(runtime, 'resolve_binary_str', lambda: 'claude-stub')

    deltas = list(runtime.stream_text(
        prompt='question', system_prompt='guide', model='claude-sonnet-5',
        effort='high', cwd='C:/guide'))

    assert deltas == ['one ', 'two']
    assert proc.command[proc.command.index('--model') + 1] == 'claude-sonnet-5'
    assert proc.command[proc.command.index('--effort') + 1] == 'high'
    assert json.loads(proc.stdin.data)['message']['content'].startswith('guide')


def test_claude_runtime_sends_cwd_brief_because_isolation_skips_autoload(tmp_path):
    """--setting-sources '' stops Claude auto-loading the cwd CLAUDE.md
    (measured 2026-09-17), so a brief that matches it must still be sent."""
    from mc import agent_runtime

    (tmp_path / 'CLAUDE.md').write_text('guide brief', encoding='utf-8')
    assert agent_runtime.ClaudeRuntime._merge_oneshot_instruction(
        'question', 'guide brief', str(tmp_path)) == 'guide brief\n\nquestion'
    assert agent_runtime.ClaudeRuntime._merge_oneshot_instruction(
        'question', 'different brief', str(tmp_path)) == \
        'different brief\n\nquestion'


def test_claude_stream_runtime_kills_child_when_consumer_disconnects(monkeypatch):
    from mc import agent_runtime

    class Pipe:
        def write(self, value):
            pass

        def flush(self):
            pass

        def close(self):
            pass

    class Proc:
        def __init__(self):
            self.stdin = Pipe()
            self.stdout = io.StringIO(
                json.dumps({'type': 'assistant', 'message': {
                    'content': [{'type': 'text', 'text': 'first'}]}}) + '\n')
            self.stderr = io.StringIO('')
            self.returncode = None
            self.killed = False

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    proc = Proc()
    monkeypatch.setattr(agent_runtime.subprocess, 'Popen',
                        lambda command, **kwargs: proc)
    runtime = agent_runtime.ClaudeRuntime()
    monkeypatch.setattr(runtime, 'resolve_binary_str', lambda: 'claude-stub')

    stream = runtime.stream_text(prompt='question')
    assert next(stream) == 'first'
    stream.close()
    assert proc.killed is True


def _capture_proc(proc, command, kwargs):
    proc.command = command
    return proc


def test_character_helper_does_not_send_foreign_engine_to_scribe(monkeypatch):
    from mc import agent_runtime, state
    from mc.blueprints import character_routes as routes

    runtime = _Runtime('## Voice\n\nA distinct voice.')
    monkeypatch.setitem(agent_runtime._RUNTIMES, runtime.name, runtime)
    monkeypatch.setitem(state.CONFIG, 'default_provider', 'claude')
    result = routes._character_model_call(
        {'provider': runtime.name, 'model': 'native-pro', 'effort': 'medium'},
        None, 'voice prompt', 'role payload')

    assert result.startswith('## Voice')
    assert runtime.calls[0]['model'] == 'native-pro'
    assert runtime.calls[0]['effort'] == 'medium'
    assert runtime.calls[0]['stdin_text'] == 'role payload'


def test_character_helper_routes_claude_through_same_runtime_seam(monkeypatch):
    from mc import agent_runtime, state
    from mc.blueprints import character_routes as routes

    runtime = _Runtime('Claude-shaped result through neutral seam')
    runtime.name = 'claude'
    monkeypatch.setitem(agent_runtime._RUNTIMES, 'claude', runtime)
    monkeypatch.setitem(state.CONFIG, 'default_provider', 'claude')

    result = routes._character_model_call(
        {'provider': 'claude', 'model': 'sonnet', 'effort': 'high'},
        None, 'voice prompt', 'role payload')

    assert result.startswith('Claude-shaped')
    assert runtime.calls[0]['model'] == 'sonnet'
    assert runtime.calls[0]['effort'] == 'high'


def test_project_summary_routes_claude_through_same_runtime_seam(monkeypatch):
    from flask import Flask
    from mc import agent_runtime, state
    from mc.blueprints import project_routes as routes

    runtime = _Runtime('{"emoji":"🧱","summary":"Builds provider-neutral orchestration without coupling dashboard features to one model vendor."}')
    runtime.name = 'claude'
    project = {'id': 'neutral', 'name': 'Neutral', 'project_path': str(Path.cwd())}
    saved = []
    monkeypatch.setitem(agent_runtime._RUNTIMES, 'claude', runtime)
    monkeypatch.setitem(state.CONFIG, 'default_provider', 'claude')
    monkeypatch.setitem(state.CONFIG, 'condense_model', '')
    monkeypatch.setattr(routes, 'load_project', lambda project_id: project)
    monkeypatch.setattr(routes, 'save_project', lambda project_id, value: saved.append((project_id, value.copy())))

    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    response = app.test_client().post(
        '/api/project/neutral/generate_summary',
        json={'provider': 'claude', 'model': 'sonnet', 'effort': 'high'},
    )

    assert response.status_code == 200
    assert runtime.calls[0]['model'] == 'sonnet'
    assert runtime.calls[0]['effort'] == 'high'
    assert saved[0][0] == 'neutral'


def test_guide_engine_uses_explicit_provider_and_model(monkeypatch):
    from mc import agent_runtime, state
    from mc.blueprints import guide_routes as routes

    runtime = _Runtime('guide answer')
    monkeypatch.setitem(agent_runtime._RUNTIMES, runtime.name, runtime)
    monkeypatch.setitem(state.CONFIG, 'default_provider', 'claude')

    engine, effort = routes._guide_engine(
        {'provider': runtime.name, 'model': 'native-pro', 'effort': 'low'})

    assert engine.provider == runtime.name
    assert engine.model == 'native-pro'
    assert effort == 'low'
