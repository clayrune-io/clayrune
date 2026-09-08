"""The Floor must show that a figure has helpers out.

The Floor is the "who is doing what" view, and a dispatched subagent was
invisible on it entirely — Ron asked "is anyone working right now?" six times
on 2026-09-02 while builders were mid-run. `/agent/status` grew the data in
MC-937 Phase 4 (`5c77642`); the Floor never carried it.

Liveness is NOT re-decided here: `_figure_subagents` delegates to
`agent_routes._active_subagents_for_session`, so the Floor cannot drift into a
second heuristic. These tests pin that delegation and — hm_d9c76579
f_cdb76b7a — that `_figure_subagents` carries NO status gate of its own.
`37cc20d` widened `_active_subagents_for_session`'s gate to
`('running', 'idle')` (a parent waiting on a helper sits at 'idle'), but this
file used to still assert the pre-fix `!= 'running'` short-circuit, which is
exactly the duplicate-gate regression: the Floor never reached the fixed
function for an idle parent.
"""
import pytest


@pytest.fixture
def fr(monkeypatch):
    """Import the module WITHOUT importing `server`.

    `import server` runs wire() with the real callables, and test_floor_routes'
    own fixture then cannot rebind them — running these tests first made that
    file fail. Stub the one wired dependency this code path uses instead.
    """
    from mc.blueprints import floor_routes as _fr
    monkeypatch.setattr(_fr, 'load_projects', lambda: [
        {'id': 'x', 'name': 'X', 'project_path': ''}], raising=False)
    return _fr


def test_idle_session_with_a_waiting_helper_still_shows_it(fr, monkeypatch):
    """Regression for f_cdb76b7a / f_31dbc93d — FAILS on the parent commit.

    A parent that dispatches a helper and then waits sits at status='idle',
    which is exactly the case the '+N helpers' badge exists for. Before this
    fix, `_figure_subagents` short-circuited on its OWN `!= 'running'` gate
    before `_active_subagents_for_session` (already fixed by 37cc20d to
    accept 'idle') was ever reached, so the Floor showed subagents=[] for a
    waiting parent even though /agent/status reported the helper as live.
    """
    seen = {}

    def _fake(s, project_path):
        seen['called'] = True
        return [{'agent_id': 'a1', 'running': True, 'tool_calls': 3}]

    monkeypatch.setattr(
        'mc.blueprints.agent_routes._active_subagents_for_session', _fake)
    out = fr._figure_subagents({'status': 'idle', 'project_id': 'x'})
    assert seen.get('called'), (
        'an idle (waiting-on-helper) session must still reach agent_routes — '
        'the Floor must not re-decide liveness with its own gate')
    assert out[0]['tool_calls'] == 3


def test_running_session_delegates_to_agent_routes(fr, monkeypatch):
    seen = {}

    def _fake(s, project_path):
        seen['called'] = True
        return [{'agent_id': 'a1', 'running': True, 'tool_calls': 7}]

    monkeypatch.setattr(
        'mc.blueprints.agent_routes._active_subagents_for_session', _fake)
    out = fr._figure_subagents({'status': 'running', 'project_id': 'x'})
    assert seen.get('called'), 'the Floor must reuse agent_routes, not its own rule'
    assert out[0]['tool_calls'] == 7


def test_lookup_failure_never_breaks_the_floor(fr, monkeypatch):
    """A figure with an unreadable transcript still renders — [] not a 500."""
    def _boom(s, project_path):
        raise RuntimeError('transcript unreadable')

    monkeypatch.setattr(
        'mc.blueprints.agent_routes._active_subagents_for_session', _boom)
    assert fr._figure_subagents({'status': 'running', 'project_id': 'x'}) == []


def test_figure_payload_carries_the_field(fr):
    """The key must exist on every figure, so the client can rely on it."""
    fig = fr._figure({'status': 'idle', 'project_id': 'x', 'session_id': 's'})
    assert 'subagents' in fig and fig['subagents'] == []
