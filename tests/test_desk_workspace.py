"""Desk v1 R1-W S0 — route M1, `GET /api/desk/workspace` (mc/desk.py `v1_workspace`).

The v1 store (static/js/desk-v1-store.js) bootstraps from this one read. Pinned:

  * the four-key shape `{projects, campaigns, accounts, pieces}`;
  * campaign state words are translated at the route and never stored
    (running->active, done->completed, dropped->archived; proposed/paused pass
    through) — the legacy Desk keeps reading the stored words;
  * the read is a deep copy: nothing done to the payload reaches the store;
  * accounts are lifted read-only out of presences, first record per channel
    wins, platform-less entries are skipped, capability defaults to `manual`;
  * `pieces` is an honest empty list until slice S4.
"""
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes  # noqa: E402
from mc import desk as _desk  # noqa: E402

PROJECTS = [
    {'id': 'alpha', 'name': 'Alpha',
     'roster': [{'character': 'quill'}, {'character': 'gone', 'removed_at': '2026-01-01'}]},
    {'id': 'beta'},
    {'name': 'no id, skipped'},
]


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda _pid: None,
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
    )
    app.register_blueprint(desk_routes.bp)
    return app.test_client()


def _campaign(client, title='Launch', **kw):
    body = {'title': title, 'thesis': 'Agents need a desk', 'project_id': 'alpha'}
    body.update(kw)
    r = client.post('/api/desk/campaigns', json=body)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_empty_store_returns_the_four_keys(client):
    body = client.get('/api/desk/workspace').get_json()
    assert set(body) == {'projects', 'campaigns', 'accounts', 'pieces'}
    assert body['campaigns'] == [] and body['accounts'] == [] and body['pieces'] == []


def test_projects_carry_roster_presence_and_skip_idless(client):
    _desk.upsert_presence('alpha', {'replies': 'auto', 'desk_agent': 'quill'})
    rows = {p['id']: p for p in client.get('/api/desk/workspace').get_json()['projects']}
    assert set(rows) == {'alpha', 'beta'}
    assert rows['alpha']['name'] == 'Alpha'
    assert rows['alpha']['roster'] == ['quill']          # removed member dropped
    assert rows['alpha']['presence']['replies'] == 'auto'
    assert rows['alpha']['presence']['desk_agent'] == 'quill'
    assert rows['beta']['name'] == 'beta'                # falls back to the id
    assert rows['beta']['presence']['replies'] == 'drafts'
    assert rows['beta']['state'] == 'active'


@pytest.mark.parametrize('stored,v1', [
    ('proposed', 'proposed'), ('running', 'active'), ('paused', 'paused'),
    ('done', 'completed'), ('dropped', 'archived'),
])
def test_campaign_state_translated_out_not_stored(client, stored, v1):
    c = _campaign(client)
    with _desk._store_lock:
        store = _desk._read_store()
        store['campaigns'][c['id']]['state'] = stored
        _desk._write_store(store)
    out = client.get('/api/desk/workspace').get_json()['campaigns'][0]
    assert out['state'] == v1
    assert out['id'] == c['id']
    assert out['projectId'] == 'alpha'
    assert out['plan']['title'] == 'Launch'
    # the legacy read still sees the stored word
    assert client.get('/api/desk/campaigns').get_json()[0]['state'] == stored


def test_v1_campaign_is_a_deep_copy():
    camp = {'id': 'c1', 'title': 'T', 'state': 'running', 'project_id': 'p',
            'plan': {'k': [1]}}
    out = _desk.v1_campaign(camp)
    out['plan']['k'].append(2)
    out['state'] = 'x'
    assert camp['plan'] == {'k': [1]} and camp['state'] == 'running'
    assert 'title' not in camp['plan']


def test_accounts_lifted_from_presences(client):
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'x:ron', 'platform': 'x', 'identity': '@ron'},
        {'channel_id': 'bare-only'},                       # no platform: skipped
    ]})
    _desk.upsert_presence('beta', {'accounts': [
        {'channel_id': 'x:ron', 'platform': 'x', 'identity': '@other'},   # dup: first wins
        {'channel_id': 'li:clay', 'platform': 'linkedin'},
    ]})
    accts = {a['id']: a for a in client.get('/api/desk/workspace').get_json()['accounts']}
    assert set(accts) == {'x:ron', 'li:clay'}
    assert accts['x:ron']['identity'] == '@ron'
    assert accts['x:ron']['capability'] == 'manual'
    assert accts['li:clay']['label'] == 'li:clay'


def test_unwired_loader_is_503(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_routes, 'load_projects', None)
    app = Flask(__name__)
    app.register_blueprint(desk_routes.bp)
    assert app.test_client().get('/api/desk/workspace').status_code == 503
