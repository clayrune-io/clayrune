"""Exercise the real readers, scheduler CRUD and follow-up seams with fakes."""
import io
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from test_api_credits_session import isolated, session, event, record
from test_scheduler_routes import ctx
from mc import api_credits_budget as budget
from mc import api_credits_session as billing


@pytest.mark.parametrize('reader_name', ['_read_agent_stream', '_read_agent_stream_b'])
def test_real_reader_halts_reaps_and_replays_before_completion(reader_name, isolated, tmp_data_dir, monkeypatch):
    import server
    from mc.blueprints import agent_routes as routes
    proc = MagicMock(pid=-1)
    proc.stdout = io.StringIO(json.dumps(event(0, is_error=True,
        result='Your credit balance is too low')) + '\n')
    proc.wait.return_value = 1
    s = session(billing='credits', mode='B' if reader_name.endswith('_b') else 'A',
                proc=proc, status='running', log_lines=[], _api_credits_turn_message='same turn')
    done = MagicMock()
    replay = MagicMock(return_value=True)
    monkeypatch.setattr(routes, '_log_agent_completion', done)
    def after_exit(sess, child, module):
        proc.wait.assert_called_once()
        assert module is routes
        assert sess['billing'] == 'subscription'
        return replay(sess, child)
    monkeypatch.setattr(billing, 'replay_after_exit', after_exit)
    getattr(routes, reader_name)(proc, s)
    proc.terminate.assert_called_once()
    replay.assert_called_once_with(s, proc)
    done.assert_not_called()
    assert not any('stream error' in line for line in s['log_lines'])


@pytest.mark.parametrize('mode', ['A', 'B'])
def test_subscription_replay_uses_real_followup_without_new_delegation_turn(mode, isolated, tmp_data_dir, tmp_path, monkeypatch):
    import server
    from mc.blueprints import agent_routes as routes
    old, new = MagicMock(pid=-1), MagicMock(pid=-2)
    old.poll.return_value = 1
    s = session(billing='subscription', proc=old, mode=mode, status='running',
        log_lines=[], process_alive=False, claude_session_id='native-id',
        _resume_id='native-id', _delegation_turn=7, _turn_owner='spawner',
        _api_credits_retry_pending=True, _api_credits_turn_message='exact same task',
        billing_oauth=True)  # what _subscription() stamps when credits run out
    # An ambient key (a dev shell may export one) must still be stripped from the replay.
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'ambient-test-key')
    monkeypatch.setattr(routes, 'agent_sessions', {'sid': s})
    monkeypatch.setattr(routes, 'load_project', lambda pid: {'id': 'p', 'project_path': str(tmp_path)})
    monkeypatch.setattr(routes, 'get_manager', lambda pid: SimpleNamespace(lock=threading.RLock(), ensure_guardian=lambda: None))
    spawn = MagicMock(return_value=new)
    monkeypatch.setattr(routes, 'spawn_engine_proc', spawn)
    human = MagicMock()
    monkeypatch.setattr(routes, '_mark_human_joined', human)
    for name in ('_observe_habit_cue', '_log_agent_activity', '_register_process',
                 '_unregister_process', '_sysprompt_cleanup', '_kill_proc_background'):
        monkeypatch.setattr(routes, name, MagicMock())
    monkeypatch.setattr(routes, '_allowance_refusal', lambda *a, **k: None)
    monkeypatch.setattr(routes, '_auto_fresh_trigger', lambda *a, **k: ('', 0))
    monkeypatch.setattr(routes, '_resume_unresumable', lambda *a: False)
    monkeypatch.setattr(routes, '_fragile_resume_target', lambda *a: False)
    monkeypatch.setattr(routes, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(routes, '_refuse_duplicate_spawn', lambda *a: False)
    monkeypatch.setattr(routes, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(routes, '_session_skills', lambda *a: [])
    monkeypatch.setattr(routes, '_respawn_sysprompt_args', lambda *a: ([], None))
    monkeypatch.setattr(routes, '_clock_wrap', lambda p, sess, content, *a: content)
    monkeypatch.setattr(routes._launch_marker, 'launch_cmd', lambda cmd, *a: cmd)
    def write(proc, content, **kwargs):
        proc.stdin.write(content() if callable(content) else content)
        return {'ack': 'written'}
    monkeypatch.setattr(routes, '_bounded_stdin_write', write)
    # Run only the existing writer closures synchronously. Never run a reader,
    # spawn a CLI, or launch a background test thread. agent_followup/_do_respawn_b
    # launch their closures through agent_mail_continuation.run_followup (a real
    # daemon thread, not routes.threading), so run that seam inline too; the
    # real-thread default made the spawn land after the assertion (MC-1170).
    monkeypatch.setattr(routes._mail_continuation, 'run_followup', lambda fn: fn())
    class Thread:
        def __init__(self, target, args=(), **kwargs):
            self.target, self.args = target, args
        def start(self):
            if self.target.__name__ in ('_start_followup', '_do_respawn_b'):
                self.target(*self.args)
    monkeypatch.setattr(routes, 'threading', SimpleNamespace(Thread=Thread, Lock=threading.Lock))
    assert billing.replay_after_exit(s, old, routes)
    assert spawn.call_count == 1, s['log_lines']
    env = spawn.call_args.kwargs['env']
    assert 'ANTHROPIC_API_KEY' not in env and 'ANTHROPIC_AUTH_TOKEN' not in env
    assert spawn.call_args.args[0][1:3] == ['-r', 'native-id']
    assert s['_delegation_turn'] == 7 and s['_turn_owner'] == 'spawner'
    assert s['mode'] == mode and s['proc'] is new and s['status'] == 'running'
    human.assert_not_called()
    if mode == 'A':
        assert spawn.call_args.args[0][4] == 'exact same task'
    else:
        assert 'exact same task' in new.stdin.write.call_args.args[0]


@pytest.mark.parametrize('reader_name', ['_read_agent_stream', '_read_agent_stream_b'])
def test_real_reader_records_fixture_result(reader_name, isolated, tmp_data_dir, monkeypatch):
    import server
    from mc.blueprints import agent_routes as routes
    proc = MagicMock(pid=-1)
    proc.stdout = io.StringIO(json.dumps(event(.125)) + '\n')
    proc.wait.return_value = 0
    s = session(billing='credits', proc=proc, status='running', log_lines=[])
    # Successful result has many unrelated completion hooks; keep their normal
    # signatures but avoid external settlement, push, Scribe and state writes.
    for name in ('_log_agent_completion', '_apply_mc_tool_blocks_for_turn',
                 '_auto_snapshot_notes_on_turn', '_emit_coverage_advisory',
                 '_scan_result_event_for_auth', '_record_permission_denials',
                 '_handle_push_signal', '_write_usage_breakdown_turn_checkpoint',
                 '_run_runtime_callbacks'):
        monkeypatch.setattr(routes, name, MagicMock())
    monkeypatch.setattr(routes._delegation_settlement, 'note_claude_turn', MagicMock())
    getattr(routes, reader_name)(proc, s)
    assert budget.status()['cycle']['session_spent_usd'] == .125
    assert not any('stream error' in line for line in s['log_lines'])


def test_schedule_crud_billing_default_optout_and_invalid(ctx):
    c = ctx.client
    row = c.post('/api/schedules', json={'project_id': 'p1', 'task': 'task',
                                       'schedule_type': 'daily'}).get_json()
    assert row['billing'] == 'auto'
    sid = row['id']
    edited = c.put('/api/schedules/' + sid, json={'billing': 'subscription'})
    assert edited.status_code == 200
    assert edited.get_json()['billing'] == 'subscription'
    assert json.loads(ctx.sched_path.read_text())[0]['billing'] == 'subscription'
    assert c.post('/api/schedules', json={'project_id': 'p1', 'task': 'task', 'billing': 'credits'}).status_code == 400
    assert c.put('/api/schedules/' + sid, json={'billing': []}).status_code == 400
    assert c.post('/api/schedule/' + sid + '/run-now').status_code == 200
    assert ctx.dispatch.calls[0]['session_metadata'] == {'billing_preference': 'subscription'}


def test_scheduled_live_append_rechecks_stop_before_write(ctx, isolated, monkeypatch, tmp_path):
    sr = ctx.sr
    proc = MagicMock(pid=-1)
    proc.poll.return_value = None
    s = session(billing='credits', proc=proc, mode='B', status='idle', process_alive=True,
                project_id='p1', stdin_lock=threading.Lock(), log_lines=[])
    monkeypatch.setattr(sr, 'agent_sessions', {'sid': s})
    monkeypatch.setattr(sr, 'get_manager', lambda pid: SimpleNamespace(lock=threading.RLock(), ensure_guardian=lambda: None))
    monkeypatch.setattr(sr, '_pid_is_alive', lambda pid: True)
    revive = MagicMock(return_value=True)
    monkeypatch.setattr(sr, '_revive_from_agent_log', revive)
    record(90)
    result = sr._scheduled_continue({'project_path': str(tmp_path)}, 'p1', 'sid', 'next cycle')
    assert result == 'revived'
    proc.stdin.write.assert_not_called()
    proc.terminate.assert_called_once()
    assert s['billing'] == 'subscription'


def test_schedule_workflow_optout_reaches_agent_step(ctx, monkeypatch):
    from test_scheduler_routes import _seed_workflow
    wf = _seed_workflow(ctx)
    row = ctx.client.post('/api/schedules', json={'workflow_id': wf['id'],
        'billing': 'subscription', 'continue_session': False}).get_json()
    response = ctx.client.post('/api/schedule/' + row['id'] + '/run-now')
    assert response.status_code == 200
    assert ctx.dispatch.calls
    assert ctx.dispatch.calls[0]['session_metadata'] == {'billing_preference': 'subscription'}


def test_reserve_widening_route_uses_existing_human_gate(isolated, monkeypatch):
    from flask import Flask, jsonify
    from mc.blueprints import api_credits_routes as routes
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: False)
    gate = MagicMock(side_effect=lambda data: (jsonify(error='proof required'), 403))
    monkeypatch.setattr(routes, '_require_human_passcode', gate)
    c = app.test_client()
    assert c.put('/api/api-credits/settings', json={'session_reserve_usd': 2}).status_code == 403
    assert budget.load_settings()['session_reserve_usd'] == 3
    gate.assert_called_once()
    assert c.put('/api/api-credits/settings', json={'session_reserve_usd': 4}).status_code == 200
