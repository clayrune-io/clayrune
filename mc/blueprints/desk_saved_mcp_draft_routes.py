"""Read saved MCP setup metadata without contacting or starting the server."""
from flask import Blueprint, jsonify, request
from mc.core import _log
from mc.desk_connect import saved_mcp_draft
from mc.desk_connect.mcp_errors import ActivationError
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_saved_mcp_draft', __name__)


@bp.route('/api/desk/connect/custom/saved-draft', methods=['POST'])
def read():
    if is_unattended_caller():
        return jsonify(error='this action needs a human', code='human_required'), 403
    try:
        return jsonify(saved_mcp_draft.read(request.get_json(silent=True)))
    except ActivationError as e:
        return jsonify(error=str(e), code=e.code), e.status
    except Exception as e:
        _log(f'[desk-connect] saved MCP draft failed: {type(e).__name__}', flush=True)
        return jsonify(error='could not read the saved setup; see the server log', code='failed'), 500
