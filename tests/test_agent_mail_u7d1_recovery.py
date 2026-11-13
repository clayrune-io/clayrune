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
               for n in ('revocations', 'flows', 'pull', 'acks', 'inbound', 'cleanup'))
    assert by['retention']['state'] == 'ok', 'cache retirement is independent of the store'
    assert report['ok'] is False and not f.paths.store.exists()


def test_legacy_cancellations_are_retained_while_pull_store_is_held(tmp_path):
    # Retired v1 cancellation retry: recovery must not adopt a legacy store.
    f = factory(tmp_path)
    core = enroll(f)
    queue_revocation(core)
    report = run(f)
    assert steps(report)['store']['reason'] == 'schema_corrupt'
    assert revocation_done(core) == [('flow-lost', 0, 0)]
    assert not report['ok']


def test_legacy_remote_ack_cannot_clear_pull_recovery_work(tmp_path):
    f = factory(tmp_path)
    calls = []
    f.remote = type('Remote', (), {'cancel_flow': lambda self, flow: calls.append(flow)})()
    core = enroll(f)
    queue_revocation(core)
    assert steps(run(f))['store']['reason'] == 'schema_corrupt'
    assert calls == [] and revocation_done(core) == [('flow-lost', 0, 0)]


def test_legacy_remote_is_never_called_by_pull_recovery(tmp_path):
    f = factory(tmp_path)
    def forbidden(*args):
        pytest.fail('retired v1 remote must not be called')
    f.remote = type('Remote', (), {'cancel_flow': forbidden, 'revoke': forbidden})()
    core = enroll(f)
    queue_revocation(core)
    report = run(f)
    assert steps(report)['store']['reason'] == 'schema_corrupt'
    assert revocation_done(core) == [('flow-lost', 0, 0)]


def test_legacy_flow_is_held_and_never_advanced_or_renewed(tmp_path):
    f = factory(tmp_path)
    core = enroll(f)
    before = core.status()
    report = run(f)
    assert steps(report)['store']['reason'] == 'schema_corrupt'
    assert steps(report)['flows']['reason'] == 'store_held'
    assert core.status() == before


def test_retired_push_replay_hook_is_rejected(tmp_path):
    with pytest.raises(TypeError):
        Recovery(factory(tmp_path), replay=lambda: pytest.fail('retired push path'))


def test_a_failing_step_never_stops_the_next_and_reports_only_a_closed_code(tmp_path, capsys):
    f = factory(tmp_path)
    enroll(f)
    f.bootstrap(authorize=lambda: True)

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
    f.bootstrap(authorize=lambda: True)
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
    f.bootstrap(authorize=lambda: True)
    initialize(core.store)
    with core.store._tx() as db:
        db.execute("INSERT INTO mail_outbound_question_channels VALUES('p','s','old','agent_mail',1)")
    run(f, clock=Clock(NOW / 1000 + 10 ** 9))
    assert ownership.count(core.store) == 1


def test_recovery_never_calls_a_human_ceremony_or_key_creation_path():
    source = inspect.getsource(recovery_module) + inspect.getsource(sup)
    for forbidden in ('.confirm(', '.verify(', '.enable(', '.disable(', '.change_identity(', '.enrollment(',
                      'Enrollment(', 'generate', 'Ed25519PrivateKey', 'CLOUDFLARE', 'urlopen', 'requests',
                      'socket.socket(', 'socket.create_connection('):
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
    assert 'steps' in status and all({'name', 'state', 'reason', 'count'} <= set(x) <= {'name', 'state', 'reason', 'count', 'details'} for x in status['steps'])


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
