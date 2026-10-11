"""Read-only list of the agent worktrees the stale-worktree gc preserved.

GET /api/worktrees/preserved[?project=<id>][&size=1][&summary=1][&refresh=1]

`agent_worktree.gc_stale` keeps any orphaned worktree that still holds
unmerged or uncommitted work "for the human" and only logs a count. This lists
them per project (session id, path, branch, last commit, commits ahead of the
base, dirty flag, live-session flag) so a person can see what is being kept.
Nothing here changes a worktree or a branch: the survey is in
`mc.preserved_worktrees` and issues read-only git calls only. There is
deliberately no POST/DELETE sibling; what happens to a preserved worktree is
the human's decision.

  project=<id>  restrict to one project (required with size=1)
  size=1        also walk each listed worktree for its byte size (opt-in:
                31-128 MB x hundreds of folders; time-boxed, per project)
  summary=1     counts only, no per-worktree items
  refresh=1     bypass the 60s per-project cache
"""
import time

from flask import Blueprint, jsonify, request

from mc import preserved_worktrees as pw, state
from mc.blueprints import project_routes
from mc.core import _log

bp = Blueprint('preserved_worktrees_routes', __name__)


def _live_checker(project_id):
    """Same rule as the startup gc's `_is_live`: a session in memory for this
    project, plus a dispatch that is mid-launch."""
    def _is_live(sid):
        s = state.agent_sessions.get(sid)
        if s and s.get('project_id') == project_id:
            return True
        return sid in state.pending_launches
    return _is_live


@bp.route('/api/worktrees/preserved', methods=['GET'])
def list_preserved():
    only = (request.args.get('project') or '').strip()
    want_size = request.args.get('size') in ('1', 'true')
    summary = request.args.get('summary') in ('1', 'true')
    refresh = request.args.get('refresh') in ('1', 'true')
    if want_size and not only:
        return jsonify({'error': 'size=1 needs project=<id>: the size walk is per project'}), 400

    if only:
        p = project_routes.load_project(only)
        if not p:
            return jsonify({'error': 'project not found'}), 404
        projects = [p]
    else:
        projects = [p for p in project_routes.load_projects() if p.get('project_path')]

    t0 = time.monotonic()
    out, totals = [], {'preserved': 0, 'in_use': 0, 'worktrees_total': 0}
    for p in projects:
        pid = p.get('id', '')
        try:
            r = pw.survey_cached(p, is_live=_live_checker(pid),
                                 include_size=want_size, refresh=refresh)
        except Exception as e:
            _log(f'[preserved-worktrees] survey failed for {pid}: {e}')
            continue
        if not r['worktrees_total']:
            continue
        r['name'] = p.get('name') or pid
        if summary:
            r = {k: v for k, v in r.items() if k != 'items'}
        for k in totals:
            totals[k] += r[k]
        out.append(r)
    out.sort(key=lambda r: r['preserved'], reverse=True)
    return jsonify({'totals': totals, 'projects': out,
                    'elapsed_s': round(time.monotonic() - t0, 2)})
