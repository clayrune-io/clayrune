"""Provider-neutral toolless model seam used by memory helpers."""

from pathlib import Path

import pytest


def test_model_call_uses_legacy_hook_without_context(monkeypatch):
    from mc import memory

    seen = []
    monkeypatch.setattr(memory, '_scribe_call',
                        lambda model, instruction, body:
                        seen.append((model, instruction, body)) or 'legacy')
    monkeypatch.setattr(memory, '_text_transform', None)

    assert memory._model_call('haiku', 'prompt', 'body') == 'legacy'
    assert seen == [('haiku', 'prompt', 'body')]


def test_model_call_uses_injected_runtime_with_authoritative_context(monkeypatch):
    from mc import memory

    seen = []

    def transform(provider, **kwargs):
        seen.append((provider, kwargs))
        return 'provider result'

    monkeypatch.setattr(memory, '_text_transform', transform)
    token = memory._with_transform_context(
        'codex', cwd=str(Path('/project')), effort='high')
    try:
        assert memory._model_call('native-model', 'prompt', 'body') == 'provider result'
    finally:
        memory._reset_transform_context(token)

    assert seen == [('codex', {
        'prompt': 'prompt', 'model': 'native-model', 'effort': 'high',
        'stdin_text': 'body', 'cwd': str(Path('/project')), 'max_turns': 1,
    })]


def test_explicit_project_provider_does_not_infer_missing_provider():
    from mc import memory

    assert memory._explicit_project_provider({'provider': 'Codex'}) == 'codex'
    assert memory._explicit_project_provider({'id': 'p'}) is None


def test_foreign_provider_does_not_receive_claude_tier_default(monkeypatch):
    from mc import memory

    monkeypatch.setitem(memory.state.CONFIG, 'scribe_model', '')
    assert memory._model_for_provider('scribe_model', 'codex') == ''
    monkeypatch.setitem(memory.state.CONFIG, 'scribe_model', 'haiku')
    assert memory._model_for_provider('scribe_model', 'codex') == ''


def test_foreign_provider_accepts_only_runtime_compatible_model(monkeypatch):
    from mc import memory

    class Runtime:
        def model_supported(self, model):
            return model == 'codex-native-model'

    monkeypatch.setattr(memory._agent_runtime, 'get_runtime',
                        lambda provider: Runtime())
    monkeypatch.setitem(memory.state.CONFIG, 'scribe_model',
                        'codex-native-model')
    assert memory._model_for_provider('scribe_model', 'codex') == \
        'codex-native-model'
    monkeypatch.setitem(memory.state.CONFIG, 'scribe_model', 'new-unknown')
    assert memory._model_for_provider('scribe_model', 'codex') == ''


def test_codex_checkpoint_uses_native_session_and_model(monkeypatch, tmp_path):
    """A Codex checkpoint does not require Claude's id or tier names."""
    from mc import memory

    transcript = tmp_path / 'rollout.jsonl'
    transcript.write_text('x' * 2048, encoding='utf-8')

    class Runtime:
        def transcript_path(self, project_path, session_id):
            assert session_id == 'codex-thread'
            return transcript

    started = []

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            started.append(args[0])

        def start(self):
            return None

    monkeypatch.setitem(memory.state.CONFIG, 'scribe_checkpoint_enabled', True)
    monkeypatch.setitem(memory.state.CONFIG, 'scribe_checkpoint_kb', 1)
    monkeypatch.setitem(memory.state.CONFIG, 'scribe_enabled', True)
    monkeypatch.setattr(memory, 'load_project',
                        lambda pid: {'id': pid, 'project_path': str(tmp_path)})
    monkeypatch.setattr(memory._agent_runtime, 'get_runtime',
                        lambda provider: Runtime())
    monkeypatch.setattr(memory, '_find_transcript_file',
                        lambda *args: pytest.fail('Claude lookup used'))
    monkeypatch.setattr(memory, '_checkpoint_prev_offset', lambda *args: 0)
    monkeypatch.setattr(memory.threading, 'Thread', FakeThread)
    monkeypatch.setattr(memory, '_checkpoint_inflight', set())
    monkeypatch.setattr(memory, '_checkpoint_guard', memory.threading.Lock())
    monkeypatch.setitem(memory.state.CONFIG, 'scribe_model', 'haiku')

    memory._maybe_checkpoint({
        'project_id': 'p', 'session_id': 'mc-session',
        'provider': 'codex', 'provider_session_id': 'codex-thread',
        'claude_session_id': '', 'process_alive': True,
    })

    assert len(started) == 1
    assert started[0]['provider'] == 'codex'
    assert started[0]['provider_session_id'] == 'codex-thread'
    assert started[0]['csid'] == ''
    assert started[0]['model'] == ''


def test_codex_checkpoint_delta_uses_selected_runtime_parser(tmp_path):
    """Codex response_item lines must not be sent through Claude parsing."""
    from mc import memory

    transcript = tmp_path / 'rollout.jsonl'
    transcript.write_text(
        '{"type":"response_item","payload":{"type":"message",'
        '"role":"assistant","content":[{"type":"output_text",'
        '"text":"THE CODEX CHECKPOINT ANSWER"}]}}\n',
        encoding='utf-8')

    rendered, offset = memory._scribe_render_delta(transcript, 0, provider='codex')

    assert offset == transcript.stat().st_size
    assert rendered == 'ASSISTANT: THE CODEX CHECKPOINT ANSWER'
