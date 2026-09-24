"""MC-958 — a Mode B turn that ends with a background job still running.

Measured 2026-09-24, claude 2.1.281, the exact Mode B flag shape
(`--print --input-format stream-json --output-format stream-json`), haiku,
task "run `sleep 20; echo done > done.txt` with run_in_background, end the
turn with STARTED, say FINISHED when told it finished". The CLI's stdout, in
order (seconds from start; noise lines elided):

     3.3  assistant  tool_use Bash {run_in_background: true}
     4.0  system     background_tasks_changed  tasks=[bu09z71mg]
     4.0  system     task_started  is_backgrounded=true
     4.0  user       tool_result "Command running in background with ID: ..."
     6.0  assistant  text "STARTED"
     6.2  result     "STARTED"                    <- turn 1 ends
    24.1  system     background_tasks_changed  tasks=[]
    24.1  system     task_updated  patch.status=completed
    24.1  system     task_notification  status=completed
    24.2  system     init                         <- CLI starts a turn ITSELF
    25.6  assistant  text "FINISHED"
    25.7  result     "FINISHED"                   <- the answer

Nothing was written to stdin after the first message: the CLI keeps its
"you will be notified" promise. Clayrune broke it by firing the spawner
callback on the first result (latched, so "FINISHED" never reached the
spawner), leaving the second turn marked `idle`, and letting idle-eviction
kill the waiting process. The lines below reproduce that stream.
"""
from __future__ import annotations

import importlib
import json

import pytest

import mc.background_tasks as bg

SID = 'cli-sess-bg'


def _sys(subtype, **kw):
    return json.dumps({'type': 'system', 'subtype': subtype, 'session_id': SID, **kw})


def _assistant(content, parent=None):
    return json.dumps({'type': 'assistant', 'session_id': SID,
                       'parent_tool_use_id': parent,
                       'message': {'content': content}})


def _result(text):
    return json.dumps({'type': 'result', 'subtype': 'success', 'session_id': SID,
                       'result': text, 'num_turns': 1,
                       'usage': {'input_tokens': 10, 'output_tokens': 5}})


TOOL_ID = 'toolu_013qkxhyvmuPSgV3fLdcs3Dw'
TASK_ID = 'bu09z71mg'
DESC = 'Background task: sleep 20 seconds then write done.txt'

TURN_1 = [
    _sys('init', model='claude-haiku-4-5'),
    _assistant([{'type': 'tool_use', 'id': TOOL_ID, 'name': 'Bash',
                 'input': {'command': 'sleep 20; echo done > done.txt',
                           'run_in_background': True}}]),
    _sys('background_tasks_changed',
         tasks=[{'task_id': TASK_ID, 'task_type': 'local_bash', 'description': DESC}]),
    _sys('task_started', task_id=TASK_ID, tool_use_id=TOOL_ID, description=DESC,
         is_backgrounded=True, task_type='local_bash'),
    json.dumps({'type': 'user', 'session_id': SID, 'message': {'role': 'user', 'content': [
        {'tool_use_id': TOOL_ID, 'type': 'tool_result',
         'content': f'Command running in background with ID: {TASK_ID}.'}]}}),
    _assistant([{'type': 'text', 'text': 'STARTED'}]),
    _result('STARTED'),
]
WAKE = [
    _sys('background_tasks_changed', tasks=[]),
    _sys('task_updated', task_id=TASK_ID, patch={'status': 'completed'}),
    _sys('task_notification', task_id=TASK_ID, tool_use_id=TOOL_ID,
         status='completed', summary=DESC),
    _sys('init', model='claude-haiku-4-5'),
    _assistant([{'type': 'text', 'text': 'FINISHED'}]),
    _result('FINISHED'),
]


class _Proc:
    """stdout is a generator so the test can snapshot session state between
    lines — the reader's status at each boundary is the thing under test."""

    def __init__(self, lines, session, snaps):
        self.pid = -1
        self._lines, self._session, self._snaps = lines, session, snaps
        self.stdout = self._gen()

    def _gen(self):
        for line in self._lines:
            self._snaps.append((line, self._session.get('status')))
            yield line + '\n'
        self._snaps.append(('<eof>', self._session.get('status')))
        # State as the stream ends, BEFORE the reader's exit cleanup runs
        # (a fake proc's EOF is a process death, which drains the tasks).
        self.at_eof = {'status': self._session.get('status'),
                       'deferred': self._session.get(bg.DEFERRED_KEY),
                       'tasks': list(bg.open_tasks(self._session))}

    def wait(self):
        return 0

    def poll(self):
        return None

    def kill(self):
        pass


@pytest.fixture
def ar(tmp_data_dir, monkeypatch):
    server = importlib.import_module('server')
    importlib.reload(server)
    mod = importlib.import_module('mc.blueprints.agent_routes')
    monkeypatch.setattr(mod, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(mod, '_log_agent_completion', lambda s: None)
    monkeypatch.setattr(mod, '_maybe_checkpoint', lambda s: None)
    return mod


def _child(**extra):
    s = {'project_id': 'p-bg', 'session_id': 'child-bg', 'status': 'running',
         'mode': 'B', 'provider': 'claude', 'log_lines': [],
         '_notify_session': 'parent-bg', 'task': 'run the suite',
         'last_output_time': 0.0, 'last_status_change_time': 0.0}
    s.update(extra)
    return s


def _run(ar, session, lines):
    snaps = []
    proc = _Proc(lines, session, snaps)
    session['proc'] = proc
    ar._read_agent_stream_b(proc, session)
    session['_at_eof'] = getattr(proc, 'at_eof', {})
    return snaps


def _capture_notify(ar, monkeypatch):
    sent = []
    monkeypatch.setattr(ar, '_notify_agent_spawner',
                        lambda pid, nsid, child, summary: sent.append(summary))
    return sent


# ── the reader, end to end on the measured stream ───────────────────────────

def test_spawner_gets_the_answer_turn_not_the_waiting_turn(ar, monkeypatch):
    sent = _capture_notify(ar, monkeypatch)
    session = _child()
    _run(ar, session, TURN_1 + WAKE)
    assert len(sent) == 1, sent
    assert 'FINISHED' in sent[0]
    assert 'STARTED' not in sent[0]


def test_first_result_holds_the_callback_while_the_job_runs(ar, monkeypatch):
    sent = _capture_notify(ar, monkeypatch)
    session = _child()
    _run(ar, session, TURN_1)
    assert sent == []
    eof = session['_at_eof']
    assert eof['status'] == 'idle'
    assert eof['deferred']
    assert eof['tasks'] == [TASK_ID]
    assert any('background task(s) still running' in l for l in session['log_lines'])


def test_self_started_turn_is_marked_running_then_idle(ar, monkeypatch):
    _capture_notify(ar, monkeypatch)
    session = _child()
    snaps = _run(ar, session, TURN_1 + WAKE)
    by_line = dict((l, st) for l, st in snaps)
    finished = _assistant([{'type': 'text', 'text': 'FINISHED'}])
    # Before the CLI's own turn produced anything, the session was idle...
    assert by_line[finished] == 'idle'
    # ...and while that turn was running, it said so.
    assert by_line[_result('FINISHED')] == 'running'
    assert session['_at_eof'] == {'status': 'idle', 'deferred': None, 'tasks': []}
    assert any('agent resuming' in l for l in session['log_lines'])


def test_no_background_job_notifies_on_first_result_as_before(ar, monkeypatch):
    sent = _capture_notify(ar, monkeypatch)
    session = _child()
    _run(ar, session, [_assistant([{'type': 'text', 'text': 'all 14 passed'}]),
                       _result('all 14 passed')])
    assert sent == ['all 14 passed']


def test_job_that_finishes_inside_the_turn_does_not_hold(ar, monkeypatch):
    """Notification drained mid-turn (seen in 82b6b6c7): the open set is
    empty again by the result, so the callback fires normally."""
    sent = _capture_notify(ar, monkeypatch)
    session = _child()
    lines = TURN_1[:5] + [
        _sys('background_tasks_changed', tasks=[]),
        _sys('task_notification', task_id=TASK_ID, status='completed', summary=DESC),
        _assistant([{'type': 'text', 'text': 'done, 14 passed'}]),
        _result('done, 14 passed'),
    ]
    _run(ar, session, lines)
    assert sent == ['done, 14 passed']
    assert session['_at_eof']['deferred'] is None


def test_subagent_output_while_idle_does_not_flip_status(ar, monkeypatch):
    """A background Agent streams sidechain messages (parent_tool_use_id set)
    while the main thread is idle. Only main-thread output means a new turn."""
    _capture_notify(ar, monkeypatch)
    session = _child()
    snaps = _run(ar, session, TURN_1 + [
        _assistant([{'type': 'text', 'text': 'sub says hi'}], parent='toolu_sub'),
        _sys('init'),
    ])
    assert snaps[-1] == ('<eof>', 'idle')


def test_process_death_reports_the_jobs_it_took_down(ar, monkeypatch):
    _capture_notify(ar, monkeypatch)
    session = _child()
    _run(ar, session, TURN_1)            # EOF with the job still open
    assert any('they were stopped with it' in l and DESC[:40] in l
               for l in session['log_lines'])
    assert bg.open_tasks(session) == {}


def test_respawned_reader_reports_leftover_jobs(ar, monkeypatch):
    _capture_notify(ar, monkeypatch)
    session = _child(**{bg.TASKS_KEY: {TASK_ID: {'description': DESC, 'since': 1.0}}})
    _run(ar, session, [])
    assert any('did not survive the previous process' in l for l in session['log_lines'])


# ── idle-eviction and the wait cap ──────────────────────────────────────────

class _Alive:
    def poll(self):
        return None


NOW = 1_000_000.0


def _idle_waiting(minutes_waiting):
    return {'status': 'idle', 'mode': 'B', 'proc': _Alive(),
            'last_output_time': NOW - 90 * 60,
            bg.TASKS_KEY: {TASK_ID: {'description': DESC,
                                     'since': NOW - minutes_waiting * 60}},
            bg.DEFERRED_KEY: NOW - minutes_waiting * 60}


def test_eviction_skips_a_session_waiting_on_its_job(ar):
    assert ar._should_evict_idle_session(_idle_waiting(90), NOW, True, 60, 120) is False


def test_eviction_resumes_once_the_wait_cap_passes(ar):
    assert ar._should_evict_idle_session(_idle_waiting(121), NOW, True, 60, 120) is True


def test_eviction_unchanged_without_background_jobs(ar):
    s = _idle_waiting(90)
    s[bg.TASKS_KEY] = {}
    assert ar._should_evict_idle_session(s, NOW, True, 60, 120) is True


def test_wait_cap_sends_interim_then_rearms_for_the_real_answer(ar, monkeypatch):
    sent = _capture_notify(ar, monkeypatch)
    rearmed = []
    monkeypatch.setattr(ar, '_advance_delegation_turn', lambda s: rearmed.append(1))
    monkeypatch.setitem(ar.state.CONFIG, 'background_wait_max_minutes', 120)
    session = _child()
    _run(ar, session, TURN_1[:-1])      # up to (not incl.) the first result
    # Replay the first result + an overdue cap, without EOF draining the job.
    ar._hold_notify_for_background(session)
    session['status'] = 'idle'
    session[bg.DEFERRED_KEY] = NOW - 121 * 60
    session['log_lines'].append('STARTED')
    assert ar._release_held_notify_if_expired(session, NOW) is True
    assert len(sent) == 1 and sent[0].startswith('[interim: still waiting')
    assert session.get(bg.INTERIM_KEY) is True
    # The job finally ends; the CLI's own turn must reach the spawner too.
    _run(ar, session, WAKE)
    assert rearmed == [1]
    assert len(sent) == 2 and 'FINISHED' in sent[1]


def test_wait_cap_not_reached_sends_nothing(ar, monkeypatch):
    sent = _capture_notify(ar, monkeypatch)
    monkeypatch.setitem(ar.state.CONFIG, 'background_wait_max_minutes', 120)
    session = _child(status='idle', **{bg.DEFERRED_KEY: NOW - 30 * 60})
    assert ar._release_held_notify_if_expired(session, NOW) is False
    assert sent == []


# ── the pure tracker ────────────────────────────────────────────────────────

def test_foreground_task_started_is_not_tracked():
    """Measured: a foreground Bash that hits its timeout emits task_started
    with is_backgrounded=false and a `failed` task_notification mid-turn."""
    s = {}
    bg.note_system_event(s, json.loads(_sys('task_started', task_id='b69', is_backgrounded=False)))
    assert bg.open_tasks(s) == {}


def test_changed_list_is_authoritative_and_keeps_first_seen():
    s = {}
    bg.note_system_event(s, json.loads(TURN_1[2]), now=10.0)
    bg.note_system_event(s, json.loads(TURN_1[3]), now=20.0)
    assert bg.open_tasks(s)[TASK_ID]['since'] == 10.0
    bg.note_system_event(s, json.loads(WAKE[0]), now=30.0)
    assert bg.open_tasks(s) == {}
    assert bg.note_system_event(s, json.loads(WAKE[2])) == 'notification'


def test_zero_cap_means_no_cap():
    assert bg.wait_expired({bg.DEFERRED_KEY: 1.0}, 10_000_000.0, 0) is False
