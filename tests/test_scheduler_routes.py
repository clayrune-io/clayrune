"""Request-level tests for the scheduler family
(mc/blueprints/scheduler_routes.py).

Added with blueprint step 1.13 (MODERNIZATION_PLAN.md Phase 5) — the last
blueprint extraction. A pure move: the route handlers + the background
_scheduler_loop are byte-verbatim from server.py, with the single Phase-2
obs.heartbeat('scheduler') line added to the loop. The agent-dispatch deps
(_dispatch_agent_internal & co.) STAY in agent_routes (1.12) and the projects
store stays in project_routes (1.11); both are late-bound via wire().

These tests guard the MOVE: registration parity (the seam's worst silent
failure), the schedules-store CRUD round-trips against a tmp schedules.json,
the run-now dispatch path with _dispatch_agent_internal PATCHED to a recorder
(MUST NOT spawn a real agent), the /runs pagination reading a seeded agent_log,
and the app-wide local_auth gate (401 before handler for a non-loopback peer).

Determinism: patches mc.blueprints.scheduler_routes.* ONLY (the Phase-0
test-port rule — never server.*). SCHEDULES_PATH and the agent-log reader are
pointed at tmp / recorders so nothing real fires. The fixture rebinds the
blueprint's wired globals on the MODULE for the duration of the test, then
restores them (wire() ran at import with the live deps).
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}

# The exact route surface 1.13 owns. A change here is intentional API churn.
EXPECTED_ROUTES = {
    '/api/schedule/<schedule_id>/run-now',
    '/api/schedule/<schedule_id>/runs',
    '/api/schedules',
    '/api/schedules/<schedule_id>',
}


class _DispatchRecorder:
    """Stand-in for _dispatch_agent_internal: records calls, returns a fake
    session id, never spawns anything. Raise-mode lets us cover the error path."""
    def __init__(self, sid='sess-fake-001', raise_exc=None):
        self.calls = []
        self._sid = sid
        self._raise = raise_exc

    def __call__(self, project_id, task, **kwargs):
        self.calls.append({'project_id': project_id, 'task': task, **kwargs})
        if self._raise is not None:
            raise self._raise
        return self._sid


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    """Flask test client + handles to the patched scheduler module.

    Patches the blueprint's wired globals ON THE MODULE (test-port rule):
    SCHEDULES_PATH -> tmp file; load_project(s) -> simple fakes; the
    agent-dispatch + agent-log seams -> recorders. Restores everything after.

    Also patches `mc.workflows` (MC-871 Q4: a schedule may invoke a workflow
    run) -- `scheduler_routes._wf` is the SAME module object, so patching it
    here covers both call sites with one set of tmp paths.
    """
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc.blueprints import local_auth as la
    from mc.blueprints import scheduler_routes as sr
    from mc import workflows as wfm

    # Deterministic gate: no LAN passcode this run (loopback exempt, LAN 401).
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    # Schedules store -> isolated tmp file.
    sched_path = tmp_path / 'schedules.json'
    monkeypatch.setattr(sr, 'SCHEDULES_PATH', sched_path)

    # Projects: a single known project so name-enrichment + continue paths work.
    projects = [{'id': 'p1', 'name': 'Project One', 'project_path': str(tmp_path / 'ws')}]
    monkeypatch.setattr(sr, 'load_projects', lambda: list(projects))
    monkeypatch.setattr(sr, 'load_project',
                        lambda pid: next((p for p in projects if p['id'] == pid), None))

    # Agent-dispatch + agent-log seams -> deterministic recorders. Default the
    # run-now path to NO continuation (no prior session) so it reaches dispatch.
    dispatch = _DispatchRecorder()
    monkeypatch.setattr(sr, '_dispatch_agent_internal', dispatch)
    monkeypatch.setattr(sr, '_latest_session_id_for_schedule', lambda pid, sid: '')
    monkeypatch.setattr(sr, '_latest_claude_sid_for_schedule', lambda pid, sid: '')
    monkeypatch.setattr(sr, '_newest_run_session_id_for_schedule', lambda pid, sid: '')
    monkeypatch.setattr(sr, '_enrich_run_entries', lambda entries: entries)
    monkeypatch.setattr(sr, '_log_agent_activity', lambda *a, **k: None)

    # Workflow store -> isolated tmp files; workflow-step dispatch -> the SAME
    # recorder (a workflow step's dispatch is indistinguishable from a raw
    # scheduled dispatch as far as these tests care).
    monkeypatch.setattr(wfm, 'WORKFLOWS_PATH', tmp_path / 'workflows.json')
    monkeypatch.setattr(wfm, 'WORKFLOW_RUNS_DIR', tmp_path / 'workflow_runs')
    monkeypatch.setattr(wfm, '_dispatch_agent_internal', dispatch)

    server.app.config['TESTING'] = True

    class Ctx:
        pass
    c = Ctx()
    c.client = server.app.test_client()
    c.sr = sr
    c.wfm = wfm
    c.sched_path = sched_path
    c.dispatch = dispatch
    c.projects = projects
    return c


def _seed_workflow(ctx, **over):
    """Create a minimal, valid, enabled workflow via the real store path and
    return its record. One agent step with no branches -- run mechanics
    aren't what these scheduler tests are checking."""
    doc = {
        'name': 'Nightly digest',
        'trigger': {'type': 'manual'},
        'nodes': [{'type': 'agent', 'name': 'only', 'project_id': 'p1',
                   'character': '', 'prompt': 'do the thing', 'x': 0, 'y': 0}],
        'edges': [],
    }
    doc.update(over)
    return ctx.wfm.create_workflow(doc)


def _seed_schedules(ctx, schedules):
    ctx.sched_path.write_text(json.dumps(schedules), encoding='utf-8')


# ── registration parity — the move's load-bearing guard ───────────────────────

def test_blueprint_registered(ctx):
    import server
    assert 'scheduler_routes' in server.app.blueprints


def test_all_expected_routes_present_under_blueprint(ctx):
    import server
    owned = {r.rule for r in server.app.url_map.iter_rules()
             if r.endpoint.startswith('scheduler_routes.')}
    missing = EXPECTED_ROUTES - owned
    assert not missing, f'routes missing from scheduler_routes blueprint: {sorted(missing)}'


def test_no_unexpected_scheduler_routes(ctx):
    import server
    owned = {r.rule for r in server.app.url_map.iter_rules()
             if r.endpoint.startswith('scheduler_routes.')}
    extra = owned - EXPECTED_ROUTES
    assert not extra, f'unpinned routes under scheduler_routes blueprint: {sorted(extra)}'


# ── GET /api/schedules — empty + populated ────────────────────────────────────

def test_get_schedules_empty(ctx):
    resp = ctx.client.get('/api/schedules')
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_get_schedules_populated_enriches_project_name(ctx):
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': 'do x', 'enabled': True,
         'schedule_type': 'daily', 'time': '09:00'},
    ])
    resp = ctx.client.get('/api/schedules')
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == 1
    assert body[0]['id'] == 's1'
    # name enrichment from load_projects()
    assert body[0]['project_name'] == 'Project One'


# ── POST /api/schedules — create happy + malformed ────────────────────────────

def test_create_schedule_happy(ctx):
    resp = ctx.client.post('/api/schedules', json={
        'project_id': 'p1', 'task': 'nightly build',
        'schedule_type': 'daily', 'time': '03:00',
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['project_id'] == 'p1'
    assert body['task'] == 'nightly build'
    assert body['enabled'] is True
    assert 'id' in body and len(body['id']) == 8
    # persisted to the tmp store
    saved = json.loads(ctx.sched_path.read_text(encoding='utf-8'))
    assert len(saved) == 1 and saved[0]['id'] == body['id']


def test_create_schedule_missing_fields_400(ctx):
    # no task
    r1 = ctx.client.post('/api/schedules', json={'project_id': 'p1'})
    assert r1.status_code == 400
    # no project_id
    r2 = ctx.client.post('/api/schedules', json={'task': 'x'})
    assert r2.status_code == 400
    # empty body
    r3 = ctx.client.post('/api/schedules', json={})
    assert r3.status_code == 400
    # nothing was written
    assert not ctx.sched_path.exists() or json.loads(ctx.sched_path.read_text()) == []


# ── PUT /api/schedules/<id> — update + 404 ────────────────────────────────────

def test_update_schedule_merges_and_recomputes(ctx):
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': 'old', 'enabled': True,
         'schedule_type': 'daily', 'time': '09:00', 'next_run': 'stale'},
    ])
    resp = ctx.client.put('/api/schedules/s1', json={'task': 'new', 'enabled': False})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['task'] == 'new'
    assert body['enabled'] is False
    saved = json.loads(ctx.sched_path.read_text(encoding='utf-8'))
    assert saved[0]['task'] == 'new'


def test_update_schedule_not_found_404(ctx):
    _seed_schedules(ctx, [])
    resp = ctx.client.put('/api/schedules/nope', json={'task': 'x'})
    assert resp.status_code == 404


# ── DELETE /api/schedules/<id> — delete + 404 ─────────────────────────────────

def test_delete_schedule(ctx):
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': 't'},
        {'id': 's2', 'project_id': 'p1', 'task': 't2'},
    ])
    resp = ctx.client.delete('/api/schedules/s1')
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True}
    saved = json.loads(ctx.sched_path.read_text(encoding='utf-8'))
    assert [s['id'] for s in saved] == ['s2']


def test_delete_schedule_not_found_404(ctx):
    _seed_schedules(ctx, [{'id': 's1', 'project_id': 'p1', 'task': 't'}])
    resp = ctx.client.delete('/api/schedules/nope')
    assert resp.status_code == 404


# ── POST /api/schedule/<id>/run-now — dispatch recorder, NO real spawn ────────

def test_run_now_dispatches_via_recorder(ctx):
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': 'fire me', 'continue_session': False},
    ])
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert body['session_id'] == 'sess-fake-001'
    # the recorder was invoked exactly once with the schedule's trigger metadata
    assert len(ctx.dispatch.calls) == 1
    call = ctx.dispatch.calls[0]
    assert call['project_id'] == 'p1'
    assert call['task'] == 'fire me'
    assert call['trigger_type'] == 'schedule'
    assert call['trigger_id'] == 's1'
    # last_run stamped for visual feedback
    saved = json.loads(ctx.sched_path.read_text(encoding='utf-8'))
    assert saved[0]['last_run']


def test_run_now_schedule_not_found_404(ctx):
    _seed_schedules(ctx, [])
    resp = ctx.client.post('/api/schedule/nope/run-now')
    assert resp.status_code == 404
    assert len(ctx.dispatch.calls) == 0


def test_run_now_missing_project_or_task_400(ctx):
    _seed_schedules(ctx, [{'id': 's1', 'project_id': '', 'task': ''}])
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 400
    assert len(ctx.dispatch.calls) == 0


def test_run_now_dispatch_failure_500(ctx):
    # recorder raises a generic Exception → 500 dispatch failed
    ctx.sr._dispatch_agent_internal = _DispatchRecorder(raise_exc=RuntimeError('boom'))
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': 't', 'continue_session': False},
    ])
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 500


# ── GET /api/schedule/<id>/runs — pagination over a seeded agent_log ──────────

def test_runs_pagination_filters_by_trigger(ctx):
    _seed_schedules(ctx, [{'id': 's1', 'project_id': 'p1', 'task': 't'}])
    # 5 matching rows + 2 noise rows (different trigger / manual)
    rows = [
        {'session_id': f'r{i}', 'trigger_type': 'schedule', 'trigger_id': 's1'}
        for i in range(5)
    ] + [
        {'session_id': 'other', 'trigger_type': 'schedule', 'trigger_id': 's2'},
        {'session_id': 'manual', 'trigger_type': 'manual'},
    ]
    ctx.sr._load_agent_log = lambda pid: list(rows)

    # page 1: limit 2
    resp = ctx.client.get('/api/schedule/s1/runs?limit=2&offset=0')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['total'] == 5            # only s1-triggered rows count
    assert body['limit'] == 2
    assert body['offset'] == 0
    assert len(body['runs']) == 2
    assert [r['session_id'] for r in body['runs']] == ['r0', 'r1']

    # page 3 (offset 4): remainder
    resp2 = ctx.client.get('/api/schedule/s1/runs?limit=2&offset=4')
    b2 = resp2.get_json()
    assert [r['session_id'] for r in b2['runs']] == ['r4']


def test_runs_schedule_not_found_404(ctx):
    _seed_schedules(ctx, [])
    resp = ctx.client.get('/api/schedule/nope/runs')
    assert resp.status_code == 404


def test_runs_bad_params_default(ctx):
    _seed_schedules(ctx, [{'id': 's1', 'project_id': 'p1', 'task': 't'}])
    ctx.sr._load_agent_log = lambda pid: []
    resp = ctx.client.get('/api/schedule/s1/runs?limit=abc&offset=-9')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['limit'] == 50  # malformed → default
    assert body['offset'] == 0  # negative → clamped


# ── master kill-switch (`scheduler_paused`) ───────────────────────────────────
#
# One switch that stops EVERY scheduled dispatch — schedules and stewards, at
# any hour — while leaving each row's own `enabled` flag alone, so unpausing
# restores the prior state. These drive ONE iteration of the real
# _scheduler_loop with the dispatch seam recorded, so a regression that
# silently fires while paused fails here.

class _OneShotStop:
    """Stop-event stand-in: falsy on the first is_set(), truthy after — so the
    loop body runs exactly once and returns instead of blocking 30s."""
    def __init__(self):
        self.checks = 0

    def is_set(self):
        self.checks += 1
        return self.checks > 1

    def wait(self, _timeout):
        return True


def _run_one_loop_iteration(ctx, monkeypatch, paused):
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'scheduler_paused', paused)
    monkeypatch.setattr(ctx.sr, '_scheduler_stop', _OneShotStop())
    ctx.sr._scheduler_loop()
    return json.loads(ctx.sched_path.read_text(encoding='utf-8'))


def _due_cron_row(**over):
    row = {
        'id': 's1', 'project_id': 'p1', 'task': 'do the thing',
        'enabled': True, 'schedule_type': 'cron', 'cron_expr': '*/5 * * * *',
        'next_run': '2020-01-01T00:00:00Z',   # long overdue
    }
    row.update(over)
    return row


def test_loop_dispatches_when_not_paused(ctx, monkeypatch):
    """Control: the same overdue row DOES fire with the switch off."""
    _seed_schedules(ctx, [_due_cron_row()])
    rows = _run_one_loop_iteration(ctx, monkeypatch, paused=False)
    assert len(ctx.dispatch.calls) == 1
    assert ctx.dispatch.calls[0]['project_id'] == 'p1'
    assert rows[0]['next_run'] != '2020-01-01T00:00:00Z'


def test_loop_does_not_dispatch_when_paused(ctx, monkeypatch):
    _seed_schedules(ctx, [_due_cron_row()])
    rows = _run_one_loop_iteration(ctx, monkeypatch, paused=True)
    assert ctx.dispatch.calls == []
    # `enabled` is untouched — unpausing must restore exactly the prior state.
    assert rows[0]['enabled'] is True
    # ...and last_run is NOT stamped: it didn't run.
    assert 'last_run' not in rows[0]


def test_paused_rolls_next_run_forward_no_stampede(ctx, monkeypatch):
    """The overdue slot is consumed while paused, so resuming doesn't fire
    every missed slot at once."""
    _seed_schedules(ctx, [_due_cron_row()])
    rows = _run_one_loop_iteration(ctx, monkeypatch, paused=True)
    from datetime import datetime, timezone
    nxt = datetime.fromisoformat(rows[0]['next_run'].replace('Z', '+00:00'))
    assert nxt > datetime.now(timezone.utc)


def test_paused_interval_row_rolls_by_its_own_interval(ctx, monkeypatch):
    _seed_schedules(ctx, [_due_cron_row(schedule_type='interval',
                                        interval_minutes=20, cron_expr='')])
    rows = _run_one_loop_iteration(ctx, monkeypatch, paused=True)
    from datetime import datetime, timedelta, timezone
    nxt = datetime.fromisoformat(rows[0]['next_run'].replace('Z', '+00:00'))
    delta = nxt - datetime.now(timezone.utc)
    assert timedelta(minutes=19) < delta <= timedelta(minutes=20)


def test_paused_once_row_stays_pending(ctx, monkeypatch):
    """A one-shot the user explicitly set is deferred, not silently dropped."""
    _seed_schedules(ctx, [_due_cron_row(schedule_type='once',
                                        run_at='2020-01-01T00:00:00Z',
                                        cron_expr='')])
    rows = _run_one_loop_iteration(ctx, monkeypatch, paused=True)
    assert ctx.dispatch.calls == []
    assert rows[0]['next_run'] == '2020-01-01T00:00:00Z'
    assert rows[0]['enabled'] is True


def test_paused_blocks_stewards_too(ctx, monkeypatch):
    """Stewards ride the same loop — the switch must cover them."""
    monkeypatch.setattr(ctx.sr, '_steward_cycle_task',
                        lambda pid: ('refreshed task', False))
    _seed_schedules(ctx, [_due_cron_row(steward=True)])
    _run_one_loop_iteration(ctx, monkeypatch, paused=True)
    assert ctx.dispatch.calls == []


def test_run_now_still_works_while_paused(ctx, monkeypatch):
    """Explicit invocation is exactly what the switch is meant to preserve."""
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'scheduler_paused', True)
    _seed_schedules(ctx, [_due_cron_row()])
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 200
    assert len(ctx.dispatch.calls) == 1


def test_scheduler_paused_is_config_editable(ctx):
    """Without this key in _CONFIG_EDITABLE_KEYS the toggle renders and
    silently fails to save (the keep_awake_enabled bug, 2026-07-16)."""
    from mc.blueprints.settings_routes import _CONFIG_EDITABLE_KEYS
    assert 'scheduler_paused' in _CONFIG_EDITABLE_KEYS


# ── auth contract — app-wide gate still covers the moved routes ───────────────

def test_moved_route_behind_lan_gate(ctx):
    """A non-loopback peer with no passcode is 401'd BEFORE the handler runs."""
    resp = ctx.client.get('/api/schedules', environ_overrides=LAN)
    assert resp.status_code == 401


# ── schedule_type coverage: the silent-death class ───────────────────────────
#
# 'weekly' was documented in the API reference that every agent reads on every
# prompt, accepted by POST with a 201, stored with enabled=True — and had no
# branch in _compute_next_run, so it returned None and the row never fired.
# Measured 2026-08-24: the weekly MEMORY HEALTH CHECK had next_run null,
# last_run null and zero runs, ever. Nothing in the UI, the API or the logs
# said so. These tests pin the two halves of the fix: the type computes, and
# an unschedulable type is refused loudly at the door.

def test_every_declared_schedule_type_computes_a_next_run(ctx):
    """The invariant. A type in SCHEDULE_TYPES that _compute_next_run cannot
    handle is an enabled schedule that never runs."""
    from mc.blueprints import scheduler_routes as sr
    from datetime import datetime, timedelta, timezone
    soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    samples = {
        'once': {'run_at': soon},
        'daily': {'time': '03:00'},
        'weekly': {'time': '03:00', 'days': [1]},
        'interval': {'interval_minutes': 30},
        'cron': {'cron_expr': '0 6 * * 1'},
    }
    assert set(samples) == set(sr.SCHEDULE_TYPES), 'a type was added without a sample'
    for stype, extra in samples.items():
        nxt = sr._compute_next_run({'id': 't', 'schedule_type': stype, **extra})
        assert nxt, f'{stype} computed no next_run — it would never fire'


def test_weekly_lands_on_the_requested_weekday(ctx):
    from mc.blueprints import scheduler_routes as sr
    from datetime import datetime
    for day, iso in ((1, 1), (7, 7)):
        nxt = sr._compute_next_run({'id': 't', 'schedule_type': 'weekly',
                                    'time': '03:00', 'days': [day]})
        local = datetime.fromisoformat(nxt.replace('Z', '+00:00')).astimezone()
        assert local.isoweekday() == iso, f'{nxt} is not weekday {iso}'


def test_weekly_accepts_day_names_as_well_as_numbers(ctx):
    """Nothing tells a caller which form to send, and the two rows that existed
    when this shipped disagreed — one held [1], the other ["sunday"]. Dropping
    the string form silently meant the schedule ran on no day at all."""
    from mc.blueprints import scheduler_routes as sr
    assert sr._normalize_days(['sunday']) == {7}
    assert sr._normalize_days(['Mon', 'weds', '5']) == {1, 3, 5}
    assert sr._normalize_days([1, 7]) == {1, 7}
    assert sr._normalize_days(['nonsense', None, 0, 9, True]) == set()


def test_weekly_with_no_usable_day_does_not_silently_become_daily(ctx):
    """Seven times the runs the caller asked for is worse than a wrong day."""
    from mc.blueprints import scheduler_routes as sr
    from datetime import datetime
    nxt = sr._compute_next_run({'id': 't', 'schedule_type': 'weekly',
                                'time': '03:00', 'days': ['garbage']})
    local = datetime.fromisoformat(nxt.replace('Z', '+00:00')).astimezone()
    assert local.isoweekday() == 1


def test_unknown_schedule_type_is_refused_at_create(ctx):
    r = ctx.client.post('/api/schedules', json={
        'project_id': 'p1', 'task': 'x', 'schedule_type': 'fortnightly'})
    assert r.status_code == 400
    assert 'fortnightly' in r.get_json()['error']
    assert not ctx.sched_path.exists() or json.loads(ctx.sched_path.read_text()) == []


def test_unknown_schedule_type_is_refused_at_update(ctx):
    """A working row must not be turned into a dead one by an edit."""
    created = ctx.client.post('/api/schedules', json={
        'project_id': 'p1', 'task': 'x', 'schedule_type': 'daily',
        'time': '03:00'}).get_json()
    r = ctx.client.put(f"/api/schedules/{created['id']}",
                       json={'schedule_type': 'fortnightly'})
    assert r.status_code == 400
    saved = json.loads(ctx.sched_path.read_text(encoding='utf-8'))
    assert saved[0]['schedule_type'] == 'daily'


def test_a_weekly_schedule_survives_the_create_round_trip(ctx):
    r = ctx.client.post('/api/schedules', json={
        'project_id': 'p1', 'task': 'review positions',
        'schedule_type': 'weekly', 'time': '04:00', 'days': ['sunday']})
    assert r.status_code == 201
    body = r.get_json()
    assert body['next_run'], 'created enabled with no next_run — the old silent death'


# ── stale-session purge keys on LAST ACTIVITY, not dispatch time ─────────────
#
# mc_d9c76579 finding f_c82ae579: the purge used to test `started_at` (set once
# at construction, never updated) against a 60-minute cutoff. Any conversation
# that had simply been open for over an hour got deleted the instant its
# status left running/idle for ANY reason — including the ordinary Mode A
# running->completed flip between turns. A user who paused for a few minutes
# mid-conversation on an hour-plus-old chat came back to find it gone. Fixed
# to key on `last_output_time` (refreshed on every turn / status transition),
# so only a session that has been genuinely untouched for an hour is purged.

def _seed_session(ctx, sid, *, status, started_at, last_output_time=None,
                   last_status_change_time=None):
    mgr = ctx.sr.get_manager('p1')
    mgr.add_session(sid)
    sess = {
        'session_id': sid, 'project_id': 'p1', 'status': status,
        'started_at': started_at,
    }
    if last_output_time is not None:
        sess['last_output_time'] = last_output_time
    if last_status_change_time is not None:
        sess['last_status_change_time'] = last_status_change_time
    ctx.sr.agent_sessions[sid] = sess
    return sess


def test_purge_spares_an_hour_old_conversation_that_just_went_idle(ctx, monkeypatch):
    """Regression for f_c82ae579 — FAILS on the parent commit.

    A conversation dispatched 3 hours ago (well past the 60-minute cutoff on
    `started_at`) just finished a turn one second ago and is sitting at
    `completed`, waiting on the user's next message — i.e. it is mid-flow.
    The purge must not touch it.
    """
    import time as _time
    from datetime import datetime, timedelta, timezone
    now = _time.time()
    old_started = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat().replace('+00:00', 'Z')
    sid = 'live-midflow-001'
    _seed_schedules(ctx, [])
    try:
        _seed_session(ctx, sid, status='completed', started_at=old_started,
                      last_output_time=now - 1)
        _run_one_loop_iteration(ctx, monkeypatch, paused=True)
        assert sid in ctx.sr.agent_sessions, (
            'purge deleted a conversation that was active one second ago — '
            'it must key on last activity, not dispatch time')
    finally:
        ctx.sr.agent_sessions.pop(sid, None)
        ctx.sr.get_manager('p1').remove_session(sid)


def test_purge_still_reaps_a_session_truly_untouched_for_an_hour(ctx, monkeypatch):
    """Companion: staleness detection still works when last activity — not
    just dispatch time — is over an hour old."""
    import time as _time
    from datetime import datetime, timedelta, timezone
    stale_started = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat().replace('+00:00', 'Z')
    stale_ts = _time.time() - (61 * 60)
    sid = 'truly-stale-001'
    _seed_schedules(ctx, [])
    try:
        _seed_session(ctx, sid, status='error', started_at=stale_started,
                      last_output_time=stale_ts)
        _run_one_loop_iteration(ctx, monkeypatch, paused=True)
        assert sid not in ctx.sr.agent_sessions, (
            'a session genuinely untouched for over an hour should still be purged')
    finally:
        ctx.sr.agent_sessions.pop(sid, None)
        ctx.sr.get_manager('p1').remove_session(sid)


def test_purge_falls_back_to_started_at_when_no_activity_timestamp_recorded(ctx, monkeypatch):
    """Old-shape session dicts with no last_output_time/last_status_change_time
    keep the pre-fix behavior (started_at) rather than crashing or leaking."""
    from datetime import datetime, timedelta, timezone
    stale_started = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat().replace('+00:00', 'Z')
    sid = 'no-activity-field-001'
    _seed_schedules(ctx, [])
    try:
        _seed_session(ctx, sid, status='error', started_at=stale_started)
        _run_one_loop_iteration(ctx, monkeypatch, paused=True)
        assert sid not in ctx.sr.agent_sessions
    finally:
        ctx.sr.agent_sessions.pop(sid, None)
        ctx.sr.get_manager('p1').remove_session(sid)


# ── MC-871 Q4: a schedule can invoke a workflow instead of a task ────────────
#
# "A schedule record gains ONE optional field, workflow_id, mutually exclusive
# with task." (WORKFLOW_BUILDER_SPEC.md Q4). These pin: the exclusivity
# validation at create/update, fire-time branching into `_wf.start_run`
# instead of `_dispatch_agent_internal`, the deleted-workflow case (never a
# dangling id firing a broken run), and the one-live-run-per-workflow overlap
# rule surfacing as a skip, not a silent drop or a second run.

def test_create_schedule_with_workflow_id(ctx):
    wf = _seed_workflow(ctx)
    resp = ctx.client.post('/api/schedules', json={
        'workflow_id': wf['id'], 'schedule_type': 'daily', 'time': '03:00'})
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['workflow_id'] == wf['id']
    assert body['task'] == ''
    assert body['next_run'], 'a workflow schedule must compute a next_run same as any other'
    assert len(ctx.dispatch.calls) == 0, 'creating the schedule must not fire it'


def test_create_schedule_rejects_both_task_and_workflow_id(ctx):
    wf = _seed_workflow(ctx)
    resp = ctx.client.post('/api/schedules', json={
        'project_id': 'p1', 'task': 'do x', 'workflow_id': wf['id']})
    assert resp.status_code == 400
    assert 'exactly one' in resp.get_json()['error']


def test_create_schedule_rejects_neither_task_nor_workflow_id(ctx):
    resp = ctx.client.post('/api/schedules', json={'project_id': 'p1'})
    assert resp.status_code == 400
    assert 'exactly one' in resp.get_json()['error']


def test_create_schedule_rejects_unknown_workflow_id(ctx):
    resp = ctx.client.post('/api/schedules', json={'workflow_id': 'wf-nope'})
    assert resp.status_code == 400
    assert 'wf-nope' in resp.get_json()['error']
    assert not ctx.sched_path.exists() or json.loads(ctx.sched_path.read_text()) == []


def test_update_schedule_switch_task_to_workflow(ctx):
    wf = _seed_workflow(ctx)
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': 'old', 'enabled': True,
         'schedule_type': 'daily', 'time': '09:00'},
    ])
    resp = ctx.client.put('/api/schedules/s1', json={'task': '', 'workflow_id': wf['id']})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['workflow_id'] == wf['id']
    saved = json.loads(ctx.sched_path.read_text(encoding='utf-8'))
    assert saved[0]['workflow_id'] == wf['id']


def test_update_schedule_leaving_dangling_workflow_id_rejected(ctx):
    """Clearing `task` on a task-only row while `workflow_id` is still absent
    (never set) must not silently produce a row that satisfies neither -- and
    clearing task without supplying a workflow_id is exactly that."""
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': 'old', 'enabled': True,
         'schedule_type': 'daily', 'time': '09:00'},
    ])
    resp = ctx.client.put('/api/schedules/s1', json={'task': ''})
    assert resp.status_code == 400
    saved = json.loads(ctx.sched_path.read_text(encoding='utf-8'))
    assert saved[0]['task'] == 'old', 'the invalid edit must not have been persisted'


def test_update_schedule_rejects_unknown_workflow_id(ctx):
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': '', 'workflow_id': 'wf-real',
         'enabled': True, 'schedule_type': 'daily', 'time': '09:00'},
    ])
    resp = ctx.client.put('/api/schedules/s1', json={'workflow_id': 'wf-ghost'})
    assert resp.status_code == 400


def test_get_schedules_workflow_display_missing_badge(ctx):
    wf = _seed_workflow(ctx)
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': '', 'task': '', 'workflow_id': wf['id'],
         'enabled': True, 'schedule_type': 'daily', 'time': '09:00'},
        {'id': 's2', 'project_id': '', 'task': '', 'workflow_id': 'wf-ghost',
         'enabled': True, 'schedule_type': 'daily', 'time': '09:00'},
    ])
    body = ctx.client.get('/api/schedules').get_json()
    by_id = {s['id']: s for s in body}
    assert by_id['s1']['workflow_display'] == {
        'id': wf['id'], 'name': wf['name'], 'enabled': True, 'missing': False}
    assert by_id['s2']['workflow_display']['missing'] is True


# ── fire-time branching ───────────────────────────────────────────────────────

def _due_workflow_row(workflow_id, **over):
    row = {
        'id': 's1', 'project_id': '', 'task': '', 'workflow_id': workflow_id,
        'enabled': True, 'schedule_type': 'cron', 'cron_expr': '*/5 * * * *',
        'next_run': '2020-01-01T00:00:00Z',   # long overdue
    }
    row.update(over)
    return row


def test_fire_starts_a_workflow_run_not_a_raw_dispatch(ctx, monkeypatch):
    wf = _seed_workflow(ctx)
    _seed_schedules(ctx, [_due_workflow_row(wf['id'])])
    rows = _run_one_loop_iteration(ctx, monkeypatch, paused=False)
    # The workflow's own step dispatched through the shared recorder...
    assert len(ctx.dispatch.calls) == 1
    assert ctx.dispatch.calls[0]['project_id'] == 'p1'
    # ...as a WORKFLOW step, never tagged trigger_type='schedule' the way a
    # plain task dispatch is -- see mc/workflows.py's on_agent_step_complete
    # wiring (trigger_type='workflow').
    assert ctx.dispatch.calls[0].get('trigger_type') == 'workflow'
    runs = ctx.wfm.list_runs(wf['id'])
    assert len(runs) == 1 and runs[0]['status'] == 'running'
    assert rows[0]['next_run'] != '2020-01-01T00:00:00Z'
    assert rows[0]['last_run']


def test_fire_master_switch_still_blocks_workflow_schedules(ctx, monkeypatch):
    wf = _seed_workflow(ctx)
    _seed_schedules(ctx, [_due_workflow_row(wf['id'])])
    _run_one_loop_iteration(ctx, monkeypatch, paused=True)
    assert ctx.dispatch.calls == []
    assert ctx.wfm.list_runs(wf['id']) == []


def test_fire_deleted_workflow_skips_and_rolls_next_run_forward(ctx, monkeypatch):
    """A dangling workflow_id (the workflow was deleted after the schedule was
    created) must never fire a broken run -- it is skipped, logged, and the
    slot still advances so the row doesn't spin on the same instant forever."""
    _seed_schedules(ctx, [_due_workflow_row('wf-does-not-exist')])
    rows = _run_one_loop_iteration(ctx, monkeypatch, paused=False)
    assert ctx.dispatch.calls == []
    assert rows[0]['next_run'] != '2020-01-01T00:00:00Z'
    assert rows[0]['last_run']


def test_fire_overlapping_run_is_skipped_not_queued_or_dropped_twice(ctx, monkeypatch):
    """One live run per workflow (spec Q5). A second fire while the first is
    still in flight must not start a concurrent run -- and must still roll
    the schedule's own next_run forward so the tick isn't silently stuck."""
    wf = _seed_workflow(ctx)
    first = ctx.wfm.start_run(wf['id'], trigger_type='manual')
    assert first['status'] == 'running'
    assert len(ctx.dispatch.calls) == 1

    _seed_schedules(ctx, [_due_workflow_row(wf['id'])])
    rows = _run_one_loop_iteration(ctx, monkeypatch, paused=False)

    assert len(ctx.dispatch.calls) == 1, 'the overlapping fire must not dispatch a second step'
    assert len(ctx.wfm.list_runs(wf['id'])) == 1, 'no second run record was created'
    assert rows[0]['next_run'] != '2020-01-01T00:00:00Z', 'the tick still advances'


# ── POST /api/schedule/<id>/run-now — workflow branch ─────────────────────────

def test_run_now_workflow_schedule_starts_a_run(ctx):
    wf = _seed_workflow(ctx)
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': '', 'task': '', 'workflow_id': wf['id'],
         'enabled': True, 'schedule_type': 'daily', 'time': '09:00'},
    ])
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert body['run']['workflow_id'] == wf['id']
    saved = json.loads(ctx.sched_path.read_text(encoding='utf-8'))
    assert saved[0]['last_run']


def test_run_now_workflow_schedule_busy_409(ctx):
    wf = _seed_workflow(ctx)
    ctx.wfm.start_run(wf['id'], trigger_type='manual')
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': '', 'task': '', 'workflow_id': wf['id'],
         'enabled': True, 'schedule_type': 'daily', 'time': '09:00'},
    ])
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 409
    assert resp.get_json()['busy'] is True


def test_run_now_workflow_schedule_deleted_workflow_404(ctx):
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': '', 'task': '', 'workflow_id': 'wf-ghost',
         'enabled': True, 'schedule_type': 'daily', 'time': '09:00'},
    ])
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 404


# ── GET /api/schedule/<id>/runs — workflow branch ─────────────────────────────

def test_schedule_runs_workflow_branch_returns_run_records(ctx):
    wf = _seed_workflow(ctx)
    run = ctx.wfm.start_run(wf['id'], trigger_type='manual')
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': '', 'task': '', 'workflow_id': wf['id'],
         'enabled': True, 'schedule_type': 'daily', 'time': '09:00'},
    ])
    resp = ctx.client.get('/api/schedule/s1/runs')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['kind'] == 'workflow'
    assert body['total'] == 1
    assert body['runs'][0]['id'] == run['id']


def test_schedule_runs_workflow_branch_never_falls_back_to_agent_log(ctx):
    """A workflow schedule's runs come from the workflow store, never from
    agent_log filtering by trigger_id -- the two ID spaces (schedule ids vs
    run ids) are not interchangeable."""
    wf = _seed_workflow(ctx)
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': '', 'task': '', 'workflow_id': wf['id'],
         'enabled': True, 'schedule_type': 'daily', 'time': '09:00'},
    ])
    ctx.sr._load_agent_log = lambda pid: (_ for _ in ()).throw(
        AssertionError('agent_log must not be consulted for a workflow schedule'))
    resp = ctx.client.get('/api/schedule/s1/runs')
    assert resp.status_code == 200
    assert resp.get_json()['total'] == 0


# ── authority guard: workflow_id on a schedule is not a definition backdoor ──

def test_agent_caller_can_point_a_schedule_at_an_existing_workflow(ctx):
    """Schedules are agent-manageable today (unlike workflow definition CRUD,
    which _refuse_if_agent_caller blocks). Letting an agent pick an EXISTING,
    human-authored workflow_id is no more capability than it already has via
    `character` (pick any existing persona) or `task` (dispatch anything, as
    anyone, anywhere) -- it cannot create, edit or rewire the workflow itself."""
    wf = _seed_workflow(ctx)
    resp = ctx.client.post('/api/schedules', json={'workflow_id': wf['id']})  # no Origin header
    assert resp.status_code == 201


def test_agent_caller_cannot_conjure_a_workflow_via_the_schedule_route(ctx):
    """The workflow must already exist -- a schedule route call can reference
    one, never author one."""
    resp = ctx.client.post('/api/schedules', json={'workflow_id': 'wf-invented'})
    assert resp.status_code == 400
    assert ctx.wfm.list_workflows() == []
