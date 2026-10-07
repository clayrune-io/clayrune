"""Record a merge review for an agent branch (MC-1075).

POST /api/project/<pid>/agent/<sid>/review   {sha, verdict, report_path?}

Agents may call this (a review is a verdict, not a capability grant), so the
one thing it has to get right is that a builder cannot pass its own work. The
reviewer identity therefore comes from caller attribution -- the OS-level
process-tree walk in `mc.caller_attribution` -- never from the request body.
All the record rules live in `mc.merge_review_gate`.
"""
from flask import Blueprint, jsonify, request

from mc import agent_worktree, caller_attribution, merge_review_gate as gate, state
from mc.blueprints import project_routes
from mc.core import _log

bp = Blueprint('merge_review_routes', __name__)


def _reviewer():
    """(session_id, attribution) of the caller. Raises ReviewRefused when the
    caller cannot be attributed to a session: self-review cannot be ruled out
    (an UNATTRIBUTED caller can be a builder's own detached helper process),
    so the record is refused rather than accepted on the caller's word.
    Reviews are recorded by attributed reviewer sessions; there is no human
    path through this route."""
    env = request.environ
    att = caller_attribution.attribute_caller(
        request.remote_addr or '', env.get('REMOTE_PORT'), env.get('SERVER_PORT'),
        caller_attribution.managed_roots(state.agent_sessions, state.tracked_processes))
    if att.status == caller_attribution.ATTRIBUTED:
        return att.session_id, 'attributed'
    if att.status == caller_attribution.UNATTRIBUTED:
        raise gate.ReviewRefused(
            403, 'the caller is not attributed to any session, so self-review '
                 'cannot be ruled out; a review is recorded by a reviewer session')
    raise gate.ReviewRefused(
        403, f'cannot tell which session is calling ({att.detail}); a review is '
             f'refused when self-review cannot be ruled out')


@bp.route('/api/project/<project_id>/agent/<session_id>/review', methods=['POST'])
def record_review(project_id, session_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'JSON body required: {sha, verdict, report_path?}'}), 400
    project = project_routes.load_project(project_id)
    if not project:
        return jsonify({'error': 'project not found'}), 404
    try:
        reviewer, how = _reviewer()
        sess = state.agent_sessions.get(reviewer) or {}
        character = (sess.get('character') or {}).get('agent_name') or ''
        rec = gate.record(project, agent_worktree.branch_name(session_id),
                          sha=body.get('sha'), verdict=body.get('verdict'),
                          reviewer_session=reviewer, reviewer_character=character,
                          report_path=body.get('report_path'), attribution=how)
    except gate.ReviewRefused as e:
        _log(f'[merge-review] refused for {project_id}/{session_id[:12]}: {e.message}')
        return jsonify({'error': e.message, **e.extra}), e.status
    except Exception as e:
        _log(f'[merge-review] record failed for {project_id}/{session_id[:12]}: {e}')
        return jsonify({'error': 'could not record the review'}), 500
    _log(f"[merge-review] {rec['verdict']} recorded for {rec['branch']} at "
         f"{rec['sha'][:8]} by {rec['reviewer_session'][:12] or 'unattributed caller'}")
    return jsonify({'ok': True, 'record': rec, 'gate_enabled': gate.enabled(project)})
