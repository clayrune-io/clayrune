"""Ephemeral real Flask/filesystem backend for MC-1112's browser regression.

Only writes within TemporaryDirectory. Never imports server.py or reads the
operator's Desk store, accounts, vault, config or browser profiles.
"""
import json
import logging
import subprocess
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from flask import Flask, jsonify, request, send_file
from werkzeug.serving import make_server
from mc import desk, desk_pieces as pieces, desk_studio_articles as articles
from mc.blueprints import desk_routes, desk_piece_lifecycle_routes, desk_studio_items_routes, desk_studio_articles_routes

logging.getLogger('werkzeug').setLevel(logging.ERROR)
PROJECT = {'id': 'alpha', 'name': 'Campaign content test', 'status': 'active', 'domain': 'general',
           'emoji': '', 'description': '', 'summary': '', 'current_task': 'Idle', 'next_action': '',
           'blocked': False, 'activity_log': [], 'backlog': [], 'project_path': '/isolated',
           'last_updated': '2026-10-23T00:00:00Z', 'last_updated_relative': 'today',
           'live_agent': None, 'display_order': 0, 'provider': 'claude', 'roster': []}


with tempfile.TemporaryDirectory(prefix='mc1112-ui-') as directory:
    sandbox = Path(directory); uploads = sandbox / 'uploads'; uploads.mkdir()
    desk_routes.wire(load_projects_fn=lambda: [PROJECT], load_project_fn=lambda pid: PROJECT if pid == 'alpha' else None,
                     store_path=sandbox / 'desk.json', signals_path=sandbox / 'signals.jsonl', uploads_root=uploads)
    desk.create_campaign('Campaign content', 'A real backend regression', campaign_id='camp-test', project_id='alpha', state='draft',
                         plan={'title': 'Campaign content', 'accounts': [], 'end': {}, 'cadence': {}})
    # Direct fixture records avoid vault metadata/network calls in account setup.
    with desk._store_lock:
        store = desk._read_store()
        store['accounts'] = {
            'ch-blog': {'id': 'ch-blog', 'platform': 'blog', 'identity': 'CL Blog', 'capability': 'manual'},
            'ch-off': {'id': 'ch-off', 'platform': 'reddit', 'identity': 'Unconnected Reddit', 'capability': 'api'},
        }
        desk._write_store(store)
    desk.set_suggestions('camp-test', {'what': [{'title': 'Nothing you want gets lost', 'channel_id': 'ch-blog', 'because': []}]})
    picture = uploads / 'desk/library/image/Demos/overview.png'; picture.parent.mkdir(parents=True)
    picture.write_bytes((ROOT / 'assets/desk-thumb-campaign.png').read_bytes())
    document = uploads / 'desk/library/file/Demos/guide.pdf'; document.parent.mkdir(parents=True); document.write_bytes(b'%PDF-1.1\n%%EOF')
    video = uploads / 'desk/library/video/Demos/demo.webm'; video.parent.mkdir(parents=True)
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=teal:s=320x180:d=1',
                    '-c:v', 'libvpx-vp9', '-an', str(video)], check=True, timeout=15)
    piece = pieces.create_piece('camp-test', 'article', 'Embedded media article', piece_id='article-test',
        body='Opening paragraph\n![Overview](desk/library/image/Demos/overview.png)\n[Demo](desk/library/video/Demos/demo.webm)\n[Guide](desk/library/file/Demos/guide.pdf)\nClosing paragraph')
    articles.put_article('artd-test', {'rev': 0, 'topic': 'Standalone library article', 'tabs': [{'id': 'draft', 'label': 'Draft', 'body': 'Standalone text'}]})
    app = Flask(__name__, static_folder=str(ROOT / 'static'))
    app.register_blueprint(desk_routes.bp)
    app.register_blueprint(desk_piece_lifecycle_routes.bp)
    app.register_blueprint(desk_studio_items_routes.bp)
    app.register_blueprint(desk_studio_articles_routes.bp)

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
        # Match production reads: on Windows a reader racing os.replace can
        # receive a sharing violation and _read_store's empty-read fallback.
        with desk._store_lock:
            return jsonify(desk._read_store())

    @app.post('/__shutdown')
    def shutdown():
        server.shutdown()
        return jsonify({'ok': True})

    @app.get('/api/serve-image')
    @app.get('/api/serve-file')
    def file():
        try: return send_file(pieces._resolve_under_uploads(request.args.get('path')))
        except pieces.PieceError as exc: return jsonify({'error': str(exc)}), exc.status

    @app.get('/assets/<path:name>')
    def assets(name): return send_file(ROOT / 'assets' / name)

    server = make_server('127.0.0.1', 0, app, threaded=True)
    print(json.dumps({'port': server.server_port}), flush=True)
    server.serve_forever()
