"""A queued child report must be visible on its parent (MC-1063).

2026-10-06: Sol_Tobin 18dabf5705c4 finished and its report was queued for Dave
c11ea2b14b40, who was mid-turn. The inbox row sat `pending`, retried with
backoff, and nothing on the parent said so; Ron saw the child done and the
parent silent and had to ask. These tests pin the read-only projection
(`mc/delegation_waiting.py`) and its two consumers, /agent/status and the
Floor figure. Nothing here changes what the delivery store does.
"""
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import delegation_waiting as dw  # noqa: E402
from mc.delegation_delivery import (DeliveryBlocked, DeliveryDeferred,  # noqa: E402
                                    DeliveryStore, DeliveryUncertain, drain_once)


def _payload(who='Sol_Tobin', child='child1', event='child1:turn:1'):
    return {'event_id': event, 'child_session_id': child, 'who': who,
            'status': 'completed', 'task': 't', 'summary': 's', 'provider': 'claude',
            'model': '', 'message': 'm'}


@pytest.fixture
def store(tmp_path):
    s = DeliveryStore(tmp_path / 'delivery.db')
    dw.configure(s.path)
    yield s
    dw.configure(None)


def _queue(store, event='child1:turn:1', parent='parentA', project='p', who='Sol_Tobin'):
    child = event.split(':turn:')[0]
    store.enqueue(event, project, parent, _payload(who, child, event))
    store.accept(event, project, parent, _payload(who, child, event))


def _drain_raising(store, exc):
    def _boom(row):
        raise exc
    drain_once(store, send_outbox=lambda row: None, process_inbox=_boom)


def test_deferred_report_is_listed_against_its_parent(store):
    _queue(store)
    _drain_raising(store, DeliveryDeferred('parent is busy; completion remains pending'))
    got = dw.waiting_by_parent('p')
    assert list(got) == ['parentA']
    r = got['parentA'][0]
    assert r['stage'] == 'waiting'
    assert r['label'] == 'report waiting from Sol_Tobin'
    assert r['child_session_id'] == 'child1'


def test_report_leaves_the_list_once_handed_to_the_parent(store):
    _queue(store)
    _drain_raising(store, DeliveryDeferred('busy'))
    assert dw.waiting_by_parent('p')
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE inbox SET next_attempt=0")   # skip the retry backoff
    drain_once(store, send_outbox=lambda r: None,
               process_inbox=lambda r: {'stdin_write_ack': 'written'})
    assert store.status('inbox', 'child1:turn:1', 'p')['state'] == 'submitted'
    assert dw.waiting_by_parent('p') == {}


@pytest.mark.parametrize('exc,stage,wording', [
    (DeliveryBlocked('parent status error cannot accept'), 'held', 'is held'),
    (DeliveryUncertain('outcome unknown'), 'review', 'may not have arrived'),
])
def test_parked_reports_stay_listed_with_their_own_wording(store, exc, stage, wording):
    _queue(store)
    _drain_raising(store, exc)
    r = dw.waiting_by_parent('p')['parentA'][0]
    assert r['stage'] == stage
    assert wording in r['label']
    assert 'not retried automatically' in r['detail']


def test_recovery_required_is_listed_as_review(store):
    _queue(store)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE inbox SET state='recovery_required'")
    assert dw.waiting_by_parent('p')['parentA'][0]['stage'] == 'review'


def test_child_finished_but_not_yet_accepted_is_already_waiting(store):
    store.enqueue('c9:turn:1', 'p', 'parentA', _payload('Fenn', 'c9', 'c9:turn:1'))
    got = dw.waiting_by_parent('p')['parentA']
    assert [r['label'] for r in got] == ['report waiting from Fenn']


def test_outbox_and_inbox_copies_of_one_event_count_once(store):
    _queue(store)  # outbox pending AND inbox pending for the same event
    assert len(dw.waiting_by_parent('p')['parentA']) == 1


def test_scoped_to_project_and_all_projects(store):
    _queue(store, 'a1:turn:1', 'parentA', 'p')
    _queue(store, 'b1:turn:1', 'parentB', 'q')
    assert list(dw.waiting_by_parent('p')) == ['parentA']
    assert sorted(dw.waiting_by_parent('')) == ['parentA', 'parentB']


def test_two_reports_for_one_parent_are_oldest_first(store):
    _queue(store, 'a1:turn:1', who='First')
    _queue(store, 'b1:turn:1', who='Second')
    assert [r['who'] for r in dw.waiting_by_parent('p')['parentA']] == ['First', 'Second']


def test_unconfigured_missing_and_corrupt_database_return_empty(tmp_path):
    dw.configure(None)
    assert dw.waiting_by_parent('p') == {}
    dw.configure(tmp_path / 'absent.db')
    assert dw.waiting_by_parent('p') == {}
    bad = tmp_path / 'bad.db'
    bad.write_bytes(b'this is not a sqlite database' * 50)
    dw.configure(bad)
    assert dw.waiting_by_parent('p') == {}   # never raises: a status poll must survive
    dw.configure(None)


def test_display_name_is_cleaned_and_bounded(store):
    _queue(store, who='Sol\n<b>Tobin</b>' + 'x' * 200)
    who = dw.waiting_by_parent('p')['parentA'][0]['who']
    assert '\n' not in who and len(who) <= 48


def test_reading_never_writes(store):
    _queue(store)
    dw.waiting_by_parent('p')
    with sqlite3.connect(store.path) as db:
        rows = db.execute('SELECT state,attempts FROM inbox').fetchall()
    assert rows == [('pending', 0)]


def test_figure_label_wording():
    one = [{'stage': 'waiting', 'label': 'report waiting from A', 'who': 'A'}]
    assert dw.figure_label(one) == 'report waiting from A'
    three = [{'stage': 'waiting', 'label': '', 'who': w} for w in 'ABC']
    assert dw.figure_label(three) == '3 reports waiting (A, B, ...)'
    parked = [{'stage': 'held', 'label': '', 'who': 'A'}]
    assert dw.figure_label(parked) == '1 report needs review'
    assert dw.figure_label(None) == '' and dw.figure_label([]) == ''


def test_floor_figure_carries_the_label(store):
    from mc.blueprints import floor_routes
    s = {'session_id': 'parentA', 'project_id': 'p', 'status': 'running', 'log_lines': []}
    assert floor_routes._figure(s)['report_waiting'] == ''
    _queue(store)
    fig = floor_routes._figure(s, reports_waiting=dw.waiting_by_parent())
    assert fig['report_waiting'] == 'report waiting from Sol_Tobin'


def test_agent_status_carries_the_queue_for_each_session(tmp_path, monkeypatch):
    import server
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'auth_state.json')
    monkeypatch.setattr(ar, 'load_project', lambda pid: {'id': 'p', 'project_path': str(tmp_path)})
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: True)
    store = DeliveryStore(tmp_path / 'delivery.db')
    dw.configure(store.path)
    _queue(store, parent='parentA')

    def sess(sid):
        return {'session_id': sid, 'project_id': 'p', 'status': 'running', 'task': 't',
                'log_lines': [], 'started_at': '2026-10-06T20:00:00Z', 'mode': 'B',
                'stdin_lock': threading.Lock()}
    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    mc_state.agent_sessions.update({'parentA': sess('parentA'), 'other': sess('other')})
    server.app.config['TESTING'] = True

    def poll():
        resp = server.app.test_client().get('/api/project/p/agent/status')
        return {r['session_id']: r for r in resp.get_json()['sessions']}
    try:
        rows = poll()
        assert [r['label'] for r in rows['parentA']['reports_waiting']] == \
            ['report waiting from Sol_Tobin']
        assert rows['other']['reports_waiting'] == []
        # Handed over: the marker clears with the next poll.
        with sqlite3.connect(store.path) as db:
            db.execute("UPDATE inbox SET state='submitted'")
            db.execute("UPDATE outbox SET state='delivered'")
        assert poll()['parentA']['reports_waiting'] == []
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)
        dw.configure(None)
