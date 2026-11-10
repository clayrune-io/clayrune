"""Independent review: stale delivery failure after a successor tab closes."""
import sys
import threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tests'))
from conftest import *
from test_runtime_delivery_truth import app_env, revive_env, _Proc, _Stdin
from test_runtime_delivery_truth_r4 import scheduled


@pytest.mark.parametrize('close_successor', [False, True])
def test_closed_same_turn_successor_keeps_its_history(scheduled, monkeypatch, close_successor):
    env = scheduled
    ar = env['ar']
    monkeypatch.setattr(ar, '_UsageBreakdownStore', lambda *a: SimpleNamespace(
        record_session_checkpoint=lambda **kw: True, mark_session_running=lambda *a: None))
    monkeypatch.setattr(ar._limit_resume_dispatch, 'close_session', lambda *a: None)
    entered, release = threading.Event(), threading.Event()
    outcome = []

    class PendingFailure(_Stdin):
        def write(self, text):
            entered.set()
            assert release.wait(10)
            raise BrokenPipeError('old scheduled prompt rejected')

    old_proc = _Proc(PendingFailure(), pid=515159)
    env['spawned']['proc'] = old_proc

    def request():
        with env['client'] as client:
            response = client.post('/api/schedule/s1/run-now')
            outcome.append((response.status_code, response.get_json()))

    caller = threading.Thread(target=request)
    caller.start()
    try:
        assert entered.wait(3)
        mgr = ar.get_manager('p1')
        with mgr.lock:
            old_session = env['sessions'][env['sid']]
            replacement = dict(old_session)
            replacement.update(proc=_Proc(_Stdin(), pid=515160), task='new successful task')
            env['sessions'][env['sid']] = replacement
            # Production turn allocation deliberately does not increment for
            # a schedule with no callback parent. Do not invent a newer turn.
            assert not replacement.get('_notify_session')
            ar._advance_delegation_turn(replacement)
            assert replacement['_delegation_turn'] == old_session['_delegation_turn'] == 1
            # Model the successor's completed result; closing a completed tab
            # uses the real endpoint and does not call any process operation.
            replacement['status'] = 'completed'
            env['history'][0].update(status='completed', summary='successor answer')
            expected = deepcopy(env['history'])
        if close_successor:
            with env['client'].application.test_client() as client:
                closed = client.delete('/api/project/p1/agent/session', json={'session_id': env['sid']})
                assert closed.status_code == 200
            assert env['sid'] not in env['sessions']
        release.set()
        caller.join(5)
        assert not caller.is_alive()
        assert outcome[0][0] == 502
        assert env['dispatches'] == []
        assert env['killed'] == [old_proc.pid]
        print('EXPECTED', expected)
        print('ACTUAL', env['history'])
        assert env['history'] == expected, 'old rejection overwrote closed successor history'
    finally:
        release.set()
        caller.join(5)
