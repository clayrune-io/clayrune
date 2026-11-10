"""Self-start claims revalidate idle ownership and tolerate postlaunch I/O failure."""
from copy import deepcopy
import threading

import pytest

from test_runtime_completion_log import env, _mode_b_session, _assistant_line, _log_rows  # noqa: F401
from mc1168_r9.test_self_start import LiveProc


def _idle(env):
    session = _mode_b_session(env, 'r9-wake')
    session['status'] = 'idle'
    env['ar']._log_agent_dispatch_pending(session)
    return session


@pytest.mark.parametrize('change', ['new-completed-delivery', 'question', 'running'])
def test_delayed_self_start_preserves_intervening_idle_owner_or_state(env, monkeypatch, change):
    ar, session = env['ar'], _idle(env)
    entered, release = threading.Event(), threading.Event()
    snapshot = ar._requested_model_snapshot
    errors = []
    worker = None

    def pause(s):
        if threading.current_thread() is worker:
            entered.set()
            assert release.wait(5)
        return snapshot(s)

    monkeypatch.setattr(ar, '_requested_model_snapshot', pause)

    def note():
        try:
            ar._note_self_started_turn(session)
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=note)
    worker.start()
    try:
        assert entered.wait(3)
        if change == 'new-completed-delivery':
            session['task'] = 'newer user task'
            ar._log_agent_dispatch_pending(session, strict=True)
            ar._update_agent_log('proj1', lambda rows: rows[0].update(status='success', summary='new answer'))
        elif change == 'question':
            session['waiting_for_question'] = True
        else:
            session['status'] = 'running'
        expected_rows, expected_session = _log_rows(env), deepcopy(session)
        # Losing the recheck must not mint even a discarded UUID.
        monkeypatch.setattr(ar._delivery_attempt, 'uuid4', lambda: pytest.fail('stale self-start minted a token'))
        release.set()
        worker.join(5)
        assert not worker.is_alive()
        assert not errors
        assert _log_rows(env) == expected_rows
        assert session == expected_session
    finally:
        release.set()
        worker.join(5)


def test_attempt_snapshot_precedes_the_first_idle_predicate(env, monkeypatch):
    ar, original = env['ar'], _idle(env)
    expected = []

    class Session(dict):
        armed = True

        def get(self, key, default=None):
            if key == 'status' and self.armed:
                self.armed = False
                self['task'] = 'new user delivery, already completed'
                ar._log_agent_dispatch_pending(self, strict=True)
                ar._update_agent_log('proj1', lambda rows: rows[0].update(status='success', summary='new answer'))
                expected[:] = _log_rows(env)
                monkeypatch.setattr(ar._delivery_attempt, 'uuid4', lambda: pytest.fail('stale predicate minted a token'))
            return super().get(key, default)

    session = Session(original)
    ar._note_self_started_turn(session)
    assert expected
    assert _log_rows(env) == expected
    assert session['status'] == 'idle'
    assert session['_delivery_attempt_id'] == expected[0]['delivery_attempt_id']


@pytest.mark.parametrize('token', [None, '', 'other-durable-owner'])
def test_self_start_refuses_unowned_durable_row_without_changing_live_state(env, monkeypatch, token):
    ar, session = env['ar'], _idle(env)
    ar._update_agent_log('proj1', lambda rows: rows[0].update(delivery_attempt_id=token))
    expected_rows, expected_session = _log_rows(env), deepcopy(session)
    monkeypatch.setattr(ar._delivery_attempt, 'uuid4', lambda: pytest.fail('unowned row minted a token'))
    ar._note_self_started_turn(session)
    assert _log_rows(env) == expected_rows
    assert session == expected_session


def test_save_failure_keeps_saved_owner_and_drains_both_live_replies(env, monkeypatch):
    ar, session = env['ar'], _idle(env)
    before, observed, logs = _log_rows(env), {}, []
    save = ar._save_agent_log

    def fail(pid, rows):
        raise OSError('r9 atomic replace failure')

    monkeypatch.setattr(ar, '_save_agent_log', fail)
    monkeypatch.setattr(ar, '_log', lambda message, **kwargs: logs.append(message))

    def after():
        observed.update(token=session['_delivery_attempt_id'], rows=_log_rows(env),
                        status=session['status'], lines=list(session['log_lines']))
        monkeypatch.setattr(ar, '_save_agent_log', save)

    proc = LiveProc([_assistant_line('wake reply'), _assistant_line('later reply')], after)
    session['proc'] = proc
    worker = threading.Thread(target=ar._read_agent_stream_b, args=(proc, session))
    worker.start()
    try:
        assert proc.waiting.wait(3)
        assert proc.poll() is None
        assert observed['rows'] == before
        assert observed['token'] == before[0]['delivery_attempt_id']
        assert observed['status'] == 'running'
        assert any('wake reply' in line for line in observed['lines'])
        assert any('later reply' in line for line in observed['lines'])
        assert any('pending write failed: r9 atomic replace failure' in line for line in logs)
    finally:
        proc.exit.set()
        worker.join(5)
        assert not worker.is_alive()


def test_self_start_claim_and_running_transition_both_hold_manager_lock(env):
    ar, original = env['ar'], _idle(env)
    lock, checks = ar.get_manager('proj1').lock, []

    def owned():
        available = []

        def probe():
            got = lock.acquire(blocking=False)
            available.append(got)
            if got:
                lock.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(3)
        assert available == [False]

    class Session(dict):
        def __setitem__(self, key, value):
            if key in ('_delivery_attempt_id', 'status'):
                owned()
                checks.append(key)
            return super().__setitem__(key, value)

    session = Session(original)
    ar._note_self_started_turn(session)
    assert checks == ['_delivery_attempt_id', 'status']
    assert session['status'] == 'running'
    assert session['_delivery_attempt_id'] != original['_delivery_attempt_id']
    assert _log_rows(env)[0]['delivery_attempt_id'] == session['_delivery_attempt_id']


def test_prelaunch_pending_write_still_refuses_storage_failure(env, monkeypatch):
    ar, session = env['ar'], _idle(env)
    before = _log_rows(env)

    def fail(*args):
        raise OSError('prelaunch storage failure')

    monkeypatch.setattr(ar, '_save_agent_log', fail)
    with pytest.raises(OSError, match='prelaunch storage failure'):
        ar._log_agent_dispatch_pending(session, strict=True)
    assert _log_rows(env) == before
    assert session['status'] == 'idle'


@pytest.mark.parametrize('exclusion', ['incognito', 'housekeeping'])
def test_excluded_self_start_marks_running_without_minting_or_history(env, monkeypatch, exclusion):
    ar, session = env['ar'], _idle(env)
    session[exclusion] = True
    expected = _log_rows(env)
    monkeypatch.setattr(ar._delivery_attempt, 'uuid4', lambda: pytest.fail('excluded session minted a token'))
    ar._note_self_started_turn(session)
    assert session['status'] == 'running'
    assert _log_rows(env) == expected
