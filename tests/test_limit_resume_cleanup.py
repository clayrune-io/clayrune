"""MC-1163 fifth-review probes and durable close/receipt cleanup boundaries."""
import json
from threading import Event, Thread
import pytest

from tests.test_limit_resume import store, integration, session, exhausted, lr, dispatch, DUE
from tests.test_limit_resume_completion import durable_completion


def test_closed_completed_chat_stays_closed_after_boot(durable_completion, monkeypatch):
    env = durable_completion
    dispatch.confirm_submission(env.live, 1)
    assert env.live['status'] == 'completed'
    with env.app.test_request_context(json={'session_id': 'child'}):
        response = env.ar.agent_session_delete('p')
    assert response.get_json()['ok']
    assert 'child' not in env.sessions
    monkeypatch.setattr(lr, '_store', lr.ResumeStore(env.store.path))
    dispatch.restore_waits(env.ar)
    print('after close and boot:', list(env.sessions), lr.store().get('child')['phase'])
    assert 'child' not in env.sessions, 'boot resurrected the explicitly closed chat'


def test_receipt_replay_dispatches_downstream_only_once(durable_completion, monkeypatch):
    env = durable_completion
    wf = env.workflows
    dispatched = []
    def spawn(*args, **kwargs):
        dispatched.append((args, kwargs))
        return 'child' if len(dispatched) == 1 else 'downstream'
    monkeypatch.setattr(wf, '_dispatch_agent_internal', spawn)
    definition = wf.create_workflow({'name': 'two-step-review', 'nodes': [
        {'type': 'agent', 'name': 'first', 'project_id': 'p', 'prompt': 'first'},
        {'type': 'agent', 'name': 'second', 'project_id': 'p', 'prompt': 'second'},
    ], 'edges': [{'from': 'first', 'to': 'second'}]})
    run = wf.start_run(definition['id'])
    env.live['_notify_workflow'] = {'run_id': run['id'], 'step': 'first'}
    with env.store._transaction():
        row = env.store.rows['child']
        row['snapshot']['_notify_workflow'] = env.live['_notify_workflow']
        env.store._commit(env.store.rows)
    class Crash(BaseException):
        pass
    def crash(*args):
        assert wf.get_run(run['id'])['steps']['first']['status'] == 'completed'
        raise Crash()
    monkeypatch.setattr(env.store, 'completion_done', crash)
    with pytest.raises(Crash):
        dispatch.confirm_submission(env.live, 1)
    env.sessions.clear()
    monkeypatch.setattr(lr, '_store', lr.ResumeStore(env.store.path))
    dispatch.restore_waits(env.ar)
    wf.adopt_on_startup()
    wf.reconcile_running_steps()
    env.sessions.pop('child', None)
    dispatch.restore_waits(env.ar)
    wf.reconcile_running_steps()
    assert len(dispatched) == 2
    assert wf.get_run(run['id'])['steps']['second']['session_id'] == 'downstream'
    assert len(env.ar._load_agent_log('p')) == 1


@pytest.mark.parametrize('status', ['running', 'stopped'])
def test_attended_reader_completes_without_resume_record(integration, monkeypatch, status):
    from tests.test_stream_reader_malformed import _FakeProc
    env = integration
    live = session()
    live.update(source='manual', trigger_type='', _turn_owner='human', status=status, mode='B')
    proc = _FakeProc([json.dumps({'type': 'result', 'is_error': False, 'num_turns': 1})])
    live['proc'] = proc
    env.sessions['child'] = live
    for name in ('_capture_system_init', '_write_usage_breakdown_turn_checkpoint',
                 '_maybe_checkpoint', '_run_runtime_callbacks'):
        monkeypatch.setattr(env.ar, name, lambda *a: None)
    calls = []
    monkeypatch.setattr(env.ar, '_log_agent_completion_body', lambda s: calls.append(s['status']))
    env.ar._read_agent_stream_b(proc, live)
    assert live['status'] == ('stopped' if status == 'stopped' else 'completed')
    assert calls
    assert env.store.get('child') is None
    assert '_limit_resume_completion' not in live
    assert not any('[stream error:' in line for line in live['log_lines'])


@pytest.mark.parametrize('phase', ['waiting', 'submitting', 'active'])
def test_close_is_scoped_and_fences_stale_writers(store, phase):
    live = exhausted(session())
    assert store.park(live)
    if phase != 'waiting':
        assert store.claim('child', DUE)
    if phase == 'active':
        assert store.submitted('child', attempt=1)
    stale = lr.ResumeStore(store.path)
    store.close('child', 'wrong-project')
    assert store.get('child')['phase'] == phase
    store.close('child', 'p')
    store.close('child', 'p')
    assert not stale.submitted('child', attempt=1)
    assert not stale.park(exhausted(session()))
    row = stale.get('child')
    assert row['phase'] == 'cancelled'
    assert row['snapshot'] == {'session_id': 'child', 'project_id': 'p'}
    assert 'completion_key' not in row


def test_close_waits_for_the_ack_project_lock(durable_completion):
    env = durable_completion
    entered, finished = Event(), Event()
    errors = []
    def close():
        entered.set()
        try:
            dispatch.close_session('p', 'child')
        except BaseException as exc:
            errors.append(exc)
        finally:
            finished.set()
    with env.ar.get_manager('p').lock:
        worker = Thread(target=close)
        worker.start()
        assert entered.wait(2)
        assert not finished.wait(0.05)
        dispatch.confirm_submission(env.live, 1)
    worker.join(3)
    assert not worker.is_alive()
    assert errors == []
    assert env.store.get('child')['phase'] == 'cancelled'
    env.sessions.clear()
    dispatch.restore_waits(env.ar)
    assert 'child' not in env.sessions


@pytest.mark.parametrize('missing', ['log', 'workflow', 'read_error'])
def test_cleanup_retains_receipt_until_durable_effects_are_readable(
        durable_completion, monkeypatch, missing):
    env = durable_completion
    dispatch.confirm_submission(env.live, 1)
    with monkeypatch.context() as patch:
        if missing == 'log':
            patch.setattr(env.ar, '_load_agent_log', lambda pid: [])
        elif missing == 'workflow':
            patch.setattr(env.workflows, 'get_run', lambda run_id: None)
        else:
            def unreadable(pid):
                raise OSError('synthetic read failure')
            patch.setattr(env.ar, '_load_agent_log', unreadable)
        dispatch.tick(DUE)
        assert env.store.get('child')['completion_done']
    dispatch.tick(DUE)
    assert env.store.get('child') is None
    assert len(env.ar._load_agent_log('p')) == 1


def test_prune_cannot_drop_replaced_or_closed_receipt(durable_completion):
    env = durable_completion
    key = env.store.get('child')['completion_key']
    dispatch.confirm_submission(env.live, 1)
    assert not env.store.prune_completed('child', 'different-completion')
    env.store.close('child', 'p')
    assert not env.store.prune_completed('child', key)
    assert env.store.get('child')['phase'] == 'cancelled'


@pytest.mark.parametrize('fail_boot_log', [False, True])
def test_successful_turn_keeps_budget_until_exit_log_is_durable(
        durable_completion, monkeypatch, fail_boot_log):
    env = durable_completion
    with env.store._transaction():
        row = env.store.rows['child']
        row['deferred_success'].update(kind='turn', status='idle')
        env.store._commit(env.store.rows)
    dispatch.confirm_submission(env.live, 1)
    dispatch.tick(DUE)
    assert env.live['status'] == 'idle'
    assert env.store.get('child')['attempts'] == 1
    assert env.ar._load_agent_log('p') == []
    # Boot must apply the missing exit log without another provider launch.
    env.sessions.clear()
    monkeypatch.setattr(lr, '_store', lr.ResumeStore(env.store.path))
    with monkeypatch.context() as patch:
        if fail_boot_log:
            def disk_full(*args):
                raise OSError('synthetic boot log write failure')
            patch.setattr(env.ar, '_save_agent_log', disk_full)
        dispatch.restore_waits(env.ar)
    if fail_boot_log:
        assert env.sessions['child']['status'] == 'waiting_for_reset'
        assert lr.store().get('child')['completion_done']
        dispatch.tick(DUE)
    assert env.sessions['child']['status'] == 'completed'
    assert len(env.ar._load_agent_log('p')) == 1
    env.sessions.clear()
    dispatch.restore_waits(env.ar)
    assert lr.store().get('child') is None
    assert 'child' not in env.sessions


def test_cleanup_write_failure_retains_receipt_for_next_tick(durable_completion, monkeypatch):
    env = durable_completion
    dispatch.confirm_submission(env.live, 1)
    with monkeypatch.context() as patch:
        def disk_full(rows):
            raise OSError('synthetic cleanup disk full')
        patch.setattr(env.store, '_commit', disk_full)
        dispatch.tick(DUE)
        assert env.store.get('child')['completion_done']
    dispatch.tick(DUE)
    assert env.store.get('child') is None


def test_successful_turn_live_exit_uses_receipt_key_then_prunes(durable_completion):
    env = durable_completion
    with env.store._transaction():
        row = env.store.rows['child']
        row['deferred_success'].update(kind='turn', status='idle')
        env.store._commit(env.store.rows)
    key = env.store.get('child')['completion_key']
    dispatch.confirm_submission(env.live, 1)
    env.live['status'] = 'completed'
    env.ar._log_agent_completion(env.live)
    assert env.ar._load_agent_log('p')[0]['completion_key'] == key
    dispatch.tick(DUE)
    assert env.store.get('child') is None
    assert len(env.ar._load_agent_log('p')) == 1
