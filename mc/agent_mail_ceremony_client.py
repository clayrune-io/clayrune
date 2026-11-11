"""Flask half of the human enrollment ceremony transport (MC-1166 unit 7d-2e-3, contract section 3).

`CeremonyClient` is the `begin` / `verify` half of the `EnrollmentAdapter` that `Enrollment` calls
(`mc/agent_mail_enrollment.py` `resume()` and `verify()`). The lifecycle half (`cancel_flow`,
`revoke`) stays in `ServiceClient`; `agent_mail_ceremony_remote.CompositeRemote` joins the two.

It talks to two places and trusts neither:

* the mail Worker, `POST /v1/mail/enrollment/{begin,confirm,complete}`. Only a 200 whose body digest
  is signed by the manifest-verified service key, bound to this path and this request's nonce, and
  carrying `install_id := flow` and `generation := 1` (the ceremony envelope, contract 2.3) counts.
  Every other status, an unsigned or mismatched body, silence or a timeout records nothing and raises
  a closed code. `verify_response` is reused unchanged.
* the control plane, `POST /v1/mail-enrollment/grant`, for the 60 s enrollment grant. The grant is a
  bearer for the Worker only; this client never inspects it beyond its shape.

Nothing is persisted between steps (decision E3): every step is idempotent or replay-safe, so a crash
at any point is recovered by calling `verify` again. The Worker never returns the mailbox code and
this module never sees it except as the human's typed answer.

Closed failure codes only (contract 3.6). Remote text, CP codes and exception text never reach an
exception or a log. The one-time code, the flow secret and the grant token are never logged.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from typing import Any, Callable, Mapping

from mc.agent_mail_ceremony_device import DeviceIdentity
from mc.agent_mail_enrollment import OwnershipReceipt, PendingIdentity
from mc.agent_mail_enrollment_keys import EnrollmentRefused, IdentitySeal, b64
from mc.agent_mail_enrollment_manifest import candidate_from_evidence
from mc.agent_mail_enrollment_state import ManifestCAS
from mc.agent_mail_lifecycle_fence import LifecycleFences
from mc.agent_mail_remote import RemoteUnavailable
from mc.agent_mail_service_client import _ORIGIN, ServiceRefused, verify_response
from mc.agent_mail_service_manifest import ServiceKeyCandidate
from mc.agent_mail_signing import ID
from mc.agent_mail_store import MailStore

WORKER_PREFIX = '/v1/mail/enrollment'
GRANT_PATH = '/v1/mail-enrollment/grant'
ENVELOPE_GENERATION = 1   # the ceremony envelope's constant (contract 2.3); the true scope rides in the body
REQUEST_LIMIT = 4096      # the Worker's ceremony body limit (ENROLLMENT_BODY_LIMIT)
GRANT_LIMIT = 8192
# The 20 transcript fields, in the order `enrollment-transcript.js` FIELDS fixes them.
R_FIELDS = ('purpose', 'version', 'service_origin', 'flow_id', 'challenge_id', 'challenge_nonce', 'expires_ms',
            'install_id', 'expected_generation', 'device_id', 'device_public_key', 'mail_key_id',
            'mail_public_key', 'owner_address_sha256', 'tunnel_uuid', 'route_origin', 'route_path',
            'manifest_sequence', 'service_key_id', 'service_public_key_sha256')
ROUTE_PATH = '/api/agent-mail/ingress'
_CODE = re.compile(r'[a-f0-9]{32}')
_NONCE = re.compile(r'[a-f0-9]{32}')
_HEX64 = re.compile(r'[a-f0-9]{64}')
_STAMP = re.compile(r'[1-9][0-9]{12}')
_GEN = re.compile(r'0|[1-9][0-9]{0,14}')
_ASCII = re.compile(r'[\x20-\x7e]{1,512}')
_JWT = re.compile(r'[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')
_BEGIN_KEYS = frozenset({'flow_id', 'install_id', 'expected_generation', 'transcript', 'state', 'code_sent'})
_CONFIRM_KEYS = frozenset({'flow_id', 'confirmed', 'state'})
_COMPLETE_KEYS = frozenset({'flow_id', 'install_id', 'generation', 'state', 'active', 'route_verified',
                            'ownership_expires_ms'})


class CeremonyRefused(EnrollmentRefused):
    """Closed code only (contract 3.6). Never carries a remote body, a CP code or exception text."""


def _refuse(code: str) -> CeremonyRefused:
    return CeremonyRefused(code)


def _fields(r: list[str]) -> dict[str, str]:
    return dict(zip(R_FIELDS, r))


class CeremonyClient:
    def __init__(self, store: MailStore, seal: IdentitySeal, *, fences: LifecycleFences, transport: Any,
                 root_public_key: str, service_origin: str, grant_origin: str,
                 device: Callable[[], DeviceIdentity], enabled: bool = False,
                 nonce: Callable[[int], str] = secrets.token_hex, clock: Callable[[], float] = time.time):
        if not _ORIGIN.fullmatch(service_origin) or not _ORIGIN.fullmatch(grant_origin):
            raise _refuse('service_origin_invalid')
        self.store, self.seal, self.fences, self.transport = store, seal, fences, transport
        self.root, self.origin, self.grant_origin = root_public_key, service_origin, grant_origin
        self.device, self.enabled, self._nonce, self.clock = device, enabled, nonce, clock
        self.manifests = ManifestCAS(store)

    # -- helpers ----------------------------------------------------------------------------------
    def _now_ms(self) -> int:
        return int(self.clock() * 1000)

    def _enabled(self) -> None:
        if self.enabled is not True:
            raise _refuse('ceremony_disabled')

    def _fresh_candidate(self, context: Mapping[str, Any]) -> ServiceKeyCandidate:
        """Same bar as `ServiceClient._candidate(fresh=True)` and `Enrollment._fresh_candidate`: the
        persisted manifest evidence must equal the CURRENT floor and be unexpired, else `manifest_stale`."""
        try:
            candidate = candidate_from_evidence(dict(context), root=self.root, origin=self.origin)
        except EnrollmentRefused:
            raise _refuse('manifest_stale') from None
        floor = self.manifests.load(candidate.origin)
        if (candidate.origin != self.origin or floor is None or floor.sequence != candidate.sequence
                or floor.digest != context['manifest_digest'] or self._now_ms() >= candidate.expires_at_ms):
            raise _refuse('manifest_stale')
        return candidate

    @staticmethod
    def _secret_hex(flow_secret: bytes) -> str:
        if not isinstance(flow_secret, (bytes, bytearray)) or len(flow_secret) != 32:
            raise _refuse('ceremony_scope_mismatch')
        return bytes(flow_secret).hex()

    def _value(self, context: Mapping[str, Any], device: DeviceIdentity,
               candidate: ServiceKeyCandidate) -> dict[str, str]:
        """The 11 fields the human ceremony supplies (contract 3.3), plus `fence_digest` only when this
        install keeps lifecycle fence tokens (otherwise the flow is one nobody can cancel remotely)."""
        value = {'flow_id': context['flow'], 'owner_address': context['owner'], 'device_id': device.device_id,
                 'device_public_key': device.device_public_key, 'mail_key_id': context['key_id'],
                 'mail_public_key': context['public_key'], 'tunnel_uuid': device.tunnel_uuid,
                 'route_origin': device.route_origin, 'manifest_sequence': str(candidate.sequence),
                 'service_key_id': candidate.key_id, 'service_public_key_sha256': candidate.fingerprint}
        if self.fences.enabled:
            digest = self.fences.digest_for(context['flow'])
            if digest is None:
                raise _refuse('ceremony_scope_mismatch')   # a fence-keeping install with no token is corrupt
            value['fence_digest'] = digest
        if not all(isinstance(v, str) and v for v in value.values()):
            raise _refuse('ceremony_scope_mismatch')
        return value

    def _post(self, name: str, value: dict[str, Any], *, flow: str,
              candidate: ServiceKeyCandidate) -> dict[str, Any]:
        """One Worker POST. Returns the parsed body of a verified, scope-bound, service-signed 200."""
        path = f'{WORKER_PREFIX}/{name}'
        raw = json.dumps(value, separators=(',', ':')).encode('ascii')
        if len(raw) > REQUEST_LIMIT:
            raise _refuse('ceremony_scope_mismatch')
        nonce = self._nonce(16)
        if not _NONCE.fullmatch(nonce):
            raise _refuse('ceremony_scope_mismatch')
        # No install key exists yet: the only request header is the nonce that binds the REPLY to this call.
        headers = {'x-mail-nonce': nonce, 'Content-Type': 'application/json'}
        try:
            response = self.transport.post(self.origin, path, headers, raw)
        except RemoteUnavailable:
            raise
        except Exception:
            raise _refuse('remote_unreachable') from None
        if not isinstance(response, Mapping):
            raise _refuse('remote_response_invalid')
        return verify_response(response, candidate=candidate, install=flow, generation=ENVELOPE_GENERATION,
                               path=path, nonce=nonce, clock_ms=self._now_ms)

    # -- begin ------------------------------------------------------------------------------------
    def _begin(self, context: Mapping[str, Any], secret: str, device: DeviceIdentity,
               candidate: ServiceKeyCandidate) -> tuple[list[str], dict[str, Any]]:
        """POST begin and check everything it answers against what Flask itself sent. Returns the
        verified transcript R and the reply body. Idempotent on the Worker: a repeat returns the same R."""
        value = self._value(context, device, candidate)
        flow = context['flow']
        body = self._post('begin', {'value': value, 'flow_secret': secret}, flow=flow, candidate=candidate)
        if set(body) != _BEGIN_KEYS or body['flow_id'] != flow or body['state'] != 'pending':
            raise _refuse('ceremony_scope_mismatch')
        r = body['transcript']
        if (not isinstance(r, list) or len(r) != len(R_FIELDS)
                or not all(isinstance(v, str) and _ASCII.fullmatch(v) for v in r)):
            raise _refuse('ceremony_scope_mismatch')
        m = _fields(r)
        chosen = {k: v for k, v in value.items() if k not in ('owner_address', 'fence_digest')}
        chosen['purpose'], chosen['version'] = 'clayrune-mail-enrollment', '1'
        # The Worker may only fill challenge_*, install_id, expected_generation, expires_ms,
        # owner_address_sha256, service_origin and route_path; everything else must be what we sent.
        if (any(m[k] != v for k, v in chosen.items())
                or m['owner_address_sha256'] != hashlib.sha256(context['owner'].encode('utf-8')).hexdigest()
                or m['service_origin'] != self.origin or m['route_path'] != ROUTE_PATH
                or not ID.fullmatch(m['install_id']) or not ID.fullmatch(m['challenge_id'])
                or not _HEX64.fullmatch(m['challenge_nonce']) or not _STAMP.fullmatch(m['expires_ms'])
                or not _GEN.fullmatch(m['expected_generation'])
                or body['install_id'] != m['install_id'] or type(body['expected_generation']) is not int
                or str(body['expected_generation']) != m['expected_generation']
                or type(body['code_sent']) is not bool):
            raise _refuse('ceremony_scope_mismatch')
        return r, body

    def begin(self, context: dict, flow_secret: bytes) -> PendingIdentity:
        self._enabled()
        secret = self._secret_hex(flow_secret)
        candidate = self._fresh_candidate(context)
        device = self.device()      # `device_identity_unavailable`, before anything is sent
        r, _ = self._begin(context, secret, device, candidate)
        m = _fields(r)
        # The local row's generation is the POST-commit registration generation (`enrollment.js` result()):
        # a fresh install is expected 0 -> 1, an established one expected G -> G+1.
        return PendingIdentity(context['flow'], m['install_id'], int(m['expected_generation']) + 1,
                               context['owner'], context['public_key'], candidate.fingerprint)

    # -- verify -----------------------------------------------------------------------------------
    def verify(self, context: dict, flow_secret: bytes, code: str) -> OwnershipReceipt:
        self._enabled()
        # 1. Local pre-checks. A malformed code is a counted strike at the Worker (5 kill the flow),
        #    so it is refused here and never sent.
        normal = code.strip().lower() if isinstance(code, str) else ''
        if not _CODE.fullmatch(normal):
            raise _refuse('invalid_code')
        secret = self._secret_hex(flow_secret)
        candidate = self._fresh_candidate(context)
        device = self.device()
        flow = context['flow']
        # 2. Confirm. `confirmed: false` is a normal 200 (a counted wrong code): never retried.
        confirmed = self._post('confirm', {'flow_id': flow, 'flow_secret': secret, 'code': normal}, flow=flow,
                               candidate=candidate)
        if set(confirmed) != _CONFIRM_KEYS or confirmed['flow_id'] != flow:
            raise _refuse('ceremony_scope_mismatch')
        if confirmed['confirmed'] is not True:
            raise _refuse('code_rejected')
        # 3. Rebuild R: begin is idempotent and returns the stored transcript. Nothing is persisted
        #    between steps, so the Flask row is the memory: R must still name the install and
        #    generation `resume()` recorded, and the repeat begin must not have minted a new flow.
        r, begun = self._begin(context, secret, device, candidate)
        m = _fields(r)
        if (begun['code_sent'] is not False or m['install_id'] != context['install']
                or int(m['expected_generation']) + 1 != context['generation']):
            raise _refuse('ceremony_scope_mismatch')
        # 4. Sign R. Device key, then the mail key through the vault (locked -> nothing sent to the CP).
        canonical = json.dumps(r, separators=(',', ':')).encode('ascii')
        device_signature = device.sign(canonical)
        mail_signature, _ = self.seal.prove(flow, context['seal_identity'], context['seal_generation'],
                                            context['key_id'], canonical)
        proofs = {'device_signature': b64(device_signature), 'mail_signature': mail_signature}
        # 5/6. Complete. E3: a lost-reply retry finds the commit already made, and a NEW grant token
        #      would be `grant_conflict`, so ask tokenless first. A Worker with no commit refuses a
        #      tokenless complete before writing anything; only then is a grant minted and presented.
        base = {'flow_id': flow, 'flow_secret': secret, **proofs}
        done = self._complete(base, flow=flow, candidate=candidate, tolerate_refusal=True)
        if done is None:
            token = self._grant(r, proofs)
            done = self._complete({**base, 'token': token}, flow=flow, candidate=candidate,
                                  tolerate_refusal=False)
        assert done is not None
        return self._receipt(done, context, m, candidate)

    def _complete(self, value: dict[str, Any], *, flow: str, candidate: ServiceKeyCandidate,
                  tolerate_refusal: bool) -> dict[str, Any] | None:
        try:
            return self._post('complete', value, flow=flow, candidate=candidate)
        except ServiceRefused as exc:
            if tolerate_refusal and str(exc) == 'remote_not_acknowledged':
                return None   # unsigned non-200: no commit exists yet (or no way to tell): take the grant path
            raise

    @staticmethod
    def _receipt(body: dict[str, Any], context: Mapping[str, Any], m: Mapping[str, str],
                 candidate: ServiceKeyCandidate) -> OwnershipReceipt:
        # The lease bound (`now < expires <= now + 300000`) is `Enrollment.verify`'s gate; only the
        # reply's scope and types are checked here.
        if (set(body) != _COMPLETE_KEYS or body['flow_id'] != context['flow']
                or body['install_id'] != m['install_id'] or type(body['generation']) is not int
                or body['generation'] != int(m['expected_generation']) + 1
                or body['state'] != 'ownership_recorded_delivery_pending'
                or body['active'] is not False or body['route_verified'] is not False
                or type(body['ownership_expires_ms']) is not int):
            raise _refuse('ceremony_scope_mismatch')
        return OwnershipReceipt(context['flow'], body['install_id'], body['generation'], context['owner'],
                                context['public_key'], candidate.fingerprint, body['ownership_expires_ms'])

    def _grant(self, r: list[str], proofs: Mapping[str, str]) -> str:
        """CP `/v1/mail-enrollment/grant` -> the 60 s grant JWT. The CP reply is unsigned JSON, so only
        its shape is checked; the Worker is what verifies the grant. Any non-200 is `grant_refused`
        and the CP's code is dropped (it is remote text)."""
        raw = json.dumps({'r': r, **proofs}, separators=(',', ':')).encode('ascii')
        try:
            response = self.transport.post(self.grant_origin, GRANT_PATH, {'Content-Type': 'application/json'}, raw)
        except RemoteUnavailable:
            raise
        except Exception:
            raise _refuse('remote_unreachable') from None
        if not isinstance(response, Mapping):
            raise _refuse('remote_response_invalid')
        if response.get('status') != 200:
            raise _refuse('grant_refused')
        body = response.get('body')
        if not isinstance(body, (bytes, bytearray)) or len(body) > GRANT_LIMIT:
            raise _refuse('remote_response_invalid')
        try:
            value = json.loads(bytes(body).decode('utf-8'))
        except (UnicodeDecodeError, ValueError):
            raise _refuse('remote_response_invalid') from None
        token = value.get('grant') if isinstance(value, dict) and set(value) == {'grant'} else None
        if not isinstance(token, str) or not _JWT.fullmatch(token) or len(token) > GRANT_LIMIT:
            raise _refuse('remote_response_invalid')
        return token
