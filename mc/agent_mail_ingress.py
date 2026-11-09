"""Local pins, immutable transport bindings and atomic signed custody (unit 4).

Enrollment/outbound code must call pin_service and bind_transport BEFORE send.
These are Python integration seams, never agent-facing credential/consent APIs.
There is no laundering, follow-up, wake or permission change in this module.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Mapping

from mc.agent_mail_envelope import MAX_REQUEST_BYTES, parse_envelope
from mc.agent_mail_signing import (ID, IngressRefused, ServicePin, SKEW_MS,
                                   VerifiedRequest, verify)
from mc.agent_mail_store import (LOCAL_RECEIVED, SUBMITTED, TERMINAL_STATES, MailStore,
                                 _bounded_digest, hash_thread_token)


class MailIngress:
    def __init__(self, store: MailStore):
        self.store = store

    def clock_ms(self) -> int:
        return int(self.store.clock() * 1000)

    def _consent(self, db: sqlite3.Connection, install: str, generation: int) -> None:
        reg = db.execute('SELECT * FROM registrations WHERE registration_id=?', (install,)).fetchone()
        if reg is None or not reg['enabled'] or reg['generation'] != generation:
            raise IngressRefused('consent_required')

    def pin_service(self, pin: ServicePin) -> bool:
        """Trusted local enrollment only. A generation pin cannot be overwritten.

        Key/route changes require revocation + a new generation, never a request
        from the Worker. Only PUBLIC key material enters this database.
        """
        pin.validate()
        with self.store._tx() as db:
            self._consent(db, pin.install_id, pin.generation)
            old = self._pin(db, pin.install_id, pin.generation)
            if old is not None:
                if old != pin:
                    raise IngressRefused('pin_conflict')
                return False
            db.execute('INSERT INTO mail_service_pins VALUES(?,?,?,?,?)',
                       (pin.install_id, pin.generation, pin.key_id, pin.public_key, pin.route_path))
            return True

    def bind_transport(self, install_id: str, generation: int, thread_id: str,
                       thread_token: str) -> bool:
        """Bind opaque transport id to an EXISTING token hash, before outbound send."""
        if (not ID.fullmatch(install_id) or not ID.fullmatch(thread_id)
                or type(generation) is not int or not 1 <= generation < 10**15):
            raise IngressRefused('invalid_binding')
        thash = hash_thread_token(thread_token)
        with self.store._tx() as db:
            self._consent(db, install_id, generation)
            refusal = self.store._validate_target(db, install_id, thash, self.store.clock())
            if refusal:
                raise IngressRefused(refusal)
            old = db.execute('SELECT thread_hash FROM mail_transport_threads '
                             'WHERE registration_id=? AND generation=? AND thread_id=?',
                             (install_id, generation, thread_id)).fetchone()
            if old is not None:
                if old['thread_hash'] != thash:
                    raise IngressRefused('binding_conflict')
                return False
            other = db.execute('SELECT 1 FROM mail_transport_threads WHERE registration_id=? '
                               'AND generation=? AND thread_hash=?',
                               (install_id, generation, thash)).fetchone()
            if other:
                raise IngressRefused('binding_conflict')
            db.execute('INSERT INTO mail_transport_threads VALUES(?,?,?,?)',
                       (install_id, generation, thread_id, thash))
            return True

    @staticmethod
    def _pin(db: sqlite3.Connection, install: str, generation: int) -> ServicePin | None:
        row = db.execute('SELECT * FROM mail_service_pins WHERE registration_id=? AND generation=?',
                         (install, generation)).fetchone()
        return (ServicePin(install, generation, row['key_id'], row['public_key'], row['route_path'])
                if row else None)

    def receive(self, headers: Mapping[str, str], raw: bytes, *,
                method: str = 'POST', path: str = '/api/agent-mail/ingress') -> dict[str, Any]:
        if len(raw) > MAX_REQUEST_BYTES:
            raise IngressRefused('body_limit')
        install = headers.get('x-mail-install-id', '')
        if not ID.fullmatch(install):
            raise IngressRefused('bad_signature_fields')
        # Signature fields select a candidate pin only; they confer no authority.
        with self.store._read() as db:
            reg = db.execute('SELECT generation FROM registrations WHERE registration_id=?',
                             (install,)).fetchone()
            pin = self._pin(db, install, reg['generation']) if reg else None
        if pin is None:
            raise IngressRefused('pin_required')
        signed = verify(headers, raw, pin, method=method, path=path, clock_ms=self.clock_ms)
        env = parse_envelope(raw, signed, self.clock_ms())
        if env['expires_at'] - env['received_at'] > self.store.config.queue_ttl_seconds * 1000:
            raise IngressRefused('invalid_expiry')
        # The transaction is shared with unit-1 revoke/disable/session deletion.
        # A crypto result from before revocation never overrides current consent.
        with self.store._tx() as db:
            self._consent(db, install, pin.generation)
            if self._pin(db, install, pin.generation) != pin:
                raise IngressRefused('pin_changed')
            signed.check_fresh(self.clock_ms)
            mapping = db.execute('SELECT thread_hash FROM mail_transport_threads '
                                 'WHERE registration_id=? AND generation=? AND thread_id=?',
                                 (install, pin.generation, env['thread_id'])).fetchone()
            if mapping is None:
                raise IngressRefused('unknown_thread')
            thash = mapping['thread_hash']
            refusal = self.store._validate_target(db, install, thash, self.store.clock())
            if refusal:
                raise IngressRefused(refusal)
            now_ms = self.clock_ms()
            if env['expires_at'] <= now_ms:
                raise IngressRefused('event_expired')
            self._claim_nonce(db, signed, now_ms)
            signed.check_fresh(self.clock_ms)  # a blocked/slow durable claim can age out
            self._ingest(db, env, thash, hashlib.sha256(raw).hexdigest())
            signed.check_fresh(self.clock_ms)
            if env['expires_at'] <= self.clock_ms():
                raise IngressRefused('event_expired')
            # No custody receipt escapes unless ALL writes have committed.
        signed.check_fresh(self.clock_ms)
        if env['expires_at'] <= self.clock_ms():
            raise IngressRefused('event_expired')
        return {'custody': 'durable', 'event_id': env['event_id'],
                'body_digest': env['body_digest'], 'install_id': install,
                'generation': pin.generation}

    @staticmethod
    def _claim_nonce(db: sqlite3.Connection, signed: VerifiedRequest, now_ms: int) -> None:
        db.execute('DELETE FROM mail_ingress_nonces WHERE expires_at<=?', (now_ms,))
        inserted = db.execute('INSERT OR IGNORE INTO mail_ingress_nonces VALUES(?,?,?,?,?,?)',
                             ('service-to-install', signed.pin.install_id, signed.pin.generation,
                              signed.pin.key_id, signed.nonce, signed.timestamp + SKEW_MS + 1))
        if inserted.rowcount != 1:
            raise IngressRefused('replayed_nonce')

    def _ingest(self, db: sqlite3.Connection, env: dict[str, Any], thash: str,
                envelope_digest: str) -> None:
        now = self.store.clock()
        # Expired receipts cannot authorize a fresh delivery; their content-free
        # tombstones need not outlive the signed event's own replay lifetime.
        db.execute('DELETE FROM mail_ingress_receipts WHERE expires_at<=?', (self.clock_ms(),))
        old = db.execute('SELECT envelope_digest FROM mail_ingress_receipts WHERE event_id=?',
                         (env['event_id'],)).fetchone()
        if old:
            if old['envelope_digest'] != envelope_digest:
                raise IngressRefused('event_conflict')
            return
        if db.execute('SELECT 1 FROM events WHERE event_id=?', (env['event_id'],)).fetchone():
            raise IngressRefused('event_conflict')
        cfg = self.store.config
        body = env['text']
        digest, blen = _bounded_digest(body, cfg.max_body_bytes)
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
                   'generation,message_id,dedupe_key,body_digest,body_len,state,body,'
                   'received_at,state_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (env['event_id'], env['install_id'], thash, binding['project_id'],
                    binding['session_id'], env['generation'], mid, dedupe, digest, blen,
                    LOCAL_RECEIVED, body, now, now,
                    min(env['expires_at'] / 1000, now + cfg.queue_ttl_seconds)))
        db.execute('INSERT INTO mail_ingress_receipts VALUES(?,?,?,?)',
                   (env['event_id'], envelope_digest, env['body_digest'], env['expires_at']))
