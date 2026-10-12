"""Scheduler run condition: spend leftover weekly allowance before reset.

Pure verdicts (mc/schedule_conditions.evaluate), API validation, and the real
scheduler loop with usage patched at the one seam it reads
(`engine_pace_dispatch.provider_usage`). No network, no real dispatch.
"""
from datetime import datetime, timedelta, timezone

import pytest

from mc import schedule_conditions as sc
from tests.test_scheduler_routes import (  # noqa: F401 — reuse its fixture
    ctx, _due_cron_row, _run_one_loop_iteration, _seed_schedules)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
COND = {'type': sc.SPEND_LEFTOVER, 'lead_hours': 12, 'ceiling_percent': 80, 'engine': ''}


def _usage(used, resets_in_hours, provider='claude'):
    resets = (NOW + timedelta(hours=resets_in_hours)).isoformat().replace('+00:00', 'Z')
    return {provider: {'utilization': used, 'resets_at': resets}}


# ── pure verdicts ───────────────────────────────────────────────────────────

def test_fires_inside_window_under_ceiling():
    d = sc.evaluate(COND, 'claude', _usage(40, 6), NOW)
    assert d.fire is True
    assert '6.0h' in d.reason and '40%' in d.reason


def test_skips_outside_window():
    d = sc.evaluate(COND, 'claude', _usage(10, 30), NOW)
    assert d.fire is False
    assert 'outside the 12h lead time' in d.reason and '30.0h' in d.reason


def test_skips_over_ceiling():
    d = sc.evaluate(COND, 'claude', _usage(91, 3), NOW)
    assert d.fire is False
    assert '91%' in d.reason and '80%' in d.reason


def test_boundaries_lead_inclusive_ceiling_exclusive():
    assert sc.evaluate(COND, 'claude', _usage(40, 12), NOW).fire is True     # exactly 12h
    assert sc.evaluate(COND, 'claude', _usage(80, 3), NOW).fire is False     # exactly 80%
    assert sc.evaluate(COND, 'claude', _usage(79.9, 3), NOW).fire is True


def test_custom_lead_and_ceiling_are_honoured():
    cond = dict(COND, lead_hours=48, ceiling_percent=50)
    assert sc.evaluate(cond, 'claude', _usage(40, 40), NOW).fire is True
    assert sc.evaluate(cond, 'claude', _usage(55, 40), NOW).fire is False


@pytest.mark.parametrize('usage', [
    {},                                                    # no reading at all
    {'claude': None},
    {'claude': {'utilization': 40}},                       # no resets_at
    {'claude': {'resets_at': '2026-10-12T15:00:00Z'}},     # no utilization
    {'claude': {'utilization': 'lots', 'resets_at': '2026-10-12T15:00:00Z'}},
    {'claude': {'utilization': 140, 'resets_at': '2026-10-12T15:00:00Z'}},
    {'claude': {'utilization': 40, 'resets_at': 'garbage'}},
    _usage(40, -1),                                        # window already reset
    _usage(40, 24 * 8),                                    # reset further out than a week
    _usage(40, 3, provider='codex'),                       # only the OTHER engine reads
])
def test_skips_on_missing_or_stale_data(usage):
    d = sc.evaluate(COND, 'claude', usage, NOW)
    assert d.fire is False
    assert 'not spending blind' in d.reason


# ── resolution + never raises ───────────────────────────────────────────────

def _check(sched, usage, *, project=None, character=None, now=NOW, boom=False):
    def usage_for(provider):
        if boom:
            raise RuntimeError('reader down')
        return usage
    return sc.check(sched, config={}, load_project=lambda pid: project or {'id': pid},
                    read_character=lambda *a, **k: character, usage_for=usage_for, now=now)


def test_no_condition_means_no_verdict():
    assert _check({'id': 's', 'project_id': 'p'}, {}) is None
    assert _check({'id': 's', 'project_id': 'p', 'run_condition': None}, {}) is None


def test_engine_follows_the_personas_provider():
    sched = {'id': 's', 'project_id': 'p', 'character': 'global:x', 'run_condition': COND}
    codex_persona = {'engine': {'provider': 'codex'}}
    d = _check(sched, _usage(40, 6, 'codex'), character=codex_persona)
    assert d.fire is True and d.reason.startswith('codex')
    # Same persona, but only claude has a reading: skip, never borrow the other.
    d = _check(sched, _usage(40, 6, 'claude'), character=codex_persona)
    assert d.fire is False


def test_explicit_engine_wins_over_the_persona():
    sched = {'id': 's', 'project_id': 'p', 'run_condition': dict(COND, engine='codex')}
    assert _check(sched, _usage(40, 6, 'codex')).fire is True


def test_reader_failure_is_a_skip_not_a_crash():
    sched = {'id': 's', 'project_id': 'p', 'run_condition': COND}
    d = _check(sched, {}, boom=True)
    assert d.fire is False and 'not spending blind' in d.reason


def test_engine_without_a_weekly_window_skips():
    sched = {'id': 's', 'project_id': 'p', 'run_condition': COND}
    d = _check(sched, {}, project={'id': 'p', 'provider': 'gemini'})
    assert d.fire is False and 'gemini' in d.reason


# ── API validation ──────────────────────────────────────────────────────────

def test_normalize_defaults_and_clearing():
    cond, err = sc.normalize_condition({'type': sc.SPEND_LEFTOVER}, schedule_type='daily')
    assert err is None
    assert cond == {'type': sc.SPEND_LEFTOVER, 'lead_hours': 12.0,
                    'ceiling_percent': 80.0, 'engine': ''}
    for empty in (None, {}, False):
        assert sc.normalize_condition(empty, schedule_type='daily') == (None, None)


@pytest.mark.parametrize('raw', [
    'yes', {'type': 'nope'}, {'type': sc.SPEND_LEFTOVER, 'lead_hours': 0},
    {'type': sc.SPEND_LEFTOVER, 'lead_hours': 200}, {'type': sc.SPEND_LEFTOVER, 'lead_hours': True},
    {'type': sc.SPEND_LEFTOVER, 'ceiling_percent': 101},
    {'type': sc.SPEND_LEFTOVER, 'ceiling_percent': 'x'},
    {'type': sc.SPEND_LEFTOVER, 'ceiling_percent': float('nan')},
    {'type': sc.SPEND_LEFTOVER, 'engine': 'gemini'},
])
def test_normalize_rejects_bad_input(raw):
    cond, err = sc.normalize_condition(raw, schedule_type='daily')
    assert cond is None and err


def test_normalize_rejects_once_and_workflow_targets():
    raw = {'type': sc.SPEND_LEFTOVER}
    assert sc.normalize_condition(raw, schedule_type='once')[1]
    assert sc.normalize_condition(raw, schedule_type='daily', workflow_target=True)[1]


def test_create_update_clear_round_trip(ctx):
    body = {'project_id': 'p1', 'task': 'burn', 'schedule_type': 'interval',
            'interval_minutes': 60,
            'run_condition': {'type': sc.SPEND_LEFTOVER, 'lead_hours': 6}}
    r = ctx.client.post('/api/schedules', json=body)
    assert r.status_code == 201
    sid = r.get_json()['id']
    assert r.get_json()['run_condition'] == {
        'type': sc.SPEND_LEFTOVER, 'lead_hours': 6.0, 'ceiling_percent': 80.0, 'engine': ''}
    # A PUT that does not mention the condition keeps it.
    r = ctx.client.put(f'/api/schedules/{sid}', json={'enabled': False})
    assert r.get_json()['run_condition']['lead_hours'] == 6.0
    # An empty one clears it.
    r = ctx.client.put(f'/api/schedules/{sid}', json={'run_condition': None})
    assert r.get_json()['run_condition'] is None
    # Switching a conditioned row to `once` is refused.
    ctx.client.put(f'/api/schedules/{sid}', json={'run_condition': {'type': sc.SPEND_LEFTOVER}})
    r = ctx.client.put(f'/api/schedules/{sid}', json={'schedule_type': 'once',
                                                      'run_at': '2099-01-01T00:00:00Z'})
    assert r.status_code == 400


def test_create_refuses_a_bad_condition(ctx):
    r = ctx.client.post('/api/schedules', json={
        'project_id': 'p1', 'task': 't', 'schedule_type': 'daily',
        'run_condition': {'type': sc.SPEND_LEFTOVER, 'ceiling_percent': 500}})
    assert r.status_code == 400 and 'ceiling_percent' in r.get_json()['error']


# ── the real loop ───────────────────────────────────────────────────────────

def _conditioned_row(**over):
    return _due_cron_row(run_condition=dict(COND, engine='claude'), **over)


def _patch_usage(monkeypatch, usage):
    from mc import engine_pace_dispatch
    monkeypatch.setattr(engine_pace_dispatch, 'provider_usage', lambda provider: usage)


def _real_now_usage(used, resets_in_hours):
    resets = datetime.now(timezone.utc) + timedelta(hours=resets_in_hours)
    return {'claude': {'utilization': used, 'resets_at': resets.isoformat().replace('+00:00', 'Z')}}


def test_loop_fires_inside_the_window_under_the_ceiling(ctx, monkeypatch):
    _patch_usage(monkeypatch, _real_now_usage(40, 6))
    _seed_schedules(ctx, [_conditioned_row()])
    rows = _run_one_loop_iteration(ctx, monkeypatch, paused=False)
    assert len(ctx.dispatch.calls) == 1
    assert rows[0]['last_run']


@pytest.mark.parametrize('usage,needle', [
    (_real_now_usage(10, 40), 'outside the 12h lead time'),
    (_real_now_usage(95, 3), 'ceiling'),
    ({}, 'not spending blind'),
    (_real_now_usage(40, -2), 'not spending blind'),
])
def test_loop_skips_and_logs_the_reason(ctx, monkeypatch, usage, needle):
    _patch_usage(monkeypatch, usage)
    _seed_schedules(ctx, [_conditioned_row()])
    rows = _run_one_loop_iteration(ctx, monkeypatch, paused=False)
    assert ctx.dispatch.calls == []
    assert 'last_run' not in rows[0]               # nothing ran
    nxt = datetime.fromisoformat(rows[0]['next_run'].replace('Z', '+00:00'))
    assert nxt > datetime.now(timezone.utc)        # slot consumed, no 30s respin
    r = ctx.client.get('/api/schedule/s1/runs')
    skips = r.get_json()['skips']
    assert len(skips) == 1 and needle in skips[0]['reason'] and skips[0]['kind'] == 'condition'


def test_skipped_interval_row_rolls_by_its_own_interval(ctx, monkeypatch):
    _patch_usage(monkeypatch, {})
    _seed_schedules(ctx, [_conditioned_row(schedule_type='interval',
                                           interval_minutes=20, cron_expr='')])
    rows = _run_one_loop_iteration(ctx, monkeypatch, paused=False)
    delta = datetime.fromisoformat(rows[0]['next_run'].replace('Z', '+00:00')) - datetime.now(timezone.utc)
    assert timedelta(minutes=19) < delta <= timedelta(minutes=20)


def test_unconditioned_schedule_is_unaffected(ctx, monkeypatch):
    _patch_usage(monkeypatch, {})                  # would skip a conditioned row
    _seed_schedules(ctx, [_due_cron_row()])
    _run_one_loop_iteration(ctx, monkeypatch, paused=False)
    assert len(ctx.dispatch.calls) == 1


def test_run_now_ignores_the_condition(ctx, monkeypatch):
    """Run Now is a human's explicit extra dispatch, not a cadence tick."""
    _patch_usage(monkeypatch, {})
    _seed_schedules(ctx, [_conditioned_row()])
    r = ctx.client.post('/api/schedule/s1/run-now')
    assert r.status_code == 200 and len(ctx.dispatch.calls) == 1


def test_deleting_a_schedule_drops_its_skip_history(ctx, monkeypatch):
    _patch_usage(monkeypatch, {})
    _seed_schedules(ctx, [_conditioned_row()])
    _run_one_loop_iteration(ctx, monkeypatch, paused=False)
    assert ctx.client.delete('/api/schedules/s1').status_code == 200
    from mc import schedule_skip_log
    assert schedule_skip_log.list_for(ctx.sched_path, 's1') == []
