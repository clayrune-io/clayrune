"""WebAuthn registration ceremony, over py_webauthn (PyPI ``webauthn``).

The library is imported lazily so a machine without it still boots and the
passkey routes answer ``passkeys_unavailable`` instead of the server failing at
import. Profile (docs/PASSKEYS_SPEC.md, "Enrollment and storage"):
``userVerification: required``, ``residentKey: preferred``, no attachment
restriction, attestation ``none``, duplicate exclusion from the registry.
Verification demands user presence AND user verification, rejects a cross-origin
or iframe-embedded client data blob, and checks challenge, RP ID hash and origin
against the server-held ceremony record, never against anything the client
sent as a claim.

Registration only. The assertion verifier is slice 2.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Iterable, List

from mc.passkeys.challenges import Ceremony

RP_NAME = 'Clayrune'
USER_NAME = 'Clayrune owner'
TIMEOUT_MS = 120_000          # matches CHALLENGE_TTL_S


class LibraryUnavailable(Exception):
    code = 'passkeys_unavailable'


class RegistrationRejected(Exception):
    code = 'invalid_registration'

    def __init__(self, detail: str = ''):
        super().__init__(detail)
        self.detail = detail[:200]


@dataclass(frozen=True)
class VerifiedCredential:
    credential_id: str          # base64url
    public_key: str             # base64url COSE key
    sign_count: int
    backup_eligible: bool
    backup_state: bool
    aaguid: str
    transports: List[str]


def _lib():
    try:
        import webauthn
        from webauthn.helpers import structs
    except Exception as e:  # ImportError, or a broken transitive dependency
        raise LibraryUnavailable(str(e)) from e
    return webauthn, structs


def library_available() -> bool:
    try:
        _lib()
        return True
    except LibraryUnavailable:
        return False


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))


def registration_options(ceremony: Ceremony, exclude_ids: Iterable[str]) -> dict:
    """Creation options for `navigator.credentials.create`, as the JSON-ready
    dict the browser's `parseCreationOptionsFromJSON` (or our own decoder in
    static/js/passkeys.js) consumes."""
    webauthn, s = _lib()
    opts = webauthn.generate_registration_options(
        rp_id=ceremony.rp_id,
        rp_name=RP_NAME,
        user_name=USER_NAME,
        user_id=b64url_decode(ceremony.owner_handle),
        challenge=ceremony.challenge,
        timeout=TIMEOUT_MS,
        attestation=s.AttestationConveyancePreference.NONE,
        authenticator_selection=s.AuthenticatorSelectionCriteria(
            resident_key=s.ResidentKeyRequirement.PREFERRED,
            user_verification=s.UserVerificationRequirement.REQUIRED),
        exclude_credentials=[
            s.PublicKeyCredentialDescriptor(id=b64url_decode(cid)) for cid in exclude_ids],
    )
    return json.loads(webauthn.options_to_json(opts))


def _reject_cross_origin(credential: dict) -> None:
    """A registration made from inside a cross-origin iframe is refused even if
    every other field checks out. `crossOrigin: true` or any `topOrigin` in the
    client data means the page that asked is not the page that is shown."""
    try:
        client = json.loads(b64url_decode(credential['response']['clientDataJSON']))
    except Exception as e:
        raise RegistrationRejected('clientDataJSON was malformed') from e
    if not isinstance(client, dict):
        raise RegistrationRejected('clientDataJSON was malformed')
    if client.get('crossOrigin') is True or 'topOrigin' in client:
        raise RegistrationRejected('cross-origin registration refused')


def verify_registration(credential, ceremony: Ceremony) -> VerifiedCredential:
    """Verify one `navigator.credentials.create` result against its ceremony.
    Raises RegistrationRejected for anything the library or this check refuses,
    LibraryUnavailable if the library is absent. Does not touch the store."""
    webauthn, s = _lib()
    from webauthn.helpers import bytes_to_base64url
    from webauthn.helpers import parse_registration_credential_json
    from webauthn.registration.verify_registration_response import verify_registration_response
    if not isinstance(credential, dict):
        raise RegistrationRejected('credential was not an object')
    _reject_cross_origin(credential)
    try:
        parsed = parse_registration_credential_json(credential)
        verified = verify_registration_response(
            credential=parsed,
            expected_challenge=ceremony.challenge,
            expected_rp_id=ceremony.rp_id,
            expected_origin=ceremony.origin,
            require_user_presence=True,
            require_user_verification=True,
        )
    except Exception as e:
        raise RegistrationRejected(str(e) or type(e).__name__) from e
    transports = [str(getattr(t, 'value', t)) for t in (parsed.response.transports or [])]
    return VerifiedCredential(
        credential_id=bytes_to_base64url(verified.credential_id),
        public_key=bytes_to_base64url(verified.credential_public_key),
        sign_count=int(verified.sign_count),
        backup_eligible=verified.credential_device_type == s.CredentialDeviceType.MULTI_DEVICE,
        backup_state=bool(verified.credential_backed_up),
        aaguid=str(verified.aaguid),
        transports=transports,
    )
