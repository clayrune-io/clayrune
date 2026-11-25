"""A handoff must carry each user turn's own text
(mc/handoff_turns.py, through `agent_routes._build_handoff_context`).

2026-11-25: a rolled session's first user turn was `[Now ...]` + the previous
handoff (4,995 chars incl. footer) + the new message. The transcript reader's
5000-char head cap kept the old handoff and cut the new message off.
"""
import json

from mc import agent_runtime
from mc import handoff_turns

REAL = 'REAL-MESSAGE entry signal #247, decide GO or PASS'


def _nested_handoff(total):
    """A previous handoff block of exactly `total` chars, header to footer."""
    head = ('=== Prior conversation, started on claude (session 3f4dbc51), '
            'handed off here ===\n\nUser: NESTED-OLD-TURN ')
    tail = f'\n\n{agent_runtime.HANDOFF_FOOTER}'
    return head + 'x' * (total - len(head) - len(tail)) + tail


def _handoff(tmp_path, monkeypatch, *user_turns):
    from mc.blueprints import agent_routes as ar
    transcript = tmp_path / 'transcript.jsonl'
    rows = [{'type': 'user', 'message': {'role': 'user', 'content': t}}
            for t in user_turns]
    transcript.write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
    runtime = agent_runtime.get_runtime('claude')
    monkeypatch.setattr(runtime, 'transcript_path', lambda pp, sid: transcript)
    return ar._build_handoff_context(str(tmp_path), 'claude', 'native-abc')[0]


def test_message_after_a_long_nested_handoff_survives(tmp_path, monkeypatch):
    nested = _nested_handoff(4995)
    assert len(nested) == 4995
    turn = f'[Now 2026-11-25 07:40 PST]\n\n{nested}\n\n{REAL}'
    assert turn.index(REAL) > 5000, 'fixture must cross the old head cap'

    out = _handoff(tmp_path, monkeypatch, turn)

    assert f'User: {REAL}' in out
    # Review F4: the previous handoff's turn rides along as labelled earlier
    # history, ahead of the message and never in place of it.
    assert out.count('NESTED-OLD-TURN') == 1
    assert (out.index(handoff_turns.EARLIER_HEAD) < out.index('NESTED-OLD-TURN')
            < out.index(handoff_turns.EARLIER_END) < out.index(f'User: {REAL}'))
    assert '[Now ' not in out
    assert out.count('=== Prior conversation') == 1
    assert out.count(agent_runtime.HANDOFF_FOOTER) == 1


def test_carry_note_ahead_of_the_message_is_stripped(tmp_path, monkeypatch):
    turn = (f'{_nested_handoff(600)}\n\n[Note: session abc123 this handoff is '
            f'carried from produced no turns]\n\n{REAL}')
    assert f'User: {REAL}' in _handoff(tmp_path, monkeypatch, turn)


def test_long_user_text_keeps_both_ends_and_states_the_cut():
    # Review F5: a 9,000-char dispatch brief has its constraints at the top
    # and its ask at the bottom; a tail-only cap dropped the constraints.
    text = 'HEAD-CONSTRAINTS ' + 'y' * 9000 + ' THE-ACTUAL-ASK'
    [(_, out)] = handoff_turns.clean_turns([('user', text)])
    omitted = len(text) - handoff_turns.USER_TURN_CAP
    head, tail = out.split(f'\n[... {omitted} chars omitted from the middle]\n')
    assert head == text[:handoff_turns.USER_HEAD_KEEP]
    assert head.startswith('HEAD-CONSTRAINTS')
    assert tail == text[-(handoff_turns.USER_TURN_CAP - handoff_turns.USER_HEAD_KEEP):]
    assert tail.endswith('THE-ACTUAL-ASK')


def test_user_text_at_the_cap_is_untouched():
    text = 'z' * handoff_turns.USER_TURN_CAP
    assert handoff_turns.clean_turns([('user', text)]) == [('user', text)]


def test_injection_only_turns_drop_and_other_roles_pass_through():
    reply = '[Now 2026-11-25 07:40 PST] quoted by the assistant'
    assert handoff_turns.clean_turns(
        [('user', '[Now 2026-11-25 07:40 PST]'), ('assistant', reply)]
    ) == [('assistant', reply)]


# ── Review F4: history older than one session, and a mid-turn roll's turn ──

def _prior_handoff(*lines):
    body = '\n\n'.join(lines)
    return ('=== Prior conversation, started on claude (session 3f4dbc51), '
            f'handed off here ===\n\n{body}\n\n{agent_runtime.HANDOFF_FOOTER}')


def test_unused_budget_is_filled_from_the_previous_handoff(tmp_path, monkeypatch):
    # A chat that rolls twice with little said in between used to start with
    # nothing from before the previous roll (replay 6200739f: 332 chars).
    nested = _prior_handoff('User: OLDER-ASK build the importer',
                            'Assistant: OLDER-REPLY importer is half built')
    out = _handoff(tmp_path, monkeypatch, f'[Now 2026-11-25 07:40 PST]\n\n{nested}\n\n{REAL}')

    assert out.index(handoff_turns.EARLIER_HEAD) < out.index('User: OLDER-ASK')
    assert (out.index('User: OLDER-ASK') < out.index('Assistant: OLDER-REPLY')
            < out.index(handoff_turns.EARLIER_END) < out.index(f'User: {REAL}'))
    assert out.count('=== Prior conversation') == 1
    assert out.count(agent_runtime.HANDOFF_FOOTER) == 1
    assert '[Now ' not in out


def test_earlier_history_is_newest_first_within_the_budget():
    old = [f'User: OLD-{i} ' + 'p' * 990 for i in range(6)]
    raw = [('user', f'{_prior_handoff(*old)}\n\n{REAL}')]
    section = handoff_turns.earlier_history(raw, 2500)

    assert 'OLD-5' in section and 'OLD-4' in section
    assert 'OLD-0' not in section and 'OLD-1' not in section
    assert section.index('OLD-4') < section.index('OLD-5'), 'kept in reading order'
    assert len(section) <= 2500


def test_earlier_history_is_empty_without_room_or_without_a_handoff():
    raw = [('user', f"{_prior_handoff('User: OLDER-ASK')}\n\n{REAL}")]
    assert handoff_turns.earlier_history(raw, 0) == ''
    assert handoff_turns.earlier_history([('user', REAL)], 4000) == ''
    assert handoff_turns.earlier_history([], 4000) == ''


def test_earlier_history_does_not_nest_its_own_frame():
    # Third roll: the previous handoff already carried an earlier-history
    # section. Its turns join this one's; its frame lines do not.
    nested = _prior_handoff(
        f'{handoff_turns.EARLIER_HEAD}\nUser: OLDEST-ASK\n{handoff_turns.EARLIER_END}',
        'User: OLDER-ASK', 'Assistant: OLDER-REPLY')
    section = handoff_turns.earlier_history([('user', f'{nested}\n\n{REAL}')], 4000)

    assert section.count(handoff_turns.EARLIER_HEAD) == 1
    assert section.count(handoff_turns.EARLIER_END) == 1
    assert (section.index('User: OLDEST-ASK') < section.index('User: OLDER-ASK')
            < section.index('Assistant: OLDER-REPLY'))


def test_full_own_turns_leave_no_earlier_history(tmp_path, monkeypatch):
    nested = _prior_handoff('User: OLDER-ASK build the importer')
    turns = [f'{nested}\n\n{REAL}'] + [f'OWN-{i} ' + 'q' * 3000 for i in range(4)]
    out = _handoff(tmp_path, monkeypatch, *turns)

    assert 'OLDER-ASK' not in out
    assert handoff_turns.EARLIER_HEAD not in out


def _midturn_first_turn(session):
    from mc import midturn_rollover as mt
    block = mt.build_state_block(session, '/nonexistent-cwd')
    return (f"{_prior_handoff('User: OLDER-ASK')}\n\n{block}\n\n{mt.ROLL_MESSAGE}")


def test_midturn_rolled_first_turn_keeps_the_inflight_and_unanswered_texts():
    # 61963f64 user[0]: 12,775 chars cleaned to 0 and the turn was dropped, so
    # the NEXT handoff opened on an Assistant line with neither text in it.
    turn = _midturn_first_turn({
        'task': 'ORIGINAL-TASK from days ago',
        '_mt_turn_prompt': REAL,
        '_mt_latest_user_msg': 'STEER also check the hedge',
        '_mt_unanswered_msgs': [{'ts': '07:41', 'text': 'STEER also check the hedge'}],
    })
    assert agent_runtime.strip_injected_preamble(turn) == '', 'fixture: all injected'

    [(role, text), reply] = handoff_turns.clean_turns(
        [('user', turn), ('assistant', 'working on it')])

    assert role == 'user' and reply == ('assistant', 'working on it')
    assert text.startswith(REAL)
    assert 'STEER also check the hedge' in text
    assert 'ORIGINAL-TASK' not in text
    assert 'git status' not in text and 'OLDER-ASK' not in text


def test_midturn_rolled_one_prompt_worker_keeps_its_task():
    # AP40: a dispatched worker's first message IS the turn in flight; the
    # state block prints it under "First message" and has no in-flight section.
    turn = _midturn_first_turn({'task': REAL})
    assert handoff_turns.clean_turns([('user', turn)]) == [('user', REAL)]


def test_midturn_rolled_turn_with_nothing_in_flight_still_drops():
    turn = _midturn_first_turn({'task': 'ORIGINAL-TASK from days ago',
                                '_mt_latest_user_msg': 'an answered message'})
    assert handoff_turns.clean_turns([('user', turn)]) == []


def test_a_previous_turn_larger_than_the_room_keeps_both_ends():
    big = 'Assistant: START-OF-REPLY ' + 'r' * 9000 + ' END-OF-REPLY'
    section = handoff_turns.earlier_history(
        [('user', f'{_prior_handoff(big)}\n\n{REAL}')], 3000)

    assert 'Assistant: START-OF-REPLY' in section and 'END-OF-REPLY' in section
    assert 'omitted from the middle' in section
    assert len(section) <= 3000
