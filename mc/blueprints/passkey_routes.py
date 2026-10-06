"""Passkey enrollment, list and revoke (`mc/passkeys/`, docs/PASSKEYS_SPEC.md slice 1).

    GET    /api/passkeys                    metadata: library present, whether THIS
                                            request may enroll, credentials (no keys)
    POST   /api/passkeys/register/options   {passcode | proof, label?} -> {ceremony_id, options}
    POST   /api/passkeys/register/finish    {ceremony_id, credential} -> {ok, credential}
    POST   /api/passkeys/assert/options     {purpose: add|revoke, credential_id?}
                                            -> {ceremony_id, options} for navigator.credentials.get
    DELETE /api/passkeys/<id>               {passcode | proof} revoke one credential
    POST   /api/passkeys/recover            {passcode, confirm} lost-all recovery: revoke every credential
    POST   /api/passkeys/reset              {passcode, confirm} quarantine a registry that is refused

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

Slice 2a (docs/PASSKEYS_SPEC.md "Slice 2 prerequisites"): the registry is MAC-ed
under a key from the unlocked vault (`mc.passkeys.integrity`), so a locked or
unconfigured vault makes passkeys unavailable (503 `passkey_vault_*`), and a
registry that fails its MAC is refused (503 `passkey_store_tampered` /
`_unsigned`), never read as empty. Once ONE active passkey exists, adding or
revoking one takes a passkey assertion (`proof`: the `assert/options` ceremony id
plus the `navigator.credentials.get` result) instead of the passcode, and a
rejected assertion never falls back to the passcode. The passcode keeps exactly
two jobs there: `recover` (lost every passkey: revokes them all) and `reset` (the
registry is already refused: moves it aside). Neither acts on a healthy registry
that has a passkey the owner can still use.

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
from mc.passkeys import assertion, audit, ceremony, host_check, recovery, store
from mc.passkeys.challenges import (
    CHALLENGE_TTL_S, STORE as CEREMONIES, CeremonyError, ExpiredCeremony,
    OperationMismatch, SessionMismatch, TooManyCeremonies, new_session_nonce)

bp = Blueprint('passkey_routes', __name__)

_PORT = 5199
_COOKIE = 'mc_passkey_ceremony'
_COOKIE_PATH = '/api/passkeys/register'
_ASSERT_COOKIE = 'mc_passkey_assert'
_ASSERT_COOKIE_PATH = '/api/passkeys'
_RECOVER_CONFIRM = 'revoke-all-passkeys'
_RESET_CONFIRM = 'reset-passkey-registry'
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


_LOCKED_MESSAGES = {
    'vault_locked': 'The vault is locked, so passkeys are unavailable. Unlock it '
                    '(Settings > Vault); until then the dashboard passcode applies.',
    'vault_not_configured': 'Passkeys need a vault passphrase to protect their '
                            'registry. Set one under Settings > Vault.',
}


def _store_failure(e: store.StoreError):
    if isinstance(e, store.StoreLocked):
        return _err(503, e.code, _LOCKED_MESSAGES.get(e.reason, str(e)), reason=e.reason)
    if isinstance(e, store.StoreCorrupt):
        _log(f'[passkeys] registry refused: {e.code}: {e}', flush=True)
        audit.record('registry', 'refused', reason=f'{e.code}: {e}')
        return _err(503, e.code, 'The passkey registry could not be verified. It is '
                                 'being refused, not treated as empty, and the dashboard '
                                 'passcode applies. From this computer, Settings > '
                                 'Passkeys can reset it.', resettable=True)
    if isinstance(e, store.UnknownCredential):
        return _err(404, e.code, str(e))
    return _err(409, e.code, str(e))


def _active(state: dict) -> list:
    return [c for c in state['credentials'] if not c.get('revoked_at')]


def _proof_refusal(proof, *, purpose: str, target: str):
    """None when `proof` is a valid assertion for exactly this operation (its
    ceremony is spent either way); otherwise the refusal response. Never offers
    the passcode: a rejected assertion is a refusal, not a downgrade."""
    try:
        cred_id = assertion.check_proof(
            proof, purpose=purpose, target=target,
            session_nonce=request.cookies.get(_ASSERT_COOKIE, ''))
    except assertion.ProofRequired:
        return _err(403, 'proof_required', 'A passkey is required for this. Approve it '
                    'with a registered passkey; the passcode no longer works here.',
                    needs_passkey=True)
    except ExpiredCeremony as e:
        audit.record(purpose, 'refused', reason=e.code)
        return _err(410, e.code, 'The passkey prompt took too long. Try again.')
    except (SessionMismatch, OperationMismatch) as e:
        audit.record(purpose, 'refused', reason=e.code)
        return _err(403, e.code, 'That passkey approval was for something else.')
    except CeremonyError as e:
        audit.record(purpose, 'refused', reason=e.code)
        return _err(400, e.code, 'That passkey approval is not pending (already used or never started).')
    except store.StoreError as e:
        audit.record(purpose, 'refused', reason=e.code)
        return _store_failure(e)
    except assertion.ProofError as e:
        audit.record(purpose, 'rejected', reason=e.detail)
        return _err(403, e.code, e.detail or 'The passkey was not accepted.')
    audit.record(purpose, 'approved', cred_id)
    return None


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
    vault_blocked = store.integrity_status()
    if vault_blocked:
        out['can_enroll_here'] = False
        out['enroll_blocked_reason'] = vault_blocked
        out['enroll_blocked_message'] = _LOCKED_MESSAGES.get(vault_blocked, '')
    out['enrolled'] = bool(state['enrolled_at'])
    out['credentials'] = store.list_credentials()
    out['active_count'] = len(_active(state))
    out['max_active'] = store.MAX_ACTIVE_CREDENTIALS
    # With one active passkey, add and revoke take a passkey assertion.
    out['needs_passkey_to_change'] = out['active_count'] > 0
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
    blocked = store.integrity_status()
    if blocked:
        return _store_failure(store.StoreLocked(blocked))
    if len(_active(state)) >= store.MAX_ACTIVE_CREDENTIALS:
        return _err(409, 'too_many_credentials')
    if _active(state):
        refusal = _proof_refusal(data.get('proof'), purpose='add', target='')
    else:
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
            label=store.clean_label(raw_label), client=request.remote_addr or '')
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
        state = store.load()
    except store.StoreError as e:
        return _store_failure(e)
    data = _body()
    if _active(state):
        refusal = _proof_refusal(data.get('proof'), purpose='revoke', target=credential_id)
        if refusal is not None:
            return refusal
    # With no active passkey there is nothing a revoke could act on, so the
    # call below reports it unknown without a passcode guess being spent.
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


@bp.route('/api/passkeys/assert/options', methods=['POST'])
def assert_options():
    """Issue the single-use ceremony that approves ONE add or revoke."""
    refusal = _host_refusal() or _library_refusal()
    if refusal is not None:
        return refusal
    if request.content_length is not None and request.content_length > _MAX_BODY:
        return _err(413, 'too_large')
    data = _body()
    purpose = data.get('purpose')
    target = data.get('credential_id') or ''
    if purpose not in assertion.PURPOSES:
        return _err(400, 'bad_purpose')
    if purpose == 'add':
        target = ''
    elif not (isinstance(target, str) and _ID_RE.match(target)):
        return _err(400, 'bad_id')
    try:
        state = store.load()
    except store.StoreError as e:
        return _store_failure(e)
    blocked = store.integrity_status()
    if blocked:
        return _store_failure(store.StoreLocked(blocked))
    active = _active(state)
    if not active:
        return _err(409, 'no_passkey_enrolled',
                    'No passkey is registered, so the dashboard passcode applies.')
    if purpose == 'revoke' and not any(c['id'] == target for c in active):
        return _err(404, 'unknown_credential', target)
    nonce = new_session_nonce()
    try:
        c = CEREMONIES.issue(
            kind='assertion', rp_id=host_check.RP_ID, origin=host_check.expected_origin(_PORT),
            owner_handle=state['owner_handle'], epoch=state['policy_epoch'],
            session_nonce=nonce, label='', purpose=purpose, target=target,
            client=request.remote_addr or '')
    except TooManyCeremonies:
        return _err(429, 'too_many_ceremonies', 'Finish or wait out the pending passkey prompt first.')
    options = assertion.assertion_options(c, [
        {'id': x['id'], 'transports': x.get('transports') or []} for x in active])
    resp = jsonify({'ceremony_id': c.id, 'options': options, 'expires_in': CHALLENGE_TTL_S})
    resp.set_cookie(_ASSERT_COOKIE, nonce, max_age=CHALLENGE_TTL_S, httponly=True,
                    samesite='Strict', path=_ASSERT_COOKIE_PATH)
    return resp


@bp.route('/api/passkeys/recover', methods=['POST'])
def recover():
    """Lost-all recovery (spec decision 3): the retyped passcode, from the host,
    revokes EVERY active passkey so the owner can enroll again with the passcode.
    The one passcode-only way to remove a passkey that still verifies; it is loud
    (audit + log), explicit (`confirm`), and leaves nothing to sign with."""
    refusal = _host_refusal()
    if refusal is not None:
        return refusal
    if request.content_length is not None and request.content_length > _MAX_BODY:
        return _err(413, 'too_large')
    data = _body()
    if data.get('confirm') != _RECOVER_CONFIRM:
        return _err(400, 'bad_confirm', f'confirm must be "{_RECOVER_CONFIRM}"')
    try:
        state = store.load()
    except store.StoreError as e:
        return _store_failure(e)
    blocked = store.integrity_status()
    if blocked:
        return _store_failure(store.StoreLocked(blocked))
    if not _active(state):
        return _err(409, 'nothing_to_recover', 'No passkey is registered.')
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
    try:
        rows = store.revoke_all()
    except store.StoreError as e:
        audit.record('recover', 'refused', reason=e.code)
        return _store_failure(e)
    dropped = CEREMONIES.clear()
    audit.record('recover', 'ok', reason=f'revoked={len(rows)} ceremonies_dropped={dropped}')
    _log(f'[passkeys] lost-all recovery: revoked {len(rows)} passkey(s) from {request.remote_addr}',
         flush=True)
    return jsonify({'ok': True, 'revoked': len(rows)})


@bp.route('/api/passkeys/reset', methods=['POST'])
def reset():
    """Host-only, passcode-gated reset of a registry the server already refuses
    (crash between the registry and marker writes, unsigned, tampered, rolled
    back, unparseable). Moves the files to a `quarantine-<utc>/` directory and
    clears pending ceremonies. Refuses a readable registry: see `recovery`."""
    refusal = _host_refusal()
    if refusal is not None:
        return refusal
    if request.content_length is not None and request.content_length > _MAX_BODY:
        return _err(413, 'too_large')
    data = _body()
    if data.get('confirm') != _RESET_CONFIRM:
        return _err(400, 'bad_confirm', f'confirm must be "{_RESET_CONFIRM}"')
    try:
        store.load()
    except store.StoreCorrupt:
        pass
    except store.StoreError as e:
        return _store_failure(e)
    else:
        return _err(409, 'reset_not_needed', 'The passkey registry is readable; nothing to reset.')
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
    try:
        out = recovery.reset_unusable()
    except store.StoreError as e:
        audit.record('reset', 'refused', reason=e.code)
        return _err(409 if isinstance(e, recovery.NotResettable) else 503, e.code, str(e))
    dropped = CEREMONIES.clear()
    audit.record('reset', 'ok', reason=f"{out['quarantine']} ceremonies_dropped={dropped}")
    _log(f"[passkeys] registry reset from {request.remote_addr}; moved {out['moved']} to {out['quarantine']}",
         flush=True)
    return jsonify({'ok': True, 'quarantine': out['quarantine'], 'moved': out['moved']})
