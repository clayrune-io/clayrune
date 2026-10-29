"""Isolated real Desk persistence for the What add dialog smoke. No operator data."""
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
PROJECT = {'id': 'alpha', 'name': 'Add a piece', 'status': 'active', 'domain': 'general',
           'description': '', 'summary': '', 'current_task': 'Idle', 'activity_log': [],
           'backlog': [], 'project_path': '/isolated', 'roster': [], 'live_agent': None}

with tempfile.TemporaryDirectory(prefix='what-add-') as directory:
    sandbox = Path(directory)
    uploads = sandbox / 'uploads'
    uploads.mkdir()
    desk_routes.wire(load_projects_fn=lambda: [PROJECT], load_project_fn=lambda pid: PROJECT,
                     store_path=sandbox / 'desk.json', signals_path=sandbox / 'signals.jsonl', uploads_root=uploads)
    for cid in ['camp-test', 'camp-library']:
        desk.create_campaign('Add a piece' if cid == 'camp-test' else 'Library campaign', '',
                             campaign_id=cid, project_id='alpha', state='draft',
                             plan={'title': 'Add a piece', 'accounts': [], 'end': {}, 'cadence': {}})
    picture = uploads / 'desk/library/image/Demos/overview.png'
    picture.parent.mkdir(parents=True)
    picture.write_bytes((ROOT / 'assets/desk-thumb-campaign.png').read_bytes())
    (picture.parent / 'second.png').write_bytes(picture.read_bytes())
    for cid, pid in [('camp-test', 'post-campaign'), ('camp-library', 'post-library')]:
        pieces.create_piece(cid, 'post', 'Original ' + pid, piece_id=pid, body='Original copy: ' + pid)
        pieces.add_asset(pid, path='desk/library/image/Demos/overview.png', asset_id='original-media')
    pieces.add_asset('post-library', path='desk/library/image/Demos/second.png', asset_id='second-media')
    # Fixture-only approval: no human gate or real destination is exercised.
    with desk._store_lock:
        store = desk._read_store()
        store['pieces']['post-library']['versions'] = [{
            'id': 'source-version', 'account_id': 'fixture-account', 'state': 'scheduled',
            'body': 'Approved destination copy', 'approved': {'by': 'fixture', 'revision': 1},
            'scheduled_at': '2030-01-01T12:00:00Z', 'revision': 1,
        }]
        desk._write_store(store)
    app = Flask(__name__, static_folder=str(ROOT / 'static'))
    app.register_blueprint(desk_routes.bp)

    @app.get('/')
    def index(): return send_file(ROOT / 'static/index.html')

    @app.get('/api/projects')
    def projects(): return jsonify([PROJECT])

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
