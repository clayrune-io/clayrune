"""Server-owned pull transport (unit 18), deliberately unwired.

The supervisor explicitly calls start()/stop(); construction/import does no I/O.
Supply the SAME guarded MailStore, IdentitySeal and reviewed manifest callback
as signup. HTTP uses SignupTransport (pinned HTTPS, no redirect, 10s timeout).
No seed leaves IdentitySeal. No callbacks dispatch work: scan_complete is a
durable barrier for the future supervisor/dispatcher, never a delivery receipt.

One OS lock beside the guarded store excludes competing clients/processes for
that install. Never delete its lock file: an open inode is the exclusion token.
Only signed ack_committed proof retires an outbox row. Network failure, stop,
lease expiry and ordinary reconnect leave custody/ack intent intact.
"""
from __future__ import annotations

from collections import OrderedDict
from email.utils import parsedate_to_datetime
import errno
import os
import random
import re
import secrets
import socket
import struct
import threading
import time
from typing import Any, BinaryIO, Callable, Mapping, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
import websocket
from websocket._abnf import frame_buffer

from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal, SealReference
from mc.agent_mail_pull_receiver import (FRAME_BYTES, PAYLOAD_BYTES, PullReceiver,
    _FRAME, _HOLD_REASONS, _array, _digest, _integer, _match, _need, _object)
from mc.agent_mail_pull_schema import _base, _validate
from mc.agent_mail_service_manifest import ManifestRefused, ServiceKeyCandidate
from mc.agent_mail_signup import H, I, ID, ORIGIN, SignupTransport, _json
from mc.agent_mail_signing import IngressRefused, SKEW_MS, unbase64url
from mc.agent_mail_store import MailStore, MailStoreError
from mc.core import _log

PROTOCOL = 'clayrune-mail-pull.v1'
TICKET = '/v1/pull/ticket'
WINDOW = 524288
ERRORS = {
    'invalid_request', 'invalid_json', 'invalid_hold', 'unsupported_frame', 'request_limit',
    'abuse_limit', 'unauthorized', 'ticket_invalid', 'version_unsupported', 'nonce_replayed',
    'operation_conflict', 'body_limit', 'body_timeout', 'not_found', 'storage_unavailable',
    'service_unavailable', 'connection_superseded', 'stale_generation', 'registration_inactive',
    'registration_frozen', 'fenced', 'revoked', 'ack_conflict', 'lease_unknown', 'unknown_thread',
    'event_expired', 'queue_full', 'frame_limit', 'reauth_due', 'service_key_changed',
}
HALT = {'connection_superseded', 'stale_generation', 'registration_inactive',
        'registration_frozen', 'fenced', 'revoked', 'unauthorized', 'vault_locked',
        'service_key_changed', 'local_disabled', 'frame_unverified', 'frame_scope',
        'response_unverified', 'protocol_failure', 'ack_conflict', 'schema_corrupt'}
_CLAIMS: set[str] = set()
_CLAIMS_LOCK = threading.Lock()


class _Failure(Exception):
    def __init__(self, reason: str, *, halt: bool = False, retry_ms: int = 0):
        self.reason, self.halt, self.retry_ms = reason, halt, retry_ms
        super().__init__(reason)


class _BoundedBuffer(frame_buffer):
    """Bound wire allocation BEFORE websocket-client reads a frame payload."""

    def recv_length(self) -> None:
        super().recv_length()
        _need(self.length is not None and self.length <= FRAME_BYTES, 'frame_limit')


class _Socket(websocket.WebSocket):
    def __init__(self):
        super().__init__(enable_multithread=True)
        # websocket-client 1.9 (already required) exposes the frame buffer used
        # by recv_frame. We assemble fragments ourselves with a message cap.
        # Upstream annotates recv_fn as returning int; it actually consumes bytes.
        self.frame_buffer = _BoundedBuffer(cast(Any, self._recv), False)

    def abort(self) -> None:
        # Upstream only aborts after connected=True. Also interrupt an in-flight
        # upgrade once its TCP/TLS socket exists; DNS may still be pending.
        if self.sock is not None:
            self.sock.shutdown(socket.SHUT_RDWR)


class PullClient:
    def __init__(self, store: MailStore, seal: IdentitySeal, transport: SignupTransport, *,
                 install_id: str, reviewed_service: Callable[[], tuple[ServiceKeyCandidate, str]],
                 socket_factory: Callable[[], Any] = _Socket,
                 monotonic: Callable[[], float] = time.monotonic,
                 jitter: Callable[[float, float], float] = random.uniform):
        _need(_match(I, install_id), 'invalid_scope')
        self.store, self.seal, self.transport = store, seal, transport
        self.install_id, self.reviewed_service = install_id, reviewed_service
        self.socket_factory, self.monotonic, self.jitter = socket_factory, monotonic, jitter
        self._mutex = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: Any = None
        self._file: BinaryIO | None = None
        self._claimed = False
        self._claim = install_id
        self._reason = ''
        self._epoch = 0
        self._row: dict[str, Any] = {}
        self._seen: OrderedDict[str, str] = OrderedDict()
        self._parts = bytearray()
        self._fragmented = False
        self._sent_acks: set[str] = set()
        self._requests: OrderedDict[str, str] = OrderedDict()
        self._minute = -1
        self._frames = self._resumes = 0

    def _now(self) -> int:
        return int(self.store.clock() * 1000)

    @property
    def running(self) -> bool:
        with self._mutex:
            return self._thread is not None and self._thread.is_alive()

    @property
    def reason(self) -> str:
        with self._mutex:
            return self._reason

    def _notice(self, reason: str) -> None:
        # Reasons are code-owned. Never interpolate exception/wire/HTTP text.
        with self._mutex:
            self._reason = reason
        _log(f'[agent-mail] pull_client {reason}', flush=True)

    def _claim_install(self) -> None:
        with _CLAIMS_LOCK:
            if self._claim in _CLAIMS:
                raise _Failure('client_already_running', halt=True)
            _CLAIMS.add(self._claim)
            self._claimed = True
        try:
            self._file = open(self.store.path.with_name(
                self.store.path.name + '.' + self.install_id + '.pull.lock'), 'a+b')
            if os.name == 'nt':
                import msvcrt
                # Windows byte-range locks may extend beyond EOF.
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._release_install()
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise _Failure('client_already_running', halt=True) from None
            raise

    def _release_install(self) -> None:
        try:
            if self._file is not None:
                self._file.close()  # Closing releases the OS lock, including on crash.
        finally:
            self._file = None
            if self._claimed:
                with _CLAIMS_LOCK:
                    _CLAIMS.discard(self._claim)
                self._claimed = False

    def start(self) -> None:
        """Explicit supervisor action; duplicate starts fail before any network."""
        with self._mutex:
            if self.running:
                self._notice('client_already_running')
                raise _Failure('client_already_running', halt=True)
            try:
                self._claim_install()
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, name='agent-mail-pull', daemon=True)
                self._thread.start()
            except Exception:
                self._release_install()
                self._notice('start_failed')
                raise

    def stop(self, timeout: float = 12) -> bool:
        """Wake recv/backoff from another thread; return whether shutdown finished.

        HTTP/connect uses the supplied transport timeouts. DNS/HTTP may still
        be pending; False retains the exclusion lock until the worker exits.
        """
        self._stop.set()
        with self._mutex:
            ws, thread = self._socket, self._thread
        if ws is not None:
            try:
                ws.abort()
            except Exception:
                self._notice('cancel_wakeup_failed')
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        stopped = thread is None or not thread.is_alive()
        if not stopped:
            self._notice('cancellation_pending')
        return stopped

    def _account(self, db: Any) -> dict[str, Any]:
        _base(db)
        _validate(db)
        r = db.execute('''SELECT a.*,g.state AS generation_state,g.service_key_id,
            g.service_public_key,g.manifest_sequence,g.manifest_digest,
            g.owner_generation AS generation_owner,g.key_id AS generation_key,
            g.seal_flow_id AS generation_seal FROM mail_pull_accounts a
            JOIN mail_pull_generations g ON g.install_id=a.install_id AND g.generation=a.generation
            WHERE a.install_id=?''', (self.install_id,)).fetchone()
        _need(r is not None, 'local_disabled')
        a = dict(r)
        _need(a['state'] == 'active' and a['local_enabled'] == 1
              and a['generation_state'] == 'current', 'local_disabled')
        _need(_integer(a['generation'], 1, 10**15 - 1)
              and _integer(a['owner_generation'], 1, 10**15 - 1)
              and a['owner_generation'] == a['generation_owner']
              and a['key_id'] == a['generation_key'] and _match(ID, a['key_id'])
              and a['seal_flow_id'] == a['generation_seal'] and _match(I, a['seal_flow_id']), 'schema_corrupt')
        _need(not db.execute('''SELECT 1 FROM mail_pull_fences WHERE install_id=? AND generation=?
            AND (mode='revoke' OR flow_id=?)''',
            (self.install_id, a['generation'], a['seal_flow_id'])).fetchone(), 'fenced')
        if self._row:
            _need(all(a[k] == self._row[k] for k in ('generation', 'owner_generation', 'key_id',
                'seal_flow_id', 'service_origin', 'consent_epoch', 'manifest_digest', 'manifest_sequence',
                'service_key_id', 'service_public_key')), 'stale_generation')
        return a

    def _authority(self) -> ServiceKeyCandidate:
        with self.store._read() as db:
            a = self._account(db)
        pin, digest = self.reviewed_service()  # May CAS manifest floor; outside SQLite tx.
        _need(_match(ORIGIN, pin.origin) and len(pin.origin) <= 261 and _match(ID, pin.key_id)
              and _match(H, digest) and _integer(pin.sequence, 1, 10**15 - 1)
              and self._now() < pin.expires_at_ms and
              (pin.origin, pin.key_id, pin.public_key, pin.sequence, digest) ==
              tuple(a[k] for k in ('service_origin', 'service_key_id', 'service_public_key',
                                   'manifest_sequence', 'manifest_digest')), 'service_key_changed')
        reason = self.seal.status(self._ref(a))
        _need(reason == 'ready', reason)
        if self._stop.is_set():
            raise _Failure('stopped', halt=True)
        self._row = a
        return pin

    @staticmethod
    def _ref(a: dict[str, Any]) -> SealReference:
        return SealReference(a['seal_flow_id'], a['install_id'], a['generation'], a['key_id'])

    def _ticket(self, pin: ServiceKeyCandidate) -> str:
        a = self._row
        op, nonce, stamp = secrets.token_hex(16), secrets.token_hex(16), str(self._now())
        body = _json({'op_id': op})  # New operation on EVERY attempt: tickets cannot replay.
        fields = {'version': '2', 'direction': 'install-to-pull', 'key-id': a['key_id'],
                  'install-id': self.install_id, 'generation': str(a['generation']),
                  'owner-generation': str(a['owner_generation']), 'timestamp': stamp,
                  'nonce': nonce, 'digest': _digest(body)}
        transcript = _array('clayrune-agent-mail-pull-http', '1', fields['direction'], pin.origin,
            'POST', TICKET, a['key_id'], self.install_id, a['generation'], a['owner_generation'],
            stamp, nonce, fields['digest'])
        headers = {'x-mail-' + k: v for k, v in fields.items()}
        headers.update({'content-type': 'application/json', 'cache-control': 'no-store',
            'x-content-type-options': 'nosniff', 'x-mail-signature': self.seal.sign_pull(self._ref(a), transcript)})
        status, supplied, raw = self.transport('POST', TICKET, headers, body)
        try:
            _need(type(status) is int and isinstance(supplied, Mapping)
                  and isinstance(raw, bytes) and len(raw) <= 32768, 'response_unverified')
            h: dict[str, str] = {}
            for k, v in supplied.items():
                _need(isinstance(k, str) and isinstance(v, str) and k.isascii() and v.isascii()
                      and k.lower() not in h, 'response_unverified')
                h[k.lower()] = v
            _need(sum(len(k) + len(v) + 4 for k, v in h.items()) <= 8192
                  and 'content-encoding' not in h and re.fullmatch(
                      r'application/json(?:;\s*charset=utf-8)?', h.get('content-type', ''), re.I), 'response_unverified')
            expected = dict(fields, direction='pull-to-install', **{'key-id': pin.key_id, 'digest': _digest(raw)})
            del expected['timestamp']
            _need(all(h.get('x-mail-' + k) == v for k, v in expected.items()), 'response_unverified')
            when = h.get('x-mail-timestamp', '')
            _need(when.isascii() and when.isdecimal() and str(int(when)) == when
                  and _integer(int(when)) and abs(self._now() - int(when)) <= SKEW_MS
                  and self._now() < pin.expires_at_ms, 'response_unverified')
            proof = _array('clayrune-agent-mail-pull-http', '1', 'pull-to-install', pin.origin,
                'POST', TICKET, pin.key_id, self.install_id, a['generation'], a['owner_generation'],
                when, nonce, status, _digest(raw))
            Ed25519PublicKey.from_public_bytes(unbase64url(pin.public_key, 32)).verify(
                unbase64url(h.get('x-mail-signature', ''), 64), proof)
            value = _object(raw)
            _need(type(value.get('v')) is int and value['v'] == 1 and value.get('request_id') == op,
                  'response_unverified')
            if status == 200:
                _need(value.keys() == {'v', 'code', 'request_id', 'ticket', 'expires_ms', 'protocol'}
                      and value['code'] == 'ok' and _match(H, value['ticket']) and value['protocol'] == PROTOCOL
                      and _integer(value['expires_ms']) and self._now() < value['expires_ms'] <= int(when) + 60000,
                      'response_unverified')
            else:
                allowed = {400: {'invalid_request', 'invalid_json'}, 401: {'unauthorized', 'ticket_invalid'},
                    404: {'not_found'}, 408: {'body_timeout'}, 409: {'nonce_replayed', 'operation_conflict'},
                    413: {'body_limit'}, 426: {'version_unsupported'},
                    429: {'request_limit', 'abuse_limit'}, 503: {'storage_unavailable', 'service_unavailable'}}
                _need(status in allowed and value.get('code') in allowed[status] and value.keys() in
                      ({'v', 'code', 'request_id'}, {'v', 'code', 'request_id', 'retry_after_ms'}), 'response_unverified')
                retry = value.get('retry_after_ms', 0)
                _need(_integer(retry) and ('retry_after_ms' not in value or status in (429, 503)), 'response_unverified')
                # Retry-After is meaningful only with verified signed failure evidence.
                delay = h.get('retry-after')
                if delay is not None:
                    if delay.isascii() and delay.isdecimal() and len(delay) <= 9:
                        retry = max(retry, self._now() + int(delay) * 1000)
                    else:
                        date = parsedate_to_datetime(delay)
                        _need(date.tzinfo is not None, 'response_unverified')
                        retry = max(retry, int(date.timestamp() * 1000))
                raise _Failure(value['code'], halt=value['code'] in HALT or status == 426, retry_ms=retry)
        except (ValueError, TypeError, KeyError, InvalidSignature, RecursionError, IngressRefused):
            raise _Failure('response_unverified', halt=True) from None
        self._authority()  # Recheck local consent, vault and reviewed pin after HTTP.
        return value['ticket']

    def _verified(self, raw: bytes, pin: ServiceKeyCandidate) -> tuple[dict[str, Any], dict[str, Any]]:
        _need(len(raw) <= FRAME_BYTES, 'frame_limit')
        f = _object(raw)
        _need(isinstance(f.get('type'), str) and f['type'] in
              {'hello', 'event', 'hold', 'ack_committed', 'status', 'wake', 'reauth_required', 'error'},
              'protocol_failure')
        _need('signature' in f, 'protocol_failure')
        _need(f.keys() == _FRAME and type(f['v']) is int and f['v'] == 1 and
              _match(I, f['frame_id']) and _match(H, f['payload_digest'])
              and all(_integer(f[k], 1, 10**15 - 1) for k in ('generation', 'owner_generation', 'epoch'))
              and _integer(f['issued_ms']), 'frame_unverified')
        _need((f['install_id'], f['generation'], f['owner_generation']) ==
              (self.install_id, self._row['generation'], self._row['owner_generation'])
              and (self._epoch == 0 and f['type'] == 'hello' or f['epoch'] == self._epoch), 'frame_scope')
        _need(f['key_id'] == pin.key_id and self._now() < pin.expires_at_ms
              and abs(self._now() - f['issued_ms']) <= SKEW_MS, 'frame_unverified')
        encoded = f['payload']
        _need(isinstance(encoded, str) and len(encoded) <= (PAYLOAD_BYTES * 4 + 2) // 3, 'frame_limit')
        # unbase64url enforces exact decoded length and canonical encoding.
        size = len(encoded) * 3 // 4
        payload = unbase64url(encoded, size)
        _need(len(payload) <= PAYLOAD_BYTES and _digest(payload) == f['payload_digest'], 'frame_unverified')
        try:
            Ed25519PublicKey.from_public_bytes(unbase64url(pin.public_key, 32)).verify(
                unbase64url(f['signature'], 64), _array('clayrune-agent-mail-pull-frame', '1', pin.origin,
                    f['type'], pin.key_id, self.install_id, self._row['generation'], self._row['owner_generation'],
                    f['epoch'], f['frame_id'], f['issued_ms'], f['payload_digest']))
        except InvalidSignature:
            raise IngressRefused('protocol_failure' if f['type'] == 'wake' else 'frame_unverified') from None
        return f, _object(payload)

    def _current(self, db: Any) -> None:
        self._account(db)
        c = db.execute('SELECT * FROM mail_pull_connections WHERE install_id=?', (self.install_id,)).fetchone()
        _need(c is not None and c['epoch'] == self._epoch and c['connection_id'] == self._connection
              and c['generation'] == self._row['generation'] and c['owner_generation'] == self._row['owner_generation']
              and self._now() < c['reauth_ms'], 'stale_generation')

    def _hello(self, f: dict[str, Any], p: dict[str, Any]) -> None:
        _need(self._epoch == 0 and p.keys() == {'connection_id', 'authenticated_ms', 'reauth_ms', 'max_events', 'max_bytes'}
              and _match(I, p['connection_id']) and _integer(p['authenticated_ms']) and _integer(p['reauth_ms'])
              and abs(self._now() - p['authenticated_ms']) <= SKEW_MS
              and self._now() < p['reauth_ms'] <= p['authenticated_ms'] + 86400000
              and type(p['max_events']) is int and p['max_events'] == 10
              and type(p['max_bytes']) is int and p['max_bytes'] == WINDOW, 'protocol_failure')
        with self.store._tx() as db:
            self._account(db)
            old = db.execute('SELECT epoch FROM mail_pull_connections WHERE install_id=?', (self.install_id,)).fetchone()
            _need(old is None or f['epoch'] > old['epoch'], 'connection_superseded')
            db.execute('''INSERT INTO mail_pull_connections VALUES(?,?,?,?,?,?,?,0,0,?,0,0,'')
                ON CONFLICT(install_id) DO UPDATE SET generation=excluded.generation,
                owner_generation=excluded.owner_generation,epoch=excluded.epoch,connection_id=excluded.connection_id,
                authenticated_ms=excluded.authenticated_ms,reauth_ms=excluded.reauth_ms,
                scan_after_seq=0,scan_complete=0,last_activity_ms=excluded.last_activity_ms,reason='' ''',
                (self.install_id, self._row['generation'], self._row['owner_generation'], f['epoch'],
                 p['connection_id'], p['authenticated_ms'], p['reauth_ms'], self._now()))
        self._epoch, self._connection = f['epoch'], p['connection_id']
        self._reauth = p['reauth_ms']
        self._connected_at = self.monotonic()
        self._receiver = PullReceiver(self.store, install_id=self.install_id,
            generation=self._row['generation'], owner_generation=self._row['owner_generation'],
            epoch=self._epoch, reviewed_service=self.reviewed_service)
        self._replay()
        self._resume(0)

    def _budget(self, resume: bool = False) -> bool:
        minute = self._now() // 60000
        if minute != self._minute:
            self._minute, self._frames, self._resumes = minute, 0, 0
        # Reserve headroom for holds and the first resume. Avoid a reconnect loop
        # on a large durable outbox: remaining acks drain on subsequent ticks.
        return self._frames < 110 and (not resume or self._resumes < 55)

    def _send(self, kind: str, payload: dict[str, Any], request: str | None = None) -> str:
        self._authority()
        with self.store._read() as db:
            self._current(db)
        _need(self._budget(kind == 'resume'), 'request_limit')
        request = request or secrets.token_hex(16)
        frame = dict(v=1, type=kind, install_id=self.install_id, generation=self._row['generation'],
            owner_generation=self._row['owner_generation'], epoch=self._epoch, request_id=request, payload=payload)
        self._socket.send(_json(frame).decode('ascii'))
        self._frames += 1
        self._resumes += int(kind == 'resume')
        self._requests[request] = kind
        while len(self._requests) > 1024:
            self._requests.popitem(last=False)
        return request

    def _resume(self, after: int) -> None:
        _need(self._resume_request is None, 'protocol_failure')
        if after == 0:
            self._wake_pending = False
        self._resume_request = self._send('resume', dict(after_seq=after, limit=10, available_bytes=WINDOW))

    def _replay(self) -> None:
        with self.store._read() as db:
            self._current(db)
            rows = db.execute('''SELECT o.* FROM mail_pull_ack_outbox o JOIN mail_pull_receipts r USING(event_id)
                WHERE r.install_id=? AND r.generation=? AND r.owner_generation=?
                AND r.disposition='local_received' AND o.next_attempt_ms<=? ORDER BY r.cloud_seq LIMIT 100''',
                (self.install_id, self._row['generation'], self._row['owner_generation'], self._now())).fetchall()
        for r in rows:
            if r['event_id'] in self._sent_acks or not self._budget() or self._frames >= 109:
                continue
            payload = {k: r[k] for k in ('event_id', 'event_digest', 'body_digest', 'lease_id', 'local_receipt_id')}
            _need(all(_match(H, payload[k]) for k in ('event_id', 'event_digest', 'body_digest'))
                  and _match(I, payload['lease_id']) and _match(I, payload['local_receipt_id']), 'schema_corrupt')
            self._send('custody_ack', payload, r['local_receipt_id'])
            self._sent_acks.add(r['event_id'])

    def _committed(self, p: dict[str, Any]) -> None:
        _need(p.keys() == {'request_id', 'event_id', 'event_digest', 'body_digest', 'local_receipt_id',
              'receipt_id', 'committed_ms'} and all(_match(I, p[k]) for k in ('request_id', 'local_receipt_id', 'receipt_id'))
              and all(_match(H, p[k]) for k in ('event_id', 'event_digest', 'body_digest'))
              and p['request_id'] == p['local_receipt_id'] and _integer(p['committed_ms'])
              and p['committed_ms'] <= self._now() + SKEW_MS, 'protocol_failure')
        with self.store._tx() as db:
            self._settlement_scope(db)
            r = db.execute('SELECT * FROM mail_pull_receipts WHERE event_id=?', (p['event_id'],)).fetchone()
            _need(r is not None and (r['install_id'], r['generation'], r['owner_generation']) ==
                  (self.install_id, self._row['generation'], self._row['owner_generation']) and
                  all(r[k] == p[k] for k in ('event_digest', 'body_digest', 'local_receipt_id'))
                  and r['committed_ms'] <= p['committed_ms'] + SKEW_MS
                  and ((r['cloud_receipt_id'] is None and r['cloud_committed_ms'] is None)
                       or (r['cloud_receipt_id'], r['cloud_committed_ms']) ==
                       (p['receipt_id'], p['committed_ms'])), 'ack_conflict')
            assert r is not None
            o = db.execute('SELECT * FROM mail_pull_ack_outbox WHERE event_id=?', (p['event_id'],)).fetchone()
            # A first proof needs durable ack intent. Exact settled duplicates
            # remain idempotent after that intent has been retired.
            _need((o is not None and all(o[k] == p[k] for k in
                  ('event_digest', 'body_digest', 'local_receipt_id')))
                  or (o is None and r['cloud_receipt_id'] == p['receipt_id']
                      and r['cloud_committed_ms'] == p['committed_ms']), 'ack_conflict')
            db.execute('UPDATE mail_pull_receipts SET cloud_receipt_id=?,cloud_committed_ms=? WHERE event_id=?',
                       (p['receipt_id'], p['committed_ms'], p['event_id']))
            db.execute('DELETE FROM mail_pull_ack_outbox WHERE event_id=?', (p['event_id'],))
        self._sent_acks.discard(p['event_id'])

    def _settlement_scope(self, db: Any) -> None:
        """Bind positive evidence to the original scope, never grant authority.

        Only _committed uses this gate, after _verified authenticates the frame.
        Local denial, a changed consent epoch or a fence cannot erase positive
        in-flight evidence. All new custody and sends still require _current.
        """
        _base(db)
        _validate(db)
        a = db.execute('''SELECT a.generation,a.owner_generation,a.key_id,a.seal_flow_id,
            a.service_origin,g.manifest_sequence,g.manifest_digest,g.service_key_id,g.service_public_key
            FROM mail_pull_accounts a JOIN mail_pull_generations g
            ON g.install_id=a.install_id AND g.generation=a.generation
            AND g.owner_generation=a.owner_generation AND g.key_id=a.key_id AND g.seal_flow_id=a.seal_flow_id
            WHERE a.install_id=?''', (self.install_id,)).fetchone()
        _need(a is not None and all(a[k] == self._row[k] for k in
              ('generation', 'owner_generation', 'key_id', 'seal_flow_id', 'service_origin',
               'manifest_sequence', 'manifest_digest', 'service_key_id', 'service_public_key')), 'stale_generation')
        c = db.execute('SELECT * FROM mail_pull_connections WHERE install_id=?', (self.install_id,)).fetchone()
        _need(c is not None and c['epoch'] == self._epoch and c['connection_id'] == self._connection
              and c['generation'] == self._row['generation'] and c['owner_generation'] == self._row['owner_generation']
              and self._now() < c['reauth_ms'], 'stale_generation')

    def _handle(self, raw: bytes, pin: ServiceKeyCandidate) -> None:
        f, p = self._verified(raw, pin)
        # Authenticate even duplicate frame ids, then compare exact wire digest.
        old = self._seen.get(f['frame_id'])
        if old is not None:
            _need(old == _digest(raw), 'frame_unverified')
            return
        self._seen[f['frame_id']] = _digest(raw)
        if len(self._seen) > 1024:
            self._seen.popitem(last=False)
        kind = f['type']
        if kind == 'hello':
            self._hello(f, p)
            return
        _need(self._epoch != 0, 'protocol_failure')
        if kind == 'event':
            # Receiver re-verifies the event AND current custody authority inside
            # its transaction. Its return is unreachable before durable commit.
            try:
                ack = self._receiver.receive(raw)
            except IngressRefused as exc:
                reason = str(exc)
                if reason not in _HOLD_REASONS:
                    raise
                self._notice(reason)
                e = p['event']
                self._send('hold', dict(event_id=e['event_id'], event_digest=e['event_digest'],
                    lease_id=p['lease_id'], reason=reason, retry_after_ms=min(e['expires_ms'], self._now() + 60000)))
                return
            _need(ack is not None, 'protocol_failure')
            assert ack is not None
            self._send('custody_ack', ack['payload'], ack['request_id'])
            self._sent_acks.add(ack['payload']['event_id'])
        elif kind == 'ack_committed':
            self._committed(p)
        elif kind == 'hold':
            self._receiver.receive(raw)
        elif kind == 'wake':
            _need(not p, 'protocol_failure')
            # Hints never carry custody or paging state. Coalesce while a page
            # is in flight; finish that scan before starting again from zero.
            self._wake_pending = True
        elif kind == 'status':
            _need(p.keys() == {'request_id', 'next_seq', 'scan_complete', 'pending_count', 'oldest_expires_ms'}
                  and _match(I, p['request_id']) and p['request_id'] in self._requests
                  and _integer(p['next_seq']) and type(p['scan_complete']) is bool and _integer(p['pending_count'])
                  and (p['oldest_expires_ms'] is None or _integer(p['oldest_expires_ms'])), 'protocol_failure')
            with self.store._tx() as db:
                self._current(db)
                db.execute('''UPDATE mail_pull_connections SET scan_after_seq=?,scan_complete=?,last_activity_ms=?
                    WHERE install_id=? AND epoch=?''',
                    (p['next_seq'], int(p['scan_complete']), self._now(), self.install_id, self._epoch))
            self._page = None if p['scan_complete'] else p['next_seq']
            if p['request_id'] == self._resume_request:
                self._resume_request = None
        elif kind == 'reauth_required':
            _need(p.keys() == {'reason', 'close_at_ms'} and p['reason'] in {'reauth_due', 'service_key_changed'}
                  and _integer(p['close_at_ms']) and p['close_at_ms'] <= self._reauth, 'protocol_failure')
            raise _Failure(p['reason'], halt=p['reason'] == 'service_key_changed')
        else:
            _need(p.keys() == {'request_id', 'code', 'retry_after_ms'} and _match(I, p['request_id'])
                  and p['code'] in ERRORS and (p['retry_after_ms'] is None or _integer(p['retry_after_ms'])),
                  'protocol_failure')
            raise _Failure(p['code'], halt=p['code'] in HALT, retry_ms=p['retry_after_ms'] or 0)

    def _receive(self) -> bytes | None:
        frame = self._socket.recv_frame()
        _need(not frame.mask_value and not any((frame.rsv1, frame.rsv2, frame.rsv3)), 'protocol_failure')
        opcode, data = frame.opcode, frame.data
        if opcode == websocket.ABNF.OPCODE_CLOSE:
            code = struct.unpack('!H', data[:2])[0] if len(data) >= 2 else 1000
            reasons = {1000: 'connection_closed', 1009: 'frame_limit', 1013: 'service_unavailable',
                4001: 'connection_superseded', 4002: 'protocol_failure', 4003: 'unauthorized', 4004: 'reauth_due'}
            reason = reasons.get(code, 'connection_lost')
            raise _Failure(reason, halt=code in (1009, 4001, 4002, 4003))
        if opcode in (websocket.ABNF.OPCODE_PING, websocket.ABNF.OPCODE_PONG):
            _need(frame.fin and len(data) <= 125, 'protocol_failure')
            if opcode == websocket.ABNF.OPCODE_PING:
                self._socket.pong(data)
            elif data == self._ping:
                self._ping_due = None
            return None
        _need(opcode in (websocket.ABNF.OPCODE_TEXT, websocket.ABNF.OPCODE_CONT)
              and (opcode == websocket.ABNF.OPCODE_CONT) == self._fragmented, 'protocol_failure')
        _need(len(self._parts) + len(data) <= FRAME_BYTES, 'frame_limit')
        self._parts.extend(data)
        self._fragmented = not bool(frame.fin)
        if not frame.fin:
            return None
        raw = bytes(self._parts)
        self._parts.clear()
        return raw

    def _session(self) -> None:
        self._epoch = 0
        self._seen.clear()
        self._sent_acks.clear()
        self._requests.clear()
        self._parts.clear()
        self._fragmented = False
        self._connected_at = None
        self._page: int | None = None
        self._resume_request: str | None = None
        self._wake_pending = False
        self._row = {}
        pin = self._authority()
        if not self._budget(True):
            raise _Failure('request_limit', retry_ms=(self._now() // 60000 + 1) * 60000)
        ticket = self._ticket(pin)
        ws = self.socket_factory()
        with self._mutex:
            self._socket = ws
        try:
            if self._stop.is_set():
                return
            ws.connect('wss' + pin.origin[5:] + '/v1/pull/socket',
                header={'Authorization': 'MailTicket ' + ticket}, subprotocols=[PROTOCOL],
                suppress_origin=True, redirect_limit=0, timeout=10, http_proxy_timeout=10)
            del ticket
            _need(ws.getstatus() == 101 and ws.getsubprotocol() == PROTOCOL, 'protocol_failure')
            ws.settimeout(1)
            hello_deadline = self.monotonic() + 10
            self._connected_at: float | None = None
            next_ping = self.monotonic() + 60
            self._ping, self._ping_due = b'', None
            while not self._stop.is_set():
                pin = self._authority()
                now = self.monotonic()
                if not self._epoch and now >= hello_deadline:
                    raise _Failure('hello_timeout')
                if self._epoch and self._now() >= self._reauth:
                    raise _Failure('reauth_due')
                if self._ping_due is not None and now >= self._ping_due:
                    raise _Failure('pong_timeout')
                if self._epoch and now >= next_ping:
                    self._ping = secrets.token_bytes(8)
                    ws.ping(self._ping)
                    self._ping_due, next_ping = now + 30, now + 60
                try:
                    raw = self._receive()
                except websocket.WebSocketTimeoutException:
                    raw = None
                if raw is not None:
                    try:
                        self._handle(raw, pin)
                    except (IngressRefused, EnrollmentRefused):
                        raise
                    except (TypeError, KeyError, ValueError, UnicodeError, RecursionError):
                        raise IngressRefused('frame_unverified') from None
                if self._epoch:
                    self._replay()
                    if self._resume_request is None and self._budget(True) and (
                            self._page is not None or self._wake_pending):
                        cursor, self._page = self._page if self._page is not None else 0, None
                        self._resume(cursor)
        except (IngressRefused, EnrollmentRefused, _Failure) as exc:
            reason = exc.reason if isinstance(exc, _Failure) else str(exc)
            code = 1009 if reason == 'frame_limit' else 4001 if reason == 'connection_superseded' else (
                4004 if reason in ('reauth_due', 'service_key_changed') else
                4003 if reason in HALT - {'protocol_failure', 'frame_scope', 'frame_unverified'} else 4002)
            closed_reason = {1009: 'frame_limit', 4001: 'connection_superseded', 4002: 'invalid_request',
                             4003: 'unauthorized', 4004: 'reauth_due'}[code]
            if ws.connected and not self._stop.is_set():
                try:
                    ws.send_close(status=code, reason=closed_reason.encode('ascii'))
                except Exception:
                    self._notice('close_frame_failed')
            raise
        finally:
            try:
                ws.shutdown()  # No blocking close handshake; cancellation wakes recv.
            except Exception:
                self._notice('socket_close_failed')
            with self._mutex:
                self._socket = None
            if self._epoch:
                try:
                    with self.store._tx() as db:
                        _validate(db)
                        db.execute('''UPDATE mail_pull_connections SET scan_complete=0
                            WHERE install_id=? AND epoch=? AND connection_id=?''',
                            (self.install_id, self._epoch, self._connection))
                except Exception:
                    self._notice('disconnect_state_failed')

    def _backoff(self, failures: int, retry_ms: int = 0) -> float:
        ceiling = min(300.0, 2.0 ** min(max(0, failures - 1), 9))
        delay = self.jitter(0.0, ceiling)
        _need(isinstance(delay, (int, float)) and 0 <= delay <= ceiling, 'invalid_backoff')
        return max(delay, (retry_ms - self._now()) / 1000, 0)

    def _record(self, failures: int, reason: str, delay: float) -> None:
        with self.store._tx() as db:
            # Do not recreate missing schema or overwrite changed lifecycle rows.
            _validate(db)
            db.execute('''UPDATE mail_pull_connections SET failures=?,next_attempt_ms=?,reason=?,scan_complete=0
                WHERE install_id=? AND generation=? AND owner_generation=? AND (?=0 OR epoch=?)''',
                (failures, self._now() + int(delay * 1000), reason, self.install_id,
                 self._row.get('generation', 0), self._row.get('owner_generation', 0), self._epoch, self._epoch))

    def _run(self) -> None:
        failures = 0
        try:
            while not self._stop.is_set():
                self._connected_at = None
                try:
                    self._session()
                    failure = _Failure('stopped', halt=True)
                except _Failure as exc:
                    failure = exc
                except (IngressRefused, EnrollmentRefused) as exc:
                    reason = str(exc)
                    # Do not leak arbitrary adapter strings, even typed exceptions.
                    safe = reason if reason in ERRORS | HALT | _HOLD_REASONS else 'local_refused'
                    failure = _Failure(safe, halt=True)
                except ManifestRefused:
                    failure = _Failure('service_key_changed', halt=True)
                except MailStoreError:
                    failure = _Failure('storage_unavailable', halt=True)
                except (websocket.WebSocketProtocolException, websocket.WebSocketPayloadException):
                    failure = _Failure('protocol_failure', halt=True)
                except Exception:
                    failure = _Failure('connection_lost')
                if self._stop.is_set():
                    self._notice('stopped')
                    break
                self._notice(failure.reason)
                failures = 1 if self._connected_at is not None and self.monotonic() - self._connected_at >= 60 else failures + 1
                delay = self._backoff(failures, failure.retry_ms)
                try:
                    self._record(failures, failure.reason, delay)
                except Exception:
                    self._notice('state_write_failed')
                    break
                if failure.halt or self._stop.wait(delay):
                    break
        except Exception:
            self._notice('worker_failed')
        finally:
            try:
                self._release_install()
            except Exception:
                self._notice('lock_release_failed')
