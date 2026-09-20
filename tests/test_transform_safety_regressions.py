"""Fake-process regressions for provider-neutral transform safety.

These tests never invoke a provider.  The fake process shapes are the two
failures found in Fenn's audit: Claude mixed thinking/text output and Codex
nonzero/quota output being published as a successful answer.
"""

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


# ── Fenn #1: the seam authorizes every transform in production ───────────────

def _claude_with_fake_run(monkeypatch, stdout='SUMMARY'):
    from mc import agent_runtime

    class Done:
        returncode = 0
        stderr = ''

    Done.stdout = stdout
    runs = []
    monkeypatch.setattr(agent_runtime.subprocess, 'run',
                        lambda cmd, **kw: runs.append(cmd) or Done())
    monkeypatch.setattr(agent_runtime, 'claude_installed', lambda: True)
    runtime = agent_runtime.ClaudeRuntime()
    monkeypatch.setattr(runtime, 'resolve_binary_str', lambda: 'claude-fake')
    monkeypatch.setattr(runtime, 'auth_status', lambda: {'ok': True})
    monkeypatch.setitem(agent_runtime._RUNTIMES, 'claude', runtime)
    return agent_runtime, runtime, runs


def test_claude_transform_is_authorized_through_execution_policy(monkeypatch):
    """No caller passes evidence: the adapter supplies it and
    authorize_execution runs with the TOOL_FREE_TRANSFORM profile. Before
    this, the seam refused every production caller (Claydo, character and
    profile generation) because none of them could pass evidence."""
    agent_runtime, _runtime, runs = _claude_with_fake_run(monkeypatch)
    seen = []
    real = agent_runtime.authorize_execution

    def spy(identity, profile, **kw):
        seen.append(profile)
        return real(identity, profile, **kw)

    monkeypatch.setattr(agent_runtime, 'authorize_execution', spy)
    assert agent_runtime.run_text_transform(
        'claude', prompt='summarize', stdin_text='transcript') == 'SUMMARY'
    assert seen == [agent_runtime.Profile.TOOL_FREE_TRANSFORM]
    cmd = runs[0]
    assert cmd[cmd.index('--tools') + 1] == ''
    assert cmd[cmd.index('--setting-sources') + 1] == ''


def test_claude_transform_refuses_when_isolation_flag_is_missing(monkeypatch):
    agent_runtime, runtime, runs = _claude_with_fake_run(monkeypatch)
    real_argv = runtime._oneshot_argv

    def leaky(**kw):
        cmd = real_argv(**kw)
        i = cmd.index('--setting-sources')
        return cmd[:i] + cmd[i + 2:]          # hooks/plugins back on

    monkeypatch.setattr(runtime, '_oneshot_argv', leaky)
    with pytest.raises(RuntimeError, match='unauthorized'):
        agent_runtime.run_text_transform('claude', prompt='summarize')
    assert runs == []                          # refused before any spawn


def test_claude_transform_refuses_when_not_installed(monkeypatch):
    agent_runtime, _runtime, runs = _claude_with_fake_run(monkeypatch)
    monkeypatch.setattr(agent_runtime, 'claude_installed', lambda: False)
    with pytest.raises(RuntimeError, match='unauthorized'):
        agent_runtime.run_text_transform('claude', prompt='summarize')
    assert runs == []


def test_uncertified_vendor_transform_refuses_before_spawn(monkeypatch):
    """Interactive profiles stay unsandboxed by Ron's decision; transforms
    do not. Codex has no certified no-tools transport yet (offline argv
    construction only, live proof pending 2026-09-24 -- VENDOR_AGNOSTIC_
    PROGRAM §8b): it must keep refusing before spawning, and the message
    must name the vendor and say why (never a silent substitution)."""
    from mc import agent_runtime
    runs = []
    monkeypatch.setattr(agent_runtime.subprocess, 'run', lambda *a, **k: runs.append(a))
    monkeypatch.setattr(agent_runtime.subprocess, 'Popen', lambda *a, **k: runs.append(a))
    with pytest.raises(agent_runtime.TransformFailure, match='cannot enforce tool-free') as exc:
        agent_runtime.run_text_transform('codex', prompt='untrusted transcript')
    assert runs == []
    assert exc.value.provider == 'codex'
    assert 'not yet certified' in str(exc.value)


# ── Gemini/Qwen certified for TOOL_FREE_TRANSFORM (VENDOR_AGNOSTIC_PROGRAM
#    §8b W1): before this, EVERY Scribe/condense/Distiller/Claydo/character
#    call on a Gemini or Qwen session silently ran through Claude instead
#    (mc.memory._scribe_call's hardcoded 'claude' fallback) when Claude was
#    installed, and refused outright when it wasn't -- either way a non-
#    Claude user never got a real summary from their own session's provider.
#    _model_call already threads the session's own provider through
#    _with_transform_context/_provider_transform; the only missing piece was
#    these two adapters proving they are tool-free. ──────────────────────────

def _fake_run_capturing(monkeypatch, stdout):
    from mc import agent_runtime
    runs = []

    class Done:
        returncode = 0
        stderr = ''
    Done.stdout = stdout

    def fake_run(cmd, **kw):
        runs.append((cmd, kw))
        return Done()
    monkeypatch.setattr(agent_runtime.subprocess, 'run', fake_run)
    return runs


def _fresh_certified_gemini(monkeypatch):
    """A FRESH GeminiRuntime, not the process-wide registered singleton --
    other test modules exercise that shared instance's real auth cache
    (`_auth_cache`), which is instance state with no per-test reset."""
    from mc import agent_runtime
    runtime = agent_runtime.GeminiRuntime()
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: Path('gemini-fake'))
    monkeypatch.setattr(runtime, 'auth_status', lambda: {'ok': True})
    monkeypatch.setitem(agent_runtime._RUNTIMES, 'gemini', runtime)
    return runtime


def _fresh_certified_qwen(monkeypatch):
    from mc import agent_runtime
    runtime = agent_runtime.QwenRuntime()
    monkeypatch.setattr(runtime, 'resolve_binary', lambda: Path('qwen-fake'))
    monkeypatch.setattr(runtime, '_qwen_auth_state', lambda: ('ok', 'test'))
    monkeypatch.setitem(agent_runtime._RUNTIMES, 'qwen', runtime)
    return runtime


@pytest.mark.parametrize('name,make_runtime,flag_pairs', [
    ('gemini', _fresh_certified_gemini, [('--skip-trust',), ('-e', 'none')]),
    ('qwen', _fresh_certified_qwen, [('--bare',), ('--max-tool-calls', '0')]),
])
def test_gemini_qwen_transform_is_authorized_and_isolated(monkeypatch, name, make_runtime, flag_pairs):
    from mc import agent_runtime
    runs = _fake_run_capturing(monkeypatch, 'SUMMARY')
    runtime = make_runtime(monkeypatch)
    assert runtime.tool_free_transform_enforced is True

    result = agent_runtime.run_text_transform(name, prompt='summarize', stdin_text='transcript')

    assert result == 'SUMMARY'
    assert len(runs) == 1
    cmd = runs[0][0]
    for pair in flag_pairs:
        assert agent_runtime._argv_contains(cmd, pair), (name, pair, cmd)


@pytest.mark.parametrize('name,make_runtime,argv_attr,break_argv', [
    ('gemini', _fresh_certified_gemini, '_transform_argv',
     lambda cmd: [c for c in cmd if c != '--skip-trust']),
    ('qwen', _fresh_certified_qwen, '_transform_argv',
     lambda cmd: [c for c in cmd if c != '--bare']),
])
def test_gemini_qwen_transform_refuses_when_isolation_flag_is_missing(
        monkeypatch, name, make_runtime, argv_attr, break_argv):
    """The self-check in transform_evidence() is load-bearing: a dropped
    isolation flag must refuse the transform, not silently run with it."""
    from mc import agent_runtime
    runs = _fake_run_capturing(monkeypatch, 'SUMMARY')
    runtime = make_runtime(monkeypatch)
    real_argv = getattr(runtime, argv_attr)
    monkeypatch.setattr(runtime, argv_attr, lambda **kw: break_argv(real_argv(**kw)))
    with pytest.raises(agent_runtime.TransformFailure, match='unauthorized'):
        agent_runtime.run_text_transform(name, prompt='summarize')
    assert runs == []


def test_codex_transform_argv_is_offline_correct_but_stays_uncertified():
    """Command-construction proof (VENDOR_AGNOSTIC_PROGRAM §8b W1 item 2):
    no live call, no allowance spent. `-s read-only` replaces the dispatch
    path's `--dangerously-bypass-approvals-and-sandbox`, and
    --ignore-user-config keeps this box's real ~/.codex/config.toml
    [mcp_servers.*] entries from loading."""
    from mc import agent_runtime
    runtime = agent_runtime.CodexRuntime()
    runtime._bin_cache = 'codex-fake'
    argv = runtime._transform_argv(model='gpt-6-astra')
    assert agent_runtime._argv_contains(argv, ('-s', 'read-only'))
    assert agent_runtime._argv_contains(argv, ('--ignore-user-config',))
    assert agent_runtime._argv_contains(argv, ('--skip-git-repo-check',))
    assert agent_runtime._argv_contains(argv, ('--ephemeral',))
    assert '--dangerously-bypass-approvals-and-sandbox' not in argv
    # Still refuses through the seam -- offline proof never flips certification.
    assert runtime.tool_free_transform_enforced is False


# ── Fenn #4: a failed transform is raised, typed, and never becomes content ──

class _CertifiedFailingRuntime:
    """A certified adapter whose oneshot fails the way Codex's did in Fenn's
    repro: nothing usable, quota JSON as the only output."""
    name = 'certified-failing'
    tool_free_transform_enforced = True

    def __init__(self, last_error):
        self.last_error = last_error

    def transform_evidence(self, *, model='', effort=''):
        from tests.test_provider_neutral_orchestration import _certified_evidence
        return _certified_evidence(self.name, model, effort)

    def oneshot(self, **kwargs):
        return None


def test_quota_failure_raises_typed_failure_with_raw_detail(monkeypatch):
    from mc import agent_runtime
    quota = 'rc=1: {"type":"error","message":"quota exceeded"}'
    monkeypatch.setitem(agent_runtime._RUNTIMES, 'certified-failing',
                        _CertifiedFailingRuntime(quota))
    with pytest.raises(agent_runtime.TransformFailure) as exc:
        agent_runtime.run_text_transform('certified-failing', prompt='summarize')
    assert exc.value.kind == 'failed'
    assert exc.value.provider == 'certified-failing'
    assert exc.value.detail == quota          # verbatim, for the allowance layer
    assert isinstance(exc.value, RuntimeError)


def test_timeout_is_typed_and_still_a_timeout_error(monkeypatch):
    from mc import agent_runtime
    monkeypatch.setitem(agent_runtime._RUNTIMES, 'certified-failing',
                        _CertifiedFailingRuntime('timeout after 180s (12c in)'))
    with pytest.raises(agent_runtime.TransformTimeout) as exc:
        agent_runtime.run_text_transform('certified-failing', prompt='summarize')
    assert isinstance(exc.value, TimeoutError) and exc.value.kind == 'timeout'


def test_refusal_is_typed(monkeypatch):
    from mc import agent_runtime
    with pytest.raises(agent_runtime.TransformFailure) as exc:
        agent_runtime.run_text_transform('codex', prompt='summarize')
    assert exc.value.kind == 'refused'


def test_fenn_repro_quota_json_never_becomes_a_scribe_summary(monkeypatch):
    """Fenn's exact repro: a provider-context Scribe summary whose model call
    'returns' quota JSON came back as ('{"type":"error",...}', 'extracted').
    It must be a model_error with no summary."""
    from mc import agent_runtime, memory
    quota = '{"type":"error","message":"quota exceeded"}'
    monkeypatch.setitem(agent_runtime._RUNTIMES, 'certified-failing',
                        _CertifiedFailingRuntime('rc=1: ' + quota))
    monkeypatch.setattr(memory, '_text_transform', None)
    token = memory._with_transform_context('certified-failing')
    try:
        summary, reason = memory._scribe_summarize_text(
            'USER: do the thing. ASSISTANT: did the thing. ' * 20, 'native')
    finally:
        memory._reset_transform_context(token)
    assert summary is None
    assert reason == 'model_error'
