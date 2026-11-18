"""POST /api/hivemind/<id>/synthesize -- a person asks for a synthesis now
(MC-1179 OS26 d).

The dashboard's "Synthesize now" button posted a body the intervene route
rejected and then toasted "Synthesis requested"; no route started a synthesis
at all. This one does what the button says: it dispatches the same orchestrator
`synthesize` task the loop dispatches when a run completes. Human-only because
it starts a tooled agent session.
"""
from __future__ import annotations

import re

from flask import Blueprint, jsonify

from mc.human_only_gate import human_only

bp = Blueprint('hivemind_synthesize_routes', __name__)

_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')


@bp.route('/api/hivemind/<hivemind_id>/synthesize', methods=['POST'])
@human_only('start a hivemind synthesis')
def hivemind_synthesize(hivemind_id):
    """Dispatch one orchestrator synthesis; 409 if the orchestrator is busy."""
    from mc.blueprints import hivemind_routes as api
    if not _ID.fullmatch(hivemind_id):
        return jsonify({'error': 'invalid hivemind id'}), 400
    manifest = api._hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    if api._hm_dispatch_orchestrator(hivemind_id, 'synthesize') is None:
        return jsonify({'error': 'the orchestrator is already running for this hivemind'}), 409
    return jsonify({'ok': True, 'started': True})
