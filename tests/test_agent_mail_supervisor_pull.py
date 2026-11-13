"""Unit 24: server-owned lifecycle with fake receive workers, never network."""
from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from mc import agent_mail_supervisor as module
from mc.agent_mail_factory import StoreHeld
from test_agent_mail_pull_client import World, eventually
from test_agent_mail_pull_receiver import INSTALL


CLEAN = {'ok': True, 'steps': [{'name': 'store', 'state': 'ok', 'reason': 'ok', 'count': 0}]}


class Client:
    def __init__(self, world):
        self.store, self.seal, self.install_id = world.store, world.seal, INSTALL
        self.reason = ''
        self.starts = 0
        self.thread = None
        self.exit = threading.Event()
        self.release_on_stop = True
        self.start_error = self.stop_error = None
        self.timeouts = []

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        if self.start_error:
            raise self.start_error
        assert not self.running, 'second worker'
        self.starts += 1
        self.exit.clear()
        self.thread = threading.Thread(target=self.exit.wait, name='fake-mail-pull', daemon=True)
        self.thread.start()

    def stop(self, timeout=12):
        self.timeouts.append(timeout)
        if self.stop_error:
            raise self.stop_error
        if self.release_on_stop:
            self.exit.set()
        if self.thread:
            self.thread.join(timeout)
        return not self.running

    def cleanup(self):
        self.exit.set()
        if self.thread:
            self.thread.join(2)
        assert not self.running


@pytest.fixture
def env(tmp_path):
    world = World(tmp_path)
    with world.store._tx() as db:
        db.execute('DELETE FROM mail_pull_connections')
    client = Client(world)
    factory = SimpleNamespace(pull_client=lambda: client, health=lambda: {'state': 'ok', 'reason': ''},
                              open_store=lambda: world.store)
    recovery = SimpleNamespace(run=lambda: CLEAN)
    config = {module.FLAG: True}
    supervisor = module.Supervisor(recovery, factory=factory, config=config,
                                   clock=lambda: world.now / 1000, interval_s=.01)
    result = SimpleNamespace(w=world, client=client, factory=factory, recovery=recovery,
                             config=config, s=supervisor)
    try:
        yield result
    finally:
        client.start_error = client.stop_error = None
        client.release_on_stop = True
        supervisor.stop(2)
        client.cleanup()
        if supervisor._thread:
            supervisor._thread.join(2)
            assert not supervisor._thread.is_alive()


def connected(env, *, epoch=2, reason='', failures=0, complete=1):
    with env.w.store._tx() as db:
        db.execute('''INSERT OR REPLACE INTO mail_pull_connections
            VALUES(?,1,1,?,?,?, ?,0,?,?,0,?,?)''',
            (INSTALL, epoch, '%032x' % epoch, env.w.now, env.w.now + 86400000,
             complete, env.w.now, failures, reason))


def test_import_and_construction_start_no_worker_or_network():
    code = '''
import sys, threading
def forbidden(*a, **kw):
    raise AssertionError('external work during import/construction')
threading.Thread.start = forbidden
def audit(event, args):
    if event in {'socket.connect', 'socket.bind', 'sqlite3.connect', 'subprocess.Popen'}:
        forbidden()
sys.addaudithook(audit)
from mc.agent_mail_supervisor import Supervisor
Supervisor(object(), factory=object())
'''
    child = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=20)
    assert child.returncode == 0, child.stderr


def test_concurrent_start_is_one_client_and_shutdown_joins_both_workers(env):
    with ThreadPoolExecutor(max_workers=8) as pool:
        starts = list(pool.map(lambda _: env.s.start(), range(8)))
    eventually(lambda: env.client.starts == 1)
    assert sum(starts) == 1
    assert env.s.start() is False
    worker, recovery_thread = env.client.thread, env.s._thread
    env.s.stop(.5)
    assert not worker.is_alive() and not recovery_thread.is_alive()
    assert env.s._thread is None
    env.s.stop(.5)
    assert env.client.starts == 1
    assert env.s.start() is True
    eventually(lambda: env.client.starts == 2)


def test_another_supervisor_cannot_adopt_or_cancel_the_owned_client(env):
    assert env.s.start()
    eventually(lambda: env.client.running)
    other = module.Supervisor(env.recovery, factory=env.factory, config=env.config, interval_s=.01)
    try:
        other.start()
        eventually(lambda: other.failure_count > 0)
        assert other.status()['last_failure']['reason'] == 'client_already_running'
        other.stop(.5)
        assert env.client.starts == 1 and env.client.running
    finally:
        other.stop(2)


def test_distinct_factories_cannot_start_a_second_process_client(env):
    second_client = Client(env.w)
    second_factory = SimpleNamespace(pull_client=lambda: second_client,
        health=env.factory.health, open_store=env.factory.open_store)
    second = module.Supervisor(env.recovery, factory=second_factory, config=env.config, interval_s=.01)
    try:
        env.s.start()
        eventually(lambda: env.client.running)
        second.start()
        eventually(lambda: second.failure_count > 0)
        assert second.status()['last_failure']['reason'] == 'client_already_running'
        assert second_client.starts == 0 and env.client.running
        env.s.stop(2)
        # The next existing pass can acquire the released process slot.
        eventually(lambda: second_client.running)
        assert second_client.starts == 1
    finally:
        second.stop(2)
        second_client.cleanup()


def test_disabled_flag_cancels_client_and_reenable_starts_once(env):
    env.s.start()
    eventually(lambda: env.client.running)
    env.config[module.FLAG] = False
    eventually(lambda: not env.client.running)
    count = env.client.starts
    time.sleep(.04)
    assert env.client.starts == count
    env.config[module.FLAG] = True
    eventually(lambda: env.client.starts == 2)


def test_one_deadline_cancels_client_even_while_recovery_is_blocked(env):
    gate, entered = threading.Event(), threading.Event()
    def blocked():
        entered.set()
        gate.wait(3)
        return CLEAN
    env.recovery.run = blocked
    env.s.start()
    assert entered.wait(2)
    try:
        start = time.monotonic()
        env.s.stop(.05)
        assert time.monotonic() - start < .3
        assert not env.client.running and env.s._thread.is_alive()
        assert env.s.start() is False
        assert env.s.status()['last_failure']['reason'] == 'thread_shutdown_pending'
    finally:
        gate.set()
        env.s.stop(2)


def test_client_cancellation_pending_retains_ownership_and_blocks_restart(env):
    env.client.release_on_stop = False
    env.s.start()
    eventually(lambda: env.client.running)
    start = time.monotonic()
    env.s.stop(.05)
    assert time.monotonic() - start < .3
    assert env.s.start() is False
    status = env.s.status()
    assert status['state'] == 'stopping' and status['client']['running']
    assert status['last_failure']['reason'] == 'cancellation_pending'
    assert status['last_failure']['at'] == env.w.now / 1000
    assert env.client.timeouts[-1] <= .05
    env.client.release_on_stop = True
    env.s.stop(2)
    assert not env.client.running and env.s._thread is None


def test_stop_during_composition_prevents_late_client_start_or_recovery(env):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def compose():
        entered.set()
        release.wait(3)
        return env.client
    env.factory.pull_client = compose
    env.recovery.run = lambda: calls.append('run') or CLEAN
    env.s.start()
    assert entered.wait(2)
    try:
        env.s.stop(.03)
        assert env.s.start() is False
    finally:
        release.set()
        env.s.stop(2)
    assert env.client.starts == 0 and calls == []


def test_stop_exception_is_logged_and_reported_without_losing_owner(env, capsys):
    env.s.start()
    eventually(lambda: env.client.running)
    env.client.stop_error = RuntimeError('SECRET mail body')
    env.s.stop(.1)
    status = env.s.status()
    assert env.client.running and env.s.start() is False
    assert status['last_failure']['reason'] == 'client_stop_failed'
    assert status['last_failure']['at'] == env.w.now / 1000
    output = capsys.readouterr().out
    assert 'client_stop_failed' in output and 'SECRET' not in output + repr(status)
    env.client.stop_error = None


def test_start_failure_is_observable_and_does_not_kill_recovery(env, capsys):
    env.client.start_error = RuntimeError('SECRET token')
    env.s.start()
    eventually(lambda: env.s.last_tick is not None)
    status = env.s.status()
    assert status['thread_alive'] and status['state'] == 'degraded'
    assert status['last_failure']['reason'] == 'client_start_failed'
    assert status['last_failure']['at'] == env.w.now / 1000
    assert 'SECRET' not in capsys.readouterr().out + repr(status)
    env.client.start_error = None
    eventually(lambda: env.client.running)


def test_unexpected_loop_failure_is_recorded_and_cancels_client(env, capsys):
    def broken_delay():
        raise RuntimeError('SECRET target')
    env.s.delay = broken_delay
    env.s.start()
    eventually(lambda: env.s._thread is not None and not env.s._thread.is_alive())
    eventually(lambda: not env.client.running)
    env.s.delay = lambda: .01
    status = env.s.status()
    assert status['state'] == 'dead'
    assert any(f['reason'] == 'worker_failed' for f in status['recent_failures'])
    assert 'SECRET' not in capsys.readouterr().out + repr(status)


def test_live_thread_without_authenticated_activity_is_not_healthy(env):
    env.s.start()
    eventually(lambda: env.s.last_tick is not None)
    status = env.s.status()
    assert status['state'] == 'degraded'
    assert status['client']['state'] == 'connecting'
    assert status['client']['last_activity_ms'] is None
    assert status['last_tick'] == env.w.now / 1000
    assert status['last_client_event']['observed_at'] == env.w.now / 1000


def test_current_activity_is_reported_and_advances_without_a_recovery_tick(env):
    env.s.interval_s = 60
    env.s.start()
    eventually(lambda: env.s.last_tick is not None)
    connected(env)
    first = env.s.status()
    assert first['state'] == 'ok' and first['client']['state'] == 'connected'
    assert first['client']['last_activity_ms'] == env.w.now
    env.w.now += 1000
    connected(env)
    second = env.s.status()
    assert second['last_tick'] == first['last_tick']
    assert second['last_client_event']['last_activity_ms'] == env.w.now
    assert second['last_client_event']['observed_at'] == env.w.now / 1000


def test_a_stalled_recovery_cannot_keep_reporting_its_previous_clean_pass(env):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def recover():
        calls.append(1)
        if len(calls) > 1:
            entered.set()
            release.wait(3)
        return CLEAN
    env.recovery.run = recover
    env.s.start()
    assert entered.wait(2)
    try:
        connected(env)
        assert env.s.status()['state'] == 'ok'
        env.w.now += 60000
        status = env.s.status()
        assert status['thread_alive'] and status['client']['running']
        assert status['tick_stale'] and status['state'] == 'degraded'
        assert status['reason'] == 'worker_stalled'
        assert status['last_failure']['at'] == env.w.now / 1000
        count = status['failure_count']
        assert env.s.status()['failure_count'] == count, 'one observation per stalled interval'
    finally:
        release.set()
        env.s.stop(2)


def test_old_durable_scan_cannot_make_a_new_worker_healthy(env):
    connected(env)
    env.s.start()
    eventually(lambda: env.s.last_tick is not None)
    assert env.s.status()['client']['state'] == 'connecting'
    connected(env, epoch=3)
    assert env.s.status()['client']['state'] == 'connected'


def test_unfinished_scan_and_expired_authentication_are_visible(env):
    env.s.start()
    eventually(lambda: env.client.running)
    connected(env, complete=0)
    assert env.s.status()['client']['state'] == 'scanning'
    env.w.now += 86400001
    assert env.s.status()['client']['state'] == 'connecting'
    assert env.s.status()['state'] != 'ok'


def test_halted_client_stays_visible_and_is_not_blindly_restarted(env):
    env.s.start()
    eventually(lambda: env.client.running)
    env.client.reason = 'unauthorized'
    env.client.exit.set()
    eventually(lambda: not env.client.running)
    status = env.s.status()
    assert status['state'] == 'degraded' and status['client']['state'] == 'held'
    assert status['last_failure']['reason'] == 'unauthorized'
    assert status['last_failure']['at'] == env.w.now / 1000
    time.sleep(.04)
    assert env.client.starts == 1


def test_silent_client_exit_is_a_failure_even_when_recovery_is_clean(env):
    env.s.start()
    eventually(lambda: env.client.running)
    env.client.exit.set()
    eventually(lambda: not env.client.running)
    assert env.s.status()['last_failure']['reason'] == 'client_exited'
    assert env.s.status()['state'] == 'degraded'


def test_vault_lock_cancels_and_observed_unlock_permits_a_fresh_attempt(env, monkeypatch):
    ready = [True]
    monkeypatch.setattr(env.client.seal, 'status', lambda ref: 'ready' if ready[0] else 'vault_locked')
    env.s.start()
    eventually(lambda: env.client.running)
    ready[0] = False
    eventually(lambda: not env.client.running)
    eventually(lambda: env.s.status()['client'].get('reason') == 'vault_locked')
    ready[0] = True
    eventually(lambda: env.client.starts == 2)


def test_scope_change_restarts_only_after_the_previous_worker_exits(env):
    env.s.start()
    eventually(lambda: env.client.running)
    old = env.client.thread
    with env.w.store._tx() as db:
        db.execute('UPDATE mail_pull_generations SET manifest_sequence=manifest_sequence+1')
    eventually(lambda: env.client.starts == 2)
    assert not old.is_alive()


def test_inactive_account_does_not_start_a_client(env):
    with env.w.store._tx() as db:
        db.execute('UPDATE mail_pull_accounts SET local_enabled=0')
    env.s.start()
    eventually(lambda: env.s.last_tick is not None)
    assert env.client.starts == 0
    assert env.s.status()['client']['reason'] == 'local_disabled'


def test_status_read_failure_is_logged_and_held(env, monkeypatch, capsys):
    env.s.start()
    eventually(lambda: env.client.running)
    env.s.stop()
    # Preserve a live owner while isolating the read failure from the driver.
    env.client.start()
    env.s._owns_client = True
    def broken_read():
        raise RuntimeError('SECRET database contents')
    monkeypatch.setattr(env.client.store, '_read', broken_read)
    status = env.s.status()
    assert status['client']['state'] == 'held'
    assert status['last_failure']['reason'] == 'status_failed'
    assert status['last_failure']['at'] == env.w.now / 1000
    assert 'SECRET' not in capsys.readouterr().out + repr(status)


def test_ownership_status_failure_is_in_the_same_response(env, capsys):
    def broken_health():
        raise RuntimeError('SECRET path')
    env.factory.health = broken_health
    status = env.s.status()
    assert status['ownership'] == {'rows': -1}
    assert status['last_failure']['source'] == 'ownership_status'
    assert status['last_failure']['at'] == env.w.now / 1000
    assert 'SECRET' not in capsys.readouterr().out + repr(status)


def test_recovered_connection_retains_failure_history_with_its_observation_time(env):
    env.s.start()
    eventually(lambda: env.client.running)
    env.client.reason = 'connection_lost'
    connected(env, reason='connection_lost', failures=3)
    first = env.s.status()
    assert first['client']['state'] == 'reconnecting'
    assert first['last_failure']['reason'] == 'connection_lost'
    env.w.now += 1000
    connected(env, epoch=3)
    second = env.s.status()
    assert second['client']['state'] == 'connected'
    assert second['last_failure'] == first['last_failure']
    assert second['last_client_event']['observed_at'] == env.w.now / 1000


def test_recovery_exceptions_and_malformed_reports_are_retained_after_success(env, capsys):
    def broken():
        raise RuntimeError('SECRET payload')
    env.recovery.run = broken
    env.s.tick()
    env.recovery.run = lambda: {}
    env.s.tick()
    env.recovery.run = lambda: CLEAN
    env.s.tick()
    status = env.s.status()
    assert status['consecutive_failures'] == 0
    assert status['failure_count'] == 2
    assert status['last_failure']['source'] == 'recovery'
    assert status['last_failure']['at'] == env.w.now / 1000
    assert 'SECRET' not in capsys.readouterr().out + repr(status)


def test_failure_history_is_bounded_without_discarding_total_count(env):
    env.recovery.run = lambda: {'ok': False, 'steps': [
        {'name': 'store', 'state': 'held', 'reason': 'store_missing', 'count': 0}]}
    for _ in range(30):
        env.s.tick()
    status = env.s.status()
    assert len(status['recent_failures']) == 20 and status['failure_count'] == 30


def test_never_enrolled_factory_is_not_bootstrapped_and_no_client_is_started(env):
    def absent():
        raise StoreHeld('not_enrolled')
    env.factory.pull_client = absent
    env.s.start()
    eventually(lambda: env.s.last_tick is not None)
    assert env.client.starts == 0
    assert env.s.status()['client'] == {'state': 'not_configured', 'running': False,
                                       'reason': 'not_enrolled'}


def test_thread_start_failure_is_logged_and_surfaced(env, monkeypatch, capsys):
    def broken(*args):
        raise RuntimeError('SECRET command')
    monkeypatch.setattr(threading.Thread, 'start', broken)
    assert env.s.start() is False
    status = env.s.status()
    assert status['thread_alive'] is False and env.client.starts == 0
    assert status['last_failure']['reason'] == 'thread_start_failed'
    assert 'SECRET' not in capsys.readouterr().out + repr(status)


def test_clock_exception_is_logged_and_surfaced_with_a_fallback_timestamp(env, capsys):
    def broken_clock():
        raise RuntimeError('SECRET value')
    env.s.clock = broken_clock
    env.s.tick()
    status = env.s.status()
    assert status['last_failure']['reason'] == 'clock_failed'
    assert isinstance(status['last_failure']['at'], float)
    assert status['failure_count'] > 0
    assert 'SECRET' not in capsys.readouterr().out + repr(status)


def test_existing_default_hooks_own_one_client_and_install_the_reader(env, monkeypatch):
    from mc import agent_mail_factory, agent_mail_ownership, state
    monkeypatch.setattr(module, '_supervisor', None)
    monkeypatch.setattr(agent_mail_factory, 'get', lambda: env.factory)
    monkeypatch.setattr(module, '_default_recovery', lambda factory, app: env.recovery)
    monkeypatch.setattr(agent_mail_ownership, '_reader', None)
    monkeypatch.setitem(state.CONFIG, module.FLAG, True)
    first = module.start_default(None)
    try:
        assert module.start_default(None) is first is module.get()
        eventually(lambda: env.client.running)
        assert env.client.starts == 1 and agent_mail_ownership._reader is not None
        module.stop_default()
        module.stop_default()
        assert first._thread is None and not env.client.running
    finally:
        first.stop(2)
