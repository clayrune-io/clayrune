"""A dispatched agent's QUESTION must reach its spawner.

Incident 2026-10-05: Tilda (dispatched by Dave with notify_session) ended a turn
on an mc:question block. `_maybe_notify_spawner` returned early on
`waiting_for_question` (the 2026-09-18 guard that stops a pause from firing a
fake "[dispatched agent finished]"), so nothing told Dave; Ron saw "waiting for
response" in her chat and had to relay it. These tests hold the fix: a distinct,
durable, once-per-question callback -- and the finished callback untouched.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mc.state as state  # noqa: E402

state.CONFIG.setdefault('port', 5199)

from mc import agent_runtime as agent_runtime  # noqa: E402
from mc.blueprints import agent_routes as ar  # noqa: E402
from mc.delegation_delivery import DeliveryStore  # noqa: E402
from mc.question_callback import SENT_KEY  # noqa: E402

QUESTION_TURN = (
    "I need a decision before I continue.\n"
    "```mc:question\n"
    '{"questions": [{"header": "Cold start", "question": "Which fix?", '
    '"options": [{"label": "Persist cache", "description": "write to disk"}, '
    '{"label": "Lazy scan", "description": "scan on demand"}], '
    '"multiSelect": false}]}\n'
    "```\n")


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = DeliveryStore(tmp_path / 'delivery.db')
    monkeypatch.setattr(ar, '_delivery_store', s)
    return s


def _outbox_ids(store):
    with store._db() as db:
        return sorted(r['event_id'] for r in db.execute('SELECT event_id FROM outbox'))


def _outbox_payload(store, event_id):
    with store._db() as db:
        row = db.execute('SELECT payload FROM outbox WHERE event_id=?', (event_id,)).fetchone()
    return json.loads(row['payload'])['payload']


def _child(**over):
    sess = {'project_id': 'proj', 'session_id': 'child0123456789', '_notify_session': 'parent-1',
            'status': 'idle', 'task': 'fix the cold start', 'provider': 'claude',
            '_delegation_turn': 1, 'log_lines': ['working on it']}
    sess.update(over)
    return sess


def _pause_on_question(sess):
    res = agent_runtime.apply_mc_tool_blocks(sess, QUESTION_TURN)
    assert res['paused'] and sess['waiting_for_question']


def test_question_pause_wakes_spawner_once_and_never_sends_finished(store):
    sess = _child()
    _pause_on_question(sess)
    qid = sess['pending_questions'][0]['question_id']

    # Mode B result path, then the stream-reader `finally` -> _log_agent_completion
    # path: both land in _maybe_notify_spawner for the same pause.
    ar._maybe_notify_spawner(sess, 'I need a decision')
    ar._notify_spawner_unless_jobs_open(sess, 'I need a decision')

    ids = _outbox_ids(store)
    assert ids == [f'child0123456789:turn:1:question:{qid}']          # one, not two
    assert not sess.get('_notify_session_sent')                       # no finished latch
    p = _outbox_payload(store, ids[0])
    msg = p['message']
    assert msg.startswith('[dispatched agent asked a question]')
    assert 'finished]' not in msg.split('\n')[0]
    assert '[Cold start] Which fix?' in msg
    assert 'Persist cache: write to disk' in msg and 'Lazy scan: scan on demand' in msg
    assert '/api/project/proj/agent/send' in msg and 'child0123456789' in msg
    assert p['status'] == 'waiting_for_answer'


def test_answer_then_real_finish_fires_the_normal_callback(store):
    sess = _child()
    _pause_on_question(sess)
    ar._maybe_notify_spawner(sess, 'I need a decision')

    # What agent_followup does when the spawner's answer lands.
    sess['waiting_for_question'] = False
    sess.pop('pending_questions', None)
    sess['_delegation_turn'] = 2
    sess['status'] = 'completed'
    ar._maybe_notify_spawner(sess, 'done, persisted the cache')

    ids = _outbox_ids(store)
    assert len(ids) == 2
    finished = [i for i in ids if ':question:' not in i]
    assert finished == ['child0123456789:turn:2']
    assert _outbox_payload(store, finished[0])['message'].startswith('[dispatched agent finished]')
    assert sess['_notify_session_sent'] is True


def test_second_question_gets_its_own_callback(store):
    sess = _child()
    _pause_on_question(sess)
    ar._maybe_notify_spawner(sess, 'q1')
    sess['waiting_for_question'] = False
    sess.pop('pending_questions', None)
    sess['_delegation_turn'] = 2
    _pause_on_question(sess)
    ar._maybe_notify_spawner(sess, 'q2')
    ar._maybe_notify_spawner(sess, 'q2 again')
    assert len([i for i in _outbox_ids(store) if ':question:' in i]) == 2


def test_no_spawner_keeps_todays_behaviour(store):
    for over in ({'_notify_session': ''}, {'_notify_session': 'child0123456789'},
                 {'_notify_session': 'parent-1', 'incognito': True}):
        sess = _child(**over)
        _pause_on_question(sess)
        ar._maybe_notify_spawner(sess, 'I need a decision')
        assert SENT_KEY not in sess
    assert _outbox_ids(store) == []


def test_spent_turn_latch_suppresses_the_question_callback(store):
    # A human-typed follow-up into a finished dispatch (MC-970): the finished
    # callback is spent for that chain, and so is the question one.
    sess = _child(_notify_session_sent=True)
    _pause_on_question(sess)
    ar._maybe_notify_spawner(sess, 'I need a decision')
    assert _outbox_ids(store) == []


def test_enqueue_failure_is_not_marked_sent_and_retries(store, monkeypatch):
    sess = _child()
    _pause_on_question(sess)
    real = store.enqueue
    monkeypatch.setattr(store, 'enqueue', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('db')))
    ar._maybe_notify_spawner(sess, 'x')                 # must not raise
    assert _outbox_ids(store) == []
    monkeypatch.setattr(store, 'enqueue', real)
    ar._maybe_notify_spawner(sess, 'x')
    assert len(_outbox_ids(store)) == 1


def test_no_store_does_not_raise():
    sess = _child()
    _pause_on_question(sess)
    ar._delivery_store, saved = None, ar._delivery_store
    try:
        ar._maybe_notify_spawner(sess, 'x')
    finally:
        ar._delivery_store = saved


def test_native_askuserquestion_shape_renders():
    from mc import question_callback as qc
    text = qc.render_questions({'question_id': 'a', 'questions': [
        {'question': 'Pick', 'header': 'H', 'multiSelect': True,
         'options': [{'label': 'A', 'description': 'first'}, 'B']}]})
    assert text.splitlines()[0] == '1. [H] Pick'
    assert '   - A: first' in text and '   - B' in text and 'more than one' in text

