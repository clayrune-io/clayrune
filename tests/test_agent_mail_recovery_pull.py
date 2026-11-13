"""Unit 23: passive pull/ack recovery and verified, bounded fence retries.

Synthetic signed service responses and private temporary stores only.
"""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace
import inspect
import subprocess
import sys

import pytest

from mc import agent_mail_recovery as module
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_recovery import Recovery, STEPS, BACKOFF_MAX_MS
from test_agent_mail_service_client_pull import World
from test_agent_mail_pull_receiver import INSTALL, NOW
from test_agent_mail_factory_pull import populated, factory


def by(report):
    return {s['name']: s for s in report['steps']}


@pytest.fixture
def w(tmp_path):
    w = World(tmp_path)
    w.factory = SimpleNamespace(paths=SimpleNamespace(root=tmp_path),
        require_pull_store=lambda: w.store, open_store=lambda: w.store,
        service_client=lambda: w.client)
    return w


def recovery(w, **kw):
    return Recovery(w.factory, clock=lambda: w.now / 1000, retire=lambda: {'retired': 0}, **kw)


def test_import_and_construction_do_no_io_or_start_threads():
    code = """
import sys, threading
def forbidden(*args, **kw):
    raise AssertionError('unexpected external work')
threading.Thread.start = forbidden
from mc.agent_mail_recovery import Recovery
def audit(event, args):
    if event in {'socket.connect','socket.bind','sqlite3.connect','subprocess.Popen'}:
        forbidden()
sys.addaudithook(audit)
Recovery(object())
"""
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('limit', [0, -1, 101, True, 1.5, None])
def test_batch_limit_cannot_disable_bounds(limit):
    with pytest.raises(ValueError):
        Recovery(object(), max_passes=limit)


def test_never_enrolled_and_bootstrap_empty_create_no_identity(tmp_path):
    f = factory(tmp_path)
    report = Recovery(f, retire=lambda: {}).run()
    assert report['ok'] and by(report)['store']['reason'] == 'not_enrolled'
    assert not f.paths.root.exists()
    f.bootstrap(authorize=lambda: True)
    report = Recovery(f, retire=lambda: {}).run()
    assert report['ok'] and by(report)['pull']['reason'] == 'not_enrolled'
    assert not f.paths.identity.exists()


@pytest.mark.parametrize('damage,reason', [('missing', 'store_missing'), ('partial', 'schema_corrupt')])
def test_real_factory_store_holds_never_repair_and_retention_still_runs(tmp_path, damage, reason):
    f = factory(tmp_path)
    store = f.bootstrap(authorize=lambda: True)
    if damage == 'missing':
        f.paths.store.unlink()
    else:
        with store._tx() as db:
            db.execute('DROP INDEX mail_pull_ack_due')
    report = Recovery(f, retire=lambda: {'retired': 3}).run()
    assert by(report)['store']['reason'] == reason and not report['ok']
    assert by(report)['retention']['count'] == 3
    assert by(report)['acks']['reason'] == 'store_held'


def test_factory_composition_uses_shared_checked_store_without_network_or_activation(tmp_path, monkeypatch):
    f, h, install = populated(tmp_path)
    def forbidden(*a, **kw):
        pytest.fail('recovery must not originate lifecycle or socket work')
    monkeypatch.setattr(h, 'transport', forbidden)
    monkeypatch.setattr(f, 'signup', forbidden)
    monkeypatch.setattr(f, 'pull_client', forbidden)
    monkeypatch.setattr(f, 'pull_receiver', forbidden)
    before = f.require_pull_store()
    with before._read() as db:
        snapshot = [tuple(r) for r in db.execute('SELECT * FROM mail_pull_accounts')]
    result = by(Recovery(f, clock=f.clock, retire=lambda: {}).run())
    assert result['store']['state'] == 'ok' and result['flows']['reason'] == 'flow_pending'
    with before._read() as db:
        assert snapshot == [tuple(r) for r in db.execute('SELECT * FROM mail_pull_accounts')]


def test_fence_signed_receipt_commits_exact_scope_and_repeated_pass_is_inert(w):
    w.fence_row()
    before = w.rows('mail_pull_accounts')
    rec = recovery(w)
    result = by(rec.run())['revocations']
    assert result['state'] == 'ok' and result['count'] == 1
    assert result['details'] == {'pending_count': 0, 'failures': []}
    row = w.rows('mail_pull_fences')[0]
    assert row['state'] == 'acked' and row['receipt_id'] == 'f' * 32
    assert w.rows('mail_pull_accounts') == before
    rec.run()
    assert len(w.calls) == 1


@pytest.mark.parametrize('mode', ['cancel', 'revoke'])
def test_fences_work_without_vault_and_can_target_old_generation(w, mode):
    w.master = None
    w.fence_row(mode=mode, generation=1)
    with w.store._tx() as db:
        db.execute('UPDATE mail_pull_accounts SET generation=2')
    assert by(recovery(w).run())['revocations']['count'] == 1
    assert w.rows('mail_pull_fences')[0]['state'] == 'acked'


@pytest.mark.parametrize('damage', ['unsigned', 'signature', 'scope', 'receipt', 'service_key'])
def test_unverified_fence_never_settles_and_failure_is_logged_and_surfaced(w, damage, capsys):
    w.fence_row()
    if damage == 'unsigned':
        w.change = lambda *args: (401, {}, b'private unauthorized')
    elif damage == 'signature':
        w.change = lambda s, h, b: (s, dict(h, **{'x-mail-signature': 'bad'}), b)
    elif damage == 'scope':
        w.change = lambda s, h, b: (s, dict(h, **{'x-mail-generation': '2'}), b)
    elif damage == 'service_key':
        w.review = lambda: (replace(w.pin, expires_at_ms=0), '0' * 64)
    else:
        w.change = lambda s, h, b: (s, h, b.replace(b'f' * 32, b'0' * 32))
    report = recovery(w).run()
    result = by(report)['revocations']
    assert result['state'] == 'failed' and result['details']['failures']
    row = w.rows('mail_pull_fences')[0]
    assert row['state'] == 'pending' and row['receipt_id'] is None
    assert row['next_attempt_ms'] > w.now
    assert 'revocations failed:' in capsys.readouterr().out
    assert 'private unauthorized' not in repr(report)


def test_bounded_rows_continue_after_individual_failure_and_deadline_survives_restart(w, capsys):
    for n in range(4):
        w.fence_row(op=f'{n:032x}', flow=f'{n+10:032x}')
    original = w.http
    def http(method, path, headers, raw):
        if b'00000000000000000000000000000000' in raw:
            raise OSError('SECRET mailbox body')
        return original(method, path, headers, raw)
    w.client.transport = http
    report = recovery(w, max_passes=2).run()
    result = by(report)['revocations']
    assert result['count'] == 1 and len(result['details']['failures']) == 1
    rows = w.rows('mail_pull_fences')
    assert sum(r['state'] == 'acked' for r in rows) == 1
    assert sum(r['next_attempt_ms'] > w.now for r in rows) == 2
    assert 'SECRET' not in capsys.readouterr().out and 'SECRET' not in repr(report)
    second = recovery(w, max_passes=2)
    assert by(second.run())['revocations']['count'] == 2
    assert by(second.run())['revocations']['details']['pending_count'] == 1
    assert len(w.calls) == 3


def test_exponential_retry_is_capped_and_one_attempt_per_due_row_per_pass(w):
    w.fence_row()
    calls = []
    def failed(*args):
        calls.append(w.now)
        raise OSError('never log this')
    w.client.transport = failed
    rec = recovery(w)
    delays = []
    for _ in range(10):
        rec.run()
        next_ms = w.rows('mail_pull_fences')[0]['next_attempt_ms']
        delays.append(next_ms - w.now)
        rec.run()
        w.now = next_ms
    assert len(calls) == 10
    assert delays[:3] == [30000, 60000, 120000]
    assert delays[-1] == BACKOFF_MAX_MS and max(delays) == BACKOFF_MAX_MS


@pytest.mark.parametrize('column,value', [('flow_id', 'e'*32), ('generation', 2),
    ('fence_token', 'e'*64), ('mode', 'cancel'), ('next_attempt_ms', NOW+999999)])
def test_scope_change_during_network_prevents_receipt_cas(w, column, value):
    w.fence_row()
    def after():
        with w.store._tx() as db:
            db.execute('UPDATE mail_pull_fences SET ' + column + '=?', (value,))
    w.after = after
    result = by(recovery(w).run())['revocations']
    assert result['state'] == 'failed'
    assert w.rows('mail_pull_fences')[0]['receipt_id'] is None


def test_network_has_no_open_transaction_and_recovery_is_nonoverlapping(w):
    w.fence_row()
    rec = recovery(w)
    nested = []
    original = w.http
    def http(*args):
        # BEGIN IMMEDIATE succeeds on a different connection during transport.
        with w.store._tx() as db:
            db.execute('UPDATE mail_pull_accounts SET updated_ms=updated_ms')
        nested.append(rec.run())
        return original(*args)
    w.client.transport = http
    assert by(rec.run())['revocations']['count'] == 1
    assert by(nested[0])['store']['reason'] == 'recovery_busy'


def test_independent_recovery_objects_claim_fence_only_once(w):
    w.fence_row()
    second = recovery(w)
    original = w.http
    nested = []
    def http(*args):
        nested.append(second.run())
        return original(*args)
    w.client.transport = http
    assert by(recovery(w).run())['revocations']['count'] == 1
    assert by(nested[0])['revocations']['count'] == 0 and len(w.calls) == 1


def test_ack_counts_ages_and_pending_survive_every_pass_and_expiry(w):
    w.receiver.receive(w.wire())
    before = w.rows('mail_pull_ack_outbox')
    w.now += 12345
    result = by(recovery(w).run())['acks']
    assert result['state'] == 'pending' and result['reason'] == 'ack_pending'
    assert result['details'] == dict(pending_count=1, held_count=0, oldest_age_ms=12345,
        oldest_pending_age_ms=12345, oldest_held_age_ms=0)
    w.now += 90 * 86400000
    recovery(w).run()
    assert w.rows('mail_pull_ack_outbox') == before
    assert w.rows('mail_pull_receipts')[0]['cloud_receipt_id'] is None
    assert not w.calls


@pytest.mark.parametrize('hold', ['outbox', 'disabled', 'generation', 'owner'])
def test_held_acks_are_visible_without_rewriting_or_dropping_them(w, hold):
    w.receiver.receive(w.wire())
    with w.store._tx() as db:
        if hold == 'outbox':
            db.execute("UPDATE mail_pull_ack_outbox SET state='held'")
        else:
            changes = {'disabled': "local_enabled=0", 'generation': 'generation=2',
                       'owner': 'owner_generation=2'}
            db.execute('UPDATE mail_pull_accounts SET ' + changes[hold])
    before = w.rows('mail_pull_ack_outbox')
    w.now += 4000
    result = by(recovery(w).run())['acks']
    assert result['state'] == 'held' and result['details']['held_count'] == 1
    assert result['details']['oldest_held_age_ms'] == 4000
    assert w.rows('mail_pull_ack_outbox') == before


@pytest.mark.parametrize('damage', ['digest', 'local_receipt', 'missing_ack', 'cloud_receipt'])
def test_custody_inconsistency_holds_never_reconstructs_ack_or_claims_cloud_acceptance(w, damage):
    w.receiver.receive(w.wire())
    with w.store._tx() as db:
        sql = {
            'digest': "UPDATE mail_pull_ack_outbox SET event_digest='" + '0'*64 + "'",
            'local_receipt': "UPDATE mail_pull_ack_outbox SET local_receipt_id='" + '0'*32 + "'",
            'missing_ack': 'DELETE FROM mail_pull_ack_outbox',
            'cloud_receipt': "UPDATE mail_pull_receipts SET cloud_receipt_id='" + '0'*32 + "'",
        }
        db.execute(sql[damage])
    before = w.custody()
    result = by(recovery(w).run())['acks']
    assert result['reason'] == 'ack_inconsistent' and result['state'] == 'held'
    assert w.rows('mail_pull_ack_outbox') == before['mail_pull_ack_outbox']
    assert w.rows('mail_pull_receipts') == before['mail_pull_receipts']


@pytest.mark.parametrize('state,reason', [('pending','flow_pending'), ('verified','flow_pending'),
    ('enable_pending','activation_pending'), ('held','flow_held')])
def test_flows_only_report_durable_states_and_never_activate(w, state, reason):
    with w.store._tx() as db:
        db.execute("""INSERT INTO mail_pull_flows VALUES(?,?,'signup',1,1,'key1',?,
            ?,?,1,?,?,'',NULL)""", ('e'*32, INSTALL, 'c'*32, '0'*64, NOW, state, NOW+10000))
    before = w.rows('mail_pull_flows')
    assert by(recovery(w).run())['flows']['reason'] == reason
    assert w.rows('mail_pull_flows') == before and not w.calls


@pytest.mark.parametrize('change,reason', [
    ("UPDATE mail_pull_connections SET scan_complete=1", 'ok'),
    ("UPDATE mail_pull_connections SET reauth_ms=0", 'pull_disconnected'),
    ("UPDATE mail_pull_connections SET epoch=0", 'pull_disconnected'),
    ("UPDATE mail_pull_connections SET owner_generation=2", 'pull_disconnected'),
    ("UPDATE mail_pull_accounts SET local_enabled=0", 'local_disabled'),
    ("UPDATE mail_pull_generations SET state='fenced'", 'local_disabled'),
])
def test_pull_is_durable_evidence_only_never_starts_or_replays(w, change, reason):
    with w.store._tx() as db:
        db.execute(change)
    assert by(recovery(w).run())['pull']['reason'] == reason
    assert not w.calls


def test_independent_callback_failures_and_cleanup_still_run(w, monkeypatch, capsys):
    calls = []
    def fail():
        calls.append('launder')
        raise OSError('SECRET raw text')
    def wake():
        calls.append('wake')
        return {'state': 'idle'}
    def reconcile():
        calls.append('wake_reconcile')
        raise EnrollmentRefused('vault_locked')
    monkeypatch.setattr(w.store, 'reconcile', lambda: (_ for _ in ()).throw(OSError('SECRET')))
    rec = recovery(w, launder=fail, wake=wake, wake_reconcile=reconcile)
    result = by(rec.run())
    assert calls == ['launder','wake','wake_reconcile']
    assert result['inbound']['details']['failures'] == ['step_failed','step_failed','vault_locked']
    assert result['cleanup']['state'] == 'ok' and result['retention']['state'] == 'ok'
    out = capsys.readouterr().out
    for name in ('local_reconcile','launder','wake_reconcile'):
        assert name + ' failed:' in out
    assert 'SECRET' not in out and 'SECRET' not in repr(result)


def test_worker_reported_failure_is_observable_and_bounded(w, capsys):
    calls = []
    rec = recovery(w, launder=lambda: calls.append(1) or {'state':'held','reason':'SECRET'})
    result = by(rec.run())['inbound']
    assert result['state'] == 'failed' and len(calls) == 1
    assert 'launder failed:' in capsys.readouterr().out and 'SECRET' not in repr(result)


def test_removed_recovery_apis_cannot_reactivate_lease_or_push_paths():
    for key in ('lifecycle','replay','replay_interval_s'):
        with pytest.raises(TypeError):
            Recovery(object(), **{key: object()})
    source = inspect.getsource(module)
    assert '._replay(' not in source and '._committed(' not in source
    assert '.activate(' not in source and '.start(' not in source


def test_result_order_and_closed_reasons_and_counts(w):
    result = recovery(w).run()
    assert tuple(s['name'] for s in result['steps']) == STEPS
    assert {s['reason'] for s in result['steps']} <= module.REASONS
    assert all(type(s['count']) is int and s['count'] >= 0 for s in result['steps'])


def test_ack_accounting_uses_one_snapshot_across_live_completion(w, monkeypatch):
    w.receiver.receive(w.wire())
    original = w.store._read
    class Read:
        def __init__(self, db):
            self.db = db
        def execute(self, sql, *args):
            result = self.db.execute(sql, *args)
            if 'UNION ALL SELECT 1' in sql:
                # Simulate the client's atomic signed-completion write between
                # recovery's consistency query and its age/count query.
                result.fetchall()
                with w.store._tx() as other:
                    other.execute("UPDATE mail_pull_receipts SET cloud_receipt_id=?,cloud_committed_ms=?",
                                  ('f'*32, w.now))
                    other.execute('DELETE FROM mail_pull_ack_outbox')
                return self.db.execute(sql, *args)
            return result
    @contextmanager
    def snapshot():
        with original() as db:
            yield Read(db)
    monkeypatch.setattr(w.store, '_read', snapshot)
    result = recovery(w)._acks(w.store)
    assert result.reason == 'ack_pending' and result.count == 1
    assert w.rows('mail_pull_ack_outbox') == []


def test_receipt_commit_failure_retains_durable_retry_and_later_steps_run(w, monkeypatch):
    w.fence_row()
    original = w.store._tx
    count = 0
    @contextmanager
    def fail_commit():
        nonlocal count
        count += 1
        with original() as db:
            yield db
            if count == 2:
                raise OSError('SECRET commit failure')
    monkeypatch.setattr(w.store, '_tx', fail_commit)
    result = by(recovery(w).run())
    assert result['revocations']['details']['failures'] == ['step_failed']
    assert result['cleanup']['state'] == 'ok'
    row = w.rows('mail_pull_fences')[0]
    assert row['state'] == 'pending' and row['receipt_id'] is None and row['next_attempt_ms'] > w.now
