"""Identity endpoint for the first-party `clayrune` MCP server (BUILTINS-7a).

POST /api/clayrune-mcp/identity   {project_id}

Answers "which session is calling?" from caller attribution (process ancestry),
never from the body. The verification itself lives in
`mc.clayrune_mcp.identity`; this file only gathers the request facts.
"""
from flask import Blueprint, jsonify, request

from mc import caller_attribution, state
from mc.clayrune_mcp import identity

bp = Blueprint('clayrune_mcp_routes', __name__)


@bp.route('/api/clayrune-mcp/identity', methods=['POST'])
def clayrune_mcp_identity():
    body = request.get_json(silent=True)
    project_id = str(body.get('project_id') or '').strip() if isinstance(body, dict) else ''
    env = request.environ
    att = caller_attribution.attribute_caller(
        request.remote_addr or '', env.get('REMOTE_PORT'), env.get('SERVER_PORT'),
        caller_attribution.managed_roots(state.agent_sessions, state.tracked_processes))
    status, payload = identity.resolve_caller(att, project_id, state.agent_sessions)
    return jsonify(payload), status
