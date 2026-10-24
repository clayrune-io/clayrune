"""User-managed library folders; isolated from the Desk route monolith."""
from flask import Blueprint, jsonify, request

from mc import desk_library_folders as folders, desk_pieces as pieces
from mc.core import _log

bp = Blueprint('desk_library_folders', __name__)


def _call(fn, status=200):
    try:
        return jsonify(fn()), status
    except pieces.PieceError as e:
        return jsonify({'error': str(e)}), e.status
    except (OSError, ValueError, RuntimeError) as e:
        _log(f'[desk-library] request failed: {e}', flush=True)
        return jsonify({'error': 'Could not update the library. Try again.'}), 503


def _body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise pieces.PieceError('Send the folder details as an object.')
    return body


@bp.get('/api/desk/library/folders')
def listing():
    return _call(lambda: {'folders': folders.list_folders()})


@bp.post('/api/desk/library/folders')
def create():
    return _call(lambda: folders.create(_body().get('title')), 201)


@bp.patch('/api/desk/library/folders')
def rename():
    return _call(lambda: folders.rename(_body().get('id'), _body().get('title')))


@bp.delete('/api/desk/library/folders')
def delete():
    return _call(lambda: folders.delete(_body().get('id')))


@bp.post('/api/desk/library/folders/copy')
def copy():
    return _call(lambda: folders.copy_files(_body().get('id'), _body().get('files')))
