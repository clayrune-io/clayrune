"""Unit25 adapter only: temporary stores, synthetic custody, no network/mail."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from typing import Any

import pytest

from mc import agent_mail_status_view as view
from mc.agent_mail_recovery import Recovery
from test_agent_mail_factory_pull import factory, populated
from test_agent_mail_pull_receiver import Harness, INSTALL, NOW, FLOW
from test_agent_mail_signup import CANONICAL_OWNER


class Source:
    factory: Any

    def __init__(self, value):
        self.value = value
        self.calls = 0

    def status(self):
        self.calls += 1
        if isinstance(self.value, Exception):
            raise self.value
        return deepcopy(self.value)


class Seal:
    def __init__(self):
        self.value = 'ready'
        self.refs = []

    def status(self, ref):
        self.refs.append(ref)
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


def live(*, factory=None, **updates):
    result = dict(state='ok', enabled=True, thread_alive=True, tick_stale=False,
                  client=dict(state='connected', running=True, epoch=1, reason='',
                              last_activity_ms=NOW, failures=0, next_attempt_ms=0),
                  last_tick=NOW / 1000, steps=[], holds={})
    result.update(updates)
    source = Source(result)
    source.factory = factory
    return source


@pytest.fixture
def kit(tmp_path):
    h: Any = Harness(tmp_path)
    h.seal = Seal()
    h.factory = SimpleNamespace(require_pull_store=lambda: h.store, seal=lambda: h.seal,
                                clock=lambda: h.now / 1000)
    with h.store._tx() as db:
        db.execute('UPDATE mail_pull_connections SET scan_complete=1')
    return h


def cached(h, **updates):
    value = dict(v=1, code='ok', request_id='a' * 32, install_id=INSTALL, generation=1,
                 owner_generation=1, state='active', address='public@example.test',
                 owner='owner@example.test', key_id='key1', queue_count=3, queue_bytes=512,
                 invalid_message_id_holds=1, oldest_received_ms=NOW - 5000,
                 oldest_expires_ms=NOW + 8000, current_epoch=1, owner_change=None,
                 last_notice_state='uncertain', reason='')
    value.update(updates)
    text = json.dumps(value, separators=(',', ':'))
    with h.store._tx() as db:
        db.execute('INSERT OR REPLACE INTO mail_pull_status VALUES(?,?,1,1,?,?)',
                   (INSTALL, NOW - 2000, text, hashlib.sha256(text.encode()).hexdigest()))


def test_absent_sources_are_unknown_not_zero_or_ready():
    result = view.pull_status(None)
    assert result['state'] == 'unknown' and result['connection'] == 'unknown'
    assert result['queue_count'] is result['queue_bytes'] is result['revocation_pending'] is None
    assert result['owner_verified'] is None and result['vault'] == {'state': 'unknown'}
    assert result['signup'] == {'state': 'unavailable'}
    assert result['recovery'] == {'state': 'not_running'}
    assert result['enabled'] is None


def test_real_factory_missing_and_empty_store_do_not_bootstrap(tmp_path):
    f = factory(tmp_path)
    assert view.pull_status(f)['state'] == 'not_enrolled'
    assert not f.paths.root.exists()
    f.bootstrap(authorize=lambda: True)
    result = view.pull_status(f)
    assert result['state'] == 'not_enrolled' and result['owner_verified'] is False
    assert result['queue_count'] is None and not f.paths.identity.exists()


@pytest.mark.parametrize('damage,reason', [('missing', 'store_missing'), ('schema', 'schema_corrupt')])
def test_real_held_store_is_never_repaired(tmp_path, damage, reason):
    f = factory(tmp_path)
    store = f.bootstrap(authorize=lambda: True)
    if damage == 'missing':
        f.paths.store.unlink()
    else:
        with store._tx() as db:
            db.execute('DROP INDEX mail_pull_ack_due')
    result = view.pull_status(f)
    assert result['state'] == 'held' and result['reason'] == reason
    assert result['connection'] != 'connected' and result['queue_count'] is None


def test_current_verified_account_and_cached_queue_are_separate_from_live_connection(kit):
    cached(kit)
    result = view.pull_status(kit.factory, supervisor=live(factory=kit.factory))
    assert result['state'] == 'active' and result['enabled'] is True
    assert result['owner_verified'] is True and result['owner'] == 'owner@example.test'
    assert result['address'] == 'public@example.test' and result['connection'] == 'connected'
    assert (result['queue_count'], result['queue_bytes'], result['invalid_message_id_holds']) == (3, 512, 1)
    assert result['queue_age_ms'] == 5000 and result['oldest_expires_ms'] == NOW + 8000
    assert result['observed_ms'] == NOW - 2000 and result['notice_state'] == 'uncertain'
    assert result['vault'] == {'state': 'ready'} and result['revocation_pending'] is False
    assert result['local_queue'] == {'count': 0, 'bytes': 0, 'oldest_age_ms': None, 'oldest_expires_ms': None}
    assert not ({'prerequisites', 'tunnel', 'machine_custody', 'proof', 'ownership_expires_ms'} & result.keys())
    ref = kit.seal.refs[-1]
    assert (ref.install, ref.flow, ref.generation, ref.key_id) == (INSTALL, FLOW, 1, 'key1')


@pytest.mark.parametrize('source', [None, Source(None), Source(RuntimeError('SECRET/path'))])
def test_durable_connection_alone_is_never_live(kit, source):
    result = view.pull_status(kit.factory, supervisor=source)
    assert result['connection'] == 'unknown'
    assert result['queue_count'] is None and result['local_queue']['count'] == 0


@pytest.mark.parametrize('updates', [dict(thread_alive=False), dict(enabled=False), dict(tick_stale=True),
    dict(tick_stale=None), dict(client=dict(state='connected', running=False, epoch=1)),
    dict(client=dict(state='connected', running=True, epoch=2))])
def test_connectivity_requires_current_live_worker_evidence(kit, updates):
    assert view.pull_status(kit.factory, supervisor=live(factory=kit.factory, **updates))['connection'] == 'offline'


@pytest.mark.parametrize('sql', ['reauth_ms=' + str(NOW), 'scan_complete=0', 'generation=2',
                               'owner_generation=2', "reason='connection_lost'"])
def test_expired_wrong_scope_or_incomplete_scan_cannot_be_connected(kit, sql):
    with kit.store._tx() as db:
        db.execute('UPDATE mail_pull_connections SET ' + sql)
    assert view.pull_status(kit.factory, supervisor=live(factory=kit.factory))['connection'] == 'offline'


@pytest.mark.parametrize('vault,expected', [('vault_locked_or_unconfigured', 'vault_locked'),
    ('seal_missing', 'held'), ('identity_unavailable_reenrollment_required', 'held'),
    (RuntimeError('SECRET mailbox/password'), 'held'), ('invented_ready', 'held')])
def test_vault_holds_never_enable_and_do_not_leak(kit, vault, expected, capsys):
    kit.seal.value = vault
    result = view.pull_status(kit.factory, supervisor=live(factory=kit.factory))
    assert result['connection'] == expected and result['state'] == 'held' and not result['enabled']
    assert 'SECRET' not in json.dumps(result) + capsys.readouterr().out


@pytest.mark.parametrize('phase', ['pending', 'verified', 'disabled', 'frozen', 'deleted'])
def test_owner_verification_is_not_inferred_from_address_or_socket(kit, phase):
    with kit.store._tx() as db:
        db.execute('UPDATE mail_pull_accounts SET state=?,local_enabled=0', (phase,))
    result = view.pull_status(kit.factory, supervisor=live(factory=kit.factory))
    assert not result['enabled'] and result['connection'] == 'offline'
    assert result['owner'] == ('owner@example.test' if phase == 'verified' else '')
    if phase == 'deleted':
        assert result['address'] == ''


def test_read_only_expiry_and_activation_pending_do_not_retry_or_write(kit):
    with kit.store._tx() as db:
        db.execute("UPDATE mail_pull_accounts SET state='verified',local_enabled=0")
        db.execute("""INSERT INTO mail_pull_flows VALUES(?,?,'signup',1,1,'key1',?,
            ?,?,1,'enable_pending',?,'',NULL)""", (FLOW, INSTALL, FLOW, 'd' * 64, NOW - 1000, NOW + 1000))
    before = kit.rows('mail_pull_flows')
    result = view.pull_status(kit.factory)
    assert result['state'] == 'enable_pending' and result['reason'] == 'activation_pending'
    kit.now += 1000
    result = view.pull_status(kit.factory)
    assert result['flow']['state'] == 'expired' and result['flow']['reason'] == 'flow_expired'
    assert kit.rows('mail_pull_flows') == before


def test_actual_signup_status_is_passive_and_redacts_flow_and_secrets(tmp_path, monkeypatch):
    f, h, install = populated(tmp_path)
    signup = f.signup(authorize=lambda: False)
    def forbidden(*a, **kw):
        pytest.fail('status must not perform transport or activation')
    monkeypatch.setattr(signup, 'transport', forbidden)
    before = h.row()
    result = view.pull_status(f, signup=signup)
    assert result['install_id'] == install and result['signup']['state'] == 'pending'
    assert result['owner'] == '' and result['owner_verified'] is False
    assert before == h.row()
    blob = json.dumps(result)
    assert before['flow_id'] not in blob and before['input_digest'] not in blob
    assert 'fence_token' not in blob and 'start_body' not in blob


def test_verified_owner_comes_from_actual_signed_signup_transition(tmp_path):
    f, h, _ = populated(tmp_path)
    h.remote_state = 'verified'
    signup = f.signup(authorize=lambda: False)
    assert signup.resume()['state'] == 'verified'
    before = list(h.calls)
    result = view.pull_status(f, signup=signup)
    assert result['owner_verified'] is True and result['owner'] == CANONICAL_OWNER
    assert result['state'] == 'verified' and not result['enabled']
    assert result['queue_count'] is None  # Signed signup status is not queue evidence.
    assert h.calls == before


def test_local_custody_queue_and_recovery_ack_ages_are_explicit(kit):
    kit.receiver.receive(kit.wire())
    kit.now += 4000
    recovery = Recovery(kit.factory, clock=lambda: kit.now / 1000, retire=lambda: {})
    # Other recovery components are absent from this fake; only real passive
    # ack accounting is used, so no reconciliation or transport side effects.
    step = recovery._acks(kit.store).as_dict()
    source = live(factory=kit.factory, steps=[step])
    result = view.pull_status(kit.factory, supervisor=source)
    assert result['queue_count'] is None  # Local custody is not the service queue.
    assert result['local_queue']['count'] == 1 and result['local_queue']['oldest_age_ms'] == 4000
    assert result['recovery']['steps'][0]['details'] == dict(
        pending_count=1, held_count=0, oldest_age_ms=4000,
        oldest_pending_age_ms=4000, oldest_held_age_ms=0)
    with kit.store._tx() as db:
        db.execute("UPDATE mail_pull_ack_outbox SET state='held'")
    step = recovery._acks(kit.store).as_dict()
    assert view.recovery_snapshot(live(steps=[step]))['steps'][0]['details']['held_count'] == 1


@pytest.mark.parametrize('mode,generation,held', [('revoke', 1, True), ('cancel', 1, True), ('revoke', 2, False)])
def test_pending_fences_include_old_generations_but_only_current_fence_holds(kit, mode, generation, held):
    with kit.store._tx() as db:
        db.execute("INSERT INTO mail_pull_fences VALUES(?,?,?,?,?,?,'pending',NULL,?,?)",
                   ('e' * 32, INSTALL, generation, FLOW, 'f' * 64, mode, NOW, NOW))
    result = view.pull_status(kit.factory, supervisor=live(factory=kit.factory))
    assert result['revocation_pending'] is True and result['revocations_pending'] == 1
    assert (result['state'] == 'held') == held
    assert 'fence_token' not in json.dumps(result) and 'f' * 64 not in json.dumps(result)


@pytest.mark.parametrize('updates', [dict(generation=2), dict(owner_generation=2),
    dict(owner='other@example.test'), dict(address='other@example.test'), dict(queue_count=-1),
    dict(queue_count=True), dict(queue_bytes=2097153), dict(code='not_ok')])
def test_stale_wrong_scope_or_malformed_remote_cache_is_unavailable(kit, updates):
    cached(kit, **updates)
    result = view.pull_status(kit.factory)
    assert result['queue_count'] is result['queue_bytes'] is result['observed_ms'] is None


@pytest.mark.parametrize('change', ["generation=2", "observed_ms=" + str(NOW + 1),
    "response_digest='" + '0' * 64 + "'", "status_json='not JSON' AND response_digest=response_digest"])
def test_cache_record_integrity_and_future_observation_are_not_trusted(kit, change):
    cached(kit)
    with kit.store._tx() as db:
        db.execute('UPDATE mail_pull_status SET ' + change)
    assert view.pull_status(kit.factory)['queue_count'] is None


@pytest.mark.parametrize('bad', [None, [], 'SECRET', {'steps': None, 'holds': [], 'state': []},
    {'steps': [None, {'name': 'acks', 'state': [], 'reason': [], 'count': True}], 'holds': 'secret'}])
def test_malformed_supervisor_shapes_never_raise_or_leak(bad):
    result = view.recovery_snapshot(Source(bad))
    assert 'SECRET' not in json.dumps(result, allow_nan=False)


def test_supervisor_metadata_is_bounded_closed_and_preserves_failure_times():
    source = live(last_attempt=float('nan'), last_success=float('inf'), last_tick=True,
                  holds={'vault_locked': 2, 'secret_shaped_like_a_code': 10},
                  last_failure=dict(source='client', reason='worker_failed', at=12.5, secret='NO'),
                  failure_count=4,
                  recent_failures=[dict(source='thread', reason='worker_stalled', at=n) for n in range(40)],
                  last_client_event=dict(state='held', running=False, reason='vault_locked', observed_at=15,
                                         flow_id='NO', ticket='NO'),
                  steps=[dict(name='acks', state='held', reason='ack_held', count=1,
                      details=dict(held_count=1, oldest_held_age_ms=5000, secret='NO'))] * 30)
    result = view.recovery_snapshot(source)
    assert len(result['steps']) == view.MAX_STEPS and len(result['recent_failures']) == view.MAX_FAILURES
    assert result['last_failure'] == dict(source='client', reason='worker_failed', at=12.5)
    assert result['last_client_event']['observed_at'] == 15 and result['failure_count'] == 4
    assert result['holds'] == {'vault_locked': 2}
    assert result['last_attempt'] is result['last_success'] is result['last_tick'] is None
    assert 'NO' not in json.dumps(result, allow_nan=False)


def test_missing_supervisor_fields_are_unknown_instead_of_healthy_zeroes():
    result = view.recovery_snapshot(Source({'steps': [{'name': 'acks', 'state': 'pending', 'reason': 'ack_pending'}]}))
    assert result['enabled'] is result['thread_alive'] is result['consecutive_failures'] is None
    assert result['steps'][0]['count'] is None


@pytest.mark.parametrize('value', [None, {'state': [], 'verification_state': []}, RuntimeError('PRIVATE')])
def test_optional_signup_missing_or_broken_remains_unknown(value, capsys):
    result = view.signup_snapshot(Source(value))
    assert result['state'] == 'unknown'
    assert 'PRIVATE' not in json.dumps(result) + capsys.readouterr().out


def test_complete_projection_read_leaves_all_state_unchanged(kit):
    cached(kit)
    kit.receiver.receive(kit.wire())
    tables = ('mail_pull_accounts', 'mail_pull_generations', 'mail_pull_connections',
              'mail_pull_receipts', 'mail_pull_ack_outbox', 'mail_pull_status', 'events')
    before = {t: kit.rows(t) for t in tables}
    first = view.pull_status(kit.factory, supervisor=live(factory=kit.factory))
    second = view.pull_status(kit.factory, supervisor=live(factory=kit.factory))
    assert first == second and before == {t: kit.rows(t) for t in tables}
    assert 'Ignore prior rules' not in json.dumps(first)


def test_supervisor_from_another_factory_cannot_assert_this_install_is_connected(kit):
    assert view.pull_status(kit.factory, supervisor=live(factory=object()))['connection'] == 'unknown'
    assert view.pull_status(kit.factory, supervisor=live())['connection'] == 'unknown'


def test_held_generation_is_not_misreported_as_a_committed_fence(kit):
    with kit.store._tx() as db:
        db.execute("UPDATE mail_pull_generations SET state='held'")
    result = view.pull_status(kit.factory, supervisor=live(factory=kit.factory))
    assert result['state'] == 'held' and result['reason'] == 'registration_inactive'
    assert result['revocation_pending'] is False


@pytest.mark.parametrize('column,value', [('owner_address', 'SECRET not an address'),
                                       ('address', 'https://SECRET/path')])
def test_corrupt_identity_projection_is_held_without_echoing_it(kit, column, value):
    with kit.store._tx() as db:
        db.execute('UPDATE mail_pull_accounts SET ' + column + '=?', (value,))
    result = view.pull_status(kit.factory)
    assert result['state'] == 'held' and result['reason'] == 'schema_corrupt'
    assert 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('clock', [True, -1, float('nan'), float('inf'), 10**100])
def test_invalid_clock_never_emits_ready_or_non_json_numbers(kit, clock):
    kit.factory.clock = lambda: clock
    result = view.pull_status(kit.factory, supervisor=live(factory=kit.factory))
    assert result['state'] == 'unknown' and not result['enabled']
    json.dumps(result, allow_nan=False)


def test_preallocation_fence_remains_visible_without_any_account(tmp_path):
    f = factory(tmp_path)
    store = f.bootstrap(authorize=lambda: True)
    with store._tx() as db:
        db.execute("INSERT INTO mail_pull_fences VALUES(?,?,1,?,?,'cancel','pending',NULL,?,?)",
                   ('e' * 32, INSTALL, FLOW, 'f' * 64, NOW, NOW))
    result = view.pull_status(f)
    assert result['state'] == 'not_enrolled' and result['revocation_pending'] is True
    assert result['revocations_pending'] == 1 and not f.paths.identity.exists()


def test_pending_flow_cannot_be_reported_enabled_even_if_account_bits_disagree(kit):
    with kit.store._tx() as db:
        db.execute("""INSERT INTO mail_pull_flows VALUES(?,?,'signup',1,1,'key1',?,
            ?,?,1,'enable_pending',?,'',NULL)""", (FLOW, INSTALL, FLOW, 'd' * 64, NOW, NOW + 1000))
    result = view.pull_status(kit.factory, supervisor=live(factory=kit.factory))
    assert result['state'] == 'enable_pending' and not result['enabled']
    assert result['connection'] == 'offline'
    assert result['reason'] == 'activation_pending'


def test_genuine_empty_remote_queue_reports_zero_with_observation_time(kit):
    cached(kit, queue_count=0, queue_bytes=0, invalid_message_id_holds=0,
           oldest_received_ms=None, oldest_expires_ms=None)
    result = view.pull_status(kit.factory)
    assert result['queue_count'] == result['queue_bytes'] == result['invalid_message_id_holds'] == 0
    assert result['observed_ms'] == NOW - 2000 and result['queue_age_ms'] is None


@pytest.mark.parametrize('raw', ['not JSON SECRET', '[]', '{"queue_count": 1}'])
def test_malformed_cache_with_matching_digest_still_cannot_supply_status(kit, raw, capsys):
    with kit.store._tx() as db:
        db.execute('INSERT INTO mail_pull_status VALUES(?,?,1,1,?,?)',
                   (INSTALL, NOW, raw, hashlib.sha256(raw.encode()).hexdigest()))
    result = view.pull_status(kit.factory)
    assert result['queue_count'] is None
    assert 'SECRET' not in json.dumps(result) + capsys.readouterr().out
