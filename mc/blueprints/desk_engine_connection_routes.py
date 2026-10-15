"""Human/passcode-gated Desk disconnect and reconnect; never modifies a credential."""
from flask import Blueprint, jsonify, request

from mc import desk_engine_connections as connections
from mc import desk_engines
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_engine_connection_routes', __name__)


@bp.route('/api/desk/engines/<engine_id>/connection', methods=['DELETE', 'PUT'])
def engine_connection(engine_id):
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human'}), 403
    if engine_id not in desk_engines.ENGINES:
        return jsonify({'error': 'engine not found'}), 404
    body = request.get_json(silent=True)
    refusal = _require_human_passcode(body if isinstance(body, dict) else {})
    if refusal is not None:
        return refusal
    try:
        connections.set_connected(engine_id, request.method == 'PUT')
    except (OSError, ValueError) as e:
        _log(f'[desk-connections] engine connection write failed: {type(e).__name__}', flush=True)
        return jsonify({'error': 'The connection could not be changed; see the server log.'}), 500
    return jsonify({'ok': True, 'engine_id': engine_id, 'connected': request.method == 'PUT',
                    **({'connection': desk_engines.connection(engine_id)} if request.method == 'PUT' else {})})
