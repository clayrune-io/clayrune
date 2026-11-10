"""Fixed-origin client for the mail service lifecycle routes (MC-1166 unit 7d-2d, contract 5.1-5.5).

Two authority classes, matching `workers/agent-mail/lifecycle.js`:

* fence-auth (`cancel_flow`, `revoke`): the flow's fence token from the local plain store. No vault,
  no key: works with a locked or lost vault (decision D2).
* install-auth (`status`, `renew`, `proof_arm`, `activate`): the install MAIL key through the
  sealed identity, direction `install-to-service-lifecycle`. Never the device key.

The ONLY thing that counts as an acknowledgment is a 200 whose body digest is signed by the
manifest-verified service key under `service-to-install-response`, bound to this request's path
and nonce, and whose fields equal the request scope. An HTTP status, silence, a timeout, an
unsigned or mismatched body records nothing and raises a closed code (5.5). Remote text never
reaches an exception or a log.

The origin comes from the constructor (the manifest-verified deployment), never from a request,
an agent or a response. No key is generated here and nothing starts on import.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mc.agent_mail_delivery_proof import DeliveryProof
from mc.agent_mail_enrollment import OwnershipReceipt, PendingIdentity
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal
from mc.agent_mail_enrollment_manifest import candidate_from_evidence
from mc.agent_mail_enrollment_revocations import CancelledAssignment
from mc.agent_mail_enrollment_state import ManifestCAS
from mc.agent_mail_lifecycle_fence import LifecycleFences
from mc.agent_mail_outbound_signing import headers as signed_headers
from mc.agent_mail_remote import REMOTE_UNAVAILABLE, RemoteUnavailable
from mc.agent_mail_service_manifest import ServiceKeyCandidate
from mc.agent_mail_signing import HEX, ID, SKEW_MS, IngressRefused, unbase64url
from mc.agent_mail_store import MailStore

PREFIX = '/v1/mail/lifecycle'
LIFECYCLE_DIRECTION = 'install-to-service-lifecycle'
RESPONSE_DIRECTION = 'service-to-install-response'
REQUEST_LIMIT = 2048          # contract 5.1: bounded JSON
RESPONSE_LIMIT = 16 * 1024
HTTP_TIMEOUT_S = 10.0
EVIDENCE_MAX_AGE_MS = 120_000  # a cached Worker status older than this is never evidence
LEASE_MS = 300_000
_NONCE = re.compile(r'[a-f0-9]{32}')
_ORIGIN = re.compile(r'https://(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?')
_REVOKE_STATES = frozenset({'revoked', 'already_superseded', 'fenced'})


class ServiceRefused(EnrollmentRefused):
    """Closed code only. Never carries a remote body, header or exception text."""


def _gen_ok(value: Any) -> bool:
    return type(value) is int and 1 <= value < 10 ** 15


# --- response verification ---------------------------------------------------------------------------
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


# --- real HTTPS transport (never wired by default) ---------------------------------------------------
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # a redirect is a refusal, never followed (contract 5.1: manual redirects)


class HttpsTransport:
    """POST-only, no query, redirects refused, 10 s timeout, bounded response. A non-200 status is
    returned (so a 401 can be told from a failure) but never treated as an acknowledgment."""

    def __init__(self, *, timeout_s: float = HTTP_TIMEOUT_S, opener: Any = None):
        self.timeout_s = timeout_s
        self._opener = opener or urllib.request.build_opener(_NoRedirect)

    def post(self, origin: str, path: str, headers: Mapping[str, str], body: bytes) -> dict[str, Any]:
        if not _ORIGIN.fullmatch(origin) or not re.fullmatch(r'/[A-Za-z0-9/_-]{1,200}', path):
            raise RemoteUnavailable(REMOTE_UNAVAILABLE)
        request = urllib.request.Request(origin + path, data=body, method='POST', headers=dict(headers))
        try:
            with self._opener.open(request, timeout=self.timeout_s) as reply:
                return {'status': reply.status, 'headers': dict(reply.headers.items()),
                        'body': reply.read(RESPONSE_LIMIT + 1)}
        except urllib.error.HTTPError as exc:
            return {'status': exc.code, 'headers': {}, 'body': b''}


# --- the client --------------------------------------------------------------------------------------
class ServiceClient:
    """Implements the lifecycle RevocationAdapter (`cancel_flow`, `revoke`) plus the install-auth
    calls. `begin`/`verify` (the human ceremony transport) are unit 7d-2e and refuse here."""

    def __init__(self, store: MailStore, seal: IdentitySeal, *, fences: LifecycleFences, transport: Any,
                 root_public_key: str, service_origin: str, delivery: DeliveryProof | None = None,
                 nonce: Callable[[int], str] = secrets.token_hex,
                 clock: Callable[[], float] = time.time):
        if not _ORIGIN.fullmatch(service_origin):
            raise ServiceRefused('service_origin_invalid')
        self.store, self.seal, self.fences, self.transport = store, seal, fences, transport
        self.root, self.origin, self.delivery, self._nonce, self.clock = (
            root_public_key, service_origin, delivery, nonce, clock)
        self.manifests = ManifestCAS(store)
        self._evidence: tuple[dict[str, Any], int] | None = None

    # -- helpers ----------------------------------------------------------------------------------
    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    def _row(self) -> dict[str, Any]:
        with self.store._read() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='mail_enrollment_flow'").fetchone():
                raise ServiceRefused('enrollment_required')
            row = db.execute('SELECT * FROM mail_enrollment_flow WHERE slot=1').fetchone()
        if row is None:
            raise ServiceRefused('enrollment_required')
        return dict(row)

    def _candidate(self, row: dict[str, Any], *, fresh: bool) -> ServiceKeyCandidate:
        candidate = candidate_from_evidence(row, root=self.root, origin=self.origin)
        if candidate.origin != self.origin:
            raise ServiceRefused('service_origin_invalid')
        if fresh:
            floor = self.manifests.load(candidate.origin)
            if (floor is None or floor.sequence != candidate.sequence or floor.digest != row['manifest_digest']
                    or self._now_ms() >= candidate.expires_at_ms):
                raise ServiceRefused('manifest_stale')
        return candidate

    def _call(self, name: str, value: dict[str, Any], *, row: dict[str, Any], install: str, generation: int,
              install_auth: bool) -> tuple[dict[str, Any] | None, int]:
        """One POST. Returns (verified body, status). Body is None unless a verified 200; transport
        failure raises. 401 is returned (not raised) so revoke can try the next flow's token."""
        path = f'{PREFIX}/{name}'
        raw = json.dumps(value, separators=(',', ':')).encode('ascii')
        if len(raw) > REQUEST_LIMIT:
            raise ServiceRefused('request_too_large')
        nonce = self._nonce(16)
        if not _NONCE.fullmatch(nonce):
            raise ServiceRefused('invalid_signature_fields')
        candidate = self._candidate(row, fresh=install_auth)
        if install_auth:
            # Vault locked raises EnrollmentRefused('vault_locked_or_unconfigured') here: nothing was sent.
            headers = signed_headers(self.seal, {**row, 'install': install, 'generation': generation}, raw,
                                     now_ms=self._now_ms(), nonce=nonce, direction=LIFECYCLE_DIRECTION, path=path)
        else:
            headers = {'x-mail-install-id': install, 'x-mail-generation': str(generation),
                       'x-mail-nonce': nonce, 'Content-Type': 'application/json'}
        try:
            response = self.transport.post(self.origin, path, headers, raw)
        except RemoteUnavailable:
            raise
        except Exception:
            raise ServiceRefused('remote_unreachable') from None
        if not isinstance(response, Mapping):
            raise ServiceRefused('remote_response_invalid')
        if response.get('status') == 401:
            return None, 401
        return verify_response(response, candidate=candidate, install=install, generation=generation,
                               path=path, nonce=nonce, clock_ms=self._now_ms), 200

    @staticmethod
    def _exact(value: Mapping[str, Any], keys: tuple[str, ...]) -> None:
        if set(value) != set(keys):
            raise ServiceRefused('remote_scope_mismatch')

    # -- fence-auth: the RevocationAdapter ---------------------------------------------------------
    def cancel_flow(self, flow: str) -> CancelledAssignment | None:
        token = self.fences.token_for(flow)
        if token is None:
            raise ServiceRefused('fence_token_missing')
        row = self._row()
        body, _ = self._call('cancel', {'flow_id': flow, 'fence_token': token}, row=row,
                             install=row['install'], generation=row['generation'], install_auth=False)
        if body is None:
            raise ServiceRefused('remote_not_acknowledged')
        self._exact(body, ('flow_id', 'state', 'assignment'))
        if body['flow_id'] != flow or body['state'] != 'cancelled':
            raise ServiceRefused('remote_scope_mismatch')
        assignment = body['assignment']
        if assignment is None:
            return None
        if (not isinstance(assignment, dict) or set(assignment) != {'install_id', 'generation'}
                or not isinstance(assignment['install_id'], str) or not ID.fullmatch(assignment['install_id'])
                or not _gen_ok(assignment['generation'])):
            raise ServiceRefused('remote_scope_mismatch')
        return CancelledAssignment(assignment['install_id'], assignment['generation'])

    def revoke(self, install: str, generation: int) -> None:
        candidates = self.fences.tokens_for_assignment(install, generation)
        if not candidates:
            raise ServiceRefused('fence_token_missing')
        row = self._row()
        for _flow, token in candidates:
            body, status = self._call('revoke', {'install_id': install, 'generation': generation,
                                                 'fence_token': token}, row=row, install=install,
                                      generation=generation, install_auth=False)
            if body is None:
                continue  # the Worker stored another flow's digest: try the next token
            self._exact(body, ('install_id', 'generation', 'state'))
            if (body['install_id'] != install or body['generation'] != generation
                    or body['state'] not in _REVOKE_STATES):
                raise ServiceRefused('remote_scope_mismatch')
            return
        raise ServiceRefused('remote_not_acknowledged')

    # -- install-auth -------------------------------------------------------------------------------
    def _install_call(self, name: str, extra: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        row = self._row()
        if row['stage'] not in ('delivery_pending', 'active'):
            raise ServiceRefused('enrollment_required')
        value = {'install_id': row['install'], 'generation': row['generation'], **extra}
        body, _ = self._call(name, value, row=row, install=row['install'], generation=row['generation'],
                             install_auth=True)
        if body is None:
            raise ServiceRefused('remote_not_acknowledged')
        return row, body

    def status(self) -> dict[str, Any]:
        row, body = self._install_call('status', {})
        self._exact(body, ('install_id', 'generation', 'registration', 'lease_until_ms', 'proof', 'fences',
                           'cp_revoke_pending'))
        if body['install_id'] != row['install'] or body['generation'] != row['generation']:
            raise ServiceRefused('remote_scope_mismatch')
        proof = body['proof']
        evidence = None
        if (isinstance(proof, dict) and proof.get('state') == 'verified' and isinstance(proof.get('receipt_digest'), str)
                and type(proof.get('expires_ms')) is int):
            evidence = {'install_id': row['install'], 'generation': row['generation'], 'state': 'verified',
                        'receipt_digest': proof['receipt_digest'], 'expires_ms': proof['expires_ms']}
        self._evidence = (evidence, self._now_ms()) if evidence else None
        return body

    def cached_evidence(self) -> dict[str, Any] | None:
        """`DeliveryProof(worker_evidence=...)`: the LAST signature-verified status only. Never
        fetches, never survives EVIDENCE_MAX_AGE_MS, never exists for an unsigned answer."""
        if self._evidence is None:
            return None
        evidence, at = self._evidence
        return dict(evidence) if self._now_ms() - at <= EVIDENCE_MAX_AGE_MS else None

    def proof_arm(self) -> bool:
        """Push the locally armed challenge. Returns False when there is nothing armed."""
        if self.delivery is None:
            raise ServiceRefused('delivery_proof_unavailable')
        row = self._row()
        armed = self.delivery.armed(row['install'], row['generation'])
        if armed is None:
            return False
        _, body = self._install_call('proof-arm', {'challenge': armed['challenge'], 'expires_ms': armed['expires_ms']})
        self._exact(body, ('install_id', 'generation', 'state', 'expires_ms'))
        if (body['install_id'] != row['install'] or body['generation'] != row['generation']
                or body['state'] != 'armed' or body['expires_ms'] != armed['expires_ms']):
            raise ServiceRefused('remote_scope_mismatch')
        return True

    def activate(self, receipt_digest: str) -> None:
        if not isinstance(receipt_digest, str) or not HEX.fullmatch(receipt_digest):
            raise ServiceRefused('invalid_receipt_digest')
        row, body = self._install_call('activate', {'receipt_digest': receipt_digest})
        self._exact(body, ('install_id', 'generation', 'state', 'receipt_digest'))
        if (body['install_id'] != row['install'] or body['generation'] != row['generation']
                or body['state'] != 'activated' or body['receipt_digest'] != receipt_digest):
            raise ServiceRefused('remote_scope_mismatch')

    def renew(self) -> int:
        """One lease renewal; returns the new `ownership_expires_ms`. Forward-only, stage-preserving:
        it never changes stage, flow, key, owner or consent, and a failure leaves the old lease."""
        row, body = self._install_call('renew', {})
        self._exact(body, ('install_id', 'generation', 'expires_at_ms'))
        expires = body['expires_at_ms']
        now = self._now_ms()
        if (body['install_id'] != row['install'] or body['generation'] != row['generation']
                or type(expires) is not int or not now < expires <= now + LEASE_MS):
            raise ServiceRefused('remote_scope_mismatch')
        with self.store._tx() as db:
            current = db.execute('SELECT * FROM mail_enrollment_flow WHERE slot=1').fetchone()
            if (current is None or any(current[k] != row[k] for k in (
                    'flow', 'epoch', 'stage', 'install', 'generation', 'manifest_digest', 'confirmed'))
                    or current['stage'] == 'disabled'):
                raise ServiceRefused('lifecycle_raced')
            if now >= expires:
                raise ServiceRefused('ownership_expired')
            db.execute('UPDATE mail_enrollment_flow SET ownership_expires_ms=? WHERE slot=1 '
                       'AND ownership_expires_ms<?', (expires, expires))
            return max(expires, current['ownership_expires_ms'])

    # -- ceremony transport: unit 7d-2e -------------------------------------------------------------
    def begin(self, context: dict, flow_secret: bytes) -> PendingIdentity:
        raise RemoteUnavailable(REMOTE_UNAVAILABLE)

    def verify(self, context: dict, flow_secret: bytes, code: str) -> OwnershipReceipt:
        raise RemoteUnavailable(REMOTE_UNAVAILABLE)
