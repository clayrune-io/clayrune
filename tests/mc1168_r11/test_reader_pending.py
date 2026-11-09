"""Independent pending-write audit: queue replay from the real Mode A reader."""
import sys
from pathlib import Path
from types import SimpleNamespace
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from conftest import *
import pytest
from test_runtime_completion_log import env, _mode_b_session, _assistant_line, _log_rows
from mc.delegation_delivery import DeliveryStore


@pytest.mark.parametrize('fail_save', [False, True])
def test_reader_queue_replay_cannot_leave_launched_process_without_reader(env, monkeypatch, fail_save):
    ar = env['ar']
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(env['tmp_path'] / 'delivery.sqlite'))
    session = _mode_b_session(env, 'reader-queue')
    session.update(mode='A', pending_followups=['queued work'])
    ar._allocate_delegation_turn(session)
    ar._log_agent_dispatch_pending(session, strict=True)
    spawned, read, registered, threads, errors, saves = [], [], [], [], [], []

    class Proc:
        def __init__(self, pid, text):
            self.pid = pid
            self.text = text
            self.exited = False
        @property
        def stdout(self):
            read.append(self.pid)
            return iter([_assistant_line(self.text)])
        def wait(self):
            self.exited = True
            return 0
        def poll(self):
            return 0 if self.exited else None

    previous, following = Proc(-101, 'previous answer'), Proc(-102, 'queued answer')
    session['proc'] = previous
    original_save = ar._save_agent_log
    def save(pid, rows):
        saves.append((rows[0]['status'], rows[0]['delivery_attempt_id']))
        if fail_save and spawned and rows[0]['status'] == 'in_progress':
            raise OSError('queue replay pending replacement failed')
        return original_save(pid, rows)
    def spawn(*args, **kwargs):
        spawned.append(following.pid)
        return following
    original_thread = threading.Thread
    def thread(*args, **kwargs):
        worker = original_thread(*args, **kwargs)
        threads.append(worker)
        return worker
    monkeypatch.setattr(ar, '_save_agent_log', save)
    monkeypatch.setattr(ar, 'spawn_engine_proc', spawn)
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'fake')
    monkeypatch.setattr(ar, '_respawn_sysprompt_args', lambda *args: ([], None))
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *args, **kwargs: [])
    monkeypatch.setattr(ar, '_hide_windows_delayed', lambda *args: None)
    monkeypatch.setattr(ar, '_notify_agent_spawner', lambda *args, **kwargs: None)
    monkeypatch.setattr(ar, '_run_runtime_callbacks', lambda *args: None)
    monkeypatch.setattr(ar, '_register_process', lambda proc, *args: registered.append(proc.pid))
    monkeypatch.setattr(ar.threading, 'Thread', thread)
    try:
        ar._read_agent_stream(previous, session)
    except Exception as exc:
        errors.append(repr(exc))
    finally:
        for worker in threads:
            worker.join(5)
            assert not worker.is_alive()
    rows = _log_rows(env)
    print({'errors': errors, 'spawned': spawned, 'read': read, 'registered': registered,
           'new_proc_poll': following.poll(), 'status': session['status'],
           'pending_followups': session.get('pending_followups'),
           'dispatching': session.get('_dispatching_followup'), 'saves': saves,
           'live_token': session['_delivery_attempt_id'], 'rows': rows})
    assert spawned == [-102], 'fixture did not reach the queued spawn'
    assert -102 in read, 'already-launched queued process has no reader after pending save failed'
    assert -102 in registered
    assert not errors
    assert rows[0]['delivery_attempt_id'] == session['_delivery_attempt_id']
    assert 'queued answer' in rows[0]['summary']


@pytest.mark.parametrize('helper', ['_advance_delegation_turn', '_rearm_notify_for_new_turn'])
def test_prelaunch_turn_helpers_still_raise_storage_failure(env, monkeypatch, helper):
    ar = env['ar']
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(env['tmp_path'] / 'delivery.sqlite'))
    session = _mode_b_session(env, 'prelaunch-strict')
    session.update(status='idle', _notify_session='parent', _notify_session_sent=True)
    ar._allocate_delegation_turn(session)
    ar._log_agent_dispatch_pending(session)
    before = _log_rows(env)
    def fail(*args):
        raise OSError('strict prelaunch refusal')
    monkeypatch.setattr(ar, '_save_agent_log', fail)
    with pytest.raises(OSError, match='strict prelaunch refusal'):
        getattr(ar, helper)(session)
    assert session['status'] == 'idle'
    assert _log_rows(env) == before


def test_live_identity_backfill_failure_does_not_mint_a_new_owner(env, monkeypatch):
    ar = env['ar']
    session = _mode_b_session(env, 'identity-backfill')
    ar._log_agent_dispatch_pending(session)
    before = _log_rows(env)
    def fail(*args):
        raise OSError('identity backfill replacement failed')
    monkeypatch.setattr(ar, '_save_agent_log', fail)
    ar._note_claude_sid(session, 'new-conversation')
    assert session['_delivery_attempt_id'] == before[0]['delivery_attempt_id']
    assert _log_rows(env) == before
