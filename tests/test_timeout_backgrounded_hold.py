"""Late spawner callback, 2026-10-05: child 302c540ba1e4 (Tobin) -> Dave.

The child's final answer ended its turn at 19:49:12Z (transcript
54dab546, "MC-1047 audit fixes are committed as `0f261a89`..."), but the
spawner callback was only enqueued at 20:00:22Z, as event
`302c540ba1e4:turn:2`, carrying the reply to a task_notification instead:
"That notification is the stray `python -` command I started by mistake".

Mechanism (MC-958 hold, not the mid-turn rollover): at 19:39:44Z the child
ran a FOREGROUND Bash (`python - < /dev/null; grep ...`, no
`run_in_background`). It hung, hit the 120s tool timeout, and the CLI moved
it to the background (task bc7swf1px). That was the only background task in
the post-rollover transcript. When the turn's `result` arrived, the open set
was non-empty, so `_hold_notify_for_background` held the callback. The task
was reported `killed` at 20:00:19Z; the CLI ran its own wake turn; that
turn's result (20:00:22Z) released the callback with the wrong reply.

The agent never chose to wait on that command: it had moved on and written
its final answer. These tests pin the behaviour the proposed fix should
give: an auto-backgrounded (timeout-moved) task does not hold the callback,
while a deliberate `run_in_background` job still does (covered by
test_background_task_wake.py, unchanged).
"""
from __future__ import annotations

import json

from tests.test_background_task_wake import (  # noqa: F401 — reuse fixture
    SID, _assistant, _capture_notify, _child, _result, _run, _sys, ar)

TOOL_ID = 'toolu_01LjbVo2UAj6F8zwakkhko3v'
TASK_ID = 'bc7swf1px'
CMD = 'python - < /dev/null; grep -n "MC-1047" docs/DESK_CONNECT_BY_URL_SPEC.md'
ANSWER = 'MC-1047 audit fixes are committed as `0f261a89`, not merged or pushed.'

# The 2026-10-05 stream shape: foreground tool_use (no run_in_background),
# the timeout move, then the final answer.
TURN = [
    _sys('init', model='claude-sonnet-5-5'),
    _assistant([{'type': 'tool_use', 'id': TOOL_ID, 'name': 'Bash',
                 'input': {'command': CMD}}]),
    _sys('background_tasks_changed',
         tasks=[{'task_id': TASK_ID, 'task_type': 'local_bash', 'description': CMD}]),
    _sys('task_started', task_id=TASK_ID, tool_use_id=TOOL_ID, description=CMD,
         is_backgrounded=True, task_type='local_bash'),
    json.dumps({'type': 'user', 'session_id': SID, 'message': {'role': 'user', 'content': [
        {'tool_use_id': TOOL_ID, 'type': 'tool_result',
         'content': ('Command did not complete within its 120s timeout and was '
                     f'moved to the background (ID: {TASK_ID}). You will be '
                     'notified when it completes.')}]}}),
    _assistant([{'type': 'text', 'text': ANSWER}]),
    _result(ANSWER),
]


def test_timeout_moved_task_does_not_hold_the_final_answer(ar, monkeypatch):
    """FAILS on master: the callback is held until the stray task ends."""
    sent = _capture_notify(ar, monkeypatch)
    session = _child()
    _run(ar, session, TURN)
    assert len(sent) == 1, (
        'spawner callback held by an auto-backgrounded foreground command; '
        f"log tail: {list(session['log_lines'])[-2:]}")
    assert '0f261a89' in sent[0]
