"""MC-1126: human-only platform deletion, same proof gate as version approval."""
from flask import Blueprint, jsonify, request

from mc import desk_pieces, desk_post_delete
from mc.blueprints.secrets_routes import _require_human_passcode
from mc.unattended import is_unattended_caller

bp = Blueprint('desk_post_delete_routes', __name__)


@bp.route('/api/desk/pieces/<piece_id>/versions/<version_id>/delete-post', methods=['POST'])
def delete_published_post(piece_id: str, version_id: str):
    data = request.get_json(silent=True) or {}
    if is_unattended_caller():
        return jsonify({'error': 'this action needs a human: an unattended agent session '
                                 'cannot delete a published post'}), 403
    refused = _require_human_passcode(data)
    if refused:
        return refused
    try:
        return jsonify(desk_post_delete.delete_post(piece_id, version_id))
    except desk_post_delete.DeleteError as e:
        return jsonify({'error': str(e), 'code': e.code}), e.status
    except desk_pieces.PieceError as e:
        return jsonify({'error': str(e)}), e.status
