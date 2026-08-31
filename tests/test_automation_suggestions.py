"""Consent-first automation suggestions (MC-915) — the safety properties.

These tests exist to pin the three things that, if they broke, would turn a
consent queue into silent self-modification:

  1. ACCEPT creates EXACTLY ONE real schedule, through the scheduler's own
     create path (no second job engine), and only from an explicit POST.
  2. DISMISS latches.
  3. A dismissed suggestion is NEVER re-offered after a restart — simulated by
     re-importing the module against the same store file and re-mining the same
     agent log, which is exactly what a fresh process does.

Determinism: patches mc.automation_suggestions.STORE_PATH,
mc.blueprints.scheduler_routes.SCHEDULES_PATH, and the wired agent-log/project
seams on the MODULES (the Phase-0 test-port rule — never server.*). Nothing
real fires; no agent is dispatched (creating a schedule does not run it).
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXPECTED_ROUTES = {
    '/api/automation/suggestions',
    '/api/automation/suggestions/<suggestion_id>/accept',
    '/api/automation/suggestions/<suggestion_id>/dismiss',
}


def _log_entries(task, n, start_day=1, trigger='manual', hour=14, step=3):
    """n manual runs of `task`, `step` days apart — far enough apart to clear
    both the distinct-day gate and the span gate (a habit, not a burst)."""
    return [{'ts': f'2026-08-{start_day + i * step:02d}T{hour:02d}:05:00Z',
             'task': task, 'trigger_type': trigger, 'status': 'completed'}
            for i in range(n)]


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers blueprints + runs wire() on import)
    from mc import automation_suggestions as sg
    from mc.blueprints import automation_routes as ar
    from mc.blueprints import local_auth as la
    from mc.blueprints import scheduler_routes as sr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    store_path = tmp_path / 'automation_suggestions.json'
    sched_path = tmp_path / 'schedules.json'
    monkeypatch.setattr(sg, 'STORE_PATH', store_path)
    monkeypatch.setattr(sr, 'SCHEDULES_PATH', sched_path)

    projects = [{'id': 'p1', 'name': 'Project One',
                 'project_path': str(tmp_path / 'ws')}]
    for mod in (sr, ar):
        monkeypatch.setattr(mod, 'load_projects', lambda: list(projects))
        monkeypatch.setattr(
            mod, 'load_project',
            lambda pid: next((p for p in projects if p['id'] == pid), None))

    log = {'p1': _log_entries('Run the nightly ledger reconciliation and '
                              'report anything unmatched.', 6)}
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: list(log.get(pid, [])))

    server.app.config['TESTING'] = True

    class Ctx:
        pass
    c = Ctx()
    c.client = server.app.test_client()
    c.sg = sg
    c.ar = ar
    c.sr = sr
    c.log = log
    c.store_path = store_path
    c.sched_path = sched_path
    return c


def _schedules(ctx):
    if not ctx.sched_path.exists():
        return []
    return json.loads(ctx.sched_path.read_text(encoding='utf-8'))


def _one_pending(ctx):
    rows = ctx.client.get('/api/automation/suggestions').get_json()
    assert len(rows) == 1, rows
    return rows[0]


# -- registration parity -------------------------------------------------------

def test_blueprint_registered(ctx):
    import server
    assert 'automation_routes' in server.app.blueprints


def test_route_surface_is_exactly_three(ctx):
    """A fourth route is how an auto-accept path would arrive. Pin the surface."""
    import server
    owned = {r.rule for r in server.app.url_map.iter_rules()
             if r.endpoint.startswith('automation_routes.')}
    assert owned == EXPECTED_ROUTES


# -- the miner -----------------------------------------------------------------

def test_miner_is_pure_and_never_writes_a_schedule(ctx):
    """The miner must not be able to reach the schedules store at all."""
    out = ctx.sg.mine_recurring_manual_tasks('p1', ctx.log['p1'], [])
    assert len(out) == 1
    assert not ctx.sched_path.exists()
    assert not ctx.store_path.exists()


def test_listing_does_not_create_a_schedule(ctx):
    """Refreshing the queue is inert. Only an explicit accept creates a job."""
    rows = ctx.client.get('/api/automation/suggestions').get_json()
    assert len(rows) == 1
    assert _schedules(ctx) == []


def test_schedule_triggered_runs_do_not_argue_for_their_own_creation(ctx):
    """A scheduled run re-logs its own task; counting those would let an
    existing job propose itself."""
    entries = _log_entries('A task that only ever ran on a schedule, at length.',
                           8, trigger='schedule')
    assert ctx.sg.mine_recurring_manual_tasks('p1', entries, []) == []


def test_already_scheduled_task_is_not_offered(ctx):
    task = ctx.log['p1'][0]['task']
    out = ctx.sg.mine_recurring_manual_tasks('p1', ctx.log['p1'], [task.upper()])
    assert out == []


def test_burst_without_distinct_days_is_not_offered(ctx):
    """Ten retries in one afternoon is a bad day, not a habit."""
    entries = [{'ts': f'2026-08-01T1{i}:00:00Z', 'trigger_type': 'manual',
                'task': 'Retry the flaky deployment step one more time please.'}
               for i in range(10)]
    assert ctx.sg.mine_recurring_manual_tasks('p1', entries, []) == []


def test_short_span_burst_is_not_offered(ctx):
    """The gate that earned its place on real data: nine runs across three
    consecutive days is a bug being retyped, not a job. It clears the count and
    distinct-day gates and is still noise; only the span gate catches it."""
    task = 'The new chat button does not show the option to set the agent mode.'
    entries = [{'ts': f'2026-08-0{day}T{10 + i}:00:00Z', 'trigger_type': 'manual',
                'task': task}
               for day in (1, 2, 3) for i in range(3)]
    assert len(entries) == 9
    mined = ctx.sg.mine_recurring_manual_tasks('p1', entries, [])
    assert mined == [], mined


def test_session_chatter_is_not_offered(ctx):
    entries = _log_entries('continue where we left off, and keep going until '
                           'the whole thing is finished.', 9)
    assert ctx.sg.mine_recurring_manual_tasks('p1', entries, []) == []


def test_fingerprint_ignores_dispatcher_wrappers(ctx):
    """A habit must not split into several fingerprints (and several latches)
    because the dispatcher prepended an envelope to some of the runs."""
    plain = 'Run the nightly ledger reconciliation and report anything unmatched.'
    wrapped = ('[Scheduled run - 2026-08-04 04:45 Pacific Daylight Time] '
               + plain)
    assert ctx.sg.fingerprint('p1', plain) == ctx.sg.fingerprint('p1', wrapped)


# -- accept: exactly one real schedule, via the existing create path -----------

def test_accept_creates_exactly_one_real_schedule(ctx):
    s = _one_pending(ctx)
    resp = ctx.client.post(f"/api/automation/suggestions/{s['id']}/accept")
    assert resp.status_code == 201, resp.get_json()

    scheds = _schedules(ctx)
    assert len(scheds) == 1
    created = scheds[0]
    assert created['project_id'] == 'p1'
    assert created['task'] == s['spec']['task']
    assert created['schedule_type'] == 'daily'
    # Fields only the scheduler's own create path produces — proof it went
    # through create_schedule_from_spec rather than a second writer.
    assert created['id'] and created['created_at'] and created['next_run']
    assert created['enabled'] is True
    assert resp.get_json()['schedule']['id'] == created['id']


def test_accept_is_idempotent_and_never_creates_a_second_schedule(ctx):
    s = _one_pending(ctx)
    first = ctx.client.post(f"/api/automation/suggestions/{s['id']}/accept")
    assert first.status_code == 201
    second = ctx.client.post(f"/api/automation/suggestions/{s['id']}/accept")
    assert second.status_code == 409
    assert second.get_json()['decision'] == 'accepted'
    assert len(_schedules(ctx)) == 1


def test_accept_honours_a_cadence_override_but_not_the_project_or_task(ctx):
    s = _one_pending(ctx)
    resp = ctx.client.post(
        f"/api/automation/suggestions/{s['id']}/accept",
        json={'schedule_type': 'interval', 'interval_minutes': 30,
              'project_id': 'somewhere-else', 'task': 'rm -rf /'})
    assert resp.status_code == 201
    created = _schedules(ctx)[0]
    assert created['schedule_type'] == 'interval'
    assert created['interval_minutes'] == 30
    # The two fields the latch is computed over are NOT overridable.
    assert created['project_id'] == 'p1'
    assert created['task'] == s['spec']['task']


def test_failed_create_leaves_the_suggestion_pending(ctx):
    """A rejected spec must not consume the human's consent."""
    s = _one_pending(ctx)
    resp = ctx.client.post(f"/api/automation/suggestions/{s['id']}/accept",
                           json={'schedule_type': 'fortnightly'})
    assert resp.status_code == 400
    assert _schedules(ctx) == []
    assert ctx.sg.get_decision(s['id']) is None
    assert len(ctx.client.get('/api/automation/suggestions').get_json()) == 1


def test_accepted_suggestion_is_not_re_offered(ctx):
    s = _one_pending(ctx)
    assert ctx.client.post(
        f"/api/automation/suggestions/{s['id']}/accept").status_code == 201
    assert ctx.client.get('/api/automation/suggestions').get_json() == []


# -- dismiss: the latch --------------------------------------------------------

def test_dismiss_latches(ctx):
    s = _one_pending(ctx)
    resp = ctx.client.post(f"/api/automation/suggestions/{s['id']}/dismiss")
    assert resp.status_code == 200 and resp.get_json()['ok'] is True

    assert ctx.client.get('/api/automation/suggestions').get_json() == []
    assert _schedules(ctx) == []
    assert ctx.sg.get_decision(s['id'])['decision'] == 'dismissed'


def test_dismiss_then_accept_is_refused(ctx):
    """"No" outranks a later stray accept — the decision record is the gate."""
    s = _one_pending(ctx)
    ctx.client.post(f"/api/automation/suggestions/{s['id']}/dismiss")
    resp = ctx.client.post(f"/api/automation/suggestions/{s['id']}/accept")
    assert resp.status_code == 409
    assert _schedules(ctx) == []


def test_dismissed_suggestion_survives_a_restart(ctx):
    """THE durability test. Re-import the module and re-mine the SAME agent log
    against the SAME store file — exactly what a fresh server process does. The
    dismissed pattern must not come back."""
    s = _one_pending(ctx)
    ctx.client.post(f"/api/automation/suggestions/{s['id']}/dismiss")

    # Restart: a brand-new module object, its in-memory state discarded.
    fresh = importlib.reload(ctx.sg)
    fresh.STORE_PATH = ctx.store_path
    try:
        # The candidate is still MINED from the log (the evidence has not gone
        # away) but must be refused entry to the store at GENERATION time.
        mined = fresh.mine_recurring_manual_tasks('p1', ctx.log['p1'], [])
        assert len(mined) == 1 and mined[0]['id'] == s['id']

        assert fresh.is_decided(s['id']) is True
        added = fresh.refresh_project('p1', ctx.log['p1'], [])
        assert added == 0
        assert fresh.list_pending('p1') == []
    finally:
        # Leave the shared module object consistent for the rest of the suite:
        # rebind the reloaded module's name back onto the route module and
        # restore its wired path.
        importlib.reload(ctx.sg)
        ctx.sg.STORE_PATH = ctx.store_path
        ctx.ar._sugg = ctx.sg


def test_latch_is_content_keyed_not_row_keyed(ctx):
    """A dismissal keyed by a row id would expire the moment the miner
    regenerated the row under a new id. Same ask -> same fingerprint."""
    s = _one_pending(ctx)
    ctx.client.post(f"/api/automation/suggestions/{s['id']}/dismiss")
    remined = ctx.sg.mine_recurring_manual_tasks('p1', ctx.log['p1'], [])
    assert remined[0]['id'] == s['id']
    assert ctx.sg.is_decided(remined[0]['id'])


def test_store_lives_outside_data_projects(ctx):
    """The LOAD-BEARING DATA_DIR rule: a stray *.json under data/projects/
    becomes a malformed 'project' and 500s both restart endpoints."""
    import server
    configured = Path(server.AUTOMATION_SUGGESTIONS_PATH)
    assert configured.parent.resolve() != Path(server.DATA_DIR).resolve()
    assert configured.name == 'automation_suggestions.json'


def test_dismiss_unknown_id_is_404(ctx):
    assert ctx.client.post(
        '/api/automation/suggestions/deadbeefdeadbeef/dismiss').status_code == 404
