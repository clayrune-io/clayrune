import sys
from pathlib import Path
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from conftest import *
from test_runtime_completion_log import env, _mode_b_session, _assistant_line, _log_rows


class LiveProc:
    pid = -1
    def __init__(self, lines, after=None):
        self.lines = lines
        self.after = after
        self.waiting = threading.Event()
        self.exit = threading.Event()
        self.stdin = None
        self.exited = False
    @property
    def stdout(self):
        yield from self.lines
        if self.after:
            self.after()
    def poll(self):
        return 0 if self.exited else None
    def wait(self):
        self.waiting.set()
        assert self.exit.wait(5)
        self.exited = True
        return 0


def test_self_started_write_failure_does_not_abandon_live_reader(env, monkeypatch):
    ar = env['ar']
    session = _mode_b_session(env, 'wake-storage')
    session['status'] = 'idle'
    ar._log_agent_dispatch_pending(session)
    original = ar._save_agent_log
    failed = []
    def fail_once(pid, rows):
        if not failed:
            failed.append(True)
            raise OSError('simulated transient atomic replace failure')
        return original(pid, rows)
    monkeypatch.setattr(ar, '_save_agent_log', fail_once)
    proc = LiveProc([_assistant_line('wake reply'), _assistant_line('later reply')])
    session['proc'] = proc
    worker = threading.Thread(target=ar._read_agent_stream_b, args=(proc, session))
    worker.start()
    try:
        assert proc.waiting.wait(3)
        print('LIVE', proc.poll(), 'LINES', list(session['log_lines']))
        assert any('later reply' in line for line in session['log_lines']), 'reader abandoned remaining live stdout after history write failed'
    finally:
        proc.exit.set()
        worker.join(5)
        assert not worker.is_alive()


def test_delayed_self_start_cannot_replace_new_schedule_claim(env, monkeypatch):
    from mc.blueprints import scheduler_routes as sr
    ar = env['ar']
    session = _mode_b_session(env, 'wake-race')
    session.update(status='idle', task='prior task', trigger_type='schedule', trigger_id='schedule1')
    ar._log_agent_dispatch_pending(session)
    entered, release = threading.Event(), threading.Event()
    reached_after, release_pipe, recorded = threading.Event(), threading.Event(), threading.Event()
    reader = None
    original_snapshot = ar._requested_model_snapshot
    def paused_snapshot(s):
        if threading.current_thread() is reader and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original_snapshot(s)
    monkeypatch.setattr(ar, '_requested_model_snapshot', paused_snapshot)
    monkeypatch.setattr(sr, 'get_manager', ar.get_manager)
    monkeypatch.setattr(ar.get_manager('proj1'), 'ensure_guardian', lambda: None)
    monkeypatch.setattr(sr, 'agent_sessions', env['sessions'])
    monkeypatch.setattr(sr, '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(sr, '_log_agent_activity', lambda *args: None)
    monkeypatch.setattr(sr, '_record_usage_breakdown_turn_started', lambda *args: None)
    original_failure = sr._delivery_failure.record_failure
    def record(*args):
        try:
            original_failure(*args)
        finally:
            recorded.set()
    monkeypatch.setattr(sr._delivery_failure, 'record_failure', record)
    class Pipe:
        def write(self, value):
            assert release_pipe.wait(5)
            raise BrokenPipeError('new schedule delivery rejected')
        def flush(self):
            pass
    proc = LiveProc([_assistant_line('old background wake')], reached_after.set)
    proc.stdin = Pipe()
    session['proc'] = proc
    reader = threading.Thread(target=ar._read_agent_stream_b, args=(proc, session))
    reader.start()
    try:
        assert entered.wait(3)
        assert sr._scheduled_continue(env['project'], 'proj1', 'wake-race', 'new scheduled task') == 'appended'
        scheduled_token = session['_delivery_attempt_id']
        release.set()
        assert reached_after.wait(3)
        release_pipe.set()
        assert recorded.wait(3)
        rows = _log_rows(env)
        print('SCHEDULED_TOKEN', scheduled_token, 'ROW', rows[0])
        assert rows[0]['delivery_attempt_id'] == scheduled_token, 'old reader minted a third owner after the scheduled claim'
        assert rows[0]['task'] == 'new scheduled task'
        assert rows[0]['status'] == 'error'
    finally:
        release.set()
        release_pipe.set()
        proc.exit.set()
        reader.join(5)
        assert not reader.is_alive()
