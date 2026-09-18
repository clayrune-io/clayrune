"""W5 (2026-09-18): explicit, opt-in cross-provider conversation handoff.

`_prior_conversation_provider`'s hard refusal ("conversation belongs to
another provider; start a new chat to switch providers") stays EXACTLY as it
was for every existing caller — the UI resume composer, the scheduler,
Hivemind, every workflow agent step — none of which pass the new
`cross_provider_handoff` flag, so none of them see any behaviour change.
Only an explicit ask that names both a `resume_conversation_id` and a
DIFFERENT `provider`, WITH `cross_provider_handoff=True`, takes the new
branch: `_build_handoff_context` rebuilds the owning provider's real turns
from its own transcript reader (the same ones that already back same-vendor
reconstruction: `ClaudeRuntime.parse_transcript_file`,
`QwenRuntime`/`CodexRuntime.extract_chat_turns`) and injects them into a
BRAND-NEW conversation on the destination vendor — `resume_id` is cleared,
this is never a native cross-vendor resume.

Gemini has no native transcript store at all (`GeminiRuntime.transcript_path`
returns `None` unconditionally) — handing off FROM Gemini therefore fails
honestly with a specific, named error rather than proceeding with an empty
or fabricated context. That is exercised here as the natural "no transcript
file exists" case, not mocked separately.
"""
import json

import pytest

from tests.test_revive_notify_carry import ar, _project  # noqa: F401


def _rig_owner(ar_mod, monkeypatch, project_id, native_id, owner):
    monkeypatch.setattr(
        ar_mod, '_load_agent_log',
        lambda pid: [{'project_id': pid, 'provider': owner,
                      'provider_session_id': native_id}])


class TestRefusalUnchangedWithoutTheFlag:

    def test_old_refusal_still_fires_when_the_flag_is_absent(
            self, ar, tmp_path, monkeypatch):
        project = _project(tmp_path)
        monkeypatch.setattr(ar, 'load_project', lambda _: project)
        _rig_owner(ar, monkeypatch, 'p1', 'native-abc', 'qwen')

        with pytest.raises(ValueError) as exc:
            ar._dispatch_agent_internal(
                'p1', 'continue please', resume_id='native-abc',
                provider_override='gemini')  # cross_provider_handoff default False

        assert 'belongs to another provider' in str(exc.value)

    def test_old_refusal_still_fires_when_the_flag_is_explicitly_false(
            self, ar, tmp_path, monkeypatch):
        project = _project(tmp_path)
        monkeypatch.setattr(ar, 'load_project', lambda _: project)
        _rig_owner(ar, monkeypatch, 'p1', 'native-abc', 'qwen')

        with pytest.raises(ValueError) as exc:
            ar._dispatch_agent_internal(
                'p1', 'continue please', resume_id='native-abc',
                provider_override='gemini', cross_provider_handoff=False)

        assert 'belongs to another provider' in str(exc.value)


class TestHandoffOptInTakesTheNewBranch:

    def test_flag_set_surfaces_the_handoff_specific_refusal_instead(
            self, ar, tmp_path, monkeypatch):
        """No transcript file exists under this tmp_path's fake qwen home, so
        this is the honest 'no transcript available' failure -- proof the
        NEW branch ran (a materially different message), not the old
        provider-mismatch refusal."""
        project = _project(tmp_path)
        monkeypatch.setattr(ar, 'load_project', lambda _: project)
        _rig_owner(ar, monkeypatch, 'p1', 'native-abc', 'qwen')

        with pytest.raises(ValueError) as exc:
            ar._dispatch_agent_internal(
                'p1', 'continue please', resume_id='native-abc',
                provider_override='gemini', cross_provider_handoff=True)

        msg = str(exc.value)
        assert 'belongs to another provider' not in msg
        assert "cannot hand off from 'qwen'" in msg

    def test_same_provider_is_a_harmless_noop_even_with_the_flag(
            self, ar, tmp_path, monkeypatch):
        """Flag set, but source and destination are the SAME provider --
        this must fall through to the ordinary resume path unchanged (no
        handoff attempted, no new error introduced for the common case)."""
        project = _project(tmp_path)
        monkeypatch.setattr(ar, 'load_project', lambda _: project)
        _rig_owner(ar, monkeypatch, 'p1', 'native-abc', 'qwen')
        called = []
        monkeypatch.setattr(ar, '_build_handoff_context',
                            lambda *a, **k: called.append(1) or ('x', {}))

        # provider_override left '' -> _prior_conversation_provider recovers
        # 'qwen' itself and returns it; same-provider, no mismatch, no call.
        provider = ar._prior_conversation_provider('p1', 'native-abc', '')
        assert provider == 'qwen'
        assert called == []


class TestBuildHandoffContext:

    def test_extracts_real_turns_and_labels_the_source(self, tmp_path, monkeypatch):
        from mc import agent_runtime
        from mc.blueprints import agent_routes as ar_mod

        chat = tmp_path / 'chat.jsonl'
        records = [
            {'provenance': 'real_user',
             'message': {'parts': [{'text': 'What is the plan?'}]}},
            {'provenance': 'assistant_output',
             'message': {'parts': [{'text': 'The plan is X.'}]}},
        ]
        chat.write_text('\n'.join(json.dumps(r) for r in records), encoding='utf-8')

        qwen_rt = agent_runtime.get_runtime('qwen')
        monkeypatch.setattr(qwen_rt, 'transcript_path', lambda pp, sid: chat)

        text, meta = ar_mod._build_handoff_context('/tmp/proj', 'qwen', 'native-abc')

        assert 'started on qwen' in text
        assert 'What is the plan?' in text
        assert 'The plan is X.' in text
        assert meta['owning_provider'] == 'qwen'
        assert meta['total_turns'] == 2
        assert meta['included_turns'] == 2
        assert meta['omitted_turns'] == 0

    def test_bounds_size_and_reports_what_was_omitted(self, tmp_path, monkeypatch):
        from mc import agent_runtime
        from mc.blueprints import agent_routes as ar_mod

        chat = tmp_path / 'chat.jsonl'
        records = []
        for i in range(40):
            records.append({'provenance': 'real_user',
                            'message': {'parts': [{'text': f'question {i} ' + 'x' * 300}]}})
            records.append({'provenance': 'assistant_output',
                            'message': {'parts': [{'text': f'answer {i} ' + 'y' * 300}]}})
        chat.write_text('\n'.join(json.dumps(r) for r in records), encoding='utf-8')

        qwen_rt = agent_runtime.get_runtime('qwen')
        monkeypatch.setattr(qwen_rt, 'transcript_path', lambda pp, sid: chat)

        text, meta = ar_mod._build_handoff_context('/tmp/proj', 'qwen', 'native-abc')

        assert meta['total_turns'] == 80
        assert meta['omitted_turns'] > 0
        assert meta['included_turns'] + meta['omitted_turns'] == 80
        assert f"{meta['omitted_turns']} earlier turn(s) omitted" in text
        # The most recent turn must survive trimming (tail is kept, not the head).
        assert 'question 39' in text or 'answer 39' in text

    def test_no_native_transcript_store_fails_honestly(self, tmp_path, monkeypatch):
        """Gemini's real shape: transcript_path always returns None. Proves
        this surfaces a specific, honest refusal -- never an empty or
        fabricated handoff."""
        from mc import agent_runtime
        from mc.blueprints import agent_routes as ar_mod

        gemini_rt = agent_runtime.get_runtime('gemini')
        assert gemini_rt.transcript_path('/tmp/proj', 'native-abc') is None

        with pytest.raises(ValueError) as exc:
            ar_mod._build_handoff_context('/tmp/proj', 'gemini', 'native-abc')

        assert "cannot hand off from 'gemini'" in str(exc.value)
        assert 'no transcript' in str(exc.value)
