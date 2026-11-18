"""MC-1216: a late-landing stdin write must not leave the parent stuck 'running'.

Measured 2026-11-17: a callback written to Dave at 18:31:20 reached his
transcript 12 s later, past the 10 s ack wait. `_stdin_write_uncertain` was set
and never cleared, so the next durable follow-up (18:37) set status='running',
then refused to write. Dave sat 'running' with nothing driving him until the
18:52 restart, and three child reports deferred as "parent is busy".
"""
import json
import threading
import time
from types import SimpleNamespace

import pytest
from flask import Flask

from mc import stdin_ack_owner
from mc.delegation_delivery import DeliveryDeferred, DeliveryStore


class SlowStdin:
    """Write blocks until released; flush can be made to fail."""

    def __init__(self, fail=False):
        self.release = threading.Event()
        self.writes = []
        self.fail = fail

    def write(self, value):
        self.release.wait(5)
        if self.fail:
            raise BrokenPipeError('pipe closed')
        self.writes.append(value)

    def flush(self):
        pass


def _proc(stdin):
    return SimpleNamespace(stdin=stdin, pid=4242, poll=lambda: None)


def _wait_for(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def test_a_write_that_lands_after_its_ack_timeout_clears_the_flag(monkeypatch):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, '_kill_proc_background', lambda proc: None)
    stdin = SlowStdin()
    proc = _proc(stdin)
    session = {'proc': proc, 'project_id': 'p', 'log_lines': []}
    ack = ar._bounded_stdin_write(proc, 'msg\n', session=session, wait_for_ack=0.05)
    assert ack['ack'] == 'unknown'
    assert session['_stdin_write_uncertain'] is True
    stdin.release.set()
    assert _wait_for(lambda: '_stdin_write_uncertain' not in session)
    assert stdin.writes == ['msg\n']
    assert stdin_ack_owner.OWNER not in session


def test_a_write_that_fails_after_its_ack_timeout_keeps_the_flag(monkeypatch):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, '_kill_proc_background', lambda proc: None)
    stdin = SlowStdin(fail=True)
    proc = _proc(stdin)
    session = {'proc': proc, 'project_id': 'p', 'log_lines': []}
    ack = ar._bounded_stdin_write(proc, 'msg\n', session=session, wait_for_ack=0.05)
    assert ack['ack'] == 'unknown'
    stdin.release.set()
    assert _wait_for(lambda: session.get('status') == 'error')
    assert session['_stdin_write_uncertain'] is True


def test_a_flag_set_by_another_path_is_never_cleared_by_a_later_write():
    session = {'proc': object(), stdin_ack_owner.FLAG: True}
    own = stdin_ack_owner.AckOwnership(session, session['proc'])
    own.write_finished(True)
    assert session[stdin_ack_owner.FLAG] is True


def test_a_watchdog_kill_disowns_the_flag():
    proc = object()
    session = {'proc': proc}
    own = stdin_ack_owner.AckOwnership(session, proc)
    assert own.ack_timed_out() is True
    own.disown()
    own.write_finished(True)
    assert session[stdin_ack_owner.FLAG] is True


def test_a_write_that_finishes_just_as_the_wait_expires_is_not_uncertain():
    proc = object()
    session = {'proc': proc}
    own = stdin_ack_owner.AckOwnership(session, proc)
    own.write_finished(True)
    assert own.ack_timed_out() is False
    assert stdin_ack_owner.FLAG not in session


def _idle_parent_harness(monkeypatch, tmp_path, stdin):
    from mc.blueprints import agent_routes as ar
    parent = {'project_id': 'p', 'status': 'completed', 'mode': 'B',
              'provider': 'claude', 'agent_model': 'sonnet', 'incognito': False,
              'process_alive': True, 'proc': _proc(stdin),
              'stdin_lock': threading.Lock(), 'log_lines': []}
    monkeypatch.setattr(ar, 'agent_sessions', {'parent': parent})
    monkeypatch.setattr(ar, 'load_project', lambda _: {'id': 'p', 'project_path': str(tmp_path)})
    monkeypatch.setattr(ar, 'get_manager', lambda _: SimpleNamespace(lock=threading.RLock()))
    monkeypatch.setattr(ar, '_pid_is_alive', lambda _: True)
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *args: '')
    monkeypatch.setattr(ar, '_memory_turn', SimpleNamespace(refresh_for_turn=lambda *args: {'block': ''}))
    monkeypatch.setattr(ar, '_behavior_tail', SimpleNamespace(render=lambda session=None: ''))
    monkeypatch.setattr(ar, '_apply_mobile_brief', lambda message, data: message)
    monkeypatch.setattr(ar, '_clock_wrap', lambda project, session, content, message=None: content)
    monkeypatch.setattr(ar, '_rearm_notify_for_new_turn', lambda _: None)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *args: None)
    app = Flask('mc1216')
    app.register_blueprint(ar.bp)
    monkeypatch.setattr(ar, '_delegation_app', app)
    return ar, parent, app


def test_a_refused_durable_followup_leaves_the_idle_parent_idle(monkeypatch, tmp_path):
    """The 18:37 handoff: flag set, parent idle. Before the fix the parent was
    left 'running' with nothing written."""
    stdin = SlowStdin()
    stdin.release.set()
    ar, parent, app = _idle_parent_harness(monkeypatch, tmp_path, stdin)
    parent['_stdin_write_uncertain'] = True
    with app.test_client() as client:
        response = client.post('/api/project/p/agent/followup', json={
            'message': 'delegated completion', 'session_id': 'parent',
            '_durable_delivery_ack': True})
    body = response.get_json()
    assert response.status_code == 503
    assert body['stdin_write_ack'] == 'failed'
    assert parent['status'] == 'completed'
    assert stdin.writes == []


def test_the_report_after_a_late_write_is_delivered_not_parked(monkeypatch, tmp_path):
    """End to end over the real delivery store: report 1's write lands late,
    then report 2 must reach the same idle parent."""
    stdin = SlowStdin()
    ar, parent, app = _idle_parent_harness(monkeypatch, tmp_path, stdin)
    monkeypatch.setattr(ar, '_kill_proc_background', lambda proc: None)
    store = DeliveryStore(tmp_path / 'delivery.db')
    monkeypatch.setattr(ar, '_delivery_store', store)

    # Report 1: the write is slow, so its ack times out (shortened wait).
    real = ar._bounded_stdin_write

    def short_wait(*args, **kwargs):
        if kwargs.get('wait_for_ack'):
            kwargs['wait_for_ack'] = 0.05
        return real(*args, **kwargs)
    monkeypatch.setattr(ar, '_bounded_stdin_write', short_wait)

    def report(event):
        p = {'event_id': event, 'child_session_id': 'child', 'who': 'worker',
             'status': 'completed', 'task': 't', 'summary': event,
             'provider': 'claude', 'model': 'sonnet', 'message': f'report {event}'}
        store.enqueue(event, 'p', 'parent', p)
        store.accept(event, 'p', 'parent', p)
        return store.claim_inbox()

    row = report('child:turn:1')
    with pytest.raises(Exception) as first:
        ar._process_inbox(row)
    assert 'acknowledgment is unknown' in str(first.value)
    stdin.release.set()
    assert _wait_for(lambda: '_stdin_write_uncertain' not in parent)
    # The parent's turn for report 1 ends.
    parent['status'] = 'idle'

    row = report('child:turn:2')
    evidence = ar._process_inbox(row)
    assert evidence['stdin_write_ack'] == 'written'
    assert [json.loads(w)['message']['content'] for w in stdin.writes] == [
        'report child:turn:1', 'report child:turn:2']


def test_an_unresolved_write_defers_the_next_report_without_marking_running(monkeypatch, tmp_path):
    stdin = SlowStdin()
    stdin.release.set()
    ar, parent, app = _idle_parent_harness(monkeypatch, tmp_path, stdin)
    store = DeliveryStore(tmp_path / 'delivery.db')
    monkeypatch.setattr(ar, '_delivery_store', store)
    parent['_stdin_write_uncertain'] = True
    p = {'event_id': 'child:turn:9', 'child_session_id': 'child', 'who': 'worker',
         'status': 'completed', 'task': 't', 'summary': 's', 'provider': 'claude',
         'model': 'sonnet', 'message': 'report'}
    store.enqueue('child:turn:9', 'p', 'parent', p)
    store.accept('child:turn:9', 'p', 'parent', p)
    row = store.claim_inbox()
    with pytest.raises(DeliveryDeferred):
        ar._process_inbox(row)
    assert parent['status'] == 'completed'
    assert stdin.writes == []


def test_a_timeout_does_not_take_ownership_of_a_flag_another_path_set():
    """Launch/steward uncertainty already raised the flag; this write's ack then
    times out and later lands. Its success settles only its own outcome."""
    proc = object()
    session = {'proc': proc, stdin_ack_owner.FLAG: True}
    own = stdin_ack_owner.AckOwnership(session, proc)
    assert own.ack_timed_out() is True
    assert stdin_ack_owner.OWNER not in session
    own.write_finished(True)
    assert session[stdin_ack_owner.FLAG] is True
