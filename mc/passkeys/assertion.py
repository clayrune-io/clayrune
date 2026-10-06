"""WebAuthn assertion ceremony for passkey add and revoke
(docs/PASSKEYS_SPEC.md, "Slice 2 prerequisites" item 2), over py_webauthn.

Once a passkey exists, adding or revoking one needs an assertion from an
existing credential instead of the retyped passcode. Two calls: `options` issues
a single-use ceremony bound to ONE operation (`purpose`, plus the credential id
it acts on for a revoke), the browser signs the challenge, and the operation's
own route hands the result to `check_proof`, which consumes the ceremony,
verifies the signature and persists the counter. The proof is the request's
authorization and nothing else: no token, no remembered approval.

This is NOT the general gate verifier (slice 2b). It authorizes only the two
operations that manage the registry itself, and no route outside
`mc/blueprints/passkey_routes.py` calls it.

`userVerification: required`, user presence, RP ID hash, exact origin and the
challenge are all checked by the library against the server-held ceremony. A
cross-origin or iframe-embedded assertion is refused here. Counters: the library
rejects a non-increasing counter unless both are zero (synced credentials report
zero), and `store.record_assertion` re-checks under the write lock.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from mc.passkeys import store
from mc.passkeys.challenges import Ceremony, STORE as CEREMONIES
from mc.passkeys.ceremony import (
    RegistrationRejected, TIMEOUT_MS, _lib, _reject_cross_origin, b64url_decode)

PURPOSES = ('add', 'revoke')


class ProofError(Exception):
    code = 'proof_error'

    def __init__(self, detail: str = ''):
        super().__init__(detail)
        self.detail = detail[:200]


class ProofRequired(ProofError):
    code = 'proof_required'


class ProofRejected(ProofError):
    """Distinct from a ceremony problem: the assertion itself was refused.
    The caller must NOT offer the passcode as a way round it."""
    code = 'invalid_assertion'


@dataclass(frozen=True)
class VerifiedAssertion:
    credential_id: str
    new_sign_count: int
    backup_state: bool


def assertion_options(ceremony: Ceremony, credentials: Iterable[dict]) -> dict:
    """Request options for `navigator.credentials.get`: the registered credentials
    for this RP, user verification required, the ceremony's own challenge.
    `credentials` are `{'id', 'transports'}` rows."""
    webauthn, s = _lib()
    allow = []
    for c in credentials:
        transports = []
        for t in c.get('transports') or []:
            try:
                transports.append(s.AuthenticatorTransport(t))
            except ValueError:
                continue
        allow.append(s.PublicKeyCredentialDescriptor(
            id=b64url_decode(c['id']), transports=transports or None))
    opts = webauthn.generate_authentication_options(
        rp_id=ceremony.rp_id, challenge=ceremony.challenge, timeout=TIMEOUT_MS,
        allow_credentials=allow,
        user_verification=s.UserVerificationRequirement.REQUIRED)
    return json.loads(webauthn.options_to_json(opts))


def verify_assertion(assertion, ceremony: Ceremony, material: dict) -> VerifiedAssertion:
    """Verify one `navigator.credentials.get` result against its ceremony and
    the credential's stored key. Raises ProofRejected for anything the library or
    this check refuses. Does not touch the store."""
    _lib()
    from webauthn.helpers import bytes_to_base64url, parse_authentication_credential_json
    from webauthn.authentication.verify_authentication_response import verify_authentication_response
    if not isinstance(assertion, dict):
        raise ProofRejected('assertion was not an object')
    try:
        _reject_cross_origin(assertion)
    except RegistrationRejected as e:
        raise ProofRejected(e.detail.replace('registration', 'assertion')) from None
    try:
        parsed = parse_authentication_credential_json(assertion)
        verified = verify_authentication_response(
            credential=parsed,
            expected_challenge=ceremony.challenge,
            expected_rp_id=ceremony.rp_id,
            expected_origin=ceremony.origin,
            credential_public_key=b64url_decode(material['public_key']),
            credential_current_sign_count=int(material['sign_count']),
            require_user_verification=True,
        )
    except Exception as e:
        raise ProofRejected(str(e) or type(e).__name__) from None
    return VerifiedAssertion(
        credential_id=bytes_to_base64url(verified.credential_id),
        new_sign_count=int(verified.new_sign_count),
        backup_state=bool(verified.credential_backed_up))


def check_proof(proof, *, purpose: str, target: str, session_nonce: str) -> str:
    """Consume the ceremony `proof` names and verify its assertion for exactly
    this `purpose` and `target`. Returns the id of the credential that approved.

    Raises ProofRequired (no usable proof in the request), a `CeremonyError`
    (unknown, used, expired, wrong session, wrong operation: the ceremony is
    spent regardless), `store.PolicyChanged` (a revocation since it was issued),
    or ProofRejected. Consumption is final: a failure means a new ceremony."""
    if not (isinstance(proof, dict) and isinstance(proof.get('ceremony_id'), str)
            and isinstance(proof.get('assertion'), dict)):
        raise ProofRequired('a passkey assertion is required')
    c = CEREMONIES.consume(proof['ceremony_id'], kind='assertion',
                           session_nonce=session_nonce, purpose=purpose, target=target)
    assertion = proof['assertion']
    claimed = assertion.get('id')
    if not isinstance(claimed, str):
        raise ProofRejected('assertion has no credential id')
    try:
        material = store.verification_material(claimed)
    except store.UnknownCredential:
        raise ProofRejected('that credential is not registered') from None
    if material['epoch'] != c.epoch:
        raise store.PolicyChanged('policy changed since the challenge was issued')
    if material['rp_id'] != c.rp_id:
        raise ProofRejected('credential belongs to a different relying party')
    v = verify_assertion(assertion, c, material)
    if v.credential_id != claimed:
        raise ProofRejected('credential id mismatch')
    try:
        store.record_assertion(v.credential_id, new_sign_count=v.new_sign_count,
                               backup_state=v.backup_state)
    except store.CounterRegression:
        raise ProofRejected('authenticator counter did not advance') from None
    except store.UnknownCredential:
        raise ProofRejected('that credential was revoked') from None
    return v.credential_id
