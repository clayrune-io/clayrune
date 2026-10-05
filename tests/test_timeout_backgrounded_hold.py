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

import mc.background_tasks as bg
from tests.test_background_task_wake import (  # noqa: F401 — reuse fixture
    SID, TURN_1, WAKE, _assistant, _capture_notify, _child, _result, _run, _sys, ar)

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


# What the CLI emits when the stray task is killed 20 min later, and the turn
# it then starts by itself (2026-10-05, 20:00:19Z / 20:00:22Z).
KILL_WAKE_TEXT = 'That notification is the stray `python -` command I started by mistake.'
KILL_WAKE = [
    _sys('background_tasks_changed', tasks=[]),
    _sys('task_updated', task_id=TASK_ID, patch={'status': 'killed'}),
    _sys('task_notification', task_id=TASK_ID, tool_use_id=TOOL_ID,
         status='killed', summary=CMD),
    _sys('init', model='claude-sonnet-5-5'),
    _assistant([{'type': 'text', 'text': KILL_WAKE_TEXT}]),
    _result(KILL_WAKE_TEXT),
]


def _feed(session, lines):
    """Run stream lines through the tracker only (no reader, so no process
    death drain) and return the session state they leave behind."""
    for line in lines:
        msg = json.loads(line)
        if msg.get('type') == 'assistant':
            for block in msg['message']['content']:
                if block.get('type') == 'tool_use':
                    bg.note_tool_use(session, block)
        else:
            bg.note_system_event(session, msg)
    return session


def test_spawner_gets_the_real_answer_not_the_kill_wake_turn(ar, monkeypatch):
    """The 2026-10-05 stream end to end: one callback, at the real turn end,
    carrying the real answer; the later self-started turn adds nothing."""
    sent = _capture_notify(ar, monkeypatch)
    session = _child()
    _run(ar, session, TURN + KILL_WAKE)
    assert len(sent) == 1, sent
    assert '0f261a89' in sent[0]
    assert 'stray' not in sent[0]


def test_timeout_moved_task_ending_does_not_rearm_the_callback(ar, monkeypatch):
    """The wake turn runs, but it is not announced to the spawner: the latch
    stays set and the wake text never becomes a callback."""
    sent = _capture_notify(ar, monkeypatch)
    session = _child()
    _run(ar, session, TURN + KILL_WAKE)
    assert session.get('_notify_session_sent')
    assert not any(KILL_WAKE_TEXT in x for x in sent)


def test_run_in_background_task_still_holds_then_delivers(ar, monkeypatch):
    """The deliberate case MC-958 exists for: held at the first result, and
    the answer turn the CLI runs when the job ends is what the spawner gets."""
    sent = _capture_notify(ar, monkeypatch)
    session = _child()
    _run(ar, session, TURN_1)
    assert sent == []
    assert session['_at_eof']['deferred']
    assert session['_at_eof']['tasks'] == ['bu09z71mg']
    _run(ar, session, WAKE)
    assert len(sent) == 1, sent
    assert 'FINISHED' in sent[0]


def test_deliberate_task_alongside_a_timeout_moved_one_still_holds(ar, monkeypatch):
    both = TURN[:-2] + [
        TURN_1[1],
        _sys('background_tasks_changed', tasks=[
            {'task_id': TASK_ID, 'task_type': 'local_bash', 'description': CMD},
            {'task_id': 'bu09z71mg', 'task_type': 'local_bash', 'description': 'sleep'}]),
        TURN_1[3],
        _assistant([{'type': 'text', 'text': ANSWER}]), _result(ANSWER)]
    sent = _capture_notify(ar, monkeypatch)
    session = _child()
    _run(ar, session, both)
    assert sent == []
    assert sorted(session['_at_eof']['tasks']) == sorted([TASK_ID, 'bu09z71mg'])
    state = _feed({}, both)
    assert list(bg.held_tasks(state)) == ['bu09z71mg']


def test_task_of_unknown_origin_keeps_holding():
    """No tool_use seen (e.g. the process was resumed): classification is
    unavailable, so the old, safe behaviour applies."""
    s = {}
    bg.note_system_event(s, json.loads(_sys('task_started', task_id='t1',
                                           tool_use_id='toolu_unseen',
                                           description='x', is_backgrounded=True)))
    assert list(bg.held_tasks(s)) == ['t1']


def test_non_shell_background_tool_holds():
    s = {}
    bg.note_tool_use(s, {'id': 'tu1', 'name': 'Monitor', 'input': {'command': 'tail -f x'}})
    bg.note_system_event(s, json.loads(_sys('task_started', task_id='t1', tool_use_id='tu1',
                                           description='watch', is_backgrounded=True)))
    assert list(bg.held_tasks(s)) == ['t1']


def test_tool_use_map_is_bounded():
    s = {}
    for i in range(200):
        bg.note_tool_use(s, {'id': f'tu{i}', 'name': 'Bash', 'input': {}})
    assert len(s[bg.TOOL_USES_KEY]) == 64 and 'tu199' in s[bg.TOOL_USES_KEY]


# ── "waiting on background task" is visible while a callback is held ────────

def test_waiting_label_only_while_held_and_idle():
    held = _feed({'status': 'idle', bg.DEFERRED_KEY: 1.0}, TURN_1)
    label = bg.waiting_label(held)
    assert label.startswith('waiting on background task: ')
    assert 'sleep 20' in label
    assert bg.waiting_label(held | {'status': 'running'}) == ''
    # A timeout-moved task never produces the label: nothing is held.
    other = _feed({'status': 'idle', bg.DEFERRED_KEY: 1.0}, TURN)
    assert bg.waiting_label(other) == ''


def test_floor_figure_and_status_carry_the_label():
    from mc.blueprints import floor_routes
    s = {'session_id': 's1', 'project_id': 'p', 'status': 'idle', 'log_lines': [],
         bg.TASKS_KEY: {'t1': {'description': 'npm test', 'task_type': 'local_bash',
                               'since': 1.0}},
         bg.DEFERRED_KEY: 1.0}
    assert floor_routes._figure(s)['bg_wait'] == 'waiting on background task: npm test'
    s['status'] = 'running'
    assert floor_routes._figure(s)['bg_wait'] == ''
