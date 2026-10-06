"""Desk routes for a user-chosen REMOTE MCP server (`mc/desk_connect/remote_mcp_*.py`, slice U2d of
docs/DESK_SERVICE_PROFILES_SPEC.md). Its own blueprint, beside `desk_connect_custom_routes` (npm).

    POST /api/desk/connect/custom/remote/review  {url, protocol?, server_name?, auth?, issuer?, scopes?,
                                                 credentials?, acknowledge?, scope?, project_id?}: the
                                                 approval card. Writes nothing and sends nothing to the
                                                 address: no lookup, no consent probe, no initialize.
    POST /api/desk/connect/custom/remote/check   {server_name, scope, project_id?}: a human-started check
                                                 of an APPROVED server (initialize and tools/list, with
                                                 its approved credentials, to its approved address).
    POST /api/desk/connect/custom/remote/adopt   {server_name, scope, project_id?, observed, passcode}:
                                                 accept the changed observation the person was shown.

The Save of a remote server is the npm one, `POST /api/desk/connect/custom/commit`: the same stored
Review, the same passcode, the same refusals for an unattended caller. Every route here is for humans
only. Review needs no passcode (it writes nothing); the check needs none either (it contacts only the
address the passcode approved); adopting new capabilities does, like the Save.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc.blueprints.desk_connect_custom_routes import _project, _refusal, _refuse_agent
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.desk_connect import remote_mcp_check as _check
from mc.desk_connect import remote_mcp_service as _service
from mc.desk_connect import remote_mcp_transport as _transport
from mc.desk_connect.mcp_errors import ActivationError
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connect_remote_routes', __name__)


@bp.route('/api/desk/connect/custom/remote/review', methods=['POST'])
def review_remote_connection():
    if is_unattended_caller():
        return _refuse_agent('review a user-chosen remote MCP server')
    try:
        return jsonify(_service.prepare(request.get_json(silent=True), _project))
    except ActivationError as e:
        return _refusal(e)
    except Exception as e:
        _log(f'[desk_connect] remote review failed unexpectedly: {type(e).__name__}', flush=True)
        return jsonify({'error': 'The review failed; see the server log.', 'code': 'failed'}), 500


@bp.route('/api/desk/connect/custom/remote/check', methods=['POST'])
def check_remote_connection():
    if is_unattended_caller():
        return _refuse_agent('run a connection check on a remote MCP server')
    try:
        return jsonify(_check.check_approved(request.get_json(silent=True)))
    except ActivationError as e:
        return _refusal(e)
    except _transport.TransportError as e:
        body = {'error': str(e), 'code': e.code}
        if e.status is not None:
            body['http_status'] = e.status
        if e.location:
            body['location'] = e.location
            body['reask'] = 'The server moved. Review the new address and approve it to use it.'
        return jsonify(body), 502
    except Exception as e:
        _log(f'[desk_connect] remote check failed unexpectedly: {type(e).__name__}', flush=True)
        return jsonify({'error': 'The check failed; see the server log.', 'code': 'failed'}), 500


@bp.route('/api/desk/connect/custom/remote/adopt', methods=['POST'])
def adopt_remote_observation():
    if is_unattended_caller():
        return _refuse_agent('accept a changed remote MCP server')
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    try:
        _check.check_adopt(d)
    except ActivationError as e:
        return _refusal(e)
    refusal = _require_human_passcode(d)
    if refusal is not None:
        return refusal
    try:
        return jsonify(_check.adopt_approved(d))
    except ActivationError as e:
        return _refusal(e)
    except Exception as e:
        _log(f'[desk_connect] remote adopt failed unexpectedly: {type(e).__name__}', flush=True)
        return jsonify({'error': 'could not adopt; see the server log', 'code': 'failed'}), 500
