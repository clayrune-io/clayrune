"""Independent read-fault probes; real temporary JSON and queue launch path."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from conftest import *
import pytest
from test_runtime_completion_log import env, _log_rows
from test_launch_transaction import _completed
from mc import launch_transaction as tx


def fail_first_read(ar, monkeypatch):
    original = ar.read_text_with_retry
    calls = []

    def read(path, *args, **kwargs):
        calls.append(str(path))
        if len(calls) == 1:
            raise PermissionError('transient sharing violation after read retries exhausted')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(ar, 'read_text_with_retry', read)
    return calls


@pytest.mark.parametrize('read_fault', [False, True])
def test_queue_refusal_restores_prior_result_after_snapshot_read_fault(env, monkeypatch, read_fault):
    ar = env['ar']
    session = _completed(env)
    session['pending_followups'] = ['later input']
    before = _log_rows(env)
    if read_fault:
        fail_first_read(ar, monkeypatch)
    spawned = []

    def refuse(*args, **kwargs):
        spawned.append(True)
        exc = FileNotFoundError('provider executable missing')
        exc._mc_prechild_refusal = True
        raise exc

    monkeypatch.setattr(ar, 'spawn_engine_proc', tx.spawn_boundary(refuse))
    ar._auto_dispatch_followup_locked(session, 'first input', env['project'],
                                     env['project']['project_path'], ['fake'], None)
    after = _log_rows(env)
    print({'read_fault': read_fault, 'spawned': spawned, 'before': before, 'after': after,
           'queue': session['pending_followups'], 'log': list(session['log_lines'])})
    assert after == before, 'failed snapshot read was treated as no prior row; rollback deleted completed history'
    assert session['pending_followups'] == ['first input', 'later input']


@pytest.mark.parametrize('read_fault', [False, True])
def test_unknown_launch_guard_survives_snapshot_read_fault(env, monkeypatch, read_fault):
    ar = env['ar']
    session = _completed(env)
    ar._update_agent_log('proj1', lambda rows: rows[0].update(launch_outcome='unknown'))
    before = _log_rows(env)
    if read_fault:
        fail_first_read(ar, monkeypatch)
    spawned = []
    try:
        transaction = tx.start_turn(session, None)
        transaction.launch(lambda: spawned.append(True))
    except (RuntimeError, OSError):
        pass
    print({'read_fault': read_fault, 'spawned': spawned, 'before': before, 'after': _log_rows(env)})
    assert not spawned, 'transient snapshot read bypassed durable unknown-outcome refusal'
    assert _log_rows(env) == before
