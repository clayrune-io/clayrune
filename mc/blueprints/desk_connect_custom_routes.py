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


@bp.route('/api/desk/connect/custom/github/stage', methods=['POST'])
def stage_repository():
    if is_unattended_caller():
        return _refuse_agent('check a user-chosen repository')
    from mc.desk_connect.github_connection import stage
    try:
        return jsonify(stage(request.get_json(silent=True)))
    except ActivationError as e:
        return _refusal(e)


@bp.route('/api/desk/connect/custom/github/check', methods=['POST'])
def check_repository():
    """Start an approved, registered repository once and list its tools (no passcode: it runs only
    the command the passcode approved, the way an agent would)."""
    if is_unattended_caller():
        return _refuse_agent('start a user-chosen repository to check it')
    from mc.desk_connect import custom_connection_store as store, github_activation, github_start_check
    d = request.get_json(silent=True)
    d = d if isinstance(d, dict) else {}
    scope, name = d.get('scope'), d.get('server_name')
    rec = None
    if scope in ('global', 'project') and isinstance(name, str):
        rec = store.get(scope, d.get('project_id') if scope == 'project' else None, name)
    if not rec or rec['operation'].get('ecosystem') != 'github':
        return jsonify({'error': 'That saved repository was not found.', 'code': 'not_found'}), 404
    try:
        path = _project(rec['project_id'])['path'] if rec['scope'] == 'project' else None
    except ActivationError as e:
        return _refusal(e)
    if github_activation.derive_state(rec, path)['state'] != 'registered':
        return jsonify({'error': 'This connection is not registered as approved. Review it again first.', 'code': 'not_registered'}), 409
    return jsonify(github_start_check.check(rec['operation']))


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
    if is_unattended_caller():
        return _refuse_agent('read user-chosen MCP connections')
    try:
        return jsonify({'connections': _service.connections(_path_of)})
    except Exception as e:
        _log(f'[desk_connect] custom connection list failed: {type(e).__name__}', flush=True)
        return jsonify({'error': 'could not read the connections; see the server log', 'code': 'failed'}), 500
