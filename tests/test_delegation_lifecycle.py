"""Ownership and shutdown tests for the durable delegation drain loop."""

import threading
import time

import pytest

from mc.blueprints import agent_routes as ar
from mc.delegation_delivery import DeliveryStore


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    monkeypatch.setattr(ar, '_delivery_path', tmp_path / 'delivery.sqlite3')
    monkeypatch.setattr(ar, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(ar, '_delivery_store', None)
    monkeypatch.setattr(ar, '_delivery_started', False)
    monkeypatch.setattr(ar, '_delivery_thread', None)
    monkeypatch.setattr(ar, '_delivery_stop_event', None)
    monkeypatch.setattr(ar, '_delivery_stop_in_progress', False)
    monkeypatch.setattr(ar, '_delivery_shutdown_requested', threading.Event())
    yield tmp_path
    result = ar.stop_delegation_delivery(1.0)
    assert not result['alive']


def test_concurrent_start_is_idempotent_and_retains_one_owner(lifecycle, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def drain(store, *, send_outbox, process_inbox):
        calls.append(store.path)
        entered.set()
        release.wait(2)
        return 0

    monkeypatch.setattr(ar, 'drain_once', drain)
    results = []
    barrier = threading.Barrier(8)

    def start():
        barrier.wait()
        results.append(ar.start_delegation_delivery(interval_s=60))

    threads = [threading.Thread(target=start) for _ in range(8)]
    for thread in threads:
        thread.start()
    assert entered.wait(2)
    for thread in threads:
        thread.join(2)
    assert len(results) == 8
    assert len({id(thread) for thread in results}) == 1
    assert ar._delivery_thread is results[0]
    release.set()


def test_stop_during_wait_is_interruptible_and_clean_restart_has_one_loop(lifecycle, monkeypatch):
    calls = []
    monkeypatch.setattr(ar, 'drain_once', lambda *args, **kwargs: calls.append(time.monotonic()) or 0)
    first = ar.start_delegation_delivery(interval_s=60)
    deadline = time.monotonic() + 2
    while not calls and time.monotonic() < deadline:
        time.sleep(0.01)
    assert calls
    result = ar.stop_delegation_delivery(1)
    assert result == {'requested': True, 'joined': True, 'alive': False,
                      'timed_out': False, 'self_join': False}
    second = ar.start_delegation_delivery(interval_s=60)
    assert second is not first
    assert ar.stop_delegation_delivery(1)['joined']
    assert len(calls) == 2


def test_stop_timeout_preserves_inflight_owner_and_no_duplicate_loop(lifecycle, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    calls = []

    def drain(*args, **kwargs):
        calls.append(1)
        entered.set()
        release.wait(2)
        return 0

    monkeypatch.setattr(ar, 'drain_once', drain)
    owner = ar.start_delegation_delivery(interval_s=60)
    assert entered.wait(2)
    timed = ar.stop_delegation_delivery(0.01)
    assert timed['requested'] and timed['timed_out'] and timed['alive']
    assert ar.start_delegation_delivery(interval_s=60) is owner
    release.set()
    assert ar.stop_delegation_delivery(1)['joined']
    assert len(calls) == 1


def test_startup_failure_leaves_retryable_empty_ownership(lifecycle, monkeypatch):
    class BrokenStore:
        def __init__(self, path):
            raise OSError('temporary sqlite init failure')

    monkeypatch.setattr(ar, 'DeliveryStore', BrokenStore)
    with pytest.raises(OSError, match='temporary sqlite'):
        ar.start_delegation_delivery()
    assert ar._delivery_store is None
    assert ar._delivery_thread is None
    assert ar._delivery_stop_event is None
    assert ar._delivery_started is False


def test_shutdown_admission_blocks_waiting_handoff_and_preserves_pending(
        lifecycle, monkeypatch):
    store = DeliveryStore(lifecycle / 'delivery.sqlite3')
    payload = {'event_id': 'child:turn:1', 'child_session_id': 'child',
               'status': 'completed', 'message': 'result'}
    store.enqueue('event-1', 'p', 'parent', payload)
    store.accept('event-1', 'p', 'parent', payload)
    manager = type('Manager', (), {'lock': threading.RLock()})()
    monkeypatch.setattr(ar, 'get_manager', lambda project_id: manager)
    monkeypatch.setattr(ar, '_deliver_outbox', lambda row: None)
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *args: '')
    ar.agent_sessions['parent'] = {'project_id': 'p', 'status': 'completed',
                                   'provider': 'codex', 'agent_model': 'fake',
                                   'incognito': False}
    launches = []
    monkeypatch.setattr(ar, 'agent_followup', lambda project_id: launches.append(1))
    entered = threading.Event()
    original_process = ar._process_inbox

    def process(row):
        entered.set()
        return original_process(row)

    monkeypatch.setattr(ar, '_process_inbox', process)
    manager.lock.acquire()
    try:
        ar.start_delegation_delivery(interval_s=60)
        assert entered.wait(2)
        timed = ar.stop_delegation_delivery(0.01)
        assert timed['timed_out'] and timed['alive']
        # This is the cleanup admission boundary: release only after stop has
        # closed admission, then let the waiting handoff revalidate.
    finally:
        manager.lock.release()
    assert ar.stop_delegation_delivery(1)['joined']
    assert launches == []
    assert ar._delivery_store is not None
    assert ar._delivery_store.status('inbox', 'event-1', 'p')['state'] == 'pending'


def test_completion_can_enqueue_after_loop_stop_and_store_is_retained(lifecycle, monkeypatch):
    entered = threading.Event()
    monkeypatch.setattr(ar, 'drain_once', lambda *args, **kwargs: entered.set() or 0)
    ar.start_delegation_delivery(interval_s=60)
    assert entered.wait(2)
    assert ar.stop_delegation_delivery(1)['joined']
    store = ar._delivery_store
    assert store is not None
    child = {'session_id': 'child', 'status': 'completed', 'provider': 'codex',
             '_delegation_turn': 1, 'task': 'task'}
    assert ar._notify_agent_spawner('p', 'parent', child, 'full result') is True
    assert store.status('outbox', 'child:turn:1', 'p')['state'] == 'pending'


def test_live_rewire_refuses_without_discarding_owner(lifecycle, monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def drain(*args, **kwargs):
        entered.set()
        release.wait(2)
        return 0

    monkeypatch.setattr(ar, 'drain_once', drain)
    owner = ar.start_delegation_delivery(interval_s=60)
    assert entered.wait(2)
    import inspect
    with pytest.raises(RuntimeError, match='rewire'):
        ar.wire(**{name: None for name in inspect.signature(ar._wire_unlocked).parameters})
    assert ar._delivery_thread is owner
    release.set()


def test_thread_start_failure_and_self_join_are_explicit(lifecycle, monkeypatch):
    def fail_start(self):
        raise RuntimeError('thread start failed')

    with monkeypatch.context() as mp:
        mp.setattr(ar.threading.Thread, 'start', fail_start)
        with pytest.raises(RuntimeError, match='thread start'):
            ar.start_delegation_delivery()
    assert ar._delivery_thread is None
    assert ar._delivery_store is None

    result = []
    entered = threading.Event()

    def drain(*args, **kwargs):
        entered.set()
        result.append(ar.stop_delegation_delivery(0))
        return 0

    monkeypatch.setattr(ar, 'drain_once', drain)
    ar.start_delegation_delivery(interval_s=60)
    assert entered.wait(2)
    assert ar.stop_delegation_delivery(1)['joined']
    assert result and result[0]['self_join'] and result[0]['timed_out']


def test_stop_signals_captured_owner_and_admission_under_lifecycle_lock(
        lifecycle, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    monkeypatch.setattr(ar, 'drain_once',
                        lambda *args, **kwargs: entered.set() or release.wait(2) or 0)
    ar.start_delegation_delivery(interval_s=60)
    assert entered.wait(2)
    original_shutdown = ar._delivery_shutdown_requested
    original_stop = ar._delivery_stop_event
    observations = []

    class ObservedEvent:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        def set(self):
            observations.append(ar._delivery_lifecycle_lock._is_owned())
            self.wrapped.set()

        def is_set(self):
            return self.wrapped.is_set()

    monkeypatch.setattr(ar, '_delivery_shutdown_requested', ObservedEvent(original_shutdown))
    monkeypatch.setattr(ar, '_delivery_stop_event', ObservedEvent(original_stop))
    timed = ar.stop_delegation_delivery(0.01)
    assert timed['timed_out'] and timed['alive']
    assert observations == [True, True]
    release.set()
    assert ar.stop_delegation_delivery(1)['joined']


def test_start_cannot_replace_owner_while_stop_join_is_in_progress(
        lifecycle, monkeypatch):
    entered = threading.Event()
    release_drain = threading.Event()
    join_entered = threading.Event()
    release_join = threading.Event()
    monkeypatch.setattr(ar, 'drain_once',
                        lambda *args, **kwargs: entered.set() or release_drain.wait(2) or 0)
    owner = ar.start_delegation_delivery(interval_s=60)
    assert entered.wait(2)
    original_join = owner.join

    def delayed_join(timeout=None):
        join_entered.set()
        assert release_join.wait(2)
        return original_join(timeout)

    owner.join = delayed_join
    stop_result = []
    stopper = threading.Thread(target=lambda: stop_result.append(
        ar.stop_delegation_delivery(1)))
    stopper.start()
    assert join_entered.wait(2)
    # stop has closed admission and is outside the lifecycle lock in join;
    # start may observe the owner, but must not create a second loop.
    assert ar.start_delegation_delivery(interval_s=60) is owner
    release_drain.set()
    release_join.set()
    stopper.join(2)
    assert stop_result and stop_result[0]['joined']


def test_wire_and_start_serialize_without_orphaning_new_owner(lifecycle, monkeypatch):
    import inspect

    hydration_entered = threading.Event()
    release_hydration = threading.Event()
    starter_entered = threading.Event()
    start_result = []
    monkeypatch.setattr(ar, 'drain_once', lambda *args, **kwargs: 0)

    def hydrate():
        hydration_entered.set()
        release_hydration.wait(2)

    monkeypatch.setattr(ar, '_hydrate_provider_env_into_os', hydrate)
    kwargs = {name: (lambda *args, **kw: None)
              for name in inspect.signature(ar._wire_unlocked).parameters}
    kwargs.update({
        'data_dir': lifecycle / 'projects',
        'uploads_dir': lifecycle / 'uploads',
        'app_dir': lifecycle,
        'port': 0,
        'shared_rules_path': lifecycle / 'rules.md',
        'provider_env_path': lifecycle / 'provider_env.json',
        'claude_home': lifecycle,
        'popen_flags': 0,
        'startupinfo': None,
    })
    wire_thread = threading.Thread(target=lambda: ar.wire(**kwargs))
    wire_thread.start()
    assert hydration_entered.wait(2)
    def start_after_barrier():
        starter_entered.set()
        start_result.append(ar.start_delegation_delivery(interval_s=60))

    starter = threading.Thread(target=start_after_barrier)
    starter.start()
    assert starter_entered.wait(2)
    assert starter.is_alive()
    release_hydration.set()
    wire_thread.join(2)
    starter.join(2)
    assert not wire_thread.is_alive() and not starter.is_alive()
    assert len(start_result) == 1
    assert ar._delivery_thread is start_result[0]
    assert ar.stop_delegation_delivery(1)['joined']


@pytest.mark.parametrize('interval', [0, -1, float('nan'), float('inf')])
def test_interval_must_be_finite_and_positive(lifecycle, interval):
    with pytest.raises(ValueError, match='finite and positive'):
        ar.start_delegation_delivery(interval)


@pytest.mark.parametrize('timeout', [float('nan'), float('inf'), -1])
def test_stop_timeout_must_be_finite_and_nonnegative(lifecycle, timeout):
    with pytest.raises(ValueError, match='finite and nonnegative'):
        ar.stop_delegation_delivery(timeout)
