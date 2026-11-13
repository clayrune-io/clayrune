"""Bounded same-origin pull HTTP transport (MC-1166 unit 21), no import I/O.

HttpsTransport(origin=...) implements SignupTransport for signup/owner/socket
composition. It returns bounded raw bytes, never authority. ServiceClient adds
reviewed root-manifest trust and v2 verification before returning any response.
Its reviewed_service callback MUST revalidate signed evidence, durable rollback
floor and human pin approval on every call, as in Signup/PullClient. No repinning.

The pre-signed callable seam validates the common response envelope; individual
route owners still validate their closed result shapes before changing state.
status() and fence() below own those two shapes. Neither changes local consent,
account authority or queue state. Future lifecycle composition owns fence ACK
commit and races. Fence calls read only durable cancellation secrets, not seals.

Legacy constructor/import names remain for units 22/23/26. Retired lifecycle
operations refuse without network; verify_response remains v1 ONLY for the
existing ceremony importer until its replacement. Never use it for pull proof.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mc.agent_mail_delivery_proof import DeliveryProof
from mc.agent_mail_enrollment import OwnershipReceipt, PendingIdentity
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal, SealReference
from mc.agent_mail_enrollment_revocations import CancelledAssignment
from mc.agent_mail_envelope import _MID
from mc.agent_mail_lifecycle_fence import LifecycleFences
from mc.agent_mail_pull_schema import _validate
from mc.agent_mail_remote import REMOTE_UNAVAILABLE, RemoteUnavailable
from mc.agent_mail_service_manifest import ServiceKeyCandidate
from mc.agent_mail_signing import HEX, ID, SKEW_MS, IngressRefused, unbase64url
from mc.agent_mail_signup import H, I, MAX_INT, ORIGIN, _json, _mailbox
from mc.agent_mail_store import MailStore
from mc.core import _log

# Compatibility constants for the retiring ceremony/lifecycle importers.
PREFIX = '/v1/mail/lifecycle'
LIFECYCLE_DIRECTION = 'install-to-service-lifecycle'
RESPONSE_DIRECTION = 'service-to-install-response'
_ORIGIN = ORIGIN
REQUEST_LIMIT = 8192
SEND_LIMIT = 128 * 1024
RESPONSE_LIMIT = 32 * 1024
HEADER_LIMIT = 8192
HTTP_TIMEOUT_S = 10.0
EVIDENCE_MAX_AGE_MS = 120_000
LEASE_MS = 300_000
PULL_PREFIX = '/v1/pull'
STATUS = PULL_PREFIX + '/status'
FENCE = PULL_PREFIX + '/fence'
ROUTES = frozenset(PULL_PREFIX + s for s in (
    '/signup/start', '/signup/status', '/activate', '/status', '/ticket', '/send',
    '/send/status', '/fence', '/management/start', '/management/status', '/key/rotate'))
SEND = PULL_PREFIX + '/send'
_RECEIPT_FIELDS = frozenset({'intent_id', 'thread_id', 'message_id', 'state', 'provider_id'})
_RECEIPT_STATES = frozenset({'reserved', 'sending', 'uncertain', 'failed', 'revoked'})
ERRORS = {
    400: {'invalid_request', 'invalid_json', 'invalid_send', 'invalid_message_id', 'invalid_text',
          'invalid_hold', 'auth_evidence', 'event_identity', 'text_digest', 'unsupported_frame'},
    401: {'unauthorized', 'ticket_invalid', 'session_required', 'csrf_failed'},
    403: {'human_required', 'passcode_required', 'old_mailbox_required', 'consent_required'},
    404: {'not_found', 'unknown_thread', 'unknown_intent'}, 408: {'body_timeout'},
    409: {'operation_conflict', 'intent_conflict', 'message_id_conflict', 'ack_conflict', 'nonce_replayed',
          'stale_generation', 'connection_superseded', 'lease_unknown', 'registration_inactive',
          'registration_frozen', 'verification_pending', 'owner_limit', 'change_limit', 'change_superseded',
          'revocation_pending', 'uncertain', 'fenced', 'revoked', 'already_sent', 'same_owner'},
    410: {'flow_expired', 'event_expired', 'token_expired', 'legacy_retired'},
    413: {'body_limit', 'frame_limit'}, 426: {'version_unsupported'},
    429: {'request_limit', 'send_limit', 'ingress_limit', 'verification_limit', 'queue_full', 'abuse_limit'},
    503: {'mail_disabled', 'manifest_not_configured', 'manifest_malformed', 'storage_unavailable',
          'provider_unavailable', 'provider_suppressed', 'provider_quota', 'service_unavailable'},
}
REASONS = frozenset().union(*ERRORS.values(), {
    'response_unverified', 'vault_locked', 'store_missing', 'store_corrupt', 'marker_missing',
    'marker_corrupt', 'seal_missing', 'schema_corrupt', 'local_disabled', 'conversation_busy',
    'reauth_due', 'service_key_changed', 'empty_body', 'queue_expired', 'connection_lost',
    'extraction_unavailable', 'extraction_ambiguous', 'history_missing', 'target_closed',
    'provider_uncertain', 'notice_pending', 'notice_failed', 'notice_uncertain', 'old_mailbox_lost', ''})


class ServiceRefused(EnrollmentRefused):
    """Code-owned closed reason only; never remote text or exception detail."""


def _need(ok: object, reason: str = 'response_unverified') -> None:
    if not ok:
        raise ServiceRefused(reason)


def _match(pattern: re.Pattern[str], value: Any) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _integer(value: Any, low: int = 0, high: int = MAX_INT) -> bool:
    return type(value) is int and low <= value <= high


def _headers(supplied: Mapping[str, str]) -> dict[str, str]:
    _need(isinstance(supplied, Mapping))
    headers: dict[str, str] = {}
    for k, v in supplied.items():
        _need(isinstance(k, str) and isinstance(v, str) and k.isascii() and v.isascii()
              and k.lower() not in headers and not any(c in k + v for c in '\r\n\x00'))
        headers[k.lower()] = v
    _need(sum(len(k) + len(v) + 4 for k, v in headers.items()) <= HEADER_LIMIT
          and 'content-encoding' not in headers)
    return headers


def _object(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value = dict(items)
        _need(len(value) == len(items))
        return value

    def invalid(_: str) -> None:
        raise ServiceRefused('response_unverified')

    def integer(s: str) -> int:
        _need(s != '-0' and _integer(int(s)))
        return int(s)

    def check(v: Any, depth: int = 0) -> None:
        _need(depth <= 5)
        if type(v) is dict:
            for k, item in v.items():
                k.encode('utf-8')
                check(item, depth + 1)
        elif type(v) is str:
            v.encode('utf-8')
        else:
            _need(v is None or _integer(v))

    value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_int=integer,
                       parse_float=invalid, parse_constant=invalid)
    _need(type(value) is dict)
    check(value)
    return value


def verify_pull_response(response: tuple[int, Mapping[str, str], bytes], *, candidate: ServiceKeyCandidate,
                         install: str, generation: int, owner_generation: int, path: str,
                         nonce: str, request_id: str, clock_ms: Callable[[], int],
                         request: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Verify v2 before parsing. Signed errors prove failure only; caller owns result shape."""
    try:
        status, supplied, raw = response
        _need(type(status) is int and (status == 200 or status in ERRORS)
              and isinstance(raw, bytes) and len(raw) <= RESPONSE_LIMIT)
        h = _headers(supplied)
        _need(re.fullmatch(r'application/json(?:;\s*charset=utf-8)?', h.get('content-type', ''), re.I))
        expected = {'version': '2', 'direction': 'pull-to-install', 'key-id': candidate.key_id,
                    'install-id': install, 'generation': str(generation),
                    'owner-generation': str(owner_generation), 'nonce': nonce,
                    'digest': hashlib.sha256(raw).hexdigest()}
        _need(all(h.get('x-mail-' + k) == v for k, v in expected.items()))
        stamp = h.get('x-mail-timestamp', '')
        _need(re.fullmatch(r'0|[1-9][0-9]{0,15}', stamp) and _integer(int(stamp))
              and abs(clock_ms() - int(stamp)) <= SKEW_MS and clock_ms() < candidate.expires_at_ms)
        transcript = _json(['clayrune-agent-mail-pull-http', '1', 'pull-to-install', candidate.origin,
            'POST', path, candidate.key_id, install, str(generation), str(owner_generation),
            stamp, nonce, str(status), expected['digest']])
        Ed25519PublicKey.from_public_bytes(unbase64url(candidate.public_key, 32)).verify(
            unbase64url(h.get('x-mail-signature', ''), 64), transcript)
        _need(abs(clock_ms() - int(stamp)) <= SKEW_MS and clock_ms() < candidate.expires_at_ms)
        value = _object(raw)
        _need(type(value.get('v')) is int and value['v'] == 1 and value.get('request_id') == request_id)
        if status == 200:
            _need(value.get('code') == ('accepted' if path == PULL_PREFIX + '/send' else
                                      'fenced' if path == FENCE else 'ok'))
        elif status == 409 and path == SEND and _RECEIPT_FIELDS.intersection(value):
            # Contract section 7: the sole structured error exception. A signed
            # intent receipt is reconciliation metadata, never send acceptance.
            _need(value.keys() == _RECEIPT_FIELDS | {'v', 'code', 'request_id'}
                  and isinstance(value['state'], str) and value['state'] in _RECEIPT_STATES
                  and value['code'] == value['state'] and request is not None)
            assert request is not None
            _need(all(_match(ID, value[k]) and value[k] == request.get(k) for k in ('intent_id', 'thread_id')))
            mid, provider = value['message_id'], value['provider_id']
            _need(isinstance(mid, str) and mid.isascii() and len(mid) <= 8192 and _MID.fullmatch(mid))
            _need(isinstance(provider, str) and len(provider) <= 256
                  and (provider == '' or re.fullmatch(r'[\x20-\x7e]+', provider) and provider.strip())
                  and (value['state'] in {'uncertain', 'revoked'} or provider == ''))
        else:
            _need(isinstance(value.get('code'), str) and value['code'] in ERRORS[status]
                  and value.keys() in ({'v', 'code', 'request_id'},
                                       {'v', 'code', 'request_id', 'retry_after_ms'})
                  and ('retry_after_ms' not in value or status in (429, 503)
                       and _integer(value['retry_after_ms'])))
        return value
    except (ValueError, TypeError, KeyError, AttributeError, InvalidSignature, IngressRefused, RecursionError):
        raise ServiceRefused('response_unverified') from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class HttpsTransport:
    """Raw bounded POSTs. origin-bound __call__ satisfies SignupTransport.

    post retains the explicit-origin legacy factory seam; responses here are
    untrusted bytes. Client/ceremony verifiers must run before interpreting them.
    The opener seam is for fake transports/tests only; production builds no-redirect
    urllib with default certificate verification. Construction does no network.
    """

    def __init__(self, *, origin: str | None = None, timeout_s: float = HTTP_TIMEOUT_S, opener: Any = None):
        _need(type(timeout_s) in (int, float) and math.isfinite(timeout_s)
              and 0 < timeout_s <= HTTP_TIMEOUT_S, 'invalid_timeout')
        _need(origin is None or _match(ORIGIN, origin) and len(origin) <= 261, 'service_origin_invalid')
        self.origin, self.timeout_s = origin, timeout_s
        self._opener = opener or urllib.request.build_opener(_NoRedirect)

    def __call__(self, method: str, path: str, headers: Mapping[str, str], body: bytes, /
                 ) -> tuple[int, Mapping[str, str], bytes]:
        _need(method == 'POST' and path in ROUTES and self.origin is not None, 'invalid_request')
        assert self.origin is not None
        r = self.post(self.origin, path, headers, body)
        return r['status'], r['headers'], r['body']

    def post(self, origin: str, path: str, headers: Mapping[str, str], body: bytes) -> dict[str, Any]:
        _need(_match(ORIGIN, origin) and len(origin) <= 261 and (self.origin is None or origin == self.origin)
              and isinstance(path, str) and re.fullmatch(r'/[A-Za-z0-9/_-]{1,200}', path), 'invalid_request')
        _need(isinstance(body, bytes) and len(body) <= (SEND_LIMIT if path == PULL_PREFIX + '/send'
                                                     else REQUEST_LIMIT), 'body_limit')
        h = _headers(headers)
        _need(not any(k in h for k in ('host', 'cookie', 'authorization', 'origin', 'transfer-encoding',
                                      'content-length', 'accept-encoding')), 'invalid_request')
        h.update({'content-type': 'application/json', 'accept-encoding': 'identity',
                  'cache-control': 'no-store', 'x-content-type-options': 'nosniff'})
        request = urllib.request.Request(origin + path, data=body, method='POST', headers=h)
        try:
            with self._opener.open(request, timeout=self.timeout_s) as reply:
                _need(reply.status not in range(300, 400), 'response_unverified')
                if callable(getattr(reply, 'geturl', None)):
                    _need(reply.geturl() == origin + path, 'response_unverified')
                return self._read(reply, reply.status)
        except urllib.error.HTTPError as exc:
            with exc:
                _need(exc.code not in range(300, 400), 'response_unverified')
                return self._read(exc, exc.code)
        except ServiceRefused:
            raise
        except Exception:
            _log('[agent-mail] service_client connection_lost', flush=True)
            raise ServiceRefused('connection_lost') from None

    @staticmethod
    def _read(reply: Any, status: int) -> dict[str, Any]:
        # Check the original header pairs before dict() can erase duplicates.
        pairs = list(reply.headers.items())
        _need(len({k.lower() for k, _ in pairs}) == len(pairs))
        h = _headers(dict(pairs))
        raw = reply.read(RESPONSE_LIMIT + 1)
        _need(isinstance(raw, bytes) and len(raw) <= RESPONSE_LIMIT)
        return {'status': status, 'headers': h, 'body': raw}


class ServiceClient:
    """Verified pull adapter; old constructor accepted until importer cutover.

    Composition supplies install_id and reviewed_service. Missing review refuses
    with legacy_retired, without trying old routes or generating trust material.
    __call__ accepts already sealed-key-signed bytes from SignupTransport callers;
    they own human intent/consent and their route-specific response schema.
    """

    def __init__(self, store: MailStore, seal: IdentitySeal, *, fences: LifecycleFences | None = None,
                 transport: Any, root_public_key: str = '', service_origin: str,
                 delivery: DeliveryProof | None = None, nonce: Callable[[int], str] = secrets.token_hex,
                 clock: Callable[[], float] = time.time, install_id: str | None = None,
                 reviewed_service: Callable[[], tuple[ServiceKeyCandidate, str]] | None = None):
        _need(_match(ORIGIN, service_origin) and len(service_origin) <= 261, 'service_origin_invalid')
        _need(install_id is None or _match(I, install_id), 'invalid_request')
        _need(not isinstance(transport, HttpsTransport) or transport.origin in (None, service_origin),
              'service_origin_invalid')
        self.store, self.seal, self.fences, self.transport = store, seal, fences, transport
        self.root, self.origin, self.delivery = root_public_key, service_origin, delivery
        self._nonce, self.clock = nonce, clock
        self.install_id, self.reviewed_service = install_id, reviewed_service

    def _now_ms(self) -> int:
        now = int(self.clock() * 1000)
        _need(_integer(now, 1), 'invalid_request')
        return now

    def _service(self) -> tuple[ServiceKeyCandidate, str]:
        _need(self.reviewed_service is not None, 'legacy_retired')
        assert self.reviewed_service is not None
        try:
            pin, digest = self.reviewed_service()
            _need(pin.origin == self.origin and _match(ID, pin.key_id) and _match(H, digest)
                  and _integer(pin.sequence, 1, 10**15 - 1) and self._now_ms() < pin.expires_at_ms,
                  'service_key_changed')
            unbase64url(pin.public_key, 32)
            return pin, digest
        except EnrollmentRefused:
            raise
        except Exception:
            _log('[agent-mail] service_client service_key_changed', flush=True)
            raise ServiceRefused('service_key_changed') from None

    def _exchange(self, path: str, h: Mapping[str, str], raw: bytes, *, pin: ServiceKeyCandidate,
                  generation: int, owner_generation: int, install: str, nonce: str,
                  request_id: str) -> tuple[tuple[int, Mapping[str, str], bytes], dict[str, Any]]:
        before = self._service()
        _need(before[0] == pin, 'service_key_changed')
        try:
            if isinstance(self.transport, HttpsTransport):
                reply = self.transport.post(self.origin, path, h, raw)
                response = reply['status'], reply['headers'], reply['body']
            elif callable(self.transport):
                response = cast(tuple[int, Mapping[str, str], bytes], self.transport('POST', path, h, raw))
            else:
                reply = self.transport.post(self.origin, path, h, raw)
                response = reply['status'], reply['headers'], reply['body']
        except (ServiceRefused, RemoteUnavailable):
            raise
        except Exception:
            _log('[agent-mail] service_client connection_lost', flush=True)
            raise ServiceRefused('connection_lost') from None
        value = verify_pull_response(response, candidate=pin, install=install, generation=generation,
            owner_generation=owner_generation, path=path, nonce=nonce, request_id=request_id,
            clock_ms=self._now_ms, request=_object(raw))
        _need(self._service() == before, 'service_key_changed')
        return response, value

    def __call__(self, method: str, path: str, headers: Mapping[str, str], body: bytes, /
                 ) -> tuple[int, Mapping[str, str], bytes]:
        """SignupTransport: verify the common signed envelope, including signed errors.

        Request bytes/signature are supplied by the route owner through IdentitySeal.
        Fence authority is available ONLY through fence(), not this signing seam.
        """
        try:
            _need(method == 'POST' and path in ROUTES and path != FENCE, 'invalid_request')
            _need(isinstance(body, bytes) and len(body) <= (SEND_LIMIT if path == PULL_PREFIX + '/send'
                                                         else REQUEST_LIMIT), 'body_limit')
            h = _headers(headers)
            _need(not any(k in h for k in ('host', 'cookie', 'authorization', 'origin', 'transfer-encoding',
                                          'content-length', 'accept-encoding')), 'invalid_request')
            _need(h.get('x-mail-version') == '2' and h.get('x-mail-direction') == 'install-to-pull'
                  and _match(ID, h.get('x-mail-key-id')) and _match(I, h.get('x-mail-install-id'))
                  and _match(I, h.get('x-mail-nonce')) and h.get('x-mail-digest') == hashlib.sha256(body).hexdigest(),
                  'invalid_request')
            install, nonce = h['x-mail-install-id'], h['x-mail-nonce']
            _need(self.install_id is None or install == self.install_id, 'invalid_request')
            nums = [h.get('x-mail-' + f, '') for f in ('generation', 'owner-generation', 'timestamp')]
            _need(all(re.fullmatch(r'[1-9][0-9]{0,15}', n) and _integer(int(n), 1) for n in nums), 'invalid_request')
            gen, owner, stamp = map(int, nums)
            _need(gen < 10**15 and owner < 10**15 and abs(self._now_ms() - stamp) <= SKEW_MS, 'invalid_request')
            unbase64url(h.get('x-mail-signature', ''), 64)
            value = _object(body)
            read = path in {STATUS, PULL_PREFIX + '/signup/status', PULL_PREFIX + '/send/status',
                            PULL_PREFIX + '/management/status'}
            request_id = nonce if read else value.get('op_id')
            _need(_match(I, request_id), 'invalid_request')
            pin, _ = self._service()
            response, _ = self._exchange(path, h, body, pin=pin, generation=gen, owner_generation=owner,
                                         install=install, nonce=nonce, request_id=cast(str, request_id))
            return response
        except ServiceRefused:
            raise
        except (ValueError, TypeError, KeyError, AttributeError, IngressRefused, RecursionError):
            raise ServiceRefused('invalid_request') from None

    def _account(self) -> dict[str, Any]:
        _need(self.reviewed_service is not None, 'legacy_retired')
        with self.store._read() as db:
            _validate(db)
            rows = db.execute('''SELECT a.*,g.service_key_id,g.service_public_key,g.manifest_sequence,
                g.manifest_digest,g.key_id AS generation_key,g.seal_flow_id AS generation_seal,
                g.owner_generation AS generation_owner,g.state AS generation_state
                FROM mail_pull_accounts a JOIN mail_pull_generations g ON g.install_id=a.install_id
                AND g.generation=a.generation WHERE (? IS NULL OR a.install_id=?)''',
                (self.install_id, self.install_id)).fetchall()
        _need(len(rows) == 1, 'registration_inactive')
        row = dict(rows[0])
        _need(_match(I, row['install_id']) and _match(ID, row['key_id']) and _match(I, row['seal_flow_id'])
              and _integer(row['generation'], 1, 10**15 - 1) and _integer(row['owner_generation'], 1, 10**15 - 1)
              and row['generation_key'] == row['key_id'] and row['generation_seal'] == row['seal_flow_id']
              and row['generation_owner'] == row['owner_generation'] and row['generation_state'] == 'current',
              'schema_corrupt')
        pin, digest = self._service()
        _need((row['service_origin'], row['service_key_id'], row['service_public_key'],
               row['manifest_sequence'], row['manifest_digest']) ==
              (pin.origin, pin.key_id, pin.public_key, pin.sequence, digest), 'service_key_changed')
        return row

    def status(self) -> dict[str, Any]:
        row = self._account()
        nonce, stamp, body = self._nonce(16), str(self._now_ms()), b'{}'
        _need(_match(I, nonce), 'invalid_request')
        h = {'x-mail-version': '2', 'x-mail-direction': 'install-to-pull', 'x-mail-key-id': row['key_id'],
             'x-mail-install-id': row['install_id'], 'x-mail-generation': str(row['generation']),
             'x-mail-owner-generation': str(row['owner_generation']), 'x-mail-timestamp': stamp,
             'x-mail-nonce': nonce, 'x-mail-digest': hashlib.sha256(body).hexdigest(),
             'content-type': 'application/json', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff'}
        ref = SealReference(row['seal_flow_id'], row['install_id'], row['generation'], row['key_id'])
        h['x-mail-signature'] = self.seal.sign_pull(ref, _json(['clayrune-agent-mail-pull-http', '1',
            'install-to-pull', self.origin, 'POST', STATUS, row['key_id'], row['install_id'],
            str(row['generation']), str(row['owner_generation']), stamp, nonce, h['x-mail-digest']]))
        response = self('POST', STATUS, h, body)
        value = _object(response[2])
        if response[0] != 200:
            raise ServiceRefused(value['code'])
        try:
            self._status_shape(value, row)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ServiceRefused('response_unverified') from None
        _need(self._account() == row, 'stale_generation')
        return value

    @staticmethod
    def _status_shape(v: dict[str, Any], row: dict[str, Any]) -> None:
        _need(v.keys() == {'v', 'code', 'request_id', 'install_id', 'generation', 'owner_generation', 'state',
            'address', 'owner', 'key_id', 'queue_count', 'queue_bytes', 'oldest_received_ms', 'oldest_expires_ms',
            'invalid_message_id_holds', 'current_epoch', 'owner_change', 'last_notice_state', 'reason'})
        _need(all(v[k] == row[k] for k in ('install_id', 'generation', 'owner_generation', 'key_id'))
              and _integer(v['generation'], 1) and _integer(v['owner_generation'], 1)
              and v['state'] in {'pending', 'verified', 'active', 'disabled', 'frozen', 'deleted'}
              and isinstance(v['reason'], str) and v['reason'] in REASONS
              and (v['last_notice_state'] is None or v['last_notice_state'] in {'queued', 'accepted', 'uncertain', 'failed'}))
        for k in ('queue_count', 'queue_bytes', 'invalid_message_id_holds', 'current_epoch'):
            _need(_integer(v[k]))
        _need(v['queue_count'] <= 100 and v['queue_bytes'] <= 2 * 1024 * 1024)
        for k in ('oldest_received_ms', 'oldest_expires_ms'):
            _need(v[k] is None or _integer(v[k]))
        for k in ('address', 'owner'):
            _need(v[k] is None or isinstance(v[k], str) and _mailbox(v[k]) == v[k])
        c = v['owner_change']
        if c is not None:
            _need(type(c) is dict and c.keys() == {'change_id', 'state', 'created_ms', 'revert_until_ms'}
                  and _match(I, c['change_id']) and c['state'] in {'pending', 'completed', 'reverted', 'expired'}
                  and _integer(c['created_ms']) and (c['revert_until_ms'] is None or _integer(c['revert_until_ms'])))

    def fence(self, op_id: str) -> dict[str, Any]:
        """Retry one durable pull fence; no vault read and no local ACK mutation."""
        _need(_match(I, op_id), 'invalid_request')
        pin, _ = self._service()
        with self.store._read() as db:
            _validate(db)
            r = db.execute('SELECT * FROM mail_pull_fences WHERE op_id=?', (op_id,)).fetchone()
        _need(r is not None, 'fence_token_missing')
        assert r is not None
        row = dict(r)
        _need(_match(I, row['install_id']) and _match(I, row['flow_id']) and _match(H, row['fence_token'])
              and _integer(row['generation'], 1, 10**15 - 1) and row['mode'] in {'cancel', 'revoke'}
              and (self.install_id is None or row['install_id'] == self.install_id), 'schema_corrupt')
        nonce, stamp = self._nonce(16), self._now_ms()
        _need(_match(I, nonce), 'invalid_request')
        raw = _json({k: row[k] for k in ('op_id', 'install_id', 'generation', 'flow_id', 'fence_token', 'mode')}
                    | {'nonce': nonce, 'timestamp': stamp})
        response, value = self._exchange(FENCE, {'content-type': 'application/json', 'cache-control': 'no-store',
            'x-content-type-options': 'nosniff'}, raw, pin=pin, generation=row['generation'], owner_generation=0,
            install=row['install_id'], nonce=nonce, request_id=op_id)
        if response[0] != 200:
            raise ServiceRefused(value['code'])
        _need(value.keys() == {'v', 'code', 'request_id', 'install_id', 'generation', 'state', 'fence_receipt'}
              and value['install_id'] == row['install_id'] and type(value['generation']) is int
              and value['generation'] == row['generation'] and _match(I, value['fence_receipt'])
              and value['state'] in {'fenced', 'already_fenced', 'already_superseded'})
        with self.store._read() as db:
            current = db.execute('SELECT * FROM mail_pull_fences WHERE op_id=?', (op_id,)).fetchone()
        _need(current is not None and all(current[k] == row[k] for k in
              ('install_id', 'generation', 'flow_id', 'fence_token', 'mode')), 'stale_generation')
        return value

    def cancel_flow(self, flow: str) -> CancelledAssignment | None:
        _need(_match(I, flow), 'invalid_request')
        self._service()
        with self.store._read() as db:
            _validate(db)
            rows = db.execute("SELECT op_id,install_id,generation FROM mail_pull_fences WHERE flow_id=? "
                "AND mode='cancel' AND (? IS NULL OR install_id=?)", (flow, self.install_id, self.install_id)).fetchall()
        _need(len(rows) == 1, 'fence_token_missing')
        self.fence(rows[0]['op_id'])
        return CancelledAssignment(rows[0]['install_id'], rows[0]['generation'])

    def revoke(self, install: str, generation: int) -> None:
        self._service()
        _need(_match(I, install) and _integer(generation, 1, 10**15 - 1)
              and (self.install_id is None or install == self.install_id), 'invalid_request')
        with self.store._read() as db:
            _validate(db)
            rows = db.execute("SELECT op_id FROM mail_pull_fences WHERE install_id=? AND generation=? "
                              "AND mode='revoke' ORDER BY op_id", (install, generation)).fetchall()
        _need(rows, 'fence_token_missing')
        # No unsigned 401 fallback: it cannot identify which capability failed.
        for row in rows:
            self.fence(row['op_id'])

    def cached_evidence(self) -> dict[str, Any] | None:
        """Retired push proof is never synthesized from a pull status response."""
        return None

    def proof_arm(self) -> bool:
        raise ServiceRefused('legacy_retired')

    def activate(self, receipt_digest: str) -> None:
        raise ServiceRefused('legacy_retired')

    def renew(self) -> int:
        raise ServiceRefused('legacy_retired')

    def begin(self, context: dict, flow_secret: bytes) -> PendingIdentity:
        raise RemoteUnavailable(REMOTE_UNAVAILABLE)

    def verify(self, context: dict, flow_secret: bytes, code: str) -> OwnershipReceipt:
        raise RemoteUnavailable(REMOTE_UNAVAILABLE)


# Retiring v1 ceremony compatibility only; pull always uses verify_pull_response.
def verify_response(response: Mapping[str, Any], *, candidate: ServiceKeyCandidate, install: str,
                    generation: int, path: str, nonce: str, clock_ms: Callable[[], int]) -> dict[str, Any]:
    """Return the parsed JSON body of a verified, scope-bound, service-signed 200. Raises ServiceRefused."""
    if response.get('status') != 200:
        raise ServiceRefused('remote_not_acknowledged')
    body = response.get('body')
    raw_headers = response.get('headers')
    if not isinstance(body, (bytes, bytearray)) or len(body) > RESPONSE_LIMIT or not isinstance(raw_headers, Mapping):
        raise ServiceRefused('remote_response_invalid')
    headers = {str(k).lower(): v for k, v in raw_headers.items()}
    fields = ['version', 'direction', 'key-id', 'install-id', 'generation', 'timestamp', 'nonce', 'digest']
    version, direction, key, who, gen, stamp, echoed, digest = [headers.get('x-mail-' + f, '') for f in fields]
    if (version != '1' or direction != RESPONSE_DIRECTION or key != candidate.key_id or who != install
            or gen != str(generation) or echoed != nonce or not re.fullmatch(r'[1-9][0-9]{12}', stamp or '')
            or not HEX.fullmatch(digest or '')):
        raise ServiceRefused('remote_response_unsigned')
    if abs(clock_ms() - int(stamp)) > SKEW_MS or hashlib.sha256(bytes(body)).hexdigest() != digest:
        raise ServiceRefused('remote_response_unsigned')
    canonical = json.dumps(['clayrune-agent-mail', version, direction, key, who, gen, 'POST', path, stamp,
                            echoed, digest], separators=(',', ':')).encode()
    try:
        Ed25519PublicKey.from_public_bytes(unbase64url(candidate.public_key, 32)).verify(
            unbase64url(headers.get('x-mail-signature', ''), 64), canonical)
    except (InvalidSignature, IngressRefused):
        raise ServiceRefused('remote_response_unsigned') from None
    if abs(clock_ms() - int(stamp)) > SKEW_MS:
        raise ServiceRefused('remote_response_unsigned')
    try:
        value = json.loads(bytes(body).decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise ServiceRefused('remote_response_invalid') from None
    if not isinstance(value, dict):
        raise ServiceRefused('remote_response_invalid')
    return value
