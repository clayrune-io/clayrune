"""Unit 7d-1: bounded recovery pass and the default-off supervisor.

Synthetic transports, keys and clocks only; no thread outlives its test.
"""
import inspect
import threading
import time

import pytest

from test_agent_mail_u7d1_factory import enroll, factory, held
from test_agent_mail_enrollment import NOW
from mc import agent_mail_deletion as deletion
from mc import agent_mail_ownership as ownership
from mc import agent_mail_recovery as recovery_module
from mc import agent_mail_supervisor as sup
from mc import state
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_factory import StoreHeld
from mc.agent_mail_recovery import REASONS, Recovery


class Clock:
    def __init__(self, value=NOW / 1000):
        self.value = value

    def __call__(self):
        return self.value


def steps(report):
    return {s['name']: s for s in report['steps']}


def run(f, **kwargs):
    return Recovery(f, clock=kwargs.pop('clock', Clock()), retire=kwargs.pop('retire', lambda: {'retired': 0}),
                    **kwargs).run()


def queue_revocation(core, flow='flow-lost'):
    with core.store._tx() as db:
        db.execute('INSERT INTO mail_enrollment_revocations (flow,install,generation) VALUES(?,NULL,NULL)', (flow,))


def revocation_done(core):
    with core.store._read() as db:
        return [tuple(r) for r in db.execute('SELECT flow,resolved,done FROM mail_enrollment_revocations')]


def test_never_enrolled_pass_creates_nothing_and_is_clean(tmp_path):
    f = factory(tmp_path)
    report = run(f)
    assert report['ok'] is True
    assert steps(report)['store'] == dict(name='store', state='skipped', reason='not_enrolled', count=0)
    assert all(s['state'] in ('ok', 'skipped') for s in report['steps'])
    assert not (tmp_path / 'agent-mail').exists()


def test_held_store_is_reported_not_repaired_and_later_steps_are_skipped(tmp_path):
    f = factory(tmp_path)
    f.open_store(create=True)
    f.paths.store.unlink()
    report = run(factory(tmp_path))
    by = steps(report)
    assert by['store']['state'] == 'held' and by['store']['reason'] == 'store_missing'
    assert all(by[n]['state'] == 'skipped' and by[n]['reason'] == 'store_held'
               for n in ('revocations', 'flows', 'replay', 'inbound', 'cleanup'))
    assert by['retention']['state'] == 'ok', 'cache retirement is independent of the store'
    assert report['ok'] is False and not f.paths.store.exists()


def test_unresolved_cancellations_are_retained_with_no_fake_ack(tmp_path):
    f = factory(tmp_path)
    core = enroll(f)
    queue_revocation(core)
    for _ in range(3):
        report = run(f)
    revocations = steps(report)['revocations']
    assert (revocations['state'], revocations['reason'], revocations['count']) == (
        'pending', 'remote_adapter_unavailable', 1)
    assert revocation_done(core) == [('flow-lost', 0, 0)], 'no acknowledgment may be recorded'
    assert report['ok'] is False


def test_a_real_remote_that_acknowledges_clears_the_queue(tmp_path):
    class Remote:
        calls = []

        def cancel_flow(self, flow):
            self.calls.append(flow)
            return None

        def revoke(self, install, generation):
            raise AssertionError('an unassigned flow has no remote identity to revoke')
    f = factory(tmp_path)
    f.remote = Remote()
    core = enroll(f)
    queue_revocation(core)
    report = run(f)
    assert steps(report)['revocations']['state'] == 'ok' and Remote.calls == ['flow-lost']
    assert revocation_done(core) == [('flow-lost', 1, 1)]


def test_a_remote_that_fails_keeps_the_work_with_a_closed_code(tmp_path):
    class Remote:
        def cancel_flow(self, flow):
            raise OSError('SECRET-remote-text token=abc')

        def revoke(self, *args):
            raise AssertionError

    f = factory(tmp_path)
    f.remote = Remote()
    core = enroll(f)
    queue_revocation(core)
    report = run(f)
    assert steps(report)['revocations']['reason'] == 'remote_cancel_retryable'
    assert 'SECRET' not in repr(report) and revocation_done(core) == [('flow-lost', 0, 0)]


def test_flow_stages_are_reported_never_advanced(tmp_path):
    f = factory(tmp_path)
    core = enroll(f)
    clock = Clock()
    before = core.status()
    assert steps(run(f, clock=clock))['flows']['state'] == 'ok'
    assert steps(run(f, clock=clock))['flows']['state'] == 'ok'
    with core.store._tx() as db:
        db.execute("UPDATE mail_enrollment_flow SET stage='delivery_pending'")
    assert steps(run(f, clock=clock))['flows'] == dict(
        name='flows', state='pending', reason='delivery_pending', count=0)
    with core.store._tx() as db:
        db.execute("UPDATE mail_enrollment_flow SET stage='active',ownership_expires_ms=?", (NOW - 1,))
    assert steps(run(f, clock=clock))['flows']['reason'] == 'ownership_expired'
    with core.store._tx() as db:
        db.execute("UPDATE mail_enrollment_flow SET ownership_expires_ms=?", (NOW + 10_000,))
    assert steps(run(f, clock=clock))['flows']['state'] == 'ok'
    after = core.status()
    assert (after['stage'], after['enabled']) == ('active', before['enabled']) and not after['enabled']


def test_replay_is_rate_limited_and_failures_carry_only_a_closed_code(tmp_path):
    f = factory(tmp_path)
    enroll(f)
    clock, calls = Clock(), []
    rec = Recovery(f, clock=clock, retire=lambda: {}, replay=lambda: calls.append(clock()))
    assert steps(rec.run())['replay']['state'] == 'ok'
    assert steps(rec.run())['replay'] == dict(name='replay', state='skipped', reason='backoff', count=0)
    clock.value += rec.replay_interval_s
    assert steps(rec.run())['replay']['state'] == 'ok' and len(calls) == 2

    def boom():
        raise OSError('SECRET worker error body')
    clock.value += rec.replay_interval_s
    failing = Recovery(f, clock=clock, retire=lambda: {}, replay=boom)
    result = steps(failing.run())['replay']
    assert result['state'] == 'failed' and result['reason'] == 'worker_reconcile_failed'
    assert steps(run(f))['replay']['reason'] == 'remote_adapter_unavailable', 'no replay hook means no call'


def test_a_failing_step_never_stops_the_next_and_reports_only_a_closed_code(tmp_path, capsys):
    f = factory(tmp_path)
    enroll(f)

    def explode(*args):
        raise RuntimeError('SECRET body text and a mailbox code 123-456')
    rec = Recovery(f, clock=Clock(), retire=lambda: {'retired': 2})
    rec._flows = explode
    report = rec.run()
    by = steps(report)
    assert by['flows']['state'] == 'failed' and by['flows']['reason'] == 'step_failed'
    assert by['cleanup']['state'] == 'ok' and by['retention']['count'] == 2
    assert 'SECRET' not in repr(report) and 'SECRET' not in capsys.readouterr().out
    assert {s['reason'] for s in report['steps']} <= REASONS


def test_deletion_intents_replay_once_the_store_is_healthy(tmp_path):
    f = factory(tmp_path)
    store = f.open_store(create=True)
    store.register('install1')
    store.bind_thread('install1', 'tok', 'p1', 's1')
    broken = type('B', (), dict(paths=f.paths, open_store=lambda self: (_ for _ in ()).throw(StoreHeld('store_corrupt'))))()
    assert deletion.revoke_session('p1', ['s1'], factory=broken) == 'deferred'
    report = run(f)
    assert steps(report)['deletion']['count'] == 1
    with store._read() as db:
        assert db.execute('SELECT revoked_at FROM thread_bindings').fetchone()[0] is not None
    assert steps(run(f))['deletion']['count'] == 0


def test_deletion_intents_stay_pending_while_the_store_is_held(tmp_path):
    f = factory(tmp_path)
    f.open_store(create=True)
    broken = type('B', (), dict(paths=f.paths, open_store=lambda self: (_ for _ in ()).throw(StoreHeld('store_corrupt'))))()
    deletion.revoke_project('p1', factory=broken)
    f.paths.store.unlink()
    report = run(factory(tmp_path))
    assert steps(report)['deletion'] == dict(name='deletion', state='pending', reason='deletion_pending', count=1)


def test_inbound_calls_are_bounded_and_idle_ends_them(tmp_path):
    f = factory(tmp_path)
    enroll(f)
    launder, wake = [], []

    def busy(log):
        def call():
            log.append(1)
            return {'state': 'submitted'}
        return call
    rec = Recovery(f, clock=Clock(), retire=lambda: {}, launder=busy(launder), wake=lambda: wake.append(1) or {'state': 'idle'},
                   max_passes=3)
    result = steps(rec.run())['inbound']
    assert len(launder) == 3 and len(wake) == 1 and result['count'] == 3 and result['state'] == 'ok'
    disabled = Recovery(f, clock=Clock(), retire=lambda: {}, launder=lambda: {'state': 'disabled'})
    assert steps(disabled.run())['inbound'] == dict(name='inbound', state='skipped', reason='stage_disabled', count=0)


def test_cleanup_purges_expired_work_but_keeps_ownership_tombstones(tmp_path):
    from mc.agent_mail_outbound_state import initialize
    f = factory(tmp_path)
    core = enroll(f)
    initialize(core.store)
    with core.store._tx() as db:
        db.execute("INSERT INTO mail_outbound_question_channels VALUES('p','s','old','agent_mail',1)")
    run(f, clock=Clock(NOW / 1000 + 10 ** 9))
    assert ownership.count(core.store) == 1


def test_recovery_never_calls_a_human_ceremony_or_key_creation_path():
    source = inspect.getsource(recovery_module) + inspect.getsource(sup)
    for forbidden in ('.confirm(', '.verify(', '.enable(', '.disable(', '.change_identity(', '.enrollment(',
                      'Enrollment(', 'generate', 'Ed25519PrivateKey', 'CLOUDFLARE', 'urlopen', 'requests', 'socket'):
        assert forbidden not in source, forbidden


def test_recovery_installs_the_read_only_owner_reader_even_with_every_flag_off(tmp_path, monkeypatch):
    from mc.agent_mail_outbound_state import initialize
    f = factory(tmp_path)
    core = enroll(f)
    initialize(core.store)
    with core.store._tx() as db:
        db.execute("INSERT INTO mail_outbound_question_channels VALUES('p','s','r','agent_mail',9e12)")
    monkeypatch.setattr(ownership, '_reader', None)
    assert ownership.recover('p', 's', 'r') is None
    run(f)
    assert ownership.recover('p', 's', 'r') == 'agent_mail'
    assert state.CONFIG.get('agent_mail_outbound_enabled') is not True


# --- supervisor ------------------------------------------------------------------------------
class Stub:
    def __init__(self, reports=None):
        self.calls, self.reports = 0, list(reports or [])
        self.gate = None

    def run(self):
        self.calls += 1
        if self.gate:
            self.gate.wait(5)
        value = self.reports.pop(0) if len(self.reports) > 1 else (self.reports[0] if self.reports else CLEAN)
        if isinstance(value, BaseException):
            raise value
        return value


CLEAN = {'ok': True, 'steps': [{'name': 'store', 'state': 'ok', 'reason': 'ok', 'count': 0}]}
BAD = {'ok': False, 'steps': [{'name': 'store', 'state': 'held', 'reason': 'store_missing', 'count': 0}]}


def supervisor(stub, enabled=True, **kwargs):
    return sup.Supervisor(stub, config={sup.FLAG: enabled}, **kwargs)


def test_default_is_off_no_thread_and_no_recovery_pass():
    stub = Stub()
    s = supervisor(stub, enabled=False)
    assert s.start() is False and s._thread is None and stub.calls == 0
    assert s.status()['state'] == 'off'
    assert sup.Supervisor(stub, config={}).enabled() is False


def test_start_and_stop_are_idempotent_and_leave_no_thread():
    stub = Stub()
    s = supervisor(stub, interval_s=0.01)
    assert s.start() is True and s.start() is False
    deadline = time.time() + 5
    while stub.calls < 2 and time.time() < deadline:
        time.sleep(0.01)
    assert stub.calls >= 2
    thread = s._thread
    s.stop()
    s.stop()
    assert not thread.is_alive() and s._thread is None
    assert s.start() is True
    s.stop()
    assert not any(t.name == 'agent-mail-supervisor' and t.is_alive() for t in threading.enumerate())


def test_disabling_the_flag_stops_further_passes_without_killing_the_thread_logic():
    stub, cfg = Stub(), {sup.FLAG: True}
    s = sup.Supervisor(stub, config=cfg, interval_s=0.01)
    s.start()
    deadline = time.time() + 5
    while stub.calls < 1 and time.time() < deadline:
        time.sleep(0.01)
    cfg[sup.FLAG] = False
    time.sleep(0.1)
    settled = stub.calls
    time.sleep(0.1)
    s.stop()
    assert stub.calls == settled


def test_backoff_doubles_is_capped_and_a_clean_pass_resets_it():
    s = supervisor(Stub([BAD, BAD, BAD, CLEAN]), interval_s=10, max_backoff_s=60)
    delays = []
    for _ in range(3):
        s.tick()
        delays.append(s.delay())
    assert delays == [20, 40, 60]
    s.tick()
    assert s.failures == 0 and s.delay() == 10 and s.last_success is not None


def test_a_raising_recovery_is_contained_and_the_thread_keeps_running(capsys):
    stub = Stub([RuntimeError('SECRET body'), CLEAN])
    s = supervisor(stub, interval_s=0.01, max_backoff_s=0.02)
    s.start()
    deadline = time.time() + 5
    while stub.calls < 3 and time.time() < deadline:
        time.sleep(0.01)
    alive = s._thread.is_alive()
    s.stop()
    assert alive and stub.calls >= 3
    assert 'SECRET' not in capsys.readouterr().out and 'SECRET' not in repr(s.report)


def test_overlapping_ticks_are_skipped_not_queued():
    stub = Stub()
    stub.gate = threading.Event()
    s = supervisor(stub)
    first = threading.Thread(target=s.tick)
    first.start()
    deadline = time.time() + 5
    while stub.calls < 1 and time.time() < deadline:
        time.sleep(0.005)
    assert s.tick()['skipped'] == 'tick_in_progress'
    stub.gate.set()
    first.join(5)
    assert stub.calls == 1


def test_a_live_thread_is_not_reported_healthy_when_the_last_pass_failed():
    s = supervisor(Stub([BAD]))
    s.tick()
    status = s.status()
    assert status['state'] == 'dead' and status['reason'] == 'store_missing', 'enabled but no thread'
    s.start()
    s.stop()
    s2 = supervisor(Stub([BAD]), interval_s=60)
    s2.start()
    deadline = time.time() + 5
    while s2.last_attempt is None and time.time() < deadline:
        time.sleep(0.01)
    live = s2.status()
    s2.stop()
    assert live['thread_alive'] and live['state'] == 'degraded' and live['reason'] == 'store_missing'
    ok = supervisor(Stub([CLEAN]), interval_s=60)
    ok.start()
    deadline = time.time() + 5
    while ok.last_attempt is None and time.time() < deadline:
        time.sleep(0.01)
    healthy = ok.status()['state']
    ok.stop()
    assert healthy == 'ok'
    assert set(live) >= {'state', 'last_attempt', 'last_success', 'consecutive_failures', 'holds', 'retention'}


def test_status_is_bounded_metadata_with_store_and_ownership_counts(tmp_path):
    f = factory(tmp_path)
    enroll(f)
    s = sup.Supervisor(Recovery(f, clock=Clock(), retire=lambda: {}), config={sup.FLAG: True}, factory=f)
    s.tick()
    status = s.status()
    assert status['store'] == {'state': 'ok', 'reason': ''}
    assert status['ownership'] == {'rows': 0, 'capacity': ownership.DEFAULT_CAPACITY} \
        or status['ownership'] == {'rows': 0}
    assert 'steps' in status and all(set(x) == {'name', 'state', 'reason', 'count'} for x in status['steps'])


def test_boot_hook_installs_the_reader_with_the_flag_off_and_starts_no_thread(monkeypatch, tmp_path):
    from mc import agent_mail_factory as fac
    monkeypatch.setattr(sup, '_supervisor', None)
    monkeypatch.setattr(ownership, '_reader', None)
    fac.reset_for_tests(factory(tmp_path))
    monkeypatch.setitem(state.CONFIG, sup.FLAG, False)
    try:
        result = sup.start_default(None)
        assert result._thread is None and ownership._reader is not None
        assert sup.start_default(None) is result, 'safe to call twice'
        sup.stop_default()
        sup.stop_default()
    finally:
        fac.reset_for_tests(None)
        sup._supervisor = None
        ownership.install_reader(None)
