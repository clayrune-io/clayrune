"""Desk Studio: delete what Studio made, and Undo it (`mc/desk_studio_items.py`).

A delete moves a draft's storyboard (and the pictures only it uses) or one library
file into a trash entry and answers a restore token; `restore` puts it all back.
Reversible data writes like the storyboard routes, so no passcode and an agent
may call them. Refusals are 409 with the sentence the page shows.
"""
from flask import Blueprint, jsonify, request

from mc import desk_studio_items as _items

bp = Blueprint('desk_studio_items_routes', __name__)


def _call(fn, *args, status=200):
    try:
        out = fn(*args)
    except _items.PieceError as e:
        return jsonify({'error': str(e)}), e.status
    return jsonify(out), status


@bp.route('/api/desk/studio/usage', methods=['GET'])
def studio_usage():
    return _call(_items.usage)


@bp.route('/api/desk/studio/<item_id>', methods=['DELETE'])
def delete_studio_draft(item_id):
    return _call(_items.delete_draft, item_id)


@bp.route('/api/desk/studio/files', methods=['DELETE'])
def delete_studio_file():
    return _call(_items.delete_file, request.args.get('path'))


@bp.route('/api/desk/studio/trash/<token>/restore', methods=['POST'])
def restore_studio_item(token):
    return _call(_items.restore, token)
