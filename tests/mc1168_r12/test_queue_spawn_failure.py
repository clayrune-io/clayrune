"""Real Mode A reader, durable history, and a rejected queued Popen."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from conftest import *
import pytest
from test_runtime_completion_log import env, _mode_b_session, _assistant_line, _log_rows
from mc.delegation_delivery import DeliveryStore


@pytest.mark.parametrize('implementation', ['parent-control', 'candidate'])
def test_queued_spawn_refusal_does_not_leave_phantom_running_history(env, monkeypatch, implementation):
    ar = env['ar']
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(env['tmp_path'] / 'delivery.sqlite'))
    session = _mode_b_session(env, 'queue-spawn-refusal')
    session.update(mode='A', _notify_session='parent', pending_followups=['queued work'])
    ar._allocate_delegation_turn(session)
    ar._log_agent_dispatch_pending(session, strict=True)
    initial_token = session['_delivery_attempt_id']
    attempts = []

    class Proc:
        pid = -301
        stdout = [_assistant_line('previous answer')]
        def wait(self):
            return 0
        def poll(self):
            return 0

    previous = session['proc'] = Proc()
    def refuse_spawn(*args, **kwargs):
        attempts.append(True)
        raise OSError('provider executable unavailable')

    monkeypatch.setattr(ar, 'spawn_engine_proc', refuse_spawn)
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'fake')
    monkeypatch.setattr(ar, '_respawn_sysprompt_args', lambda *args: ([], None))
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *args, **kwargs: [])
    monkeypatch.setattr(ar, '_notify_agent_spawner', lambda *args, **kwargs: None)
    monkeypatch.setattr(ar, '_run_runtime_callbacks', lambda *args: None)
    if implementation == 'parent-control':
        namespace = dict(ar.__dict__)
        source = (Path(__file__).parent / 'parent_queue_function.py').read_text()
        exec(compile(source, 'parent_0c39990e_queue_function', 'exec'), namespace)
        monkeypatch.setattr(ar, '_auto_dispatch_followup_locked', namespace['_auto_dispatch_followup_locked'])

    ar._read_agent_stream(previous, session)
    rows = _log_rows(env)
    print({'implementation': implementation, 'attempts': attempts,
           'same_proc': session['proc'] is previous, 'live_status': session['status'],
           'queue': session['pending_followups'], 'dispatching': session.get('_dispatching_followup'),
           'initial_token': initial_token, 'final_token': session['_delivery_attempt_id'],
           'rows': rows, 'lines': list(session['log_lines'])})
    assert attempts == [True]
    assert session['proc'] is previous
    assert not session.get('_dispatching_followup')
    assert rows[0]['status'] != 'in_progress', 'Popen failed, but durable history claims a running turn with no reader to finish it'
    assert 'previous answer' in rows[0]['summary']
