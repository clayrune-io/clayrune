"""Remove custom MCP connections through the existing human passcode gate."""
from flask import Blueprint, jsonify, request

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.desk_connect import custom_connection_removal as removal
from mc.desk_connect.mcp_errors import ActivationError
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connection_removal_routes', __name__)


@bp.route('/api/desk/connect/custom/connections', methods=['DELETE'])
def remove_connection():
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human'}), 403
    body = request.get_json(silent=True)
    try:
        target = removal.target(body)
    except ActivationError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    refusal = _require_human_passcode(body if isinstance(body, dict) else {})
    if refusal is not None:
        return refusal
    try:
        return jsonify(removal.remove(*target))
    except ActivationError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    except Exception as e:
        _log(f'[desk-connections] removal failed: {type(e).__name__}', flush=True)
        return jsonify({'error': 'The connection could not be removed. Try again; see the server log.',
                        'code': 'remove_failed'}), 500
