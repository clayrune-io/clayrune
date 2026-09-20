"""W4 item 1: Gemini resume after a server restart.

Live-verified 2026-09-18 (not simulated): dispatched a real gemini turn that
memorized "PINEAPPLE-77", captured its `provider_session_id`, then — as a
BRAND NEW `GeminiRuntime().dispatch()` call with a fresh session dict (no
in-memory state carried over, exactly what a server restart leaves behind)
and only `resume_id=<the captured id>` — asked "what was the code?" and got
back "PINEAPPLE-77". Full transcript: docs/_journal/gemini-cold-resume-proof.md.

Before this fix, none of that was possible:
  1. `GeminiRuntime._read_stream`'s INIT branch stashed the captured id onto
     a PRIVATE `session['_gemini_session_id']` key that nothing ever
     persisted to the durable agent_log (`_runtime_note_init` backfills
     `provider_session_id`, a different key it never wrote).
  2. `GeminiRuntime.build_command()` had no `resume_id` parameter at all —
     even a caller holding a valid id had no way to thread it into a COLD
     `dispatch()` (only the warm `write_followup` path manually appended
     `--resume` after the fact).
  3. `gemini` was explicitly excluded from `_COLD_RESUMABLE_PROVIDERS`
     (agent_routes.py), so `_revive_non_claude_from_agent_log` never even
     tried to pass a resume_id through for it.

These tests pin the wiring (1)-(3) at the unit level; the live call above is
the end-to-end proof that the wiring actually works against the real CLI.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402
from mc.agent_runtime import GeminiRuntime, SessionHandle  # noqa: E402


class _FakeProc:
    def __init__(self, lines, rc=0, pid=424242):
        self.stdout = iter(lines)
        self._rc = rc
        self.pid = pid

    def wait(self):
        return self._rc

    def poll(self):
        return self._rc


class TestBuildCommandThreadsResumeId:
    def test_no_resume_id_omits_the_flag(self):
        cmd = GeminiRuntime().build_command(model='gemini-flash-lite-latest')
        assert '--resume' not in cmd

    def test_resume_id_appends_the_flag_with_the_id(self):
        cmd = GeminiRuntime().build_command(
            model='gemini-flash-lite-latest', resume_id='abc-123')
        assert '--resume' in cmd
        assert cmd[cmd.index('--resume') + 1] == 'abc-123'


class TestReadStreamCapturesGenericProviderSessionId:
    def test_init_event_sets_provider_session_id_not_a_private_key(self):
        lines = [
            '{"type":"init","session_id":"sid-live-1","model":"gemini-flash-lite-latest"}\n',
            '{"type":"message","role":"assistant","content":"ok","delta":true}\n',
            '{"type":"result","status":"success"}\n',
        ]
        proc = _FakeProc(lines, rc=0)
        session = {'log_lines': [], 'proc': proc}
        handle = SessionHandle(
            mc_session_id='sid-1', provider='gemini', mode='A',
            project_path='/p', project_id='proj1', session_dict=session,
            meta={'callbacks': {}},
        )
        GeminiRuntime()._read_stream(proc, handle)
        assert session.get('provider_session_id') == 'sid-live-1'
        assert '_gemini_session_id' not in session
        assert session.get('observed_model') == 'gemini-flash-lite-latest'

    def test_init_event_fires_the_on_init_callback(self):
        """`_runtime_note_init` (agent_routes.py) is wired as the `on_init`
        callback for every runtime, including gemini's own bespoke reader —
        without this call the durable agent_log row never gets backfilled,
        even though the in-memory session dict has the right value."""
        seen = []
        lines = [
            '{"type":"init","session_id":"sid-live-2"}\n',
            '{"type":"result","status":"success"}\n',
        ]
        proc = _FakeProc(lines, rc=0)
        session = {'log_lines': [], 'proc': proc}
        handle = SessionHandle(
            mc_session_id='sid-1', provider='gemini', mode='A',
            project_path='/p', project_id='proj1', session_dict=session,
            meta={'callbacks': {'on_init': lambda ev, s: seen.append(ev)}},
        )
        GeminiRuntime()._read_stream(proc, handle)
        assert len(seen) == 1
        assert seen[0].type == agent_runtime_mod.EventType.INIT


class TestWriteFollowupReadsTheGenericKey:
    def test_write_followup_resumes_from_provider_session_id(self, monkeypatch):
        captured_cmd = {}

        rt = GeminiRuntime()
        monkeypatch.setattr(rt, 'resolve_binary', lambda: Path('gemini'))
        monkeypatch.setattr(
            agent_runtime_mod, '_sync_mcp_to_gemini_safe', lambda *_a, **_k: {})
        monkeypatch.setattr(
            agent_runtime_mod, '_log_mcp_sync_result', lambda *_a, **_k: None)

        class _FakeCompletedProc:
            stdin = None
            stdout = iter([])
            pid = 1

            def wait(self):
                return 0

            def poll(self):
                return 0

        def _fake_popen(cmd, **kwargs):
            captured_cmd['cmd'] = cmd
            return _FakeCompletedProc()

        monkeypatch.setattr(agent_runtime_mod.subprocess, 'Popen', _fake_popen)
        monkeypatch.setattr(rt, '_write_prompt_async', lambda *a, **k: None)

        session = {'proc': None, 'log_lines': [], 'provider_session_id': 'sid-warm-1'}
        handle = SessionHandle(
            mc_session_id='sid-1', provider='gemini', mode='A',
            project_path='/p', project_id='proj1', session_dict=session,
            meta={'callbacks': {}},
        )
        rt.write_followup(handle, 'what was the code?')
        assert '--resume' in captured_cmd['cmd']
        assert captured_cmd['cmd'][captured_cmd['cmd'].index('--resume') + 1] == 'sid-warm-1'


class TestColdResumableProviders:
    def test_gemini_is_cold_resumable(self):
        from mc.blueprints import agent_routes as ar
        assert 'gemini' in ar._COLD_RESUMABLE_PROVIDERS
        assert 'codex' in ar._COLD_RESUMABLE_PROVIDERS
        assert 'qwen' in ar._COLD_RESUMABLE_PROVIDERS
