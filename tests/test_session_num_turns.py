"""Session num_turns must be the SUM of each `result`'s num_turns.

Claude Code's stream-json `result` carries `num_turns` for THAT turn only:
the model round-trips inside one user message. It is not cumulative for the
process (contrast total_cost_usd, see test_session_cost_total.py). Every
reader overwrote the session value with the latest turn's, so a long session
showed 1 turn.

The numbers below are real, captured 2026-09-14 from CLI 2.1.268 (haiku):
one Mode-B process, three turns, then the same session resumed in a new
process for two more.

    process 1, turn 1   num_turns 1   (plain reply)
    process 1, turn 2   num_turns 3   (two Bash tool calls)
    process 1, turn 3   num_turns 1   (plain reply: not 5, so not cumulative)
    process 2, turn 1   num_turns 1   (resumed, plain reply)
    process 2, turn 2   num_turns 2   (one Bash tool call)

So the session has 5 turns after process 1 and 8 after process 2.
"""
from __future__ import annotations

import importlib
import io
import json
import time

P1 = (1, 3, 1)
P2 = (1, 2)


def _result(n, sid='sess-turns'):
    return json.dumps({'type': 'result', 'subtype': 'success', 'session_id': sid,
                       'num_turns': n,
                       'usage': {'input_tokens': 10, 'output_tokens': 5}})


def _assistant(text):
    return json.dumps({'type': 'assistant', 'session_id': 'sess-turns',
                       'message': {'content': [{'type': 'text', 'text': text}]}})


def _turns(ns):
    lines = []
    for i, n in enumerate(ns):
        lines += [_assistant(f'reply {i}'), _result(n)]
    return lines


class _FakeProc:
    def __init__(self, lines, rc=0):
        self.stdout = io.StringIO('\n'.join(lines) + '\n' if lines else '')
        self.pid = -1
        self._rc = rc

    def wait(self):
        return self._rc

    def poll(self):
        return self._rc

    def kill(self):
        pass


def _new_session(**extra):
    s = {'project_id': 'p-turns', 'status': 'running', 'log_lines': [],
         'last_output_time': 0.0, 'last_status_change_time': 0.0,
         'provider': 'claude'}
    s.update(extra)
    return s


def _run(reader, session, lines, rc=0):
    proc = _FakeProc(lines, rc)
    session['proc'] = proc
    reader(proc, session)


# ── the helper ──────────────────────────────────────────────────────────────

def test_helper_sums_measured_turns_in_one_process():
    from mc.agent_runtime import accumulate_result_turns
    session, proc = {}, {}
    for n in P1:
        accumulate_result_turns(session, {'num_turns': n}, proc)
    assert session['num_turns'] == 5
    assert proc['num_turns'] == 5


def test_helper_resumed_process_adds_to_carried_total():
    from mc.agent_runtime import accumulate_result_turns
    session = {'num_turns': 5}   # revived from the agent log
    proc = {}
    for n in P2:
        accumulate_result_turns(session, {'num_turns': n}, proc)
    assert session['num_turns'] == 8
    assert proc['num_turns'] == 3   # only what this process produced


def test_helper_ignores_missing_or_bad_values():
    from mc.agent_runtime import accumulate_result_turns
    session, proc = {}, {}
    for msg in ({}, {'num_turns': None}, {'num_turns': '2'},
                {'num_turns': -1}, {'num_turns': True}, None):
        accumulate_result_turns(session, msg, proc)
    assert 'num_turns' not in session
    assert proc == {}


# ── the live Claude readers (mc/blueprints/agent_routes.py) ─────────────────

def test_mode_b_reader_long_then_resumed_session(tmp_data_dir):
    server = importlib.import_module('server')
    importlib.reload(server)
    session = _new_session()

    _run(server._read_agent_stream_b, session, _turns(P1))
    assert session['num_turns'] == 5

    _run(server._read_agent_stream_b, session, _turns(P2))
    assert session['num_turns'] == 8


def test_mode_a_reader_one_process_per_turn(tmp_data_dir):
    server = importlib.import_module('server')
    importlib.reload(server)
    session = _new_session()
    for n in P1:
        _run(server._read_agent_stream, session, _turns([n]))
    assert session['num_turns'] == 5


def _recover_calls(server, monkeypatch):
    calls = []
    monkeypatch.setattr(importlib.import_module('mc.blueprints.agent_routes'),
                        '_auto_recover_failed_resume', lambda s: calls.append(s))
    return calls


def test_failed_resume_guard_fires_for_revived_session_with_history(tmp_data_dir, monkeypatch):
    """A revived session carries its old total. If the resumed process dies
    before producing a turn, recovery must still fire; reading the session
    total (5) would have suppressed it."""
    server = importlib.import_module('server')
    importlib.reload(server)
    calls = _recover_calls(server, monkeypatch)
    session = _new_session(mode='B', num_turns=5, _resume_id='old-sid',
                           _resume_confirmed=False, _dispatch_time=time.time())
    _run(server._read_agent_stream_b, session, [], rc=1)
    assert session['status'] == 'error'
    assert len(calls) == 1
    assert session['num_turns'] == 5


def test_failed_resume_guard_quiet_when_this_process_produced_a_turn(tmp_data_dir, monkeypatch):
    server = importlib.import_module('server')
    importlib.reload(server)
    calls = _recover_calls(server, monkeypatch)
    session = _new_session(mode='A', num_turns=0, _resume_id='old-sid',
                           _dispatch_time=time.time())
    _run(server._read_agent_stream, session, _turns([2]), rc=1)
    assert session['status'] == 'error'
    assert calls == []
    assert session['num_turns'] == 2


# ── the shared Mode-A reader (mc/agent_runtime.py `_mode_a_reader`) ─────────

def test_shared_mode_a_reader_sums_turns():
    from mc import agent_runtime as rt
    proc = _FakeProc(_turns(P1))
    session: dict = {'log_lines': [], 'proc': proc}
    handle = rt.SessionHandle(
        mc_session_id='sid-turns', provider='claude', mode='A',
        project_path='/p', project_id='p-turns', session_dict=session,
        meta={'callbacks': {}},
    )
    rt._mode_a_reader(proc, handle, rt.ClaudeRuntime())
    assert session['num_turns'] == 5
