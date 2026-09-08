"""Regression tests for hivemind hm_d9c76579's remaining identity-plumbing
findings (item 5 of the fix pass — the P0 bare-persona-name bug on the
non-claude revive path has its own test file,
tests/test_revive_non_claude_character_ref.py):

  * f_4a2ccd47 — `_dispatch_via_runtime` (the ONE dispatch entry point for
    every non-claude provider) never had a `source` parameter at all, and
    its `if not incognito:` guard skipped the WHOLE context build —
    persona included — for an incognito non-claude chat.
  * f_d20a4e36 / f_adf3a4bd — `_revive_from_agent_log`'s two session dicts
    and two `_build_agent_context` calls omitted `incognito`/`session_id`/
    `source`, un-hiding a revived incognito chat and losing a revived
    delegated session's identity.
  * f_83b82859 — `_respawn_sysprompt_args`'s stale-fingerprint rebuild
    omitted `source=`, so a delegated worker silently renamed itself to the
    project's default agent mid-conversation the instant any watched file's
    mtime moved (e.g. the Scribe writing MEMORY.md).

Each test below fails on the parent commit and passes after the fix.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def ar(tmp_path, monkeypatch):
    import server  # noqa: F401 — wires the blueprint's global-scope deps
    from mc.blueprints import agent_routes as _ar
    # Isolate the module-level session map for this test only.
    snapshot = dict(_ar.agent_sessions)
    _ar.agent_sessions.clear()
    try:
        yield _ar
    finally:
        _ar.agent_sessions.clear()
        _ar.agent_sessions.update(snapshot)


PROJECT = {'id': 'p1', 'project_path': '/tmp/p1', 'provider': 'claude'}


# ── _dispatch_via_runtime: `source` unplumbed + incognito skips the whole
#    context build ───────────────────────────────────────────────────────────

class _FakeRuntime:
    def __init__(self):
        self.dispatch_calls = []

    def model_supported(self, model):
        return True

    def build_command(self, model=None, resume_id=None):
        return ['fake-cli', '-p', 'x']

    def dispatch(self, **kw):
        self.dispatch_calls.append(kw)
        return object()


@pytest.fixture()
def runtime_env(ar, monkeypatch):
    fake = _FakeRuntime()
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda name: fake)
    built = {}

    def _fake_build(project, **kw):
        built.update(kw)
        built['called'] = True
        return 'CTX'
    monkeypatch.setattr(ar, '_build_agent_context', _fake_build)
    return fake, built


class TestDispatchViaRuntimeIdentity:

    def test_source_reaches_the_session_dict(self, ar, runtime_env):
        fake, built = runtime_env
        sid = ar._dispatch_via_runtime(
            PROJECT, 'do a thing', provider_name='gemini',
            model_override='gemini-2.5-flash', source='agent')
        assert ar.agent_sessions[sid]['source'] == 'agent'

    def test_source_reaches_build_agent_context(self, ar, runtime_env):
        fake, built = runtime_env
        ar._dispatch_via_runtime(
            PROJECT, 'do a thing', provider_name='gemini',
            model_override='gemini-2.5-flash', source='agent')
        assert built.get('source') == 'agent'

    def test_incognito_does_not_skip_the_context_build(self, ar, runtime_env):
        """f_4a2ccd47: `if not incognito: system_prompt = _build_agent_context(...)`
        meant an incognito chat on a non-claude provider got NO context at
        all — no persona, no incognito notice block, nothing but the bare
        CLI. incognito must change what _build_agent_context puts IN the
        prompt, not whether it is called."""
        fake, built = runtime_env
        ar._dispatch_via_runtime(
            PROJECT, 'do a thing', provider_name='gemini',
            model_override='gemini-2.5-flash', incognito=True,
            character_body='YOU ARE DAVE.', character_meta={'agent_name': 'Dave'})
        assert built.get('called'), (
            'incognito must not skip the context build — it changes what '
            'goes IN it, exactly like the claude dispatch path')
        assert built.get('character_body') == 'YOU ARE DAVE.'
        assert built.get('incognito') is True

    def test_dispatch_agent_internal_forwards_source_for_non_claude(self, ar, monkeypatch, tmp_path):
        """The other half: _dispatch_agent_internal must pass its own
        `source` argument down into _dispatch_via_runtime."""
        captured = {}
        monkeypatch.setattr(ar, '_dispatch_via_runtime',
                            lambda *a, **kw: captured.update(kw) or 'sidX')
        monkeypatch.setattr(ar, 'load_project',
                            lambda pid: {'id': 'p1', 'project_path': str(tmp_path),
                                        'provider': 'gemini'})
        sid = ar._dispatch_agent_internal('p1', 'do a thing', source='agent')
        assert sid == 'sidX'
        assert captured.get('source') == 'agent'


# ── _revive_from_agent_log: dicts + context rebuilds drop identity ──────────

class TestReviveFromAgentLogIdentity:

    def test_revive_context_rebuild_carries_incognito_session_and_source(self, ar, monkeypatch):
        """Source-level guard, following tests/test_resume_persona.py's own
        precedent for this function: _revive_from_agent_log is too heavy
        (real subprocess.Popen, thread starts) to run end-to-end in a unit
        test, so pin the CALL SHAPE instead of executing it. Both
        `_build_agent_context` calls inside it must carry incognito=,
        session_id= and source= — f_adf3a4bd's exact list."""
        src = (PROJECT_ROOT / 'mc' / 'blueprints' / 'agent_routes.py').read_text(encoding='utf-8')
        start = src.index('def _revive_from_agent_log(')
        end = src.index('\ndef _revive_non_claude_from_agent_log(')
        body = src[start:end]
        calls = [blk for blk in body.split('_build_agent_context(')[1:]]
        assert len(calls) == 2, f'expected 2 _build_agent_context calls, found {len(calls)}'
        for i, blk in enumerate(calls):
            head = blk[:500]
            assert 'incognito=' in head, f'call {i} missing incognito=: {head!r}'
            assert 'session_id=session_id' in head, f'call {i} missing session_id=session_id: {head!r}'
            assert 'source=' in head, f'call {i} missing source=: {head!r}'

    def test_revive_session_dicts_carry_incognito_and_source(self, ar):
        """Both Mode B and Mode A session dicts built inside
        _revive_from_agent_log must set 'incognito' and 'source' — without
        them, _fresh_context_for / _respawn_sysprompt_args (which read these
        keys straight off the session dict on the NEXT turn) silently treat
        a revived incognito chat as public and a revived delegated chat as
        the human's own."""
        src = (PROJECT_ROOT / 'mc' / 'blueprints' / 'agent_routes.py').read_text(encoding='utf-8')
        start = src.index('def _revive_from_agent_log(')
        end = src.index('\ndef _revive_non_claude_from_agent_log(')
        body = src[start:end]
        # Two session={...} dict literals (Mode B, Mode A), each terminated
        # by the following `with mgr.lock:` line.
        blocks = body.split('with mgr.lock:')[:-1]
        session_blocks = [b for b in blocks if "'character': _revive_character" in b]
        assert len(session_blocks) == 2, (
            f'expected 2 revive session-dict blocks, found {len(session_blocks)}')
        for i, blk in enumerate(session_blocks):
            assert "'incognito': _revive_incognito" in blk, (
                f"revive session dict {i} does not set 'incognito'")
            assert "'source': _revive_source" in blk, (
                f"revive session dict {i} does not set 'source'")


# ── _respawn_sysprompt_args drops `source` on the stale-fingerprint rebuild,
#    renaming a delegated worker mid-conversation ────────────────────────────

class TestRespawnSyspromptArgsSource:

    def test_stale_fingerprint_rebuild_carries_source(self, ar, monkeypatch):
        """f_83b82859 — FAILS on the parent commit.

        A delegated worker (source='agent', no persona) mid-conversation:
        when _context_fingerprint moves (any rules/memory/roster edit — the
        Scribe writes MEMORY.md during normal operation), the stash is
        dropped and the prompt is rebuilt. Losing `source` there flips
        `_delegated_unnamed` to False, so the worker's self-description
        silently reverts to the project's default agent_name mid-turn.
        """
        monkeypatch.setattr(ar, '_context_fingerprint', lambda project: 'NEW-FP')
        monkeypatch.setattr(ar, '_session_character_parts', lambda project, session: ('', '', []))
        captured = {}

        def _fake_build(project, **kw):
            captured.update(kw)
            return 'REBUILT-CTX'
        monkeypatch.setattr(ar, '_build_agent_context', _fake_build)
        monkeypatch.setattr(ar, '_sysprompt_file_args', lambda ctx: (['--x'], '/tmp/fake'))

        session = {'source': 'agent', 'character': None, 'session_id': 's1',
                  '_system_prompt': 'OLD', '_system_prompt_fp': 'STALE-FP'}
        ar._respawn_sysprompt_args(session, PROJECT, 'go on')
        assert captured.get('source') == 'agent', (
            'the stale-fingerprint rebuild dropped `source`, which silently '
            "renames a delegated worker to the project's default agent name "
            'mid-conversation')
