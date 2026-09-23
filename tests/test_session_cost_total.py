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
# The CLI appends {"type":"cost-state","totalCostUSD":...} to the transcript
# when a process EXITS CLEANLY; a resume starts from the last such line. A
# force-killed process writes none. Real figures, 2026-09-19, CLI 2.1.278,
# haiku, one session, seven processes (reported total_cost_usd):
#   T1 clean 0.0255332   T2 clean 0.0296387   T3 killed /F after result 0.0332395
#   T4 clean 0.0341232 (= T2's saved line + T4's spend; T3 was not carried)
#   T5 killed /F mid-turn, no result   T6 clean 0.0403146   T7 clean 0.0441431

T1, T2, T3, T4, T6, T7 = (0.0255332, 0.0296387, 0.0332395, 0.0341232,
                          0.0403146, 0.0441431)
# What was really spent: everything the CLI carried at T7, plus T3's turn,
# which the CLI dropped when that process was killed.
TRUE_SPEND = T7 + (T3 - T2)


def _init(version, sid='sess-cost'):
    return json.dumps({'type': 'system', 'subtype': 'init', 'session_id': sid,
                       'model': 'claude-haiku-4-5', 'claude_code_version': version})


class _Transcript:
    """Stands in for ~/.claude/projects/<dir>/<sid>.jsonl."""
    def __init__(self, path):
        self.path = path
        path.write_text(json.dumps({'type': 'user', 'message': {}}) + '\n',
                        encoding='utf-8')

    def clean_exit(self, total):
        with open(self.path, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'type': 'cost-state', 'sessionId': 'sess-cost',
                                'totalCostUSD': total}) + '\n')


@pytest.fixture
def transcript(tmp_path, monkeypatch):
    from mc import agent_runtime as rt
    t = _Transcript(tmp_path / 'sess-cost.jsonl')
    monkeypatch.setattr(rt, '_find_claude_transcript', lambda cwd, sid: t.path)
    return t


def _proc(version):
    from mc.agent_runtime import note_cli_init
    proc = {}
    note_cli_init(proc, {'claude_code_version': version, 'session_id': 'sess-cost'})
    return proc


def test_read_saved_cli_cost_takes_last_line(tmp_path):
    from mc.agent_runtime import read_saved_cli_cost
    f = tmp_path / 't.jsonl'
    f.write_text('\n'.join([
        json.dumps({'type': 'cost-state', 'totalCostUSD': 0.01}),
        json.dumps({'type': 'assistant', 'text': '"cost-state"'}),
        '{"type":"cost-state", broken',
        json.dumps({'type': 'cost-state', 'totalCostUSD': 0.02}),
        json.dumps({'type': 'user'}),
    ]) + '\n', encoding='utf-8')
    assert read_saved_cli_cost(f) == pytest.approx(0.02)


def test_read_saved_cli_cost_nothing_saved_and_missing(tmp_path):
    from mc.agent_runtime import read_saved_cli_cost
    f = tmp_path / 't.jsonl'
    f.write_text(json.dumps({'type': 'user'}) + '\n', encoding='utf-8')
    assert read_saved_cli_cost(f) == 0.0
    assert read_saved_cli_cost(tmp_path / 'missing.jsonl') is None
    assert read_saved_cli_cost(None) is None


def test_measured_run_with_force_kills_counts_true_spend(transcript):
    from mc.agent_runtime import accumulate_result_cost
    session: dict = {}

    def turn(total, clean):
        accumulate_result_cost(session, {'total_cost_usd': total}, _proc('2.1.278'))
        if clean:
            transcript.clean_exit(total)
    turn(T1, True)
    turn(T2, True)
    turn(T3, False)      # killed after its result: no cost-state
    turn(T4, True)
    _proc('2.1.278')     # T5 killed mid-turn: init, no result, no cost-state
    turn(T6, True)
    turn(T7, True)
    assert session['cost_usd'] == pytest.approx(TRUE_SPEND)


def test_old_cli_never_seeds_even_with_saved_state(transcript):
    # Pre-2.1.277 the counter restarts per process; a saved line (say from a
    # newer CLI earlier in the session) must not be subtracted.
    from mc.agent_runtime import accumulate_result_cost
    transcript.clean_exit(0.5)
    session: dict = {}
    accumulate_result_cost(session, {'total_cost_usd': 0.004}, _proc('2.1.274'))
    accumulate_result_cost(session, {'total_cost_usd': 0.03}, _proc('2.1.274'))
    assert session['cost_usd'] == pytest.approx(0.034)


def test_upgrade_mid_session_counts_first_new_turn_in_full(transcript):
    # Earlier processes ran 2.1.274, which saves no cost-state, so the first
    # 2.1.278 resume carries nothing and its figure is all new spend.
    from mc.agent_runtime import accumulate_result_cost
    session: dict = {}
    accumulate_result_cost(session, {'total_cost_usd': 0.031132}, _proc('2.1.274'))
    accumulate_result_cost(session, {'total_cost_usd': 0.004}, _proc('2.1.278'))
    assert session['cost_usd'] == pytest.approx(0.035132)


def test_unknown_version_does_not_seed(transcript):
    from mc.agent_runtime import accumulate_result_cost
    transcript.clean_exit(0.5)
    session: dict = {}
    accumulate_result_cost(session, {'total_cost_usd': 0.6}, {})
    assert session['cost_usd'] == pytest.approx(0.6)


def test_unreadable_transcript_counts_in_full(monkeypatch):
    from mc import agent_runtime as rt
    monkeypatch.setattr(rt, '_find_claude_transcript', lambda cwd, sid: None)
    session: dict = {}
    rt.accumulate_result_cost(session, {'total_cost_usd': 0.02}, _proc('2.1.278'))
    assert session['cost_usd'] == pytest.approx(0.02)


def test_version_gate_boundaries():
    from mc.agent_runtime import _version_tuple, CLAUDE_RESUME_CARRIES_COST as G
    assert _version_tuple('2.1.276') < G <= _version_tuple('2.1.277')
    assert _version_tuple('v2.1.278') == (2, 1, 278)
    assert _version_tuple('V2.1.278') > G
    assert _version_tuple('2.2.0') > G
    assert _version_tuple('3.0.0-beta.1') > G
    assert _version_tuple('') < G
    assert _version_tuple(None) < G


def _clean_exit_between(reader, session, transcript, totals):
    for total in totals:
        _run(reader, session, [_init('2.1.278'), _assistant('x'), _result(total=total)])
        transcript.clean_exit(total)


def test_mode_b_reader_new_cli_resume_no_double_count(tmp_data_dir, transcript):
    server = importlib.import_module('server')
    importlib.reload(server)
    session = _new_session()
    _clean_exit_between(server._read_agent_stream_b, session, transcript, (T1, T2))
    # killed after its result: the reader saw it, the CLI did not save it
    _run(server._read_agent_stream_b, session,
         [_init('2.1.278'), _assistant('x'), _result(total=T3)])
    _clean_exit_between(server._read_agent_stream_b, session, transcript, (T4,))
    assert session['cost_usd'] == pytest.approx(T4 + (T3 - T2))


def test_mode_a_reader_new_cli_resume_no_double_count(tmp_data_dir, transcript):
    server = importlib.import_module('server')
    importlib.reload(server)
    session = _new_session()
    _clean_exit_between(server._read_agent_stream, session, transcript, (T1, T2))
    assert session['cost_usd'] == pytest.approx(T2)


def test_mode_a_reader_old_cli_resume_unchanged(tmp_data_dir):
    server = importlib.import_module('server')
    importlib.reload(server)
    session = _new_session()
    _run(server._read_agent_stream, session,
         [_init('2.1.274'), _assistant('one'), _result(total=0.031132)])
    _run(server._read_agent_stream, session,
         [_init('2.1.274'), _assistant('two'), _result(total=0.0037736)])
    assert session['cost_usd'] == pytest.approx(0.031132 + 0.0037736)


def _shared(session, lines):
    from mc import agent_runtime as rt
    proc = _FakeProc(lines)
    session['proc'] = proc
    handle = rt.SessionHandle(
        mc_session_id='sid-cost', provider='claude', mode='A',
        project_path='/p', project_id='p-cost', session_dict=session,
        meta={'callbacks': {}},
    )
    rt._mode_a_reader(proc, handle, rt.ClaudeRuntime())


def test_shared_mode_a_reader_new_cli_resume_no_double_count(transcript):
    session: dict = {'log_lines': []}
    for total in (T1, T2):
        _shared(session, [_init('2.1.278'), _assistant('x'), _result(total=total)])
        transcript.clean_exit(total)
    assert session['cost_usd'] == pytest.approx(T2)
