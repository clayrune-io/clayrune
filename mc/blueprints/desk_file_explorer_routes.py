"""Studio host browse/preview routes. No arbitrary file-serving route."""
import os
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, send_file

from mc import desk_file_explorer as explorer, desk_pieces as pieces, desk_explorer_file
from mc.core import _log

bp = Blueprint('desk_file_explorer', __name__)


def call(fn):
    try:
        return fn()
    except pieces.PieceError as e:
        return jsonify({'error': str(e)}), e.status
    except (OSError, ValueError, RuntimeError) as e:
        _log(f'[desk-explorer] request failed: {e}', flush=True)
        return jsonify({'error': 'Could not read that folder. Try again.'}), 503


@bp.get('/api/desk/explorer')
def browse():
    return call(lambda: jsonify(explorer.browse(request.args.get('root', 'home'), request.args.get('path', ''),
                                               query=request.args.get('q', ''), all_files=request.args.get('filter') == 'all')))


@bp.get('/api/desk/explorer/preview')
def preview():
    def read():
        _, path, stream = desk_explorer_file.open_media(request.args.get('root', ''), request.args.get('path', ''))
        try:
            stat = os.fstat(stream.fileno())
            response = send_file(stream, download_name=path.name, conditional=False, max_age=0)
            response.content_length = stat.st_size
            response.last_modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            response.make_conditional(request.environ, accept_ranges=True, complete_length=stat.st_size)
            response.call_on_close(stream.close)
            return response
        except BaseException:
            stream.close()
            raise
    return call(read)
