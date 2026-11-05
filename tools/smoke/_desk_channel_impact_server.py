"""Ephemeral REAL Flask/filesystem backend for the MC-1152 P4 channel-impact smoke.

The Desk store, the ledger, the P1 impact store, the P3 cached comparison and the
P4 routes are all the production code, writing only inside a TemporaryDirectory.
Never imports server.py; never reads the operator's Desk store, vault, config or
browser profiles; never opens a network socket to anything but the loopback page.

What is NOT production here, and the smoke says so: no GitHub connection exists on
this machine, so the connection resolver is an injected one for `conn-1`. This is an
isolated-backend check, not a live GitHub check.

  GET /__cfg?on=0|1&fail=0|1   flip the dark flag / make the compare route answer 500
  GET /__seed                  set a definition and seed stale star snapshots (idempotent)
  GET /__reads                 count of transport/vault/network touches (must stay 0)
"""
import copy
import json
import logging
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from flask import Flask, jsonify, request, send_file
from werkzeug.serving import make_server
from mc import desk, state
from mc import desk_impact_view as view
from mc.blueprints import desk_impact_compare_routes as routes
from mc.blueprints import desk_routes
from mc.desk_impact_sources import base as B
from mc.desk_impact_store import ImpactStore
from tests.desk_impact_github_fakes import TARGET, normalize, star

logging.getLogger('werkzeug').setLevel(logging.ERROR)
PROJECT = {'id': 'alpha', 'name': 'Impact test', 'status': 'active', 'domain': 'general',
           'emoji': '', 'description': '', 'summary': '', 'current_task': 'Idle', 'next_action': '',
           'blocked': False, 'activity_log': [], 'backlog': [], 'project_path': '/isolated',
           'last_updated': '2026-10-23T00:00:00Z', 'last_updated_relative': 'today',
           'live_agent': None, 'display_order': 0, 'provider': 'claude', 'roster': []}
CID = 'camp-p4'
mode = {'fail': False}
touches = {'n': 0}

with tempfile.TemporaryDirectory(prefix='mc1152-p4-') as directory:
    sandbox = Path(directory)
    uploads = sandbox / 'uploads'
    uploads.mkdir()
    state.CONFIG = {view.CONFIG_KEY: False, 'desk_v1': True, 'desk_v1_live': True}
    # The two human-only writes are not driven from the browser here; the gate stays real.
    desk_routes.wire(load_projects_fn=lambda: [PROJECT], load_project_fn=lambda pid: PROJECT if pid == 'alpha' else None,
                     store_path=sandbox / 'data' / 'desk.json', signals_path=sandbox / 'signals.jsonl', uploads_root=uploads)
    desk.create_campaign('Channel impact', 'A real backend smoke', campaign_id=CID, project_id='alpha', state='draft',
                         plan={'title': 'Channel impact', 'accounts': [{'id': 'ch-x', 'platform': 'x'}, {'id': 'ch-li', 'platform': 'linkedin'}],
                               'end': {}, 'cadence': {}})
    desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-x', 'platform': 'x', 'identity': '@clay'},
        {'channel_id': 'ch-li', 'platform': 'linkedin', 'identity': 'Clayrune page'}]})
    with desk._store_lock:
        camp = copy.deepcopy(desk._read_store()['campaigns'][CID])
    start = datetime.fromisoformat(camp['created_at'].replace('Z', '+00:00')).replace(hour=0, minute=0, second=0, microsecond=0)
    now = start + timedelta(days=3, hours=12)
    inside = (start + timedelta(days=1, hours=9)).isoformat()

    def publish(account, platform, outcomes=()):
        row = desk.record_published(platform=platform, voice='', body='b', campaign_id=CID, project_id='alpha',
                                    account=account, published_at=inside, piece_id=None)
        for metric, value in outcomes:
            desk.record_feed_outcome(row['id'], metric, value, at=inside)
    publish('ch-x', 'x', [('impressions', 140), ('likes', 9), ('clicks', 6)])
    publish('ch-x', 'x', [('impressions', 10), ('likes', 1), ('clicks', 2)])
    publish('ch-gone', 'x', [('impressions', 5)])          # an account since removed: history stays

    connections = {'conn-1': {'id': 'conn-1', 'project_id': 'alpha', 'target': TARGET,
                              'vault_ref': 'chosen-by-human', 'read_enabled': True}}

    def resolver(ident):
        return copy.deepcopy(connections.get(ident))

    store = ImpactStore(sandbox / 'data' / 'desk_impact', data_dir=sandbox / 'data' / 'projects',
                        connection_resolver=resolver, clock=lambda: now.isoformat())

    def no_transport(_project_id, _resolve):
        touches['n'] += 1
        raise AssertionError('the smoke never refreshes; no transport may be built')

    view.wire(store=store, connection_resolver=resolver, clock=lambda: now, transport_factory=no_transport)

    app = Flask(__name__, static_folder=str(ROOT / 'static'))
    app.register_blueprint(desk_routes.bp)
    app.register_blueprint(routes.bp)

    @app.before_request
    def maybe_fail():
        if mode['fail'] and request.path.startswith('/api/desk/impact/compare/') and not request.path.endswith('/status'):
            return jsonify({'error': 'the impact record could not be read or saved'}), 500
        return None

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

    @app.get('/__cfg')
    def cfg():
        if 'on' in request.args:
            state.CONFIG[view.CONFIG_KEY] = request.args['on'] == '1'
        if 'fail' in request.args:
            mode['fail'] = request.args['fail'] == '1'
        return jsonify({'enabled': view.enabled(), 'fail': mode['fail']})

    @app.get('/__seed')
    def seed():
        spec = {'label': 'Stars', 'source_id': 'github', 'connection_id': 'conn-1', 'metric': 'stars',
                'unit': 'stars', 'target': TARGET}
        if not store.get_definitions('alpha', CID)['active']:
            store.set_definition('alpha', CID, spec, expected_revision=0)
        w = view.comparison_view(CID)['requested_window']
        window = B.Window(w['start'], w['end'], w['granularity'])
        # Observed a day BEFORE the period ended: the block must say Stale in words.
        observed = (datetime.fromisoformat(w['end']) - timedelta(days=1)).isoformat()
        for row in normalize('stars', [star(1, (start + timedelta(days=1, hours=2)).isoformat().replace('+00:00', 'Z'))],
                             window=window, now=observed):
            store.append_snapshot('alpha', row)
        return jsonify({'seeded': True})

    @app.get('/__reads')
    def reads(): return jsonify(touches)

    @app.get('/__shutdown')
    def shutdown():
        server.shutdown()
        return jsonify({'ok': True})

    @app.get('/assets/<path:name>')
    def assets(name): return send_file(ROOT / 'assets' / name)

    server = make_server('127.0.0.1', 0, app, threaded=True)
    print(json.dumps({'port': server.server_port}), flush=True)
    server.serve_forever()
