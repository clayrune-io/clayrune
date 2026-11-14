"""Local synthetic HTTP proofs + real loopback RFC6455; never real mail/TLS/vault."""
from contextlib import contextmanager
from dataclasses import replace
from email.utils import formatdate
import base64
import hashlib
import json
import os
import queue
import socket
import struct
import subprocess
import sys
import threading
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import pytest
import websocket

from mc.agent_mail_enrollment_keys import IdentitySeal, SealReference
from mc.agent_mail_pull_client import PullClient, _Socket, _Failure, FRAME_BYTES, PROTOCOL, TICKET
from mc.agent_mail_signing import IngressRefused, unbase64url
from test_agent_mail_pull_receiver import (
    Harness, NOW, INSTALL, FLOW, MANIFEST, ORIGIN, LEASE, event, compact, array, digest, encoded)


def eventually(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate(), 'condition did not become true'


class World(Harness):
    def __init__(self, path):
        super().__init__(path)
        self.key = b'k' * 32
        self.seal = IdentitySeal(path / 'seals', key=lambda: self.key)
        self.ref = SealReference(FLOW, INSTALL, 1, 'key1')
        self.public = self.seal.generate_pull(self.ref, authorize=lambda: True, expected_public_key=None)
        self.calls = []
        self.http_update = lambda status, headers, value: (status, headers, value)
        self.last_request = None

    def response(self, request, value, status=200, **headers):
        raw = compact(value)
        nonce = request['x-mail-nonce']
        h = {'content-type': 'application/json', 'x-mail-version': '2',
             'x-mail-direction': 'pull-to-install', 'x-mail-key-id': self.pin.key_id,
             'x-mail-install-id': INSTALL, 'x-mail-generation': '1', 'x-mail-owner-generation': '1',
             'x-mail-timestamp': str(self.now), 'x-mail-nonce': nonce, 'x-mail-digest': digest(raw)}
        h['x-mail-signature'] = encoded(self.private.sign(array('clayrune-agent-mail-pull-http', '1',
            'pull-to-install', ORIGIN, 'POST', TICKET, self.pin.key_id, INSTALL, 1, 1,
            self.now, nonce, status, digest(raw))))
        h.update(headers)
        return status, h, raw

    def http(self, method, path, headers, body):
        assert (method, path) == ('POST', TICKET)
        assert set(json.loads(body)) == {'op_id'}
        assert headers['x-mail-digest'] == digest(body)
        assert headers['x-mail-direction'] == 'install-to-pull'
        assert headers['x-mail-version'] == '2'
        assert not any(k.lower() in ('cookie', 'authorization', 'origin') for k in headers)
        proof = array('clayrune-agent-mail-pull-http', '1', 'install-to-pull', ORIGIN, 'POST', TICKET,
            'key1', INSTALL, 1, 1, headers['x-mail-timestamp'], headers['x-mail-nonce'], digest(body))
        Ed25519PublicKey.from_public_bytes(unbase64url(self.public, 32)).verify(
            unbase64url(headers['x-mail-signature'], 64), proof)
        self.calls.append((dict(headers), body))
        self.last_request = headers
        value = dict(v=1, code='ok', request_id=json.loads(body)['op_id'], ticket='f' * 64,
                     expires_ms=self.now + 60000, protocol=PROTOCOL)
        return self.http_update(*self.response(headers, value))

    def client(self, **updates):
        return PullClient(self.store, self.seal, self.http, install_id=INSTALL,
            reviewed_service=lambda: (self.pin, MANIFEST), **updates)

    def hello(self, epoch=2, **updates):
        p = dict(connection_id=('%032x' % epoch), authenticated_ms=self.now,
                 reauth_ms=self.now + 86400000, max_events=10, max_bytes=524288)
        p.update(updates)
        return self.wire(kind='hello', payload=p, epoch=epoch, frame_id=os.urandom(16).hex())

    def control(self, kind, p, epoch=2, **updates):
        return self.wire(kind=kind, payload=p, epoch=epoch, frame_id=os.urandom(16).hex(), **updates)


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


class Peer:
    """Small local fake service speaking real RFC6455, with explicit shutdown."""
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(4)
        self.listener.settimeout(.1)
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.incoming = queue.Queue()
        self.commands = queue.Queue()
        self.errors = []
        self.connections = 0
        self.headers = []
        self.pings = queue.Queue()
        self.auto_pong = True
        self.conn = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        try:
            while not self.stop.is_set():
                try:
                    c, _ = self.listener.accept()
                except socket.timeout:
                    continue
                with c:
                    self.conn = c
                    c.settimeout(2)
                    raw = b''
                    while b'\r\n\r\n' not in raw:
                        raw += c.recv(4096)
                    h = dict(line.split(': ', 1) for line in raw.decode().split('\r\n')[1:] if ': ' in line)
                    self.headers.append({k.lower(): v for k, v in h.items()})
                    key = self.headers[-1]['sec-websocket-key']
                    accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                    c.sendall(('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                        'Sec-WebSocket-Accept: ' + accept + '\r\nSec-WebSocket-Protocol: ' + PROTOCOL + '\r\n\r\n').encode())
                    self.connections += 1
                    c.settimeout(.05)
                    decoder = websocket.WebSocket(enable_multithread=True)
                    decoder.sock, decoder.connected = c, True
                    while not self.stop.is_set():
                        try:
                            command = self.commands.get_nowait()
                        except queue.Empty:
                            command = None
                        if command == 'drop':
                            break
                        if command is not None:
                            c.sendall(command)
                        try:
                            f = decoder.recv_frame()
                        except websocket.WebSocketTimeoutException:
                            continue
                        except websocket.WebSocketConnectionClosedException:
                            break
                        if f.opcode == websocket.ABNF.OPCODE_CLOSE:
                            break
                        if f.opcode == websocket.ABNF.OPCODE_TEXT:
                            assert f.mask_value == 1
                            self.incoming.put(json.loads(f.data))
                        elif f.opcode == websocket.ABNF.OPCODE_PING:
                            self.pings.put(f.data)
                            if self.auto_pong:
                                self.send(f.data, websocket.ABNF.OPCODE_PONG)
                        else:
                            self.incoming.put(f)
        except OSError as exc:
            if not self.stop.is_set():
                self.errors.append(exc)
        except Exception as exc:
            self.errors.append(exc)

    def send(self, raw, opcode=websocket.ABNF.OPCODE_TEXT, fin=1):
        f = websocket.ABNF.create_frame(raw, opcode, fin=fin)
        f.mask_value = 0
        self.commands.put(f.format())

    def take(self, kind):
        f = self.incoming.get(timeout=4)
        assert f['type'] == kind
        return f

    def factory(self):
        peer = self
        class LocalSocket(_Socket):
            def connect(self, url, **kwargs):
                assert url == 'wss://mail.example.test/v1/pull/socket'
                assert kwargs['suppress_origin'] is True and kwargs['redirect_limit'] == 0
                assert kwargs['subprotocols'] == [PROTOCOL]
                assert kwargs['header'] == {'Authorization': 'MailTicket ' + 'f' * 64}
                return super().connect('ws://127.0.0.1:' + str(peer.port) + '/v1/pull/socket', **kwargs)
        return LocalSocket()

    def close(self):
        self.stop.set()
        self.thread.join(3)
        self.listener.close()
        assert not self.thread.is_alive()
        assert not self.errors


@contextmanager
def running(w, **updates):
    peer = Peer()
    client = w.client(socket_factory=peer.factory, **updates)
    try:
        client.start()
        eventually(lambda: peer.connections == 1)
        yield client, peer
    finally:
        assert client.stop()
        peer.close()


def connect(w, peer, epoch=2):
    peer.send(w.hello(epoch))
    return peer.take('resume')


def offered(w, epoch=2, **updates):
    return w.wire(epoch=epoch, frame_id=os.urandom(16).hex(), **updates)


def committed(w, ack, **updates):
    p = dict(request_id=ack['request_id'], **{k: ack['payload'][k]
        for k in ('event_id', 'event_digest', 'body_digest', 'local_receipt_id')},
        receipt_id='9' * 32, committed_ms=w.now)
    p.update(updates)
    return w.control('ack_committed', p, ack['epoch'])


def test_import_and_construction_have_no_network_or_seal_creation(w, monkeypatch):
    def forbidden(*a, **k):
        pytest.fail('network at construction')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    client = w.client(socket_factory=forbidden)
    assert not client.running and client.reason == '' and w.calls == []
    assert client.stop()
    # Fresh interpreter catches module-level start/socket side effects too.
    script = "import socket; socket.socket.connect=lambda *a: (_ for _ in ()).throw(RuntimeError('network')); import mc.agent_mail_pull_client"
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_signed_hello_gates_resume_and_connection_is_durable(w):
    with running(w) as (client, peer):
        assert peer.incoming.empty()
        resume = connect(w, peer)
        assert resume['payload'] == dict(after_seq=0, limit=10, available_bytes=524288)
        assert resume['epoch'] == 2
        c = w.rows('mail_pull_connections')[0]
        assert (c['epoch'], c['connection_id'], c['scan_complete']) == (2, '%032x' % 2, 0)
        assert 'origin' not in peer.headers[0]
        assert peer.headers[0]['authorization'] == 'MailTicket ' + 'f' * 64
        assert peer.headers[0]['sec-websocket-protocol'] == PROTOCOL


def test_custody_commits_before_ack_and_only_cloud_proof_removes_outbox(w):
    with running(w) as (client, peer):
        connect(w, peer)
        peer.send(offered(w))
        ack = peer.take('custody_ack')
        assert len(w.rows('events')) == len(w.rows('mail_pull_receipts')) == len(w.rows('mail_pull_ack_outbox')) == 1
        assert ack['payload']['local_receipt_id'] == w.rows('mail_pull_receipts')[0]['local_receipt_id']
        assert w.rows('mail_pull_receipts')[0]['cloud_receipt_id'] is None
        peer.send(committed(w, ack))
        eventually(lambda: w.rows('mail_pull_ack_outbox') == [])
        assert w.rows('mail_pull_receipts')[0]['cloud_receipt_id'] == '9' * 32
        assert len(w.rows('events')) == 1


def test_disconnect_after_local_commit_replays_ack_before_resume_without_new_work(w):
    with running(w, jitter=lambda low, high: 0) as (client, peer):
        connect(w, peer)
        peer.send(offered(w))
        first = peer.take('custody_ack')
        peer.commands.put('drop')
        eventually(lambda: peer.connections == 2)
        assert len(w.calls) == 2
        assert w.calls[0][1] != w.calls[1][1]
        assert w.calls[0][0]['x-mail-nonce'] != w.calls[1][0]['x-mail-nonce']
        peer.send(w.hello(3))
        replay = peer.take('custody_ack')
        assert replay == dict(first, epoch=3)
        peer.take('resume')
        peer.send(committed(w, replay))
        eventually(lambda: not w.rows('mail_pull_ack_outbox'))
        assert len(w.rows('events')) == 1


def test_process_restart_replays_old_expired_lease_in_new_epoch(w):
    first = w.receiver.receive(w.wire())
    w.now += 120000
    with running(w) as (client, peer):
        peer.send(w.hello(2))
        replay = peer.take('custody_ack')
        assert replay == dict(first, epoch=2)
        peer.take('resume')


def test_failed_custody_commit_sends_no_ack_and_preserves_intent(w, monkeypatch):
    with running(w) as (client, peer):
        connect(w, peer)
        original = w.store._tx
        @contextmanager
        def fail_before_commit():
            with original() as db:
                yield db
                if db.execute('SELECT 1 FROM mail_pull_receipts').fetchone():
                    raise RuntimeError('SECRET body from synthetic commit failure')
        monkeypatch.setattr(w.store, '_tx', fail_before_commit)
        peer.send(offered(w))
        eventually(lambda: client.reason == 'connection_lost')
        assert peer.incoming.empty() and not w.rows('events') and not w.rows('mail_pull_ack_outbox')
        monkeypatch.setattr(w.store, '_tx', original)


def test_unknown_thread_is_hold_not_ack(w):
    with running(w) as (client, peer):
        connect(w, peer)
        peer.send(offered(w, e=event(thread_id='unknown')))
        hold = peer.take('hold')
        assert hold['payload']['reason'] == 'unknown_thread'
        assert hold['payload']['retry_after_ms'] == w.now + 60000
        assert not w.rows('events') and not w.rows('mail_pull_ack_outbox')


@pytest.mark.parametrize('update', [dict(generation=2), dict(owner_generation=2), dict(install_id='b'*32),
    dict(epoch=1), dict(type='send'), dict(issued_ms=NOW-300001), dict(extra='x')])
def test_wrong_scope_stale_or_unknown_frame_closes_without_custody(w, update):
    with running(w) as (client, peer):
        connect(w, peer)
        peer.send(offered(w, **update))
        eventually(lambda: not client.running)
        assert not w.rows('events') and not w.rows('mail_pull_ack_outbox') and peer.incoming.empty()


def test_unsigned_event_and_payload_substitution_are_rejected(w):
    with running(w) as (client, peer):
        connect(w, peer)
        f = json.loads(offered(w))
        f['signature'] = encoded(b'x' * 64)
        peer.send(compact(f))
        eventually(lambda: not client.running)
        assert client.reason == 'frame_unverified' and not w.rows('events')


@pytest.mark.parametrize('case', ['before_hello','second_hello','unhashable_type','boolean_scope','duplicate_key'])
def test_protocol_confusion_never_receives_custody(w, case):
    with running(w) as (client, peer):
        if case != 'before_hello':
            connect(w,peer)
        if case == 'second_hello':
            raw = w.hello()
        elif case == 'unhashable_type':
            f=json.loads(offered(w))
            f['type']=[]
            raw=compact(f)
        elif case == 'boolean_scope':
            raw=offered(w,generation=True)
        elif case == 'duplicate_key':
            raw=offered(w)[:-1]+b',"v":1}'
        else:
            raw=offered(w)
        peer.send(raw)
        eventually(lambda:not client.running)
        assert not w.rows('events') and not w.rows('mail_pull_ack_outbox')


def test_reoffer_keeps_one_work_item_and_reconciles_same_cloud_receipt(w):
    with running(w) as (client, peer):
        connect(w,peer)
        peer.send(offered(w))
        first=peer.take('custody_ack')
        proof=committed(w,first)
        peer.send(proof)
        eventually(lambda:not w.rows('mail_pull_ack_outbox'))
        w.now+=120000
        peer.send(offered(w,payload=dict(event=event(),lease_id='5'*32,lease_until_ms=w.now+60000)))
        second=peer.take('custody_ack')
        assert second['request_id']==first['request_id']
        assert second['payload']==dict(first['payload'],lease_id='5'*32)
        # A new frame carries the original committed receipt, never new work.
        peer.send(w.control('ack_committed',json.loads(base64.urlsafe_b64decode(
            json.loads(proof)['payload']+'='*(-len(json.loads(proof)['payload'])%4)))))
        eventually(lambda:not w.rows('mail_pull_ack_outbox'))
        assert len(w.rows('events'))==1


def test_duplicate_frame_is_deduplicated_and_conflicting_id_halts(w):
    with running(w) as (client, peer):
        connect(w, peer)
        raw = offered(w)
        peer.send(raw)
        peer.take('custody_ack')
        peer.send(raw)
        peer.send(w.wire(epoch=2, frame_id=json.loads(raw)['frame_id'], e=event(text='conflict')))
        eventually(lambda: not client.running)
        assert client.reason == 'frame_unverified' and len(w.rows('events')) == 1
        assert peer.incoming.empty()


@pytest.mark.parametrize('changes', [dict(event_digest='a'*64), dict(body_digest='a'*64),
    dict(local_receipt_id='a'*32), dict(receipt_id='invalid'), dict(committed_ms=True)])
def test_conflicting_ack_confirmation_preserves_outbox(w, changes):
    with running(w) as (client, peer):
        connect(w, peer)
        peer.send(offered(w))
        ack = peer.take('custody_ack')
        peer.send(committed(w, ack, **changes))
        eventually(lambda: not client.running)
        assert len(w.rows('mail_pull_ack_outbox')) == 1
        assert w.rows('mail_pull_receipts')[0]['cloud_receipt_id'] is None


def test_signed_status_pages_and_initial_scan_barrier(w):
    with running(w) as (client, peer):
        first = connect(w, peer)
        p = dict(request_id=first['request_id'], next_seq=10, scan_complete=False,
                 pending_count=20, oldest_expires_ms=w.now+100000)
        peer.send(w.control('status', p))
        second = peer.take('resume')
        assert second['payload']['after_seq'] == 10
        assert w.rows('mail_pull_connections')[0]['scan_complete'] == 0
        peer.send(w.control('status', dict(p, request_id=second['request_id'], next_seq=20, scan_complete=True)))
        eventually(lambda: w.rows('mail_pull_connections')[0]['scan_complete'] == 1)
        assert peer.incoming.empty()  # No endless idle resume polling.


def page_status(w, request, *, next_seq=0, complete=True):
    return w.control('status', dict(request_id=request['request_id'], next_seq=next_seq,
        scan_complete=complete, pending_count=0, oldest_expires_ms=None))


def test_wake_starts_own_resume_and_duplicate_hints_coalesce_mid_page(w):
    with running(w) as (client, peer):
        first = connect(w, peer)
        peer.send(page_status(w, first))
        eventually(lambda: w.rows('mail_pull_connections')[0]['scan_complete'] == 1)
        raw = w.control('wake', {})
        peer.send(raw)
        resumed = peer.take('resume')
        assert resumed['request_id'] != first['request_id']
        assert resumed['payload']['after_seq'] == 0
        # Exact wire replay and distinct hints cannot start concurrent resumes.
        peer.send(raw)
        for _ in range(4):
            peer.send(w.control('wake', {}))
        eventually(lambda: len(client._seen) == 7)  # hello, status, five unique hints
        assert peer.incoming.empty()
        # Finish the current scan, including an empty/skipped first page.
        peer.send(page_status(w, resumed, next_seq=10, complete=False))
        page2 = peer.take('resume')
        assert page2['payload']['after_seq'] == 10
        assert peer.incoming.empty()
        peer.send(offered(w))
        ack = peer.take('custody_ack')
        peer.send(committed(w, ack))
        peer.send(page_status(w, page2, next_seq=11))
        # All hints that arrived mid-scan become one restart from zero.
        coalesced = peer.take('resume')
        assert coalesced['payload']['after_seq'] == 0
        peer.send(page_status(w, coalesced, next_seq=11))
        eventually(lambda: client._resume_request is None)
        assert peer.incoming.empty() and len(w.rows('events')) == 1


def test_wake_mid_initial_page_preserves_cursor_until_scan_finishes(w):
    with running(w) as (client, peer):
        first = connect(w, peer)
        peer.send(w.control('wake', {}))
        peer.send(page_status(w, first, next_seq=10, complete=False))
        second = peer.take('resume')
        assert second['payload']['after_seq'] == 10
        peer.send(page_status(w, second, next_seq=20))
        assert peer.take('resume')['payload']['after_seq'] == 0
        assert peer.incoming.empty()


@pytest.mark.parametrize('case', ['unknown', 'unsigned', 'forged', 'payload', 'unsolicited_status'])
def test_invalid_wake_or_unsolicited_status_halts_protocol_failure(w, case):
    with running(w) as (client, peer):
        connect(w, peer)
        if case == 'unsolicited_status':
            raw = page_status(w, dict(request_id='0' * 32))
        else:
            raw = w.control('wake_unknown' if case == 'unknown' else 'wake',
                            {'event': 'untrusted'} if case == 'payload' else {})
            if case in ('unsigned', 'forged'):
                frame = json.loads(raw)
                if case == 'unsigned':
                    del frame['signature']
                else:
                    frame['signature'] = encoded(b'x' * 64)
                raw = compact(frame)
        peer.send(raw)
        eventually(lambda: not client.running)
        assert client.reason == 'protocol_failure'
        assert peer.incoming.empty() and not w.rows('events')


def test_stop_cancels_blocked_receive_and_releases_exclusion(w):
    with running(w) as (client, peer):
        connect(w, peer)
        start = time.monotonic()
        assert client.stop(2)
        assert time.monotonic() - start < 2
        other = w.client()
        other._claim_install()
        other._release_install()


def test_stop_during_http_cannot_open_socket_and_retains_lock_until_exit(w):
    entered, release = threading.Event(), threading.Event()
    http = w.http
    def blocking(*args):
        entered.set()
        assert release.wait(3)
        return http(*args)
    w.http = blocking
    client = w.client(socket_factory=lambda: pytest.fail('socket after cancellation'))
    other = w.client()
    client.start()
    try:
        assert entered.wait(2)
        assert client.stop(.01) is False
        with pytest.raises(_Failure, match='client_already_running'):
            other.start()
    finally:
        release.set()
        assert client.stop(3)
    other._claim_install()
    other._release_install()


def test_stop_clears_scan_barrier_and_same_client_can_restart(w):
    with running(w) as (client, peer):
        r = connect(w, peer)
        peer.send(w.control('status', dict(request_id=r['request_id'], next_seq=0,
            scan_complete=True,pending_count=0,oldest_expires_ms=None)))
        eventually(lambda: w.rows('mail_pull_connections')[0]['scan_complete'] == 1)
        assert client.stop()
        assert w.rows('mail_pull_connections')[0]['scan_complete'] == 0
        client.start()
        eventually(lambda: peer.connections == 2)
        peer.send(w.hello(3))
        assert peer.take('resume')['epoch'] == 3


def test_only_one_client_across_objects_and_rejected_start_keeps_lock(w):
    with running(w) as (client, peer):
        other = w.client(socket_factory=lambda: pytest.fail('second socket'))
        for _ in range(2):
            with pytest.raises(_Failure, match='client_already_running'):
                other.start()
        with pytest.raises(_Failure, match='client_already_running'):
            client.start()
        assert len(w.calls) == peer.connections == 1


def test_exclusion_is_enforced_across_processes(w):
    with running(w) as (client, peer):
        lock = str(w.store.path.with_name(w.store.path.name+'.'+INSTALL+'.pull.lock'))
        script = '''import os,sys
f=open(sys.argv[1],'a+b')
try:
 if os.name=='nt':
  import msvcrt
  msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
 else:
  import fcntl
  fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
except OSError:
 sys.exit(23)
sys.exit(0)
'''
        result = subprocess.run([sys.executable, '-c', script, lock], capture_output=True)
        assert result.returncode == 23


@pytest.mark.parametrize('mutation,reason', [('vault', 'vault_locked'), ('consent','local_disabled'),
    ('pin','service_key_changed'), ('epoch','stale_generation')])
def test_live_authority_loss_stops_without_reconnect(w, mutation, reason):
    with running(w, jitter=lambda low, high: 0) as (client, peer):
        connect(w, peer)
        if mutation == 'vault':
            w.key = None
        elif mutation == 'pin':
            w.pin = replace(w.pin, expires_at_ms=w.now)
        else:
            with w.store._tx() as db:
                db.execute('UPDATE mail_pull_accounts SET local_enabled=0' if mutation == 'consent'
                           else 'UPDATE mail_pull_connections SET epoch=999')
        if mutation == 'epoch':
            peer.send(offered(w))
        eventually(lambda: not client.running)
        assert client.reason == reason and len(w.calls) == 1 and not w.rows('events')


@pytest.mark.parametrize('code,reason,reconnect', [(1000,'connection_closed',True), (1009,'frame_limit',False),
    (1013,'service_unavailable',True), (4001,'connection_superseded',False),
    (4002,'protocol_failure',False), (4003,'unauthorized',False), (4004,'reauth_due',True)])
def test_close_codes_control_reconnect(w, code, reason, reconnect):
    with running(w, jitter=lambda low, high: 30 if high >= 30 else high) as (client, peer):
        connect(w, peer)
        peer.send(struct.pack('!H',code)+b'UNTRUSTED SECRET', websocket.ABNF.OPCODE_CLOSE)
        eventually(lambda: client.reason == reason)
        if not reconnect:
            eventually(lambda: not client.running)
        assert 'SECRET' not in client.reason


def test_backoff_full_jitter_bounds_cap_and_longer_retry_after(w):
    bounds = []
    def jitter(low, high):
        bounds.append((low, high))
        return high / 2
    client = w.client(jitter=jitter)
    assert [client._backoff(n) for n in (1,2,3,9,10,1000000)] == [.5,1,2,128,150,150]
    assert bounds == [(0,1),(0,2),(0,4),(0,256),(0,300),(0,300)]
    assert client._backoff(1, w.now+600000) == 600


def test_backoff_resets_only_after_sixty_seconds_of_authenticated_connection(w, monkeypatch):
    ticks = [0.0]
    client = w.client(monotonic=lambda: ticks[0], jitter=lambda low,high: high)
    durations = iter([59,59,60,59])
    observed = []
    def session():
        client._epoch = 2
        client._connected_at = ticks[0]
        ticks[0] += next(durations)
        raise _Failure('connection_lost')
    def record(failures,reason,delay):
        observed.append((failures,delay))
        if len(observed)==4:
            client._stop.set()
    monkeypatch.setattr(client,'_session',session)
    monkeypatch.setattr(client,'_record',record)
    monkeypatch.setattr(client._stop,'wait',lambda delay: client._stop.is_set())
    client._run()
    assert observed == [(1,1),(2,2),(1,1),(2,2)]


def test_stop_interrupts_reconnect_backoff(w):
    with running(w, jitter=lambda low, high: high) as (client, peer):
        connect(w, peer)
        peer.send(struct.pack('!H',1013), websocket.ABNF.OPCODE_CLOSE)
        eventually(lambda: client.reason == 'service_unavailable')
        assert client.stop(1)
        assert len(w.calls) == 1


@pytest.mark.parametrize('mutation', ['signature','nonce','scope','unsigned','extra','duplicate','expired','protocol'])
def test_ticket_response_proof_is_required_before_socket(w, mutation):
    def update(status, h, raw):
        if mutation == 'signature':
            h['x-mail-signature'] = encoded(b'x'*64)
        elif mutation == 'nonce':
            h['x-mail-nonce'] = '0'*32
        elif mutation == 'scope':
            h['x-mail-owner-generation'] = '2'
        elif mutation == 'unsigned':
            h = {'content-type':'application/json'}
        else:
            value = json.loads(raw)
            if mutation == 'extra':
                value['cookie'] = 'bad'
            elif mutation == 'duplicate':
                raw = raw[:-1]+b',"v":1}'
                h['x-mail-digest'] = digest(raw)
                h['x-mail-signature'] = encoded(w.private.sign(array('clayrune-agent-mail-pull-http','1',
                    'pull-to-install',ORIGIN,'POST',TICKET,w.pin.key_id,INSTALL,1,1,w.now,
                    h['x-mail-nonce'],200,digest(raw))))
                return status,h,raw
            elif mutation == 'expired':
                value['expires_ms'] = w.now
            else:
                value['protocol'] = 'wrong'
            return w.response(w.last_request, value)
        return status, h, raw
    w.http_update = update
    client = w.client(socket_factory=lambda: pytest.fail('unverified ticket used'))
    client.start()
    eventually(lambda: not client.running)
    assert client.reason == 'response_unverified'


@pytest.mark.parametrize('retry_header', ['180', formatdate((NOW+180000)/1000,usegmt=True)])
def test_signed_throttle_honors_longer_retry_after(w, retry_header):
    def update(status,h,raw):
        return w.response(w.last_request, dict(v=1,code='request_limit',request_id=json.loads(raw)['request_id'],
            retry_after_ms=w.now+120000),429,**{'retry-after':retry_header})
    w.http_update = update
    client = w.client(socket_factory=lambda: pytest.fail('throttled socket'), jitter=lambda low, high: high)
    delays = []
    original = client._record
    def record(failures, reason, delay):
        delays.append(delay)
        original(failures,reason,delay)
    client._record = record
    client.start()
    eventually(lambda: bool(delays))
    assert client.stop(1)
    assert delays == [180]


def test_abort_can_wake_upgrade_before_connected_flag(w):
    a,b=socket.socketpair()
    ws=_Socket()
    ws.sock=a
    assert ws.connected is False
    try:
        ws.abort()
        b.settimeout(1)
        assert b.recv(1)==b''
    finally:
        a.close()
        b.close()


def test_fragmented_message_cap_is_enforced_before_accumulation(w):
    with running(w) as (client, peer):
        connect(w, peer)
        peer.send(b' '*FRAME_BYTES, fin=0)
        peer.send(b' ', websocket.ABNF.OPCODE_CONT)
        eventually(lambda: not client.running)
        assert client.reason == 'frame_limit' and not w.rows('events')


def test_frame_size_limit_rejects_header_without_reading_payload(w):
    with running(w) as (client, peer):
        connect(w, peer)
        peer.commands.put(b'\x81\x7f'+struct.pack('!Q',FRAME_BYTES+1))
        eventually(lambda: not client.running)
        assert client.reason == 'frame_limit'


def test_binary_frames_are_rejected(w):
    with running(w) as (client, peer):
        connect(w, peer)
        peer.send(offered(w), websocket.ABNF.OPCODE_BINARY)
        eventually(lambda: not client.running)
        assert client.reason == 'protocol_failure' and not w.rows('events')


def test_control_ping_pong_without_json_heartbeat(w):
    with running(w) as (client, peer):
        connect(w, peer)
        peer.send(b'probe', websocket.ABNF.OPCODE_PING)
        f = peer.incoming.get(timeout=4)
        assert f.opcode == websocket.ABNF.OPCODE_PONG and f.data == b'probe'


def test_client_ping_and_missing_pong_deadline(w):
    ticks = [0.0]
    with running(w, monotonic=lambda: ticks[0]) as (client, peer):
        connect(w, peer)
        peer.auto_pong = False
        ticks[0] = 60
        ping = peer.pings.get(timeout=3)
        assert isinstance(ping,bytes) and len(ping)==8
        ticks[0] = 91
        eventually(lambda: client.reason == 'pong_timeout')


def test_hello_timeout_sends_no_resume(w):
    ticks = [0.0]
    with running(w, monotonic=lambda: ticks[0]) as (client, peer):
        # The client computes hello_deadline only after ws.connect returns,
        # which can be after peer.connections == 1. A single jump to 11 could
        # land before that, leaving the deadline at 21 on a frozen clock.
        # Advance on every poll so the deadline is crossed whenever it was set.
        def timed_out():
            ticks[0] += 11
            return client.reason == 'hello_timeout'
        eventually(timed_out)
        assert peer.incoming.empty()


@pytest.mark.parametrize('reason', ['reauth_due','service_key_changed'])
def test_reauth_notice_stops_offers_and_uses_new_ticket_or_review_gate(w, reason):
    with running(w, jitter=lambda low,high:high) as (client, peer):
        connect(w,peer)
        peer.send(w.control('reauth_required',dict(reason=reason,close_at_ms=w.now+10000)))
        eventually(lambda:client.reason==reason)
        if reason=='service_key_changed':
            eventually(lambda:not client.running)
        assert not w.rows('events')


def test_no_raw_exception_or_wire_content_in_logs(w, monkeypatch):
    logs = []
    monkeypatch.setattr('mc.agent_mail_pull_client._log', lambda message, **k: logs.append(message))
    def http(*args):
        raise RuntimeError('SECRET token and mail body')
    w.http = http
    client = w.client(jitter=lambda low, high: high)
    client.start()
    eventually(lambda: bool(logs))
    assert client.stop()
    assert logs and all('SECRET' not in line for line in logs)
    assert any('connection_lost' in line for line in logs)
