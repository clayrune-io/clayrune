"""Session cost must come from Claude's `total_cost_usd`, counted as deltas.

Claude Code's stream-json `result` object has no `cost_usd` key. It carries
`total_cost_usd`, and that figure is cumulative for the CLI PROCESS. Every
reader read `cost_usd`, so every session's cost stayed 0.

The numbers below are real, captured 2026-09-13 from CLI 2.1.268 (haiku):
one Mode-B process, two turns, then the same session resumed in a new
process for a third turn.

    process 1, turn 1   total_cost_usd 0.031334
    process 1, turn 2   total_cost_usd 0.0354149   (turn 2 alone ~0.0041)
    process 2, turn 1   total_cost_usd 0.0038313   (counter restarted)

So the session has really spent 0.0354149 after process 1 and 0.0392462
after process 2. Setting from the last event would report 0.0038313; adding
every event would report 0.0705802. Both are wrong, which is what these
tests pin.
"""
from __future__ import annotations

import importlib
import io
import json

import pytest

P1_T1 = 0.031334
P1_T2 = 0.0354149
P2_T1 = 0.0038313


def _result(total=None, cost=None, sid='sess-cost'):
    msg = {'type': 'result', 'subtype': 'success', 'session_id': sid,
           'num_turns': 1,
           'usage': {'input_tokens': 10, 'output_tokens': 47}}
    if total is not None:
        msg['total_cost_usd'] = total
    if cost is not None:
        msg['cost_usd'] = cost
    return json.dumps(msg)


def _assistant(text):
    return json.dumps({'type': 'assistant', 'session_id': 'sess-cost',
                       'message': {'content': [{'type': 'text', 'text': text}]}})


class _FakeProc:
    def __init__(self, lines):
        self.stdout = io.StringIO('\n'.join(lines) + '\n')
        self.pid = -1
        self._rc = 0

    def wait(self):
        return self._rc

    def poll(self):
        return self._rc

    def kill(self):
        pass


def _new_session():
    return {'project_id': 'p-cost', 'status': 'running', 'log_lines': [],
            'last_output_time': 0.0, 'last_status_change_time': 0.0,
            'provider': 'claude'}


def _run(reader, session, lines):
    proc = _FakeProc(lines)
    session['proc'] = proc
    reader(proc, session)


# ── the helper ──────────────────────────────────────────────────────────────

def test_helper_two_turns_one_process_counts_the_delta():
    from mc.agent_runtime import accumulate_result_cost
    session, proc_cost = {}, {}
    accumulate_result_cost(session, {'total_cost_usd': P1_T1}, proc_cost)
    accumulate_result_cost(session, {'total_cost_usd': P1_T2}, proc_cost)
    assert session['cost_usd'] == pytest.approx(P1_T2)


def test_helper_new_process_counts_its_first_turn_in_full():
    from mc.agent_runtime import accumulate_result_cost
    session = {}
    first = {}
    accumulate_result_cost(session, {'total_cost_usd': P1_T1}, first)
    accumulate_result_cost(session, {'total_cost_usd': P1_T2}, first)
    accumulate_result_cost(session, {'total_cost_usd': P2_T1}, {})
    assert session['cost_usd'] == pytest.approx(P1_T2 + P2_T1)


def test_helper_backwards_total_is_a_restart_not_a_refund():
    from mc.agent_runtime import accumulate_result_cost
    session, proc_cost = {}, {}
    accumulate_result_cost(session, {'total_cost_usd': 0.5}, proc_cost)
    accumulate_result_cost(session, {'total_cost_usd': 0.1}, proc_cost)
    assert session['cost_usd'] == pytest.approx(0.6)


def test_helper_falls_back_to_per_turn_cost_usd():
    from mc.agent_runtime import accumulate_result_cost
    session, proc_cost = {}, {}
    accumulate_result_cost(session, {'cost_usd': 0.01}, proc_cost)
    accumulate_result_cost(session, {'cost_usd': 0.02}, proc_cost)
    assert session['cost_usd'] == pytest.approx(0.03)


def test_helper_no_cost_fields_leaves_session_untouched():
    from mc.agent_runtime import accumulate_result_cost
    session = {}
    accumulate_result_cost(session, {'cost_usd': None, 'total_cost_usd': None}, {})
    assert 'cost_usd' not in session


def test_claude_turn_end_payload_carries_total_cost_usd():
    from mc.agent_runtime import ClaudeRuntime
    ev = ClaudeRuntime().parse_event(_result(total=P1_T1))
    assert ev.payload['total_cost_usd'] == P1_T1
    assert ev.payload['cost_usd'] is None


# ── the live Claude readers (mc/blueprints/agent_routes.py) ─────────────────

def test_mode_b_reader_two_turns_then_respawn(tmp_data_dir):
    server = importlib.import_module('server')
    importlib.reload(server)
    session = _new_session()

    _run(server._read_agent_stream_b, session, [
        _assistant('one'), _result(total=P1_T1),
        _assistant('two'), _result(total=P1_T2),
    ])
    assert session['cost_usd'] == pytest.approx(P1_T2)

    # The respawned process's counter starts again at its own first turn.
    _run(server._read_agent_stream_b, session, [
        _assistant('three'), _result(total=P2_T1),
    ])
    assert session['cost_usd'] == pytest.approx(P1_T2 + P2_T1)


def test_mode_a_reader_accumulates_one_process_per_turn(tmp_data_dir):
    server = importlib.import_module('server')
    importlib.reload(server)
    session = _new_session()

    _run(server._read_agent_stream, session, [_assistant('one'), _result(total=0.02)])
    _run(server._read_agent_stream, session, [_assistant('two'), _result(total=0.03)])
    assert session['cost_usd'] == pytest.approx(0.05)


# ── the shared Mode-A reader (mc/agent_runtime.py `_mode_a_reader`) ─────────

def test_shared_mode_a_reader_two_turns_one_process():
    from mc import agent_runtime as rt
    proc = _FakeProc([_assistant('one'), _result(total=P1_T1),
                      _assistant('two'), _result(total=P1_T2)])
    session: dict = {'log_lines': [], 'proc': proc}
    handle = rt.SessionHandle(
        mc_session_id='sid-cost', provider='claude', mode='A',
        project_path='/p', project_id='p-cost', session_dict=session,
        meta={'callbacks': {}},
    )
    rt._mode_a_reader(proc, handle, rt.ClaudeRuntime())
    assert session['cost_usd'] == pytest.approx(P1_T2)


# ── CLI >= 2.1.277: a headless resume carries the session total over ────────
#
# Real figures, 2026-09-19, haiku, `-p` then two `-p --resume` turns, each in
# a new process:  2.1.278 -> 0.0275012, 0.0355756, 0.0394183.

N1 = 0.0275012
N2 = 0.0355756
N3 = 0.0394183


def _init(version, sid='sess-cost'):
    return json.dumps({'type': 'system', 'subtype': 'init', 'session_id': sid,
                       'model': 'claude-haiku-4-5', 'claude_code_version': version})


def test_helper_new_cli_resume_counts_only_new_spend():
    from mc.agent_runtime import accumulate_result_cost, note_cli_init
    session = {}
    for total in (N1, N2, N3):  # each a fresh process on 2.1.278
        proc = {}
        note_cli_init(proc, {'claude_code_version': '2.1.278'})
        accumulate_result_cost(session, {'total_cost_usd': total,
                                         'session_id': 's1'}, proc)
    assert session['cost_usd'] == pytest.approx(N3)
    assert session['cli_cost_totals'] == {'s1': N3}


def test_helper_old_cli_resume_still_counts_first_turn_in_full():
    # Pre-2.1.277 the counter restarts; a first turn costing MORE than the
    # prior total must not be mistaken for a carried-over figure.
    from mc.agent_runtime import accumulate_result_cost, note_cli_init
    session = {}
    p1 = {}
    note_cli_init(p1, {'claude_code_version': '2.1.274'})
    accumulate_result_cost(session, {'total_cost_usd': 0.004, 'session_id': 's1'}, p1)
    p2 = {}
    note_cli_init(p2, {'claude_code_version': '2.1.274'})
    accumulate_result_cost(session, {'total_cost_usd': 0.03, 'session_id': 's1'}, p2)
    assert session['cost_usd'] == pytest.approx(0.034)


def test_helper_unknown_version_does_not_seed():
    from mc.agent_runtime import accumulate_result_cost
    session = {'cli_cost_totals': {'s1': 0.5}}
    accumulate_result_cost(session, {'total_cost_usd': 0.02, 'session_id': 's1'}, {})
    assert session['cost_usd'] == pytest.approx(0.02)


def test_helper_new_cli_fresh_session_id_counts_in_full():
    # A brand-new (or forked) Claude session has no saved total to seed from.
    from mc.agent_runtime import accumulate_result_cost, note_cli_init
    session = {'cli_cost_totals': {'old': 0.5}}
    proc = {}
    note_cli_init(proc, {'cli_version': '2.1.278'})
    accumulate_result_cost(session, {'total_cost_usd': 0.02, 'session_id': 'new'}, proc)
    assert session['cost_usd'] == pytest.approx(0.02)


def test_version_gate_boundaries():
    from mc.agent_runtime import _version_tuple, CLAUDE_RESUME_CARRIES_COST as G
    assert _version_tuple('2.1.276') < G <= _version_tuple('2.1.277')
    assert _version_tuple('2.2.0') > G
    assert _version_tuple('3.0.0-beta.1') > G
    assert _version_tuple('') < G
    assert _version_tuple(None) < G


def test_claude_turn_end_payload_carries_session_id():
    from mc.agent_runtime import ClaudeRuntime
    ev = ClaudeRuntime().parse_event(_result(total=N1, sid='abc'))
    assert ev.payload['session_id'] == 'abc'


def test_mode_b_reader_new_cli_resume_no_double_count(tmp_data_dir):
    server = importlib.import_module('server')
    importlib.reload(server)
    session = _new_session()
    _run(server._read_agent_stream_b, session,
         [_init('2.1.278'), _assistant('one'), _result(total=N1)])
    _run(server._read_agent_stream_b, session,
         [_init('2.1.278'), _assistant('two'), _result(total=N2)])
    _run(server._read_agent_stream_b, session,
         [_init('2.1.278'), _assistant('three'), _result(total=N3)])
    assert session['cost_usd'] == pytest.approx(N3)


def test_mode_a_reader_new_cli_resume_no_double_count(tmp_data_dir):
    server = importlib.import_module('server')
    importlib.reload(server)
    session = _new_session()
    _run(server._read_agent_stream, session,
         [_init('2.1.278'), _assistant('one'), _result(total=N1)])
    _run(server._read_agent_stream, session,
         [_init('2.1.278'), _assistant('two'), _result(total=N2)])
    assert session['cost_usd'] == pytest.approx(N2)


def test_mode_a_reader_old_cli_resume_unchanged(tmp_data_dir):
    server = importlib.import_module('server')
    importlib.reload(server)
    session = _new_session()
    _run(server._read_agent_stream, session,
         [_init('2.1.274'), _assistant('one'), _result(total=0.031132)])
    _run(server._read_agent_stream, session,
         [_init('2.1.274'), _assistant('two'), _result(total=0.0037736)])
    assert session['cost_usd'] == pytest.approx(0.031132 + 0.0037736)


def test_shared_mode_a_reader_new_cli_resume_no_double_count():
    from mc import agent_runtime as rt
    session: dict = {'log_lines': []}
    for total in (N1, N2):
        proc = _FakeProc([_init('2.1.278'), _assistant('x'), _result(total=total)])
        session['proc'] = proc
        handle = rt.SessionHandle(
            mc_session_id='sid-cost', provider='claude', mode='A',
            project_path='/p', project_id='p-cost', session_dict=session,
            meta={'callbacks': {}},
        )
        rt._mode_a_reader(proc, handle, rt.ClaudeRuntime())
    assert session['cost_usd'] == pytest.approx(N2)
