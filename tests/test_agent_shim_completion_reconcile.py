"""MC-1150 P4: regression tests for Fenn's THIRD-review BLOCK on 6ca14879
(docs/_journal/ebbb15a7-fenn-p4-rereview.md, "THIRD review"). Every test marked
FAILS-BEFORE fails on 6ca14879.

A workflow step whose child has an outstanding durable completion (a pending stamp
on its shim checkpoint) must not be ended by any reconciler reading the agent-log
row, and a replay must not clear a success that the run refused.

  A  the scheduler is already running when boot replays: its reconcile tick marked
     the run interrupted from boot's `interrupted` row, then replay acknowledged
     the success it could not apply.
  B  a transient `_write_run` failure retained the record, boot adoption then
     interrupted the run, and the next boot cleared the record unapplied.

Same real-everything setup as tests/test_agent_shim_completion_replay.py.

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_shim_completion_reconcile.py -q
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mc import agent_shim_adopt as A
from mc import agent_shim_completion as comp
from mc import agent_spawn
from mc import state as mc_state

from tests.test_agent_shim_completion_replay import (  # noqa: F401
    PID, _clean_registry, _two_step_run, env,
)
from tests.test_workflows import wf  # noqa: F401


@pytest.fixture
def owed(env, wf, monkeypatch):
    """A two-step run whose first step finished successfully and whose completion
    is owed (record on disk, no wake yet), with boot's `interrupted` log row."""
    monkeypatch.setattr(agent_spawn, '_root_override', env.root)
    run, sid = _two_step_run(wf)
    env.s.update(session_id=sid, mode='B', status='idle', provider='claude',
                 _notify_session='', _notify_workflow={'run_id': run['id'], 'step': 'first'})
    comp.begin(env.s, 'turn')
    wf.agent_logs[PID] = [{'session_id': sid, 'status': 'interrupted',
                           'summary': 'server restarted'}]
    # In production a shim dir is named for its session id, and the reconcilers look
    # it up that way; the fixture's dir was made before the id existed.
    new = env.root / sid
    env.sdir.rename(new)
    env.ck = lambda: A.read_checkpoint(new)
    return run, sid


def _state(wf_, run):
    r = wf_.m.get_run(run['id'])
    return r['status'], r['steps']['first']['status']


def test_scheduler_reconcile_before_replay_leaves_the_owed_step_alone(env, wf, owed):
    """FAILS-BEFORE. Path A: the scheduler's first tick runs between boot's log
    sweep and the completion replay."""
    run, sid = owed
    assert wf.m.reconcile_running_steps() == {'advanced': 0, 'stalled': 0}
    assert _state(wf, run) == ('running', 'running')

    rep = comp.replay_pending(env.root)
    assert rep.replayed == [sid] and not rep.retained
    assert comp.KEY not in env.ck()['session']
    assert _state(wf, run) == ('running', 'completed')
    wf.m.adopt_on_startup()                              # boot continues
    assert wf.m.get_run(run['id'])['steps']['second']['status'] == 'running'
    assert len(wf.dispatch.calls) == 2


def test_failed_replay_then_adoption_keeps_the_owed_success(env, wf, owed, monkeypatch):
    """FAILS-BEFORE. Path B: replay fails once, boot continues into adoption, the
    next boot replays again."""
    run, sid = owed
    real_write = wf.m._write_run
    calls = []

    def fail_once(obj):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError('injected transient workflow replace failure')
        return real_write(obj)

    monkeypatch.setattr(wf.m, '_write_run', fail_once)
    first = comp.replay_pending(env.root)
    assert first.retained and not first.replayed
    assert comp.KEY in env.ck()['session']

    wf.m.adopt_on_startup()                              # same boot, after replay returned
    assert _state(wf, run) == ('running', 'running')     # not interrupted
    assert len(wf.dispatch.calls) == 1

    second = comp.replay_pending(env.root)               # next boot, disk is fine
    assert second.replayed == [sid] and not second.retained
    assert _state(wf, run) == ('running', 'completed')
    wf.m.adopt_on_startup()
    assert wf.m.get_run(run['id'])['steps']['second']['status'] == 'running'
    assert len(wf.dispatch.calls) == 2


def test_periodic_stall_reconcile_skips_an_owed_step_however_old(env, wf, owed):
    """FAILS-BEFORE for the stalled branch: no log row at all, long past the grace,
    but the completion is owed -- the step is not 'never arrived'."""
    run, sid = owed
    wf.agent_logs[PID] = []
    later = datetime.now(timezone.utc) + timedelta(days=3)
    assert wf.m.reconcile_running_steps(now=later) == {'advanced': 0, 'stalled': 0}
    assert _state(wf, run) == ('running', 'running')
    assert wf.m._interrupt_unconfirmed(run['id'], 'first', 'raced') is False
    assert _state(wf, run) == ('running', 'running')


def test_a_live_session_between_begin_and_ack_is_owed_too(wf):
    sid = 'live-owed'
    mc_state.agent_sessions[sid] = {'session_id': sid, comp.KEY: {'id': 'x', 'kind': 'turn'}}
    try:
        assert wf.m._completion_owed(sid) is True
        mc_state.agent_sessions[sid].pop(comp.KEY)
        assert wf.m._completion_owed(sid) is False
    finally:
        mc_state.agent_sessions.pop(sid, None)


def test_without_an_owed_completion_the_reconciler_still_interrupts(env, wf, monkeypatch):
    """Guard against over-skipping: a step with no record behaves as before."""
    monkeypatch.setattr(agent_spawn, '_root_override', env.root)
    run, sid = _two_step_run(wf)
    wf.agent_logs[PID] = [{'session_id': sid, 'status': 'interrupted', 'summary': ''}]
    wf.m.reconcile_running_steps()
    assert wf.m.get_run(run['id'])['status'] == 'interrupted'


def test_human_cancel_still_wins_and_the_record_is_cleared(env, wf, owed):
    run, sid = owed
    wf.m.cancel_run(run['id'])
    rep = comp.replay_pending(env.root)
    assert rep.replayed == [sid] and not rep.retained
    assert comp.KEY not in env.ck()['session']
    assert wf.m.get_run(run['id'])['status'] == 'cancelled'      # not resurrected
    assert len(wf.dispatch.calls) == 1


def test_a_success_refused_by_an_already_interrupted_run_is_not_acknowledged(env, wf, owed):
    """FAILS-BEFORE. A run damaged before this fix (interrupted while the success
    was owed): replay must keep the record, not clear it unapplied."""
    run, sid = owed
    # interrupt WITHOUT the new guard, the way 6ca14879's reconciler did
    r = wf.m._read_run(run['id'])
    r['status'] = 'interrupted'
    r['steps']['first']['status'] = 'failed'
    wf.m._write_run(r)

    assert wf.m.on_agent_step_complete(run['id'], 'first', PID, sid, 'idle', 'ok',
                                       advance=False) == 'refused'
    rep = comp.replay_pending(env.root)
    assert rep.replayed == [] and sid in rep.retained
    assert comp.KEY in env.ck()['session']
    assert wf.m.get_run(run['id'])['status'] == 'interrupted'


def test_a_non_success_completion_for_an_interrupted_run_stays_ignored(wf):
    run, sid = _two_step_run(wf)
    r = wf.m._read_run(run['id'])
    r['status'] = 'interrupted'
    wf.m._write_run(r)
    for status in ('error', 'stopped', 'interrupted'):
        assert wf.m.on_agent_step_complete(run['id'], 'first', PID, sid, status, '',
                                           advance=False) == 'ignored'
    assert wf.m.on_agent_step_complete(run['id'], 'first', PID, 'other-sess', 'idle', 'x',
                                       advance=False) == 'ignored'
