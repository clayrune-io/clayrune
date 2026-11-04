"""Strict role continuity using real isolated delivery databases; no agents."""
from types import SimpleNamespace
import threading

import pytest

from mc import steward_role_children as children, steward_role_dispatch as dispatch, state
from mc.delegation_delivery import DeliveryStore
from mc.steward_role_model import RoleError


@pytest.fixture
def env(tmp_path):
    store = DeliveryStore(tmp_path / 'delivery.sqlite3')
    child = {'session_id': 'child', 'project_id': 'p', '_notify_session': 'old',
             'status': 'idle', '_delegation_turn': 1}
    return SimpleNamespace(store=store, child=child)


def inspect(env, *, sessions=None, logs=()):
    return children.inspect('p', 'old', sessions=[env.child] if sessions is None else sessions,
                            agent_log=logs, delivery_path=env.store.path)


def receipt(env, event='child:turn:1', *, project='p', parent='old', status='processed'):
    payload = {'child_session_id': event.split(':turn:')[0], 'task': 'PRIVATE TASK BODY'}
    env.store.record_completion_source(event, project, parent, payload)
    env.store.enqueue(event, project, parent, payload)
    env.store.accept(event, project, parent, payload)
    with env.store._db() as db:
        db.execute('UPDATE inbox SET state=? WHERE event_id=?', (status, event))


@pytest.mark.parametrize('status', ['processed', 'confirmed_in_transcript', 'handled'])
def test_current_turn_receipt_proves_drained_without_mutating_ledger(env, status):
    receipt(env, status=status)
    before = env.store.status('inbox', 'child:turn:1', 'p')
    result = inspect(env)
    assert result.drained and result.children == ('child',)
    children.require_drained(result)
    assert env.store.status('inbox', 'child:turn:1', 'p') == before
    text = result.handoff('old')
    assert 'old' in text and 'child' in text and 'PRIVATE TASK BODY' not in text
    # A terminal receipt dominates an old pending transport/source copy.
    assert env.store.status('outbox', 'child:turn:1', 'p')['state'] == 'pending'


@pytest.mark.parametrize('status', ['pending', 'dispatch_intent', 'submitted', 'blocked',
                                   'uncertain', 'recovery_required', 'future_state'])
def test_unsettled_callback_never_allows_successor(env, status):
    receipt(env, status=status)
    result = inspect(env)
    assert not result.drained and result.pending_events == ('child:turn:1',)
    with pytest.raises(RoleError, match='undrained child callbacks'):
        children.require_drained(result)
    assert env.store.status('inbox', 'child:turn:1', 'p')['state'] == status


def test_older_turn_receipt_cannot_settle_current_turn(env):
    receipt(env)
    env.child['_delegation_turn'] = 2
    assert inspect(env).unfinished == ('child',)


def test_live_running_or_waiting_child_overrides_completed_log(env):
    receipt(env)
    log = {'session_id': 'child', 'spawned_by_session_id': 'old', 'status': 'completed',
           'delegation_completion': {'event_id': 'child:turn:1'}}
    env.child['status'] = 'running'
    assert inspect(env, logs=[log]).unfinished == ('child',)
    env.child.update(status='idle', pending_question=True)
    assert inspect(env, logs=[log]).unfinished == ('child',)


def test_restart_uses_durable_child_log_and_receipt(env):
    receipt(env)
    log = {'session_id': 'child', 'spawned_by_session_id': 'old', 'status': 'idle',
           'delegation_completion': {'event_id': 'child:turn:1'}}
    assert inspect(env, sessions=[], logs=[log]).drained
    log.pop('delegation_completion')
    assert not inspect(env, sessions=[], logs=[log]).drained


def test_foreign_project_events_and_sessions_are_excluded(env):
    receipt(env, project='other')
    foreign = dict(env.child, project_id='other')
    result = inspect(env, sessions=[foreign])
    assert result.drained and not result.children and not result.pending_events


def test_source_only_completion_and_delivered_transport_without_inbox_hold(env):
    payload = {'child_session_id': 'child'}
    env.store.record_completion_source('child:turn:1', 'p', 'old', payload)
    assert inspect(env).pending_events == ('child:turn:1',)
    env.store.enqueue('child:turn:1', 'p', 'old', payload)
    claim = env.store.claim_outbox()
    env.store.ack_outbox(claim['event_id'], claim['fence_token'])
    assert inspect(env).pending_events == ('child:turn:1',)


def test_transitive_child_work_cannot_revive_an_abandoned_receiving_chain(env):
    receipt(env)
    grandchild = dict(env.child, session_id='grandchild', _notify_session='child', status='running')
    result = inspect(env, sessions=[env.child, grandchild])
    assert not result.drained and result.unfinished == ('grandchild',)


@pytest.mark.parametrize('kind', ['unconfigured', 'missing', 'corrupt', 'wrong_schema'])
def test_unknown_database_is_a_blocker_without_creating_a_file(tmp_path, kind):
    path = tmp_path / 'unknown.sqlite3'
    if kind == 'corrupt':
        path.write_bytes(b'broken database')
    elif kind == 'wrong_schema':
        import sqlite3
        sqlite3.connect(path).close()
    with pytest.raises(RoleError, match='callback state'):
        children.inspect('p', 'old', sessions=[], agent_log=[],
                         delivery_path=None if kind == 'unconfigured' else path)
    assert path.exists() == (kind in ('corrupt', 'wrong_schema'))


def test_production_continuation_collects_children_but_successor_holds(env, monkeypatch):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, '_delivery_path', env.store.path)
    monkeypatch.setitem(state.agent_sessions, 'child', env.child)
    called = []
    scheduler = SimpleNamespace(_load_agent_log=lambda pid: [],
        get_manager=lambda pid: SimpleNamespace(lock=threading.RLock()))
    services = dispatch.SchedulerServices(scheduler, lambda *args: called.append(args) or 'channel ready')
    detail = {'predecessor_session_id': 'old'}
    text = services.lifecycle({'id': 'p'}, {'id': 'wake'}, 'continue', detail)
    assert 'Unfinished children: ["child"]' in text and 'channel ready' in text
    with pytest.raises(RoleError, match='undrained'):
        services.lifecycle({'id': 'p'}, {'id': 'wake'}, 'successor', detail)
    assert len(called) == 1
    receipt(env)
    assert 'Unfinished children: []' in services.lifecycle(
        {'id': 'p'}, {'id': 'wake'}, 'successor', detail)


def test_production_successor_rechecks_child_state_immediately_before_delivery(env, monkeypatch):
    from mc.blueprints import agent_routes as ar
    receipt(env)
    monkeypatch.setattr(ar, '_delivery_path', env.store.path)
    monkeypatch.setitem(state.agent_sessions, 'child', env.child)
    launched = []
    scheduler = SimpleNamespace(_load_agent_log=lambda pid: [{'session_id': 'old', 'status': 'idle'}],
        get_manager=lambda pid: SimpleNamespace(lock=threading.RLock()),
        _dispatch_agent_internal=lambda *a, **k: launched.append(a) or 'new')
    services = dispatch.SchedulerServices(scheduler, lambda *a: '')
    services.lifecycle({'id': 'p'}, {'id': 'wake'}, 'successor', {'predecessor_session_id': 'old'})
    # New work arrives during context preparation, after the earlier check.
    env.child.update(status='running', _delegation_turn=2)
    prepared = dispatch.Prepared({}, {'provider': 'codex'}, 'rules',
                                 predecessor_session_id='old', successor=True)
    with pytest.raises(RoleError, match='undrained'):
        services.deliver({'id': 'p'}, {'id': 'wake'}, '[Steward cycle] task', prepared, None)
    assert not launched and env.child['_notify_session'] == 'old'


@pytest.mark.parametrize('source', ['live', 'durable'])
def test_missing_child_identity_cannot_be_treated_as_drained(env, source):
    row = dict(env.child)
    row.pop('session_id')
    with pytest.raises(RoleError, match='child session identity unavailable'):
        inspect(env, sessions=[row] if source == 'live' else [],
                logs=[row] if source == 'durable' else [])


def test_ready_successor_child_snapshot_is_durably_journaled(env, monkeypatch, tmp_path):
    from mc.blueprints import agent_routes as ar
    from mc.steward_role_state import RoleStateStore
    import json
    receipt(env)
    monkeypatch.setattr(ar, '_delivery_path', env.store.path)
    monkeypatch.setitem(state.agent_sessions, 'child', env.child)
    scheduler = SimpleNamespace(_load_agent_log=lambda pid: [],
        get_manager=lambda pid: SimpleNamespace(lock=threading.RLock()))
    services = dispatch.SchedulerServices(scheduler, lambda *a: 'channel ready')
    project = {'id': 'p', 'project_path': str(tmp_path / 'project')}
    row = {'id': 'wake', 'steward_role': {'journal_path': 'docs/_journal/role.md'}}
    detail = {'predecessor_session_id': 'old'}
    text = dispatch._handoff(services, project, row, 'successor', detail,
                             RoleStateStore(tmp_path / 'data'))
    journal = tmp_path / 'project/docs/_journal/role.md'
    entry = json.loads(journal.read_text().splitlines()[-1])
    assert entry['child_work'] == {'receiving_session_id': 'old', 'children': ['child'],
                                   'unfinished': [], 'pending_events': []}
    assert 'channel ready' in text and env.child['_notify_session'] == 'old'
