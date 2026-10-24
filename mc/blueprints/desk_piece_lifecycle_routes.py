"""Reversible piece lifecycle and acceptance; no publishing or approval route."""
from flask import Blueprint, jsonify, request
from mc import desk_piece_lifecycle as lifecycle, desk_suggestion_pieces as suggestions
from mc.desk_pieces import PieceError

bp = Blueprint('desk_piece_lifecycle', __name__)


def _call(fn, *args):
    try:
        return jsonify(fn(*args))
    except PieceError as exc:
        return jsonify({'error': str(exc)}), exc.status


@bp.post('/api/desk/pieces/<piece_id>/archive')
def archive(piece_id):
    return _call(lifecycle.archive, piece_id)


@bp.post('/api/desk/pieces/<piece_id>/unarchive')
def unarchive(piece_id):
    return _call(lifecycle.archive, piece_id, True)


@bp.post('/api/desk/pieces/<piece_id>/trash')
def trash(piece_id):
    return _call(lifecycle.trash, piece_id)


@bp.post('/api/desk/campaigns/<campaign_id>/materialize')
def materialize(campaign_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('title'), str):
        return jsonify({'error': 'a suggestion title is required'}), 400
    return _call(suggestions.materialize, campaign_id, data['title'], data.get('channel_id'))
