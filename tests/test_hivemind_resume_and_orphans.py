"""MC-1179 OS24 (a paused or stopped hivemind can never be resumed) and OS25
(a workstream whose worker is gone stays `active` and blocks the run).

State is built on disk under the fixture's hivemind directory and the real
routes and loop body are called; no worker, process or orchestrator starts.
"""
import json
import time

import pytest

from tests.test_hivemind_routes import _create, client  # noqa: F401  (client is a fixture)


def _ws_path(client, hm_id, ws_id):
    return client.hm_dir / hm_id / 'workstreams' / f'{ws_id}.json'


def _ws(client, hm_id, ws_id):
    return json.loads(_ws_path(client, hm_id, ws_id).read_text(encoding='utf-8'))


def _set(client, hm_id, ws_id, **fields):
    ws = _ws(client, hm_id, ws_id)
    ws.update(fields)
    _ws_path(client, hm_id, ws_id).write_text(json.dumps(ws), encoding='utf-8')


def _manifest(client, hm_id):
    return json.loads((client.hm_dir / hm_id / 'manifest.json').read_text(encoding='utf-8'))


class OneTick:
    stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.stopped = True


def _tick(client, monkeypatch, spawned=None):
    """One real pass of the orchestrator loop; spawning is recorded, not run."""
    hm = client.hm
    monkeypatch.setattr(hm, '_hivemind_orchestrator_stop', OneTick())
    monkeypatch.setattr(hm.obs, 'heartbeat', lambda *a: None)
    monkeypatch.setattr(hm, '_hm_dispatch_orchestrator', lambda *a: None)
    monkeypatch.setattr(hm, '_hm_start_integration', lambda *a: None)

    def spawn(manifest, ws, p, hivemind_id, ws_id):
        sid = f'hm_new_{ws_id}'
        if spawned is not None:
            spawned.append(ws_id)
        client.state.agent_sessions[sid] = {
            'hivemind_id': hivemind_id, 'hivemind_ws_id': ws_id,
            'status': 'running', 'project_id': 'thm', 'proc': None, 'mode': 'A',
            'log_lines': []}
        return sid

    monkeypatch.setattr(hm, '_hm_spawn_worker_session', spawn)
    hm._hivemind_orchestrator_loop()


def _live(hm_id, ws_id, status='running', **extra):
    return {'hivemind_id': hm_id, 'hivemind_ws_id': ws_id, 'status': status,
            'project_id': 'thm', 'proc': None, 'mode': 'A', 'log_lines': [], **extra}


GONE_LONG_AGO = lambda: time.time() - 600  # noqa: E731


# ── OS25: a worker that is gone ───────────────────────────────────────────────

class TestGoneOwner:
    def test_dead_active_workstreams_do_not_hold_every_slot(self, client, monkeypatch):
        """The finding's measurement: three active workstreams with no session
        and one pending; nothing ever spawned."""
        hm_id = _create(client, workstreams=[
            {'id': f'ws_a{i}', 'title': f'dead {i}'} for i in range(3)
        ] + [{'id': 'ws_next', 'title': 'waiting'}])['hivemind']['id']
        for i in range(3):
            _set(client, hm_id, f'ws_a{i}', status='active',
                 current_agent_session_id=f'hm_dead{i}',
                 owner_gone_since=GONE_LONG_AGO())
        spawned = []
        _tick(client, monkeypatch, spawned)
        assert set(spawned) == {'ws_a0', 'ws_a1', 'ws_a2'}, 'dead owners must free their slots'
        for i in range(3):
            ws = _ws(client, hm_id, f'ws_a{i}')
            assert ws['current_agent_session_id'] == f'hm_new_ws_a{i}'

    def test_first_sighting_only_notes_it(self, client, monkeypatch):
        hm_id = _create(client)['hivemind']['id']
        _set(client, hm_id, 'ws_001', status='active', current_agent_session_id='hm_gone')
        _tick(client, monkeypatch)
        ws = _ws(client, hm_id, 'ws_001')
        assert ws['status'] == 'active'
        assert isinstance(ws['owner_gone_since'], float)

    def test_gone_past_grace_goes_back_to_pending_with_a_retry(self, client, monkeypatch):
        hm_id = _create(client)['hivemind']['id']
        _set(client, hm_id, 'ws_001', status='active', current_agent_session_id='hm_gone',
             owner_gone_since=GONE_LONG_AGO())
        _tick(client, monkeypatch, [])
        ws = _ws(client, hm_id, 'ws_001')
        assert ws['retry_count'] == 1
        assert ws['last_agent_session_id'] == 'hm_gone'
        assert 'owner_gone_since' not in ws
        # the same tick respawned it, so it is `active` under a NEW owner
        assert ws['current_agent_session_id'] == 'hm_new_ws_001'

    def test_stopped_session_is_taken_back_too(self, client, monkeypatch):
        hm_id = _create(client)['hivemind']['id']
        client.state.agent_sessions['hm_stopped'] = _live(hm_id, 'ws_001', 'stopped')
        _set(client, hm_id, 'ws_001', status='active', current_agent_session_id='hm_stopped',
             owner_gone_since=GONE_LONG_AGO())
        _tick(client, monkeypatch, [])
        assert _ws(client, hm_id, 'ws_001')['retry_count'] == 1

    def test_retries_exhausted_fails_the_workstream(self, client, monkeypatch):
        hm_id = _create(client)['hivemind']['id']
        _set(client, hm_id, 'ws_001', status='active', current_agent_session_id='hm_gone',
             retry_count=2, owner_gone_since=GONE_LONG_AGO())
        _tick(client, monkeypatch, [])
        ws = _ws(client, hm_id, 'ws_001')
        assert ws['status'] == 'failed' and ws['failure_reason'] == 'worker_gone'

    def test_roster_workstream_is_held_not_retried(self, client, monkeypatch):
        hm_id = _create(client)['hivemind']['id']
        _set(client, hm_id, 'ws_001', status='active', character='global:somebody',
             current_agent_session_id='hm_gone', owner_gone_since=GONE_LONG_AGO())
        spawned = []
        _tick(client, monkeypatch, spawned)
        ws = _ws(client, hm_id, 'ws_001')
        assert ws['status'] == 'blocked' and ws['failure_reason'] == 'worker_gone'
        assert ws['current_agent_session_id'] is None
        assert 'ws_001' not in spawned

    @pytest.mark.parametrize('status', ['running', 'idle'])
    def test_live_owner_is_left_alone_and_its_marker_cleared(self, client, monkeypatch, status):
        hm_id = _create(client)['hivemind']['id']
        client.state.agent_sessions['hm_live'] = _live(hm_id, 'ws_001', status, process_alive=True)
        _set(client, hm_id, 'ws_001', status='active', current_agent_session_id='hm_live',
             owner_gone_since=GONE_LONG_AGO())
        _tick(client, monkeypatch, [])
        ws = _ws(client, hm_id, 'ws_001')
        assert ws['status'] == 'active' and ws['current_agent_session_id'] == 'hm_live'
        assert 'owner_gone_since' not in ws
        assert ws.get('retry_count', 0) == 0

    def test_finished_session_is_still_collected_as_before(self, client, monkeypatch):
        hm_id = _create(client)['hivemind']['id']
        client.state.agent_sessions['hm_done'] = _live(hm_id, 'ws_001', 'completed')
        _set(client, hm_id, 'ws_001', status='active', current_agent_session_id='hm_done')
        _tick(client, monkeypatch, [])
        assert _ws(client, hm_id, 'ws_001')['status'] == 'completed'


# ── OS24: pause / stop, then resume ───────────────────────────────────────────

class TestResume:
    def test_pause_then_start_gives_the_workstream_back(self, client):
        hm_id = _create(client)['hivemind']['id']
        _set(client, hm_id, 'ws_001', status='active', current_agent_session_id='hm_gone')
        client.post(f'/api/hivemind/{hm_id}/pause')
        assert _ws(client, hm_id, 'ws_001')['status'] == 'paused'
        assert client.post(f'/api/hivemind/{hm_id}/start').status_code == 200
        ws = _ws(client, hm_id, 'ws_001')
        assert ws['status'] == 'pending'
        assert ws['current_agent_session_id'] is None
        ready = client.hm._hm_resolve_dependencies(client.hm._hm_list_workstreams(hm_id))
        assert [w['id'] for w in ready] == ['ws_001']

    def test_stop_then_start_leaves_nothing_paused(self, client):
        hm_id = _create(client)['hivemind']['id']
        client.post(f'/api/hivemind/{hm_id}/stop')
        assert {_ws(client, hm_id, w)['status'] for w in ('ws_001', 'ws_002')} == {'paused'}
        client.post(f'/api/hivemind/{hm_id}/start')
        assert {_ws(client, hm_id, w)['status'] for w in ('ws_001', 'ws_002')} == {'pending'}
        assert _manifest(client, hm_id)['status'] == 'active'
        ready = client.hm._hm_resolve_dependencies(client.hm._hm_list_workstreams(hm_id))
        assert [w['id'] for w in ready] == ['ws_001']

    def test_paused_workstream_whose_worker_still_runs_stays_owned(self, client):
        hm_id = _create(client)['hivemind']['id']
        client.state.agent_sessions['hm_live'] = _live(hm_id, 'ws_001', process_alive=True)
        _set(client, hm_id, 'ws_001', status='active', current_agent_session_id='hm_live')
        client.post(f'/api/hivemind/{hm_id}/pause')
        client.post(f'/api/hivemind/{hm_id}/start')
        ws = _ws(client, hm_id, 'ws_001')
        assert ws['status'] == 'active' and ws['current_agent_session_id'] == 'hm_live'

    def test_worker_that_finished_during_pause_is_collected_after_resume(self, client, monkeypatch):
        hm_id = _create(client)['hivemind']['id']
        client.state.agent_sessions['hm_done'] = _live(hm_id, 'ws_001')
        _set(client, hm_id, 'ws_001', status='active', current_agent_session_id='hm_done')
        client.post(f'/api/hivemind/{hm_id}/pause')
        client.state.agent_sessions['hm_done']['status'] = 'completed'
        client.post(f'/api/hivemind/{hm_id}/start')
        _tick(client, monkeypatch, [])
        assert _ws(client, hm_id, 'ws_001')['status'] == 'completed'

    def test_stop_stops_the_workers_and_only_the_workers(self, client):
        hm_id = _create(client)['hivemind']['id']
        sessions = client.state.agent_sessions
        sessions['hm_w'] = _live(hm_id, 'ws_001')
        sessions['hm_orch'] = {'hivemind_id': hm_id, 'status': 'running', 'project_id': 'thm',
                               'proc': None, 'mode': 'A', 'log_lines': []}
        sessions['hm_other'] = _live('hm_someone_else', 'ws_001')
        sessions['hm_done'] = _live(hm_id, 'ws_002', 'completed')
        _set(client, hm_id, 'ws_001', status='active', current_agent_session_id='hm_w')
        assert client.post(f'/api/hivemind/{hm_id}/stop').status_code == 200
        assert sessions['hm_w']['status'] == 'stopped'
        assert sessions['hm_orch']['status'] == 'running'
        assert sessions['hm_other']['status'] == 'running'
        assert sessions['hm_done']['status'] == 'completed'

    def test_stop_kills_the_process_outside_the_lock(self, client, monkeypatch):
        from mc.blueprints import agent_routes as ar
        killed = []
        monkeypatch.setattr(ar, '_kill_proc_background', killed.append)
        monkeypatch.setattr(ar, '_unregister_process', lambda pid: None)

        class Proc:
            pid = 4242

        hm_id = _create(client)['hivemind']['id']
        client.state.agent_sessions['hm_w'] = _live(hm_id, 'ws_001', proc=Proc())
        client.post(f'/api/hivemind/{hm_id}/stop')
        assert len(killed) == 1 and killed[0].pid == 4242
