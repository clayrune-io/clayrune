"""MC-1179 SF29: a run whose owner went away is finished as `lost`, not left open.

Every store here lives in a temp directory; nothing touches the real data root.
"""
from __future__ import annotations

import time

from flask import Flask
import pytest

from mc import state, test_run_floor as projection
from mc import test_run_lost_sweep as sweep
from mc.blueprints import floor_routes as floor, test_run_routes as routes
from mc.test_run_store import TERMINAL_STATES, TestRunStore as RunStore

NOW = 1_000_000.0
OLD = NOW - sweep.LOST_AFTER_SECONDS - 1
RECENT = NOW - 60


@pytest.fixture
def env(tmp_path, monkeypatch):
    store = RunStore(tmp_path)
    sessions: dict = {}
    projects = [{'id': 'a', 'name': 'Alpha'}]
    clock = {'now': NOW}
    monkeypatch.setattr(sweep, '_now', lambda: clock['now'])
    monkeypatch.setattr(sweep, '_last_sweep', {})
    monkeypatch.setattr(state, 'CONFIG', {projection.FLAG: True})
    monkeypatch.setattr(routes, '_storage', lambda: store)
    floor.wire(agent_sessions_ref=sessions, load_projects_fn=lambda: projects,
               list_characters_fn=lambda **kw: [])
    monkeypatch.setattr(floor, '_figure_subagents', lambda *args: [])
    monkeypatch.setattr(floor._delegation_waiting, 'waiting_by_parent', lambda: {})
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(floor.bp)
    return store, sessions, projects, clock, app.test_client()


def open_run(store, *, sid='gone', at=OLD, pid='a'):
    return store.create(pid, observed_at=at, owner_session_id=sid, runner='pytest')


def live(sessions, sid='alive', pid='a'):
    sessions[sid] = {'session_id': sid, 'project_id': pid, 'status': 'idle'}


def test_dead_owner_and_old_updated_at_is_finished_lost(env):
    store, sessions, _, _, _ = env
    run = open_run(store)
    lost = sweep.sweep_snapshots(store, 'a', store.active_snapshots('a'), sessions, NOW)
    assert lost == {run['run_id']}
    done = store.get('a', run['run_id'])
    assert (done['state'], done['capture'], done['finished_at']) == ('lost', 'lost', NOW)


def test_live_owner_is_not_swept(env):
    store, sessions, _, _, _ = env
    live(sessions)
    run = open_run(store, sid='alive')
    assert sweep.sweep_snapshots(store, 'a', store.active_snapshots('a'), sessions, NOW) == set()
    assert store.get('a', run['run_id'])['state'] == 'running'


def test_owner_in_another_project_counts_as_not_live(env):
    store, sessions, _, _, _ = env
    live(sessions, 'alive', pid='other')
    run = open_run(store, sid='alive')
    assert sweep.sweep_snapshots(store, 'a', store.active_snapshots('a'), sessions, NOW) == {run['run_id']}


def test_dead_owner_with_recent_update_is_not_swept(env):
    store, sessions, _, _, _ = env
    run = open_run(store, at=RECENT)
    assert sweep.sweep_snapshots(store, 'a', store.active_snapshots('a'), sessions, NOW) == set()
    assert store.get('a', run['run_id'])['state'] == 'running'


def test_recent_event_keeps_an_old_run_open(env):
    store, sessions, _, _, _ = env
    run = open_run(store)
    store.apply('a', run['run_id'], dict(
        schema=1, run_id=run['run_id'], producer_id='p', producer_seq=1,
        observed_at=RECENT, type='heartbeat', payload={}))
    assert sweep.sweep_snapshots(store, 'a', store.active_snapshots('a'), sessions, NOW) == set()


def test_ownerless_run_is_not_swept(env):
    store, sessions, _, _, _ = env
    run = open_run(store, sid=None)
    assert sweep.sweep_snapshots(store, 'a', store.active_snapshots('a'), sessions, NOW) == set()
    assert store.get('a', run['run_id'])['state'] == 'running'


def test_terminal_run_is_never_rewritten(env):
    store, sessions, _, _, _ = env
    run = open_run(store)
    store.finish('a', run['run_id'], state='passed', exit_code=0, observed_at=OLD + 1)
    path = store._path('a', run['run_id'])
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    # Hand the sweep the terminal snapshot directly, as well as the store's own view.
    terminal = store.get('a', run['run_id'])
    assert sweep.sweep_snapshots(store, 'a', [terminal], sessions, NOW) == set()
    assert sweep.sweep_snapshots(store, 'a', store.active_snapshots('a'), sessions, NOW) == set()
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert store.get('a', run['run_id'])['state'] == 'passed'


def test_event_after_lost_does_not_reopen_the_run(env):
    store, sessions, _, _, _ = env
    run = open_run(store)
    sweep.sweep_snapshots(store, 'a', store.active_snapshots('a'), sessions, NOW)
    path = store._path('a', run['run_id'])
    before = path.read_bytes()
    after = store.apply('a', run['run_id'], dict(
        schema=1, run_id=run['run_id'], producer_id='late', producer_seq=1,
        observed_at=NOW + 5, type='collection', payload=dict(selected_total=3, revision=1)))
    assert after['state'] == 'lost' and after['selected_total'] is None
    assert path.read_bytes() == before
    again = store.finish('a', run['run_id'], state='passed', exit_code=0, observed_at=NOW + 6)
    assert again['state'] == 'lost'


def test_floor_reads_inside_the_window_sweep_once(env, monkeypatch):
    store, sessions, _, clock, client = env
    first = open_run(store, sid='gone')
    walks = []
    original = sweep.sweep_snapshots

    def spy(*args, **kw):
        walks.append(1)
        return original(*args, **kw)

    monkeypatch.setattr(sweep, 'sweep_snapshots', spy)
    assert client.get('/api/floor').status_code == 200
    assert store.get('a', first['run_id'])['state'] == 'lost'
    second = open_run(store, sid='gone')
    clock['now'] = NOW + sweep.SWEEP_INTERVAL_SECONDS - 1
    client.get('/api/floor')
    assert len(walks) == 1
    assert store.get('a', second['run_id'])['state'] == 'running'
    clock['now'] = NOW + sweep.SWEEP_INTERVAL_SECONDS
    client.get('/api/floor')
    assert len(walks) == 2
    assert store.get('a', second['run_id'])['state'] == 'lost'


def test_floor_for_room_with_only_unavailable_runs_is_quiet(env):
    store, sessions, _, clock, client = env
    open_run(store, sid='gone', at=RECENT)   # too recent to sweep, owner absent
    body = client.get('/api/floor').get_json()
    assert body['rooms'] == []
    quiet = next(c for c in body['quiet'] if c['id'] == 'a')
    summary = quiet['test_runs']
    assert summary['active_count'] == 0
    assert summary['unavailable_owner_count'] == 1
    assert summary['runs'] == []
    assert projection.room_visible(summary) is False


def test_unavailable_roots_stay_out_of_the_aggregate(env):
    store, sessions, _, _, _ = env
    live(sessions)
    mine = open_run(store, sid='alive', at=RECENT)
    open_run(store, sid='gone', at=RECENT)
    out = projection.project('a', store.active_snapshots('a'), sessions)
    assert out['summary']['active_count'] == 1
    assert [r['run_id'] for r in out['summary']['runs']] == [mine['run_id']]
    assert out['summary']['unavailable_owner_count'] == 1


def test_startup_sweep_finishes_old_orphans_in_every_project(env):
    store, sessions, projects, _, _ = env
    projects.append({'id': 'b'})
    old_a, old_b = open_run(store), open_run(store, pid='b')
    young = open_run(store, at=RECENT)
    total = sweep.startup_sweep(store, projects, sessions)
    assert total == 2
    assert store.get('a', old_a['run_id'])['state'] == 'lost'
    assert store.get('b', old_b['run_id'])['state'] == 'lost'
    assert store.get('a', young['run_id'])['state'] == 'running'


def test_constants_match_the_decision():
    assert sweep.LOST_AFTER_SECONDS == 60 * 60
    assert sweep.SWEEP_INTERVAL_SECONDS == 60
    assert 'lost' in TERMINAL_STATES
