"""Identity endpoint for the first-party `clayrune` MCP server (BUILTINS-7a).

POST /api/clayrune-mcp/identity   {project_id}

Answers "which session is calling?" from caller attribution (process ancestry),
never from the body. The verification itself lives in
`mc.clayrune_mcp.identity`; this file only gathers the request facts.
"""
from flask import Blueprint, jsonify, request

from mc import caller_attribution, state
from mc.clayrune_mcp import fence_parity, identity
from mc.blueprints import project_routes

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


# Which request each fence-checked tool is about to make. The server builds the
# body, so the client cannot choose what the fence is asked about.
_CHECKED = {
    'send_message': ('/agent/send', lambda text, target: {'session_id': target, 'message': text}),
    'dispatch_agent': ('/agent/dispatch', lambda text, target: {'task': text}),
}


@bp.route('/api/clayrune-mcp/fence-check', methods=['POST'])
def clayrune_mcp_fence_check():
    """Would the unattended fence have blocked this request sent the curl way?

    POST {project_id, action: send_message|dispatch_agent, text, target_session_id?}
    Caller is attributed server-side exactly as for /identity; an unverifiable
    caller is refused (403), never treated as manual.
    Reply: {ok, armed, blocked, reason}. armed=false (manual session or the
    fence switched off) means nothing was classified and nothing is blocked.
    """
    body = request.get_json(silent=True)
    body = body if isinstance(body, dict) else {}
    project_id = str(body.get('project_id') or '').strip()
    action = str(body.get('action') or '')
    text = body.get('text')
    spec = _CHECKED.get(action)
    if spec is None or not isinstance(text, str) or not text:
        return jsonify({'ok': False, 'error': 'action and text required'}), 400
    env = request.environ
    att = caller_attribution.attribute_caller(
        request.remote_addr or '', env.get('REMOTE_PORT'), env.get('SERVER_PORT'),
        caller_attribution.managed_roots(state.agent_sessions, state.tracked_processes))
    status, who = identity.resolve_caller(att, project_id, state.agent_sessions)
    if status != 200:
        return jsonify({'ok': False, 'error': who.get('error', 'identity refused')}), status
    sess = state.agent_sessions.get(who['session_id']) or {}
    if not fence_parity.armed(sess, state.CONFIG):
        return jsonify({'ok': True, 'armed': False, 'blocked': False, 'reason': ''})
    project = project_routes.load_project(project_id) or {}
    project_path = project.get('project_path') or None
    path, make_body = spec
    blocked, reason = fence_parity.check_request(
        env.get('SERVER_PORT') or 5199, f'/api/project/{project_id}{path}',
        make_body(text, str(body.get('target_session_id') or '')), project_path, project_path)
    return jsonify({'ok': True, 'armed': True, 'blocked': blocked, 'reason': reason})
