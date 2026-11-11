"""Unit 7d-2e-3: Flask ceremony adapter, end to end over an in-process fake of the Worker and the control plane.

Fixed fixture seeds, a synthetic clock, no network, no real key, no `mc_remote`. The fake Worker and CP verify
the device and mail proofs the adapter signs and answer in the ceremony envelope the Python-built vectors of
7d-2e-2 pin (`test_agent_mail_u7d2e_ceremony_vectors.py`), so a divergence between the two halves shows here.

Run with the application tests (NOT with control_plane/tests: their `tests.conftest` collide).
"""
import base64
import hashlib
import json
import subprocess
import sys
import types
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
import pytest

from test_agent_mail_enrollment import MASTER, NOW, ORIGIN, OWNER, PUBLIC, SEED, SECRET
from test_agent_mail_u7d2_lifecycle import manifest, response
from test_agent_mail_u7d2_proof import SVC_PUBLIC, ROUTE_ORIGIN
from test_agent_mail_u7d2e_ceremony_vectors import DEVICE, TUNNEL, canonical
from mc import agent_mail_factory as fac
from mc.agent_mail_ceremony_client import GRANT_PATH, R_FIELDS, CeremonyClient, CeremonyRefused
from mc.agent_mail_ceremony_device import (DEVICE_UNAVAILABLE, LocalDeviceIdentity, device_key_b64url,
                                           keystore_identity)
from mc.agent_mail_ceremony_remote import CompositeRemote, select_ceremony
from mc.agent_mail_enrollment import Enrollment, OwnershipReceipt, PendingIdentity, Prerequisites
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal, b64
from mc.agent_mail_enrollment_revocations import CancelledAssignment, Revocations
from mc.agent_mail_enrollment_state import initialize as init_enrollment
from mc.agent_mail_lifecycle_fence import LifecycleFences
from mc.agent_mail_remote import UnavailableRemote
from mc.agent_mail_service_client import ServiceClient
from mc.agent_mail_store import MailStore

CP = 'https://authority.example.test'
CONFIG = {'agent_mail_ceremony_enabled': True, 'agent_mail_enrollment_enabled': True, 'agent_mail_grant_origin': CP}
DEV = LocalDeviceIdentity('device1', b64(DEVICE.public_key().public_bytes_raw()), TUNNEL, ROUTE_ORIGIN, DEVICE.sign)
ROOT_DIR = Path(__file__).resolve().parents[1]
BEGIN, CONFIRM, COMPLETE = ('/v1/mail/enrollment/' + n for n in ('begin', 'confirm', 'complete'))


def unsigned(status):
    return {'status': status, 'headers': {}, 'body': b'{"code":"remote text that must never escape"}'}


def verify_sig(public_b64url, message, signature_b64url):
    pad = lambda s: s + '=' * (-len(s) % 4)
    try:
        Ed25519PublicKey.from_public_bytes(base64.urlsafe_b64decode(pad(public_b64url))).verify(
            base64.urlsafe_b64decode(pad(signature_b64url)), message)
        return True
    except (InvalidSignature, ValueError):
        return False


class World:
    """The Worker's ceremony routes and the CP's grant route, as one recording fake."""

    def __init__(self, kit):
        self.kit, self.calls = kit, []
        self.install, self.expected, self.code = 'install1', 0, 'a1' * 16
        self.flows, self.grants, self.wrong, self.mails = {}, 0, 0, 0
        self.status = {}        # route name -> non-200 status answered instead
        self.cp_status = 200
        self.tweak = {}         # kwargs overriding the signed reply envelope (signer, nonce, path, install ...)
        self.begin_body = lambda body: body
        self.complete_body = lambda body: body
        self.lease = None
        self.drop_before = {}   # route -> count: raise before processing (request lost)
        self.drop_after = {}    # route -> count: process, then raise (reply lost)

    def named(self, path):
        return [c for c in self.calls if c['path'] == path]

    def post(self, origin, path, headers, body):
        value = json.loads(body)
        name = path.rsplit('/', 1)[-1]
        self.calls.append({'origin': origin, 'path': path, 'headers': dict(headers), 'value': value})
        if self.drop_before.get(name):
            self.drop_before[name] -= 1
            raise OSError('request lost')
        if origin == CP:
            reply = self._grant(value)
        else:
            assert origin == ORIGIN and path.startswith('/v1/mail/enrollment/')
            if name in self.status:
                reply = unsigned(self.status[name])
            else:
                reply = getattr(self, '_' + name)(value, headers)
        if self.drop_after.get(name):
            self.drop_after[name] -= 1
            raise OSError('reply lost')
        return reply

    def _sign(self, body, path, flow, headers):
        kw = dict(path=path, install=flow, generation=1, nonce=headers['x-mail-nonce'], stamp=self.kit.now)
        kw.update(self.tweak)
        return response(body, **kw)

    def _begin(self, value, headers):
        v = value['value']
        flow = v['flow_id']
        state = self.flows.get(flow)
        sent = state is None
        if state is None:
            r = ['clayrune-mail-enrollment', '1', ORIGIN, flow, 'challenge1', 'cd' * 32, str(NOW + 900_000),
                 self.install, str(self.expected), v['device_id'], v['device_public_key'], v['mail_key_id'],
                 v['mail_public_key'], hashlib.sha256(OWNER.encode()).hexdigest(), v['tunnel_uuid'],
                 v['route_origin'], '/api/agent-mail/ingress', v['manifest_sequence'], v['service_key_id'],
                 v['service_public_key_sha256']]
            state = self.flows[flow] = {'r': r, 'secret': value['flow_secret'], 'confirmed': False,
                                        'committed': False, 'value': v}
            self.mails += 1
        r = state['r']
        body = {'flow_id': flow, 'install_id': r[7], 'expected_generation': int(r[8]), 'transcript': r,
                'state': 'pending', 'code_sent': sent}
        return self._sign(self.begin_body(body), BEGIN, flow, headers)

    def _flow(self, value):
        state = self.flows.get(value['flow_id'])
        return state if state is not None and state['secret'] == value['flow_secret'] else None

    def _confirm(self, value, headers):
        state = self._flow(value)
        if state is None:
            return unsigned(401)
        if not state['confirmed'] and value['code'] == self.code:
            state['confirmed'] = True
        elif not state['confirmed']:
            self.wrong += 1
        body = {'flow_id': value['flow_id'], 'confirmed': state['confirmed'], 'state': 'pending'}
        return self._sign(body, CONFIRM, value['flow_id'], headers)

    def _complete(self, value, headers):
        state = self._flow(value)
        if state is None:
            return unsigned(401)
        raw = canonical(state['r'])
        if not (verify_sig(state['value']['device_public_key'], raw, value['device_signature'])
                and verify_sig(state['value']['mail_public_key'], raw, value['mail_signature'])):
            return unsigned(403)
        if not state['committed']:
            if 'token' not in value:
                return unsigned(403)            # E3: a tokenless complete needs a commit to replay
            if not state['confirmed']:
                return unsigned(409)
            state['committed'] = True
        elif 'token' in value:
            return unsigned(409)                # grant_conflict: a fresh token after the commit
        r = state['r']
        body = {'flow_id': value['flow_id'], 'install_id': r[7], 'generation': int(r[8]) + 1,
                'state': 'ownership_recorded_delivery_pending', 'active': False, 'route_verified': False,
                'ownership_expires_ms': self.lease or self.kit.now + 300_000}
        return self._sign(self.complete_body(body), COMPLETE, value['flow_id'], headers)

    def _grant(self, value):
        assert set(value) == {'r', 'device_signature', 'mail_signature'}
        if self.cp_status != 200:
            return unsigned(self.cp_status)
        self.grants += 1
        return {'status': 200, 'headers': {},
                'body': json.dumps({'grant': f'aaaa.bbbb.cc{self.grants:04d}'}).encode()}


class Kit:
    def __init__(self, tmp_path, *, fenced=True, config=None):
        self.now, self.master, self.device_fn = NOW, MASTER, lambda: DEV
        self.world = World(self)
        self.factory = fac.MailFactory(tmp_path / 'agent-mail', clock=self.clock, key=lambda: self.master, config={})
        self.store = self.factory.open_store(create=True)
        self.seal = IdentitySeal(self.factory.paths.identity, key=lambda: self.master)
        self.fences = LifecycleFences(self.store, enabled=fenced)
        self.remote = select_ceremony(CONFIG if config is None else config, store=self.store, seal=self.seal,
                                      fences=self.fences, transport=self.world, root_public_key=PUBLIC,
                                      service_origin=ORIGIN, device=lambda: self.device_fn(), clock=self.clock)
        self.core = Enrollment(self.store, self.seal, adapter=self.remote, material=lambda: (SEED, SECRET),
                               prerequisites=lambda: Prerequisites(True, True, True), install_id='install1',
                               root_public_key=PUBLIC, service_origin=ORIGIN, enabled=True,
                               fences=self.fences if fenced else None)

    def clock(self):
        return self.now / 1000

    def start(self):
        self.core.start(OWNER, *manifest(public=SVC_PUBLIC))
        return self.core

    def flow(self):
        with self.store._read() as db:
            return dict(db.execute('SELECT * FROM mail_enrollment_flow').fetchone())

    def ctx(self):
        row = self.flow()
        return row, self.seal.flow_secret(row['flow'], row['seal_identity'], row['seal_generation'], row['key_id'])

    def reviewed(self):
        self.start()
        self.core.confirm(self.core.review())
        self.world.calls.clear()
        return self.core

    def establish(self, generation):
        with self.store._tx() as db:
            db.execute('INSERT INTO registrations VALUES(?,?,0,?)', ('install1', generation, self.store.clock()))


@pytest.fixture
def kit(tmp_path):
    return Kit(tmp_path)


# --- flags: off is today's behaviour --------------------------------------------------------------------
@pytest.mark.parametrize('config', [
    {}, {'agent_mail_ceremony_enabled': True}, {'agent_mail_enrollment_enabled': True},
    {'agent_mail_ceremony_enabled': 1, 'agent_mail_enrollment_enabled': True, 'agent_mail_grant_origin': CP},
    {'agent_mail_ceremony_enabled': True, 'agent_mail_enrollment_enabled': 'true', 'agent_mail_grant_origin': CP},
    {'agent_mail_ceremony_enabled': True, 'agent_mail_enrollment_enabled': True},
    {'agent_mail_ceremony_enabled': True, 'agent_mail_enrollment_enabled': True, 'agent_mail_grant_origin': ''},
    {'agent_mail_ceremony_enabled': True, 'agent_mail_enrollment_enabled': True,
     'agent_mail_grant_origin': 'http://not-https.example.test'},
])
def test_select_ceremony_is_none_unless_both_flags_are_exactly_true_and_the_grant_origin_is_usable(tmp_path, config):
    assert Kit(tmp_path, config=config).remote is None


def test_factory_with_the_flag_off_keeps_the_unavailable_remote_and_builds_no_fence_table(tmp_path):
    config = {'agent_mail_enrollment_enabled': True, 'agent_mail_install_id': 'install1',
              'agent_mail_root_public_key': PUBLIC, 'agent_mail_service_origin': ORIGIN}
    factory = fac.MailFactory(tmp_path / 'm', clock=lambda: NOW / 1000, key=lambda: MASTER, config=config)
    factory.open_store(create=True)
    core = factory.enrollment()
    assert isinstance(core.adapter, UnavailableRemote) and core.fences is None
    with factory.require_store()._read() as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE '%fence%'").fetchone()


def test_factory_with_both_flags_builds_the_composite_over_the_injected_seams(tmp_path):
    config = {**CONFIG, 'agent_mail_install_id': 'install1', 'agent_mail_root_public_key': PUBLIC,
              'agent_mail_service_origin': ORIGIN}
    world = type('T', (), {'calls': [], 'post': lambda self, *a: self.calls.append(a)})()
    factory = fac.MailFactory(tmp_path / 'm', clock=lambda: NOW / 1000, key=lambda: MASTER, config=config,
                              ceremony_transport=world, ceremony_device=lambda: DEV)
    factory.open_store(create=True)
    core = factory.enrollment()
    assert isinstance(core.adapter, CompositeRemote) and core.fences is not None and core.fences.enabled
    assert core.adapter.ceremony.device() is DEV and world.calls == []
    # the lifecycle half is the unchanged 7d-2d client
    assert isinstance(core.adapter.lifecycle, ServiceClient)


def test_flag_off_never_imports_mc_remote():
    code = ("import sys, mc.agent_mail_factory, mc.agent_mail_ceremony_client, mc.agent_mail_ceremony_remote\n"
            "assert 'mc_remote' not in sys.modules, 'mc_remote was imported'\n")
    done = subprocess.run([sys.executable, '-c', code], cwd=ROOT_DIR, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_the_disabled_client_refuses_before_anything(kit):
    client = CeremonyClient(kit.store, kit.seal, fences=kit.fences, transport=kit.world, root_public_key=PUBLIC,
                            service_origin=ORIGIN, grant_origin=CP, device=lambda: DEV)
    kit.start()
    row, secret = kit.ctx()
    kit.world.calls.clear()
    for call in (lambda: client.begin(row, secret), lambda: client.verify(row, secret, 'a1' * 16)):
        with pytest.raises(CeremonyRefused, match='^ceremony_disabled$'):
            call()
    assert kit.world.calls == []


# --- device seam ----------------------------------------------------------------------------------------
def test_device_key_std_base64_converts_to_unpadded_base64url():
    raw = bytes([0xfb] * 31 + [0xff])           # std base64 contains '+' and '/'
    std = base64.b64encode(raw).decode()
    assert '+' in std and '/' in std
    assert std == '+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/v7+/8='
    assert device_key_b64url(std) == '-_v7-_v7-_v7-_v7-_v7-_v7-_v7-_v7-_v7-_v7-_8'


@pytest.mark.parametrize('bad', ['', 'not base64!', base64.urlsafe_b64encode(bytes([0xfb] * 32)).decode(),
                                 base64.b64encode(b'\x01' * 31).decode(), base64.b64encode(b'\x01' * 33).decode(),
                                 base64.b64encode(b'\x01' * 32).decode().rstrip('='), None])
def test_device_key_conversion_refuses_instead_of_repairing(bad):
    with pytest.raises(EnrollmentRefused, match=f'^{DEVICE_UNAVAILABLE}$'):
        device_key_b64url(bad)


@pytest.mark.parametrize('field,value', [('device_id', ''), ('device_id', 'has space'), ('tunnel_uuid', 'nope'),
                                         ('tunnel_uuid', TUNNEL.upper()), ('route_origin', 'http://x.example.test'),
                                         ('route_origin', ROUTE_ORIGIN + '/path'), ('device_public_key', 'short')])
def test_device_identity_validates_at_construction(field, value):
    fields = dict(device_id='device1', device_public_key=DEV.device_public_key, tunnel_uuid=TUNNEL,
                  route_origin=ROUTE_ORIGIN, _signer=DEVICE.sign)
    fields[field] = value
    with pytest.raises(EnrollmentRefused, match=f'^{DEVICE_UNAVAILABLE}$'):
        LocalDeviceIdentity(**fields)


def test_device_signer_failures_and_wrong_length_are_the_closed_code():
    short = LocalDeviceIdentity('d', DEV.device_public_key, TUNNEL, ROUTE_ORIGIN, lambda m: b'x' * 63)
    boom = LocalDeviceIdentity('d', DEV.device_public_key, TUNNEL, ROUTE_ORIGIN,
                               lambda m: (_ for _ in ()).throw(RuntimeError('keystore secret')))
    for identity in (short, boom):
        with pytest.raises(EnrollmentRefused) as exc:
            identity.sign(b'x')
        assert str(exc.value) == DEVICE_UNAVAILABLE
    assert DEV.sign(b'x') == DEVICE.sign(b'x')


def test_keystore_identity_without_mc_remote_data_is_the_closed_code(monkeypatch):
    class Stored:
        device_id, device_pub_b64, hostname = 'device1', base64.b64encode(bytes(range(32))).decode(), 'Dev.Example.Test'

    class Keys:
        tunnel = TUNNEL
        load_identity = staticmethod(lambda: Stored())
        load_tunnel_uuid = staticmethod(lambda: Keys.tunnel)
        sign = staticmethod(DEVICE.sign)

    package = types.ModuleType('mc_remote')
    package.device_keys = Keys           # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, 'mc_remote', package)
    identity = keystore_identity()
    assert (identity.device_id, identity.tunnel_uuid, identity.route_origin) == ('device1', TUNNEL,
                                                                               'https://dev.example.test')
    assert identity.device_public_key == b64(bytes(range(32)))
    Keys.tunnel = None                             # E1: no tunnel id persisted yet
    with pytest.raises(EnrollmentRefused, match=f'^{DEVICE_UNAVAILABLE}$'):
        keystore_identity()
    Keys.tunnel = TUNNEL
    Keys.load_identity = staticmethod(lambda: None)    # not enrolled
    with pytest.raises(EnrollmentRefused, match=f'^{DEVICE_UNAVAILABLE}$'):
        keystore_identity()


# --- begin: pre-checks, value, transcript ---------------------------------------------------------------
def test_begin_sends_exactly_the_twelve_fields_and_returns_what_resume_demands(kit):
    kit.start()
    call = kit.world.named(BEGIN)[0]
    value = call['value']['value']
    assert set(value) == {'flow_id', 'owner_address', 'device_id', 'device_public_key', 'mail_key_id',
                          'mail_public_key', 'tunnel_uuid', 'route_origin', 'manifest_sequence', 'service_key_id',
                          'service_public_key_sha256', 'fence_digest'}
    row, secret = kit.ctx()
    assert call['value']['flow_secret'] == secret.hex() and len(secret) == 32
    assert value['fence_digest'] == kit.fences.digest_for(row['flow']) and value['owner_address'] == OWNER
    assert (value['device_id'], value['tunnel_uuid'], value['route_origin'], value['mail_key_id'],
            value['mail_public_key']) == ('device1', TUNNEL, ROUTE_ORIGIN, row['key_id'], row['public_key'])
    assert value['device_public_key'] == DEV.device_public_key and value['manifest_sequence'] == '1'
    # the request carries no install key: only the reply-binding nonce and the content type
    assert set(call['headers']) == {'x-mail-nonce', 'Content-Type'}
    # begin's PendingIdentity is exactly what Enrollment.resume recorded
    assert (row['install'], row['generation'], row['assigned']) == ('install1', 1, 1) and row['stage'] == 'pending'
    assert kit.world.mails == 1


def test_without_lifecycle_fences_the_value_has_eleven_fields(tmp_path):
    kit = Kit(tmp_path, fenced=False)
    kit.start()
    value = kit.world.named(BEGIN)[0]['value']['value']
    assert len(value) == 11 and 'fence_digest' not in value


def test_begin_value_equals_the_transcript_the_worker_returns_for_it(kit):
    kit.start()
    sent = kit.world.named(BEGIN)[0]['value']['value']
    r = dict(zip(R_FIELDS, kit.world.flows[sent['flow_id']]['r']))
    for key in set(sent) - {'owner_address', 'fence_digest'}:
        assert r[key] == sent[key], key
    assert r['owner_address_sha256'] == hashlib.sha256(OWNER.encode()).hexdigest()
    assert r['service_origin'] == ORIGIN and len(R_FIELDS) == 20


@pytest.mark.parametrize('index', [0, 1, 2, 3, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19])
def test_a_mutated_transcript_field_is_refused_as_scope_mismatch(kit, index):
    def mutate(body):
        r = list(body['transcript'])
        r[index] = r[index][:-1] + ('0' if r[index][-1] != '0' else '1')
        return {**body, 'transcript': r}
    kit.world.begin_body = mutate
    with pytest.raises(EnrollmentRefused, match='^remote_begin_retryable$'):
        kit.start()
    kit.world.calls.clear()
    row, secret = kit.ctx()
    with pytest.raises(CeremonyRefused, match='^ceremony_scope_mismatch$'):
        kit.remote.ceremony.begin(row, secret)


@pytest.mark.parametrize('mutate', [
    lambda b: {**b, 'install_id': 'someone_else'},
    lambda b: {**b, 'expected_generation': 7},
    lambda b: {**b, 'expected_generation': True},
    lambda b: {**b, 'state': 'cancelled'},
    lambda b: {**b, 'flow_id': 'f' * 32},
    lambda b: {**b, 'code_sent': 'yes'},
    lambda b: {**b, 'extra': 1},
    lambda b: {k: v for k, v in b.items() if k != 'code_sent'},
    lambda b: {**b, 'transcript': b['transcript'][:-1]},
    lambda b: {**b, 'transcript': [*b['transcript'][:-1], 5]},
    lambda b: {**b, 'transcript': [*b['transcript'][:-1], 'café']},
    lambda b: {**b, 'transcript': 'not a list'},
])
def test_a_malformed_or_inconsistent_begin_body_is_scope_mismatch(kit, mutate):
    kit.start()
    kit.world.begin_body = mutate
    row, secret = kit.ctx()
    with pytest.raises(CeremonyRefused, match='^ceremony_scope_mismatch$'):
        kit.remote.ceremony.begin(row, secret)


def test_owner_hash_service_origin_and_route_path_are_checked_against_what_flask_knows(kit):
    kit.start()
    row, secret = kit.ctx()
    for index, value in ((13, hashlib.sha256(b'other@example.test').hexdigest()), (2, 'https://evil.example.test'),
                         (16, '/api/other')):
        kit.world.begin_body = lambda b, i=index, v=value: {**b, 'transcript': [*b['transcript'][:i], v,
                                                                                *b['transcript'][i + 1:]]}
        with pytest.raises(CeremonyRefused, match='^ceremony_scope_mismatch$'):
            kit.remote.ceremony.begin(row, secret)


@pytest.mark.parametrize('tweak', [
    {'signer': Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))},
    {'nonce': 'ee' * 16},
    {'path': '/v1/mail/enrollment/confirm'},
    {'install': 'install1'},                 # the real install id: the ceremony envelope names the FLOW
    {'generation': 2},
    {'key_id': 'other'},
    {'stamp': NOW - 10 * 60 * 1000},
    {'direction': 'worker-to-nobody'},
    {'version': '2'},
    {'drop': ('x-mail-signature',)},
])
def test_unsigned_or_mis_scoped_replies_are_refused_on_every_ceremony_route(kit, tweak):
    kit.world.tweak = tweak
    with pytest.raises(EnrollmentRefused, match='^remote_begin_retryable$'):
        kit.start()
    row, secret = kit.ctx()
    with pytest.raises(EnrollmentRefused, match='^remote_response_(unsigned|invalid)$'):
        kit.remote.ceremony.begin(row, secret)
    kit.world.tweak = {}
    kit.remote.ceremony.begin(row, secret)                       # same flow, honest reply: accepted
    kit.world.tweak = tweak
    kit.world.calls.clear()
    with pytest.raises(EnrollmentRefused, match='^remote_response_(unsigned|invalid)$'):
        kit.remote.ceremony.verify(row, secret, kit.world.code)
    # nothing past the first unverifiable reply: no CP call, no complete
    assert kit.world.calls[0]['path'] == CONFIRM
    assert not [c for c in kit.world.calls if c['path'] in (GRANT_PATH, COMPLETE)]


@pytest.mark.parametrize('status', [403, 503, 409, 429, 500])
def test_a_non_200_answer_never_surfaces_its_body_and_every_worker_code_collapses_to_not_acknowledged(kit, status):
    """Contract 2.6 preflight_refused (403) and preflight_unavailable (503) share their status with other
    Worker codes, a non-200 is never signed and HttpsTransport drops its body, so Flask can only say
    remote_not_acknowledged: the closed Flask set (3.6) has no finer code that would be honest."""
    kit.world.status = {'begin': status}
    with pytest.raises(EnrollmentRefused, match='^remote_begin_retryable$'):
        kit.start()
    row, secret = kit.ctx()
    with pytest.raises(EnrollmentRefused) as exc:
        kit.remote.ceremony.begin(row, secret)
    assert str(exc.value) == 'remote_not_acknowledged' and 'remote text' not in repr(exc.value.args)
    assert kit.flow()['assigned'] == 0 and kit.world.mails == 0


def test_a_stale_manifest_device_unavailable_and_a_bad_secret_send_nothing(kit):
    kit.start()
    row, secret = kit.ctx()
    kit.world.calls.clear()
    for bad in (b'', b'x' * 31, b'x' * 33, 'ab' * 32, None):
        with pytest.raises(CeremonyRefused, match='^ceremony_scope_mismatch$'):
            kit.remote.ceremony.begin(row, bad)
    kit.device_fn = lambda: (_ for _ in ()).throw(EnrollmentRefused(DEVICE_UNAVAILABLE))
    with pytest.raises(EnrollmentRefused, match=f'^{DEVICE_UNAVAILABLE}$'):
        kit.remote.ceremony.begin(row, secret)
    kit.device_fn = lambda: DEV
    assert kit.world.calls == []
    kit.now = NOW + 5 * 24 * 3600 * 1000          # past the manifest's expiry
    with pytest.raises(CeremonyRefused, match='^manifest_stale$'):
        kit.remote.ceremony.begin(row, secret)
    with pytest.raises(CeremonyRefused, match='^manifest_stale$'):
        kit.remote.ceremony.verify(row, secret, kit.world.code)
    assert kit.world.calls == []


def test_a_floor_that_no_longer_matches_the_persisted_manifest_is_stale(kit):
    kit.start()
    row, secret = kit.ctx()
    kit.world.calls.clear()
    row['manifest_digest'] = 'f' * 64
    with pytest.raises(CeremonyRefused, match='^manifest_stale$'):
        kit.remote.ceremony.begin(row, secret)
    assert kit.world.calls == []


def test_transport_failures_are_closed_codes_that_carry_no_exception_text(kit):
    kit.start()
    row, secret = kit.ctx()

    class Boom:
        def post(self, *a):
            raise RuntimeError('secret detail ' + secret.hex())

    kit.remote.ceremony.transport = Boom()
    with pytest.raises(CeremonyRefused) as exc:
        kit.remote.ceremony.begin(row, secret)
    assert str(exc.value) == 'remote_unreachable' and exc.value.__suppress_context__ and secret.hex() not in str(exc.value)
    kit.remote.ceremony.transport = type('N', (), {'post': lambda self, *a: 'not a mapping'})()
    with pytest.raises(CeremonyRefused, match='^remote_response_invalid$'):
        kit.remote.ceremony.begin(row, secret)


# --- generation mapping ---------------------------------------------------------------------------------
def test_a_fresh_install_maps_expected_zero_to_generation_one(kit):
    kit.world.install, kit.world.expected = 'install1', 0
    kit.start()
    row, secret = kit.ctx()
    pending = kit.remote.ceremony.begin(row, secret)
    assert pending == PendingIdentity(row['flow'], 'install1', 1, OWNER, row['public_key'],
                                      json.loads(row['candidate'])['fingerprint'])
    assert kit.flow()['generation'] == 1


def test_a_worker_chosen_install_id_is_adopted_by_resume(kit):
    kit.world.install = 'worker_install_9'
    kit.start()
    row = kit.flow()
    assert (row['install'], row['generation'], row['assigned']) == ('worker_install_9', 1, 1)


def test_a_previously_established_install_maps_expected_g_to_g_plus_one(tmp_path):
    kit = Kit(tmp_path)
    kit.establish(3)                               # local start seeds generation 3 for a known registration
    kit.world.install, kit.world.expected = 'install1', 2
    kit.start()
    row, secret = kit.ctx()
    assert row['generation'] == 3
    assert kit.remote.ceremony.begin(row, secret).generation == 3
    kit.core.confirm(kit.core.review())
    kit.core.verify(kit.world.code)
    assert kit.flow()['stage'] == 'delivery_pending'
    assert kit.world.named(COMPLETE)[-1]['value'].get('token') is not None
    with kit.store._read() as db:
        assert db.execute('SELECT generation FROM registrations WHERE registration_id=?',
                          ('install1',)).fetchone()[0] == 3


def test_a_begin_that_would_move_the_generation_is_refused_by_resume(tmp_path):
    kit = Kit(tmp_path)
    kit.establish(3)
    kit.world.install, kit.world.expected = 'install1', 0        # would lower the fence 3 -> 1
    with pytest.raises(EnrollmentRefused, match='^pending_scope_mismatch$'):
        kit.start()


# --- verify: local pre-checks ---------------------------------------------------------------------------
@pytest.mark.parametrize('code', ['', ' ', 'abc', 'a1' * 15, 'a1' * 17, 'g1' * 16, 'a1-' * 10 + 'a1',
                                  'A1' * 16 + 'z', '0x' + 'a1' * 15, None, 12345, b'a1' * 16])
def test_a_malformed_code_is_refused_locally_with_no_send_and_no_strike(kit, code):
    kit.reviewed()
    row, secret = kit.ctx()
    with pytest.raises(CeremonyRefused, match='^invalid_code$'):
        kit.remote.ceremony.verify(row, secret, code)
    assert kit.world.calls == [] and kit.world.wrong == 0


def test_a_code_typed_with_spaces_and_capitals_is_normalised_before_it_is_sent(kit):
    kit.reviewed()
    row, secret = kit.ctx()
    kit.remote.ceremony.verify(row, secret, '  ' + kit.world.code.upper() + '\n')
    assert kit.world.named(CONFIRM)[0]['value']['code'] == kit.world.code and kit.world.wrong == 0


def test_a_wrong_code_is_rejected_without_a_cp_call_and_is_never_retried(kit):
    kit.reviewed()
    row, secret = kit.ctx()
    with pytest.raises(CeremonyRefused, match='^code_rejected$'):
        kit.remote.ceremony.verify(row, secret, 'b2' * 16)
    assert [c['path'] for c in kit.world.calls] == [CONFIRM] and kit.world.wrong == 1
    assert kit.world.grants == 0
    # through Enrollment the human sees the existing retryable code and the stage does not move
    with pytest.raises(EnrollmentRefused, match='^remote_verify_retryable$'):
        kit.core.verify('c3' * 16)
    assert kit.flow()['stage'] == 'pending' and kit.world.wrong == 2


# --- verify: signing, grant, complete -------------------------------------------------------------------
def test_vault_locked_after_confirm_sends_nothing_to_the_cp_or_to_complete(kit):
    kit.reviewed()
    row, secret = kit.ctx()
    kit.master = None
    with pytest.raises(EnrollmentRefused, match='^vault_locked_or_unconfigured$'):
        kit.remote.ceremony.verify(row, secret, kit.world.code)
    assert [c['path'] for c in kit.world.calls] == [CONFIRM, BEGIN]
    assert kit.world.grants == 0


def test_the_device_signer_failing_stops_before_the_cp(kit):
    kit.reviewed()
    row, secret = kit.ctx()
    kit.device_fn = lambda: LocalDeviceIdentity('device1', DEV.device_public_key, TUNNEL, ROUTE_ORIGIN,
                                                lambda m: b'short')
    with pytest.raises(EnrollmentRefused, match=f'^{DEVICE_UNAVAILABLE}$'):
        kit.remote.ceremony.verify(row, secret, kit.world.code)
    assert kit.world.grants == 0 and kit.world.named(COMPLETE) == []


def test_both_proofs_sign_the_canonical_transcript_and_the_grant_request_carries_only_r_and_proofs(kit):
    kit.reviewed()
    row, secret = kit.ctx()
    kit.remote.ceremony.verify(row, secret, kit.world.code)
    r = kit.world.flows[row['flow']]['r']
    grant = next(c for c in kit.world.calls if c['path'] == GRANT_PATH)
    assert grant['origin'] == CP and grant['value']['r'] == r and set(grant['headers']) == {'Content-Type'}
    assert verify_sig(DEV.device_public_key, canonical(r), grant['value']['device_signature'])
    assert verify_sig(row['public_key'], canonical(r), grant['value']['mail_signature'])
    # the flow secret and the code never go to the CP
    assert secret.hex() not in json.dumps(grant['value']) and kit.world.code not in json.dumps(grant['value'])


def test_complete_asks_tokenless_first_then_mints_a_grant_and_presents_it(kit):
    kit.reviewed()
    row, secret = kit.ctx()
    kit.remote.ceremony.verify(row, secret, kit.world.code)
    completes = kit.world.named(COMPLETE)
    assert ['token' in c['value'] for c in completes] == [False, True]
    assert completes[1]['value']['token'] == 'aaaa.bbbb.cc0001' and kit.world.grants == 1


def test_a_cp_refusal_sends_no_grant_to_the_worker_and_never_surfaces_the_cp_code(kit):
    kit.reviewed()
    row, secret = kit.ctx()
    kit.world.cp_status = 403
    with pytest.raises(CeremonyRefused) as exc:
        kit.remote.ceremony.verify(row, secret, kit.world.code)
    assert str(exc.value) == 'grant_refused' and 'remote text' not in repr(exc.value.args)
    # the only complete is the grant-less E3 probe; nothing carrying a token reached the Worker
    assert ['token' in c['value'] for c in kit.world.named(COMPLETE)] == [False]
    kit.world.cp_status = 200


@pytest.mark.parametrize('body', [b'', b'not json', b'[]', b'{}', b'{"grant":5}', b'{"grant":"a.b"}',
                                  b'{"grant":"a.b.c","extra":1}', b'{"grant":"a b.c.d"}', b'x' * 9000,
                                  b'\xff\xfe'])
def test_a_malformed_cp_answer_is_refused_before_the_worker_sees_a_token(kit, body):
    kit.reviewed()
    row, secret = kit.ctx()
    kit.world._grant = lambda value: {'status': 200, 'headers': {}, 'body': body}
    with pytest.raises(CeremonyRefused, match='^remote_response_invalid$'):
        kit.remote.ceremony.verify(row, secret, kit.world.code)
    assert ['token' in c['value'] for c in kit.world.named(COMPLETE)] == [False]


def test_receipt_scope_and_types_are_checked(kit):
    kit.reviewed()
    row, secret = kit.ctx()
    for mutate in (lambda b: {**b, 'install_id': 'x'}, lambda b: {**b, 'generation': 2},
                   lambda b: {**b, 'generation': True}, lambda b: {**b, 'active': True},
                   lambda b: {**b, 'route_verified': True}, lambda b: {**b, 'state': 'active'},
                   lambda b: {**b, 'ownership_expires_ms': 'soon'}, lambda b: {**b, 'extra': 1},
                   lambda b: {**b, 'flow_id': 'f' * 32}):
        kit.world.complete_body = mutate
        with pytest.raises(EnrollmentRefused, match='^ceremony_scope_mismatch$'):
            kit.remote.ceremony.verify(row, secret, kit.world.code)
        kit.world.flows[row['flow']]['committed'] = False      # let the next case commit afresh


@pytest.mark.parametrize('offset', [-1, 0, 300_001, 10 * 60 * 1000])
def test_receipt_expiry_outside_the_lease_bound_is_refused_by_enrollment(kit, offset):
    kit.reviewed()
    kit.world.lease = NOW + offset
    with pytest.raises(EnrollmentRefused, match='^ownership_expired$'):
        kit.core.verify(kit.world.code)
    assert kit.flow()['stage'] == 'pending' and kit.flow()['ownership'] == 0


def test_receipt_expiry_at_the_lease_bound_is_accepted(kit):
    kit.reviewed()
    kit.world.lease = NOW + 300_000
    kit.core.verify(kit.world.code)
    assert kit.flow()['ownership_expires_ms'] == NOW + 300_000


# --- end to end -----------------------------------------------------------------------------------------
def test_start_resume_confirm_verify_end_to_end_reaches_delivery_pending(kit):
    kit.start()
    row = kit.flow()
    assert (row['stage'], row['assigned']) == ('pending', 1)
    kit.core.confirm(kit.core.review())
    status = kit.core.verify(kit.world.code)
    row = kit.flow()
    assert status['stage'] == 'delivery_pending' and row['stage'] == 'delivery_pending'
    assert row['ownership'] == 1 and row['ownership_expires_ms'] == NOW + 300_000
    # order on the wire: begin, confirm, begin (R rebuild), tokenless complete, grant, complete
    assert [c['path'] for c in kit.world.calls] == [BEGIN, CONFIRM, BEGIN, COMPLETE, GRANT_PATH, COMPLETE]
    assert kit.world.mails == 1 and kit.world.grants == 1


def test_enrollment_is_not_asked_to_trust_the_adapter_beyond_its_own_gate(kit):
    kit.reviewed()
    receipt = kit.remote.ceremony.verify(*kit.ctx(), kit.world.code)
    assert isinstance(receipt, OwnershipReceipt) and receipt.expires_at_ms == NOW + 300_000
    assert receipt.install == kit.flow()['install'] and receipt.generation == kit.flow()['generation']


# --- crash matrix (contract 4) --------------------------------------------------------------------------
def test_begin_committed_reply_lost_resume_returns_the_same_transcript_and_no_second_mail(kit):
    kit.world.drop_after = {'begin': 1}
    with pytest.raises(EnrollmentRefused, match='^remote_begin_retryable$'):
        kit.start()
    assert kit.flow()['remote_started'] == 1 and kit.flow()['assigned'] == 0 and kit.world.mails == 1
    kit.core.resume()
    assert kit.flow()['assigned'] == 1 and kit.world.mails == 1
    assert len(kit.world.named(BEGIN)) == 2


def test_begin_request_lost_resume_resends(kit):
    kit.world.drop_before = {'begin': 1}
    with pytest.raises(EnrollmentRefused, match='^remote_begin_retryable$'):
        kit.start()
    assert kit.world.mails == 0
    kit.core.resume()
    assert kit.flow()['assigned'] == 1 and kit.world.mails == 1


def test_confirm_reply_lost_verify_again_succeeds_without_a_second_strike(kit):
    kit.reviewed()
    kit.world.drop_after = {'confirm': 1}
    with pytest.raises(EnrollmentRefused, match='^remote_verify_retryable$'):
        kit.core.verify(kit.world.code)
    assert kit.flow()['stage'] == 'pending'
    kit.core.verify(kit.world.code)
    assert kit.flow()['stage'] == 'delivery_pending' and kit.world.wrong == 0


def test_cp_grant_minted_but_complete_never_reached_the_worker_a_new_grant_is_minted(kit):
    kit.reviewed()
    # tokenless probe goes through; the token-bearing complete is the one that is lost
    original = kit.world.post
    state = {'lost': False}

    def post(origin, path, headers, body):
        if path == COMPLETE and 'token' in json.loads(body) and not state['lost']:
            state['lost'] = True
            kit.world.calls.append({'origin': origin, 'path': path, 'headers': dict(headers),
                                    'value': json.loads(body)})
            raise OSError('request lost')
        return original(origin, path, headers, body)
    kit.world.post = post
    with pytest.raises(EnrollmentRefused, match='^remote_verify_retryable$'):
        kit.core.verify(kit.world.code)
    assert kit.world.grants == 1 and kit.flow()['stage'] == 'pending'
    kit.core.verify(kit.world.code)
    assert kit.world.grants == 2 and kit.flow()['stage'] == 'delivery_pending'


def test_complete_committed_reply_lost_the_retry_is_tokenless_and_replays_the_commit(kit):
    kit.reviewed()
    original = kit.world.post
    state = {'lost': False}

    def post(origin, path, headers, body):
        reply = original(origin, path, headers, body)
        if path == COMPLETE and 'token' in json.loads(body) and not state['lost']:
            state['lost'] = True
            raise OSError('reply lost')
        return reply
    kit.world.post = post
    with pytest.raises(EnrollmentRefused, match='^remote_verify_retryable$'):
        kit.core.verify(kit.world.code)
    assert kit.world.flows[kit.flow()['flow']]['committed'] and kit.flow()['stage'] == 'pending'
    kit.world.calls.clear()
    kit.core.verify(kit.world.code)
    assert kit.flow()['stage'] == 'delivery_pending' and kit.world.grants == 1       # no second grant was minted
    assert ['token' in c['value'] for c in kit.world.named(COMPLETE)] == [False]   # tokenless, answered by the replay


def test_flask_crash_after_the_receipt_verify_again_is_replay_safe(kit):
    kit.reviewed()
    first = kit.remote.ceremony.verify(*kit.ctx(), kit.world.code)       # receipt obtained, never written locally
    assert kit.flow()['stage'] == 'pending'
    kit.core.verify(kit.world.code)                                       # a later verify replays the commit
    assert kit.flow()['stage'] == 'delivery_pending' and kit.world.grants == 1
    assert first.expires_at_ms == kit.flow()['ownership_expires_ms']


# --- E6: Revocations.retry on a Worker `assignment: null` ------------------------------------------------
class Cancels:
    def __init__(self, answer):
        self.answer, self.revokes = answer, []

    def cancel_flow(self, flow):
        return self.answer

    def revoke(self, install, generation):
        self.revokes.append((install, generation))


def revocations(tmp_path, answer, *, assigned):
    store = MailStore(tmp_path / 'm.sqlite3', clock=lambda: NOW / 1000)
    init_enrollment(store)
    adapter = Cancels(answer)
    with store._tx() as db:
        Revocations.queue(db, {'flow': 'f' * 32, 'install': 'install1', 'generation': 1, 'assigned': assigned})
    return store, adapter, Revocations(store, adapter)


def row_of(store):
    with store._read() as db:
        return dict(db.execute('SELECT * FROM mail_enrollment_revocations').fetchone())


def test_a_null_assignment_resolves_a_row_that_had_an_assigned_identity_with_no_remote_revoke(tmp_path):
    store, adapter, revs = revocations(tmp_path, None, assigned=1)
    assert row_of(store)['install'] == 'install1'
    revs.retry()
    row = row_of(store)
    assert (row['resolved'], row['done'], row['install'], row['generation']) == (1, 1, None, None)
    assert adapter.revokes == []


def test_a_non_null_assignment_must_still_equal_the_queued_one(tmp_path):
    store, adapter, revs = revocations(tmp_path, CancelledAssignment('wrong_remote', 1), assigned=1)
    with pytest.raises(EnrollmentRefused, match='^remote_cancel_scope_mismatch$'):
        revs.retry()
    assert row_of(store)['resolved'] == 0 and adapter.revokes == []


def test_a_non_null_assignment_equal_to_the_queued_one_is_revoked_as_before(tmp_path):
    store, adapter, revs = revocations(tmp_path, CancelledAssignment('install1', 1), assigned=1)
    revs.retry()
    assert adapter.revokes == [('install1', 1)] and row_of(store)['done'] == 1


def test_an_abandoned_ceremony_flow_is_cancelled_and_resolved_through_the_real_composite(kit):
    """begin ran (an identity was assigned), the human walked away, the Worker has no commit: disable must
    finish revocation instead of wedging on `remote_cancel_scope_mismatch` (the 0.3 third trap)."""
    kit.start()
    assert kit.flow()['assigned'] == 1
    composite = kit.remote
    composite.lifecycle = Cancels(None)               # the Worker's cancel answers `assignment: null`
    kit.core.revocations = Revocations(kit.store, composite)
    kit.core.disable()
    kit.core.retry_revocations()
    assert kit.core.status()['revocations_pending'] == 0 and composite.lifecycle.revokes == []
