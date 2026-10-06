"""MC-1062/05: account-local Desk permission metadata and human-only Save.

This records consent only. No UI advertises it as enforced before ticket 06.
Browser profile/site and arbitrary MCP approval keep their separate endpoints.
"""
from flask import Blueprint, jsonify, request

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.desk_connect import permission_policy as _policy
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connect_permission_routes', __name__)


@bp.route('/api/desk/connect/permissions/<account_id>', methods=['GET'])
def permission_view(account_id: str):
    try:
        return jsonify(_policy.view(account_id))
    except _policy.PolicyError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status


@bp.route('/api/desk/connect/permissions/commit', methods=['POST'])
def permission_commit():
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent session '
                                 'cannot change connection permissions'}), 403
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    try:
        request_id, clean = _policy.clean_submission(body)
    except _policy.PolicyError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    refusal = _require_human_passcode(body)
    if refusal is not None:
        return refusal
    try:
        result, duplicate = _policy.commit(request_id, clean)
    except _policy.PolicyError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    except Exception as e:
        _log(f'[desk_connect] permission commit failed: {type(e).__name__}', flush=True)
        return jsonify({'error': 'permissions could not be saved; see the server log', 'code': 'failed'}), 500
    return jsonify({'ok': True, 'duplicate': duplicate, **result}), 200 if duplicate else 201
