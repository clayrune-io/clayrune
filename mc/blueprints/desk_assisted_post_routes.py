"""Human-started: fill in an approved post on the network's own post screen in the signed-in
pane, then stop. A person presses Share/Post. See mc/desk_assisted_post.py."""
from flask import Blueprint, jsonify, request

from mc import desk_assisted_post as _assisted
from mc.blueprints.desk_routes import _human_only
from mc.core import _log

bp = Blueprint('desk_assisted_post', __name__)


@bp.route('/api/desk/pieces/<piece_id>/versions/<version_id>/assisted-post', methods=['POST'])
def assisted_post(piece_id, version_id):
    d = request.get_json(silent=True) or {}
    refused = _human_only('fill in', d, 'a post in the browser pane')
    if refused:
        return refused
    try:
        return jsonify(_assisted.prepare(piece_id, version_id))
    except _assisted.AssistedError as e:
        return jsonify({'error': str(e), 'code': e.code, **e.extra}), e.status
    except Exception as e:
        _log(f'[desk_assisted_post] failed: {type(e).__name__}', flush=True)
        return jsonify({'error': 'the post could not be filled in; try again', 'code': 'failed'}), 500
