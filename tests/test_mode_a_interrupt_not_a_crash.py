"""MA1 (MC-1179): a user Stop on a Mode A engine is a stop, not a crash.

`_mode_a_interrupt` killed the process first and set `status = 'stopped'`
afterwards. `_kill_pid` is synchronous, so the reader's `finally` ran while the
status was still 'running' and logged `[<engine> exited with code N]`, a
`[hint]`, set status 'error' and fired `on_process_exit` on an 'error' session.
These tests run the real reader and the real interrupt against a real sleeping
child (started here, ended by PID); no vendor CLI is launched.
"""
import subprocess
import sys
import threading
import time

import pytest

from mc import agent_runtime as ar


class _StubRuntime:
    name = 'fake'
    transcript_path = None

    def parse_event(self, line, mc_session_id=None):
        return ar.AgentEvent(type=ar.EventType.ASSISTANT_TEXT, provider='fake',
                             session_id=None, mc_session_id='s',
                             timestamp='t', payload={'text': line})

    def explain_exit_error(self, rc, tail):
        return 'HINT-FROM-EXPLAIN'

    def turn_context_tokens(self, handle, usage, payload):
        return None


def _run(stop_before_exit):
    seen = []
    session = {'status': 'running', 'log_lines': ar.TimestampedLines(),
               'process_alive': True}
    handle = ar.SessionHandle(
        mc_session_id='s', provider='fake', mode='A', project_path='.',
        project_id='p', session_dict=session,
        meta={'callbacks': {'on_process_exit':
                            lambda ev, sess: seen.append((ev.payload['rc'], sess['status']))}})
    proc = subprocess.Popen(
        [sys.executable, '-u', '-c',
         'import time;print("hello",flush=True);time.sleep(60)'],
        stdout=subprocess.PIPE, text=True)
    try:
        session['proc'] = proc
        t = threading.Thread(target=ar._mode_a_reader,
                             args=(proc, handle, _StubRuntime()), daemon=True)
        t.start()
        deadline = time.time() + 10
        while 'hello' not in session['log_lines'] and time.time() < deadline:
            time.sleep(0.02)
        assert 'hello' in session['log_lines']
        ar._mode_a_interrupt(handle)
        t.join(15)
        assert not t.is_alive()
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
    return session, seen


@pytest.mark.parametrize('run', range(3))
def test_stop_is_not_logged_as_a_crash(run):
    session, seen = _run(True)
    lines = list(session['log_lines'])
    assert session['status'] == 'stopped', lines
    assert not any('exited with code' in l for l in lines), lines
    assert not any(l.startswith('[hint]') for l in lines), lines
    assert '[interrupted]' in lines
    # the exit callback still fires (it clears bookkeeping) but sees 'stopped'
    assert seen and all(status == 'stopped' for _rc, status in seen), seen


def test_next_turn_is_not_affected_by_a_previous_stop():
    """The stop marker must not outlive the process it stopped: a later turn
    that exits non-zero on its own is still an error."""
    session, _ = _run(True)
    session['status'] = 'running'
    handle = ar.SessionHandle(
        mc_session_id='s', provider='fake', mode='A', project_path='.',
        project_id='p', session_dict=session, meta={'callbacks': {}})
    proc = subprocess.Popen([sys.executable, '-c', 'import sys;print("x");sys.exit(3)'],
                            stdout=subprocess.PIPE, text=True)
    session['proc'] = proc
    ar._mode_a_reader(proc, handle, _StubRuntime())
    proc.stdout.close()
    assert session['status'] == 'error', list(session['log_lines'])
    assert any('exited with code 3' in l for l in session['log_lines'])
