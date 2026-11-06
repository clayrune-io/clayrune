"""MC-1150 P4: the scheduler tick retries an owed completion (Dave's decision 1 on
73c31e02, docs/_journal/ebbb15a7-agent-survive-restart.md).

On 73c31e02 a boot replay that failed once (a transient `_write_run` error) left
the run `running` until the next boot: every reconciler skips an owed step and
nothing retried the replay. `mc/agent_shim_completion_retry.py` does, from the
same 30s tick that runs `reconcile_running_steps`.

Same real-everything setup as tests/test_agent_shim_completion_reconcile.py (real
`agent_routes` runners, real `mc.workflows` over a file-backed run store; only the
agent dispatch is a recorder).

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_shim_completion_retry.py -q
"""
from __future__ import annotations

import pytest

from mc import agent_shim_adopt as A
from mc import agent_shim_completion as comp
from mc import agent_shim_completion_retry as retry
from mc import state as mc_state

from tests.test_agent_shim_completion_reconcile import _state, owed  # noqa: F401
from tests.test_agent_shim_completion_replay import PID, _clean_registry, env  # noqa: F401
from tests.test_workflows import wf  # noqa: F401


def _failing_write(wf_, monkeypatch, *, times):
    """_write_run raises for the first `times` calls, then works."""
    real, calls = wf_.m._write_run, []

    def write(obj):
        calls.append(1)
        if len(calls) <= times:
            raise PermissionError('injected transient workflow replace failure')
        return real(obj)

    monkeypatch.setattr(wf_.m, '_write_run', write)
    return real


def _boot_with_failed_replay(env, wf, monkeypatch):
    """Boot order: replay (fails once) -> workflow adoption. Returns restore()."""
    real = _failing_write(wf, monkeypatch, times=1)
    first = comp.replay_pending(env.root)
    assert first.retained and not first.replayed
    wf.m.adopt_on_startup()
    monkeypatch.setattr(wf.m, '_write_run', real)


def test_next_tick_retries_a_failed_boot_replay_and_advances_the_run_once(
        env, wf, owed, monkeypatch):
    """FAILS-BEFORE (73c31e02: no retry, the run stays on the first step)."""
    run, sid = owed
    _boot_with_failed_replay(env, wf, monkeypatch)
    assert comp.KEY in env.ck()['session']
    assert _state(wf, run) == ('running', 'running')
    assert len(wf.dispatch.calls) == 1

    wf.m.reconcile_running_steps()                     # what 73c31e02's tick did: nothing
    assert _state(wf, run) == ('running', 'running')

    got = retry.retry_owed_workflow_steps(env.root)    # the new first half of the tick
    assert got == {'retried': 1, 'replayed': 1, 'advanced': 1}
    assert comp.KEY not in env.ck()['session']
    after = wf.m.get_run(run['id'])
    assert after['steps']['first']['status'] == 'completed'
    assert after['steps']['second']['status'] == 'running'
    assert len(wf.dispatch.calls) == 2

    # later ticks and a repeated adoption pass dispatch nothing more
    assert retry.retry_owed_workflow_steps(env.root)['retried'] == 0
    wf.m.reconcile_running_steps()
    wf.m.adopt_on_startup()
    assert len(wf.dispatch.calls) == 2
    assert wf.m.get_run(run['id'])['steps']['second']['status'] == 'running'


def test_a_retry_that_fails_again_keeps_the_record_and_logs_it(env, wf, owed, monkeypatch):
    run, sid = owed
    _boot_with_failed_replay(env, wf, monkeypatch)
    real = _failing_write(wf, monkeypatch, times=10_000)
    logged = []
    monkeypatch.setattr(A, '_log', lambda m, *a, **k: logged.append(m))

    for _ in range(2):                                 # two ticks, one attempt each
        got = retry.retry_owed_workflow_steps(env.root)
        assert got == {'retried': 1, 'replayed': 0, 'advanced': 0}
        assert comp.KEY in env.ck()['session']
        assert _state(wf, run) == ('running', 'running')
        assert len(wf.dispatch.calls) == 1
    failures = [m for m in logged if 'retry of' in m and 'record kept' in m]
    assert len(failures) == 2, logged

    monkeypatch.setattr(wf.m, '_write_run', real)      # the disk is back
    assert retry.retry_owed_workflow_steps(env.root)['replayed'] == 1
    assert comp.KEY not in env.ck()['session']
    assert len(wf.dispatch.calls) == 2


def test_a_cancelled_run_is_not_resurrected(env, wf, owed, monkeypatch):
    run, sid = owed
    _boot_with_failed_replay(env, wf, monkeypatch)
    wf.m.cancel_run(run['id'])
    assert retry.retry_owed_workflow_steps(env.root) == {'retried': 0, 'replayed': 0, 'advanced': 0}
    assert wf.m.get_run(run['id'])['status'] == 'cancelled'
    assert len(wf.dispatch.calls) == 1
    assert comp.KEY in env.ck()['session']             # next boot's replay acks it (ignored)


def test_a_cancel_landing_during_the_retry_is_acked_and_not_advanced(env, wf, owed, monkeypatch):
    """The run is cancelled after the tick picked the session: replay sees
    'ignored' (acked), and the advance that follows is a no-op."""
    run, sid = owed
    _boot_with_failed_replay(env, wf, monkeypatch)
    real_runners = comp.default_runners()

    def cancel_then_run(session):
        wf.m.cancel_run(run['id'])
        return real_runners['turn'](session)

    got = retry.retry_owed_workflow_steps(env.root, runners={'turn': cancel_then_run, 'exit': None})
    assert got['replayed'] == 1
    assert comp.KEY not in env.ck()['session']
    assert wf.m.get_run(run['id'])['status'] == 'cancelled'
    assert len(wf.dispatch.calls) == 1


def test_no_retry_before_boot_adoption_has_finished(env, wf, owed, monkeypatch):
    """A tick landing between boot's replay and adoption must not advance the run:
    adoption would read the step it just dispatched as unconfirmed."""
    run, sid = owed
    _failing_write(wf, monkeypatch, times=1)
    comp.replay_pending(env.root)                      # fails; adoption has not run yet
    monkeypatch.setattr(wf.m, '_boot_adoption_done', False)
    assert retry.retry_owed_workflow_steps(env.root)['retried'] == 0
    assert comp.KEY in env.ck()['session']
    assert len(wf.dispatch.calls) == 1


def test_a_session_live_with_its_own_stamp_is_left_to_its_own_thread(env, wf, owed, monkeypatch):
    run, sid = owed
    _boot_with_failed_replay(env, wf, monkeypatch)
    mc_state.agent_sessions[sid] = {'session_id': sid, comp.KEY: {'id': 'x', 'kind': 'turn'}}
    try:
        assert retry.retry_owed_workflow_steps(env.root)['retried'] == 0
    finally:
        mc_state.agent_sessions.pop(sid, None)
    assert comp.KEY in env.ck()['session']


def test_a_replay_already_running_makes_the_tick_skip_not_wait(env, wf, owed, monkeypatch):
    run, sid = owed
    _boot_with_failed_replay(env, wf, monkeypatch)
    assert comp._REPLAY_LOCK.acquire(blocking=False)
    try:
        got = retry.retry_owed_workflow_steps(env.root)
    finally:
        comp._REPLAY_LOCK.release()
    assert got['replayed'] == 0 and comp.KEY in env.ck()['session']
    assert len(wf.dispatch.calls) == 1


def test_the_scheduler_tick_retries_before_it_reconciles():
    import inspect
    from mc.blueprints import scheduler_routes
    src = inspect.getsource(scheduler_routes)
    assert 0 < src.index('retry_owed_workflow_steps()') < src.index('_wf.reconcile_running_steps()')
