"""A parent's turn-end wakes its queued child reports (MC-1063 follow-up).

2026-10-06: Sol_Tobin's report waited out a 1..128 s backoff behind a busy Dave
even after Dave's turn ended, and a report parked `blocked` (parent in `error`
status) stayed parked after a human got that parent running again. The drain
below uses the SAME gate `_process_inbox` applies (running -> defer, error ->
block, idle/completed -> hand over) against a fake session table, with a fake
clock so backoff is deterministic.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import delegation_wake as dwk  # noqa: E402
from mc.delegation_delivery import (DeliveryBlocked, DeliveryDeferred,  # noqa: E402
                                    DeliveryStore, drain_once)


class Env:
    """Store + fake clock + fake parent sessions + the real drain/wake pair."""

    def __init__(self, tmp_path):
        self.now = 1000.0
        self.store = DeliveryStore(tmp_path / 'delivery.db', clock=lambda: self.now)
        self.sessions = {'parentA': {'project_id': 'p', 'status': 'running',
                                     'last_status_change_time': 1.0}}
        self.waker = dwk.ParentWaker()
        self.handed = []

    def gate(self, row):
        s = self.sessions[row['parent_session_id']]
        if s['status'] == 'running':
            raise DeliveryDeferred('parent is busy; completion remains pending')
        if s['status'] not in ('idle', 'completed'):
            raise DeliveryBlocked(dwk.parent_status_block_message(s['status']))
        self.handed.append(row['event_id'])
        return {'stdin_write_ack': 'written'}

    def cycle(self, advance=0.0, gate=None):
        """One delivery-loop iteration, as `_loop` runs it."""
        self.now += advance
        drain_once(self.store, send_outbox=lambda r: None, process_inbox=gate or self.gate)
        return self.waker.tick(self.store, self.sessions)

    def parent(self, status):
        s = self.sessions['parentA']
        s['status'] = status
        s['last_status_change_time'] += 1.0

    def row(self):
        with sqlite3.connect(self.store.path) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute('SELECT * FROM inbox').fetchone())


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    payload = {'event_id': 'c1:turn:1', 'child_session_id': 'c1', 'who': 'Sol_Tobin',
               'status': 'completed', 'task': 't', 'summary': 's', 'provider': 'claude',
               'model': '', 'message': 'm'}
    e.store.enqueue('c1:turn:1', 'p', 'parentA', payload)
    e.store.accept('c1:turn:1', 'p', 'parentA', payload)
    return e


def _backoff_to_far_future(env):
    """Defer enough times that the retry clock sits well past the next tick."""
    for _ in range(6):
        env.cycle(advance=400.0)
    r = env.row()
    assert r['state'] == 'pending' and r['attempts'] >= 5
    assert r['next_attempt'] > env.now + 10


def test_deferred_report_is_handed_over_right_after_the_turn_ends(env):
    _backoff_to_far_future(env)
    env.cycle()                      # parent still running: nothing happens
    assert env.handed == []
    env.parent('idle')               # turn ends, 1 s later nothing else has moved
    env.cycle(advance=1.0)           # wake pulls next_attempt to now ...
    env.cycle()                      # ... and the next drain hands it over
    assert env.handed == ['c1:turn:1']
    assert env.row()['state'] == 'submitted'


def test_without_the_waker_the_same_report_waits_out_the_backoff(env):
    """The behaviour being fixed: proves the scenario above is not vacuous."""
    _backoff_to_far_future(env)
    env.parent('idle')
    for _ in range(3):
        env.now += 1.0
        drain_once(env.store, send_outbox=lambda r: None, process_inbox=env.gate)
    assert env.handed == []
    assert env.row()['state'] == 'pending'


def test_a_parent_that_stays_running_is_never_woken(env):
    _backoff_to_far_future(env)
    before = env.row()['next_attempt']
    for _ in range(5):
        env.cycle(advance=1.0)
    assert env.row()['next_attempt'] == before
    assert env.handed == []


def test_an_idle_parent_does_not_cancel_backoff_for_other_deferrals(env):
    """Transition-driven, not level-driven: a deferral with no busy parent
    behind it (stdin write failed, shutdown admission) keeps its backoff."""
    def _stdin_failed(row):
        raise DeliveryDeferred('parent stdin write failed')
    env.parent('idle')
    env.cycle(gate=_stdin_failed)                     # first sight: stamp recorded
    for _ in range(4):
        env.cycle(advance=500.0, gate=_stdin_failed)
    waits = env.row()['next_attempt']
    assert waits > env.now + 10
    for _ in range(5):
        env.cycle(gate=_stdin_failed)                 # parent idle, no new transition
    assert env.row()['next_attempt'] == waits


def test_blocked_by_parent_error_is_retried_once_when_the_parent_next_idles(env):
    env.parent('error')
    env.cycle()                                       # blocked: parent in error
    assert env.row()['state'] == 'blocked'
    env.cycle(advance=5.0)
    assert env.row()['state'] == 'blocked'            # still error: no retry
    env.parent('running')                             # a human starts a turn ...
    env.cycle()
    assert env.row()['state'] == 'blocked'            # ... not idle yet
    env.parent('idle')                                # ... and it ends
    env.cycle()                                       # wake moves it to pending
    env.cycle()                                       # drain hands it over
    assert env.handed == ['c1:turn:1']
    assert env.row()['state'] == 'submitted'


def test_the_blocked_retry_happens_only_once(env):
    env.parent('error')
    env.cycle()
    env.parent('idle')
    refused = []

    def _still_blocked(row):
        refused.append(row['event_id'])
        raise DeliveryBlocked(dwk.parent_status_block_message('error'))
    env.cycle(gate=_still_blocked)                    # wake -> pending
    env.cycle(gate=_still_blocked)                    # drain -> blocked again
    assert env.row()['state'] == 'blocked' and len(refused) == 1
    for _ in range(3):                                # more turns on that parent
        env.parent('running')
        env.cycle(gate=_still_blocked)
        env.parent('idle')
        env.cycle(gate=_still_blocked)
        env.cycle(gate=_still_blocked)
    assert env.row()['state'] == 'blocked'
    assert len(refused) == 1                          # no second automatic attempt


@pytest.mark.parametrize('parked', ['uncertain', 'recovery_required'])
def test_uncertain_and_recovery_required_are_never_touched(env, parked):
    with sqlite3.connect(env.store.path) as db:
        db.execute('UPDATE inbox SET state=?,last_error=?', (parked, 'parent status error x'))
    env.cycle()
    env.parent('running')
    env.cycle()
    env.parent('idle')
    for _ in range(3):
        env.cycle()
    assert env.row()['state'] == parked
    assert env.handed == []


def test_blocked_for_another_reason_is_not_retried(env):
    def _quota(row):
        raise DeliveryBlocked('model quota exhausted')
    env.parent('idle')
    env.cycle(gate=_quota)
    assert env.row()['state'] == 'blocked'
    env.parent('running')
    env.cycle(gate=_quota)
    env.parent('idle')
    for _ in range(3):
        env.cycle(gate=_quota)
    assert env.row()['state'] == 'blocked'


def test_a_row_that_became_uncertain_between_read_and_update_is_not_replayed(env):
    env.parent('error')
    env.cycle()
    env.parent('idle')
    with sqlite3.connect(env.store.path) as db:      # lost the race to a handoff outcome
        db.execute("UPDATE inbox SET state='uncertain'")
    assert dwk.ParentWaker._retry_blocked(env.store, 'c1:turn:1', env.now) == 0
    assert env.row()['state'] == 'uncertain'


def test_unknown_parent_and_a_broken_store_never_raise(env):
    env.sessions.clear()                              # parent not in memory
    assert env.waker.tick(env.store, env.sessions) == 0

    class _Boom:
        def _db(self):
            raise RuntimeError('database is locked')
    assert dwk.ParentWaker().tick(_Boom(), {}) == 0


def test_block_message_is_recognised_and_unchanged():
    msg = dwk.parent_status_block_message('error')
    assert msg == 'parent status error cannot accept a delegated completion'
    assert dwk.is_parent_status_block(msg)
    assert not dwk.is_parent_status_block('model quota exhausted')
    assert not dwk.is_parent_status_block('')


def test_process_inbox_blocks_with_the_recognised_message(tmp_path, monkeypatch):
    """The real gate in agent_routes must produce what the waker looks for."""
    import server  # noqa: F401  (wires agent_routes)
    from mc.blueprints import agent_routes as ar
    store = DeliveryStore(tmp_path / 'delivery.db')
    payload = {'message': 'm'}
    store.enqueue('c1:turn:1', 'p', 'parentA', payload)
    store.accept('c1:turn:1', 'p', 'parentA', payload)
    row = store.claim_inbox()
    monkeypatch.setattr(ar, '_delivery_store', store)
    monkeypatch.setitem(ar.agent_sessions, 'parentA',
                        {'project_id': 'p', 'status': 'error', 'session_id': 'parentA'})
    try:
        with pytest.raises(DeliveryBlocked) as exc:
            ar._process_inbox(row)
    finally:
        ar.agent_sessions.pop('parentA', None)
    assert dwk.is_parent_status_block(str(exc.value))
