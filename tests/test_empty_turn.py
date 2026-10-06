"""Backlog d37d0183: a turn with no visible reply and no tool calls is a
failure, not a 'completed'.

Incident (2026-10-05, qwen3:8b via Ollama): the turn ended with zero tool
calls and only an ```mc:todo``` block. rc == 0, so `_mode_a_reader` wrote
status 'completed'; the chat showed nothing; the spawner callback read
"ended with status=completed" over an empty final message.

Pinned here, all through the REAL readers / payload builder (no stand-ins for
the code under test):
  * the rule itself (mc/empty_turn.py): empty vs tool-only vs question-paused
  * the shared Mode-A reader (qwen/codex/opencode/...) flips status to 'error',
    writes a bracketed chat line, and the agent-log row + callback say so
  * a tool-only turn and an mc:question turn are NOT flagged
  * the Claude turn-boundary verdict (`_apply_mc_tool_blocks_for_turn`)
  * a new turn clears a stale flag
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402
from mc import empty_turn  # noqa: E402
from mc.delegation_delivery import callback_payload  # noqa: E402
from tests.test_runtime_completion_log import (  # noqa: E402,F401
    _FakeRuntime, env, _dispatch, _log_rows)

TODO_ONLY = ('__MSG__:```mc:todo\n{"todos": [{"content": "x", '
             '"status": "pending"}]}\n```')
QUESTION = ('__MSG__:```mc:question\n{"questions": [{"header": "H", '
            '"question": "Which?", "options": [{"label": "A", '
            '"description": "a"}]}]}\n```')


class _ToolRuntime(_FakeRuntime):
    """Adds a `__TOOL__` line that yields a real TOOL_USE event."""

    def parse_event(self, line, mc_session_id):
        if line.startswith('__TOOL__'):
            return agent_runtime_mod.AgentEvent(
                type=agent_runtime_mod.EventType.TOOL_USE, provider=self.name,
                session_id=None, mc_session_id=mc_session_id, timestamp='',
                payload={'blocks': [{'type': 'tool_use', 'name': 'Bash',
                                     'input': {'command': 'ls'}}]})
        return super().parse_event(line, mc_session_id)


# ── the rule ────────────────────────────────────────────────────────────────

def test_rule_flags_only_the_silent_turn():
    r = empty_turn.empty_reason
    assert r(visible_text='', tool_calls=0, paused=False)
    assert r(visible_text='  \n ', tool_calls=0, paused=False)
    assert r(visible_text='hi', tool_calls=0, paused=False) is None
    assert r(visible_text='', tool_calls=2, paused=False) is None
    assert r(visible_text='', tool_calls=0, paused=True) is None


def test_control_only_reason_names_the_control_blocks():
    assert 'mc control blocks' in empty_turn.empty_reason(
        visible_text='', tool_calls=0, paused=False, had_control_blocks=True)


def test_chat_line_is_bracketed_so_it_is_never_taken_for_the_reply():
    line = empty_turn.line_for('why')
    assert line.startswith('[') and line.endswith(']')
    assert agent_runtime_mod._collect_trailing_reply_text([line]) == ''


# ── shared Mode-A reader (the incident's path) ──────────────────────────────

def test_mc_todo_only_turn_is_an_error_not_a_completion(env):
    env['runtime'].__class__ = _ToolRuntime
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, [TODO_ONLY], rc=0)

    sess = env['sessions'][sid]
    assert sess['status'] == 'error'
    assert 'only mc control blocks' in sess[empty_turn.EMPTY_KEY]
    assert any(l.startswith('[ended with no reply') for l in sess['log_lines'])
    rows = _log_rows(env)
    assert len(rows) == 1
    assert rows[0]['status'] == 'error'
    assert rows[0]['summary'].startswith('[ended with no reply')


def test_no_output_at_all_is_flagged(env):
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, [], rc=0)
    assert env['sessions'][sid]['status'] == 'error'
    assert 'no reply text' in env['sessions'][sid][empty_turn.EMPTY_KEY]


def test_tool_only_turn_still_completes(env):
    env['runtime'].__class__ = _ToolRuntime
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['__TOOL__'], rc=0)
    sess = env['sessions'][sid]
    assert sess['status'] == 'completed'
    assert empty_turn.EMPTY_KEY not in sess


def test_tool_call_plus_mc_todo_still_completes(env):
    env['runtime'].__class__ = _ToolRuntime
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['__TOOL__', TODO_ONLY], rc=0)
    assert env['sessions'][sid]['status'] == 'completed'


def test_reply_text_turn_still_completes(env):
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, ['__MSG__:All done.'], rc=0)
    assert env['sessions'][sid]['status'] == 'completed'


def test_mc_question_turn_is_paused_not_flagged(env):
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, [QUESTION], rc=0)
    sess = env['sessions'][sid]
    assert sess['status'] == 'idle'
    assert empty_turn.EMPTY_KEY not in sess


def test_nonzero_exit_keeps_its_own_failure_path(env):
    """rc != 0 already reads as an error with its own `[... exited with code]`
    line; the empty-turn line must not pile a second verdict on top."""
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, [], rc=1)
    sess = env['sessions'][sid]
    assert sess['status'] == 'error'
    assert empty_turn.EMPTY_KEY not in sess


def test_new_turn_clears_a_stale_flag(env):
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, [], rc=0)
    assert empty_turn.EMPTY_KEY in env['sessions'][sid]
    env['runtime'].run_turn(handle, ['__MSG__:Now I answer.'], rc=0)
    sess = env['sessions'][sid]
    assert empty_turn.EMPTY_KEY not in sess
    assert sess['status'] == 'completed'


# ── spawner callback ────────────────────────────────────────────────────────

def test_callback_says_the_turn_ended_empty(env):
    sid, handle = _dispatch(env)
    env['runtime'].run_turn(handle, [TODO_ONLY], rc=0)
    child = env['sessions'][sid]
    payload = callback_payload(child, '', 'evt-1')
    assert payload['status'] == 'error'
    assert 'status=error' in payload['message']
    assert 'ended with no reply' in payload['message']
    assert 'status=completed' not in payload['message']


def test_callback_for_a_normal_turn_is_unchanged():
    child = {'session_id': 's' * 12, 'status': 'completed', 'task': 't',
             'provider': 'qwen'}
    payload = callback_payload(child, 'the answer', 'evt-2')
    assert payload['status'] == 'completed'
    assert payload['summary'] == 'the answer'


# ── Claude turn boundary (Mode A result event / Mode B result event) ───────

@pytest.fixture()
def ar_mod():
    import server  # noqa: F401
    from mc.blueprints import agent_routes
    return agent_routes


def _claude_session(**kw):
    return {'session_id': 'c' * 12, 'project_id': 'proj1', 'status': 'running',
            'log_lines': [], **kw}


def test_claude_turn_with_no_text_and_no_tools_is_flagged(ar_mod):
    s = _claude_session()
    ar_mod._apply_mc_tool_blocks_for_turn(s)
    assert s.get(empty_turn.EMPTY_KEY)
    assert s['status'] == 'running'  # status is the reader's call, not the verdict's
    assert any(l.startswith('[ended with no reply') for l in s['log_lines'])


def test_claude_mc_todo_only_turn_is_flagged(ar_mod):
    s = _claude_session(_mc_turn_buf=[
        '```mc:todo\n{"todos": []}\n```'])
    ar_mod._apply_mc_tool_blocks_for_turn(s)
    assert 'only mc control blocks' in s[empty_turn.EMPTY_KEY]


def test_claude_tool_only_turn_is_not_flagged(ar_mod):
    s = _claude_session()
    empty_turn.note_tool_call(s)
    ar_mod._apply_mc_tool_blocks_for_turn(s)
    assert empty_turn.EMPTY_KEY not in s
    assert empty_turn.TOOL_COUNT_KEY not in s  # counter resets per turn


def test_claude_turn_with_reply_is_not_flagged(ar_mod):
    s = _claude_session(_mc_turn_buf=['Here is the answer.'])
    ar_mod._apply_mc_tool_blocks_for_turn(s)
    assert empty_turn.EMPTY_KEY not in s


def test_claude_interrupted_turn_is_not_flagged(ar_mod):
    s = _claude_session(_interrupted=True)
    ar_mod._apply_mc_tool_blocks_for_turn(s)
    assert empty_turn.EMPTY_KEY not in s


def test_claude_question_turn_is_not_flagged(ar_mod):
    s = _claude_session(_mc_turn_buf=[
        '```mc:question\n{"questions": [{"header": "H", "question": "Q?", '
        '"options": [{"label": "A", "description": "a"}]}]}\n```'])
    ar_mod._apply_mc_tool_blocks_for_turn(s)
    assert empty_turn.EMPTY_KEY not in s
    assert s['waiting_for_question']
