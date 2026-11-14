"""A report parked `blocked` on an errored parent is re-armed once the parent
has left memory (2026-11-13: 3 rows sat blocked 235-253 min after the guardian
killed Dave's session and it dropped out of agent_sessions).

Uses the same store/gate/clock harness as test_delegation_wake.py. Absent
parent == `sessions` has no entry; the real gate then revives, here a fake
that records the handoff."""
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import delegation_wake as dwk  # noqa: E402
from mc import delegation_wake_absent as dwa  # noqa: E402
from mc.delegation_delivery import (DeliveryBlocked, DeliveryStore,  # noqa: E402
                                    drain_once)


class Env:
    def __init__(self, tmp_path):
        self.now = 1000.0
        self.store = DeliveryStore(tmp_path / 'delivery.db', clock=lambda: self.now)
        self.sessions = {'parentA': {'project_id': 'p', 'status': 'error',
                                     'last_status_change_time': 1.0}}
        self.waker = dwk.ParentWaker()
        self.absent = dwa.AbsentParentWaker()
        self.revived = []

    def gate(self, row):
        s = self.sessions.get(row['parent_session_id'])
        if s is None:                                  # _process_inbox -> revive
            self.revived.append(row['event_id'])
            return {'stdin_write_ack': 'written'}
        if s['status'] != 'idle':
            raise DeliveryBlocked(dwk.parent_status_block_message(s['status']))
        return {'stdin_write_ack': 'written'}

    def cycle(self, gate=None):
        self.now += 1.0
        drain_once(self.store, send_outbox=lambda r: None, process_inbox=gate or self.gate)
        self.waker.tick(self.store, self.sessions)
        return self.absent.tick(self.store, self.sessions)

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


def test_blocked_row_is_revived_after_the_errored_parent_leaves_memory(env):
    env.cycle()
    assert env.row()['state'] == 'blocked'            # parent in memory, status error
    env.cycle()
    assert env.row()['state'] == 'blocked'            # still in memory: not our row
    del env.sessions['parentA']                       # stale cleanup / restart
    assert env.cycle() == 1                           # re-armed ...
    env.cycle()                                       # ... drain reaches the revive branch
    assert env.revived == ['c1:turn:1']
    assert env.row()['state'] == 'submitted'


def test_without_it_the_row_stays_blocked_forever(env):
    """The gap being fixed: ParentWaker alone skips an absent parent."""
    env.cycle()
    del env.sessions['parentA']
    for _ in range(5):
        env.now += 1.0
        drain_once(env.store, send_outbox=lambda r: None, process_inbox=env.gate)
        env.waker.tick(env.store, env.sessions)
    assert env.row()['state'] == 'blocked' and env.revived == []


def test_a_parent_still_in_memory_is_left_to_parent_waker(env):
    env.cycle()
    for _ in range(3):
        assert env.cycle() == 0
    assert env.row()['state'] == 'blocked'


def test_only_one_automatic_retry_per_row(env):
    env.cycle()
    del env.sessions['parentA']

    def _refuse(row):
        raise DeliveryBlocked(dwk.parent_status_block_message('error'))
    assert env.cycle(gate=_refuse) == 1               # re-armed
    env.cycle(gate=_refuse)                           # drain refuses -> blocked again
    assert env.row()['state'] == 'blocked'
    for _ in range(4):
        assert env.cycle(gate=_refuse) == 0           # not retried a second time
    assert env.row()['state'] == 'blocked'


@pytest.mark.parametrize('parked', ['uncertain', 'recovery_required'])
def test_uncertain_and_recovery_required_are_never_touched(env, parked):
    with sqlite3.connect(env.store.path) as db:
        db.execute('UPDATE inbox SET state=?,last_error=?', (parked, 'parent status error x'))
    del env.sessions['parentA']
    for _ in range(3):
        assert env.cycle() == 0
    assert env.row()['state'] == parked


def test_blocked_for_other_reasons_is_never_touched(env):
    with sqlite3.connect(env.store.path) as db:
        db.execute("UPDATE inbox SET state='blocked',last_error='parent identity/privacy state changed'")
    del env.sessions['parentA']
    for _ in range(3):
        assert env.cycle() == 0
    assert env.row()['state'] == 'blocked'
