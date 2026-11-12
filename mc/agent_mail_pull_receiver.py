"""Private pull custody receiver (MC-1166 unit 17); no socket or execution.

Composition supplies the SAME guarded, checked MailStore, current connection
scope and reviewed_service callback used by Signup. The callback revalidates
root proof, durable manifest floor and human pin review on every call. A signed
hello/current connection must already be durable; unit 18 owns frame-id caches,
ack replay and socket sequencing. Returned custody_ack is ready only AFTER the
single custody transaction commits. A service hold returns None, never an ack.
Mail text is stored as untrusted data; it is never interpreted here.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import sqlite3
from typing import Any, Callable, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mc.agent_mail_envelope import MAX_TEXT_BYTES, _MID, _RESULT
from mc.agent_mail_pull_schema import _base, _validate
from mc.agent_mail_service_manifest import ServiceKeyCandidate
from mc.agent_mail_signing import HEX, ID, IngressRefused, SKEW_MS, unbase64url
from mc.agent_mail_store import (BLOCKED, LOCAL_RECEIVED, SUBMITTED, TERMINAL_STATES,
                                 MailStore, _bounded_digest)

FRAME_BYTES = 160 * 1024
PAYLOAD_BYTES = 112 * 1024
MAX_INT = 2**53 - 1
MONTH_MS = 30 * 86400 * 1000
_I = re.compile(r'[a-f0-9]{32}')
_ORIGIN = re.compile(r'https://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?')
_FRAME = {'v', 'type', 'install_id', 'generation', 'owner_generation', 'epoch',
          'frame_id', 'issued_ms', 'key_id', 'payload', 'payload_digest', 'signature'}
_EVENT = {'v', 'event_id', 'install_id', 'generation', 'owner_generation', 'thread_id',
          'seq', 'incoming_message_id', 'body_digest', 'event_digest', 'received_ms',
          'expires_ms', 'thread_expires_ms', 'auth', 'kind', 'text', 'reason'}
_HOLD_REASONS = {'unknown_thread', 'queue_full', 'storage_unavailable', 'local_disabled',
                 'vault_locked', 'conversation_busy'}


def _need(ok: object, reason: str = 'frame_unverified') -> None:
    if not ok:
        raise IngressRefused(reason)


def _match(pattern: re.Pattern[str], value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _integer(value: Any, low: int = 0, high: int = MAX_INT) -> bool:
    return type(value) is int and low <= value <= high


def _array(*values: Any) -> bytes:
    return json.dumps([str(v) for v in values], separators=(',', ':')).encode('ascii')


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _object(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value = dict(items)
        _need(len(value) == len(items))
        return value

    def number(value: str) -> int:
        _need(value != '-0')
        result = int(value)
        _need(_integer(result))
        return result

    def invalid(value: str) -> None:
        raise IngressRefused('frame_unverified')

    def bounded(value: Any, depth: int = 1) -> None:
        _need(depth <= 8)
        if isinstance(value, dict):
            for k, v in value.items():
                k.encode('utf-8')
                bounded(v, depth + 1)
        elif isinstance(value, list):
            _need(len(value) <= 100)
            for v in value:
                bounded(v, depth + 1)
        elif isinstance(value, str):
            value.encode('utf-8')  # Reject lone surrogates, including escaped ones.

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_int=number,
                           parse_float=invalid, parse_constant=invalid)
        _need(type(value) is dict)
        bounded(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise IngressRefused('frame_unverified') from None


class PullReceiver:
    def __init__(self, store: MailStore, *, install_id: str, generation: int,
                 owner_generation: int, epoch: int,
                 reviewed_service: Callable[[], tuple[ServiceKeyCandidate, str]]):
        _need(_match(_I, install_id) and all(_integer(v, 1, 10**15 - 1)
              for v in (generation, owner_generation, epoch)), 'invalid_scope')
        self.store, self.install_id = store, install_id
        self.generation, self.owner_generation, self.epoch = generation, owner_generation, epoch
        self.reviewed_service = reviewed_service

    def _now(self) -> int:
        return int(self.store.clock() * 1000)

    def _scope(self, db: sqlite3.Connection, pin: ServiceKeyCandidate, digest: str) -> None:
        # Consent and epoch are read again in the custody transaction, never
        # authorized by the earlier cryptographic result alone.
        row = db.execute('''SELECT a.*,g.state AS generation_state,g.service_key_id,
            g.service_public_key,g.manifest_sequence,g.manifest_digest,
            g.owner_generation AS generation_owner,g.key_id AS generation_key,
            g.seal_flow_id AS generation_seal,c.epoch,c.generation AS connection_generation,
            c.owner_generation AS connection_owner,c.connection_id,c.reauth_ms
            FROM mail_pull_accounts a JOIN mail_pull_generations g
            ON g.install_id=a.install_id AND g.generation=a.generation
            JOIN mail_pull_connections c ON c.install_id=a.install_id WHERE a.install_id=?''',
                         (self.install_id,)).fetchone()
        _need(row is not None and row['state'] == 'active' and row['local_enabled'] == 1
              and row['generation_state'] == 'current', 'local_disabled')
        assert row is not None
        _need((row['generation'], row['owner_generation'], row['generation_owner'],
               row['connection_generation'], row['connection_owner'], row['epoch']) ==
              (self.generation, self.owner_generation, self.owner_generation,
               self.generation, self.owner_generation, self.epoch)
              and _match(_I, row['connection_id']) and _integer(row['reauth_ms'])
              and self._now() < row['reauth_ms'], 'stale_generation')
        _need(row['key_id'] == row['generation_key'] and row['seal_flow_id'] == row['generation_seal'],
              'local_disabled')
        _need((pin.origin, pin.key_id, pin.public_key, pin.sequence, digest) ==
              (row['service_origin'], row['service_key_id'], row['service_public_key'],
               row['manifest_sequence'], row['manifest_digest'])
              and self._now() < pin.expires_at_ms, 'service_key_changed')

    def _verify(self, raw: bytes, pin: ServiceKeyCandidate) -> tuple[dict[str, Any], dict[str, Any]]:
        _need(isinstance(raw, bytes) and len(raw) <= FRAME_BYTES, 'frame_limit')
        f = _object(raw)
        _need(f.keys() == _FRAME and type(f['v']) is int and f['v'] == 1
              and f['type'] in ('event', 'hold') and _match(_I, f['frame_id'])
              and _match(ID, f['key_id']) and _match(HEX, f['payload_digest'])
              and all(_integer(f[k], 1, 10**15 - 1) for k in ('generation', 'owner_generation', 'epoch'))
              and _integer(f['issued_ms']))
        _need((f['install_id'], f['generation'], f['owner_generation'], f['epoch']) ==
              (self.install_id, self.generation, self.owner_generation, self.epoch), 'frame_scope')
        _need(_match(_ORIGIN, pin.origin) and len(pin.origin) <= 261 and _match(ID, pin.key_id)
              and _integer(pin.sequence, 1, 10**15 - 1) and f['key_id'] == pin.key_id
              and self._now() < pin.expires_at_ms, 'service_key_changed')
        self._fresh(f, pin)
        encoded = f['payload']
        _need(isinstance(encoded, str) and len(encoded) <= (PAYLOAD_BYTES * 4 + 2) // 3
              and re.fullmatch(r'[A-Za-z0-9_-]+', encoded), 'frame_limit')
        try:
            payload = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
        except ValueError:
            raise IngressRefused('frame_unverified') from None
        _need(len(payload) <= PAYLOAD_BYTES and base64.urlsafe_b64encode(payload).decode().rstrip('=') == encoded
              and _digest(payload) == f['payload_digest'])
        transcript = _array('clayrune-agent-mail-pull-frame', '1', pin.origin, f['type'],
                            pin.key_id, self.install_id, self.generation, self.owner_generation,
                            self.epoch, f['frame_id'], f['issued_ms'], f['payload_digest'])
        try:
            Ed25519PublicKey.from_public_bytes(unbase64url(pin.public_key, 32)).verify(
                unbase64url(f['signature'], 64), transcript)
        except InvalidSignature:
            raise IngressRefused('frame_unverified') from None
        return f, _object(payload)  # No mail payload parsing before signature proof.

    def _fresh(self, frame: dict[str, Any], pin: ServiceKeyCandidate) -> None:
        _need(abs(self._now() - frame['issued_ms']) <= SKEW_MS, 'stale_signature')
        _need(self._now() < pin.expires_at_ms, 'service_key_changed')

    def _event(self, value: Any) -> dict[str, Any]:
        _need(type(value) is dict and value.keys() == _EVENT and type(value['v']) is int and value['v'] == 1,
              'invalid_event')
        e = cast(dict[str, Any], value)
        _need(all(_integer(e[k], 1, 10**15 - 1) for k in ('generation', 'owner_generation'))
              and (e['install_id'], e['generation'], e['owner_generation']) ==
              (self.install_id, self.generation, self.owner_generation), 'event_scope')
        _need(_match(ID, e['thread_id']) and _integer(e['seq'], 1)
              and all(_match(HEX, e[k]) for k in ('event_id', 'body_digest', 'event_digest')),
              'invalid_event')
        mid = e['incoming_message_id']
        _need(isinstance(mid, str) and len(mid) <= 8192 and _MID.fullmatch(mid), 'invalid_message_id')
        auth: dict[str, Any] = e['auth']
        _need(type(auth) is dict and auth.keys() == {'ingress', 'spf', 'dkim', 'dmarc', 'aligned'}
              and all(isinstance(v, str) and v.isascii() and len(v) <= 256 for v in auth.values())
              and auth['ingress'] == 'cloudflare' and auth['aligned'] in ('spf', 'dkim', 'spf+dkim')
              and re.fullmatch(r'pass(?:\+pass)*', auth['dmarc'])
              and all(re.fullmatch(_RESULT + r'(?:\+' + _RESULT + r')*', auth[k]) for k in ('spf', 'dkim'))
              and all('pass' in auth[k].split('+') for k in auth['aligned'].split('+')), 'auth_evidence')
        if e['kind'] == 'text':
            _need(isinstance(e['text'], str) and e['text'].strip() and '\x00' not in e['text']
                  and len(e['text'].encode('utf-8')) <= MAX_TEXT_BYTES and e['reason'] == '', 'invalid_text')
        else:
            _need(e['kind'] == 'held' and e['text'] is None and e['reason'] == 'empty_body', 'invalid_hold')
        _need(_digest((e['text'] or '').encode('utf-8')) == e['body_digest'], 'text_digest')
        _need(all(_integer(e[k]) for k in ('received_ms', 'expires_ms', 'thread_expires_ms'))
              and e['received_ms'] <= self._now() + SKEW_MS
              and e['received_ms'] < e['expires_ms'] <= e['thread_expires_ms']
              and e['expires_ms'] - e['received_ms'] <= self.store.config.queue_ttl_seconds * 1000
              and e['thread_expires_ms'] <= MAX_INT - MONTH_MS, 'invalid_expiry')
        _need(_digest(_array(self.install_id, self.generation, e['thread_id'], mid)) == e['event_id'],
              'event_identity')
        fields = ('install_id', 'generation', 'owner_generation', 'event_id', 'thread_id', 'seq',
                  'incoming_message_id', 'body_digest', 'received_ms', 'expires_ms',
                  'thread_expires_ms', 'kind', 'reason')
        _need(_digest(_array('clayrune-mail-pull-event', '1', *(e[k] for k in fields),
                            *(auth[k] for k in ('ingress', 'spf', 'dkim', 'dmarc', 'aligned'))))
              == e['event_digest'], 'event_digest')
        return e

    def _thread(self, db: sqlite3.Connection, e: dict[str, Any]) -> sqlite3.Row:
        row = db.execute('''SELECT p.*,b.registration_id,b.project_id AS base_project,
            b.session_id AS base_session,b.owner_generation AS base_generation,b.expires_at,
            c.snapshot FROM mail_pull_threads p JOIN thread_bindings b USING(thread_hash)
            JOIN mail_continuations c USING(thread_hash)
            WHERE p.install_id=? AND p.generation=? AND p.thread_id=?''',
                         (self.install_id, self.generation, e['thread_id'])).fetchone()
        _need(row is not None, 'unknown_thread')
        assert row is not None
        _need(row['state'] == 'active' and row['owner_generation'] == self.owner_generation
              and (row['registration_id'], row['base_generation'], row['base_project'], row['base_session']) ==
              (self.install_id, self.generation, row['project_id'], row['session_id'])
              and row['thread_expires_ms'] == e['thread_expires_ms']
              and row['dedupe_until_ms'] == e['thread_expires_ms'] + MONTH_MS
              and row['expires_at'] * 1000 == e['thread_expires_ms']
              and _digest(row['snapshot'].encode('utf-8')) == row['continuation_digest'], 'unknown_thread')
        refusal = self.store._validate_target(db, self.install_id, row['thread_hash'], self.store.clock())
        _need(not refusal, refusal)
        return row

    def receive(self, raw: bytes) -> dict[str, Any] | None:
        """Verify one event/hold in a current authenticated connection.

        Refusal/storage exceptions produce no ack. The client owns hold scheduling
        and quoting/logging policy; it must never log raw input or exception data.
        Duplicate offers refresh only outbox lease, preserving receipt and work.
        """
        pin, manifest_digest = self.reviewed_service()
        _need(_match(HEX, manifest_digest), 'service_key_changed')
        frame, payload = self._verify(raw, pin)
        e: dict[str, Any] = {}
        if frame['type'] == 'hold':
            _need(payload.keys() == {'request_id', 'event_id', 'reason', 'retry_after_ms'}
                  and _match(_I, payload['request_id']) and _match(HEX, payload['event_id'])
                  and isinstance(payload['reason'], str) and payload['reason'] in _HOLD_REASONS
                  and _integer(payload['retry_after_ms']), 'invalid_hold')
        else:
            _need(payload.keys() == {'event', 'lease_id', 'lease_until_ms'}
                  and _match(_I, payload['lease_id']) and _integer(payload['lease_until_ms']), 'invalid_event')
            e = self._event(payload['event'])

        with self.store._tx() as db:
            _base(db)
            _validate(db)  # Refuse absent/changed extension; never initialize it.
            self._scope(db, pin, manifest_digest)
            self._fresh(frame, pin)
            if frame['type'] == 'hold':
                return None
            old = db.execute('SELECT * FROM mail_pull_receipts WHERE event_id=?', (e['event_id'],)).fetchone()
            if old is not None:
                _need((old['install_id'], old['generation'], old['owner_generation'], old['event_digest'],
                       old['body_digest']) == (self.install_id, self.generation, self.owner_generation,
                       e['event_digest'], e['body_digest']), 'event_conflict')
                _need(old['disposition'] == 'local_received', 'event_expired')
                local_receipt = old['local_receipt_id']
            thread = self._thread(db, e)
            if old is None:
                _need(self._now() < e['expires_ms'], 'event_expired')
                # A legacy receipt without a pull receipt cannot prove the new
                # atomic custody graph; never adopt it or reconstruct lost work.
                _need(not db.execute('SELECT 1 FROM mail_ingress_receipts WHERE event_id=?',
                                     (e['event_id'],)).fetchone(), 'event_conflict')
                _need(not db.execute('''SELECT 1 FROM mail_pull_receipts WHERE
                    (install_id=? AND cloud_seq=?) OR (install_id=? AND generation=?
                    AND thread_id=? AND incoming_hash=?)''',
                    (self.install_id, e['seq'], self.install_id, self.generation,
                     e['thread_id'], _digest(e['incoming_message_id'].encode('ascii')))).fetchone(), 'event_conflict')
                env = dict(version=2 if e['kind'] == 'held' else 1, event_id=e['event_id'],
                           install_id=self.install_id, generation=self.generation,
                           incoming_message_id=e['incoming_message_id'], text=e['text'], reason=e['reason'],
                           body_digest=e['body_digest'], expires_at=e['expires_ms'])
                _ingest_custody(self.store, db, env, thread['thread_hash'], e['event_digest'])
                local_receipt = secrets.token_hex(16)
                db.execute('''INSERT INTO mail_pull_receipts VALUES(
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,'local_received')''',
                    (e['event_id'], self.install_id, self.generation, self.owner_generation,
                     e['thread_id'], e['seq'], _digest(e['incoming_message_id'].encode('ascii')),
                     e['event_digest'], e['body_digest'], local_receipt, e['event_id'],
                     e['received_ms'], e['expires_ms'], thread['dedupe_until_ms'], self._now()))
                _need(self._now() < e['expires_ms'], 'event_expired')
            db.execute('''INSERT INTO mail_pull_ack_outbox
                (event_id,local_receipt_id,lease_id,event_digest,body_digest,state,next_attempt_ms,created_ms)
                VALUES(?,?,?,?,?,'pending',?,?) ON CONFLICT(event_id) DO UPDATE SET lease_id=excluded.lease_id''',
                (e['event_id'], local_receipt, payload['lease_id'], e['event_digest'],
                 e['body_digest'], self._now(), self._now()))
            self._fresh(frame, pin)
            _need(old is not None or self._now() < e['expires_ms'], 'event_expired')
        # No ack construction or transmission is reachable before COMMIT.
        self._fresh(frame, pin)  # A slow fsync may age out the original proof.
        return {'v': 1, 'type': 'custody_ack', 'install_id': self.install_id,
                'generation': self.generation, 'owner_generation': self.owner_generation,
                'epoch': self.epoch, 'request_id': local_receipt,
                'payload': {'event_id': e['event_id'], 'event_digest': e['event_digest'],
                            'body_digest': e['body_digest'], 'lease_id': payload['lease_id'],
                            'local_receipt_id': local_receipt}}


def _ingest_custody(store: MailStore, db: sqlite3.Connection, env: dict[str, Any],
                    thash: str, envelope_digest: str) -> None:
    """Shared legacy/pull insert; caller owns the transaction and commit.

    Extracted unchanged from MailIngress._ingest. Legacy receipt semantics and
    the base store's length-prefixed body digest remain intact.
    """
    now = store.clock()
    # Expired receipts cannot authorize a fresh delivery; their content-free
    # tombstones need not outlive the signed event's own replay lifetime.
    db.execute('DELETE FROM mail_ingress_receipts WHERE expires_at<=?', (int(store.clock() * 1000),))
    old = db.execute('SELECT envelope_digest FROM mail_ingress_receipts WHERE event_id=?',
                     (env['event_id'],)).fetchone()
    if old:
        if old['envelope_digest'] != envelope_digest:
            raise IngressRefused('event_conflict')
        return
    if db.execute('SELECT 1 FROM events WHERE event_id=?', (env['event_id'],)).fetchone():
        raise IngressRefused('event_conflict')
    cfg = store.config
    held = env['version'] == 2
    body = None if held else env['text']
    digest, blen = _bounded_digest(body or '', cfg.max_body_bytes)
    if blen > cfg.max_body_bytes:
        raise IngressRefused('body_limit')
    excluded = sorted(TERMINAL_STATES | {SUBMITTED})
    active = db.execute('SELECT COUNT(*) FROM events WHERE registration_id=? AND state NOT IN ('
                        + ','.join('?' * len(excluded)) + ')',
                        (env['install_id'], *excluded)).fetchone()[0]
    if active >= cfg.max_active_events:
        raise IngressRefused('queue_full')
    binding = db.execute('SELECT * FROM thread_bindings WHERE thread_hash=?', (thash,)).fetchone()
    mid = env['incoming_message_id']
    dedupe = hashlib.sha256(f"{env['install_id']}\x1f{thash}\x1f{mid}".encode()).hexdigest()
    # A legacy unit-1 event cannot be silently adopted without its full
    # authenticated transport receipt; unit 7 must never migrate guesses.
    if db.execute('SELECT 1 FROM events WHERE dedupe_key=?', (dedupe,)).fetchone():
        raise IngressRefused('event_conflict')
    db.execute('INSERT INTO events(event_id,registration_id,thread_hash,project_id,session_id,'
               'generation,message_id,dedupe_key,body_digest,body_len,state,body,reason,'
               'received_at,state_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (env['event_id'], env['install_id'], thash, binding['project_id'],
                binding['session_id'], env['generation'], mid, dedupe, digest, blen,
                BLOCKED if held else LOCAL_RECEIVED, body, env['reason'] if held else '', now, now,
                min(env['expires_at'] / 1000, now + cfg.queue_ttl_seconds)))
    db.execute('INSERT INTO mail_ingress_receipts VALUES(?,?,?,?)',
               (env['event_id'], envelope_digest, env['body_digest'], env['expires_at']))
