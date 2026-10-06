"""Desk Studio: standalone articles (`mc/desk_studio_articles.py`).

An article written in Studio with no campaign behind it: a topic, any project,
optionally a campaign. Reversible data writes like the storyboard routes, so no
passcode and an agent may call them. Refusals carry the sentence the page shows.
"""
from flask import Blueprint, jsonify, request

from mc import desk_studio_articles as _arts
from mc.blueprints import desk_routes as _desk_routes

bp = Blueprint('desk_studio_articles_routes', __name__)


def _call(fn, *args, status=200, **kw):
    try:
        out = fn(*args, **kw)
    except _arts.PieceError as e:
        body: dict = {'error': str(e)}
        if e.problems:
            body['problems'] = e.problems
        return jsonify(body), e.status
    return jsonify(out), status


@bp.route('/api/desk/studio/articles', methods=['GET'])
def list_studio_articles():
    return jsonify({'articles': _arts.list_articles()})


@bp.route('/api/desk/studio/articles/<article_id>', methods=['GET'])
def get_studio_article(article_id):
    return _call(_arts.get_article, article_id)


@bp.route('/api/desk/studio/articles/<article_id>', methods=['PUT'])
def put_studio_article(article_id):
    # The project loader is the Desk blueprint's own (wired at boot), read at call time.
    return _call(_arts.put_article, article_id, request.get_json(silent=True),
                 load_project=_desk_routes.load_project)


@bp.route('/api/desk/studio/articles/<article_id>/attach', methods=['POST'])
def attach_studio_article(article_id):
    d = request.get_json(silent=True) or {}
    return _call(_arts.attach, article_id, d.get('campaign_id'), d.get('piece_id'), status=201)


@bp.route('/api/desk/studio/articles/<article_id>', methods=['DELETE'])
def delete_studio_article(article_id):
    return _call(_arts.delete_article, article_id)


@bp.route('/api/desk/studio/articles/trash/<token>/restore', methods=['POST'])
def restore_studio_article(token):
    return _call(_arts.restore, token)
