"""Bring in the projects a user already has in Claude Code (backlog ba3b73f9).

    GET /api/claude-import/scan
        Dry run over `~/.claude/projects/`: `{ok, candidates:[{path, name, id,
        last_activity, session_count}], total, truncated, skipped:{...}}`, newest
        first. Registers nothing and never writes to `~/.claude`.

There is deliberately NO add route here. The UI creates each ticked project
through `POST /api/project/<id>`, the endpoint the "Create Project" form uses, so
the folder-in-use check, the install-dir refusal and the steward-fence install
all apply to an import exactly as they do to a hand-added project.

The scan itself lives in `mc/claude_projects_scan.py`.
"""
from __future__ import annotations

from flask import Blueprint, jsonify

from mc import claude_projects_scan as _scan
from mc import state
from mc.core import _log

bp = Blueprint('claude_import_routes', __name__)


@bp.route('/api/claude-import/scan', methods=['GET'])
def claude_import_scan():
    from mc.blueprints import project_routes as _pr  # late: wired by server.py
    try:
        projects = _pr.load_projects()
        result = _scan.scan(
            registered_paths=[p.get('project_path') or '' for p in projects],
            registered_ids=[p.get('id') or '' for p in projects],
            app_dir=_pr._APP_DIR,
            allow_install_dir=bool(state.CONFIG.get('allow_project_in_install_dir')),
        )
    except Exception as e:
        _log(f"[claude-import] scan failed: {e}", flush=True)
        return jsonify({'ok': False, 'error': f'could not scan Claude Code projects: {e}'}), 500
    return jsonify({'ok': True, **result})
