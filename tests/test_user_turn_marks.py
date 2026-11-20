"""R34 second-review follow-up: a user prompt keeps its role across the adopt
checkpoint, so `_last_reply_text` still stops at it after a server restart.

The round trip is the real one: `write_checkpoint` -> checkpoint.json on disk
-> `read_checkpoint` -> `restore_session`. No shim process is started; the
ShimProc only supplies the directory and the launch id.

Run:  .venv/Scripts/python.exe -m pytest tests/test_user_turn_marks.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mc import agent_shim_adopt as A  # noqa: E402
from mc import agent_shim_client as C  # noqa: E402
from mc import triggered_turns as tt  # noqa: E402
from mc import user_turn_marks as marks  # noqa: E402
from mc.core import TimestampedLines  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_registry():
    A.reset_for_tests()
    yield
    A.reset_for_tests()


@pytest.fixture
def routes(monkeypatch):
    from mc.blueprints import agent_routes
    monkeypatch.setitem(agent_routes.state.CONFIG, 'user_name', 'Example')
    return agent_routes


def _restart(tmp_path, lines, sid='s_r34'):
    """The buffer as a restarted server gets it back from checkpoint.json."""
    sdir = tmp_path / sid
    sdir.mkdir()
    proc = C.ShimProc(sdir, manifest={'cli_pid': 1, 'shim_pid': 1, 'launch_id': 'L1'})
    session = {'session_id': sid, 'status': 'running', 'project_id': 'p', 'mode': 'A',
               'proc': proc, 'log_lines': TimestampedLines(lines)}
    assert A.write_checkpoint(session)
    ck = A.read_checkpoint(sdir)
    assert ck is not None
    return ck, A.restore_session(ck['session'])


def _queued_follow_up_buffer():
    # Slot 2 is the shape `_auto_dispatch_followup_locked` appends: unframed,
    # and not in slot zero.
    return [tt.user_turn_line('Example', 'first task', framed=False), 'reply one',
            tt.user_turn_line('Example', 'queued follow-up', framed=False), 'reply two']


def test_restored_unframed_follow_up_still_ends_the_reply(routes, tmp_path):
    live = _queued_follow_up_buffer()
    assert routes._last_reply_text({'log_lines': live}) == 'reply two'
    ck, restored = _restart(tmp_path, live)
    assert ck['session']['log_lines']['user_turns'] == [0, 2]
    assert list(restored['log_lines']) == [str(x) for x in live]
    assert routes._last_reply_text(restored) == 'reply two'


def test_restored_reply_that_starts_with_a_quoted_label_is_returned_whole(routes, tmp_path):
    lines = [tt.user_turn_line('Example', 'first task', framed=False), 'reply one',
             tt.user_turn_line('Example', 'queued follow-up', framed=False),
             '> Someone: text', 'rest of the reply']
    _ck, restored = _restart(tmp_path, lines)
    assert routes._last_reply_text(restored) == '> Someone: textrest of the reply'


def test_only_the_kept_tail_is_marked_and_the_marks_follow_it():
    lines = _queued_follow_up_buffer()
    wire = json.loads(json.dumps(A.project_session({'log_lines': TimestampedLines(lines)},
                                                   log_tail=2)))
    assert wire['log_lines']['lines'] == ['> Example: queued follow-up', 'reply two']
    assert wire['log_lines']['user_turns'] == [0]
    back = A.restore_session(wire)['log_lines']
    assert isinstance(back[0], tt.UserTurnLine) and not isinstance(back[1], tt.UserTurnLine)


def test_a_payload_written_before_the_marks_existed_restores_as_before(routes, tmp_path):
    ck, _restored = _restart(tmp_path, _queued_follow_up_buffer())
    old = dict(ck['session'])
    old['log_lines'] = {k: v for k, v in old['log_lines'].items() if k != marks.KEY}
    back = A.restore_session(old)
    assert list(back['log_lines']) == ['> Example: first task', 'reply one',
                                       '> Example: queued follow-up', 'reply two']
    assert not any(isinstance(x, tt.UserTurnLine) for x in back['log_lines'])
    assert isinstance(back['log_lines'], TimestampedLines)


@pytest.mark.parametrize('damaged', [None, 'x', {'0': True}, [True, -1, 99, 1.0, '0', None, [0]]])
def test_damaged_marks_never_raise_and_mark_nothing(damaged):
    lines = ['> Example: task', 'reply']
    out = marks.rewrap(lines, damaged)
    assert out == lines and not any(isinstance(x, tt.UserTurnLine) for x in out)


def test_a_mark_on_a_line_that_is_not_a_rendered_prompt_is_ignored():
    out = marks.rewrap(['> Example: task', 'reply', '\n> Example: more\n'], [0, 1, 2])
    assert [isinstance(x, tt.UserTurnLine) for x in out] == [True, False, True]
