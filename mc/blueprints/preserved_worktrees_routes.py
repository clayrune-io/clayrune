"""Read-only list of the agent worktrees the stale-worktree gc preserved.

GET /api/worktrees/preserved[?project=<id>][&size=1][&summary=1][&refresh=1]
                            [&measure=1|force]

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
  measure=1     start a BACKGROUND walk of the project's whole .clayrune/agents
                folder (bytes + file count) unless a result under 30 min old
                exists or one is already running; measure=force ignores the
                age. The request never waits for it: each project carries a
                `disk` block ('unmeasured' | 'measuring' | 'measured', with the
                cached numbers and their age) to poll. The only side effect is
                a read-only directory walk.

Each project also carries `preserved_disk` (sum of the measured sizes of the
kept folders no live session holds; `complete` False = lower bound; also each
item's `size_bytes` once measured) and totals.preserved_bytes.

Each project also carries `unregistered`: folders directly under
.clayrune/agents that `git worktree list` does not know, with their size once
measured (null when git's list could not be read).
"""
import time

from flask import Blueprint, jsonify, request

from mc import preserved_worktrees as pw, state
from mc.blueprints import project_routes
from mc.core import _log

bp = Blueprint('preserved_worktrees_routes', __name__)

DISK_MAX_AGE = 30 * 60   # seconds a measure=1 request leaves a disk result alone


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
    measure = (request.args.get('measure') or '').strip().lower()
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
    totals['preserved_bytes'] = None     # sum of measured sizes; None until a disk walk finished
    num_keys = ('preserved', 'in_use', 'worktrees_total')
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
        if measure in ('1', 'true', 'force'):
            pw.start_measure(p, max_age=None if measure == 'force' else DISK_MAX_AGE)
        r['disk'] = pw.disk_status(p)
        r['unregistered'] = pw.unregistered_with_sizes(p, r.get('unregistered'))
        r['items'], r['preserved_disk'] = pw.items_with_disk(p, r['items'])
        unreg = r['unregistered']
        r['unregistered_disk'] = None if unreg is None else {
            'folders': len(unreg),
            'measured': sum(1 for u in unreg if u['size_bytes'] is not None),
            'bytes': sum(u['size_bytes'] or 0 for u in unreg) if r['disk']['bytes'] is not None else None}
        if summary:
            r['unregistered_count'] = None if unreg is None else len(unreg)
            r = {k: v for k, v in r.items() if k not in ('items', 'unregistered')}
        for k in num_keys:
            totals[k] += r[k]
        pb = r['preserved_disk']['bytes']
        if pb is not None:
            totals['preserved_bytes'] = (totals['preserved_bytes'] or 0) + pb
        out.append(r)
    out.sort(key=lambda r: r['preserved'], reverse=True)
    return jsonify({'totals': totals, 'projects': out,
                    'elapsed_s': round(time.monotonic() - t0, 2)})
