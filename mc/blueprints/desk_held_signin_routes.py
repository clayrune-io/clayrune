"""Connect flow, Details step: sign in BEFORE the Save, held in server memory
(`mc/desk_oauth_hold.py`; Dave 2026-10-05, option B, after Ron: "if step 3 is the login
details, all options should be covered there").

    POST /api/desk/connect/<service>/start-held   {passcode, account_id?, hold?: {client_id?, client_secret?}}
         Opens the sign-in like `/start`, but the callback holds the token instead of
         storing it. Answers `{flow_id, auth_url, profile, claim, hold_ttl_s, account_id?}`.
         `claim` is the secret the Save and the cancel must present: the flow id alone,
         which the poll shows, claims nothing. X's `account_id` is the Desk account the
         Save will create when no id was supplied. An explicit existing X id is validated
         through account_attach and uses only that saved account's own OAuth profile.
    POST /api/desk/connect/flows/<flow_id>/cancel {claim}
         The person backed out: drop the held token and revoke it. No passcode (it only
         ever removes something), but it needs the claim.

Same gate as `/start`: refused to an unattended caller, then the retyped dashboard
passcode. The vault gate shows before this is called and `desk_oauth.start` reports a locked
vault before it opens anything.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc import desk as _desk
from mc import desk_account_refs as _refs
from mc import desk_oauth as _oauth
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.desk_connect.providers.key_paste import MAX_VALUE
from mc.desk_connect import account_attach, x_account_attach
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_held_signin_routes', __name__)


def _app_fields(raw) -> dict:
    """The X app as typed in the Connect form (not stored yet): text only, bounded."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError('hold must be an object')
    out = {}
    for key in ('client_id', 'client_secret'):
        v = raw.get(key)
        if v in (None, ''):
            continue
        if not isinstance(v, str) or len(v) > MAX_VALUE:
            raise ValueError(f'{key.replace("_", " ")} must be text of at most {MAX_VALUE} characters')
        out[key] = v.strip()
    return out


@bp.route('/api/desk/connect/<service>/start-held', methods=['POST'])
def start_held(service):
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent session '
                                 'cannot start a sign-in for a service'}), 403
    refused = _require_human_passcode(d)
    if refused is not None:
        return refused
    try:
        hold = _app_fields(d.get('hold'))
    except ValueError as e:
        return jsonify({'error': str(e), 'code': 'invalid'}), 400
    account_id, arg = None, None
    if 'account_id' in d:
        try:
            rec = x_account_attach.target(service, d['account_id'])
        except (account_attach.AttachError, LookupError) as e:
            return jsonify({'error': str(e), 'code': getattr(e, 'code', 'account_refused')}), getattr(e, 'status', 400)
        account_id, arg = rec['account_id'], x_account_attach.oauth_arg(rec)
    elif service in _oauth.PER_ACCOUNT:
        account_id = _desk._new_id('acct')
        arg = _refs.planned_oauth_arg(account_id)
    try:
        if service == 'higgsfield' and _oauth.status('higgsfield')['state'] == 'connected':
            raise _oauth.OAuthError('already_signed_in', 'Higgsfield is already signed in. Disconnect it in '
                                    'Connections first to sign in again.', 409)
        out = _oauth.start(service, arg, hold={**hold, 'account_id': account_id})
    except _oauth.OAuthError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    if account_id:
        out['account_id'] = account_id
    return jsonify(out), 201


@bp.route('/api/desk/connect/flows/<flow_id>/cancel', methods=['POST'])
def cancel_held(flow_id):
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human'}), 403
    claim = d.get('claim')
    if not isinstance(claim, str):
        return jsonify({'ok': False})
    return jsonify(_oauth.cancel_flow(flow_id, claim))
