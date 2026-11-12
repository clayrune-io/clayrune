"""Human-authorized pull signup; no sockets, activation, schema creation or wiring.

Composition supplies the SAME guarded MailStore with the complete checked pull
extension (contract section 9), IdentitySeal and a reviewed_service callback.
That callback must revalidate root-signed manifest evidence, its durable CAS
floor and human pin approval on every call; returning a candidate is a trusted
internal seam, never an HTTP-supplied pin. A changed pin cannot rewrite a flow.

The flow result_json holds versioned local intent metadata and exact start
bytes, not a second authority registry. The existing account/generation rows
own authority. Fence plaintext is deliberately cancellation-only; seed and flow
secret stay in IdentitySeal. No private owner mailbox is returned or logged.
Public methods return closed observable reasons; no transport exception text.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from typing import Any, Callable, Mapping, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal, SealReference
from mc.agent_mail_service_manifest import ManifestRefused, ServiceKeyCandidate
from mc.agent_mail_signing import IngressRefused, unbase64url
from mc.agent_mail_store import MailStore, MailStoreCorrupt, MailStoreError
from mc.core import _log

START = '/v1/pull/signup/start'
STATUS = '/v1/pull/signup/status'
SKEW = 300_000
TTL = 1_800_000
MAX_INT = 9_007_199_254_740_991
I = re.compile(r'[0-9a-f]{32}')
H = re.compile(r'[0-9a-f]{64}')
ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
ORIGIN = re.compile(r'https://(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?')
LABEL = r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?'
MAILBOX = re.compile(r"([A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*)@(" + LABEL + r'(?:\.' + LABEL + r')+)')
ERRORS = {400: {'invalid_request', 'invalid_json'}, 401: {'unauthorized'},
          404: {'not_found'}, 408: {'body_timeout'},
          409: {'operation_conflict', 'nonce_replayed', 'stale_generation', 'owner_limit', 'fenced', 'already_sent'},
          410: {'flow_expired', 'token_expired'}, 413: {'body_limit'},
          429: {'request_limit', 'verification_limit', 'abuse_limit'},
          503: {'storage_unavailable', 'service_unavailable'}}
LOCAL_REASONS = frozenset().union(*ERRORS.values(), {
    'response_unverified', 'vault_locked', 'schema_corrupt', 'service_key_changed',
    'human_required', 'identity_unavailable_reenrollment_required', 'seal_missing',
    'version_unsupported', 'vault_key_invalid', 'identity_material_unavailable',
    'identity_material_invalid', 'invalid_flow', 'connection_lost',
    'local_disabled',
})


class SignupTransport(Protocol):
    """Exact raw bytes; adapter enforces pinned HTTPS/no redirects/10s timeout."""

    def __call__(self, method: str, path: str, headers: Mapping[str, str], body: bytes, /
                 ) -> tuple[int, Mapping[str, str], bytes]: ...


def _need(ok: object, reason: str = 'response_unverified') -> None:
    if not ok:
        raise EnrollmentRefused(reason)


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError()
    return result


def _object(raw: bytes) -> dict[str, Any]:
    # These response/local metadata objects are flat, with bounded string values.
    def integer(text: str) -> int:
        if text == '-0':
            raise ValueError()
        return int(text)

    def invalid_number(text: str) -> None:
        raise ValueError()

    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                       parse_int=integer, parse_float=invalid_number, parse_constant=invalid_number)
    _need(type(value) is dict)
    _need(all(type(v) in (str, int, type(None)) for v in value.values()))
    _need(all(type(v) is not int or 0 <= v <= MAX_INT for v in value.values()))
    _need(all(type(v) is not str or not any(0xD800 <= ord(c) <= 0xDFFF for c in v) for v in value.values()))
    return value


def _mailbox(value: str) -> str:
    match = MAILBOX.fullmatch(value) if isinstance(value, str) else None
    _need(match and len(value) <= 254 and len(match[1]) <= 64, 'invalid_request')
    assert match is not None
    return match[1] + '@' + match[2].lower()


class Signup:
    def __init__(self, store: MailStore, seal: IdentitySeal, transport: SignupTransport, *,
                 reviewed_service: Callable[[], tuple[ServiceKeyCandidate, str]],
                 authorize: Callable[[], bool], clock_ms: Callable[[], int] | None = None):
        self.store, self.seal, self.transport = store, seal, transport
        self.reviewed_service, self.authorize = reviewed_service, authorize
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def _now(self) -> int:
        now = self.clock_ms()
        _need(type(now) is int and 0 < now <= MAX_INT - TTL, 'invalid_request')
        return now

    def owner_change(self, *, install_id: str):
        """Compose the separate human adapter with the same custody/trust gates.

        No action or network here. This static import also makes the unwired
        module reachable to frozen-app packaging until HTTP composition lands.
        """
        from mc.agent_mail_owner_change import OwnerChange
        return OwnerChange(self.store, self.seal, self.transport, install_id=install_id,
                           reviewed_service=self.reviewed_service, authorize=self.authorize, clock_ms=self.clock_ms)

    def _service(self, row: dict[str, Any] | None = None) -> tuple[ServiceKeyCandidate, str]:
        try:
            pin, digest = self.reviewed_service()
        except (ManifestRefused, EnrollmentRefused):
            raise
        except Exception:
            _log('[agent-mail] signup service_unavailable', flush=True)
            raise EnrollmentRefused('service_unavailable') from None
        _need(ORIGIN.fullmatch(pin.origin) and len(pin.origin) <= 261 and ID.fullmatch(pin.key_id) and H.fullmatch(digest)
              and type(pin.sequence) is int and 1 <= pin.sequence < 10**15
              and self._now() < pin.expires_at_ms, 'service_key_changed')
        unbase64url(pin.public_key, 32)
        if row is not None:
            _need((pin.origin, pin.key_id, pin.public_key, pin.sequence, digest) ==
                  (row['service_origin'], row['service_key_id'], row['service_public_key'],
                   row['manifest_sequence'], row['manifest_digest']), 'service_key_changed')
        return pin, digest

    def _row(self, db: sqlite3.Connection) -> dict[str, Any] | None:
        # Full schema validation belongs to unit 15/22; this unit never creates
        # missing tables. A direct caller with an absent extension fails closed.
        try:
            rows = db.execute('''SELECT f.*,a.service_origin,a.address,a.owner_address,
                a.key_id,a.seal_flow_id AS account_seal,a.state AS account_state,
                a.local_enabled,a.consent_version,a.generation AS account_generation,
                a.owner_generation AS account_owner_generation,a.consent_epoch AS account_consent_epoch,g.service_key_id,
                g.service_public_key,g.manifest_sequence,g.manifest_digest,
                g.state AS generation_state,g.seal_flow_id AS generation_seal,g.key_id AS generation_key
                FROM mail_pull_flows f JOIN mail_pull_accounts a USING(install_id)
                JOIN mail_pull_generations g ON g.install_id=f.install_id
                AND g.generation=f.expected_generation WHERE f.purpose='signup' ''').fetchall()
        except sqlite3.OperationalError as exc:
            if not any(s in str(exc).lower() for s in ('no such table', 'no such column')):
                raise
            raise EnrollmentRefused('schema_corrupt') from None
        _need(len(rows) <= 1, 'operation_conflict')
        if not rows:
            # Lost flow/generation rows are not an invitation to recreate one.
            _need(not db.execute('''SELECT 1 FROM mail_pull_accounts UNION ALL
                SELECT 1 FROM mail_pull_flows UNION ALL SELECT 1 FROM mail_pull_generations LIMIT 1''').fetchone(),
                  'schema_corrupt')
            return None
        row = dict(rows[0])
        try:
            _need(isinstance(row['result_json'], str) and len(row['result_json']) <= 16384, 'schema_corrupt')
            meta = _object(row['result_json'].encode('utf-8'))
            _need(set(meta) == {'v', 'op_id', 'public_key', 'start_body', 'started', 'notice_state', 'fence_token'}, 'schema_corrupt')
            _need(meta['v'] == 1 and type(meta['v']) is int and I.fullmatch(meta['op_id'])
                  and H.fullmatch(meta['fence_token']) and meta['started'] in (0, 1) and type(meta['started']) is int
                  and (meta['notice_state'] is None or meta['notice_state'] in {'queued', 'accepted', 'uncertain', 'failed'}), 'schema_corrupt')
            _need(all(I.fullmatch(row[k]) for k in ('flow_id', 'install_id', 'seal_flow_id'))
                  and row['seal_flow_id'] == row['flow_id'] == row['account_seal'] == row['generation_seal']
                  and row['key_id'] == row['candidate_key_id'] == row['generation_key'] and ID.fullmatch(row['key_id'])
                  and row['expected_generation'] == row['expected_owner_generation'] == 1
                  and row['human_authorized_ms'] > 0 and row['consent_epoch'] > 0
                  and type(row['expires_ms']) is int and row['expires_ms'] > 0, 'schema_corrupt')
            _need(row['account_generation'] == row['account_owner_generation'] == 1
                  and row['consent_epoch'] == row['account_consent_epoch']
                  and row['generation_state'] == 'current'
                  and row['account_state'] in {'pending', 'verified'} and row['local_enabled'] == 0,
                  'stale_generation')
            _need(row['state'] in {'pending', 'verified', 'completed', 'expired', 'cancelled'}, 'local_disabled')
            _need(not db.execute('''SELECT 1 FROM mail_pull_fences WHERE install_id=? AND generation=1
                AND (mode='revoke' OR flow_id=?)''', (row['install_id'], row['flow_id'])).fetchone(), 'fenced')
            if meta['public_key']:
                unbase64url(meta['public_key'], 32)
                body = meta['start_body'].encode('ascii')
                _need(len(body) <= 8192, 'schema_corrupt')
                parsed = _object(body)
                _need(_digest(body) == row['input_digest'] and parsed == self._body(row, meta['public_key']), 'schema_corrupt')
            else:
                _need(meta['start_body'] is None and meta['started'] == 0 and row['input_digest'] == '0' * 64, 'schema_corrupt')
            row['meta'] = meta
            return row
        except EnrollmentRefused as exc:
            if str(exc) == 'response_unverified':
                raise EnrollmentRefused('schema_corrupt') from None
            raise
        except (ValueError, TypeError, KeyError, AttributeError, IngressRefused, RecursionError):
            raise EnrollmentRefused('schema_corrupt') from None

    @staticmethod
    def _ref(row: dict[str, Any]) -> SealReference:
        return SealReference(row['seal_flow_id'], row['install_id'], 1, row['candidate_key_id'])

    def _body(self, row: dict[str, Any], public: str) -> dict[str, Any]:
        meta = row['meta'] if 'meta' in row else json.loads(row['result_json'])
        ref = self._ref(row)
        # Check the observable key state before the legacy flow_secret accessor,
        # which deliberately retains its old lock/missing reason strings.
        reason = self.seal.status(ref)
        _need(reason == 'ready', reason)
        secret = self.seal.flow_secret(ref.flow, ref.install, 1, ref.key_id).hex()
        return {'op_id': meta['op_id'],
                'flow_id': row['flow_id'], 'public_key': public, 'key_id': ref.key_id,
                'owner': row['owner_address'], 'flow_secret_hash': _digest(secret.encode('ascii')),
                'fence_hash': _digest(meta['fence_token'].encode('ascii')), 'consent_version': row['consent_version'],
                'manifest_sequence': row['manifest_sequence'], 'manifest_digest': row['manifest_digest']}

    @staticmethod
    def _view(row: dict[str, Any] | None, reason: str = '') -> dict[str, Any]:
        state = 'not_enrolled' if row is None else row['state']
        if row and row['state'] == 'pending' and not row['meta']['started']:
            state = 'start_pending' if row['meta']['public_key'] else 'preparing'
        return {'state': 'held' if reason else state, 'reason': reason, 'enabled': False,
                'install_id': row['install_id'] if row else None, 'flow_id': row['flow_id'] if row else None,
                'address': row['address'] if row else '', 'expires_ms': row['expires_ms'] if row else None,
                'verification_state': row['meta']['notice_state'] if row else None}

    def _run(self, action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return action()
        except EnrollmentRefused as exc:
            reason = 'vault_locked' if str(exc) == 'vault_locked_or_unconfigured' else str(exc)
            return self._view(None, reason if reason in LOCAL_REASONS else 'service_unavailable')
        except ManifestRefused:
            return self._view(None, 'service_key_changed')
        except IngressRefused:
            return self._view(None, 'response_unverified')
        except MailStoreCorrupt:
            return self._view(None, 'store_corrupt')
        except (MailStoreError, OSError):
            _log('[agent-mail] signup storage_unavailable', flush=True)
            return self._view(None, 'storage_unavailable')

    def status(self) -> dict[str, Any]:
        """Local read only: never creates state, generates keys or calls transport."""
        def read() -> dict[str, Any]:
            with self.store._read() as db:
                row = self._row(db)
            if row is None:
                return self._view(None)
            if row['state'] not in {'completed', 'cancelled', 'expired'} and self._now() >= row['expires_ms']:
                return {**self._view(row), 'state': 'expired', 'reason': 'flow_expired'}
            reason = self.seal.status(self._ref(row))
            if not row['meta']['public_key'] and reason == 'seal_missing':
                reason = 'ready'  # Durable draft, generation has not committed yet.
            return self._view(row, '' if reason == 'ready' else reason)
        return self._run(read)

    def start(self, owner: str, consent_version: str, *, op_id: str | None = None) -> dict[str, Any]:
        """Human-only intent. Omitted op_id is generated once and durably reused.

        Supplying a different owner, consent or operation cannot modify an existing
        flow, including an expired flow. Fresh-signup/recovery is a separate unit.
        """
        def begin() -> dict[str, Any]:
            IdentitySeal._authorize(self.authorize)
            canonical_owner = _mailbox(owner)
            _need(isinstance(consent_version, str) and ID.fullmatch(consent_version)
                  and (op_id is None or isinstance(op_id, str) and I.fullmatch(op_id)), 'invalid_request')
            pin, manifest_digest = self._service()
            now = self._now()
            with self.store._tx() as db:
                row = self._row(db)
                if row is not None:
                    _need(row['owner_address'] == canonical_owner and row['consent_version'] == consent_version
                          and (op_id is None or op_id == row['meta']['op_id']), 'operation_conflict')
                else:
                    _need(not any(self.seal.folder.glob('*.sealed')), 'seal_missing')
                    install, flow, key_id = (secrets.token_hex(16) for _ in range(3))
                    meta = {'v': 1, 'op_id': op_id or secrets.token_hex(16), 'public_key': '',
                            'start_body': None, 'started': 0, 'notice_state': None, 'fence_token': secrets.token_hex(32)}
                    db.execute('''INSERT INTO mail_pull_accounts VALUES(?,1,1,?,'',?,?,?,'pending',0,?,1,'',?)''',
                               (install, pin.origin, canonical_owner, key_id, flow, consent_version, now))
                    db.execute("INSERT INTO mail_pull_generations VALUES(?,1,1,?,?,?,?,?,?,'current',?,NULL)",
                               (install, key_id, flow, pin.sequence, manifest_digest, pin.key_id, pin.public_key, now))
                    db.execute("INSERT INTO mail_pull_flows VALUES(?,?,'signup',1,1,?,?,?, ?,1,'pending',?,'',?)",
                               (flow, install, key_id, flow, '0' * 64, now, now + TTL, _json(meta).decode('ascii')))
                    # Keep the denial capability with this flow until an actual
                    # cancel/revoke intent exists. Inserting a pending fence
                    # outbox row here would cancel the signup on supervision.
            return self._resume()
        return self._run(begin)

    def resume(self) -> dict[str, Any]:
        """Retry a durable authorized start, or query its status after restart."""
        return self._run(self._resume)

    def _resume(self) -> dict[str, Any]:
        with self.store._read() as db:
            observed = self._row(db)
        if observed is None:
            return self._view(None)
        if observed['state'] in {'expired', 'cancelled', 'completed'}:
            return {**self._view(observed), 'reason': 'flow_expired' if observed['state'] == 'expired' else ''}
        # The manifest verifier may CAS its floor in this SAME database. Never
        # invoke it inside a lifecycle transaction (or retain a read connection).
        pin = self._service(observed)[0] if self._now() < observed['expires_ms'] else None
        with self.store._tx() as db:
            row = self._current(db, observed, _json(observed['meta']))
            if self._now() >= row['expires_ms']:
                db.execute("UPDATE mail_pull_flows SET state='expired',reason='flow_expired' WHERE flow_id=?", (row['flow_id'],))
                return {**self._view(row), 'state': 'expired', 'reason': 'flow_expired'}
            _need(pin is not None, 'service_key_changed')
            assert pin is not None
            meta = row['meta']
            if not meta['public_key']:
                # Authorization is the durable human intent, not a remote flag.
                # Orphan seals reopen after rollback; committed missing seals
                # never pass expected_public_key=None again.
                public = self.seal.generate_pull(self._ref(row), authorize=lambda: row['human_authorized_ms'] > 0,
                                                 expected_public_key=None)
                meta['public_key'] = public
                meta['start_body'] = _json(self._body(row, public)).decode('ascii')
                row['input_digest'] = _digest(meta['start_body'].encode('ascii'))
                db.execute('UPDATE mail_pull_flows SET input_digest=?,result_json=? WHERE flow_id=?',
                           (row['input_digest'], _json(meta).decode('ascii'), row['flow_id']))
            else:
                reason = self.seal.status(self._ref(row))
                _need(reason == 'ready', reason)
                _need(self.seal.public(row['flow_id'], row['install_id'], 1, row['candidate_key_id']) == meta['public_key'],
                      'operation_conflict')
            snapshot = _json(meta)
        # No SQLite transaction is held across transport. Exact bytes/op survive
        # a crash before send, after remote commit, or before local receipt commit.
        path = STATUS if meta['started'] else START
        if path == START:
            body = meta['start_body'].encode('ascii')
        else:
            ref = self._ref(row)
            body = _json({'flow_id': ref.flow, 'flow_secret': self.seal.flow_secret(ref.flow, ref.install, 1, ref.key_id).hex()})
        nonce, stamp = secrets.token_hex(16), str(self._now())
        fields = {'version': '2', 'direction': 'install-to-pull', 'key-id': row['candidate_key_id'],
                  'install-id': row['install_id'], 'generation': '1', 'owner-generation': '1',
                  'timestamp': stamp, 'nonce': nonce, 'digest': _digest(body)}
        transcript = _json(['clayrune-agent-mail-pull-http', '1', fields['direction'], pin.origin,
                            'POST', path, fields['key-id'], row['install_id'], '1', '1', stamp, nonce, fields['digest']])
        headers = {'x-mail-' + k: v for k, v in fields.items()}
        headers.update({'content-type': 'application/json', 'cache-control': 'no-store',
                        'x-content-type-options': 'nosniff', 'x-mail-signature': self.seal.sign_pull(self._ref(row), transcript)})
        try:
            response = self.transport('POST', path, headers, body)
        except Exception:
            _log('[agent-mail] signup connection_lost', flush=True)
            return self._view(row, 'connection_lost')
        value, raw, http_status = self._verify(response, row, pin, path, nonce)
        # A verified error is failure evidence only; never binding/enable evidence.
        if http_status != 200:
            if value['code'] in {'flow_expired', 'token_expired'}:
                return self._expire(row, snapshot)
            return self._view(row, value['code'])
        self._service(row)  # Pin/floor/expiry may change while the request is out.
        with self.store._tx() as db:
            current = self._current(db, row, snapshot)
            _need(self._now() < current['expires_ms'], 'flow_expired')
            meta['started'], meta['notice_state'] = 1, value['verification_state']
            state = {'active': 'completed', 'cancelled': 'cancelled'}.get(value['state'], value['state'])
            # Signup evidence cannot arm consent or enable local execution.
            account_state = 'verified' if value['state'] in {'verified', 'active'} else current['account_state']
            db.execute('UPDATE mail_pull_accounts SET address=?,state=?,updated_ms=? WHERE install_id=?',
                       (value['address'], account_state, self._now(), row['install_id']))
            db.execute('UPDATE mail_pull_flows SET state=?,expires_ms=?,reason=?,result_json=? WHERE flow_id=?',
                       (state, value['expires_ms'], 'flow_expired' if state == 'expired' else '', _json(meta).decode('ascii'), row['flow_id']))
            db.execute('INSERT OR REPLACE INTO mail_pull_status VALUES(?,?,1,1,?,?)',
                       (row['install_id'], self._now(), raw.decode('utf-8'), _digest(raw)))
        return self.status()

    def _current(self, db: sqlite3.Connection, row: dict[str, Any], snapshot: bytes) -> dict[str, Any]:
        current = self._row(db)
        _need(current and current['flow_id'] == row['flow_id'] and _json(current['meta']) == snapshot
              and all(current[k] == row[k] for k in ('state', 'consent_epoch', 'human_authorized_ms', 'expires_ms')),
              'stale_generation')
        assert current is not None
        return current

    def _expire(self, row: dict[str, Any], snapshot: bytes) -> dict[str, Any]:
        with self.store._tx() as db:
            self._current(db, row, snapshot)
            db.execute("UPDATE mail_pull_flows SET state='expired',reason='flow_expired' WHERE flow_id=?", (row['flow_id'],))
        return {**self._view(row), 'state': 'expired', 'reason': 'flow_expired'}

    def _verify(self, response: tuple[int, Mapping[str, str], bytes], row: dict[str, Any],
                pin: ServiceKeyCandidate, path: str, nonce: str) -> tuple[dict[str, Any], bytes, int]:
        try:
            status, supplied_headers, raw = response
            _need(type(status) is int and (status == 200 or status in ERRORS)
                  and isinstance(raw, bytes) and len(raw) <= 32768 and isinstance(supplied_headers, Mapping))
            headers: dict[str, str] = {}
            for key, value in supplied_headers.items():
                _need(isinstance(key, str) and isinstance(value, str) and key.lower() not in headers)
                _need(key.isascii() and value.isascii())
                headers[key.lower()] = value
            _need(sum(len(k) + len(v) + 4 for k, v in headers.items()) <= 8192
                  and 'content-encoding' not in headers
                  and re.fullmatch(r'application/json(?:;\s*charset=utf-8)?', headers.get('content-type', ''), re.I))
            expected = {'version': '2', 'direction': 'pull-to-install', 'key-id': pin.key_id,
                        'install-id': row['install_id'], 'generation': '1', 'owner-generation': '1',
                        'nonce': nonce, 'digest': _digest(raw)}
            _need(all(headers.get('x-mail-' + k) == v for k, v in expected.items()))
            stamp = headers.get('x-mail-timestamp', '')
            _need(re.fullmatch(r'0|[1-9][0-9]{0,15}', stamp) and int(stamp) <= MAX_INT
                  and abs(self._now() - int(stamp)) <= SKEW and self._now() < pin.expires_at_ms)
            transcript = _json(['clayrune-agent-mail-pull-http', '1', 'pull-to-install', pin.origin,
                                'POST', path, pin.key_id, row['install_id'], '1', '1', stamp, nonce, str(status), _digest(raw)])
            Ed25519PublicKey.from_public_bytes(unbase64url(pin.public_key, 32)).verify(
                unbase64url(headers.get('x-mail-signature', ''), 64), transcript)
            value = _object(raw)
            _need(value.get('v') == 1 and type(value['v']) is int
                  and value.get('request_id') == (row['meta']['op_id'] if path == START else nonce))
            if status != 200:
                _need(value.get('code') in ERRORS[status] and set(value) in (
                    {'v', 'code', 'request_id'}, {'v', 'code', 'request_id', 'retry_after_ms'}))
                _need('retry_after_ms' not in value or status in (429, 503) and type(value['retry_after_ms']) is int)
                return value, raw, status
            _need(set(value) == {'v', 'code', 'request_id', 'flow_id', 'install_id', 'generation',
                                'owner_generation', 'address', 'state', 'expires_ms', 'verification_state'}
                  and value['code'] == 'ok' and value['flow_id'] == row['flow_id']
                  and value['install_id'] == row['install_id'] and type(value['generation']) is int
                  and type(value['owner_generation']) is int and value['generation'] == value['owner_generation'] == 1
                  and value['state'] in ({'pending'} if path == START else {'pending', 'verified', 'active', 'expired', 'cancelled'})
                  and value['verification_state'] in {'queued', 'accepted', 'uncertain', 'failed'}
                  and type(value['expires_ms']) is int and 0 < value['expires_ms'] <= int(stamp) + TTL)
            _need(_mailbox(value['address']) == value['address'] and re.fullmatch(r'm[0-9a-f]{29}', value['address'].split('@')[0]))
            _need(not row['address'] or row['address'] == value['address'])
            _need(not row['meta']['started'] or value['expires_ms'] == row['expires_ms'])
            _need(row['state'] != 'verified' or value['state'] != 'pending')
            _need(value['state'] in {'expired', 'cancelled'} or self._now() < value['expires_ms'])
            return value, raw, status
        except (ValueError, TypeError, KeyError, AttributeError, InvalidSignature, IngressRefused, EnrollmentRefused, RecursionError):
            raise EnrollmentRefused('response_unverified') from None
