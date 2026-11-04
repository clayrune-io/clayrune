"""Composer duplicate route: content only, never a release or approval."""
from flask import Blueprint, jsonify, request

from mc import desk_piece_duplicate, desk_pieces

bp = Blueprint('desk_piece_duplicate_routes', __name__)


@bp.post('/api/desk/pieces/<piece_id>/duplicate')
def duplicate_post(piece_id: str):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or any(k not in ('id', 'campaign_id') for k in data):
        return jsonify({'error': 'body must be an object containing only id and campaign_id'}), 400
    try:
        result = desk_piece_duplicate.duplicate_post(piece_id, data.get('campaign_id'), new_id=data.get('id'))
        return jsonify(result), 201
    except desk_pieces.PieceError as exc:
        return jsonify({'error': str(exc)}), exc.status
