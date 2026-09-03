"""The Floor must show that a figure has helpers out.

The Floor is the "who is doing what" view, and a dispatched subagent was
invisible on it entirely — Ron asked "is anyone working right now?" six times
on 2026-09-02 while builders were mid-run. `/agent/status` grew the data in
MC-937 Phase 4 (`5c77642`); the Floor never carried it.

Liveness is NOT re-decided here: `_figure_subagents` delegates to
`agent_routes._active_subagents_for_session`, so the Floor cannot drift into a
second heuristic. These tests pin that delegation and the running-only gate.
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


def test_idle_session_is_never_scanned(fr):
    """An idle figure costs zero — same gate /agent/status applies."""
    assert fr._figure_subagents({'status': 'idle', 'project_id': 'x'}) == []


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
