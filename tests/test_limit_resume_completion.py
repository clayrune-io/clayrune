"""MC-1163 durable successful completion; Fenn restart probes plus fault boundaries."""
import json
import pytest

from tests.test_limit_resume import (
    store, integration, claude_followup, session, exhausted, DUE, lr, dispatch,
)


@pytest.fixture(autouse=True)
def synthetic_launch_only(monkeypatch):
    # The worker's provider process is stubbed in these probes. Do not run the
    # real launch-fence self-test using the fixture's deferred Thread class.
    from mc import launch_marker
    monkeypatch.setattr(launch_marker, 'launch_cmd', lambda cmd, *a: cmd)


@pytest.mark.parametrize('crash_at', ['before_ack', 'after_durable_ack'])
def test_success_completion_survives_restart(claude_followup, monkeypatch, tmp_path, crash_at):
    from mc import workflows
    from tests.test_stream_reader_malformed import _FakeProc
    env = claude_followup
    s = exhausted(session())
    s.update(proc=None, process_alive=False, _turn_owner='spawner', _notify_session='parent')
    env.sessions['child'] = s
    env.store.park(s)
    monkeypatch.setattr(workflows.state, 'agent_sessions', env.sessions)
    monkeypatch.setattr(workflows, '_load_agent_log', lambda pid: [])
    monkeypatch.setattr(workflows, '_session_summary', lambda live: 'finished task')
    monkeypatch.setattr(workflows, 'WORKFLOWS_PATH', tmp_path / 'workflows.json')
    monkeypatch.setattr(workflows, 'WORKFLOW_RUNS_DIR', tmp_path / 'workflow_runs')
    monkeypatch.setattr(workflows, '_dispatch_agent_internal', lambda *a, **k: 'child')
    workflow = workflows.create_workflow({'name': 'crash-review', 'nodes': [
        {'type': 'agent', 'name': 'first', 'project_id': 'p', 'prompt': 'original task'},
    ]})
    run = workflows.start_run(workflow['id'])
    calls = []
    for name in ('_capture_system_init', '_write_usage_breakdown_turn_checkpoint',
                 '_maybe_checkpoint', '_run_runtime_callbacks'):
        monkeypatch.setattr(env.ar, name, lambda *a: None)
    monkeypatch.setattr(env.ar, '_log_agent_completion_body', lambda live: calls.append('exit'))
    monkeypatch.setattr(env.ar, '_notify_agent_spawner', lambda *a: calls.append('parent'))
    proc = _FakeProc([json.dumps({'type': 'result', 'is_error': False, 'num_turns': 1})])
    proc.poll = lambda: None
    monkeypatch.setattr(env.ar, 'spawn_engine_proc', lambda *a, **k: proc)

    class ServerCrash(BaseException):
        pass

    def write(*a, **k):
        reader = next(t for t in env.pending if t.target.__name__ == '_read_agent_stream_b')
        reader.target(*reader.args)
        assert s['_limit_resume_completion'] == {'attempt': 1, 'kind': 'exit', 'status': 'completed'}
        assert calls == []
        if crash_at == 'before_ack':
            raise ServerCrash()
        return {'ack': 'written'}
    monkeypatch.setattr(env.ar, '_bounded_stdin_write', write)
    confirm = lr.confirm_submission
    def commit_then_crash(sid, attempt):
        assert confirm(sid, attempt)
        raise ServerCrash()
    if crash_at == 'after_durable_ack':
        monkeypatch.setattr(lr, 'confirm_submission', commit_then_crash)
    dispatch.tick(DUE)
    worker = next(t for t in env.pending if t.target.__name__ == '_do_respawn_b')
    with pytest.raises(ServerCrash):
        worker.target(*worker.args)
    assert '_pending_completion' not in s
    env.sessions.clear()
    monkeypatch.setattr(lr, '_store', lr.ResumeStore(env.store.path))
    monkeypatch.setattr(env.ar, 'agent_followup', lambda pid: pytest.fail('uncertain delivery replayed'))
    dispatch.tick(DUE)
    dispatch.tick(DUE)
    recovered = env.sessions.get('child', {})
    row = lr.store().get('child')
    verdict = workflows._step_session_verdict({'session_id': 'child', 'project_id': 'p'}, use_live=True)
    reconciled = workflows.reconcile_running_steps()
    print(json.dumps({'crash_at': crash_at, 'phase': row['phase'], 'attempts': row['attempts'],
                      'restored_status': recovered.get('status'), 'deferred': recovered.get('_limit_resume_completion'),
                      'review_required': row.get('review_required'), 'verdict': verdict,
                      'reconciled': reconciled, 'run_status': workflows.get_run(run['id'])['status'],
                      'completion_calls': calls}))
    assert workflows.get_run(run['id'])['status'] == 'completed'


@pytest.fixture
def durable_completion(integration, monkeypatch, tmp_path):
    """Real completion log + workflow files, isolated from operator state."""
    from mc import workflows
    env = integration
    log_dir = tmp_path / 'projects'
    log_dir.mkdir()
    monkeypatch.setattr(env.ar, 'DATA_DIR', log_dir)
    monkeypatch.setattr(env.ar, '_compute_code_delta', lambda s: {})
    monkeypatch.setattr(workflows.state, 'agent_sessions', env.sessions)
    monkeypatch.setattr(workflows, '_load_agent_log', env.ar._load_agent_log)
    monkeypatch.setattr(workflows, '_session_summary', lambda s: 'finished task')
    monkeypatch.setattr(workflows, 'WORKFLOWS_PATH', tmp_path / 'workflows.json')
    monkeypatch.setattr(workflows, 'WORKFLOW_RUNS_DIR', tmp_path / 'workflow_runs')
    monkeypatch.setattr(workflows, '_dispatch_agent_internal', lambda *a, **k: 'child')
    workflow = workflows.create_workflow({'name': 'durable-success', 'nodes': [
        {'type': 'agent', 'name': 'first', 'project_id': 'p', 'prompt': 'task'}]})
    run = workflows.start_run(workflow['id'])
    s = exhausted(session())
    s.update(housekeeping=True, _notify_workflow={'run_id': run['id'], 'step': 'first'})
    env.sessions['child'] = s
    assert env.store.park(s)
    assert env.store.claim('child', DUE)
    lr.new_turn(s, internal=True)
    s.update(_limit_resume_attempt=1, status='idle')
    s['log_lines'].append('finished task')
    assert lr.hold_completion(s, {}, completion_kind='turn')
    assert lr.hold_completion(s, {}, completion_kind='exit')
    env.live, env.run, env.workflows = s, run, workflows
    return env


@pytest.mark.parametrize('crash_at', ['before_receipt', 'after_receipt', 'none'])
def test_finalization_is_idempotent_across_log_and_receipt_crashes(
        durable_completion, monkeypatch, crash_at):
    env = durable_completion
    s = env.live
    mark = env.store.completion_done
    class ServerCrash(BaseException):
        pass
    def crash(live, attempt):
        # Real _log_agent_completion has committed its row and workflow first.
        rows = env.ar._load_agent_log('p')
        assert len(rows) == 1
        assert rows[0]['completion_key'] == env.store.get('child')['completion_key']
        assert env.workflows.get_run(env.run['id'])['steps']['first']['status'] == 'completed'
        if crash_at == 'after_receipt':
            assert mark(live, attempt)
        raise ServerCrash()
    if crash_at != 'none':
        monkeypatch.setattr(env.store, 'completion_done', crash)
        with pytest.raises(ServerCrash):
            dispatch.confirm_submission(s, 1)
    else:
        dispatch.confirm_submission(s, 1)
        dispatch.confirm_submission(s, 1)
    env.sessions.clear()
    monkeypatch.setattr(lr, '_store', lr.ResumeStore(env.store.path))
    monkeypatch.setattr(env.ar, 'agent_followup', lambda pid: pytest.fail('provider replay'))
    for _ in range(2):
        dispatch.restore_waits(env.ar)
        dispatch.tick(DUE)
        env.sessions.clear()  # a second boot, not just a second tick
    rows = env.ar._load_agent_log('p')
    assert len(rows) == 1
    assert rows[0]['summary'] == 'finished task'
    assert rows[0]['status'] == 'completed'
    assert lr.store().get('child') is None
    assert env.workflows._step_session_verdict(
        {'session_id': 'child', 'project_id': 'p'}, use_live=False)[:2] == ('ended', 'completed')
    assert env.workflows.reconcile_running_steps() == {'advanced': 0, 'stalled': 0}


@pytest.mark.parametrize('intervention', ['cancel', 'human', 'review', 'stale'])
def test_restart_never_releases_overruled_success(durable_completion, monkeypatch, intervention):
    env = durable_completion
    if intervention == 'cancel':
        assert env.store.cancel('child', 'p')
    elif intervention == 'human':
        monkeypatch.setattr(env.ar, '_update_agent_log', lambda *a, **k: None)
        with env.app.test_request_context(headers={'Origin': 'http://localhost:5199'}):
            env.ar._mark_human_joined('p', 'child')
    elif intervention == 'review':
        lr.submission_failed(env.live, 1)
    else:
        with env.store._transaction():
            rows = env.store.rows
            row = {**rows['child'], 'deferred_success':
                   {'attempt': 2, 'kind': 'exit', 'status': 'completed'}}
            env.store._commit({**rows, 'child': row})
    env.sessions.clear()
    monkeypatch.setattr(lr, '_store', lr.ResumeStore(env.store.path))
    monkeypatch.setattr(env.ar, 'agent_followup', lambda pid: pytest.fail('provider replay'))
    dispatch.tick(DUE)
    assert env.ar._load_agent_log('p') == []
    if intervention in ('cancel', 'human'):
        assert 'child' not in env.sessions
        assert lr.store().get('child')['phase'] == 'cancelled'
    else:
        assert env.sessions['child']['status'] == 'waiting_for_reset'
        assert lr.store().get('child')['phase'] == 'submitting'


def test_live_result_still_waits_for_matching_ack_on_scheduler_tick(durable_completion):
    env = durable_completion
    dispatch.tick(DUE)
    assert env.live['status'] == 'waiting_for_reset'
    assert env.store.get('child')['phase'] == 'submitting'
    assert env.ar._load_agent_log('p') == []


def test_failed_completion_write_retains_success_for_recovery(durable_completion, monkeypatch):
    env = durable_completion
    def disk_full(*a):
        raise OSError('synthetic log disk full')
    with monkeypatch.context() as patch:
        patch.setattr(env.ar, '_save_agent_log', disk_full)
        dispatch.confirm_submission(env.live, 1)
    assert env.live['status'] == 'waiting_for_reset'
    assert env.store.get('child')['deferred_success']['attempt'] == 1
    assert not env.store.get('child').get('review_required')
    dispatch.tick(DUE)
    assert env.store.get('child')['phase'] == 'active'
    dispatch.tick(DUE)  # next cleanup pass sees the now-committed receipt
    assert env.store.get('child') is None
    assert len(env.ar._load_agent_log('p')) == 1


def test_new_task_cannot_reuse_previous_completion_key(durable_completion):
    env = durable_completion
    first_key = env.store.get('child')['completion_key']
    dispatch.confirm_submission(env.live, 1)
    lr.new_turn(env.live)
    env.live['status'] = 'error'
    assert env.store.park(exhausted(env.live))
    assert env.store.claim('child', DUE)
    lr.new_turn(env.live, internal=True)
    env.live.update(status='idle', _limit_resume_attempt=1)
    assert lr.hold_completion(env.live, {}, completion_kind='turn')
    assert env.store.get('child')['completion_key'] != first_key


def test_applied_terminal_receipt_is_pruned_without_restoring_chat(durable_completion):
    env = durable_completion
    dispatch.confirm_submission(env.live, 1)
    env.sessions.clear()
    dispatch.tick(DUE)
    assert 'child' not in env.sessions
    dispatch.restore_waits(env.ar)
    assert 'child' not in env.sessions
    assert env.store.get('child') is None


def test_failed_parent_wake_retries_completion_without_provider(durable_completion, monkeypatch):
    env = durable_completion
    env.live['_notify_session'] = 'parent'
    monkeypatch.setattr(env.ar, '_notify_agent_spawner', lambda *a: False)
    dispatch.confirm_submission(env.live, 1)
    assert env.store.get('child')['phase'] == 'submitting'
    assert env.live['status'] == 'waiting_for_reset'
    monkeypatch.setattr(env.ar, '_notify_agent_spawner', lambda *a: True)
    monkeypatch.setattr(env.ar, 'agent_followup', lambda pid: pytest.fail('provider replay'))
    dispatch.tick(DUE)
    assert env.store.get('child')['phase'] == 'active'
    dispatch.tick(DUE)
    assert env.store.get('child') is None
    assert len(env.ar._load_agent_log('p')) == 1
