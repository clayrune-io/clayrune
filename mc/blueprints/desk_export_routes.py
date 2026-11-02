"""Local Studio MP4 job endpoints; no paid renderer or publication gate."""
from flask import Blueprint, jsonify, request, send_file

from mc import desk_export as export, desk_pieces as pieces
from mc.core import _log

bp = Blueprint('desk_export', __name__)


def _call(fn):
    try:
        return fn()
    except pieces.PieceError as e:
        return jsonify({'error': str(e)}), e.status
    except (OSError, ValueError, RuntimeError) as e:
        _log(f'[desk-export] request failed: {e}', flush=True)
        return jsonify({'error': f'Export unavailable: {e}'}), 503


@bp.post('/api/desk/exports')
def start_export():
    def start():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or not isinstance(data.get('owner'), dict):
            raise pieces.PieceError('Export needs a storyboard owner and scenes.')
        owner = data['owner']
        return jsonify(export.start(owner.get('kind'), owner.get('id'), data.get('scenes'))), 202
    return _call(start)


@bp.get('/api/desk/exports')
def latest_export():
    return _call(lambda: jsonify({'job': export.latest(request.args.get('kind', ''), request.args.get('id', ''))}))


@bp.get('/api/desk/exports/<job_id>')
def export_status(job_id):
    return _call(lambda: jsonify(export.get_job(job_id)))


@bp.get('/api/desk/exports/<job_id>/file')
def export_file(job_id):
    def download():
        job = export.get_job(job_id)
        if job['status'] != 'done':
            raise pieces.PieceError('This export is not finished yet.', 409)
        from pathlib import Path
        path = export.authorize_path(Path(job['path']))
        return send_file(path, mimetype='video/mp4', as_attachment=request.args.get('open') != '1',
                         download_name=path.name, conditional=True)
    return _call(download)
