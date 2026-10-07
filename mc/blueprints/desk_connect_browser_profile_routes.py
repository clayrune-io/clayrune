"""Human-only generic browser connection setup; independent of account setup."""
from flask import Blueprint, jsonify, request

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.desk_connect import browser_connections as connections
from mc.desk_connect.browser_setup import SetupError
from mc.desk_connect.commit import CommitError, clean_request_id
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connect_browser_profile', __name__)


@bp.route('/api/desk/connect/browser-profile/commit', methods=['POST'])
def save():
    if is_unattended_caller():
        return jsonify(error='this action needs a human', code='human_required'), 403
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    try:
        rid = clean_request_id(data.get('request_id'))
        clean = connections.clean_draft(data.get('draft'), own_hosts=((request.host or '').split(':')[0],))
    except (SetupError, CommitError) as e:
        return jsonify(error=str(e), code=e.code), e.status
    refusal = _require_human_passcode(data)
    if refusal is not None:
        return refusal
    try:
        result, duplicate = connections.commit(rid, clean)
    except SetupError as e:
        return jsonify(error=str(e), code=e.code), e.status
    except Exception as e:
        _log(f'[desk_connect] browser profile save failed: {type(e).__name__}', flush=True)
        return jsonify(error='could not save; see the server log', code='failed'), 500
    return jsonify(ok=True, duplicate=duplicate, **result), 200 if duplicate or result['unchanged'] else 201
