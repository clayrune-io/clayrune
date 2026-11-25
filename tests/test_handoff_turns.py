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
    assert 'NESTED-OLD-TURN' not in out
    assert '[Now ' not in out
    assert out.count('=== Prior conversation') == 1
    assert out.count(agent_runtime.HANDOFF_FOOTER) == 1


def test_carry_note_ahead_of_the_message_is_stripped(tmp_path, monkeypatch):
    turn = (f'{_nested_handoff(600)}\n\n[Note: session abc123 this handoff is '
            f'carried from produced no turns]\n\n{REAL}')
    assert f'User: {REAL}' in _handoff(tmp_path, monkeypatch, turn)


def test_long_user_text_keeps_its_end_and_states_the_cut():
    text = 'HEAD ' + 'y' * 9000 + ' THE-ACTUAL-ASK'
    [(_, out)] = handoff_turns.clean_turns([('user', text)])
    assert out.endswith('THE-ACTUAL-ASK')
    assert out.startswith(
        f'[... {len(text) - handoff_turns.USER_TURN_CAP} chars omitted]\n')
    assert 'HEAD' not in out


def test_injection_only_turns_drop_and_other_roles_pass_through():
    reply = '[Now 2026-11-25 07:40 PST] quoted by the assistant'
    assert handoff_turns.clean_turns(
        [('user', '[Now 2026-11-25 07:40 PST]'), ('assistant', reply)]
    ) == [('assistant', reply)]
