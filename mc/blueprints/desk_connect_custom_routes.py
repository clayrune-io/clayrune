"""Desk routes for a user-chosen MCP server (`mc/desk_connect/custom_connection_*.py`, slice U2a of
docs/DESK_SERVICE_PROFILES_SPEC.md). Its own blueprint: a new concern does not grow
`desk_connect_routes.py`.

    POST /api/desk/connect/custom/review       {package, entry?, server_name?, args?, credentials?,
                                                scope?, project_id?}: read the npm package without
                                                running it and return the approval card. Writes
                                                nothing: no vault entry, no MCP config, no record.
    POST /api/desk/connect/custom/commit       {request_id, fingerprint, passcode}: the one Save.
                                                Takes no command, version or scope: the operation is
                                                the one Review stored.
    POST /api/desk/connect/custom/connections  {}: every approved server with its truthful state now.

Review and Save are for humans only and Save is passcode-gated, in the fixed order of
`desk_connect_purpose_routes`: refuse an unattended caller, check the shape and the stored
Review (a stale or wrong approval must not cost a passcode guess), check the passcode once, then
write. No request can mark itself approved.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from mc.blueprints.secrets_routes import _require_human_passcode
from mc.core import _log
from mc.desk_connect import custom_connection_service as _service
from mc.desk_connect.mcp_errors import ActivationError
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_connect_custom_routes', __name__)


def _refuse_agent(what: str):
    return jsonify({'error': f'this action needs a human: an unattended agent session cannot {what}'}), 403


def _project(project_id: str):
    """`{id, name, path}` of a project that has a folder, or ActivationError."""
    from mc.blueprints import project_routes as _pr
    p = _pr.load_project(project_id)
    if not p:
        raise ActivationError('that project was not found', 'project_not_found', 404)
    path = p.get('project_path') or None
    if not path:
        raise ActivationError('that project has no folder yet; set it up first, or choose global', 'project_no_path', 400)
    return {'id': project_id, 'name': str(p.get('name') or project_id), 'path': path}


def _path_of(project_id):
    try:
        return _project(project_id)['path']
    except ActivationError:
        return None


def _refusal(e: ActivationError):
    return jsonify({'error': str(e), 'code': e.code}), e.status


@bp.route('/api/desk/connect/custom/review', methods=['POST'])
def review_custom_connection():
    if is_unattended_caller():
        return _refuse_agent('review a user-chosen MCP server')
    body = request.get_json(silent=True)
    try:
        return jsonify(_service.prepare(body, _project))
    except ActivationError as e:
        return _refusal(e)
    except Exception as e:
        _log(f'[desk_connect] custom review failed unexpectedly: {type(e).__name__}', flush=True)
        return jsonify({'error': 'The review failed; see the server log.', 'code': 'failed'}), 500


@bp.route('/api/desk/connect/custom/commit', methods=['POST'])
def commit_custom_connection():
    if is_unattended_caller():
        return _refuse_agent('approve a user-chosen MCP server')
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    try:
        request_id, fingerprint = _service.clean_submission(d)
        _service.check_submission(request_id, fingerprint)
    except ActivationError as e:
        return _refusal(e)
    refusal = _require_human_passcode(d)
    if refusal is not None:
        return refusal
    try:
        result, duplicate = _service.commit(request_id, fingerprint)
    except ActivationError as e:
        return _refusal(e)
    except Exception as e:
        _log(f'[desk_connect] custom commit failed unexpectedly: {type(e).__name__}', flush=True)
        return jsonify({'error': 'could not save; see the server log', 'code': 'failed'}), 500
    return jsonify({**result, 'duplicate': duplicate}), (200 if duplicate else 201)


@bp.route('/api/desk/connect/custom/connections', methods=['POST'])
def custom_connections():
    try:
        return jsonify({'connections': _service.connections(_path_of)})
    except Exception as e:
        _log(f'[desk_connect] custom connection list failed: {type(e).__name__}', flush=True)
        return jsonify({'error': 'could not read the connections; see the server log', 'code': 'failed'}), 500
