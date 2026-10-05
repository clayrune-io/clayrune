"""Passkey enrollment, list and revoke (`mc/passkeys/`, docs/PASSKEYS_SPEC.md slice 1).

    GET    /api/passkeys                    metadata: library present, whether THIS
                                            request may enroll, credentials (no keys)
    POST   /api/passkeys/register/options   {passcode, label?} -> {ceremony_id, options}
    POST   /api/passkeys/register/finish    {ceremony_id, credential} -> {ok, credential}
    DELETE /api/passkeys/<id>               {passcode} revoke one credential

DISABLED FOR ACTIONS. Nothing here, and nothing anywhere else, reads the registry
to authorize an operation: `_require_human_passcode` and every human-only route
behave exactly as before (slice 2, which waits for Ron, changes that). A passkey
enrolled today is a stored public key and nothing more.

Enrollment and revocation are host-only (`mc.passkeys.host_check`): a direct
loopback peer, the exact `http://localhost:<port>` Host and Origin, no Cf-* or
proxy headers. LAN and tunnel callers, and forged-header callers, are refused
before the passcode is ever consulted. The retyped dashboard passcode is checked
once, at `register/options` and on revoke, through the shared guard so it spends
the same per-IP guessing budget as every other human-proof route. `finish` needs
no passcode: the single-use ceremony it presents can only have been issued after
one. Order everywhere: host check, request shape, store readable, passcode, work
(a malformed request must not cost a passcode guess).

`is_unattended_caller()` is not consulted: the host check demands a browser Origin,
which is exactly the signal that function treats as "human", so it could never
refuse anything here. The passcode is the human proof; the Origin/Host check is
the "which door" proof.
"""
from __future__ import annotations

import re
import secrets

from flask import Blueprint, jsonify, request

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.passkeys import audit, ceremony, host_check, store
from mc.passkeys.challenges import (
    CHALLENGE_TTL_S, STORE as CEREMONIES, CeremonyError, ExpiredCeremony,
    SessionMismatch, TooManyCeremonies, new_session_nonce)

bp = Blueprint('passkey_routes', __name__)

_PORT = 5199
_COOKIE = 'mc_passkey_ceremony'
_COOKIE_PATH = '/api/passkeys/register'
_MAX_BODY = 64 * 1024
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,1024}$')


def wire(*, port: int) -> None:
    """Late-bind the server's configured port (server.py, before registering the
    blueprint). The allowed Host/Origin is derived from this, never from the
    request."""
    global _PORT
    _PORT = int(port)


def _err(status: int, error: str, message: str = '', **extra):
    body = {'error': error}
    if message:
        body['message'] = message
    body.update(extra)
    return jsonify(body), status


def _host_refusal(require_origin: bool = True):
    code = host_check.refusal_code(request.remote_addr, request.headers, _PORT, require_origin)
    if code is None:
        return None
    return _err(403, 'host_only', host_check.refusal_message(code, _PORT), reason=code)


def _library_refusal():
    if ceremony.library_available():
        return None
    return _err(503, 'passkeys_unavailable',
                'The passkey library (webauthn) is not installed on this server.')


def _store_failure(e: store.StoreError):
    if isinstance(e, store.StoreCorrupt):
        _log(f'[passkeys] registry unreadable: {e}', flush=True)
        return _err(503, e.code, 'The passkey registry could not be read. It is not '
                                 'being treated as empty.')
    status = 404 if isinstance(e, store.UnknownCredential) else 409
    return _err(status, e.code, str(e))


def _body() -> dict:
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}


@bp.route('/api/passkeys', methods=['GET'])
def list_passkeys():
    blocked = host_check.refusal_code(request.remote_addr, request.headers, _PORT,
                                      require_origin=False)
    available = ceremony.library_available()
    out = {
        'available': available,
        'rp_id': host_check.RP_ID,
        'origin': host_check.expected_origin(_PORT),
        # Enrollment needs the library as well as the right door: never say yes
        # to a caller whose POST would answer 503.
        'can_enroll_here': available and blocked is None,
        'enroll_blocked_reason': blocked,
        'enroll_blocked_message': host_check.refusal_message(blocked, _PORT) if blocked else '',
    }
    try:
        state = store.load()
    except store.StoreError as e:
        return _store_failure(e)
    out['enrolled'] = bool(state['enrolled_at'])
    out['credentials'] = store.list_credentials()
    out['active_count'] = sum(1 for c in out['credentials'] if not c['revoked_at'])
    out['max_active'] = store.MAX_ACTIVE_CREDENTIALS
    return jsonify(out)


@bp.route('/api/passkeys/register/options', methods=['POST'])
def register_options():
    refusal = _host_refusal() or _library_refusal()
    if refusal is not None:
        return refusal
    if request.content_length is not None and request.content_length > _MAX_BODY:
        return _err(413, 'too_large')
    data = _body()
    raw_label = data.get('label')
    if raw_label is not None and not isinstance(raw_label, str):
        return _err(400, 'bad_label')
    try:
        state = store.load()
    except store.StoreError as e:
        return _store_failure(e)
    if sum(1 for c in state['credentials'] if not c.get('revoked_at')) >= store.MAX_ACTIVE_CREDENTIALS:
        return _err(409, 'too_many_credentials')
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
    handle = state['owner_handle'] or ceremony.b64url(secrets.token_bytes(32))
    nonce = new_session_nonce()
    try:
        c = CEREMONIES.issue(
            kind='registration', rp_id=host_check.RP_ID,
            origin=host_check.expected_origin(_PORT), owner_handle=handle,
            epoch=state['policy_epoch'], session_nonce=nonce,
            label=store.clean_label(raw_label))
    except TooManyCeremonies:
        return _err(429, 'too_many_ceremonies', 'Finish or wait out the pending enrollment first.')
    try:
        options = ceremony.registration_options(c, store.active_credential_ids())
    except store.StoreError as e:
        return _store_failure(e)
    resp = jsonify({'ceremony_id': c.id, 'options': options, 'expires_in': CHALLENGE_TTL_S})
    resp.set_cookie(_COOKIE, nonce, max_age=CHALLENGE_TTL_S, httponly=True,
                    samesite='Strict', path=_COOKIE_PATH)
    return resp


def _ceremony_refusal(e: CeremonyError):
    if isinstance(e, ExpiredCeremony):
        return _err(410, e.code, 'The enrollment took too long. Start again.')
    if isinstance(e, SessionMismatch):
        return _err(403, e.code, 'This enrollment was started in a different browser session.')
    return _err(400, e.code, 'That enrollment is not pending (already used or never started).')


@bp.route('/api/passkeys/register/finish', methods=['POST'])
def register_finish():
    refusal = _host_refusal() or _library_refusal()
    if refusal is not None:
        return refusal
    if request.content_length is not None and request.content_length > _MAX_BODY:
        return _err(413, 'too_large')
    data = _body()
    ceremony_id = data.get('ceremony_id')
    credential = data.get('credential')
    if not isinstance(ceremony_id, str) or not isinstance(credential, dict):
        return _err(400, 'bad_request', 'ceremony_id and credential are required')
    try:
        c = CEREMONIES.consume(ceremony_id, kind='registration',
                               session_nonce=request.cookies.get(_COOKIE, ''))
    except CeremonyError as e:
        audit.record('register', 'refused', reason=e.code)
        return _ceremony_refusal(e)
    try:
        v = ceremony.verify_registration(credential, c)
    except ceremony.RegistrationRejected as e:
        audit.record('register', 'rejected', reason=e.detail)
        return _err(400, e.code, e.detail)
    try:
        rec = store.add_credential(
            credential_id=v.credential_id, public_key=v.public_key, rp_id=c.rp_id,
            origin=c.origin, transports=v.transports, sign_count=v.sign_count,
            backup_eligible=v.backup_eligible, backup_state=v.backup_state,
            aaguid=v.aaguid, label=c.label, owner_handle=c.owner_handle, epoch=c.epoch)
    except store.StoreError as e:
        audit.record('register', 'refused', v.credential_id, e.code)
        return _store_failure(e)
    audit.record('register', 'ok', v.credential_id)
    _log(f'[passkeys] enrolled {audit.credential_ref(v.credential_id)} from {request.remote_addr}',
         flush=True)
    resp = jsonify({'ok': True, 'credential': rec})
    resp.delete_cookie(_COOKIE, path=_COOKIE_PATH)
    return resp


@bp.route('/api/passkeys/<credential_id>', methods=['DELETE'])
def revoke_passkey(credential_id: str):
    refusal = _host_refusal()
    if refusal is not None:
        return refusal
    if not _ID_RE.match(credential_id or ''):
        return _err(400, 'bad_id')
    try:
        store.load()
    except store.StoreError as e:
        return _store_failure(e)
    refusal = _require_human_passcode(_body())
    if refusal is not None:
        return refusal
    try:
        rec = store.revoke(credential_id)
    except store.StoreError as e:
        audit.record('revoke', 'refused', credential_id, e.code)
        return _store_failure(e)
    dropped = CEREMONIES.clear()
    audit.record('revoke', 'ok', credential_id, f'ceremonies_dropped={dropped}')
    _log(f'[passkeys] revoked {audit.credential_ref(credential_id)} from {request.remote_addr}',
         flush=True)
    return jsonify({'ok': True, 'credential': rec})
