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
    """
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc.blueprints import local_auth as la
    from mc.blueprints import scheduler_routes as sr

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

    server.app.config['TESTING'] = True

    class Ctx:
        pass
    c = Ctx()
    c.client = server.app.test_client()
    c.sr = sr
    c.sched_path = sched_path
    c.dispatch = dispatch
    c.projects = projects
    return c


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
