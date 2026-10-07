"""Token-based auto-fresh trigger (docs/CONTEXT_ECONOMY_SPEC.md §2) — unit
tests for the pure decision helpers in mc/blueprints/agent_routes.py:
`_context_tokens_over_threshold`, `_auto_fresh_trigger`, `_in_flight_children`.

These fail before the helpers exist (ImportError via getattr below) and pass
once `context_rollover_tokens` (default 200000, 0 disables) is wired in as
an independent OR with the existing byte-based `_session_too_large` backstop.
Never spawns a real model CLI — pure function calls against monkeypatched
config/state.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest


@pytest.fixture()
def ar(monkeypatch):
    import server  # noqa: F401  (registers the blueprint)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as _ar
    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    try:
        yield _ar
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


class TestContextTokensOverThreshold:
    def test_default_threshold_200k(self, ar, monkeypatch):
        from mc import state as mc_state
        monkeypatch.delitem(mc_state.CONFIG, 'context_rollover_tokens', raising=False)
        assert ar._context_tokens_over_threshold(199_999) is False
        assert ar._context_tokens_over_threshold(200_000) is True

    def test_zero_disables(self, ar, monkeypatch):
        from mc import state as mc_state
        monkeypatch.setitem(mc_state.CONFIG, 'context_rollover_tokens', 0)
        assert ar._context_tokens_over_threshold(10_000_000) is False

    def test_custom_threshold(self, ar, monkeypatch):
        from mc import state as mc_state
        monkeypatch.setitem(mc_state.CONFIG, 'context_rollover_tokens', 5000)
        assert ar._context_tokens_over_threshold(60_000) is True
        assert ar._context_tokens_over_threshold(59_999) is False

    def test_none_never_trips(self, ar, monkeypatch):
        from mc import state as mc_state
        monkeypatch.setitem(mc_state.CONFIG, 'context_rollover_tokens', 100)
        assert ar._context_tokens_over_threshold(None) is False


class TestAutoFreshTrigger:
    def test_token_trigger_fires_without_touching_disk(self, ar, monkeypatch):
        """Token check must short-circuit before the byte check — if it
        called _session_too_large it would try to stat a real transcript
        path and this test would blow up on a nonexistent project dir."""
        from mc import state as mc_state
        monkeypatch.setitem(mc_state.CONFIG, 'context_rollover_tokens', 1000)
        monkeypatch.setattr(ar, '_session_too_large',
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError('byte check must not run when tokens already trip')))
        reason, detail = ar._auto_fresh_trigger('/no/such/project', 'sid-1', context_tokens=65_000)
        assert (reason, detail) == ('tokens', 65_000)

    def test_known_tokens_under_threshold_never_falls_through_to_bytes(self, ar, monkeypatch):
        """Regression (2026-09-18): clayrune_website auto-freshed twice at
        6.4 MB/9.9 MB transcripts whose real context was only 123k/85k
        tokens, well under threshold — because the OLD OR-based trigger ran
        the byte check independently of a KNOWN token result, and the byte
        check tripped on base64 image blobs the token figure never saw. A
        known token figure now decides on its own; the byte check must not
        even be consulted."""
        from mc import state as mc_state
        monkeypatch.setitem(mc_state.CONFIG, 'context_rollover_tokens', 1_000_000)
        monkeypatch.setattr(ar, '_session_too_large',
                            lambda *a, **k: (_ for _ in ()).throw(
                                AssertionError('byte check must not run when tokens are known')))
        reason, detail = ar._auto_fresh_trigger('/p', 'sid-1', context_tokens=100)
        assert (reason, detail) == (None, 0)

    def test_neither_trigger_fires(self, ar, monkeypatch):
        from mc import state as mc_state
        monkeypatch.setitem(mc_state.CONFIG, 'context_rollover_tokens', 1_000_000)
        monkeypatch.setattr(ar, '_session_too_large', lambda pp, sid: (False, 1024))
        reason, detail = ar._auto_fresh_trigger('/p', 'sid-1', context_tokens=100)
        assert reason is None
        assert detail == 0

    def test_no_live_context_tokens_falls_back_to_byte_only(self, ar, monkeypatch):
        """A revived session (server restarted, no in-memory context_tokens)
        must still get the byte-based backstop — None never trips tokens."""
        from mc import state as mc_state
        monkeypatch.setitem(mc_state.CONFIG, 'context_rollover_tokens', 100)
        monkeypatch.setattr(ar, '_session_too_large', lambda pp, sid: (True, 6 * 1024 * 1024))
        reason, detail = ar._auto_fresh_trigger('/p', 'sid-1', context_tokens=None)
        assert reason == 'bytes'


@pytest.mark.parametrize('ref,expected', [('global:dave', 'tokens'),
                                        ('global:builder', None),
                                        ('project:dave', None)])
def test_auto_fresh_uses_character_threshold(ar, monkeypatch, ref, expected):
    monkeypatch.setitem(ar.state.CONFIG, 'context_rollover_tokens', 200_000)
    monkeypatch.setitem(ar.state.CONFIG, 'context_rollover_by_character',
                        {'global:dave': 120_000})
    reason, detail = ar._auto_fresh_trigger(
        '/p', 'sid', 130_000, session_or_character={'character': ref})
    assert (reason, detail) == (expected, 130_000 if expected else 0)


@pytest.mark.parametrize('provider', ['codex', 'gemini', 'qwen'])
def test_mode_a_rollover_uses_character_threshold(ar, monkeypatch, provider):
    monkeypatch.setitem(ar.state.CONFIG, 'context_rollover_tokens', 200_000)
    monkeypatch.setitem(ar.state.CONFIG, 'context_rollover_by_character',
                        {'global:dave': 120_000})
    monkeypatch.setattr(ar, '_auto_fresh_handoff',
                        lambda *a, **k: ('handoff', 'rolled', 'activity'))
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a: None)
    session = {'character': {'name': 'dave', 'scope': 'global'},
               'context_tokens': 130_000, 'provider_session_id': 'old'}
    assert ar._mode_a_token_rollover('/p', 'p', 's', session, provider, 'continue') == \
        'handoff\n\ncontinue'
    assert session['context_tokens'] is None
    assert 'provider_session_id' not in session


class TestInFlightChildren:
    def test_finds_a_running_child_by_notify_session(self, ar):
        from mc import state as mc_state
        mc_state.agent_sessions['child-1'] = {
            'project_id': 'p1', 'session_id': 'child-1',
            '_notify_session': 'parent-1', 'status': 'running', 'task': 'do the thing',
        }
        kids = ar._in_flight_children('p1', 'parent-1')
        assert len(kids) == 1
        assert kids[0]['session_id'] == 'child-1'

    def test_ignores_done_and_error_children(self, ar):
        from mc import state as mc_state
        mc_state.agent_sessions['child-done'] = {
            'project_id': 'p1', 'session_id': 'child-done',
            '_notify_session': 'parent-1', 'status': 'done', 'task': 'finished',
        }
        mc_state.agent_sessions['child-err'] = {
            'project_id': 'p1', 'session_id': 'child-err',
            '_notify_session': 'parent-1', 'status': 'error', 'task': 'failed',
        }
        assert ar._in_flight_children('p1', 'parent-1') == []

    def test_ignores_other_projects_and_other_parents(self, ar):
        from mc import state as mc_state
        mc_state.agent_sessions['other-proj'] = {
            'project_id': 'p2', 'session_id': 'other-proj',
            '_notify_session': 'parent-1', 'status': 'running', 'task': 'x',
        }
        mc_state.agent_sessions['other-parent'] = {
            'project_id': 'p1', 'session_id': 'other-parent',
            '_notify_session': 'someone-else', 'status': 'running', 'task': 'y',
        }
        assert ar._in_flight_children('p1', 'parent-1') == []

    def test_survives_a_rollover_because_the_mc_session_id_is_unchanged(self, ar):
        """The whole point of keeping the MC session_id stable across an
        auto-fresh roll (docs/CONTEXT_ECONOMY_SPEC.md §3): a child dispatched
        BEFORE the roll must still be found by the SAME parent session_id
        AFTER the roll mutates the parent's claude_session_id/proc in place."""
        from mc import state as mc_state
        parent = {'project_id': 'p1', 'session_id': 'parent-1',
                  'claude_session_id': 'old-csid', 'status': 'idle'}
        mc_state.agent_sessions['parent-1'] = parent
        mc_state.agent_sessions['child-1'] = {
            'project_id': 'p1', 'session_id': 'child-1',
            '_notify_session': 'parent-1', 'status': 'running', 'task': 'still going',
        }
        before = ar._in_flight_children('p1', parent['session_id'])
        assert len(before) == 1
        # Simulate what auto-fresh actually does: mutate the SAME dict in
        # place (new claude_session_id, process, etc.) -- never replace the
        # agent_sessions[session_id] entry with a new dict/key.
        parent['claude_session_id'] = 'new-csid'
        parent['context_tokens'] = None
        after = ar._in_flight_children('p1', parent['session_id'])
        assert after == before
