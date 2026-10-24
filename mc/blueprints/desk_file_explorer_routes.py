"""Studio host browse/preview routes. No arbitrary file-serving route."""
import os
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, send_file

from mc import desk_file_explorer as explorer, desk_pieces as pieces, desk_explorer_file
from mc.core import _log, _is_loopback_request
from mc import desk_explorer_search as search

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
    def read():
        local = _is_loopback_request() and not any(k.lower().startswith('cf-') for k in request.headers.keys())
        all_files = request.args.get('filter') == 'all'
        if all_files and not local:
            raise pieces.PieceError('All files is available only on this computer.', 403)
        with search.flight(_client(), _request_id()) as lease:
            if not lease.acquired or lease.stopped():
                raise pieces.PieceError('File search was cancelled. Try again.', 409)
            disconnected = request.environ.get('werkzeug.socket')
            def stopped():
                return (lease.stopped() or not lease.acquired
                        or (disconnected is not None and disconnected.fileno() < 0))
            out = explorer.browse(request.args.get('root', 'home'), request.args.get('path', ''),
                                  query=request.args.get('q', ''), all_files=all_files,
                                  stopped=stopped, deadline=lease.deadline)
            out['allow_all_files'] = local
            return jsonify(out)
    return call(read)


def _client() -> str:
    # The browser ID is a cancellation channel, never an authorization token.
    # Bound its length and the live registry; slots disappear after the last user.
    client = request.args.get('client', '')
    if len(client) > 64 or any(not (c.isalnum() or c in '-_') for c in client):
        raise pieces.PieceError('Invalid file search client.')
    return repr((request.remote_addr, request.headers.get('Cf-Connecting-Ip', '')[:64], client))


def _request_id() -> str:
    value = request.args.get('search', '')
    if len(value) > 32 or (value and not value.isdecimal()):
        raise pieces.PieceError('Invalid file search request.')
    return value


@bp.delete('/api/desk/explorer/search')
def cancel_search():
    def cancel():
        search.cancel(_client(), _request_id())
        return jsonify({'cancelled': True})
    return call(cancel)


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
