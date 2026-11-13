"""Fake-only pull service HTTP: synthetic trust/keys, no network or live mail."""
from dataclasses import replace
import hashlib
import io
import json
import urllib.error
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import pytest

from mc.agent_mail_enrollment_keys import IdentitySeal, SealReference
from mc.agent_mail_service_client import (FENCE, STATUS, ROUTES, RESPONSE_LIMIT, ServiceClient,
    ServiceRefused, HttpsTransport, verify_pull_response, _NoRedirect)
from mc.agent_mail_signing import unbase64url
from test_agent_mail_pull_receiver import Harness, NOW, INSTALL, FLOW, MANIFEST, ORIGIN, array, compact, digest, encoded


class World(Harness):
    def __init__(self, path):
        super().__init__(path)
        self.master = b'k' * 32
        self.seal = IdentitySeal(path / 'seals', key=lambda: self.master)
        self.ref = SealReference(FLOW, INSTALL, 1, 'key1')
        self.public = self.seal.generate_pull(self.ref, authorize=lambda: True, expected_public_key=None)
        self.calls = []
        self.change = lambda status, h, raw: (status, h, raw)
        self.after = lambda: None
        self.review = lambda: (self.pin, MANIFEST)
        self.client = self.make()

    def make(self, **kw):
        return ServiceClient(self.store, self.seal, transport=self.http, service_origin=ORIGIN,
            install_id=INSTALL, reviewed_service=lambda: self.review(), clock=lambda: self.now / 1000, **kw)

    def status_value(self):
        return dict(install_id=INSTALL, generation=1, owner_generation=1, state='active',
            address='public@example.test', owner='owner@example.test', key_id='key1', queue_count=0,
            queue_bytes=0, oldest_received_ms=None, oldest_expires_ms=None, invalid_message_id_holds=0,
            current_epoch=1, owner_change=None, last_notice_state=None, reason='')

    def proof(self, path, h, raw):
        a = array('clayrune-agent-mail-pull-http', '1', 'install-to-pull', ORIGIN, 'POST', path,
            'key1', INSTALL, h['x-mail-generation'], h['x-mail-owner-generation'], h['x-mail-timestamp'],
            h['x-mail-nonce'], digest(raw))
        Ed25519PublicKey.from_public_bytes(unbase64url(self.public, 32)).verify(
            unbase64url(h['x-mail-signature'], 64), a)

    def response(self, path, h, request, value, status=200, **updates):
        raw = compact(value)
        fence = path == FENCE
        gen = str(request['generation']) if fence else h['x-mail-generation']
        owner = '0' if fence else h['x-mail-owner-generation']
        nonce = request['nonce'] if fence else h['x-mail-nonce']
        hdr = {'content-type': 'application/json', 'x-mail-version': '2', 'x-mail-direction': 'pull-to-install',
            'x-mail-key-id': self.pin.key_id, 'x-mail-install-id': INSTALL, 'x-mail-generation': gen,
            'x-mail-owner-generation': owner, 'x-mail-timestamp': str(self.now), 'x-mail-nonce': nonce,
            'x-mail-digest': digest(raw)}
        hdr.update(updates)
        hdr['x-mail-signature'] = encoded(self.private.sign(array('clayrune-agent-mail-pull-http', '1',
            'pull-to-install', ORIGIN, 'POST', path, self.pin.key_id, INSTALL, gen, owner,
            self.now, nonce, status, digest(raw))))
        return status, hdr, raw

    def http(self, method, path, h, raw):
        assert method == 'POST' and path in ROUTES
        request = json.loads(raw)
        self.calls.append((path, dict(h), request, raw))
        if path == FENCE:
            assert not any(k.startswith('x-mail-') for k in h)
            assert set(request) == {'op_id', 'install_id', 'generation', 'flow_id', 'fence_token', 'mode', 'nonce', 'timestamp'}
            rid = request['op_id']
            value = dict(install_id=INSTALL, generation=request['generation'], state='fenced', fence_receipt='f' * 32)
        else:
            self.proof(path, h, raw)
            rid = request.get('op_id', h['x-mail-nonce'])
            value = self.status_value() if path == STATUS else {'sentinel': 'route owner validates this'}
            if path == '/v1/pull/send':
                value = dict(intent_id=request.get('intent_id'), thread_id=request.get('thread_id'),
                             message_id='dedupe@example.test', state='accepted', provider_id='provider-id')
        common = dict(v=1, code='fenced' if path == FENCE else 'accepted' if path.endswith('/send') else 'ok', request_id=rid)
        response = self.change(*self.response(path, h, request, dict(common, **value)))
        self.after()
        return response

    def fence_row(self, mode='revoke', generation=1, op='b' * 32, flow=FLOW):
        with self.store._tx() as db:
            db.execute("INSERT INTO mail_pull_fences VALUES(?,?,?,?,?,?,'pending',NULL,?,?)",
                (op, INSTALL, generation, flow, 'd' * 64, mode, self.now, self.now))
        return op

    def request(self, path=STATUS, value=None):
        raw = compact({} if value is None else value)
        nonce = '9' * 32
        h = {'x-mail-version': '2', 'x-mail-direction': 'install-to-pull', 'x-mail-key-id': 'key1',
             'x-mail-install-id': INSTALL, 'x-mail-generation': '1', 'x-mail-owner-generation': '1',
             'x-mail-timestamp': str(self.now), 'x-mail-nonce': nonce, 'x-mail-digest': digest(raw),
             'content-type': 'application/json'}
        h['x-mail-signature'] = self.seal.sign_pull(self.ref, array('clayrune-agent-mail-pull-http', '1',
            'install-to-pull', ORIGIN, 'POST', path, 'key1', INSTALL, 1, 1, self.now, nonce, digest(raw)))
        return h, raw


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


def test_status_uses_pull_exact_path_sealed_signature_and_no_evidence(w):
    before = w.rows('mail_pull_accounts')
    first = w.client.status()
    second = w.client.status()
    assert first['state'] == 'active' and first['request_id'] != second['request_id']
    assert w.calls[0][0] == STATUS and w.calls[0][2] == {}
    assert w.client.cached_evidence() is None
    assert w.rows('mail_pull_accounts') == before
    assert not w.rows('mail_pull_status')  # Projection belongs to future lifecycle unit.


@pytest.mark.parametrize('path', sorted(ROUTES - {FENCE}))
def test_callable_is_a_verified_signup_transport_and_preserves_exact_raw_bytes(w, path):
    value = {} if path.endswith('/status') else {'op_id': '5' * 32}
    if path == '/v1/pull/send':
        value.update(intent_id='intent1', thread_id='thread1')
    h, raw = w.request(path, value)
    response = w.client('POST', path, h, raw)
    assert response[0] == 200 and w.calls[-1][3] == raw


@pytest.mark.parametrize('field,value', [
    ('x-mail-version', '1'), ('x-mail-direction', 'service-to-install-response'),
    ('x-mail-install-id', '0' * 32), ('x-mail-generation', '2'), ('x-mail-owner-generation', '0'),
    ('x-mail-key-id', 'other'), ('x-mail-nonce', '0' * 32), ('x-mail-digest', '0' * 64),
    ('x-mail-timestamp', '0'), ('x-mail-signature', 'bad'), ('content-type', 'text/plain'),
    ('content-encoding', 'gzip'), ('X-Mail-Nonce', 'duplicated'), ('oversized', 'x' * 8192),
])
def test_response_header_forgery_refuses_before_use(w, field, value):
    w.change = lambda s, h, b: (s, dict(h, **{field: value}), b)
    with pytest.raises(ServiceRefused, match='^response_unverified$'):
        w.client.status()
    assert w.client.cached_evidence() is None


@pytest.mark.parametrize('raw', [b'[]', b'null', b'{"v":1,"v":1}', b'{"v":NaN}', b'{"v":1.0}',
    b'{"v":true}', b'{"v":-0}', b'{"v":9007199254740992}', b'{"v":"\\ud800"}', b'\xff', b'{' + b'x' * RESPONSE_LIMIT], ids=[str(n) for n in range(11)])
def test_signed_bad_json_is_not_evidence(w, raw):
    def update(s, h, original):
        h['x-mail-digest'] = digest(raw)
        h['x-mail-signature'] = encoded(w.private.sign(array('clayrune-agent-mail-pull-http', '1',
            'pull-to-install', ORIGIN, 'POST', STATUS, w.pin.key_id, INSTALL, 1, 1,
            w.now, h['x-mail-nonce'], s, digest(raw))))
        return s, h, raw
    w.change = update
    with pytest.raises(ServiceRefused, match='^response_unverified$'):
        w.client.status()


@pytest.mark.parametrize('field,value', [('state', 'invented'), ('extra', 'execute this'), ('generation', 2),
    ('owner_generation', True), ('queue_count', -1), ('queue_count', 101), ('queue_bytes', 2097153),
    ('owner_change', {'change_id': 'b' * 32}), ('last_notice_state', 'invented'),
    ('reason', 'run remote command'), ('owner', 'bad\r\n@example.test')])
def test_signed_status_closed_shape(w, field, value):
    original = w.status_value
    w.status_value = lambda: dict(original(), **{field: value})
    with pytest.raises(ServiceRefused, match='^response_unverified$'):
        w.client.status()


@pytest.mark.parametrize('signed_path', ['/v1/pull/ticket', '/v1/mail/lifecycle/status'])
def test_signature_binds_exact_request_path(w, signed_path):
    def wrong(s, h, raw):
        h['x-mail-signature'] = encoded(w.private.sign(array('clayrune-agent-mail-pull-http', '1',
            'pull-to-install', ORIGIN, 'POST', signed_path, w.pin.key_id, INSTALL, 1, 1,
            w.now, h['x-mail-nonce'], s, digest(raw))))
        return s, h, raw
    w.change = wrong
    with pytest.raises(ServiceRefused, match='^response_unverified$'):
        w.client.status()


def test_signature_binds_status(w):
    w.change = lambda s, h, raw: (409, h, raw)
    with pytest.raises(ServiceRefused, match='^response_unverified$'):
        w.client.status()


def test_nonce_and_operation_response_replay_refuses(w):
    w.client.status()
    path, h, req, _ = w.calls[-1]
    old = w.response(path, h, req, dict(v=1, code='ok', request_id=h['x-mail-nonce'], **w.status_value()))
    w.change = lambda *args: old
    with pytest.raises(ServiceRefused, match='^response_unverified$'):
        w.client.status()


def test_signed_error_is_closed_failure_only(w):
    def failed(s, h, raw):
        path, request_h, req, _ = w.calls[-1]
        return w.response(path, request_h, req, dict(v=1, code='registration_frozen',
            request_id=request_h['x-mail-nonce']), 409)
    w.change = failed
    with pytest.raises(ServiceRefused, match='^registration_frozen$'):
        w.client.status()
    assert w.rows('mail_pull_accounts')[0]['state'] == 'active'
    assert w.client.cached_evidence() is None


def test_unsigned_401_is_not_a_verified_error_or_capability_fallback(w):
    w.fence_row()
    w.change = lambda *_: (401, {}, b'{"v":1,"code":"unauthorized"}')
    with pytest.raises(ServiceRefused, match='^response_unverified$'):
        w.client.revoke(INSTALL, 1)
    assert len(w.calls) == 1 and w.rows('mail_pull_fences')[0]['state'] == 'pending'


@pytest.mark.parametrize('change', ['expired', 'pin', 'floor', 'exception'])
def test_review_pin_and_floor_rechecked_after_request(w, change):
    def changed():
        if change == 'exception':
            def fail():
                raise OSError('private network exception')
            w.review = fail
        elif change == 'floor':
            w.review = lambda: (w.pin, '0' * 64)
        else:
            w.pin = replace(w.pin, **({'expires_at_ms': NOW} if change == 'expired' else {'key_id': 'service2'}))
    w.after = changed
    with pytest.raises(ServiceRefused, match='^service_key_changed$'):
        w.client.status()


def test_local_generation_changed_during_status_is_not_evidence(w):
    def changed():
        with w.store._tx() as db:
            db.execute("UPDATE mail_pull_accounts SET local_enabled=0,state='disabled'")
    w.after = changed
    with pytest.raises(ServiceRefused, match='^stale_generation$'):
        w.client.status()


def test_fence_works_with_locked_vault_stable_operation_and_fresh_nonces(w):
    op = w.fence_row()
    w.master = None
    first = w.client.fence(op)
    w.now += 1000
    second = w.client.fence(op)
    assert first == second
    a, b = (c[2] for c in w.calls)
    assert a['op_id'] == b['op_id'] == op and a['nonce'] != b['nonce'] and a['timestamp'] != b['timestamp']
    assert w.rows('mail_pull_fences')[0]['state'] == 'pending'
    with pytest.raises(ValueError, match='vault_locked'):
        w.client.status()


def test_cancel_and_revoke_target_exact_durable_scope_including_old_generations(w):
    op = w.fence_row(mode='cancel')
    assignment = w.client.cancel_flow(FLOW)
    assert (assignment.install, assignment.generation) == (INSTALL, 1)
    w.fence_row(op='8' * 32, generation=2)
    w.client.revoke(INSTALL, 2)
    assert [c[2]['op_id'] for c in w.calls] == [op, '8' * 32]
    assert w.calls[-1][2]['generation'] == 2


def test_fence_intent_changed_during_request_refuses_ack(w):
    op = w.fence_row()
    def changed():
        with w.store._tx() as db:
            db.execute('UPDATE mail_pull_fences SET fence_token=?', ('0' * 64,))
    w.after = changed
    with pytest.raises(ServiceRefused, match='^stale_generation$'):
        w.client.fence(op)


@pytest.mark.parametrize('method', ['renew', 'proof_arm', 'activate', 'begin', 'verify', 'status', 'cancel_flow', 'revoke'])
def test_legacy_constructs_but_never_hits_old_routes(w, method):
    c = ServiceClient(w.store, w.seal, transport=w.http, service_origin=ORIGIN, root_public_key='old')
    args = {'activate': ('a' * 64,), 'begin': ({}, b''), 'verify': ({}, b'', 'code'),
            'cancel_flow': (FLOW,), 'revoke': (INSTALL, 1)}.get(method, ())
    with pytest.raises((ServiceRefused, __import__('mc.agent_mail_remote', fromlist=['RemoteUnavailable']).RemoteUnavailable)):
        getattr(c, method)(*args)
    assert not w.calls and c.cached_evidence() is None


@pytest.mark.parametrize('path', [STATUS + '?', STATUS + '?x=1', '/v1/pull/socket', '//evil.test/x',
    ORIGIN + STATUS, '/v1/mail/lifecycle/status', '/v1/pull/../status', FENCE])
def test_callable_rejects_unknown_paths_without_network(w, path):
    h, raw = w.request()
    with pytest.raises(ServiceRefused, match='^invalid_request$'):
        w.client('POST', path, h, raw)
    assert not w.calls


@pytest.mark.parametrize('header,value', [('x-mail-generation', '01'), ('x-mail-owner-generation', '0'),
    ('x-mail-timestamp', str(NOW - 300001)), ('x-mail-nonce', 'no'), ('x-mail-digest', '0' * 64),
    ('x-mail-version', '1'), ('cookie', 'session'), ('authorization', 'ticket')])
def test_callable_refuses_malformed_request_before_network(w, header, value):
    h, raw = w.request()
    h[header] = value
    with pytest.raises(ServiceRefused, match='^invalid_request$'):
        w.client('POST', STATUS, h, raw)
    assert not w.calls


class Reply:
    status = 200
    headers = {'content-type': 'application/json'}
    def __init__(self, body=b'{}', url=ORIGIN + STATUS):
        self.raw, self.url, self.reads = body, url, []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.closed = True
    def geturl(self):
        return self.url
    def read(self, n):
        self.reads.append(n)
        return self.raw[:n]


class Opener:
    def __init__(self, reply):
        self.reply, self.calls = reply, []
    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return self.reply


def test_bound_https_transport_is_callable_bounded_and_never_retargets():
    reply = Reply()
    op = Opener(reply)
    t = HttpsTransport(origin=ORIGIN, opener=op)
    assert t('POST', STATUS, {}, b'{}') == (200, {'content-type': 'application/json'}, b'{}')
    req, timeout = op.calls[0]
    assert req.full_url == ORIGIN + STATUS and req.get_method() == 'POST' and timeout == 10
    assert req.get_header('Accept-encoding') == 'identity'
    assert reply.reads == [32769] and reply.closed
    with pytest.raises(ServiceRefused):
        t.post('https://other.example.test', STATUS, {}, b'{}')
    assert len(op.calls) == 1


@pytest.mark.parametrize('timeout', [0, -1, 11, float('nan'), float('inf'), True, '10'])
def test_timeout_is_finite_and_bounded(timeout):
    with pytest.raises(ServiceRefused, match='^invalid_timeout$'):
        HttpsTransport(timeout_s=timeout)


@pytest.mark.parametrize('origin', ['http://mail.example.test', ORIGIN + '/', ORIGIN + ':443',
    ORIGIN + '?', 'https://user@mail.example.test', 'https://127.0.0.1', 'https://localhost', 'https://MAIL.example.test'])
def test_origin_is_exact_https_dns_only(origin):
    with pytest.raises(ServiceRefused, match='^service_origin_invalid$'):
        HttpsTransport(origin=origin)


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
def test_all_redirects_refused_even_with_injected_opener(status):
    reply = Reply()
    reply.status = status
    with pytest.raises(ServiceRefused, match='^response_unverified$'):
        HttpsTransport(origin=ORIGIN, opener=Opener(reply))('POST', STATUS, {}, b'{}')
    assert _NoRedirect().redirect_request(None, None, status, '', {}, ORIGIN) is None


def test_http_error_bytes_retained_for_verifier_but_redirect_errors_refused():
    class Failing:
        def open(self, *args, **kw):
            raise urllib.error.HTTPError(ORIGIN + STATUS, self.code, '',
                {'content-type': 'application/json'}, io.BytesIO(b'{}'))
    op = Failing()
    op.code = 409
    t = HttpsTransport(origin=ORIGIN, opener=op)
    assert t('POST', STATUS, {}, b'{}')[0] == 409
    op.code = 302
    with pytest.raises(ServiceRefused, match='^response_unverified$'):
        t('POST', STATUS, {}, b'{}')


def test_transport_request_and_response_caps_before_use():
    reply = Reply(b'x' * (RESPONSE_LIMIT + 1))
    op = Opener(reply)
    t = HttpsTransport(origin=ORIGIN, opener=op)
    with pytest.raises(ServiceRefused, match='^response_unverified$'):
        t('POST', STATUS, {}, b'{}')
    assert reply.reads == [32769]
    with pytest.raises(ServiceRefused, match='^body_limit$'):
        t('POST', STATUS, {}, b'x' * 8193)
    assert len(op.calls) == 1
    # /send alone gets the larger cap, verified before handing to opener.
    reply.raw, reply.url = b'{}', ORIGIN + '/v1/pull/send'
    t('POST', '/v1/pull/send', {}, b'x' * 131072)
    with pytest.raises(ServiceRefused, match='^body_limit$'):
        t('POST', '/v1/pull/send', {}, b'x' * 131073)


def test_transport_response_url_retarget_refuses_before_read():
    reply = Reply(url='https://elsewhere.example.test' + STATUS)
    with pytest.raises(ServiceRefused, match='^response_unverified$'):
        HttpsTransport(origin=ORIGIN, opener=Opener(reply))('POST', STATUS, {}, b'{}')
    assert not reply.reads


def test_service_cannot_be_composed_with_another_bound_origin(w):
    transport = HttpsTransport(origin='https://elsewhere.example.test')
    with pytest.raises(ServiceRefused, match='^service_origin_invalid$'):
        ServiceClient(w.store, w.seal, transport=transport, service_origin=ORIGIN,
            reviewed_service=lambda: (w.pin, MANIFEST))


def test_transport_error_is_closed_and_logged(capsys):
    class Failed:
        def open(self, *args, **kw):
            raise OSError('secret provider details')
    with pytest.raises(ServiceRefused, match='^connection_lost$'):
        HttpsTransport(origin=ORIGIN, opener=Failed())('POST', STATUS, {}, b'{}')
    output = capsys.readouterr().out
    assert 'connection_lost' in output and 'secret provider details' not in output


def test_service_transport_exception_is_closed_logged_and_never_records_evidence(w, capsys):
    def failed(*args):
        raise RuntimeError('private fence and mailbox details')
    w.client.transport = failed
    with pytest.raises(ServiceRefused, match='^connection_lost$'):
        w.client.status()
    output = capsys.readouterr().out
    assert 'connection_lost' in output and 'private fence and mailbox details' not in output
    assert w.client.cached_evidence() is None and not w.rows('mail_pull_status')


def test_real_root_manifest_review_and_cas_floor_are_used_on_each_call(w):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from mc.agent_mail_service_manifest import verify_service_manifest, ManifestState
    from mc.agent_mail_enrollment_state import ManifestCAS, initialize
    initialize(w.store)
    root = Ed25519PrivateKey.generate()
    payload = ['clayrune-mail-service-manifest', '1', ORIGIN, '1', str(NOW - 1000), str(NOW + 86400000),
        w.pin.key_id, w.pin.public_key, str(NOW - 500), str(NOW + 86400000), []]
    signed = encoded(root.sign(compact(payload)))
    manifest_digest = digest(compact(payload))
    floor = ManifestCAS(w.store)
    calls = []
    def reviewed():
        calls.append(1)
        pin = verify_service_manifest(payload, signed, root_public_key=encoded(root.public_key().public_bytes_raw()),
            service_origin=ORIGIN, store=floor, now_ms=w.now, enabled=True)
        return pin, manifest_digest
    w.review = reviewed
    w.pin = reviewed()[0]
    with w.store._tx() as db:
        db.execute('UPDATE mail_pull_generations SET manifest_digest=?', (manifest_digest,))
    assert w.client.status()['state'] == 'active' and len(calls) >= 5
    assert floor.compare_and_set(ORIGIN, floor.load(ORIGIN), ManifestState(2, '0' * 64))
    count = len(w.calls)
    with pytest.raises(ServiceRefused, match='^service_key_changed$'):
        w.client.status()
    assert len(w.calls) == count


def test_importers_still_resolve_without_network():
    from mc.agent_mail_ceremony_client import verify_response
    from mc.agent_mail_ceremony_remote import ServiceClient as same
    from mc.agent_mail_factory import HttpsTransport as same_transport
    assert callable(verify_response) and same is ServiceClient and same_transport is HttpsTransport


_SEND_VECTORS = json.loads((Path(__file__).parent / 'fixtures' /
    'agent-mail-pull-send-receipts.json').read_text())['vectors']


@pytest.mark.parametrize('path', ['/v1/pull/status', '/v1/pull/ticket'])
def test_common_uncertain_remains_valid_outside_send(w, path):
    request = {'op_id': '5' * 32} if path.endswith('/ticket') else {}
    h, raw = w.request(path, request)
    request_id = request.get('op_id', h['x-mail-nonce'])
    body = {'v': 1, 'code': 'uncertain', 'request_id': request_id}
    w.change = lambda *args: w.response(path, h, request, body, 409)
    result = w.client('POST', path, h, raw)
    assert result[0] == 409 and json.loads(result[2]) == body


@pytest.mark.parametrize('vector', _SEND_VECTORS, ids=lambda v: v['name'])
def test_shared_send_intent_receipt_vectors(w, vector):
    path, request = vector['path'], vector['request']
    h, raw = w.request(path, request)
    def response(*args):
        return w.response(path, h, request, vector['body'], vector['status'])
    w.change = response
    before = w.rows('mail_pull_accounts')
    if vector['valid']:
        result = w.client('POST', path, h, raw)
        assert json.loads(result[2]) == vector['body'] and result[0] == vector['status']
    else:
        with pytest.raises(ServiceRefused, match='^response_unverified$'):
            w.client('POST', path, h, raw)
    assert w.rows('mail_pull_accounts') == before and w.client.cached_evidence() is None


@pytest.mark.parametrize('provider,valid', [
    ('provider-id', True), (' opaque id ', True), ('x' * 256, True),
    ('', False), (' ', False), ('x' * 257, False), ('\nprovider', False),
    ('non-ascii-\u00e9', False), (None, False), (123, False),
])
def test_accepted_send_requires_bounded_provider_evidence(w, provider, valid):
    path = '/v1/pull/send'
    request = {'op_id': '5' * 32, 'intent_id': 'intent1', 'thread_id': 'thread1'}
    h, raw = w.request(path, request)
    body = dict(v=1, code='accepted', request_id=request['op_id'], state='accepted',
                intent_id='intent1', thread_id='thread1', message_id='dedupe@example.test',
                provider_id=provider)
    w.change = lambda *args: w.response(path, h, request, body, 200)
    if valid:
        assert json.loads(w.client('POST', path, h, raw)[2]) == body
    else:
        with pytest.raises(ServiceRefused, match='^response_unverified$'):
            w.client('POST', path, h, raw)
    assert w.client.cached_evidence() is None
