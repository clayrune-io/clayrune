"""697d2efe: complete followups and queued instructions survive a context roll."""
import json

import pytest

from test_midturn_rollover import (  # noqa: F401 -- shared scripted child fixture
    env, _Proc, _session, _run_reader, _assistant, _tool_result, _wait,
)


@pytest.mark.parametrize('queued', [False, True], ids=['followup', 'pending-followups'])
def test_send_10000_characters_reaches_child_after_roll(env, monkeypatch, queued):
    import server

    ar = env['ar']
    mode = 'A' if queued else 'B'
    proc = _Proc()
    session = _session(proc, mode=mode)
    message = ('BEGIN\n' + 'ruling identity unchanged; \u03bb\n' * 400)[:9996] + '\nEND'
    assert len(message) == 10_000
    for name in ('_mark_human_joined', '_observe_habit_cue',
                 '_write_usage_breakdown_turn_start_checkpoint', '_advance_delegation_turn'):
        monkeypatch.setattr(ar, name, lambda *a, **k: None)
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: True)
    monkeypatch.setitem(env['CONFIG'], 'turn_clock_enabled', False)
    if queued:
        env['mode_a_spawn'].append(True)

    def lines():
        yield _assistant('tool1', 100_000, 'm1')
        yield _tool_result('tool1')
        session['status'] = 'running' if queued else 'idle'
        with server.app.test_request_context(
                '/api/project/p1/agent/send', method='POST',
                json={'session_id': 'worker-1', 'message': message}):
            response = server.app.make_response(ar.agent_send('p1'))
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.get_json()['route'] == ('queue' if queued else 'followup')
        if queued:
            assert session['pending_followups'] == [message]
        else:
            assert _wait(lambda: bool(proc.stdin.written))
            assert json.loads(proc.stdin.written[-1])['message']['content'].endswith(message)
        yield _assistant('tool2', 250_000, 'm2')
        yield _tool_result('tool2')

    _run_reader(env, lines, session=session, proc=proc,
                reader='reader_a' if queued else 'reader')
    env['join_respawns']()
    assert _wait(lambda: bool(env['new_proc'].stdin.written)), session['log_lines']
    wire = ''.join(env['new_proc'].stdin.written)
    prompt = wire if queued else json.loads(wire)['message']['content']
    assert message in prompt
    assert ' [... truncated]' not in prompt


def test_roll_keeps_all_unanswered_instructions(env):
    from mc import midturn_rollover as mt

    session = {}
    messages = [f'Instruction {i}: ' + 'x' * 10_000 for i in range(8)]
    for message in messages:
        mt.note_user_message(session, message, mid_turn=True)
    prompt = mt.build_roll_state_block(session, str(env['pp']))
    for message in messages:
        assert message in prompt
    mt.note_turn_complete(session)
    assert '_mt_unanswered_msgs' not in session


def test_queued_10000_characters_reaches_next_child_without_roll(env, monkeypatch):
    """Normal Mode A queue drain also preserves the entire prompt argument."""
    import server

    ar = env['ar']
    session = _session(_Proc(), mode='A')
    message = 'BEGIN' + 'q' * 9992 + 'END'
    assert len(message) == 10_000
    env['mode_a_spawn'].append(True)
    # Queue delivery is under test; completion observers would invoke Scribe
    # and the Distiller against this synthetic conversation.
    monkeypatch.setattr(ar, '_log_agent_completion', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_run_runtime_callbacks', lambda *a, **k: None)
    for name in ('_mark_human_joined', '_observe_habit_cue',
                 '_write_usage_breakdown_turn_start_checkpoint', '_advance_delegation_turn'):
        monkeypatch.setattr(ar, name, lambda *a, **k: None)

    def lines():
        yield _assistant('tool1', 100_000, 'm1')
        yield _tool_result('tool1')
        with server.app.test_request_context(
                '/api/project/p1/agent/send', method='POST',
                json={'session_id': 'worker-1', 'message': message}):
            response = server.app.make_response(ar.agent_send('p1'))
        assert response.status_code == 200
        assert response.get_json()['route'] == 'queue'
        assert session['pending_followups'] == [message]

    _run_reader(env, lines, session=session, proc=session['proc'], reader='reader_a')
    assert len(env['spawned']) == 1, session['log_lines']
    cmd = env['spawned'][0]
    assert cmd[cmd.index('-p') + 1].endswith(message)
    assert not session['pending_followups']
