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


# ── OS39 ────────────────────────────────────────────────────────────────────

def _client(coord):
    import server
    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c.environ_base['HTTP_ORIGIN'] = 'http://localhost:5199'
    return c


def _stub_project(coord, monkeypatch):
    monkeypatch.setattr(coord, 'load_project', lambda pid: {'id': pid})
    monkeypatch.setattr(coord, '_push_sse', lambda *a, **k: None)


@pytest.mark.parametrize('targets', [5, 'abc', ['a'] * 21, ['x' * 201], [1, 2], [['nested']]])
def test_publish_refuses_bad_targets(coord, monkeypatch, targets):
    _stub_project(coord, monkeypatch)
    r = _client(coord).post('/api/project/p1/coord/publish',
                            json={'type': 'completion', 'summary': 's', 'targets': targets})
    assert r.status_code == 400
    assert not coord._events_path('p1').exists()


def test_publish_caps_agent_and_summary(coord, monkeypatch):
    _stub_project(coord, monkeypatch)
    r = _client(coord).post('/api/project/p1/coord/publish',
                            json={'type': 'intention', 'agent': 'a' * 5000,
                                  'summary': 's' * 5000, 'targets': ['f.py']})
    assert r.status_code == 200
    ev = r.get_json()['event']
    assert len(ev['agent']) == 80 and len(ev['summary']) == 500


def test_one_malformed_stored_event_does_not_blind_the_feed(coord, monkeypatch):
    _live(coord, monkeypatch, {})
    good = {'type': 'completion', 'ts': coord.now_iso(), 'summary': 'fix scheduler', 'targets': ['a.py'],
            'agent': 'x'}
    bad = {'type': 'completion', 'ts': coord.now_iso(), 'summary': 'boom', 'targets': 5}
    monkeypatch.setattr(coord, '_read_events', lambda *a, **k: [bad, good])
    out = coord.sibling_activity('p1', 'sid-me', 'fix scheduler')
    assert [i['summary'] for i in out] == ['fix scheduler']
