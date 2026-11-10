"""Fenn independent R9 probes: the self-start path includes callback rearming."""
import sys
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from conftest import *
import pytest
from test_runtime_completion_log import env, _mode_b_session, _assistant_line, _result_line, _log_rows
from mc1168_r9.test_self_start import LiveProc
from mc.delegation_delivery import DeliveryStore


@pytest.mark.parametrize('failure', ['none', 'rearm-only', 'both'])
def test_rearm_write_failure_preserves_owner_and_completion(env, monkeypatch, failure):
    ar = env['ar']
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(env['tmp_path'] / 'delivery.sqlite'))
    monkeypatch.setattr(ar, '_notify_agent_spawner', lambda *args, **kwargs: None)
    session = _mode_b_session(env, 'wake-rearm-storage')
    session.update(status='idle', _notify_session='parent', _notify_session_sent=True)
    ar._allocate_delegation_turn(session)
    ar._log_agent_dispatch_pending(session)
    ar._update_agent_log('proj1', lambda rows: rows[0].update(status='completed', summary='prior answer'))
    original = ar._save_agent_log
    saves, observed, logs = [], {}, []

    def fail(pid, rows):
        saves.append(rows[0].get('delivery_attempt_id'))
        if failure == 'both' or (failure == 'rearm-only' and len(saves) == 2):
            raise OSError('rearm history replacement failure')
        return original(pid, rows)

    monkeypatch.setattr(ar, '_save_agent_log', fail)
    monkeypatch.setattr(ar, '_log', lambda message, **kwargs: logs.append(message))
    # Do not send a real callback; retain all real claim/reader/completion code.
    monkeypatch.setattr(ar, '_run_runtime_callbacks', lambda *args: None)

    def after():
        observed.update(token=session['_delivery_attempt_id'], rows=_log_rows(env),
                        status=session['status'], lines=list(session['log_lines']))
        monkeypatch.setattr(ar, '_save_agent_log', original)

    def lines():
        yield _assistant_line('wake answer')
        yield _assistant_line('later answer')
        after()  # Restore persistence before the actual turn-completion event.
        yield _result_line()

    proc = LiveProc(lines(), lambda: observed.update(after_result_rows=_log_rows(env), after_result_status=session['status']))
    session['proc'] = proc
    worker = threading.Thread(target=ar._read_agent_stream_b, args=(proc, session))
    worker.start()
    try:
        assert proc.waiting.wait(4)
        assert proc.poll() is None
        assert any('later answer' in line for line in observed['lines'])
        print('BEFORE EXIT', observed, 'SAVES', saves, 'LOGS', logs)
    finally:
        proc.exit.set()
        worker.join(5)
        assert not worker.is_alive()
    print('AFTER EXIT', _log_rows(env), 'LOGS', logs)
    assert observed['token'] == observed['rows'][0]['delivery_attempt_id'], 'callback rearm broke live/durable ownership after self-start rollback'
    assert observed['after_result_status'] == 'idle'
    assert 'later answer' in _log_rows(env)[0]['summary'], 'finished self-start disappeared from history'


def test_two_prepared_self_starts_only_claim_once(env, monkeypatch):
    ar = env['ar']
    session = _mode_b_session(env, 'competing-wakes')
    session['status'] = 'idle'
    ar._log_agent_dispatch_pending(session)
    snapshot = ar._requested_model_snapshot
    barrier = threading.Barrier(2)
    minted, results, errors = [], [], []
    uuid4 = ar._delivery_attempt.uuid4

    def sync_snapshot(s):
        barrier.wait(4)
        return snapshot(s)

    def mint():
        value = uuid4()
        minted.append(value.hex)
        return value

    monkeypatch.setattr(ar, '_requested_model_snapshot', sync_snapshot)
    monkeypatch.setattr(ar._delivery_attempt, 'uuid4', mint)

    def claim():
        try:
            results.append(ar._log_agent_dispatch_pending(session, self_started=True, write_turn_start=False))
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=claim) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
        assert not worker.is_alive()
    assert not errors
    assert sorted(results) == [False, True]
    assert minted == [session['_delivery_attempt_id']]
    assert _log_rows(env)[0]['delivery_attempt_id'] == minted[0]
    assert session['status'] == 'running'


def test_manager_wait_rechecks_claim_after_new_delivery(env, monkeypatch):
    ar = env['ar']
    session = _mode_b_session(env, 'blocked-on-manager')
    session['status'] = 'idle'
    ar._log_agent_dispatch_pending(session)
    entered, errors, answers = threading.Event(), [], []
    manager = ar.get_manager('proj1')
    original_lock = manager.lock

    class WitnessLock:
        def __enter__(self):
            if threading.current_thread() is worker:
                entered.set()
            original_lock.acquire()
        def __exit__(self, *args):
            original_lock.release()

    monkeypatch.setattr(manager, 'lock', WitnessLock())

    def claim():
        try:
            answers.append(ar._log_agent_dispatch_pending(session, self_started=True))
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=claim)
    with original_lock:
        worker.start()
        assert entered.wait(3)
        session['task'] = 'new delivery'
        ar._advance_delegation_turn(session)
        ar._update_agent_log('proj1', lambda rows: rows[0].update(status='completed', summary='new answer'))
        expected = _log_rows(env)
        monkeypatch.setattr(ar._delivery_attempt, 'uuid4', lambda: pytest.fail('waiting loser minted'))
    worker.join(5)
    assert not worker.is_alive()
    assert not errors
    assert answers == [False]
    assert _log_rows(env) == expected
    assert session['status'] == 'idle'
