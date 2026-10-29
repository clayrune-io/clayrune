"""The standalone Studio writer's conversation and proposal routes."""
from flask import Blueprint, jsonify, request

from mc import desk_article_chat as _chat
from mc.blueprints import desk_routes

bp = Blueprint('desk_article_chat', __name__)


def _call(fn, *args, **kwargs):
    try:
        return jsonify(fn(*args, **kwargs))
    except _chat.PieceError as e:
        return jsonify({'error': str(e)}), e.status


@bp.route('/api/desk/studio/articles/<article_id>/chat', methods=['GET'])
def get_article_chat(article_id):
    return _call(lambda: {'thread': _chat.read_thread(article_id)})


@bp.route('/api/desk/studio/articles/<article_id>/chat', methods=['POST'])
def post_article_chat(article_id):
    return _call(_chat.chat, article_id, request.get_json(silent=True), desk_routes.load_project)


@bp.route('/api/desk/studio/articles/<article_id>/chat/<turn_id>', methods=['POST'])
def apply_article_proposal(article_id, turn_id):
    return _call(_chat.apply_proposal, article_id, turn_id, request.get_json(silent=True))
