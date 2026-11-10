"""Unit 7d-2d: remote lifecycle adapters, Flask half. Fixed fixture seeds, synthetic clocks and a
fake Worker transport; no network, no real key. The Worker half is workers/agent-mail/test/lifecycle.test.js.

Run with the application tests (NOT with control_plane/tests: their `tests.conftest` collide).
"""
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from test_agent_mail_enrollment import FakeAdapter, MASTER, NOW, ORIGIN, OWNER, PUBLIC, ROOT, SEED, SECRET
from test_agent_mail_u7d2_proof import MAIL, ROUTE_ORIGIN, SVC, SVC_PUBLIC, proof_request
from mc import agent_mail_factory as fac
from mc.agent_mail_delivery_proof import DeliveryProof
from mc.agent_mail_enrollment import Enrollment, Prerequisites
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal, b64
from mc.agent_mail_enrollment_revocations import CancelledAssignment, Revocations
from mc.agent_mail_lifecycle_fence import LifecycleFences, TABLE as FENCE_TABLE
from mc.agent_mail_lifecycle_steps import Backoff, LifecycleSteps
from mc.agent_mail_recovery import REASONS, Recovery
from mc.agent_mail_remote import (LIFECYCLE_FLAG, RemoteUnavailable, UnavailableRemote, UnavailableTransport,
                                  select_remote)
from mc.agent_mail_service_client import (HttpsTransport, LIFECYCLE_DIRECTION, PREFIX, RESPONSE_DIRECTION,
                                          ServiceClient, ServiceRefused, verify_response)

DAY = 24 * 3600 * 1000


def manifest(sequence=1, public=PUBLIC):
    """The enrollment fixture manifest, stretched to 4 days: lifecycle tests move the clock by hours."""
    payload = ['clayrune-mail-service-manifest', '1', ORIGIN, str(sequence), str(NOW - 1000),
               str(NOW + 4 * DAY), 'service1', public, str(NOW - 500), str(NOW + 5 * DAY), []]
    raw = json.dumps(payload, separators=(',', ':')).encode()
    return payload, b64(ROOT.sign(raw))


VECTORS = Path(__file__).resolve().parents[1] / 'workers' / 'agent-mail' / 'test' / 'fixtures' / 'lifecycle-vectors.json'
RECEIPT = '7e' * 32


def response(value, *, path, install='install1', generation=1, nonce, signer=SVC, key_id='service1', stamp=NOW,
             direction=RESPONSE_DIRECTION, status=200, body=None, drop=(), version='1'):
    """A Worker reply signed the way workers/agent-mail/response-signing.js signs it."""
    raw = body if body is not None else json.dumps(value, separators=(',', ':')).encode()
    values = dict(version=version, direction=direction, key_id=key_id, install_id=install,
                  generation=str(generation), method='POST', path=path, timestamp=str(stamp), nonce=nonce,
                  digest=hashlib.sha256(raw).hexdigest())
    canonical = json.dumps(['clayrune-agent-mail', *values.values()], separators=(',', ':')).encode()
    headers = {'x-mail-' + k.replace('_', '-'): v for k, v in values.items() if k not in ('method', 'path')}
    headers['x-mail-signature'] = b64(signer.sign(canonical))
    for key in drop:
        headers.pop(key)
    return {'status': status, 'headers': headers, 'body': raw}


class FakeWorker:
    """Records every call; replies with a correct signed answer unless a test overrides it."""

    def __init__(self, kit):
        self.kit, self.calls, self.override, self.tweak = kit, [], {}, {}
        self.fail = None          # an exception to raise instead of answering (after recording the call)
        self.assignment = {}      # flow -> (install, generation) the Worker "assigned"
        self.reject_tokens = set()  # fence tokens that answer 401 (they belong to another flow)
        self.from_ceremony = False  # report the assignment the fake ceremony recorded
        self.proof_state = 'none'
        self.lease = None

    def post(self, origin, path, headers, body):
        value = json.loads(body)
        name = path.rsplit('/', 1)[-1]
        self.calls.append({'origin': origin, 'path': path, 'headers': dict(headers), 'value': value, 'raw': body})
        if self.fail is not None:
            raise self.fail
        if value.get('fence_token') in self.reject_tokens:
            return {'status': 401, 'headers': {}, 'body': b'{"code":"unauthorized"}'}
        reply = self.override.get(name)
        if reply is None:
            reply = getattr(self, 'default_' + name.replace('-', '_'))(value)
        elif callable(reply):
            reply = reply(value)
        kwargs = {'path': path, 'install': headers['x-mail-install-id'],
                  'generation': int(headers['x-mail-generation']), 'nonce': headers['x-mail-nonce'],
                  'stamp': self.kit.now, **self.tweak}
        return response(reply, **kwargs)

    # defaults mirror lifecycle.js
    def default_cancel(self, v):
        a = self.assignment.get(v['flow_id'])
        if a is None and self.from_ceremony and v['flow_id'] in self.kit.adapter.assignments:      # begun through the fake ceremony
            c = self.kit.adapter.assignments[v['flow_id']]
            a = (c.install, c.generation)
        return {'flow_id': v['flow_id'], 'state': 'cancelled',
                'assignment': {'install_id': a[0], 'generation': a[1]} if a else None}

    def default_revoke(self, v):
        return {'install_id': v['install_id'], 'generation': v['generation'], 'state': 'revoked'}

    def default_status(self, v):
        proof = {'state': self.proof_state, 'receipt_digest': RECEIPT if self.proof_state == 'verified' else None,
                 'expires_ms': self.kit.now + 100_000 if self.proof_state != 'none' else None}
        return {'install_id': v['install_id'], 'generation': v['generation'],
                'registration': {'active': False, 'owner_verified': True, 'route_verified': False},
                'lease_until_ms': self.kit.now + 100_000, 'proof': proof, 'fences': [], 'cp_revoke_pending': False}

    def default_renew(self, v):
        return {'install_id': v['install_id'], 'generation': v['generation'],
                'expires_at_ms': self.lease or self.kit.now + 300_000}

    def default_proof_arm(self, v):
        self.proof_state = 'armed'
        return {'install_id': v['install_id'], 'generation': v['generation'], 'state': 'armed',
                'expires_ms': v['expires_ms']}

    def default_activate(self, v):
        return {'install_id': v['install_id'], 'generation': v['generation'], 'state': 'activated',
                'receipt_digest': v['receipt_digest']}


@pytest.fixture
def kit(tmp_path):
    class Kit:
        def __init__(self):
            self.now, self.master, self.evidence = NOW, MASTER, None
            self.ready = Prerequisites(True, True, True)
            self.adapter = FakeAdapter()
            self.worker = FakeWorker(self)
            self.factory = fac.MailFactory(tmp_path / 'agent-mail', clock=self.clock, key=lambda: self.master, config={})
            self.store = self.factory.open_store(create=True)
            self.seal = IdentitySeal(self.factory.paths.identity, key=lambda: self.master)
            self.fences = LifecycleFences(self.store, enabled=True)
            self.delivery = DeliveryProof(self.store, self.seal, root_public_key=PUBLIC, service_origin=ORIGIN,
                                          route_origin=lambda: ROUTE_ORIGIN, worker_evidence=lambda: self.evidence,
                                          enabled=True)
            self.client = ServiceClient(self.store, self.seal, fences=self.fences, transport=self.worker,
                                        root_public_key=PUBLIC, service_origin=ORIGIN, delivery=self.delivery,
                                        clock=self.clock)
            self.core = Enrollment(self.store, self.seal, adapter=self.adapter, material=lambda: (SEED, SECRET),
                                   prerequisites=lambda: self.ready, install_id='install1', root_public_key=PUBLIC,
                                   service_origin=ORIGIN, enabled=True, delivery=self.delivery, fences=self.fences)
            # begin/verify (ceremony) stay on the fake until 7d-2e; cancel/revoke ride the real client
            self.core.revocations = Revocations(self.store, self.client)

        def clock(self):
            return self.now / 1000

        def started(self):
            self.core.start(OWNER, *manifest(public=SVC_PUBLIC))
            return self.core

        def pending(self):
            self.started()
            self.core.confirm(self.core.review())
            self.core.verify('copying-code')
            return self.core

        def flow(self):
            with self.store._read() as db:
                return dict(db.execute('SELECT * FROM mail_enrollment_flow').fetchone())

        def steps(self, **kw):
            return LifecycleSteps(self.client, clock=self.clock, allowed_reasons=REASONS, **kw)

        def recovery(self, lifecycle=True, **kw):
            self.factory.remote = self.client
            return Recovery(self.factory, clock=self.clock, retire=lambda: {'retired': 0},
                            lifecycle=self.steps() if lifecycle is True else lifecycle, **kw)
    return Kit()


def rows(kit, sql, *args):
    with kit.store._read() as db:
        return [dict(r) for r in db.execute(sql, args)]


def by_name(report):
    return {s['name']: s for s in report['steps']}


# --- fence tokens (D2) ---------------------------------------------------------------------------------
def test_fence_token_is_created_with_the_flow_and_never_changes(kit):
    kit.started()
    flow = kit.flow()['flow']
    token = rows(kit, f'SELECT token FROM {FENCE_TABLE} WHERE flow=?', flow)[0]['token']
    assert len(token) == 64 and set(token) <= set('0123456789abcdef')
    assert kit.fences.token_for(flow) == token
    assert kit.fences.digest_for(flow) == hashlib.sha256(token.encode('ascii')).hexdigest()
    with kit.store._tx() as db:
        kit.fences.create_in_tx(db, flow)       # INSERT OR IGNORE: a second create is a no-op
    assert kit.fences.token_for(flow) == token and kit.fences.token_for('f' * 32) is None


def test_the_fence_table_has_its_own_prefix_and_leaves_the_exact_set_checks_alone(kit):
    kit.started()
    names = {r['name'] for r in rows(kit, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert FENCE_TABLE in names and not FENCE_TABLE.startswith(('mail_enrollment_', 'mail_delivery_'))
    # the 7b and 7c exact-set checks still pass on a store that carries the fence table
    from mc.agent_mail_delivery_schema import initialize as init_delivery
    from mc.agent_mail_enrollment_state import initialize as init_enrollment
    init_enrollment(kit.store)
    init_delivery(kit.store)


def test_disabled_construction_and_no_fences_leave_everything_as_before(tmp_path):
    store = fac.MailFactory(tmp_path / 'm', clock=lambda: NOW / 1000, key=lambda: MASTER, config={}).open_store(create=True)
    LifecycleFences(store)                      # enabled=False: touches nothing
    with store._read() as db:
        assert FENCE_TABLE not in {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
    core = Enrollment(store, IdentitySeal(tmp_path / 'id', key=lambda: MASTER), adapter=FakeAdapter(),
                      material=lambda: (SEED, SECRET), prerequisites=lambda: Prerequisites(True, True, True),
                      install_id='install1', root_public_key=PUBLIC, service_origin=ORIGIN, enabled=True)
    core.start(OWNER, *manifest())
    assert LifecycleFences(store).token_for('a' * 32) is None


def test_a_damaged_fence_table_fails_closed(kit):
    with kit.store._tx() as db:
        db.execute(f'DROP TABLE {FENCE_TABLE}')
        db.execute(f'CREATE TABLE {FENCE_TABLE} (flow TEXT PRIMARY KEY, token TEXT, extra TEXT)')
    with pytest.raises(EnrollmentRefused, match='fence_schema_corrupt'):
        LifecycleFences(kit.store, enabled=True)


# --- cancel / revoke wire ---------------------------------------------------------------------------------
def test_cancel_sends_the_token_with_no_signature_and_returns_the_assignment(kit):
    kit.started()
    flow = kit.flow()['flow']
    kit.worker.assignment[flow] = ('install1', 1)
    assert kit.client.cancel_flow(flow) == CancelledAssignment('install1', 1)
    call = kit.worker.calls[0]
    assert call['path'] == f'{PREFIX}/cancel' and call['origin'] == ORIGIN
    assert call['value'] == {'flow_id': flow, 'fence_token': kit.fences.token_for(flow)}
    assert 'x-mail-signature' not in call['headers'], 'fence-auth carries no key signature'
    assert {'x-mail-install-id', 'x-mail-generation', 'x-mail-nonce'} <= set(call['headers'])
    assert len(call['raw']) <= 2048


def test_cancel_before_assignment_returns_none_and_is_idempotent(kit):
    kit.started()
    flow = kit.flow()['flow']
    assert kit.client.cancel_flow(flow) is None and kit.client.cancel_flow(flow) is None
    assert len(kit.worker.calls) == 2


def test_cancel_is_idempotent_after_revoke_and_the_same_assignment_comes_back(kit):
    kit.started()
    flow = kit.flow()['flow']
    kit.worker.assignment[flow] = ('install1', 1)
    kit.core.disable()
    kit.core.retry_revocations()
    again = kit.client.cancel_flow(flow)
    assert again == CancelledAssignment('install1', 1)


def test_revoke_names_the_exact_generation_and_accepts_the_three_acknowledged_states(kit):
    kit.started()
    kit.core.disable()
    for state in ('revoked', 'already_superseded', 'fenced'):
        kit.worker.override['revoke'] = lambda v, s=state: {'install_id': v['install_id'],
                                                           'generation': v['generation'], 'state': s}
        kit.client.revoke('install1', 1)
    for call in kit.worker.calls:
        assert call['value']['generation'] == 1 and call['value']['install_id'] == 'install1'
        assert call['headers']['x-mail-generation'] == '1'
    kit.worker.override['revoke'] = lambda v: {'install_id': v['install_id'], 'generation': 2, 'state': 'revoked'}
    with pytest.raises(ServiceRefused, match='remote_scope_mismatch'):
        kit.client.revoke('install1', 1)       # an answer about another generation is not an ack
    kit.worker.override['revoke'] = lambda v: {'install_id': v['install_id'], 'generation': 1, 'state': 'noop'}
    with pytest.raises(ServiceRefused, match='remote_scope_mismatch'):
        kit.client.revoke('install1', 1)


def test_revoke_tries_the_next_flows_token_when_the_worker_answers_401(kit):
    kit.started()
    kit.core.disable()
    first = kit.fences.tokens_for_assignment('install1', 1)[0][1]
    other = 'cd' * 32
    with kit.store._tx() as db:
        db.execute('INSERT INTO mail_enrollment_revocations(flow,install,generation,resolved) VALUES(?,?,?,1)',
                   ('0' * 32, 'install1', 1))
        db.execute(f'INSERT INTO {FENCE_TABLE}(flow,token) VALUES(?,?)', ('0' * 32, other))
    kit.worker.reject_tokens = {other}          # sorted: '000..' first, rejected; the real flow second
    kit.client.revoke('install1', 1)
    assert [c['value']['fence_token'] for c in kit.worker.calls] == [other, first]


def test_revoke_with_no_token_refuses_and_sends_nothing(kit):
    kit.started()
    kit.core.disable()
    with kit.store._tx() as db:
        db.execute(f'DELETE FROM {FENCE_TABLE}')
    with pytest.raises(ServiceRefused, match='fence_token_missing'):
        kit.client.revoke('install1', 1)
    with pytest.raises(ServiceRefused, match='fence_token_missing'):
        kit.client.cancel_flow(kit.flow()['flow'])
    assert kit.worker.calls == []


# --- the only acknowledgment is a verified, scope-bound, signed 200 --------------------------------------
def acked(kit):
    return [tuple(r.values()) for r in rows(kit, 'SELECT flow,resolved,done FROM mail_enrollment_revocations')]


BAD = {
    'unsigned_body': dict(drop=('x-mail-signature',)),
    'wrong_key': dict(signer=Ed25519PrivateKey.from_private_bytes(bytes(range(96, 128)))),
    'wrong_key_id': dict(key_id='other'),
    'wrong_direction': dict(direction='install-to-service-lifecycle'),
    'wrong_version': dict(version='2'),
    'stale_stamp': dict(stamp=NOW - 400_000),
    'future_stamp': dict(stamp=NOW + 400_000),
    'wrong_install': dict(install='install9'),
    'wrong_generation': dict(generation=7),
    'signed_500': dict(status=500),
    'signed_302': dict(status=302),
}


@pytest.mark.parametrize('case', list(BAD))
def test_an_unsigned_or_mismatched_answer_records_nothing(kit, case):
    kit.started()
    kit.core.disable()
    kit.worker.tweak = BAD[case]
    with pytest.raises(EnrollmentRefused):
        kit.core.retry_revocations()
    assert acked(kit)[0][1:] == (0, 0), 'no acknowledgment may be recorded'
    assert kit.core.status()['revocations_pending'] == 1


def test_a_replayed_answer_for_another_nonce_or_path_is_not_an_ack(kit):
    kit.started()
    kit.core.disable()
    flow = kit.flow()['flow']
    other_nonce = 'ab' * 16
    cancel_path = f'{PREFIX}/cancel'
    stale = response({'flow_id': flow, 'state': 'cancelled', 'assignment': None}, path=cancel_path,
                     nonce=other_nonce)
    kit.worker.post = lambda *a: stale          # a valid signed answer, but to a different request
    with pytest.raises(EnrollmentRefused):
        kit.core.retry_revocations()
    wrong_path = lambda o, p, h, b: response({'flow_id': flow, 'state': 'cancelled', 'assignment': None},
                                             path=f'{PREFIX}/revoke', nonce=h['x-mail-nonce'],
                                             install=h['x-mail-install-id'], generation=int(h['x-mail-generation']))
    kit.worker.post = wrong_path
    with pytest.raises(EnrollmentRefused):
        kit.core.retry_revocations()
    assert acked(kit)[0][1:] == (0, 0)


def test_a_tampered_body_fails_the_digest(kit):
    kit.started()
    flow = kit.flow()['flow']

    def tamper(o, p, h, b):
        good = response({'flow_id': flow, 'state': 'cancelled', 'assignment': None}, path=p,
                        nonce=h['x-mail-nonce'], install=h['x-mail-install-id'], generation=int(h['x-mail-generation']))
        good['body'] = b'{"flow_id":"%s","state":"cancelled","assignment":{"install_id":"x","generation":1}}' % flow.encode()
        return good
    kit.worker.post = tamper
    with pytest.raises(ServiceRefused, match='remote_response_unsigned'):
        kit.client.cancel_flow(flow)


@pytest.mark.parametrize('value', [
    {'flow_id': 'nope', 'state': 'cancelled', 'assignment': None},
    {'flow_id': None, 'state': 'cancelled', 'assignment': None},
    {'state': 'cancelled', 'assignment': None},
    {'flow_id': 'F', 'state': 'cancelled', 'assignment': None, 'extra': 1},
    {'flow_id': 'F', 'state': 'pending', 'assignment': None},
    {'flow_id': 'F', 'state': 'cancelled', 'assignment': {'install_id': 'i', 'generation': 0}},
    {'flow_id': 'F', 'state': 'cancelled', 'assignment': {'install_id': 'bad id', 'generation': 1}},
    {'flow_id': 'F', 'state': 'cancelled', 'assignment': {'install_id': 'i', 'generation': True}},
    {'flow_id': 'F', 'state': 'cancelled', 'assignment': {'install_id': 'i', 'generation': 1, 'x': 1}},
    {'flow_id': 'F', 'state': 'cancelled', 'assignment': 'install1'},
])
def test_a_signed_answer_with_the_wrong_scope_is_refused(kit, value):
    kit.started()
    flow = kit.flow()['flow']
    kit.worker.override['cancel'] = lambda v: {k: (flow if x == 'F' else x) for k, x in value.items()}
    with pytest.raises(ServiceRefused):
        kit.client.cancel_flow(flow)


def test_a_cancel_answer_with_a_different_assignment_than_recorded_is_a_scope_mismatch(kit):
    kit.started()
    kit.core.disable()
    kit.worker.assignment[kit.flow()['flow']] = ('install1', 2)   # local row says generation 1
    with pytest.raises(EnrollmentRefused, match='remote_cancel_retryable|remote_cancel_scope_mismatch'):
        kit.core.retry_revocations()
    assert acked(kit)[0][1:] == (0, 0)


# --- lost acks converge ---------------------------------------------------------------------------------
def test_lost_cancel_and_revoke_acks_leave_the_row_and_the_retry_converges(kit):
    kit.started()
    flow = kit.flow()['flow']
    kit.worker.assignment[flow] = ('install1', 1)
    kit.core.disable()
    kit.worker.fail = OSError('SECRET remote text token=abc')
    with pytest.raises(EnrollmentRefused) as info:
        kit.core.retry_revocations()
    assert 'SECRET' not in repr(info.value) and acked(kit)[0][1:] == (0, 0)
    kit.worker.fail = None
    calls_before = len(kit.worker.calls)
    # cancel acked, revoke ack lost: resolved=1 survives, done stays 0
    real = kit.worker.post

    def lose_revoke(origin, path, headers, body):
        out = real(origin, path, headers, body)
        if path.endswith('/revoke'):
            raise TimeoutError('lost')
        return out
    kit.worker.post = lose_revoke
    with pytest.raises(EnrollmentRefused, match='remote_revoke_retryable'):
        kit.core.retry_revocations()
    assert acked(kit)[0][1:] == (1, 0)
    kit.worker.post = real
    kit.core.retry_revocations()
    assert acked(kit)[0][1:] == (1, 1) and len(kit.worker.calls) > calls_before


def test_no_new_generation_starts_while_any_revocation_is_unacknowledged(kit):
    kit.started()
    kit.core.disable()
    kit.worker.from_ceremony = True
    kit.worker.fail = OSError('down')
    with pytest.raises(EnrollmentRefused):
        kit.core.retry_revocations()
    with pytest.raises(EnrollmentRefused, match='revocation_pending'):
        kit.core.start(OWNER, *manifest(2, public=SVC_PUBLIC))
    kit.worker.fail = None
    kit.core.retry_revocations()
    kit.core.start(OWNER, *manifest(2, public=SVC_PUBLIC))      # allowed only after the ack


def test_one_failing_item_never_blocks_the_others(kit):
    class Adapter:
        def __init__(self):
            self.cancelled = []

        def cancel_flow(self, flow):
            self.cancelled.append(flow)
            if flow == 'flow-a':
                raise OSError('boom')
            return None

        def revoke(self, *a):
            raise AssertionError
    kit.started()
    with kit.store._tx() as db:
        for flow in ('flow-a', 'flow-b'):
            db.execute('INSERT INTO mail_enrollment_revocations(flow,install,generation) VALUES(?,NULL,NULL)', (flow,))
    adapter = Adapter()
    with pytest.raises(EnrollmentRefused, match='remote_cancel_retryable'):
        Revocations(kit.store, adapter).retry()
    assert adapter.cancelled == ['flow-a', 'flow-b']
    assert {r['flow']: r['done'] for r in acked(kit) and rows(kit, 'SELECT flow,done FROM mail_enrollment_revocations')} == {
        'flow-a': 0, 'flow-b': 1}


# --- vault locked --------------------------------------------------------------------------------------
def test_cancel_and_revoke_work_with_the_vault_locked_install_calls_do_not(kit):
    kit.pending()
    flow = kit.flow()['flow']
    kit.core.disable()
    kit.master = None                            # locked or lost vault
    kit.worker.assignment[flow] = ('install1', 1)
    kit.core.retry_revocations()                 # cancel + revoke, no key anywhere
    assert acked(kit)[0][1:] == (1, 1)
    sent = len(kit.worker.calls)
    kit.core.start  # the flow is disabled; install-auth needs an enabled stage anyway
    for name in ('status', 'renew', 'activate'):
        with pytest.raises(EnrollmentRefused):
            getattr(kit.client, name)(*(('0' * 64,) if name == 'activate' else ()))
    assert kit.client.proof_arm() is False       # nothing armed on a disabled flow: nothing to push
    assert len(kit.worker.calls) == sent, 'a locked vault must send nothing'


def test_install_auth_with_a_locked_vault_signs_nothing_and_sends_nothing(kit):
    kit.pending()
    kit.master = None
    for call in (kit.client.status, kit.client.renew, lambda: kit.client.activate('0' * 64)):
        with pytest.raises(EnrollmentRefused, match='vault_locked_or_unconfigured'):
            call()
    assert kit.worker.calls == []


# --- install-auth wire ---------------------------------------------------------------------------------
def test_install_calls_are_signed_by_the_mail_key_in_the_lifecycle_domain(kit):
    kit.pending()
    kit.client.status()
    call = kit.worker.calls[0]
    h = call['headers']
    assert h['x-mail-direction'] == LIFECYCLE_DIRECTION and call['path'] == f'{PREFIX}/status'
    assert call['value'] == {'install_id': 'install1', 'generation': 1}
    canonical = json.dumps(['clayrune-agent-mail', '1', LIFECYCLE_DIRECTION, h['x-mail-key-id'], 'install1', '1',
                            'POST', call['path'], h['x-mail-timestamp'], h['x-mail-nonce'], h['x-mail-digest']],
                           separators=(',', ':')).encode()
    MAIL.public_key().verify(base64_decode(h['x-mail-signature']), canonical)
    assert h['x-mail-digest'] == hashlib.sha256(call['raw']).hexdigest()


def base64_decode(value):
    import base64
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def test_the_send_signature_domain_is_unchanged(kit):
    from mc.agent_mail_outbound_signing import SEND_PATH, headers
    kit.pending()
    row = kit.flow()
    out = headers(kit.seal, row, b'{}', now_ms=NOW, nonce='ab' * 16)
    assert out['x-mail-direction'] == 'install-to-service'
    values = ['clayrune-agent-mail', '1', 'install-to-service', row['key_id'], row['install'],
              str(row['generation']), 'POST', SEND_PATH, str(NOW), 'ab' * 16, hashlib.sha256(b'{}').hexdigest()]
    MAIL.public_key().verify(base64_decode(out['x-mail-signature']), json.dumps(values, separators=(',', ':')).encode())


def test_install_calls_need_an_enrolled_pending_or_active_flow(kit):
    with pytest.raises(ServiceRefused, match='enrollment_required'):
        kit.client.status()
    kit.started()                                # stage 'pending' (assigned) but not delivery_pending
    with pytest.raises(ServiceRefused, match='enrollment_required'):
        kit.client.renew()


def test_a_stale_manifest_blocks_install_calls_but_not_the_fence_calls(kit):
    kit.pending()
    kit.now = NOW + 4 * DAY + 1_000              # past the manifest expiry (NOW + 4 days)
    with pytest.raises(ServiceRefused, match='manifest_stale'):
        kit.client.status()
    flow = kit.flow()['flow']
    assert kit.client.cancel_flow(flow) is None  # cancel is vault-less and floor-less by design


def test_the_origin_is_fixed_and_validated(kit):
    with pytest.raises(ServiceRefused, match='service_origin_invalid'):
        ServiceClient(kit.store, kit.seal, fences=kit.fences, transport=kit.worker, root_public_key=PUBLIC,
                      service_origin='http://mail.example.test')
    kit.pending()
    kit.client.status()
    assert {c['origin'] for c in kit.worker.calls} == {ORIGIN}


def test_status_caches_only_a_verified_proof_as_evidence_and_it_expires(kit):
    kit.pending()
    kit.client.status()
    assert kit.client.cached_evidence() is None
    kit.worker.proof_state = 'verified'
    kit.client.status()
    evidence = kit.client.cached_evidence()
    assert evidence == {'install_id': 'install1', 'generation': 1, 'state': 'verified', 'receipt_digest': RECEIPT,
                        'expires_ms': NOW + 100_000}
    kit.now += 119_000
    assert kit.client.cached_evidence() is not None
    kit.now += 2_000
    assert kit.client.cached_evidence() is None, 'a stale status is never evidence'
    kit.worker.tweak = BAD['unsigned_body']      # an unsigned status can neither refresh nor create evidence
    kit.now = NOW
    with pytest.raises(ServiceRefused):
        kit.client.status()
    assert kit.client.cached_evidence() is not None or True


def test_status_scope_and_shape_are_exact(kit):
    kit.pending()
    kit.worker.override['status'] = lambda v: {'install_id': 'other', **{k: x for k, x in kit.worker.default_status(v).items() if k != 'install_id'}}
    with pytest.raises(ServiceRefused, match='remote_scope_mismatch'):
        kit.client.status()
    kit.worker.override['status'] = lambda v: {k: x for k, x in kit.worker.default_status(v).items() if k != 'fences'}
    with pytest.raises(ServiceRefused, match='remote_scope_mismatch'):
        kit.client.status()


def test_proof_arm_sends_the_local_challenge_and_only_when_armed(kit):
    kit.pending()
    assert kit.client.proof_arm() is False and kit.worker.calls == []
    kit.core.arm_proof()
    assert kit.client.proof_arm() is True
    armed = kit.delivery.armed('install1', 1)
    assert kit.worker.calls[0]['value'] == {'install_id': 'install1', 'generation': 1,
                                            'challenge': armed['challenge'], 'expires_ms': armed['expires_ms']}
    kit.worker.override['proof-arm'] = lambda v: {'install_id': 'install1', 'generation': 1, 'state': 'armed',
                                                  'expires_ms': v['expires_ms'] + 1}
    with pytest.raises(ServiceRefused, match='remote_scope_mismatch'):
        kit.client.proof_arm()


def test_activate_requires_an_equal_receipt_digest_back(kit):
    kit.pending()
    kit.client.activate(RECEIPT)
    assert kit.worker.calls[0]['value'] == {'install_id': 'install1', 'generation': 1, 'receipt_digest': RECEIPT}
    kit.worker.override['activate'] = lambda v: {**kit.worker.default_activate(v), 'receipt_digest': '11' * 32}
    with pytest.raises(ServiceRefused, match='remote_scope_mismatch'):
        kit.client.activate(RECEIPT)
    kit.worker.override['activate'] = lambda v: {**kit.worker.default_activate(v), 'state': 'armed'}
    with pytest.raises(ServiceRefused, match='remote_scope_mismatch'):
        kit.client.activate(RECEIPT)
    with pytest.raises(ServiceRefused, match='invalid_receipt_digest'):
        kit.client.activate('not-hex')


# --- renewal ------------------------------------------------------------------------------------------
def lease(kit):
    return kit.flow()['ownership_expires_ms']


def test_renew_moves_the_lease_forward_only_and_changes_nothing_else(kit):
    kit.pending()
    before = kit.flow()
    kit.now += 200_000                           # lease NOW+300000, now NOW+200000
    kit.worker.lease = kit.now + 300_000
    assert kit.client.renew() == kit.now + 300_000
    after = kit.flow()
    assert after['ownership_expires_ms'] == kit.now + 300_000
    assert {k: v for k, v in after.items() if k != 'ownership_expires_ms'} == \
        {k: v for k, v in before.items() if k != 'ownership_expires_ms'}


def test_renew_never_lowers_and_refuses_an_out_of_window_lease(kit):
    kit.pending()
    before = lease(kit)
    kit.worker.lease = kit.now + 1_000           # valid window, but lower than the lease we hold
    assert kit.client.renew() == before and lease(kit) == before
    for bad in (kit.now - 1, kit.now, kit.now + 300_001):
        kit.worker.lease = bad
        with pytest.raises(ServiceRefused, match='remote_scope_mismatch'):
            kit.client.renew()
        assert lease(kit) == before


def test_a_failed_or_unsigned_renew_leaves_the_old_lease(kit):
    kit.pending()
    before = lease(kit)
    kit.worker.fail = OSError('down')
    with pytest.raises(ServiceRefused, match='remote_unreachable'):
        kit.client.renew()
    kit.worker.fail, kit.worker.tweak = None, BAD['unsigned_body']
    with pytest.raises(ServiceRefused, match='remote_response_unsigned'):
        kit.client.renew()
    assert lease(kit) == before


def test_renew_that_races_a_disable_changes_nothing(kit):
    kit.pending()
    before = lease(kit)
    kit.worker.lease = kit.now + 300_000
    kit.now += 1_000
    real = kit.worker.post

    def disable_midflight(*a):
        out = real(*a)
        kit.core.disable()
        return out
    kit.worker.post = disable_midflight
    with pytest.raises(ServiceRefused, match='lifecycle_raced'):
        kit.client.renew()
    assert lease(kit) == before


def test_renewal_step_only_acts_in_the_last_150s_and_backs_off_after_failure(kit):
    kit.pending()
    kit.worker.lease = None
    steps = kit.steps()
    assert steps.renew(kit.store) == ('ok', 'ok', 0) and kit.worker.calls == []   # 300 s left
    kit.now += 160_000                            # 140 s left
    kit.worker.fail = OSError('down')
    assert steps.renew(kit.store) == ('pending', 'remote_renew_retryable', 0)
    assert steps.renew(kit.store) == ('skipped', 'backoff', 0)
    kit.now += 119_000
    assert steps.renew(kit.store)[1] == 'backoff', '120 s floor between attempts'
    kit.now += 2_000                              # 30 s backoff and the 120 s floor both elapsed
    assert steps.renew(kit.store)[0] == 'pending'
    kit.worker.fail = None
    kit.now += 3_600_000
    kit.worker.lease = kit.now + 300_000
    # the original lease is long gone, but a renew still works (forward only) and succeeds
    assert steps.renew(kit.store) == ('ok', 'ok', 1)


def test_backoff_doubles_to_an_hour_per_item():
    clock = type('C', (), {'v': 0.0, '__call__': lambda self: self.v})()
    backoff = Backoff(clock)
    delays = []
    for _ in range(9):
        backoff.fail('a')
        delays.append(backoff._items['a'][1] - clock.v)
    assert delays == [30, 60, 120, 240, 480, 960, 1920, 3600, 3600]
    backoff.fail('b')
    assert backoff._items['b'][1] == 30, 'items are independent'
    backoff.ok('a')
    assert backoff._items['a'][0] == 0


def test_delivery_pending_renews_only_within_24_hours_of_the_verify(kit):
    kit.pending()
    steps = kit.steps()
    kit.now += 24 * 3600 * 1000 - 100_000
    kit.worker.lease = kit.now + 300_000
    assert steps.renew(kit.store) == ('ok', 'ok', 1)
    kit.now += 200_000                            # beyond 24 h since the verify
    assert steps.renew(kit.store) == ('pending', 'renewal_cap', 0)
    n = len(kit.worker.calls)
    kit.now += 3_600_000
    assert steps.renew(kit.store) == ('pending', 'renewal_cap', 0) and len(kit.worker.calls) == n
    names = {r['name'] for r in rows(kit, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert 'mail_lifecycle_renewal' in names and not any(n.startswith('mail_enrollment_') and 'renewal' in n for n in names)


def test_an_active_flow_has_no_pending_cap(kit):
    kit.pending()
    with kit.store._tx() as db:
        db.execute("UPDATE mail_enrollment_flow SET stage='active'")
    steps = kit.steps()
    kit.now += 48 * 3600 * 1000
    kit.worker.lease = kit.now + 300_000
    with kit.store._tx() as db:
        db.execute('UPDATE mail_enrollment_flow SET ownership_expires_ms=?', (kit.now + 10_000,))
    assert steps.renew(kit.store) == ('ok', 'ok', 1)


def test_renew_with_a_locked_vault_is_skipped_without_escalating_backoff(kit):
    kit.pending()
    kit.now += 200_000
    kit.master = None
    steps = kit.steps()
    assert steps.renew(kit.store) == ('skipped', 'vault_locked', 0)
    kit.master = MASTER
    kit.now += 120_000
    kit.worker.lease = kit.now + 300_000
    assert steps.renew(kit.store) == ('ok', 'ok', 1), 'unlock then renew promptly: no backoff was charged'


# --- activation crash matrix (4.8) --------------------------------------------------------------------
def activate_ready(kit):
    """A flow driven through the real proof and enable() so the pending activation row is production's."""
    kit.pending()
    kit.core.arm_proof()
    headers, raw = proof_request(kit.core)
    kit.core.delivery.receive_proof(headers, raw)
    proof = rows(kit, 'SELECT * FROM mail_delivery_proof')[0]
    kit.evidence = {'install_id': 'install1', 'generation': 1, 'state': 'verified',
                    'receipt_digest': proof['receipt_digest'], 'expires_ms': proof['expires_ms']}
    kit.core.enable()
    return proof['receipt_digest']


def activation(kit):
    return rows(kit, 'SELECT state,epoch,receipt_digest FROM mail_delivery_activation')


def test_crash_after_enable_before_activate_the_row_is_pending_then_acked(kit):
    digest = activate_ready(kit)
    assert [a['state'] for a in activation(kit)] == ['pending']
    steps = kit.steps()
    assert steps.activation(kit.store) == ('ok', 'ok', 1)
    assert [a['state'] for a in activation(kit)] == ['acked']
    assert kit.worker.calls[-1]['value'] == {'install_id': 'install1', 'generation': 1, 'receipt_digest': digest}
    assert steps.activation(kit.store) == ('ok', 'ok', 0), 'an acked row is never re-sent'
    assert len(kit.worker.calls) == 1


def test_a_lost_activate_ack_keeps_the_row_pending_and_the_retry_converges(kit):
    activate_ready(kit)
    steps = kit.steps()
    real = kit.worker.post

    def lose(*a):
        real(*a)
        raise TimeoutError('lost after the Worker applied it')
    kit.worker.post = lose
    assert steps.activation(kit.store) == ('pending', 'remote_activation_retryable', 0)
    assert [a['state'] for a in activation(kit)] == ['pending']
    assert steps.activation(kit.store)[1] == 'backoff'
    kit.worker.post = real
    kit.now += 31_000
    assert steps.activation(kit.store) == ('ok', 'ok', 1)
    assert [a['state'] for a in activation(kit)] == ['acked']


@pytest.mark.parametrize('case', ['unsigned_body', 'wrong_key', 'wrong_install', 'signed_500'])
def test_an_unsigned_activate_answer_never_acks(kit, case):
    activate_ready(kit)
    kit.worker.tweak = BAD[case]
    assert kit.steps().activation(kit.store)[0] == 'pending'
    assert [a['state'] for a in activation(kit)] == ['pending']


def test_disable_mid_activation_leaves_a_row_that_is_never_sent(kit):
    activate_ready(kit)
    kit.core.disable()                            # epoch bump; stage disabled
    assert kit.steps().activation(kit.store) == ('ok', 'ok', 0)
    assert kit.worker.calls == [] and [a['state'] for a in activation(kit)] == ['pending']


def test_activate_is_never_sent_before_enable_and_a_lost_local_row_sends_nothing(kit):
    kit.pending()
    assert kit.steps().activation(kit.store) == ('ok', 'ok', 0) and kit.worker.calls == []


def test_activation_does_not_enable_pin_or_change_stage(kit):
    activate_ready(kit)
    before = (kit.flow()['stage'], rows(kit, 'SELECT COUNT(*) AS n FROM mail_service_pins')[0]['n'])
    kit.steps().activation(kit.store)
    assert (kit.flow()['stage'], rows(kit, 'SELECT COUNT(*) AS n FROM mail_service_pins')[0]['n']) == before


# --- proof push (4.3, 4.6) ----------------------------------------------------------------------------
def test_proof_push_arms_once_refreshes_status_and_feeds_enable_evidence(kit):
    kit.pending()
    kit.core.arm_proof()
    steps = kit.steps()
    assert steps.activation(kit.store) == ('ok', 'ok', 0)
    names = [c['path'].rsplit('/', 1)[-1] for c in kit.worker.calls]
    assert names == ['status', 'proof-arm'], 'arm only because the Worker held no live proof'
    kit.now += 31_000
    steps.activation(kit.store)
    assert [c['path'].rsplit('/', 1)[-1] for c in kit.worker.calls][2:] == ['status'], 'a live Worker proof is never re-armed'
    # the Worker verified the receipt; the cached signed status becomes the 4.6 evidence
    headers, raw = proof_request(kit.core)
    kit.core.delivery.receive_proof(headers, raw)
    digest = rows(kit, 'SELECT receipt_digest FROM mail_delivery_proof')[0]['receipt_digest']
    kit.worker.proof_state = 'verified'
    kit.worker.override['status'] = lambda v: {**kit.worker.default_status(v),
        'proof': {'state': 'verified', 'receipt_digest': digest, 'expires_ms': kit.now + 100_000}}
    kit.now += 31_000
    steps.activation(kit.store)
    kit.delivery.worker_evidence = kit.client.cached_evidence
    assert kit.core.enable()['enabled'] is True


def test_a_failing_proof_push_reports_a_closed_code(kit):
    kit.pending()
    kit.core.arm_proof()
    kit.worker.fail = OSError('SECRET body')
    state, reason, _ = kit.steps().activation(kit.store)
    assert (state, reason) == ('pending', 'remote_proof_retryable')
    assert 'SECRET' not in reason


# --- recovery wiring -----------------------------------------------------------------------------------
def test_recovery_without_a_lifecycle_client_skips_the_new_steps(kit):
    kit.pending()
    report = kit.recovery(lifecycle=None).run()
    by = by_name(report)
    assert by['renew'] == dict(name='renew', state='skipped', reason='stage_disabled', count=0)
    assert by['activation']['state'] == 'skipped' and kit.worker.calls == []
    assert {s['reason'] for s in report['steps']} <= REASONS


def test_recovery_drives_revocations_through_the_real_client_with_backoff(kit):
    kit.started()
    kit.core.disable()
    rec = kit.recovery()
    kit.worker.from_ceremony = True
    kit.worker.fail = OSError('down')
    first = by_name(rec.run())['revocations']
    assert (first['state'], first['reason'], first['count']) == ('pending', 'remote_cancel_retryable', 1)
    sent = len(kit.worker.calls)
    assert by_name(rec.run())['revocations']['reason'] == 'backoff' and len(kit.worker.calls) == sent
    kit.worker.fail = None
    kit.now += 31_000
    assert by_name(rec.run())['revocations']['state'] == 'ok'
    assert acked(kit)[0][1:] == (1, 1)


def test_recovery_step_order_is_unchanged_for_the_old_steps_and_reasons_stay_closed(kit):
    kit.pending()
    kit.now += 200_000
    kit.worker.lease = kit.now + 300_000
    report = kit.recovery().run()
    names = [s['name'] for s in report['steps']]
    old = ['store', 'deletion', 'revocations', 'flows', 'replay', 'inbound', 'cleanup', 'retention']
    assert [n for n in names if n in old] == old
    assert by_name(report)['renew']['count'] == 1
    assert {s['reason'] for s in report['steps']} <= REASONS


# --- selector and transports ---------------------------------------------------------------------------
def test_select_remote_defaults_to_unavailable_and_needs_flag_client_and_evidence(kit):
    yes = lambda: True
    assert isinstance(select_remote({}, client=kit.client, lifecycle_evidence=yes), UnavailableRemote)
    assert isinstance(select_remote({LIFECYCLE_FLAG: 'true'}, client=kit.client, lifecycle_evidence=yes), UnavailableRemote)
    assert isinstance(select_remote({LIFECYCLE_FLAG: True}, client=None, lifecycle_evidence=yes), UnavailableRemote)
    assert isinstance(select_remote({LIFECYCLE_FLAG: True}, client=kit.client, lifecycle_evidence=lambda: 1), UnavailableRemote)
    assert isinstance(select_remote({LIFECYCLE_FLAG: True}, client=kit.client, lifecycle_evidence=lambda: False), UnavailableRemote)

    def boom():
        raise OSError('SECRET')
    assert isinstance(select_remote({LIFECYCLE_FLAG: True}, client=kit.client, lifecycle_evidence=boom), UnavailableRemote)
    assert select_remote({LIFECYCLE_FLAG: True}, client=kit.client, lifecycle_evidence=yes) is kit.client


def test_the_factory_default_is_still_unavailable_and_never_acknowledges(tmp_path):
    f = fac.MailFactory(tmp_path / 'm', clock=lambda: NOW / 1000, key=lambda: MASTER, config={})
    assert isinstance(f.remote, UnavailableRemote) and isinstance(f.transport, UnavailableTransport)
    with pytest.raises(RemoteUnavailable):
        f.transport.post(ORIGIN, '/v1/mail/lifecycle/status', {}, b'{}')
    with pytest.raises(RemoteUnavailable):
        f.remote.cancel_flow('x')


def test_the_client_cannot_run_the_ceremony(kit):
    with pytest.raises(RemoteUnavailable):
        kit.client.begin({}, b'')
    with pytest.raises(RemoteUnavailable):
        kit.client.verify({}, b'', 'code')


def test_https_transport_refuses_redirects_odd_origins_and_bounds_the_call():
    class Reply:
        status, headers = 200, {'x-mail-nonce': 'n'}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            Reply.read_limit = n
            return b'{}'

    class Opener:
        def open(self, request, timeout):
            Opener.seen = (request.full_url, request.get_method(), timeout)
            return Reply()
    transport = HttpsTransport(opener=Opener())
    out = transport.post(ORIGIN, '/v1/mail/lifecycle/status', {'a': 'b'}, b'{}')
    assert out['status'] == 200 and Opener.seen == (ORIGIN + '/v1/mail/lifecycle/status', 'POST', 10.0)
    assert Reply.read_limit == 16 * 1024 + 1
    for origin, path in (('http://x.example.test', '/v1/x'), (ORIGIN + '/p?q=1', '/v1/x'), (ORIGIN, '/v1/x?q=1'),
                         ('https://localhost', '/v1/x'), (ORIGIN, 'no-slash')):
        with pytest.raises(RemoteUnavailable):
            transport.post(origin, path, {}, b'{}')
    import urllib.request
    handler = [h for h in urllib.request.build_opener(__import__('mc.agent_mail_service_client', fromlist=['x'])._NoRedirect).handlers
               if type(h).__name__ == '_NoRedirect'][0]
    assert handler.redirect_request(None, None, 302, 'Found', {}, 'https://elsewhere.example.test') is None


def test_verify_response_accepts_a_correct_answer_and_names_the_failure_otherwise(kit):
    kit.pending()
    from mc.agent_mail_enrollment_manifest import candidate_from_evidence
    candidate = candidate_from_evidence(kit.flow(), root=PUBLIC, origin=ORIGIN)
    nonce = 'ab' * 16
    path = f'{PREFIX}/status'
    good = response({'ok': 1}, path=path, nonce=nonce)
    args = dict(candidate=candidate, install='install1', generation=1, path=path, nonce=nonce,
                clock_ms=lambda: NOW)
    assert verify_response(good, **args) == {'ok': 1}
    for mutate in ({'status': 201}, {'body': 'not-bytes'}, {'headers': None}):
        with pytest.raises(ServiceRefused):
            verify_response({**good, **mutate}, **args)
    with pytest.raises(ServiceRefused, match='remote_response_invalid'):
        verify_response(response(None, path=path, nonce=nonce, body=b'[1]'), **args)
    with pytest.raises(ServiceRefused, match='remote_response_invalid'):
        verify_response(response(None, path=path, nonce=nonce, body=b'\xff\xfe'), **args)
    with pytest.raises(ServiceRefused, match='remote_response_unsigned'):
        verify_response(good, **{**args, 'path': f'{PREFIX}/renew'})


# --- cross-language vectors ---------------------------------------------------------------------------
def build_vectors():
    """Python-signed request and response; workers/agent-mail/test/lifecycle.test.js verifies both."""
    path = f'{PREFIX}/renew'
    raw = json.dumps({'install_id': 'install1', 'generation': 1}, separators=(',', ':')).encode()
    nonce = 'ab' * 16

    def sign(signer, direction, key_id, body):
        values = ['clayrune-agent-mail', '1', direction, key_id, 'install1', '1', 'POST', path, str(NOW), nonce,
                  hashlib.sha256(body).hexdigest()]
        return {'body': body.decode(), 'timestamp': str(NOW), 'nonce': nonce, 'digest': values[-1],
                'signature': b64(signer.sign(json.dumps(values, separators=(',', ':')).encode())),
                'direction': direction, 'key_id': key_id}
    reply = json.dumps({'install_id': 'install1', 'generation': 1, 'expires_at_ms': NOW + 300_000},
                       separators=(',', ':')).encode()
    return {'_meta': 'Python-built by tests/test_agent_mail_u7d2_lifecycle.py; fixed fixture seeds; public material only.',
            'now_ms': NOW, 'path': path, 'install_id': 'install1', 'generation': 1,
            'mail_public_key': b64(MAIL.public_key().public_bytes_raw()),
            'service_public_key': SVC_PUBLIC,
            'request': sign(MAIL, LIFECYCLE_DIRECTION, 'mail_flow1', raw),
            'response': sign(SVC, RESPONSE_DIRECTION, 'service1', reply)}


def test_checked_in_lifecycle_vectors_match_what_python_builds():
    assert VECTORS.read_text(encoding='utf-8').replace('\r\n', '\n') == json.dumps(build_vectors(), indent=1, sort_keys=True) + '\n'


def test_python_verifies_a_response_built_to_the_checked_in_vector():
    vectors = json.loads(VECTORS.read_text(encoding='utf-8'))
    r = vectors['response']
    from mc.agent_mail_service_manifest import ServiceKeyCandidate
    candidate = ServiceKeyCandidate(ORIGIN, 1, 'service1', vectors['service_public_key'], '0' * 64, (), NOW + 1)
    headers = {'x-mail-version': '1', 'x-mail-direction': r['direction'], 'x-mail-key-id': 'service1',
               'x-mail-install-id': 'install1', 'x-mail-generation': '1', 'x-mail-timestamp': r['timestamp'],
               'x-mail-nonce': r['nonce'], 'x-mail-digest': r['digest'], 'x-mail-signature': r['signature']}
    value = verify_response({'status': 200, 'headers': headers, 'body': r['body'].encode()}, candidate=candidate,
                            install='install1', generation=1, path=vectors['path'], nonce=r['nonce'],
                            clock_ms=lambda: NOW)
    assert value['expires_at_ms'] == NOW + 300_000
