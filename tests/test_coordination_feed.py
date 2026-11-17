"""Coordination layer (mc/blueprints/coordination_routes.py): sibling feed.

MC-1179 pass 09 OS38 (caller must pass its own session id so it is not its own
sibling), OS39 (publish validation, one bad event must not blind the feed).
"""
import json

import pytest

from mc import state


@pytest.fixture()
def coord(tmp_path, monkeypatch):
    import server  # noqa: F401 -- registers blueprints
    from mc.blueprints import coordination_routes as c
    monkeypatch.setattr(c, 'COORD_DIR', tmp_path / 'coord')
    monkeypatch.setitem(state.CONFIG, 'coordination_enabled', True)
    return c


def _live(coord, monkeypatch, sessions):
    class _M:
        def iter_sessions(self):
            return list(sessions.items())
    monkeypatch.setattr(coord, 'get_manager', lambda pid: _M())


def test_context_builder_passes_its_own_session_id_to_the_sibling_feed(coord, monkeypatch, tmp_path):
    from mc.blueprints import agent_routes as ar
    seen = {}

    def fake(project_id, my_session_id, task, topk=3):
        seen['sid'] = my_session_id
        return []
    monkeypatch.setattr(coord, 'sibling_activity', fake)
    monkeypatch.setattr(coord, 'reconcile_commits', lambda p: None)
    monkeypatch.setattr(ar, '_recent_claude_transcripts', lambda *a, **kw: [])
    monkeypatch.setattr(ar, '_load_agent_log', lambda *a: [])
    monkeypatch.setitem(state.CONFIG, 'exploration_readback_enabled', False)
    project = {'id': 'p1', 'name': 'P1', 'project_path': str(tmp_path), 'backlog': []}
    ar._build_agent_context(project, task='fix the scheduler', session_id='sid-me', source='agent')
    assert seen['sid'] == 'sid-me'


def test_a_live_session_is_not_its_own_sibling(coord, monkeypatch):
    _live(coord, monkeypatch, {'sid-me': {'status': 'running', 'task': 'fix the scheduler'},
                               'sid-other': {'status': 'running', 'task': 'fix the scheduler too'}})
    monkeypatch.setattr(coord, '_read_events', lambda *a, **k: [])
    out = coord.sibling_activity('p1', 'sid-me', 'fix the scheduler')
    assert [i['summary'] for i in out] == ['fix the scheduler too']
