"""POST /api/hivemind/<id>/integration/release -- a person releases a held
reviewed integration (MC-1179 OS33). HTTP only; the rules are in
mc.hivemind_integration_release. Human-only: a hold is the product telling a
person to look, so no agent or unattended caller may clear it.
"""
from __future__ import annotations

import re

from flask import Blueprint, jsonify

from mc import hivemind_integration_release as release
from mc.human_only_gate import human_only
from mc.merge_review_gate import ReviewRefused

bp = Blueprint('hivemind_integration_routes', __name__)

_ID = re.compile(r'[A-Za-z0-9_-]{1,64}')


@bp.route('/api/hivemind/<hivemind_id>/integration/release', methods=['POST'])
@human_only('release a held hivemind integration')
def hivemind_integration_release(hivemind_id):
    """Archive the held attempt, then start a fresh one."""
    from mc.blueprints import hivemind_routes as api
    if not _ID.fullmatch(hivemind_id):
        return jsonify({'error': 'invalid hivemind id'}), 400
    try:
        result = release.release(api, hivemind_id)
    except ReviewRefused as exc:
        return jsonify({'error': exc.message}), exc.status
    restarted = api._hm_start_integration(hivemind_id)
    return jsonify({'ok': True, 'restarted': bool(restarted), **result})
