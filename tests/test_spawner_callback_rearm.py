"""Lost spawner callbacks (Bram's diagnosis, session 12c4c9228d07, 2026-10-05).

A dispatched child with notify_session sends ONE '[dispatched agent finished]'
callback. If it was later woken by something that is not a Clayrune send (a
Claude Code SendMessage peer message, a background-task wake after the final
callback) its next finished turn sent nothing: `_notify_session_sent` was
still set and `_note_self_started_turn` only re-armed it after a wait-cap
interim report. At least 6 callbacks lost 2026-09-29..10-05.

A: a self-started turn re-arms the spawner callback -- exactly one callback
   per such turn -- unless it continues a HUMAN turn chain. Who owns the chain
   (`_turn_owner`) is tracked apart from the delivery latch: a background-task
   wake inherits the owner of the turn it continues, a peer SendMessage wake
   is always the spawner's.
C: an inbox row whose handoff failed BEFORE the parent was handed the message
   is retried; one that may have been handed over is not.
D: a callback suppressed by a latch is logged with session and reason.
"""
from __future__ import annotations

import importlib
import json

import pytest

import mc.state as state
from mc.delegation_delivery import (DeliveryBlocked, DeliveryNotHandedOff,
                                    DeliveryStore, DeliveryUncertain,
                                    drain_once)

state.CONFIG.setdefault('port', 5199)

SID = 'cli-sess-wake'


def _assistant(text, parent=None):
    return json.dumps({'type': 'assistant', 'session_id': SID,
                       'parent_tool_use_id': parent,
                       'message': {'content': [{'type': 'text', 'text': text}]}})


def _result(text):
    return json.dumps({'type': 'result', 'subtype': 'success', 'session_id': SID,
                       'result': text, 'num_turns': 1,
                       'usage': {'input_tokens': 10, 'output_tokens': 5}})


class _Proc:
    pid = -1

    def __init__(self, lines):
        self.stdout = self._gen(lines)

    @staticmethod
    def _gen(lines):
        # A callable element runs between stream lines (what a Clayrune send
        # does to the session while the process sits idle); it is not output.
        for l in lines:
            if callable(l):
                l()
                continue
            yield l + '\n'

    def wait(self):
        return 0

    def poll(self):
        return None

    def kill(self):
        pass


@pytest.fixture
def ar(tmp_data_dir, tmp_path, monkeypatch):
    server = importlib.import_module('server')
    importlib.reload(server)
    mod = importlib.import_module('mc.blueprints.agent_routes')
    monkeypatch.setattr(mod, '_write_session_memory', lambda *a, **k: True)
    monkeypatch.setattr(mod, '_log_agent_completion', lambda s: None)
    monkeypatch.setattr(mod, '_maybe_checkpoint', lambda s: None)
    monkeypatch.setattr(mod, '_delivery_store', DeliveryStore(tmp_path / 'd.db'))
    return mod


def _child(**extra):
    s = {'project_id': 'p-wake', 'session_id': 'child-wake', 'status': 'running',
         'mode': 'B', 'provider': 'claude', 'log_lines': [],
         '_notify_session': 'parent-wake', 'task': 'build it',
         'last_output_time': 0.0, 'last_status_change_time': 0.0}
    s.update(extra)
    return s


def _capture(ar, monkeypatch):
    """Record (turn, summary) of every callback handed to the durable queue."""
    sent = []
    monkeypatch.setattr(
        ar, '_notify_agent_spawner',
        lambda pid, nsid, child, summary: sent.append((child.get('_delegation_turn', 1), summary)))
    return sent


def _run(ar, session, lines):
    proc = _Proc(lines)
    session['proc'] = proc
    ar._read_agent_stream_b(proc, session)
    # A crash inside the reader is caught and logged as a stream-error line,
    # which silently ends the stream: every test that reaches here must prove
    # it was consumed end to end.
    assert not [l for l in session['log_lines'] if l.startswith('[stream error')], \
        session['log_lines']


def _sys(subtype, **kw):
    return json.dumps({'type': 'system', 'subtype': subtype, 'session_id': SID, **kw})


TASK_ID = 'bu09z71mg'
JOB_STARTED = _sys('task_started', task_id=TASK_ID, description='sleep 20',
                   is_backgrounded=True, task_type='local_bash')
JOB_DONE = [_sys('background_tasks_changed', tasks=[]),
            _sys('task_updated', task_id=TASK_ID, patch={'status': 'completed'}),
            _sys('task_notification', task_id=TASK_ID, status='completed',
                 summary='sleep 20')]


def _human_followup(ar, session):
    """What `agent_followup` does to a live idle session when Ron types and
    sends no `notify_session`: the turn starts running, the owner is human."""
    def go():
        session['status'] = 'running'
        session['log_lines'].append('[follow-up sent]')   # the boundary a real send leaves
        ar._advance_delegation_turn(session)
    return go


def _agent_followup(ar, session):
    """The same send with an explicit `notify_session`: agent-authorized."""
    def go():
        session['status'] = 'running'
        session['log_lines'].append('[follow-up sent]')   # the boundary a real send leaves
        ar._rearm_notify_for_new_turn(session)
    return go


# ── A ───────────────────────────────────────────────────────────────────────

def test_peer_message_wake_then_finish_sends_one_new_callback(ar, monkeypatch):
    sent = _capture(ar, monkeypatch)
    session = _child()
    ar._allocate_delegation_turn(session)      # what dispatch did: turn 1
    # The process lives on between turns (Mode B). A SendMessage peer message
    # wakes the idle child: Clayrune sent nothing, so the second turn starts
    # with assistant output while status is 'idle'.
    _run(ar, session, [_assistant('first answer'), _result('first answer'),
                       _assistant('peer-woken answer'), _result('peer-woken answer')])
    assert [s for _, s in sent] == ['first answer', 'peer-woken answer']
    # A fresh durable turn, or the delegation store would dedup the event.
    assert sent[1][0] > sent[0][0]


def test_second_finish_without_a_new_wake_sends_nothing(ar, monkeypatch):
    sent = _capture(ar, monkeypatch)
    session = _child()
    _run(ar, session, [_assistant('one'), _result('one'),
                       _assistant('two'), _result('two'),       # woken
                       _result('two again')])                    # no assistant output
    assert [s for _, s in sent] == ['one', 'two']


def test_clayrune_sent_turn_is_not_a_self_started_turn(ar, monkeypatch):
    """A human follow-up (MC-970) arrives with status 'running' and must NOT
    re-arm: Ron typing into a finished dispatch does not call Dave back."""
    sent = _capture(ar, monkeypatch)
    session = _child()
    _run(ar, session, [_assistant('one'), _result('one'),
                       _human_followup(ar, session),
                       _assistant('human answer'), _result('human answer')])
    assert 'human answer' in session['log_lines']        # the stream was consumed
    assert [s for _, s in sent] == ['one']


def test_human_followup_then_background_wake_sends_nothing(ar, monkeypatch):
    """Fenn's P1: Ron's follow-up launches a background task; the CLI wakes
    itself when it ends. That wake is Ron's work and must not reach Dave."""
    sent = _capture(ar, monkeypatch)
    session = _child()
    _run(ar, session, [_assistant('one'), _result('one'),
                       _human_followup(ar, session),
                       _assistant('starting job'), JOB_STARTED, _result('started job'),
                       *JOB_DONE,
                       _assistant('job done'), _result('job done')])
    assert 'job done' in session['log_lines']
    assert [s for _, s in sent] == ['one']
    assert session['_turn_owner'] == 'human'


def test_background_wake_after_a_spawner_turn_sends_a_new_callback(ar, monkeypatch):
    sent = _capture(ar, monkeypatch)
    session = _child()
    ar._allocate_delegation_turn(session)
    _run(ar, session, [_assistant('one'), _result('one'),
                       *JOB_DONE,
                       _assistant('woken answer'), _result('woken answer')])
    assert [s for _, s in sent] == ['one', 'woken answer']
    assert sent[1][0] > sent[0][0]
    assert session['_turn_owner'] == 'spawner'


def test_background_wake_after_an_agent_followup_sends_a_new_callback(ar, monkeypatch):
    """An explicit-notify follow-up (`_rearm_notify_for_new_turn`) is the
    spawner's, so a task it launches reports back when its wake finishes."""
    sent = _capture(ar, monkeypatch)
    session = _child()
    _run(ar, session, [_assistant('one'), _result('one'),
                       _agent_followup(ar, session),
                       _assistant('follow-up answer'), _result('follow-up answer'),
                       *JOB_DONE,
                       _assistant('woken answer'), _result('woken answer')])
    assert [s for _, s in sent] == ['one', 'follow-up answer', 'woken answer']


def test_peer_wake_after_a_human_turn_rearms(ar, monkeypatch):
    """Decided 2026-10-05: a SendMessage peer wake is an agent talking to the
    child and its sender is waiting, so it re-arms even after Ron's turn."""
    sent = _capture(ar, monkeypatch)
    session = _child()
    _run(ar, session, [_assistant('one'), _result('one'),
                       _human_followup(ar, session),
                       _assistant('human answer'), _result('human answer'),
                       _assistant('peer answer'), _result('peer answer')])
    assert [s for _, s in sent] == ['one', 'peer answer']
    assert session['_turn_owner'] == 'spawner'


def test_background_wake_after_a_peer_wake_stays_spawner_owned(ar, monkeypatch):
    sent = _capture(ar, monkeypatch)
    session = _child()
    _run(ar, session, [_assistant('one'), _result('one'),
                       _human_followup(ar, session),
                       _assistant('human answer'), _result('human answer'),
                       _assistant('peer answer'), _result('peer answer'),
                       *JOB_DONE,
                       _assistant('job done'), _result('job done')])
    assert [s for _, s in sent] == ['one', 'peer answer', 'job done']


# ── turn owner bookkeeping ──────────────────────────────────────────────────

def test_owner_follows_who_started_the_turn(ar, monkeypatch):
    monkeypatch.setattr(ar, '_log_agent_dispatch_pending', lambda *a, **k: None)
    session = _child(_notify_session_sent=True)
    assert '_turn_owner' not in session              # dispatch: absent == spawner
    ar._advance_delegation_turn(session)
    assert session['_turn_owner'] == 'human'
    assert session['_notify_session_sent'] is True    # ownership is not delivery state
    ar._advance_delegation_turn(session, owner=None)  # midturn rollover / queue replay
    assert session['_turn_owner'] == 'human'
    ar._rearm_notify_for_new_turn(session)
    assert session['_turn_owner'] == 'spawner'
    assert '_notify_session_sent' not in session


def test_self_started_turn_on_a_session_with_no_spawner_does_nothing(ar, monkeypatch):
    sent = _capture(ar, monkeypatch)
    session = _child(_notify_session='')
    _run(ar, session, [_assistant('one'), _result('one'),
                       _assistant('two'), _result('two')])
    assert sent == []
    assert '_delegation_turn' not in session


def test_workflow_latch_stays_permanent(ar, monkeypatch):
    """A workflow step completes once; a wake must not re-fire it."""
    steps = []
    monkeypatch.setattr(ar, '_notify_workflow_step', lambda w, s, summ: steps.append(summ))
    _capture(ar, monkeypatch)
    session = _child(_notify_session='', _notify_workflow={'run_id': 'r', 'step': 's'})
    _run(ar, session, [_assistant('one'), _result('one'),
                       _assistant('two'), _result('two')])
    assert steps == ['one']


# ── D ───────────────────────────────────────────────────────────────────────

def test_suppressed_callback_is_logged_with_session_and_reason(ar, monkeypatch):
    logged = []
    monkeypatch.setattr(ar, '_log', lambda *a, **k: logged.append(' '.join(str(x) for x in a)))
    _capture(ar, monkeypatch)
    session = _child(_notify_session_sent=True)
    ar._maybe_notify_spawner(session, 'late answer')
    lines = [l for l in logged if 'suppressed' in l]
    assert len(lines) == 1
    assert 'child-wake' in lines[0] and '_notify_session_sent' in lines[0]


def test_unsuppressed_callback_logs_nothing(ar, monkeypatch):
    logged = []
    monkeypatch.setattr(ar, '_log', lambda *a, **k: logged.append(' '.join(str(x) for x in a)))
    _capture(ar, monkeypatch)
    ar._maybe_notify_spawner(_child(), 'answer')
    assert not [l for l in logged if 'suppressed' in l]


# ── C ───────────────────────────────────────────────────────────────────────

def _store(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.db')
    payload = {'message': 'm', 'child_session_id': 'c'}
    assert store.enqueue('c:turn:1', 'p', 'parent', payload)
    row = store.claim_outbox()
    store.ack_outbox(row['event_id'], row['fence_token'])
    assert store.accept('c:turn:1', 'p', 'parent', payload)
    return store


def _drain(store, process):
    return drain_once(store, send_outbox=lambda r: None, process_inbox=process)


def _state(store):
    return store.status('inbox', 'c:turn:1', 'p')


def test_pre_handoff_failure_is_retried_once_and_delivered(tmp_path):
    store = _store(tmp_path)
    handed = []

    def lost_claim(row):
        raise DeliveryNotHandedOff('inbox claim expired while waiting for parent lock')

    _drain(store, lost_claim)
    assert _state(store)['state'] == 'pending'          # not stuck 'uncertain'
    assert _state(store)['recovery_required'] == 0
    store.clock = lambda: 1e12                             # past the backoff

    def deliver(row):
        handed.append(row['event_id'])
        return {'stdin_write_ack': 'legacy'}

    _drain(store, deliver)
    assert handed == ['c:turn:1']
    assert _state(store)['state'] == 'submitted'


def test_pre_handoff_retry_is_bounded(tmp_path):
    store = _store(tmp_path)
    store.max_attempts = 3
    store.clock = lambda: 1e12

    def lost_claim(row):
        raise DeliveryNotHandedOff('claim fenced')

    for _ in range(3):
        _drain(store, lost_claim)
        store.clock = lambda c=store.clock: c() + 1000
    assert _state(store)['state'] == 'recovery_required'


def test_post_handoff_uncertain_is_not_retried(tmp_path):
    store = _store(tmp_path)
    calls = []

    def maybe_sent(row):
        calls.append(1)
        raise DeliveryUncertain('parent stdin write acknowledgment is unknown')

    _drain(store, maybe_sent)
    assert _state(store)['state'] == 'uncertain'
    store.clock = lambda: 1e12
    _drain(store, maybe_sent)
    assert len(calls) == 1                                  # never replayed


def test_blocked_stays_blocked_and_logs_why(tmp_path, monkeypatch):
    import mc.delegation_retry as retry
    logged = []
    monkeypatch.setattr(retry, '_log', lambda *a, **k: logged.append(' '.join(str(x) for x in a)))
    store = _store(tmp_path)

    def stopped(row):
        raise DeliveryBlocked('parent status stopped cannot accept a delegated completion')

    _drain(store, stopped)
    assert _state(store)['state'] == 'blocked'
    assert any('c:turn:1' in l and 'blocked' in l and 'stopped' in l for l in logged)


def test_process_inbox_raises_not_handed_off_when_the_claim_is_lost(ar, tmp_path, monkeypatch):
    """The two real raise sites in `_process_inbox`, before the parent lock and
    after waiting for it, are the pre-handoff class; the post-handoff sites
    (stdin ack unknown, HTTP 5xx, queued) stay plain DeliveryUncertain."""
    store = _store(tmp_path)
    monkeypatch.setattr(ar, '_delivery_store', store)
    row = {'event_id': 'c:turn:1', 'project_id': 'p', 'parent_session_id': 'parent',
           'payload': json.dumps({'payload': {'message': 'm'}}), 'fence_token': 'stale'}
    with pytest.raises(DeliveryNotHandedOff):
        ar._process_inbox(row)
