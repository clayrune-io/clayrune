"""MC-1150 P4 fourth review (Fenn): a retry must not lose a run once its step is
recorded.

7c0c89d0 picked retry candidates from steps still `running`. A replay that
recorded the step (`completed`) and then failed to clear the record (Repro A), or
cleared it and then could not advance the run (Repro B), left the run out of every
recovery scan. Same real-everything setup as tests/test_agent_shim_completion_retry.py.

Run:  .venv/Scripts/python.exe -m pytest tests/test_agent_shim_completion_retry_recorded.py -q
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mc import agent_shim_completion as comp
from mc import agent_shim_completion_retry as retry

from tests.test_agent_shim_completion_reconcile import _state, owed  # noqa: F401
from tests.test_agent_shim_completion_replay import _clean_registry, env  # noqa: F401
from tests.test_agent_shim_completion_retry import _boot_with_failed_replay
from tests.test_workflows import wf  # noqa: F401


def _fail_checkpoint_clear_once(monkeypatch):
    real, calls = comp.write_json_atomic, []

    def write(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError('injected transient checkpoint clear failure')
        return real(*a, **k)

    monkeypatch.setattr(comp, 'write_json_atomic', write)


def test_ack_failure_after_the_step_is_recorded_is_retried_and_advances_once(
        env, wf, owed, monkeypatch):
    """Fenn Repro A. FAILS-BEFORE (7c0c89d0: retried=0 on every later tick)."""
    run, sid = owed
    _boot_with_failed_replay(env, wf, monkeypatch)
    _fail_checkpoint_clear_once(monkeypatch)

    first = retry.retry_owed_workflow_steps(env.root)
    assert first == {'retried': 1, 'replayed': 0, 'advanced': 0}
    assert comp.is_owed(sid, env.root)                          # record kept
    r = wf.m.get_run(run['id'])
    assert r['steps']['first']['status'] == 'completed'         # step recorded, no step running
    assert r['steps']['second']['status'] == 'pending'
    assert len(wf.dispatch.calls) == 1

    got = retry.retry_owed_workflow_steps(env.root)             # healthy tick
    assert got == {'retried': 1, 'replayed': 1, 'advanced': 1}
    assert not comp.is_owed(sid, env.root)
    r = wf.m.get_run(run['id'])
    assert (r['steps']['first']['status'], r['steps']['second']['status']) == ('completed', 'running')
    assert len(wf.dispatch.calls) == 2                          # exactly one successor

    for _ in range(3):                                          # nothing more, ever
        assert retry.retry_owed_workflow_steps(env.root)['retried'] == 0
        wf.m.reconcile_running_steps()
    assert len(wf.dispatch.calls) == 2


def test_a_record_left_after_the_successor_started_is_acked_without_a_second_dispatch(
        env, wf, owed, monkeypatch):
    """The ack failed AND the successor was already dispatched: the retry clears
    the record, re-records nothing, and does not start the successor again."""
    run, sid = owed
    _boot_with_failed_replay(env, wf, monkeypatch)
    _fail_checkpoint_clear_once(monkeypatch)
    retry.retry_owed_workflow_steps(env.root)
    wf.m.advance_idle_run(run['id'])                            # something else advanced it
    assert len(wf.dispatch.calls) == 2
    assert comp.is_owed(sid, env.root)

    got = retry.retry_owed_workflow_steps(env.root)
    assert got == {'retried': 1, 'replayed': 1, 'advanced': 0}  # acked; the advance was a no-op
    assert not comp.is_owed(sid, env.root)
    assert len(wf.dispatch.calls) == 2
    assert wf.m.get_run(run['id'])['steps']['second']['status'] == 'running'


def test_a_cancelled_run_with_a_recorded_step_is_not_resurrected(env, wf, owed, monkeypatch):
    run, sid = owed
    _boot_with_failed_replay(env, wf, monkeypatch)
    _fail_checkpoint_clear_once(monkeypatch)
    retry.retry_owed_workflow_steps(env.root)
    wf.m.cancel_run(run['id'])
    for _ in range(3):
        assert retry.retry_owed_workflow_steps(env.root)['advanced'] == 0
    assert wf.m.get_run(run['id'])['status'] == 'cancelled'
    assert len(wf.dispatch.calls) == 1


def _one_read_failure_on_advance(wf, monkeypatch):
    """advance_idle_run's first call hits one PermissionError reading this run's
    file (`_read_run` swallows it and returns None); every later call is healthy."""
    real_read, real_advance = Path.read_text, wf.m.advance_idle_run
    state = {'done': False}

    def advance(rid):
        if state['done']:
            return real_advance(rid)
        state['done'] = True

        def fail(path, *a, **k):
            if path == wf.m._run_path(rid):
                raise PermissionError('injected transient workflow read failure')
            return real_read(path, *a, **k)

        monkeypatch.setattr(Path, 'read_text', fail)
        try:
            return real_advance(rid)
        finally:
            monkeypatch.setattr(Path, 'read_text', real_read)

    monkeypatch.setattr(wf.m, 'advance_idle_run', advance)


def test_an_advance_that_did_nothing_is_not_counted_as_advanced(env, wf, owed, monkeypatch):
    """Fenn Repro B, first half. FAILS-BEFORE (advanced=1 with nothing dispatched)."""
    run, sid = owed
    _boot_with_failed_replay(env, wf, monkeypatch)
    _one_read_failure_on_advance(wf, monkeypatch)
    got = retry.retry_owed_workflow_steps(env.root)
    assert got == {'retried': 1, 'replayed': 1, 'advanced': 0}
    assert not comp.is_owed(sid, env.root)
    assert len(wf.dispatch.calls) == 1


def _set_updated(wf, run_id, value):
    """Rewrite run['updated'] on disk without going through _write_run (which re-stamps it)."""
    path = wf.m._run_path(run_id)
    data = json.loads(path.read_text(encoding='utf-8'))
    if value is _MISSING:
        data.pop('updated', None)
    else:
        data['updated'] = value
    path.write_text(json.dumps(data), encoding='utf-8')


_MISSING = object()


def _ago(seconds):
    t = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return t.isoformat().replace('+00:00', 'Z')


def _idle_run_after_failed_advance(env, wf, owed, monkeypatch):
    """Step recorded, record acked, advance could not read the run: idle `running`
    run, pending frontier, nothing owed any more."""
    run, sid = owed
    _boot_with_failed_replay(env, wf, monkeypatch)
    _one_read_failure_on_advance(wf, monkeypatch)
    got = retry.retry_owed_workflow_steps(env.root)
    assert got == {'retried': 1, 'replayed': 1, 'advanced': 0}
    assert not comp.is_owed(sid, env.root)
    assert len(wf.dispatch.calls) == 1
    assert wf.m.get_run(run['id'])['steps']['second']['status'] == 'pending'
    return run


def test_advance_read_failure_after_ack_is_recovered_by_a_later_tick(env, wf, owed, monkeypatch):
    """Fenn Repro B, second half: a stale idle run is advanced exactly once."""
    run = _idle_run_after_failed_advance(env, wf, owed, monkeypatch)
    _set_updated(wf, run['id'], _ago(retry.IDLE_RUN_GRACE_S + 5))
    got = retry.retry_owed_workflow_steps(env.root)
    assert got == {'retried': 0, 'replayed': 0, 'advanced': 1}
    r = wf.m.get_run(run['id'])
    assert (r['steps']['first']['status'], r['steps']['second']['status']) == ('completed', 'running')
    assert len(wf.dispatch.calls) == 2
    for _ in range(3):                                          # nothing more, ever
        assert retry.retry_owed_workflow_steps(env.root)['advanced'] == 0
        wf.m.reconcile_running_steps()
    assert len(wf.dispatch.calls) == 2


def test_a_run_written_inside_the_grace_is_not_advanced(env, wf, owed, monkeypatch):
    """The record-then-advance window: the caller of the write advances it itself."""
    run = _idle_run_after_failed_advance(env, wf, owed, monkeypatch)
    _set_updated(wf, run['id'], _ago(retry.IDLE_RUN_GRACE_S - 30))
    for _ in range(3):
        assert retry.retry_owed_workflow_steps(env.root)['advanced'] == 0
    assert len(wf.dispatch.calls) == 1
    assert wf.m.get_run(run['id'])['steps']['second']['status'] == 'pending'


@pytest.mark.parametrize('bad', ['not-a-date', '', None, 12345, '2026-11-05T20:00:00',
                                 _MISSING, '2099-01-01T00:00:00Z'])
def test_an_unusable_updated_counts_as_fresh(env, wf, owed, monkeypatch, bad):
    """Unparseable, empty, null, non-string, missing, naive (no zone) or future `updated`: skip."""
    run = _idle_run_after_failed_advance(env, wf, owed, monkeypatch)
    _set_updated(wf, run['id'], bad)
    for _ in range(3):
        assert retry.retry_owed_workflow_steps(env.root)['advanced'] == 0
    assert len(wf.dispatch.calls) == 1
    assert wf.m.get_run(run['id'])['steps']['second']['status'] == 'pending'
