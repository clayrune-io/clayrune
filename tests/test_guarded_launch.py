"""Real SQLite transactions, fake process creation only; no CLI certification."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import sqlite3

import pytest

from mc.conversation_store import ConversationStore, EventConflict, LaunchUncertain
from mc import execution_lifecycle as life


@pytest.fixture
def claimed(tmp_path):
    store = ConversationStore(tmp_path / 'journal.sqlite')
    engine = {'provider': 'fake', 'model': 'pinned', 'effort': '', 'account_ref': 'a'}
    store.create_lifecycle_conversation('p', 'c', engine=engine, event_id='create')
    state = store.accept_request('p', 'c', request_id='r', user_message={'text': 'original'},
        engine=engine, provenance={'origin': 'interactive'}, expected_revision=0, event_id='accept')
    state, owner = store.claim_owner('p', 'c', owner_id='o', expected_revision=state.revision, event_id='owner')
    _, token = store.claim_attempt(owner, request_id='r', attempt_id='a',
                                  expected_revision=state.revision, event_id='claim')
    return store, token


def launch(store, token, spawn, authorize=lambda: None, event_id='launch'):
    return store.launch_claimed(token, expected_attempt_revision=0, event_id=event_id,
                                authorize=authorize, spawn=spawn)


def test_marker_is_durable_before_creation_and_reference_not_native_id(claimed):
    store, token = claimed
    checked = []
    def spawn():
        # A separate readonly connection sees the already committed marker.
        state = ConversationStore(store.db_path).lifecycle_state('p', 'c')
        assert state.attempts[0].status == life.AttemptStatus.SPAWNING
        checked.append('created')
        return 'owned-process:123'
    state = launch(store, token, spawn)
    assert state.attempts[0].status == life.AttemptStatus.RUNNING
    assert state.attempts[0].native_handle is None
    assert store.read_events('p', 'c')[-1].payload['process_reference'] == 'owned-process:123'
    for event_id in ('launch', 'another-id'):
        with pytest.raises((life.LifecycleConflict, ValueError, RuntimeError)):
            launch(store, token, spawn, event_id=event_id)
    assert checked == ['created']


def test_unknown_creation_failure_requires_reconciliation(claimed):
    store, token = claimed
    created = []
    def spawn():
        created.append('maybe-created')
        raise OSError('secret stderr must not be persisted')
    with pytest.raises(LaunchUncertain, match='reconcile'):
        launch(store, token, spawn)
    state = store.lifecycle_state('p', 'c')
    assert state.attempts[0].status == life.AttemptStatus.UNCERTAIN
    assert state.active_attempt == 'a'
    with pytest.raises(life.LifecycleConflict):
        launch(store, token, spawn, event_id='retry')
    assert created == ['maybe-created']
    assert 'secret stderr' not in repr(store.read_events('p', 'c'))


def test_existing_final_event_id_rejected_before_creation(claimed):
    store, token = claimed
    spawned = []
    with pytest.raises(EventConflict):
        launch(store, token, lambda: spawned.append(1) or 'pid:1', event_id='accept')
    assert spawned == []


def test_postspawn_event_insert_failure_cannot_retry(claimed, monkeypatch):
    store, token = claimed
    insert = store._lifecycle_event
    spawned = []
    def fail_event(db, state, event_id, *args, **kwargs):
        if event_id == 'launch':
            raise OSError('event persistence failed')
        return insert(db, state, event_id, *args, **kwargs)
    monkeypatch.setattr(store, '_lifecycle_event', fail_event)
    with pytest.raises(OSError):
        launch(store, token, lambda: spawned.append(1) or 'pid:1')
    assert store.lifecycle_state('p', 'c').attempts[0].status == life.AttemptStatus.SPAWNING
    with pytest.raises(life.LifecycleConflict):
        launch(store, token, lambda: spawned.append(2) or 'pid:2', event_id='retry')
    assert spawned == [1]


def test_postspawn_commit_failure_cannot_retry(claimed, monkeypatch):
    store, token = claimed
    connect = sqlite3.connect
    class FailRunningCommit(sqlite3.Connection):
        def commit(self):
            row = self.execute('SELECT status FROM lifecycle_attempts WHERE attempt_id=?', ('a',)).fetchone()
            if row and row[0] == 'running':
                raise sqlite3.OperationalError('simulated commit failure')
            return super().commit()
    monkeypatch.setattr(sqlite3, 'connect', lambda *args, **kwargs: connect(
        *args, **kwargs, factory=FailRunningCommit))
    spawned = []
    with pytest.raises(sqlite3.OperationalError):
        launch(store, token, lambda: spawned.append(1) or 'pid:1')
    reopened = ConversationStore(store.db_path)
    assert reopened.lifecycle_state('p', 'c').attempts[0].status == life.AttemptStatus.SPAWNING
    with pytest.raises(life.LifecycleConflict):
        launch(reopened, token, lambda: spawned.append(2) or 'pid:2', event_id='retry')
    assert spawned == [1]


def test_concurrent_launch_callers_create_only_once(claimed):
    store, token = claimed
    spawned = []
    def try_launch(index):
        try:
            return launch(ConversationStore(store.db_path), token,
                          lambda: spawned.append(index) or f'pid:{index}', event_id=f'launch:{index}')
        except life.LifecycleConflict:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        states = list(pool.map(try_launch, range(4)))
    assert len(spawned) == 1
    assert sum(state is not None for state in states) == 1


def test_postspawn_persistence_failure_keeps_consumed_launch_marker(claimed, monkeypatch):
    store, token = claimed
    save = store._save_lifecycle
    created = []
    def fail_running(db, before, state, *args):
        if state.attempts and state.attempts[0].status == life.AttemptStatus.RUNNING:
            raise OSError('disk failure')
        return save(db, before, state, *args)
    monkeypatch.setattr(store, '_save_lifecycle', fail_running)
    with pytest.raises(OSError):
        launch(store, token, lambda: created.append(1) or 'pid:1')
    assert store.lifecycle_state('p', 'c').attempts[0].status == life.AttemptStatus.SPAWNING
    with pytest.raises(life.LifecycleConflict):
        launch(store, token, lambda: created.append(2) or 'pid:2', event_id='retry')
    assert created == [1]


@pytest.mark.parametrize('deny_on', [1, 2])
def test_authorization_denied_before_sensitive_creation(claimed, deny_on):
    store, token = claimed
    calls = []
    spawned = []
    def authorize():
        calls.append(1)
        if len(calls) == deny_on:
            raise PermissionError('authorization changed')
    with pytest.raises(PermissionError):
        launch(store, token, lambda: spawned.append(1) or 'pid:1', authorize)
    assert spawned == []
    assert store.lifecycle_state('p', 'c').attempts[0].status == (
        life.AttemptStatus.LAUNCH_INTENT if deny_on == 1 else life.AttemptStatus.SPAWNING)


def test_takeover_between_marker_and_guard_prevents_creation(claimed, monkeypatch):
    store, token = claimed
    apply = store._lifecycle_apply
    def takeover(*args, **kwargs):
        result = apply(*args, **kwargs)
        if kwargs['event_id'].endswith(':prepared'):
            state = store.lifecycle_state('p', 'c')
            store.claim_owner('p', 'c', owner_id='new', expected_revision=state.revision, event_id='takeover')
        return result
    monkeypatch.setattr(store, '_lifecycle_apply', takeover)
    spawned = []
    with pytest.raises(life.LifecycleConflict):
        launch(store, token, lambda: spawned.append(1) or 'pid:1')
    assert spawned == []


def test_takeover_cannot_commit_during_creation(claimed):
    store, token = claimed
    entered, release, trying, changed = Event(), Event(), Event(), Event()
    def spawn():
        entered.set()
        assert release.wait(2)
        return 'pid:owned'
    def takeover():
        other = ConversationStore(store.db_path)
        trying.set()
        # Expected revision after the guarded RUNNING transition commits.
        state = other.claim_owner('p', 'c', owner_id='new', expected_revision=5, event_id='takeover')
        changed.set()
        return state
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(launch, store, token, spawn)
        assert entered.wait(2)
        taking = pool.submit(takeover)
        assert trying.wait(2)
        try:
            assert not changed.wait(0.05)
        finally:
            release.set()
        running.result()
        taking.result()
    assert changed.is_set()
    assert store.lifecycle_state('p', 'c').attempts[0].status == life.AttemptStatus.UNCERTAIN
