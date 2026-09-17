"""Deterministic domain-failure regressions without live workers."""
import pytest

from tests.test_hivemind_routes import client


@pytest.mark.parametrize('statuses,expected', [
    ([], None), (['completed'], 'completed'), (['failed'], 'failed'),
    (['completed', 'failed'], 'failed'), (['pending', 'failed'], None),
    (['active', 'completed'], None), (['blocked'], None),
])
def test_terminal_outcome_is_not_false_success(client, statuses, expected):
    assert client.hm._hm_terminal_outcome([{'status': s} for s in statuses]) == expected


@pytest.mark.parametrize('invalid_engine', [True, False])
def test_failed_launch_is_persisted_not_retried_every_tick(client, monkeypatch, invalid_engine):
    hm = client.hm
    manifest = {'id': 'h', 'status': 'active', 'project_id': 'p1'}
    work = {'id': 'w', 'status': 'pending', 'dependencies': []}
    monkeypatch.setattr(hm, '_hm_load_manifest', lambda _: manifest)
    monkeypatch.setattr(hm, '_hm_list_workstreams', lambda _: [work.copy()])
    monkeypatch.setattr(hm, 'load_project', lambda _: {'project_path': str(client.proj_path)})
    attempts = []
    def fail(*args):
        attempts.append(True)
        from mc.engine_selection import EngineSelectionError
        raise EngineSelectionError('unsupported selection') if invalid_engine else RuntimeError('launch uncertain')
    monkeypatch.setattr(hm, '_hm_spawn_worker_session', fail)
    monkeypatch.setattr(hm, '_hm_save_workstream', lambda hid, wid, ws: work.update(ws))
    monkeypatch.setattr(hm, '_hm_push_sse', lambda *a: None)
    hm._hm_auto_spawn_workers('h')
    hm._hm_auto_spawn_workers('h')
    assert len(attempts) == 1
    assert work['status'] == ('failed' if invalid_engine else 'blocked')
    assert work['failure_reason'] == ('unsupported_engine' if invalid_engine else 'launch_uncertain')


@pytest.mark.parametrize('failure', ['sse', 'activity', 'save_once', 'save_always'])
def test_live_spawn_survives_bookkeeping_failure_without_duplicate(client, monkeypatch, failure):
    hm = client.hm
    manifest = {'id': 'h', 'status': 'active', 'project_id': 'p1'}
    work = {'id': 'w', 'status': 'pending', 'dependencies': []}
    sessions = {}
    monkeypatch.setattr(hm, 'agent_sessions', sessions)
    monkeypatch.setattr(hm, '_hm_load_manifest', lambda _: manifest)
    monkeypatch.setattr(hm, '_hm_list_workstreams', lambda _: [work.copy()])
    monkeypatch.setattr(hm, 'load_project', lambda _: {'project_path': str(client.proj_path)})
    spawns, saves = [], []
    def spawn(*args):
        spawns.append('live')
        sessions['live'] = {'hivemind_id': 'h', 'hivemind_ws_id': 'w', 'status': 'running'}
        return 'live'
    def save(hid, wid, ws):
        saves.append(ws.copy())
        if failure == 'save_always' or (failure == 'save_once' and len(saves) == 1):
            raise OSError('disk unavailable')
        work.update(ws)
    def sse(*args):
        if failure == 'sse':
            raise RuntimeError('notification unavailable')
    def activity(*args):
        if failure == 'activity':
            raise RuntimeError('activity unavailable')
    monkeypatch.setattr(hm, '_hm_spawn_worker_session', spawn)
    monkeypatch.setattr(hm, '_hm_save_workstream', save)
    monkeypatch.setattr(hm, '_hm_push_sse', sse)
    monkeypatch.setattr(hm, '_log_agent_activity', activity)
    hm._hm_auto_spawn_workers('h')
    hm._hm_auto_spawn_workers('h')
    assert spawns == ['live']
    assert sessions['live']['status'] == 'running'
    assert all(ws['status'] == 'active' for ws in saves)
    if failure != 'save_always':
        assert work['status'] == 'active'
        assert work['current_agent_session_id'] == 'live'
        assert 'failure_reason' not in work


@pytest.mark.parametrize('failed', [True, False])
def test_orchestrator_terminates_failed_dependencies_without_synthesis(client, monkeypatch, failed):
    hm = client.hm
    (client.hm_dir / 'h').mkdir(parents=True)
    manifest = {'id': 'h', 'status': 'active', 'project_id': 'p1'}
    workstreams = [
        {'id': 'root', 'status': 'failed' if failed else 'completed', 'dependencies': []},
        {'id': 'child', 'status': 'pending' if failed else 'completed', 'dependencies': ['root']},
        {'id': 'grandchild', 'status': 'blocked' if failed else 'completed', 'dependencies': ['child']},
    ]
    class OneTick:
        stopped = False
        def is_set(self):
            return self.stopped
        def wait(self, seconds):
            self.stopped = True
    monkeypatch.setattr(hm, '_hivemind_orchestrator_stop', OneTick())
    monkeypatch.setattr(hm.obs, 'heartbeat', lambda *a: None)
    monkeypatch.setattr(hm, '_hm_load_manifest', lambda _: manifest)
    monkeypatch.setattr(hm, '_hm_list_workstreams', lambda _: [w.copy() for w in workstreams])
    def save(hid, wid, ws):
        next(w for w in workstreams if w['id'] == wid).update(ws)
    monkeypatch.setattr(hm, '_hm_save_workstream', save)
    monkeypatch.setattr(hm, '_hm_save_manifest', lambda hid, m: manifest.update(m))
    monkeypatch.setattr(hm, '_hm_auto_spawn_workers', lambda *a: None)
    events, synthesis = [], []
    monkeypatch.setattr(hm, '_hm_push_sse', lambda hid, event: events.append(event))
    monkeypatch.setattr(hm, '_hm_dispatch_orchestrator', lambda *a: synthesis.append(a))
    hm._hivemind_orchestrator_loop()
    assert manifest['status'] == ('failed' if failed else 'completed')
    assert synthesis == ([] if failed else [('h', 'synthesize')])
    assert events[-1]['status'] == manifest['status']
    if failed:
        assert [w['status'] for w in workstreams] == ['failed'] * 3
        assert workstreams[2]['failure_reason'] == 'dependency_failed'
