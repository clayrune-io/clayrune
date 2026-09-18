"""Fake-process regressions for provider-neutral transform safety.

These tests never invoke a provider.  The fake process shapes are the two
failures found in Fenn's audit: Claude mixed thinking/text output and Codex
nonzero/quota output being published as a successful answer.
"""

import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from mc.execution_policy import (
    Capability, CapabilityClaim, Certification, ExecutionIdentity,
    Readiness, RequestedEngine, Support,
)


def test_claude_mixed_thinking_then_text_is_not_dropped():
    from mc.agent_runtime import ClaudeRuntime, EventType

    line = json.dumps({
        'type': 'assistant',
        'session_id': 'fake',
        'message': {'content': [
            {'type': 'thinking', 'thinking': 'private reasoning'},
            {'type': 'text', 'text': 'THE ANSWER'},
        ]},
    })
    event = ClaudeRuntime().parse_event(line)
    assert event is not None
    # Classification stays first-block (TOOL_USE-gated consumers rely on it);
    # the text must survive in the blocks for stream_text to read.
    assert event.type is EventType.THINKING
    assert [b['text'] for b in event.payload['blocks'] if b['type'] == 'text'] == ['THE ANSWER']


def test_claude_tool_use_then_text_stays_tool_use_for_doc_scanner():
    from mc.agent_runtime import ClaudeRuntime, EventType

    line = json.dumps({
        'type': 'assistant', 'session_id': 'fake',
        'message': {'content': [
            {'type': 'tool_use', 'id': 't1', 'name': 'Write',
             'input': {'file_path': 'notes.md', 'content': 'x'}},
            {'type': 'text', 'text': 'wrote it'},
        ]},
    })
    event = ClaudeRuntime().parse_event(line)
    assert event is not None and event.type is EventType.TOOL_USE


def test_claude_stream_helper_yields_text_after_thinking(monkeypatch):
    from mc import agent_runtime

    class Pipe:
        def write(self, _value):
            pass
        def flush(self):
            pass
        def close(self):
            pass

    class Proc:
        def __init__(self):
            self.stdin = Pipe()
            self.stdout = io.StringIO(json.dumps({
                'type': 'assistant', 'message': {'content': [
                    {'type': 'thinking', 'thinking': 'private'},
                    {'type': 'text', 'text': 'THE ANSWER'},
                ]},
            }) + '\n' + json.dumps({'type': 'result', 'is_error': False}) + '\n')
            self.stderr = io.StringIO('')
            self.returncode = 0
        def wait(self, timeout=None):
            return self.returncode
        def poll(self):
            return self.returncode
        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(agent_runtime.subprocess, 'Popen', lambda *a, **k: Proc())
    runtime = agent_runtime.ClaudeRuntime()
    monkeypatch.setattr(runtime, 'resolve_binary_str', lambda: 'claude-fake')
    assert list(runtime.stream_text(prompt='summarize')) == ['THE ANSWER']


@pytest.mark.parametrize('returncode,output', [
    (1, '{"type":"error","message":"quota exceeded"}'),
    (1, 'rate limit exceeded'),
])
def test_codex_nonzero_or_quota_never_becomes_transform_content(monkeypatch, returncode, output):
    from mc import agent_runtime

    class FakeProc:
        def __init__(self):
            self.returncode = returncode
            self.stdout = output
            self.stderr = output

    runtime = agent_runtime.CodexRuntime()
    runtime._bin_cache = 'codex-fake'
    runtime._npx_fallback = False
    monkeypatch.setattr(agent_runtime.subprocess, 'run', lambda *a, **k: FakeProc())

    result = runtime.oneshot(prompt='summarize')
    assert result is None
    assert runtime.last_error


def test_codex_valid_answer_may_discuss_quota(monkeypatch):
    from mc import agent_runtime

    class FakeProc:
        returncode = 0
        stdout = json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message',
            'text': 'quota is 429 tokens',
        }})
        stderr = ''

    runtime = agent_runtime.CodexRuntime()
    runtime._bin_cache = 'codex-fake'
    monkeypatch.setattr(agent_runtime.subprocess, 'run', lambda *a, **k: FakeProc())
    result = runtime.oneshot(prompt='explain quota')
    assert result is not None
    assert result.text == 'quota is 429 tokens'


def test_codex_rc0_unstructured_error_is_not_successful_content(monkeypatch):
    from mc import agent_runtime

    class FakeProc:
        returncode = 0
        stdout = 'quota exceeded'
        stderr = ''

    runtime = agent_runtime.CodexRuntime()
    runtime._bin_cache = 'codex-fake'
    monkeypatch.setattr(agent_runtime.subprocess, 'run', lambda *a, **k: FakeProc())
    assert runtime.oneshot(prompt='summarize') is None


def test_transform_seam_refuses_runtime_without_enforced_boundary(monkeypatch):
    from mc import agent_runtime

    class FakeRuntime:
        name = 'fake-unsafe'
        tool_free_transform_enforced = False

    monkeypatch.setitem(agent_runtime._RUNTIMES, 'fake-unsafe', FakeRuntime())
    with pytest.raises(RuntimeError, match='cannot enforce tool-free'):
        agent_runtime.run_text_transform('fake-unsafe', prompt='untrusted transcript')


def test_transform_seam_accepts_explicit_fresh_policy_evidence(monkeypatch):
    from mc import agent_runtime

    class Result:
        text = 'safe answer'

    class CertifiedRuntime:
        name = 'certified-fake'
        tool_free_transform_enforced = True
        def oneshot(self, **kwargs):
            return Result()

    now = datetime.now(timezone.utc)
    identity = ExecutionIdentity(
        RequestedEngine('certified-fake', 'model-1', '', 'account-1'),
        'cli-1', 'test', 'a' * 64)
    readiness = Readiness(identity, Support.SUPPORTED, Support.SUPPORTED,
                          now - timedelta(seconds=1), now + timedelta(minutes=1))
    certification = Certification(
        identity, agent_runtime.Profile.TOOL_FREE_TRANSFORM,
        tuple(CapabilityClaim(c, Support.SUPPORTED) for c in Capability),
        'test-evidence', 'test-canary', now - timedelta(seconds=1),
        now + timedelta(minutes=1))
    runtime = CertifiedRuntime()
    monkeypatch.setitem(agent_runtime._RUNTIMES, runtime.name, runtime)
    assert agent_runtime.run_text_transform(
        runtime.name, prompt='transform', identity=identity,
        readiness=readiness, certification=certification) == 'safe answer'
