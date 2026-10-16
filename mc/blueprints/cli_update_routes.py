"""MC-1103: let the scheduled CLI script join the built-in retry cycle.

No commands, paths, versions, force flag or process sweep from the caller:
the existing server pass selects due CLIs and retains every in-use guard.
"""
from flask import Blueprint, jsonify

from mc import cli_update
from mc.blueprints.system_routes import _run_cli_update

bp = Blueprint('cli_update_routes', __name__)


@bp.post('/api/cli-updates/run')
def run_cli_updates():
    report = _run_cli_update()
    rows = [{'name': name, **rec} for name, rec in cli_update.snapshot().items()]
    return jsonify({**report, 'clis': rows})
