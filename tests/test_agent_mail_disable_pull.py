"""W02 local denial: private SQLite, synthetic signed responses, no network/mail."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from mc.agent_mail_disable import Disable
from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_factory import MailFactory, GuardedMailStore
from mc.agent_mail_pull_client import PullClient
from mc.agent_mail_pull_schema import PullSchemaCorrupt
from mc.agent_mail_recovery import BACKOFF_START_MS
from mc.agent_mail_signing import IngressRefused
from mc.agent_mail_store import MailStoreError
from test_agent_mail_service_client_pull import World
from test_agent_mail_pull_receiver import INSTALL, FLOW, MANIFEST, compact
from test_agent_mail_pull_late_ack import snapshot, proof
from test_agent_mail_factory_pull import populated
from test_agent_mail_outbound import out, kit, setup, env, prepare  # noqa: F401

OP = '7' * 32
TOKEN = 'd' * 64


def add_flow(w, *, flow=FLOW, purpose='signup', state='completed', token=TOKEN):
    meta = dict(v=1, op_id='8' * 32, public_key=w.public, start_body=None,
                started=1, notice_state='accepted', fence_token=token)
    with w.store._tx() as db:
        db.execute('INSERT INTO mail_pull_flows VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (flow, INSTALL, purpose, 1, 1, 'key1', flow, '0' * 64, w.now, 1,
             state, w.now + 86400000, '', json.dumps(meta)))


def compose(w, *, authorize=lambda: True, stop=None, max_passes=5):
    factory = SimpleNamespace(require_pull_store=lambda: w.store,
        service_client=lambda: w.client, clock=lambda: w.now / 1000, seal=lambda: w.seal)
    if stop is None:
        def stop():
            assert w.rows('mail_pull_accounts')[0]['local_enabled'] == 0
            assert w.rows('mail_pull_generations')[0]['state'] == 'fenced'
            return True
    return Disable(factory, install_id=INSTALL, authorize=authorize, stop=stop, max_passes=max_passes)


@pytest.fixture
def w(tmp_path):
    w = World(tmp_path)
    add_flow(w)
    return w


def assert_off(w):
    a = w.rows('mail_pull_accounts')[0]
    assert a['local_enabled'] == 0 and a['consent_epoch'] == 2
    assert w.rows('registrations')[0]['enabled'] == 0
    assert w.rows('registrations')[0]['generation'] == 2
    assert w.rows('mail_pull_threads')[0]['state'] == 'revoked'
    assert w.rows('thread_bindings')[0]['revoked_at'] == w.now / 1000
    c = w.rows('mail_pull_connections')[0]
    assert (c['epoch'], c['connection_id']) == (1, '1' * 32)
    assert c['scan_complete'] == 0 and c['reason'] == 'local_disabled'


def test_denial_commits_before_stop_and_remote_signed_receipt_only_settles_fence(w):
    original = w.client.transport
    def http(*args):
        assert_off(w)
        assert w.rows('mail_pull_fences')[0]['state'] == 'pending'
        op = w.rows('mail_pull_disable_ops')[0]
        assert (op['op_id'], op['install_id'], op['generation'], op['owner_generation'],
                op['flow_id'], op['input_digest'], op['state'], op['reason'], op['created_ms']) == (
            OP, INSTALL, 1, 1, FLOW, hashlib.sha256(compact({'op_id': OP})).hexdigest(),
            'local_off', 'local_disabled', w.now)
        assert json.loads(op['result_json']) == dict(enabled=False, revocation_pending=True,
                                                    reason='local_disabled')
        # An independent writer can enter: no SQLite transaction spans I/O.
        with w.store._tx() as db:
            db.execute('SELECT 1')
        return original(*args)
    w.client.transport = http
    result = compose(w).disable(OP)
    assert_off(w)
    assert result['enabled'] is False and result['revocation_pending'] is False
    row = w.rows('mail_pull_fences')[0]
    assert (row['op_id'], row['mode'], row['state'], row['receipt_id']) == (OP, 'revoke', 'acked', 'f' * 32)
    assert w.calls[0][2]['fence_token'] == TOKEN
    assert TOKEN not in repr(result) and 'fence_token' not in repr(result)
    assert op_record(w)['state'] == 'local_off'  # remote settlement never rewrites local result


@pytest.mark.parametrize('vault', ['locked', 'lost', 'throws'])
def test_denial_never_uses_vault_key_or_creates_replacement(w, vault):
    if vault == 'locked':
        w.master = None
    elif vault == 'lost':
        w.master = b'z' * 32
    else:
        def unavailable():
            raise OSError('private vault detail')
        w.seal._key = unavailable
    before = {p.name: p.read_bytes() for p in w.seal.folder.glob('*')}
    result = compose(w).disable(OP)
    assert_off(w)
    assert w.rows('mail_pull_fences')[0]['state'] == 'acked'
    assert before == {p.name: p.read_bytes() for p in w.seal.folder.glob('*')}
    assert 'private vault detail' not in repr(result)


@pytest.mark.parametrize('key', [None, b'Z' * 32])
def test_real_factory_with_locked_or_lost_vault_still_persists_denial(tmp_path, key):
    f, h, install = populated(tmp_path)
    f.key = lambda: key
    def offline(*args):
        raise OSError('synthetic offline')
    f._pull_transport = offline
    before = {p.name: p.read_bytes() for p in f.paths.identity.glob('*')}
    d = Disable(f, install_id=install, authorize=lambda: True, stop=lambda: True)
    result = d.disable(OP)
    with h.store._read() as db:
        assert db.execute('SELECT local_enabled FROM mail_pull_accounts').fetchone()[0] == 0
        fence = db.execute('SELECT * FROM mail_pull_fences').fetchone()
        assert fence['op_id'] == OP and fence['mode'] == 'cancel' and fence['state'] == 'pending'
    assert result['enabled'] is False and result['revocation_pending'] is True
    assert before == {p.name: p.read_bytes() for p in f.paths.identity.glob('*')}


@pytest.mark.parametrize('failure', ['offline', 'unsigned', 'scope', 'signature', 'trust'])
def test_failed_remote_stays_off_and_durable_backoff_is_not_reset_by_duplicate(w, failure, capsys):
    if failure == 'offline':
        def offline(*args):
            raise OSError('PRIVATE token=' + TOKEN)
        w.client.transport = offline
    elif failure == 'trust':
        def no_review():
            raise EnrollmentRefused('service_key_changed')
        w.review = no_review
    else:
        def alter(status, headers, raw):
            if failure == 'unsigned':
                return 401, {}, b'PRIVATE'
            return status, dict(headers, **{'x-mail-' + ('generation' if failure == 'scope' else 'signature'):
                                           '2' if failure == 'scope' else 'bad'}), raw
        w.change = alter
    d = compose(w)
    result = d.disable(OP)
    assert_off(w)
    row = w.rows('mail_pull_fences')[0]
    assert row['state'] == 'pending' and row['receipt_id'] is None and row['next_attempt_ms'] > w.now
    assert result['enabled'] is False and result['revocation_pending'] is True
    before = snapshot(w)
    d.disable(OP)
    assert snapshot(w) == before
    assert len(w.calls) <= 1
    text = capsys.readouterr().out
    assert 'revocations failed:' in text and TOKEN not in text and 'PRIVATE' not in text


def test_restart_retries_original_exact_capability_and_never_resurrects(w):
    w.change = lambda s, h, b: (401, {}, b'')
    compose(w).disable(OP)
    before = snapshot(w)
    due = w.rows('mail_pull_fences')[0]['next_attempt_ms']
    w.now = due
    w.change = lambda *args: args
    result = compose(w).retry_revocations()
    after = snapshot(w)
    expected = dict(before)
    expected['mail_pull_fences'] = [dict(before['mail_pull_fences'][0], state='acked', receipt_id='f' * 32,
                                       next_attempt_ms=due + BACKOFF_START_MS)]
    assert after == expected
    assert result['enabled'] is False and result['revocation_pending'] is False
    assert len(w.calls) == 2
    assert {k: w.calls[0][2][k] for k in ('op_id', 'install_id', 'generation', 'flow_id', 'fence_token', 'mode')} == {
        k: w.calls[1][2][k] for k in ('op_id', 'install_id', 'generation', 'flow_id', 'fence_token', 'mode')}


@pytest.mark.parametrize('state', ['pending', 'verified', 'active'])
def test_pending_signup_cancel_and_allocated_generation_revoke(w, state):
    with w.store._tx() as db:
        db.execute('UPDATE mail_pull_accounts SET state=?,local_enabled=?', (state, int(state == 'active')))
        db.execute("UPDATE mail_pull_flows SET state='pending'")
    compose(w).disable(OP)
    assert w.rows('mail_pull_fences')[0]['mode'] == ('cancel' if state == 'pending' else 'revoke')
    assert w.rows('mail_pull_flows')[0]['state'] == 'cancelled'
    assert_off(w)


def test_management_flow_cancel_is_independent_of_generation_revoke(w):
    add_flow(w, flow='2' * 32, purpose='owner_change', state='verified', token='e' * 64)
    compose(w).disable(OP)
    rows = w.rows('mail_pull_fences')
    assert len(rows) == 2 and {r['mode'] for r in rows} == {'cancel', 'revoke'}
    assert all(r['state'] == 'acked' for r in rows)
    assert all(r['state'] in ('cancelled', 'completed') for r in w.rows('mail_pull_flows'))


@pytest.mark.parametrize('damage', ['foreign', 'generation', 'flow'])
def test_reused_op_cannot_disable_another_scope(w, damage):
    w.fence_row(op=OP)
    with w.store._tx() as db:
        field, value = {'foreign': ('install_id', '0' * 32), 'generation': ('generation', 2),
                        'flow': ('flow_id', '0' * 32)}[damage]
        db.execute(f'UPDATE mail_pull_fences SET {field}=?', (value,))
    before = snapshot(w)
    with pytest.raises(EnrollmentRefused, match='operation_conflict'):
        compose(w).disable(OP)
    assert snapshot(w) == before and not w.calls


def test_other_op_on_same_scope_cannot_change_remote_operation_identity(w):
    compose(w).disable(OP)
    before = snapshot(w)
    with pytest.raises(EnrollmentRefused, match='operation_conflict'):
        compose(w).disable('4' * 32)
    assert snapshot(w) == before


@pytest.mark.parametrize('allowed', [False, None, 1])
def test_human_gate_precedes_store_access(w, allowed):
    d = compose(w, authorize=lambda: allowed)
    d.factory.require_pull_store = lambda: pytest.fail('gate must precede store')
    with pytest.raises(EnrollmentRefused, match='human_required'):
        d.disable(OP)
    with pytest.raises(EnrollmentRefused, match='human_required'):
        d.retry_revocations()


@pytest.mark.parametrize('op', ['', None, True, 'x' * 32, 'a' * 31])
def test_invalid_operation_does_not_touch_state(w, op):
    before = snapshot(w)
    with pytest.raises(EnrollmentRefused, match='invalid_request'):
        compose(w).disable(op)
    assert snapshot(w) == before


def test_real_factory_never_bootstraps_missing_store(tmp_path):
    root = tmp_path / 'never-created'
    f = MailFactory(root=root, key=lambda: None, config={})
    d = Disable(f, install_id=INSTALL, authorize=lambda: True, stop=lambda: pytest.fail('no store'))
    for action in (lambda: d.disable(OP), d.retry_revocations):
        with pytest.raises(MailStoreError):
            action()
        assert not root.exists()


def test_lost_existing_guarded_store_is_not_recreated(w):
    w.store = GuardedMailStore(w.store.path, clock=lambda: w.now / 1000)
    w.store.path.unlink()
    with pytest.raises(MailStoreError):
        compose(w).disable(OP)
    assert not w.store.path.exists()


def test_partial_pull_schema_refuses_without_repair(w):
    with w.store._tx() as db:
        db.execute('DROP INDEX mail_pull_ack_due')
    before = snapshot(w)
    with pytest.raises(PullSchemaCorrupt):
        compose(w).disable(OP)
    assert snapshot(w) == before


@pytest.mark.parametrize('damage', ['no_flow', 'no_token', 'bad_token', 'duplicate_json'])
def test_missing_usable_capability_is_durable_local_hold_not_remote_success(w, damage):
    with w.store._tx() as db:
        if damage == 'no_flow':
            db.execute('DELETE FROM mail_pull_flows')
        else:
            text = {'no_token': '{}', 'bad_token': '{"fence_token":"bad"}',
                    'duplicate_json': '{"fence_token":"' + TOKEN + '","fence_token":"' + TOKEN + '"}'}[damage]
            db.execute('UPDATE mail_pull_flows SET result_json=?', (text,))
    result = compose(w).disable(OP)
    assert_off(w)
    assert w.rows('mail_pull_accounts')[0]['reason'] == 'fence_token_missing'
    assert result['reason'] == 'fence_token_missing' and result['revocation_pending'] is True
    assert w.rows('mail_pull_fences') == [] and w.calls == []
    record = op_record(w)
    assert record['state'] == 'held' and record['flow_id'] == FLOW
    assert json.loads(record['result_json']) == dict(enabled=False, revocation_pending=True,
                                                    reason='fence_token_missing')
    assert TOKEN not in repr(record) and 'fence_token"' not in record['result_json']
    before = snapshot(w)
    w.store = GuardedMailStore(w.store.path, clock=lambda: w.now / 1000)
    compose(w).disable(OP)
    assert snapshot(w) == before and w.calls == []


def test_submitted_turn_and_positive_local_receipts_are_retained(w):
    ack = w.receiver.receive(w.wire())
    with w.store._tx() as db:
        db.execute("UPDATE events SET state='submitted',submit_evidence='positive',turn_id='turn1'")
    receipts = w.rows('mail_pull_receipts')
    compose(w).disable(OP)
    row = w.rows('events')[0]
    assert (row['state'], row['submit_evidence'], row['turn_id']) == ('submitted', 'positive', 'turn1')
    assert row['body'] is None and row['laundered'] is None
    assert w.rows('mail_pull_receipts') == [dict(receipts[0], disposition='revoked')]
    assert w.rows('mail_pull_ack_outbox')[0]['local_receipt_id'] == ack['request_id']


def test_atomic_rollback_never_stops_or_sends_before_denial_commit(w):
    original = w.store._tx
    @contextmanager
    def fail_commit():
        with original() as db:
            yield db
            raise RuntimeError('simulated commit failure')
    before = snapshot(w)
    w.store._tx = fail_commit
    d = compose(w, stop=lambda: pytest.fail('uncommitted denial'))
    with pytest.raises(RuntimeError, match='commit failure'):
        d.disable(OP)
    assert snapshot(w) == before and not w.calls


@pytest.mark.parametrize('stop', [lambda: False, lambda: (_ for _ in ()).throw(OSError('PRIVATE'))])
def test_stop_failure_is_observable_and_cannot_undo_denial(w, stop, capsys):
    compose(w, stop=stop).disable(OP)
    assert_off(w)
    text = capsys.readouterr().out
    assert 'disable socket_stop_' in text and 'PRIVATE' not in text


def test_concurrent_duplicate_disable_and_retry_claim_one_exact_remote_operation(w):
    ds = [compose(w) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda d: d.disable(OP), ds))
    assert all(r['enabled'] is False for r in results)
    assert_off(w)
    assert len(w.rows('mail_pull_fences')) == 1 and len(w.calls) == 1


@pytest.mark.parametrize('ack_first', [False, True])
def test_signed_ack_and_disable_both_orderings_preserve_positive_custody(w, ack_first):
    ack = w.receiver.receive(w.wire())
    client = PullClient(w.store, w.seal, lambda *args: pytest.fail('no transport'),
                        install_id=INSTALL, reviewed_service=lambda: (w.pin, MANIFEST))
    with w.store._read() as db:
        client._row = client._account(db)
    client._epoch, client._connection = 1, '1' * 32
    payload = {k: v for k, v in ack['payload'].items() if k != 'lease_id'}
    payload.update(request_id=ack['request_id'], receipt_id='9' * 32, committed_ms=w.now)
    raw = proof(w, payload)
    if ack_first:
        client._handle(raw, w.pin)
    compose(w, stop=lambda: (client._stop.set() or True)).disable(OP)
    if not ack_first:
        before = snapshot(w)
        client._handle(raw, w.pin)  # actual W02-P late signed settlement
        after = snapshot(w)
        expected = dict(before)
        expected['mail_pull_receipts'] = [dict(before['mail_pull_receipts'][0],
            cloud_receipt_id=payload['receipt_id'], cloud_committed_ms=payload['committed_ms'])]
        expected['mail_pull_ack_outbox'] = []
        assert after == expected
    assert_off(w)
    assert w.rows('mail_pull_receipts')[0]['cloud_receipt_id'] == payload['receipt_id']
    assert w.rows('mail_pull_ack_outbox') == []
    with w.store._read() as db:
        with pytest.raises(IngressRefused):
            client._current(db)
    with pytest.raises(IngressRefused):
        w.receiver.receive(w.wire())


def test_admitted_send_finishes_after_disable_without_another_send(out):
    w = out.kit
    add_flow(w)
    first = prepare(out)
    entered, release = threading.Event(), threading.Event()
    def hold(*args):
        entered.set()
        assert release.wait(10)
    out.recording.hook = hold
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(out.outbound.send, first['intent_id'])
        assert entered.wait(10)
        try:
            d = compose(w)
            # This provider fixture implements send only; denial remains pending.
            d.factory.service_client = lambda: (_ for _ in ()).throw(OSError('offline'))
            assert d.disable(OP)['revocation_pending'] is True
            row = w.rows('mail_outbound_intents')[0]
            assert row['state'] == 'sending' and row['attempt']
        finally:
            release.set()
        result = future.result(10)
    row = w.rows('mail_outbound_intents')[0]
    assert row['message_id'] and result['transport_state'] == 'accepted'
    assert w.rows('mail_pull_accounts')[0]['local_enabled'] == 0
    out.outbound.send(first['intent_id'])
    assert len(out.recording.calls) == 1


def test_disable_before_send_retires_prepared_intent_without_provider_call(out):
    add_flow(out.kit)
    first = prepare(out)
    d = compose(out.kit)
    d.factory.service_client = lambda: (_ for _ in ()).throw(OSError('offline'))
    d.disable(OP)
    assert out.outbound.send(first['intent_id'])['state'] == 'revoked'
    assert out.recording.calls == []


def test_retry_entry_is_bounded_and_creates_no_new_intent(w):
    for n in range(7):
        w.fence_row(op=f'{n:032x}', flow=f'{n + 20:032x}')
    before = snapshot(w)
    d = compose(w, max_passes=2)
    d.retry_revocations()
    assert len(w.calls) == 2
    assert sum(r['state'] == 'pending' for r in w.rows('mail_pull_fences')) == 5
    after = snapshot(w)
    assert {k: v for k, v in after.items() if k != 'mail_pull_fences'} == {
        k: v for k, v in before.items() if k != 'mail_pull_fences'}


def test_missing_token_op_cannot_revoke_later_independently_authorized_generation(w):
    with w.store._tx() as db:
        db.execute("UPDATE mail_pull_flows SET result_json='{}'")
    compose(w).disable(OP)
    assert w.rows('mail_pull_accounts')[0]['local_enabled'] == 0
    assert w.rows('mail_pull_fences') == []

    # Synthetic later human-authorized recovery in the SAME private store;
    # this setup does not implement or invoke a recovery API.
    new_flow = '2' * 32
    add_flow(w, flow=new_flow)
    with w.store._tx() as db:
        db.execute('UPDATE mail_pull_flows SET expected_generation=2 WHERE flow_id=?', (new_flow,))
        db.execute('''INSERT INTO mail_pull_generations
            SELECT install_id,2,owner_generation,key_id,?,manifest_sequence,
                manifest_digest,service_key_id,service_public_key,'current',created_ms,NULL
            FROM mail_pull_generations WHERE generation=1''', (new_flow,))
        db.execute("UPDATE mail_pull_accounts SET generation=2,seal_flow_id=?,state='active',"
                   "local_enabled=1,consent_epoch=3,reason=''", (new_flow,))
        db.execute('UPDATE registrations SET enabled=1,generation=2')
    before = snapshot(w)
    w.store = GuardedMailStore(w.store.path, clock=lambda: w.now / 1000)
    with pytest.raises(EnrollmentRefused, match='^operation_conflict$'):
        compose(w, stop=lambda: pytest.fail('replay cannot stop later client')).disable(OP)
    assert snapshot(w) == before and not w.calls
    assert w.rows('mail_pull_accounts')[0]['local_enabled'] == 1


def op_record(w):
    rows = w.rows('mail_pull_disable_ops')
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize('field,value', [('generation', 2), ('owner_generation', 2),
    ('flow_id', '3' * 32), ('input_digest', '4' * 64), ('install_id', '5' * 32)])
def test_every_immutable_binding_is_compared_before_duplicate_denial(w, field, value):
    compose(w).disable(OP)
    with w.store._tx() as db:
        if field == 'install_id':
            db.execute('''INSERT INTO mail_pull_accounts SELECT ?,generation,owner_generation,
                service_origin,address,owner_address,key_id,seal_flow_id,state,0,
                consent_version,consent_epoch,reason,updated_ms FROM mail_pull_accounts''', (value,))
        db.execute(f'UPDATE mail_pull_disable_ops SET {field}=?', (value,))
    before, calls = snapshot(w), len(w.calls)
    with pytest.raises(EnrollmentRefused, match='^operation_conflict$'):
        compose(w, stop=lambda: pytest.fail('conflict cannot stop')).disable(OP)
    assert snapshot(w) == before and len(w.calls) == calls


@pytest.mark.parametrize('field,value', [('state', 'held'), ('reason', 'invented'),
    ('result_json', '{}'), ('result_json', '{"enabled":true}'),
    ('result_json', '{"enabled":false,"enabled":false}'),
    ('result_json', '{"fence_token":"private"}')])
def test_duplicate_refuses_corrupt_original_result_without_rewriting_it(w, field, value):
    compose(w).disable(OP)
    with w.store._tx() as db:
        db.execute(f'UPDATE mail_pull_disable_ops SET {field}=?', (value,))
    before, calls = snapshot(w), len(w.calls)
    with pytest.raises(EnrollmentRefused, match='^schema_corrupt$'):
        compose(w, stop=lambda: pytest.fail('corrupt result cannot stop')).disable(OP)
    assert snapshot(w) == before and len(w.calls) == calls


def test_missing_token_duplicate_cannot_create_fence_after_token_is_restored(w):
    with w.store._tx() as db:
        db.execute("UPDATE mail_pull_flows SET result_json='{}'")
    compose(w).disable(OP)
    record = op_record(w)
    with w.store._tx() as db:
        db.execute('UPDATE mail_pull_flows SET result_json=?', (json.dumps({'fence_token': TOKEN}),))
    before = snapshot(w)
    w.store = GuardedMailStore(w.store.path, clock=lambda: w.now / 1000)
    result = compose(w).disable(OP)
    assert result['reason'] == 'fence_token_missing' and result['revocation_pending'] is True
    assert op_record(w) == record and snapshot(w) == before and not w.calls
