"""Human owner-change intent and signed status projection (pull unit 20).

Unwired: composition must supply the SAME checked store, reviewed manifest
callback and trusted human/passcode gate as Signup. Do not expose this object
through agent-facing mail tools. Only start() sends management/start and it
checks human authorization on every call; refresh() sends status requests only.
No new mailbox, hosted cookie, link token, confirmation or revert action is
accepted here. The human uses the service's fixed emailed links for those.

Service completion stops local authority but does not install a new generation
or reseal/enable it. Later human lifecycle composition owns that transition.
The old starting seal is retained solely for the service's metadata-status
access. Revert deadlines and frozen state come from signed hosted observations;
no local seven-day calculation can thaw mail or extend the revert window.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal, SealReference
from mc.agent_mail_pull_schema import _validate
from mc.agent_mail_service_manifest import ManifestRefused, ServiceKeyCandidate
from mc.agent_mail_signup import (ERRORS, H, I, ID, LOCAL_REASONS, MAX_INT, ORIGIN,
                                  SKEW, TTL, SignupTransport, _digest, _json, _mailbox, _need)
from mc.agent_mail_signing import IngressRefused, unbase64url
from mc.agent_mail_store import MailStore, MailStoreCorrupt, MailStoreError
from mc.core import _log

START = '/v1/pull/management/start'
STATUS = '/v1/pull/management/status'
ACCOUNT_STATUS = '/v1/pull/status'
STATES = {'pending', 'approved', 'completed', 'expired', 'cancelled', 'reverted'}
NOTICES = {'queued', 'accepted', 'uncertain', 'failed'}
REASONS = LOCAL_REASONS | {'registration_inactive', 'registration_frozen', 'old_mailbox_required',
                          'owner_change_rebind_required', 'store_corrupt', 'notice_pending', 'notice_failed',
                          'notice_uncertain', 'old_mailbox_lost', 'revoked', 'revocation_pending', 'reauth_due'}
ERROR_CODES = {k: set(v) for k, v in ERRORS.items()}
ERROR_CODES[409] |= {'registration_inactive', 'registration_frozen'}
META = {'v', 'op_id', 'public_key', 'start_body', 'started', 'fence_token',
        'remote_state', 'result', 'notice_state', 'owner_change', 'observed_ms', 'registration_state'}
REGISTRATION_STATES = {'pending', 'verified', 'active', 'disabled', 'frozen', 'deleted'}


def _integer(value: Any, low: int = 0, high: int = MAX_INT) -> bool:
    return type(value) is int and low <= value <= high


def _match(pattern: re.Pattern[str], value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


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
        raise EnrollmentRefused('response_unverified')

    def check(value: Any, depth: int = 0) -> None:
        _need(depth <= 5)
        if type(value) is dict:
            for key, item in value.items():
                key.encode('utf-8')
                check(item, depth + 1)
        elif type(value) is str:
            value.encode('utf-8')
        else:
            _need(value is None or _integer(value))

    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_int=number,
                       parse_float=invalid, parse_constant=invalid)
    _need(type(value) is dict)
    check(value)
    return value


class OwnerChange:
    def __init__(self, store: MailStore, seal: IdentitySeal, transport: SignupTransport, *,
                 install_id: str, reviewed_service: Callable[[], tuple[ServiceKeyCandidate, str]],
                 authorize: Callable[[], bool], clock_ms: Callable[[], int] | None = None):
        self.store, self.seal, self.transport = store, seal, transport
        self.install_id, self.reviewed_service, self.authorize = install_id, reviewed_service, authorize
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def _now(self) -> int:
        now = self.clock_ms()
        _need(_integer(now, 1, MAX_INT - TTL), 'invalid_request')
        return now

    def _service(self, account: dict[str, Any]) -> ServiceKeyCandidate:
        try:
            pin, digest = self.reviewed_service()
        except (ManifestRefused, EnrollmentRefused):
            raise
        except Exception:
            _log('[agent-mail] owner_change service_unavailable', flush=True)
            raise EnrollmentRefused('service_unavailable') from None
        _need(_match(ORIGIN, pin.origin) and len(pin.origin) <= 261 and _match(ID, pin.key_id)
              and _match(H, digest) and _integer(pin.sequence, 1, 10**15 - 1)
              and _integer(pin.expires_at_ms, 1) and self._now() < pin.expires_at_ms,
              'service_key_changed')
        unbase64url(pin.public_key, 32)
        _need((pin.origin, pin.key_id, pin.public_key, pin.sequence, digest) ==
              tuple(account[k] for k in ('service_origin', 'service_key_id', 'service_public_key',
                                        'manifest_sequence', 'manifest_digest')), 'service_key_changed')
        return pin

    def _account(self, db: sqlite3.Connection) -> dict[str, Any]:
        _validate(db)
        _need(_match(I, self.install_id), 'invalid_request')
        row = db.execute('''SELECT a.*,g.service_key_id,g.service_public_key,g.manifest_sequence,
            g.manifest_digest,g.state AS generation_state,g.seal_flow_id AS generation_seal,
            g.key_id AS generation_key,g.owner_generation AS generation_owner
            FROM mail_pull_accounts a JOIN mail_pull_generations g ON g.install_id=a.install_id
            AND g.generation=a.generation WHERE a.install_id=?''', (self.install_id,)).fetchone()
        _need(row is not None, 'registration_inactive')
        a = dict(row)
        _need(a['key_id'] == a['generation_key'] and a['seal_flow_id'] == a['generation_seal']
              and a['owner_generation'] == a['generation_owner']
              and _match(ID, a['key_id']) and _match(I, a['seal_flow_id'])
              and _integer(a['generation'], 1, 10**15 - 1)
              and _integer(a['owner_generation'], 1, 10**15 - 1), 'schema_corrupt')
        return a

    def _row(self, db: sqlite3.Connection, flow_id: str | None = None, *,
             op_id: str | None = None) -> dict[str, Any] | None:
        rows = db.execute('''SELECT f.*,g.service_key_id,g.service_public_key,g.manifest_sequence,
            g.manifest_digest FROM mail_pull_flows f JOIN mail_pull_generations g
            ON g.install_id=f.install_id AND g.generation=f.expected_generation
            WHERE f.install_id=? AND f.purpose='owner_change'
            ORDER BY f.human_authorized_ms DESC,f.rowid DESC''', (self.install_id,)).fetchall()
        selected = None
        try:
            for item in rows:
                row = dict(item)
                _need(isinstance(row['result_json'], str) and len(row['result_json']) <= 16384, 'schema_corrupt')
                meta = _object(row['result_json'].encode('utf-8'))
                _need(set(meta) == META and type(meta['v']) is int and meta['v'] == 1
                      and _match(I, meta['op_id']) and _match(H, meta['fence_token'])
                      and _integer(meta['started'], 0, 1) and _integer(meta['observed_ms'])
                      and (meta['notice_state'] is None or meta['notice_state'] in NOTICES)
                      and (meta['remote_state'] is None or meta['remote_state'] in STATES)
                      and (meta['registration_state'] is None or meta['registration_state'] in REGISTRATION_STATES), 'schema_corrupt')
                _need(_match(I, row['flow_id']) and _match(I, row['seal_flow_id'])
                      and row['seal_flow_id'] == row['flow_id'] and _match(ID, row['candidate_key_id'])
                      and _integer(row['expected_generation'], 1, 10**15 - 1)
                      and _integer(row['expected_owner_generation'], 1, 10**15 - 1)
                      and row['human_authorized_ms'] > 0 and row['consent_epoch'] > 0, 'schema_corrupt')
                self._result(meta['result'], row, meta['remote_state'])
                self._change(meta['owner_change'])
                expected_state = {'approved': 'verified', 'reverted': 'completed'}.get(
                    meta['remote_state'] or 'pending', meta['remote_state'] or 'pending')
                _need(row['state'] == expected_state and
                      (meta['started'] == 1 and meta['remote_state'] is not None or
                       meta['started'] == 0 and meta['remote_state'] is None and meta['result'] is None), 'schema_corrupt')
                if meta['public_key']:
                    unbase64url(meta['public_key'], 32)
                    body = meta['start_body'].encode('ascii')
                    parsed = _object(body)
                    _need(len(body) <= 8192 and _digest(body) == row['input_digest']
                          and set(parsed) == {'op_id', 'flow_id', 'purpose', 'candidate_key_id',
                                              'candidate_public_key', 'candidate_signature',
                                              'flow_secret_hash', 'fence_hash'}
                          and parsed['op_id'] == meta['op_id'] and parsed['flow_id'] == row['flow_id']
                          and parsed['purpose'] == 'owner_change'
                          and parsed['candidate_key_id'] == row['candidate_key_id']
                          and parsed['candidate_public_key'] == meta['public_key']
                          and parsed['fence_hash'] == _digest(meta['fence_token'].encode('ascii')),
                          'schema_corrupt')
                else:
                    _need(meta['public_key'] == '' and meta['start_body'] is None and meta['started'] == 0
                          and row['input_digest'] == '0' * 64, 'schema_corrupt')
                row['meta'] = meta
                if ((flow_id is None or flow_id == row['flow_id']) and
                        (op_id is None or op_id == meta['op_id'])):
                    _need(selected is None or op_id is None and flow_id is None, 'schema_corrupt')
                    if selected is None:
                        selected = row
            return selected
        except (ValueError, TypeError, KeyError, AttributeError, IngressRefused, RecursionError):
            raise EnrollmentRefused('schema_corrupt') from None

    @staticmethod
    def _ref(row: dict[str, Any]) -> SealReference:
        return SealReference(row['seal_flow_id'], row['install_id'], row['expected_generation'], row['candidate_key_id'])

    def _read(self, flow_id: str | None = None) -> tuple[dict[str, Any], dict[str, Any] | None]:
        _need(flow_id is None or _match(I, flow_id), 'invalid_request')
        with self.store._read() as db:
            a = self._account(db)
            row = self._row(db, flow_id)
            _need(flow_id is None or row is not None, 'not_found')
        return a, row

    @staticmethod
    def _view(a: dict[str, Any] | None, row: dict[str, Any] | None, reason: str = '') -> dict[str, Any]:
        meta = row['meta'] if row else {}
        result = meta.get('result')
        state = ((meta.get('registration_state') or result['state']) if result else meta.get('remote_state') or
                 ('start_pending' if meta.get('public_key') else 'preparing')) if row else 'idle'
        enabled = bool(a and a['local_enabled'] and a['state'] == 'active' and a['generation_state'] == 'current'
                       and (result is None or a['generation'] == result['generation'])
                       and meta.get('registration_state') in {None, 'active'} and state != 'frozen')
        return {'state': state, 'reason': reason, 'enabled': enabled,
                'install_id': a['install_id'] if a else None, 'flow_id': row['flow_id'] if row else None,
                'flow_state': meta.get('remote_state'), 'expires_ms': row['expires_ms'] if row else None,
                'generation': result['generation'] if result else a['generation'] if a else None,
                'owner_generation': result['owner_generation'] if result else a['owner_generation'] if a else None,
                'verification_state': meta.get('notice_state'), 'owner_change': meta.get('owner_change'),
                'observed_ms': meta.get('observed_ms') or None, 'requires_enable': result is not None and not enabled}

    def _run(self, action: Callable[[], dict[str, Any]], *, flow_id: str | None = None) -> dict[str, Any]:
        try:
            return action()
        except ManifestRefused:
            reason = 'service_key_changed'
        except IngressRefused:
            reason = 'response_unverified'
        except MailStoreCorrupt:
            reason = 'schema_corrupt'
        except (MailStoreError, OSError, sqlite3.Error):
            reason = 'storage_unavailable'
            _log('[agent-mail] owner_change storage_unavailable', flush=True)
        except EnrollmentRefused as exc:
            code = 'vault_locked' if str(exc) == 'vault_locked_or_unconfigured' else str(exc)
            reason = code if code in REASONS else 'service_unavailable'
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            reason = 'response_unverified'
        try:
            a, row = self._read(flow_id)
            return self._view(a, row, reason)
        except (EnrollmentRefused, MailStoreError, OSError, sqlite3.Error):
            return self._view(None, None, reason)

    def status(self, flow_id: str | None = None) -> dict[str, Any]:
        """Read-only local projection; never signs, generates, polls or thaws."""
        return self._run(lambda: self._view(*self._read(flow_id)), flow_id=flow_id)

    def start(self, op_id: str) -> dict[str, Any]:
        """Trusted human action ONLY. Exact operation retry never changes intent."""
        def begin() -> dict[str, Any]:
            IdentitySeal._authorize(self.authorize)
            _need(_match(I, op_id), 'invalid_request')
            a, _ = self._read()
            pin = self._service(a)  # Reviewer can CAS the same store: outside tx.
            with self.store._tx() as db:
                _need(self._account(db) == a, 'stale_generation')
                row = self._row(db, op_id=op_id)
                if row is None:
                    _need(a['state'] in {'active', 'verified'} and a['generation_state'] == 'current',
                          'registration_frozen' if a['state'] == 'frozen' else 'registration_inactive')
                    _need(not db.execute('''SELECT 1 FROM mail_pull_fences WHERE install_id=?
                        AND generation=? AND mode='revoke' ''', (self.install_id, a['generation'])).fetchone(), 'fenced')
                    _need(not db.execute('''SELECT 1 FROM mail_pull_flows WHERE install_id=?
                        AND purpose!='signup' AND state IN ('pending','verified','enable_pending') AND expires_ms>?''',
                        (self.install_id, self._now())).fetchone(), 'operation_conflict')
                    flow, now = secrets.token_hex(16), self._now()
                    meta = dict(v=1, op_id=op_id, public_key='', start_body=None, started=0,
                                fence_token=secrets.token_hex(32), remote_state=None, result=None,
                                notice_state=None, owner_change=None, observed_ms=0, registration_state=None)
                    db.execute("INSERT INTO mail_pull_flows VALUES(?,?,'owner_change',?,?,?,?,?,?,?,'pending',?,'',?)",
                               (flow, self.install_id, a['generation'], a['owner_generation'], a['key_id'],
                                flow, '0' * 64, now, a['consent_epoch'], now + TTL, _json(meta).decode('ascii')))
                    row = self._row(db, flow)
                assert row is not None
                flow_id = row['flow_id']
            # Draft intent commits BEFORE seal creation. A rollback/crash leaves
            # a recoverable reference, so the next human retry reopens its orphan.
            with self.store._tx() as db:
                _need(self._account(db) == a, 'stale_generation')
                row = self._row(db, flow_id)
                _need(row is not None, 'schema_corrupt')
                assert row is not None
                if row['meta']['public_key']:
                    prepared = row
                else:
                    current = SealReference(a['seal_flow_id'], self.install_id, a['generation'], a['key_id'])
                    _need((row['expected_generation'], row['expected_owner_generation'], row['consent_epoch']) ==
                          (a['generation'], a['owner_generation'], a['consent_epoch']), 'stale_generation')
                    public = self.seal.prepare_management(current, self._ref(row), authorize=lambda: True,
                                                          expected_public_key=None)
                    meta = row['meta']
                    meta['public_key'] = public
                    body = self._body(row, pin)
                    meta['start_body'] = body.decode('ascii')
                    db.execute('UPDATE mail_pull_flows SET input_digest=?,result_json=? WHERE flow_id=?',
                               (_digest(body), _json(meta).decode('ascii'), row['flow_id']))
                    prepared = self._row(db, row['flow_id'])
            assert prepared is not None
            return self._refresh(prepared['flow_id'], allow_start=True)
        return self._run(begin)

    def _body(self, row: dict[str, Any], pin: ServiceKeyCandidate) -> bytes:
        meta, ref = row['meta'], self._ref(row)
        _need(self.seal.status(ref) == 'ready', self.seal.status(ref))
        secret = self.seal.flow_secret(ref.flow, ref.install, ref.generation, ref.key_id).hex()
        fence, hashed = _digest(meta['fence_token'].encode('ascii')), _digest(secret.encode('ascii'))
        proof = _json(['clayrune-mail-pull-candidate', '1', pin.origin, 'owner_change', row['flow_id'],
                       self.install_id, str(ref.generation), str(row['expected_owner_generation']),
                       ref.key_id, meta['public_key'], fence, hashed])
        return _json(dict(op_id=meta['op_id'], flow_id=row['flow_id'], purpose='owner_change',
                          candidate_key_id=ref.key_id, candidate_public_key=meta['public_key'],
                          candidate_signature=self.seal.sign_pull(ref, proof), flow_secret_hash=hashed, fence_hash=fence))

    def refresh(self, flow_id: str | None = None) -> dict[str, Any]:
        """Recover status after restart; never retries management/start or confirms."""
        return self._run(lambda: self._refresh(flow_id), flow_id=flow_id)

    def _current(self, db: sqlite3.Connection, a: dict[str, Any], row: dict[str, Any]) -> None:
        _need(self._account(db) == a and self._row(db, row['flow_id']) == row, 'stale_generation')

    def _refresh(self, flow_id: str | None, *, allow_start: bool = False) -> dict[str, Any]:
        a, row = self._read(flow_id)
        if row is None or not row['meta']['public_key']:
            return self._view(a, row)
        # Historical flow metadata uses its original pin and bound starting key.
        scope = {**a, **{k: row[k] for k in ('service_key_id', 'service_public_key', 'manifest_sequence', 'manifest_digest')}}
        pin = self._service(scope)
        ref, meta = self._ref(row), row['meta']
        _need(self.seal.status(ref) == 'ready', self.seal.status(ref))
        _need(self.seal.public(ref.flow, ref.install, ref.generation, ref.key_id) == meta['public_key'], 'operation_conflict')
        if allow_start and not meta['started']:
            _need(a['generation'] == ref.generation and a['owner_generation'] == row['expected_owner_generation']
                  and a['consent_epoch'] == row['consent_epoch'] and a['state'] in {'active', 'verified'}
                  and a['generation_state'] == 'current', 'stale_generation')
            _need(self._now() < row['expires_ms'], 'flow_expired')
            # Validate exact committed intent against sealed material, no rewrite.
            _need(self._body(row, pin) == meta['start_body'].encode('ascii'), 'schema_corrupt')
            path, body = START, meta['start_body'].encode('ascii')
        else:
            path = STATUS
            body = _json(dict(flow_id=row['flow_id'], flow_secret=self.seal.flow_secret(
                ref.flow, ref.install, ref.generation, ref.key_id).hex()))
        with self.store._read() as db:
            self._current(db, a, row)
            _need(path != START or not db.execute('''SELECT 1 FROM mail_pull_fences WHERE install_id=?
                AND generation=? AND (mode='revoke' OR flow_id=?)''',
                (self.install_id, ref.generation, row['flow_id'])).fetchone(), 'fenced')
        value, _, code = self._exchange(pin, row, path, body, ref.generation, row['expected_owner_generation'])
        if code != 200:
            return self._view(a, row, value['code'])
        self._management(value, row, path)
        self._service(scope)
        with self.store._tx() as db:
            self._current(db, a, row)
            meta = {**meta, 'started': 1, 'remote_state': value['state'], 'observed_ms': self._now()}
            if value['state'] != row['meta']['remote_state']:
                meta['owner_change'] = None
                meta['registration_state'] = None
            meta['notice_state'] = value['verification_state'] if path == START else meta['notice_state']
            meta['result'] = None if path == START else value['result']
            local_state = {'approved': 'verified', 'reverted': 'completed'}.get(value['state'], value['state'])
            db.execute('UPDATE mail_pull_flows SET state=?,expires_ms=?,result_json=? WHERE flow_id=?',
                       (local_state, value['expires_ms'], _json(meta).decode('ascii'), row['flow_id']))
            result = meta['result']
            if result is not None and (a['generation'] < result['generation'] or
                                      a['generation'] == result['generation'] and result['state'] == 'frozen'):
                # Denial-only transition. Preserve old seal/generation for metadata,
                # never rebind a new owner or transfer threads using this proof.
                if a['local_enabled'] or a['state'] not in {'held', 'disabled', 'deleted'}:
                    db.execute("UPDATE mail_pull_accounts SET local_enabled=0,state='held',reason='owner_change_rebind_required',"
                               "consent_epoch=consent_epoch+1,updated_ms=? WHERE install_id=? AND state!='deleted'",
                               (self._now(), self.install_id))
                db.execute("UPDATE mail_pull_generations SET state='held' WHERE install_id=? AND generation=? AND state='current'",
                           (self.install_id, a['generation']))
                db.execute("UPDATE mail_pull_threads SET state='revoked' WHERE install_id=? AND generation<=?",
                           (self.install_id, a['generation']))
                db.execute('DELETE FROM mail_pull_connections WHERE install_id=?', (self.install_id,))
        # MR deliberately lacks the revert deadline. OC can be queried only
        # with a LOCALLY bound current-generation seal, never an old seal signed
        # under new headers. Later human lifecycle composition owns that rebind.
        return self._account_status(row['flow_id'])

    def _account_status(self, flow_id: str) -> dict[str, Any]:
        a, row = self._read(flow_id)
        assert row is not None
        result = row['meta']['result']
        generation, owner = a['generation'], a['owner_generation']
        if result and (generation != result['generation'] or owner != result['owner_generation']):
            return self._view(a, row, 'owner_change_rebind_required')
        signing_ref = SealReference(a['seal_flow_id'], self.install_id, generation, a['key_id'])
        _need(self.seal.public(signing_ref.flow, signing_ref.install, signing_ref.generation,
                               signing_ref.key_id) == row['meta']['public_key'], 'stale_generation')
        scope = {**a, **{k: row[k] for k in ('service_key_id', 'service_public_key', 'manifest_sequence', 'manifest_digest')}}
        pin = self._service(scope)
        value, raw, code = self._exchange(pin, row, ACCOUNT_STATUS, b'{}', generation, owner, signing_ref=signing_ref)
        if code != 200:
            return self._view(a, row, value['code'])
        self._status_shape(value, row, generation, owner)
        self._service(scope)
        with self.store._tx() as db:
            self._current(db, a, row)
            meta = {**row['meta'], 'owner_change': value['owner_change'], 'notice_state': value['last_notice_state'],
                    'observed_ms': self._now(), 'registration_state': value['state']}
            db.execute('UPDATE mail_pull_flows SET result_json=? WHERE flow_id=?', (_json(meta).decode('ascii'), flow_id))
            db.execute('INSERT OR REPLACE INTO mail_pull_status VALUES(?,?,?,?,?,?)',
                       (self.install_id, self._now(), generation, owner, raw.decode('utf-8'), _digest(raw)))
        return self.status(flow_id)

    @staticmethod
    def _result(value: Any, row: dict[str, Any], state: str | None) -> None:
        if state not in {'completed', 'reverted'}:
            _need(value is None)
            return
        _need(type(value) is dict and set(value) == {'install_id', 'generation', 'owner_generation', 'state', 'change_id'}
              and value['install_id'] == row['install_id'] and _match(I, value['change_id'])
              and _integer(value['generation'], 1, 10**15 - 1) and _integer(value['owner_generation'], 1, 10**15 - 1))
        if state == 'completed':
            _need(value['generation'] == row['expected_generation'] + 1
                  and value['owner_generation'] == row['expected_owner_generation'] + 1 and value['state'] == 'verified')
        else:
            _need(value['generation'] >= row['expected_generation'] + 2
                  and value['owner_generation'] == row['expected_owner_generation'] + 2 and value['state'] == 'frozen')

    @staticmethod
    def _change(value: Any) -> None:
        if value is None:
            return
        _need(type(value) is dict and set(value) == {'change_id', 'state', 'created_ms', 'revert_until_ms'}
              and _match(I, value['change_id']) and value['state'] in {'pending', 'completed', 'reverted', 'expired'}
              and _integer(value['created_ms'], 1)
              and (value['revert_until_ms'] is None or _integer(value['revert_until_ms'], 1)))

    def _management(self, value: dict[str, Any], row: dict[str, Any], path: str) -> None:
        _need(value['code'] == 'ok' and value.get('flow_id') == row['flow_id']
              and _integer(value.get('expires_ms'), 1))
        if path == START:
            _need(set(value) == {'v', 'code', 'request_id', 'flow_id', 'state', 'expires_ms', 'verification_state'}
                  and value['state'] == 'pending' and value['verification_state'] in NOTICES
                  and value['expires_ms'] <= self._now() + TTL)
        else:
            _need(set(value) == {'v', 'code', 'request_id', 'flow_id', 'state', 'expires_ms', 'result'}
                  and value['state'] in STATES)
            self._result(value['result'], row, value['state'])
            previous = row['meta']['remote_state']
            _need(previous != 'reverted' or value['state'] == 'reverted')
            _need(previous != 'completed' or value['state'] in {'completed', 'reverted'})
            _need(previous != 'approved' or value['state'] != 'pending')
            _need(previous not in {'expired', 'cancelled'} or value['state'] == previous)
            old = row['meta']['result']
            if old and value['state'] == previous:
                _need(value['result'] == old)

    def _status_shape(self, v: dict[str, Any], row: dict[str, Any], generation: int, owner: int) -> None:
        _need(set(v) == {'v', 'code', 'request_id', 'install_id', 'generation', 'owner_generation', 'state',
                        'address', 'owner', 'key_id', 'queue_count', 'queue_bytes', 'oldest_received_ms',
                        'oldest_expires_ms', 'invalid_message_id_holds', 'current_epoch', 'owner_change',
                        'last_notice_state', 'reason'} and v['code'] == 'ok' and v['install_id'] == self.install_id
              and _integer(v['generation'], 1) and v['generation'] == generation
              and _integer(v['owner_generation'], 1) and v['owner_generation'] == owner
              and v['key_id'] == row['candidate_key_id'] and v['state'] in REGISTRATION_STATES
              and isinstance(v['reason'], str) and v['reason'] in REASONS | {''}
              and (v['last_notice_state'] is None or v['last_notice_state'] in NOTICES))
        for key in ('queue_count', 'queue_bytes', 'invalid_message_id_holds', 'current_epoch'):
            _need(_integer(v[key]))
        for key in ('oldest_received_ms', 'oldest_expires_ms'):
            _need(v[key] is None or _integer(v[key], 1))
        for key in ('address', 'owner'):
            _need(v[key] is None or _mailbox(v[key]) == v[key])
        self._change(v['owner_change'])
        result, change = row['meta']['result'], v['owner_change']
        if result:
            _need(change is not None and change['change_id'] == result['change_id']
                  and change['state'] in ({'reverted'} if row['meta']['remote_state'] == 'reverted'
                                          else {'completed', 'expired'})
                  and v['state'] in ({'frozen', 'disabled', 'deleted'} if result['state'] == 'frozen'
                                    else {'verified', 'active', 'disabled', 'deleted'}))

    def _exchange(self, pin: ServiceKeyCandidate, row: dict[str, Any], path: str, body: bytes,
                  generation: int, owner: int, *, signing_ref: SealReference | None = None
                  ) -> tuple[dict[str, Any], bytes, int]:
        nonce, stamp, ref = secrets.token_hex(16), str(self._now()), signing_ref or self._ref(row)
        _need(ref.generation == generation, 'stale_generation')
        fields = {'version': '2', 'direction': 'install-to-pull', 'key-id': ref.key_id,
                  'install-id': self.install_id, 'generation': str(generation), 'owner-generation': str(owner),
                  'timestamp': stamp, 'nonce': nonce, 'digest': _digest(body)}
        transcript = _json(['clayrune-agent-mail-pull-http', '1', fields['direction'], pin.origin, 'POST', path,
                            ref.key_id, self.install_id, str(generation), str(owner), stamp, nonce, fields['digest']])
        headers = {'x-mail-' + k: v for k, v in fields.items()}
        headers.update({'content-type': 'application/json', 'cache-control': 'no-store',
                        'x-content-type-options': 'nosniff', 'x-mail-signature': self.seal.sign_pull(ref, transcript)})
        try:
            response = self.transport('POST', path, headers, body)
        except Exception:
            _log('[agent-mail] owner_change connection_lost', flush=True)
            raise EnrollmentRefused('connection_lost') from None
        try:
            code, supplied, raw = response
            _need(type(code) is int and (code == 200 or code in ERROR_CODES)
                  and isinstance(supplied, Mapping) and isinstance(raw, bytes) and len(raw) <= 32768)
            output: dict[str, str] = {}
            for key, value in supplied.items():
                _need(isinstance(key, str) and isinstance(value, str) and key.isascii() and value.isascii()
                      and key.lower() not in output)
                output[key.lower()] = value
            _need(sum(len(k) + len(v) + 4 for k, v in output.items()) <= 8192 and 'content-encoding' not in output
                  and re.fullmatch(r'application/json(?:;\s*charset=utf-8)?', output.get('content-type', ''), re.I))
            expected = {**fields, 'direction': 'pull-to-install', 'key-id': pin.key_id, 'digest': _digest(raw)}
            for key, value in expected.items():
                if key != 'timestamp':
                    _need(output.get('x-mail-' + key) == value)
            issued = output.get('x-mail-timestamp', '')
            _need(re.fullmatch(r'0|[1-9][0-9]{0,15}', issued) and _integer(int(issued))
                  and abs(self._now() - int(issued)) <= SKEW and self._now() < pin.expires_at_ms)
            proof = _json(['clayrune-agent-mail-pull-http', '1', 'pull-to-install', pin.origin, 'POST', path,
                           pin.key_id, self.install_id, str(generation), str(owner), issued, nonce, str(code), _digest(raw)])
            Ed25519PublicKey.from_public_bytes(unbase64url(pin.public_key, 32)).verify(
                unbase64url(output.get('x-mail-signature', ''), 64), proof)
            _need(abs(self._now() - int(issued)) <= SKEW and self._now() < pin.expires_at_ms)
            value = _object(raw)
            _need(value.get('v') == 1 and type(value['v']) is int
                  and value.get('request_id') == (row['meta']['op_id'] if path == START else nonce))
            if code != 200:
                _need(set(value) in ({'v', 'code', 'request_id'}, {'v', 'code', 'request_id', 'retry_after_ms'})
                      and value.get('code') in ERROR_CODES[code]
                      and ('retry_after_ms' not in value or code in {429, 503} and _integer(value['retry_after_ms'])))
            return value, raw, code
        except (ValueError, TypeError, KeyError, AttributeError, InvalidSignature, IngressRefused, RecursionError):
            raise EnrollmentRefused('response_unverified') from None
