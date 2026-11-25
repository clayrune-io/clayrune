"""A revived Claude chat records the message that opens its turn.

2026-11-25 review, F3: `_revive_from_agent_log` recorded no latest message,
so a mid-turn roll in the first turn after a restart presented the chat's
ORIGINAL task as "the current task; no later message was recorded".
"""
import pytest

from mc import midturn_rollover as mt
from tests.test_effort_continuity import captured_flags  # noqa: F401  (fixture)
from tests.test_revive_notify_carry import ar, _project, CSID  # noqa: F401

ORIGINAL = 'ORIGINAL-TASK set up the tracker'
MESSAGE = 'ENTRY #247 CRWD, decide GO or PASS'


@pytest.mark.parametrize('streaming', [False, True])
def test_revive_records_the_opening_message_as_latest_and_in_flight(
        ar, monkeypatch, tmp_path, captured_flags, streaming):
    monkeypatch.setitem(ar.state.CONFIG, 'use_streaming_agent', streaming)
    monkeypatch.setitem(ar.state.CONFIG, 'midturn_rollover_enabled', True)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **kw: None)
    monkeypatch.setattr(mt, '_git', lambda *a: '(clean)')
    row = dict(session_id='s1', claude_session_id=CSID, model='sonnet', task=ORIGINAL)
    monkeypatch.setattr(ar, '_load_agent_log', lambda _, **_kw: [row])

    session = ar._revive_from_agent_log('p1', 's1', MESSAGE, _project(tmp_path))

    assert session is not None
    assert session['_mt_turn_prompt'] == MESSAGE
    assert session['_mt_latest_user_msg'] == MESSAGE
    block = mt.build_state_block(session, str(tmp_path))
    assert block.index(MESSAGE) < block.index('--- Unanswered user messages')
    assert 'NOT answered yet; finish it' in block
    assert 'no later message was recorded' not in block
    assert '(none recorded after the first message)' not in block
