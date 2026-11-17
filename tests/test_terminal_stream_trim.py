"""SF20 (MC-1179 pass 11): the terminal SSE stream must survive the buffer trim.

The readers keep at most 5,000 chunks and cut to the last 3,000 past that. The
stream used to follow the buffer with a list index (`lines[sent:]`, then
`sent = len(lines)`), so after the first trim `sent` was 5,000 and the list
never again held more than 5,000 entries: nothing after chunk 5,000 was ever
delivered while the process kept running.

These tests drive the REAL reader threads (`_read_terminal_stream`,
`_read_pty_stream`) and the REAL `terminal_stream` generator in lockstep on one
thread of control, so "one poll per chunk" is exact. Only the byte source and
the sleep between polls are replaced.
"""
import json
import os
import threading
import types
from pathlib import Path
import sys

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import state as st  # noqa: E402
from mc.blueprints import terminal_routes as tr  # noqa: E402

EOF = 'EOF'


class _Harness:
    """Scripted chunk source + a sleep hook that is the producer.

    `schedule` is consumed one entry per generator poll (each poll ends in
    `sleep`): an int N produces N chunks before the next poll, EOF ends the
    process. Every produced chunk is fully appended by the real reader before
    the hook returns.
    """

    def __init__(self, kind, schedule, monkeypatch):
        self.kind = kind
        self.schedule = list(schedule)
        self.produced = 0
        self._go = threading.Semaphore(0)
        self._done = threading.Semaphore(0)
        self._eof = False
        self.session = {
            'proc': None, 'pty': None, 'status': 'running', 'command': 'x',
            'output_lines': [], 'session_id': 'trim12345678', 'project_id': 'p',
            'exit_code': None,
        }
        st.terminal_sessions[self.session['session_id']] = self.session
        monkeypatch.setattr(tr, '_unregister_process', lambda pid: None)
        monkeypatch.setattr(tr, '_time', types.SimpleNamespace(sleep=self._sleep))

        if kind == 'pipe':
            outer = self

            class _Stdout:
                def fileno(self):
                    return 987654

            class _Proc:
                pid = 991001
                stdout = _Stdout()

                def wait(self):
                    return 0

            self.obj = _Proc()
            self.session['proc'] = self.obj
            shim = types.SimpleNamespace(**vars(os))
            shim.read = lambda fd, n: outer._next_chunk().encode('utf-8')
            monkeypatch.setattr(tr, 'os', shim)
            target = tr._read_terminal_stream
        else:
            outer = self

            class _Pty:
                pid = 991002

                def read(self, size=4096):
                    return outer._next_chunk()

                def wait(self):
                    return 0

            self.obj = _Pty()
            self.session['pty'] = self.obj
            target = tr._read_pty_stream
        self.thread = threading.Thread(target=target, args=(self.obj, self.session), daemon=True)
        self.thread.start()
        assert self._done.acquire(timeout=10), 'reader never reached its first read'

    # -- reader side ----------------------------------------------------
    def _next_chunk(self):
        self._done.release()  # previous chunk (if any) is fully appended
        assert self._go.acquire(timeout=30), 'producer never released a chunk'
        if self._eof:
            return ''
        return f'<{self.produced}>'

    # -- producer side --------------------------------------------------
    def _produce(self, n):
        for _ in range(n):
            self.produced += 1
            self._go.release()
            assert self._done.acquire(timeout=30), 'reader stalled'

    def _sleep(self, _secs):
        item = self.schedule.pop(0)
        if item == EOF:
            self._eof = True
            self._go.release()
            self.thread.join(10)
            assert not self.thread.is_alive()
        else:
            self._produce(item)

    # -- stream side ----------------------------------------------------
    def run_stream(self, since=0):
        app = Flask('t')
        with app.test_request_context(
                f"/api/terminal/stream?session={self.session['session_id']}&since={since}"):
            resp = tr.terminal_stream()
            events = [json.loads(e[len('data: '):]) for e in resp.response
                      if isinstance(e, str) and e.startswith('data: ')]
        outputs = [e['text'] for e in events if e['type'] == 'output']
        return [int(t[1:-1]) for t in outputs if t.startswith('<')], events


@pytest.fixture(autouse=True)
def _clean_state():
    before = dict(st.terminal_sessions)
    yield
    st.terminal_sessions.clear()
    st.terminal_sessions.update(before)


@pytest.mark.parametrize('kind', ['pipe', 'pty'])
def test_every_chunk_after_the_first_trim_is_delivered_once_and_in_order(kind, monkeypatch):
    total = 20000
    h = _Harness(kind, [1] * total + [EOF], monkeypatch)
    delivered, events = h.run_stream()
    assert events[-1]['type'] == 'status'
    assert len(delivered) == total
    assert delivered == list(range(1, total + 1))


@pytest.mark.parametrize('kind', ['pipe', 'pty'])
def test_a_reader_that_polls_rarely_resumes_at_the_oldest_kept_chunk(kind, monkeypatch):
    # Polled once at 4,900 chunks, then 20,000 more arrive before the next
    # poll: the chunks it had not seen were trimmed away. It must get the
    # newest chunks, in order, once each, and keep receiving after that.
    h = _Harness(kind, [4900, 20000, 10, 10, EOF], monkeypatch)
    delivered, events = h.run_stream()
    assert events[-1]['type'] == 'status'
    newest = h.produced
    assert newest == 24920
    assert delivered[:4900] == list(range(1, 4901))
    tail = delivered[4900:]
    assert tail == sorted(set(tail)), 'duplicate or out-of-order chunk after the gap'
    assert tail[0] > 4901, 'the trimmed chunks cannot be redelivered'
    assert tail[-1] == newest
    assert tail[-20:] == list(range(newest - 19, newest + 1))
    # resumed from the oldest KEPT chunk: what follows the gap is contiguous
    assert tail == list(range(tail[0], newest + 1))


@pytest.mark.parametrize('kind', ['pipe', 'pty'])
def test_dropped_count_matches_what_the_trim_removed(kind, monkeypatch):
    h = _Harness(kind, [12000, EOF], monkeypatch)
    h.run_stream()
    s = h.session
    # 12,000 chunks + the exit marker are all accounted for: dropped + kept.
    assert s['dropped'] + len(s['output_lines']) == 12001
    assert s['dropped'] > 0


def test_status_reports_dropped_so_a_refresh_can_resume_from_the_absolute_position():
    session = {'proc': None, 'status': 'running', 'command': 'x', 'output_lines': ['a', 'b'],
              'dropped': 7, 'session_id': 'stat12345678', 'project_id': 'pstat',
              'started_at': 't', 'exit_code': None}
    st.terminal_sessions['stat12345678'] = session
    app = Flask('t2')
    with app.test_request_context('/'):
        body = tr.terminal_status('pstat').get_json()
    assert body['sessions'][0]['dropped'] == 7
    assert body['sessions'][0]['output_lines'] == ['a', 'b']


def test_since_from_a_status_refresh_does_not_replay_what_status_already_showed(monkeypatch):
    # A refresh reads /terminal/status (buffer starts at absolute position
    # `dropped`) and connects with since = dropped + len(buffer).
    h = _Harness('pipe', [9000, 5, EOF], monkeypatch)
    app = Flask('t3')
    h._sleep(None)  # produce 9000 chunks (schedule item 1) before connecting
    s = h.session
    absolute_end = s['dropped'] + len(s['output_lines'])
    assert absolute_end == 9000
    with app.test_request_context(
            f"/api/terminal/stream?session={s['session_id']}&since={absolute_end}"):
        resp = tr.terminal_stream()
        events = [json.loads(e[len('data: '):]) for e in resp.response
                  if isinstance(e, str) and e.startswith('data: ')]
    got = [int(e['text'][1:-1]) for e in events if e['type'] == 'output' and e['text'].startswith('<')]
    assert got == [9001, 9002, 9003, 9004, 9005]
