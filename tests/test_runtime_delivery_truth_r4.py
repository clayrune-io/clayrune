"""MC-1168 r4: rejected scheduled turns never replay; deferred starts persist.

Real scheduler/revival/runtime call paths with isolated storage and process
doubles. No vendor process, live schedule, or project data is used.
"""
from copy import deepcopy
import threading

import pytest

import mc.agent_runtime as art
from mc.pending_delivery import publication_boundary
from mc.delivery_attempt import DeliveryAttempt
from test_runtime_delivery_truth import (  # noqa: F401
    app_env, revive_env, codex, _Proc, _Stdin, CSID,
)
from test_scheduler_routes import _OneShotStop, _due_cron_row


@pytest.fixture()
def scheduled(revive_env, monkeypatch, tmp_path):
    from mc.blueprints import scheduler_routes as sr
    env = revive_env
    ar = env['ar']
    sid = 'revsid000001'
    history = [{'session_id': sid, 'claude_session_id': CSID, 'provider': 'claude',
                'task': 'old scheduled task', 'trigger_type': 'schedule',
                'trigger_id': 's1', 'status': 'completed', 'cost_usd': 1.25}]
    schedules = [_due_cron_row(continue_session=True)]
    saved, dispatches = [], []
    monkeypatch.setattr(ar, '_load_agent_log', lambda *a: history)
    monkeypatch.setattr(ar, '_update_agent_log', lambda pid, update: update(history))
    monkeypatch.setattr(ar.get_manager('p1'), 'ensure_guardian', lambda: None)
    monkeypatch.setattr(sr, 'get_manager', ar.get_manager)
    monkeypatch.setattr(sr, 'agent_sessions', env['sessions'])
    monkeypatch.setattr(sr, '_revive_from_agent_log', ar._revive_from_agent_log)
    monkeypatch.setattr(sr, '_revive_non_claude_from_agent_log', ar._revive_non_claude_from_agent_log)
    monkeypatch.setattr(sr, '_load_schedules', lambda: schedules)
    monkeypatch.setattr(sr, 'load_project', lambda *a: env['project'])
    monkeypatch.setattr(sr, 'load_projects', lambda: [])
    monkeypatch.setattr(sr, 'all_managers', lambda: [])
    monkeypatch.setattr(sr, '_load_agent_log', lambda *a: history)
    monkeypatch.setattr(sr, '_enrich_run_entries', lambda rows: rows)
    monkeypatch.setattr(sr, '_latest_session_id_for_schedule', lambda *a: sid)
    monkeypatch.setattr(sr, '_latest_claude_sid_for_schedule', lambda *a: CSID)
    monkeypatch.setattr(sr, '_role_writes', type('Writes', (), {
        'save_runtime_schedules': staticmethod(lambda rows: saved.append(rows))})())
    monkeypatch.setattr(sr, '_dispatch_agent_internal',
                        lambda *a, **kw: dispatches.append((a, kw)) or sid)
    monkeypatch.setattr(sr, '_log_agent_activity', lambda *a: None)
    monkeypatch.setattr(sr, '_claim_slot', lambda *a: True)
    monkeypatch.setattr(sr, '_prune_claims', lambda: None)
    monkeypatch.setattr(sr, '_skipped_by_condition', lambda *a: False)
    monkeypatch.setattr(sr, '_scheduler_stop', _OneShotStop())
    monkeypatch.setitem(ar.state.CONFIG, 'scheduler_paused', False)
    # Suppress unrelated periodic workers while exercising the real cadence.
    from mc import limit_resume_dispatch, agent_shim_completion_retry
    monkeypatch.setattr(limit_resume_dispatch, 'tick', lambda: None)
    monkeypatch.setattr(agent_shim_completion_retry, 'retry_owed_workflow_steps', lambda: None)
    monkeypatch.setattr(sr._wf, 'reconcile_running_steps', lambda: None)
    monkeypatch.setattr(sr._wf, 'resume_due_waits', lambda: None)
    return {**env, 'sr': sr, 'history': history, 'schedule': schedules[0],
            'saved': saved, 'dispatches': dispatches, 'sid': sid}


@pytest.mark.parametrize('path', ['cadence', 'run-now'])
@pytest.mark.parametrize('failure', ['write', 'flush'])
def test_schedule_revival_failure_is_a_failed_run_without_replay(scheduled, failure, path):
    env = scheduled

    class FailingPipe(_Stdin):
        def flush(self):
            if failure == 'flush':
                raise BrokenPipeError('flush failed after write returned')

    stdin = FailingPipe(fail_on='write' if failure == 'write' else None)
    env['spawned']['proc'] = _Proc(stdin, pid=515159)
    if path == 'run-now':
        response = env['client'].post('/api/schedule/s1/run-now')
        body = response.get_json()
        assert response.status_code == 502
        assert body['ok'] is False and body['delivered'] is False
        assert body['session_id'] == env['sid']
        assert 'BrokenPipeError' in body['error']
    else:
        env['sr']._scheduler_loop()
        assert env['schedule']['next_run'] != '2020-01-01T00:00:00Z'
    assert env['dispatches'] == []
    assert env['killed'] == [515159]
    assert len(stdin.written) == (1 if failure == 'flush' else 0)
    assert 'BrokenPipeError' in env['schedule']['last_error']
    assert env['schedule']['last_error_at'] and env['saved']
    assert 'last_run' not in env['schedule']
    response = env['client'].get('/api/schedule/s1/runs')
    assert response.status_code == 200
    runs = response.get_json()['runs']
    assert len(runs) == 1 and runs[0]['status'] == 'error'
    assert runs[0]['session_id'] == env['sid']
    assert 'BrokenPipeError' in runs[0]['summary']
    assert runs[0]['claude_session_id'] == CSID and runs[0]['cost_usd'] == 1.25


@pytest.mark.parametrize('path', ['cadence', 'run-now'])
def test_schedule_codex_delivery_error_never_falls_back(scheduled, monkeypatch, path):
    env = scheduled
    calls = []
    monkeypatch.setattr(env['sr'], '_revive_from_agent_log', lambda *a: None)

    def reject(*a):
        calls.append(a)
        raise DeliveryAttempt.capture(None).bind(art.PromptDeliveryError('codex delivery failed'))

    monkeypatch.setattr(env['sr'], '_revive_non_claude_from_agent_log', reject)
    if path == 'run-now':
        response = env['client'].post('/api/schedule/s1/run-now')
        assert response.status_code == 502 and response.get_json()['ok'] is False
    else:
        env['sr']._scheduler_loop()
    assert len(calls) == 1 and env['dispatches'] == []
    assert env['history'][0]['status'] == 'error'
    assert 'codex delivery failed' in env['history'][0]['summary']


def test_unknown_revival_error_is_not_a_clean_miss(scheduled, monkeypatch):
    def fail(*a):
        raise OSError('unclassified revival failure')
    monkeypatch.setattr(scheduled['sr'], '_revive_from_agent_log', fail)
    with pytest.raises(OSError, match='unclassified'):
        scheduled['sr']._scheduled_continue(scheduled['project'], 'p1', scheduled['sid'], 'answer')
    assert scheduled['dispatches'] == []


@pytest.fixture()
def runtime_start(revive_env, codex, monkeypatch):
    ar, rt = revive_env['ar'], codex['rt']
    monkeypatch.setattr(ar.get_manager('p1'), 'ensure_guardian', lambda: None)
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda *a: rt)
    monkeypatch.setattr(ar, '_runtime_mcp_config_json', lambda *a: '')
    monkeypatch.setattr(ar, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setitem(ar.state.CONFIG, 'vendor_context_sync_enabled', False)
    rows = []
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending',
                        lambda s, **k: rows.append({'session_id': s['session_id'], 'status': 'in_progress'}))
    monkeypatch.setattr(ar, '_update_agent_log', lambda pid, update: update(rows))

    def dispatch(**kw):
        return art._mode_a_dispatch(rt, ['codex', 'exec'], kw['task'], kw['project_path'],
            kw['project_id'], kw['task'], kw['mc_session_id'], kw['session_dict'],
            False, None, kw['callbacks'], None)

    monkeypatch.setattr(rt, 'dispatch', dispatch)
    codex['holder']['proc'] = _Proc(_Stdin(fail_on='write'))
    return {**revive_env, 'codex': codex, 'rows': rows}


def _start(env):
    return env['ar']._dispatch_via_runtime(env['project'], 'answer', provider_name='codex',
        resume_id='native-existing', reuse_session_id='codexstart')


@pytest.mark.parametrize('outer_lock', [False, True])
def test_deferred_broken_pipe_closes_durable_row_outside_locks(runtime_start, monkeypatch, outer_lock):
    env, lock_checks = runtime_start, []
    ar = env['ar']
    mgr = ar.get_manager('p1')

    def update(pid, mutate):
        # UUID claim is a short manager -> leaf transaction before launch.
        # Failure bookkeeping below still runs after publication locks exit.
        if mutate.__name__ == 'claim':
            mutate(env['rows'])
            return
        acquired = []
        def check():
            got = mgr.lock.acquire(timeout=1)
            acquired.append(got)
            if got:
                mgr.lock.release()
        thread = threading.Thread(target=check)
        thread.start()
        thread.join(2)
        lock_checks.extend(acquired)
        assert acquired == [True], 'failure persistence still holds publication lock'
        mutate(env['rows'])

    monkeypatch.setattr(ar, '_update_agent_log', update)
    with pytest.raises(art.PromptDeliveryError):
        if outer_lock:
            with publication_boundary():
                with mgr.lock:
                    _start(env)
                    assert env['rows'][0]['status'] == 'in_progress'
        else:
            _start(env)
    assert lock_checks == [True]
    assert env['rows'][0]['status'] == 'error'
    assert 'codex dispatch failed' in env['rows'][0]['summary']
    assert 'BrokenPipeError' in env['rows'][0]['summary']
    assert env['sessions']['codexstart']['status'] == 'error'


@pytest.mark.parametrize('replacement', ['proc', 'registry'])
def test_deferred_start_cleanup_preserves_replacement_owner(runtime_start, replacement):
    env = runtime_start
    new_proc = _Proc(_Stdin(), pid=737374)
    with pytest.raises(art.PromptDeliveryError):
        with publication_boundary():
            _start(env)
            expected = deepcopy(env['rows'])
            original = env['sessions']['codexstart']
            if replacement == 'registry':
                env['sessions']['codexstart'] = dict(original, proc=new_proc)
            else:
                original['proc'] = new_proc
    current = env['sessions']['codexstart']
    assert current['proc'] is new_proc
    assert current['process_alive'] is True and current['status'] == 'running'
    assert env['rows'] == expected
    assert env['codex']['killed'] == [737373]


def test_failed_schedule_history_can_precede_pending_row(app_env, monkeypatch):
    from mc.schedule_delivery_failure import record_failure
    rows = []
    monkeypatch.setattr(app_env['ar'], '_update_agent_log', lambda pid, update: update(rows))
    record_failure('p1', 's1', 'addressed', 'task',
                   DeliveryAttempt.capture(None).bind(art.PromptDeliveryError('failed')))
    assert rows[0]['status'] == 'error' and rows[0]['session_id'] == 'addressed'
    assert rows[0]['trigger_type'] == 'schedule' and rows[0]['trigger_id'] == 's1'
    assert 'failed' in rows[0]['summary']
