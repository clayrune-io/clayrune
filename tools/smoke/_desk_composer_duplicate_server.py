"""C4 real Flask/store fixture. Isolated temporary state; no live Desk data."""
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from flask import Flask, jsonify, request, send_file
from werkzeug.serving import make_server
from mc import desk, desk_pieces as pieces
from mc.blueprints import desk_routes

logging.getLogger('werkzeug').setLevel(logging.ERROR)
PROJECTS = [dict(id=pid, name=name, status='active', domain='general', description='', summary='',
                 current_task='Idle', activity_log=[], backlog=[], project_path='/isolated', roster=[], live_agent=None)
            for pid, name in [('alpha', 'Source project'), ('beta', 'Target project')]]
with tempfile.TemporaryDirectory(prefix='composer-duplicate-') as directory:
    sandbox = Path(directory)
    uploads = sandbox / 'uploads'
    uploads.mkdir()
    desk_routes.wire(load_projects_fn=lambda: PROJECTS,
                     load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
                     store_path=sandbox / 'desk.json', signals_path=sandbox / 'signals.jsonl', uploads_root=uploads)
    desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'x', 'platform': 'x', 'identity': '@source'},
        {'channel_id': 'li', 'platform': 'linkedin', 'identity': 'Source'}]})
    for cid, project, accounts, title in [('source', 'alpha', ['x', 'li'], 'Release notes'),
            ('target', 'beta', ['li'], "Ron's other campaign"), ('empty', 'beta', [], 'No channels yet')]:
        desk.create_campaign(title, '', campaign_id=cid, project_id=project, state='draft',
                             plan={'title': title, 'accounts': accounts, 'end': {}, 'cadence': {}})
    picture = uploads / 'overview.png'
    picture.write_bytes((ROOT / 'assets/desk-thumb-campaign.png').read_bytes())
    pieces.create_piece('source', 'post', "Ron's release <post>", piece_id='original',
                        body="Saved copy: Ron's release <script>\nSecond line")
    pieces.add_asset('original', path='overview.png')
    pieces.add_version('original', 'x', body='X customized')
    pieces.add_version('original', 'li', body='LinkedIn customized')
    with desk._store_lock:
        data = desk._read_store()
        data['pieces']['original']['draft_status'] = 'in_review'
        for v in data['pieces']['original']['versions']:
            v.update(state='scheduled', scheduled_at='2030-01-01T12:00:00Z', approved={'by': 'fixture'}, revision=3)
        desk._write_store(data)
    app = Flask(__name__, static_folder=str(ROOT / 'static'))
    app.register_blueprint(desk_routes.bp)

    @app.get('/')
    def index(): return send_file(ROOT / 'static/index.html')

    @app.get('/api/projects')
    def projects(): return jsonify(PROJECTS)

    @app.get('/api/config')
    def config(): return jsonify({'desk_v1': True, 'desk_v1_live': True, 'user_timezone': 'UTC'})

    @app.get('/api/characters')
    def characters(): return jsonify([])

    @app.get('/api/floor')
    def floor(): return jsonify({'bench': []})

    @app.get('/__state')
    def state():
        with desk._store_lock:
            return jsonify(desk._read_store())

    @app.post('/__shutdown')
    def shutdown():
        threading.Thread(target=server.shutdown).start()
        return jsonify({'ok': True})

    @app.get('/api/serve-image')
    @app.get('/api/serve-file')
    def file():
        try:
            raw = request.args.get('path', '')
            rel = Path(raw).relative_to(uploads).as_posix() if Path(raw).is_absolute() else raw
            return send_file(pieces._resolve_under_uploads(rel))
        except (ValueError, pieces.PieceError) as exc:
            return jsonify({'error': str(exc)}), 404

    @app.get('/assets/<path:name>')
    def assets(name): return send_file(ROOT / 'assets' / name)

    server = make_server('127.0.0.1', 0, app, threaded=True)
    print(json.dumps({'port': server.server_port}), flush=True)
    server.serve_forever()
