"""MC-1179 pass 09 / OS1: a failed dispatch of an `interval` schedule must not
be retried on every 30 s tick.

`_compute_next_run` for an interval row computes `last_run + interval`; a
failed dispatch leaves `last_run` alone (correct: last_run means "last
successful dispatch"), so that value is in the past and the function returns
`now + 5 s`. The row was therefore due again on the next tick, for as long as
the cause lasted. The paused path, the condition-skip path and the steward-role
path already roll an interval row to `now + interval` explicitly; the ordinary
failure path now does too.
"""
import json
from datetime import datetime, timedelta, timezone

from test_scheduler_routes import (  # noqa: F401  (ctx is a pytest fixture)
    ctx, _DispatchRecorder, _OneShotStop, _seed_schedules)  # pyright: ignore


def _due_interval_row(**over):
    row = {
        'id': 's1', 'project_id': 'p1', 'task': 'do the thing',
        'enabled': True, 'schedule_type': 'interval', 'interval_minutes': 60,
        'next_run': '2020-01-01T00:00:00Z',   # long overdue
    }
    row.update(over)
    return row


def _tick(ctx, monkeypatch):
    from mc import state
    monkeypatch.setitem(state.CONFIG, 'scheduler_paused', False)
    monkeypatch.setattr(ctx.sr, '_scheduler_stop', _OneShotStop())
    ctx.sr._scheduler_loop()
    return json.loads(ctx.sched_path.read_text(encoding='utf-8'))


def _parse(z):
    return datetime.fromisoformat(z.replace('Z', '+00:00'))


def test_failed_interval_fire_rolls_next_run_a_full_interval(ctx, monkeypatch):
    ctx.sr._dispatch_agent_internal = _DispatchRecorder(raise_exc=RuntimeError('boom'))
    _seed_schedules(ctx, [_due_interval_row(last_run='2019-12-31T23:00:00Z')])
    before = datetime.now(timezone.utc)
    rows = _tick(ctx, monkeypatch)
    nxt = _parse(rows[0]['next_run'])
    assert nxt >= before + timedelta(minutes=59), (
        f"next_run {rows[0]['next_run']} is not a full interval away")
    # last_run still means "last successful dispatch": untouched on failure.
    assert rows[0]['last_run'] == '2019-12-31T23:00:00Z'
    assert rows[0]['last_error'] == 'boom'


def test_failed_interval_fire_with_no_last_run_rolls_a_full_interval(ctx, monkeypatch):
    ctx.sr._dispatch_agent_internal = _DispatchRecorder(raise_exc=RuntimeError('boom'))
    _seed_schedules(ctx, [_due_interval_row()])
    before = datetime.now(timezone.utc)
    rows = _tick(ctx, monkeypatch)
    assert _parse(rows[0]['next_run']) >= before + timedelta(minutes=59)
    assert 'last_run' not in rows[0]


def _freeze_clock(monkeypatch, ctx, start):
    """Drive scheduler_routes.datetime.now() from a mutable clock, so several
    30 s ticks can be run back to back with real elapsed-time semantics."""
    clock = {'t': start}

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            t = clock['t']
            return t.astimezone(tz) if tz else t.astimezone().replace(tzinfo=None)

    monkeypatch.setattr(ctx.sr, 'datetime', _FakeDT)
    return clock


def test_failing_interval_row_is_attempted_once_per_interval_not_per_tick(ctx, monkeypatch):
    rec = _DispatchRecorder(raise_exc=RuntimeError('boom'))
    ctx.sr._dispatch_agent_internal = rec
    t0 = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    clock = _freeze_clock(monkeypatch, ctx, t0)
    _seed_schedules(ctx, [_due_interval_row(next_run='2030-01-01T11:59:00Z')])
    for _ in range(10):             # ten 30 s ticks = 4.5 minutes of wall time
        _tick(ctx, monkeypatch)
        clock['t'] += timedelta(seconds=30)
    assert len(rec.calls) == 1, f"attempted {len(rec.calls)}x in 4.5 min of a 60 min interval"
    # ...and it is attempted again once the interval has elapsed.
    clock['t'] = t0 + timedelta(minutes=61)
    _tick(ctx, monkeypatch)
    assert len(rec.calls) == 2


def test_failed_workflow_interval_fire_rolls_a_full_interval(ctx, monkeypatch):
    """The workflow branch shares the finalization block."""
    monkeypatch.setattr(ctx.wfm, 'start_run',
                        lambda *a, **k: (_ for _ in ()).throw(Exception('wf boom')))
    row = _due_interval_row(workflow_id='wf-x')
    row.pop('task')
    _seed_schedules(ctx, [row])
    before = datetime.now(timezone.utc)
    rows = _tick(ctx, monkeypatch)
    assert _parse(rows[0]['next_run']) >= before + timedelta(minutes=59)


def test_successful_interval_fire_still_stamps_last_run_and_rolls(ctx, monkeypatch):
    """Control: success path is unchanged."""
    _seed_schedules(ctx, [_due_interval_row()])
    rows = _tick(ctx, monkeypatch)
    assert rows[0]['last_run']
    assert 'last_error' not in rows[0]
    assert _parse(rows[0]['next_run']) > datetime.now(timezone.utc) + timedelta(minutes=59)


def test_failed_cron_fire_still_uses_compute_next_run(ctx, monkeypatch):
    """Control: only interval rows take the explicit roll."""
    ctx.sr._dispatch_agent_internal = _DispatchRecorder(raise_exc=RuntimeError('boom'))
    _seed_schedules(ctx, [{
        'id': 's1', 'project_id': 'p1', 'task': 't', 'enabled': True,
        'schedule_type': 'cron', 'cron_expr': '*/5 * * * *',
        'next_run': '2020-01-01T00:00:00Z'}])
    rows = _tick(ctx, monkeypatch)
    assert rows[0]['next_run'] != '2020-01-01T00:00:00Z'
