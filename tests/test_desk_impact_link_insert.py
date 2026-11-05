"""MC-1152 P2: the tracked-link insertion seam against the REAL Desk piece store.

Spec: docs/desk_v1/CHANNEL_IMPACT_SPEC.md section 12 row P2. Pinned here:
preview == stored body == what the publisher is handed; nothing existing is
rewritten; approved/sent/archived versions are never edited; the routes are 404
while the flag is off. No network; the Desk store lives in tmp_path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_impact_link_insert as ins  # noqa: E402
from mc import desk_pieces as _pieces  # noqa: E402
from mc import state  # noqa: E402
from mc.blueprints import desk_impact_routes as routes  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402
from mc.desk_impact_links import TrackedLinks  # noqa: E402
from mc.desk_impact_store import ImpactStore  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
CID = 'camp-p'
DEST = 'https://example.com/landing?a=1#top'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_routes, '_require_human_passcode', lambda data: None)
    monkeypatch.setattr(state, 'CONFIG', {ins.CONFIG_KEY: True}, raising=False)
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'data' / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=uploads,
    )
    app.register_blueprint(desk_routes.bp)
    app.register_blueprint(routes.bp)
    client = app.test_client()
    r = client.post('/api/desk/campaigns?shape=v1', json={
        'id': CID, 'state': 'draft', 'projectId': 'alpha', 'rules': {},
        'map': {'stop': 'what', 'done': []},
        'plan': {'brief': 'b', 'title': 'P', 'accounts': [{'id': 'ch-x', 'platform': 'x'}],
                 'cadence': {}, 'end': {}}})
    assert r.status_code == 201, r.get_json()
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-x', 'platform': 'x', 'identity': '@a'}]})
    links = TrackedLinks(ImpactStore(tmp_path / 'data' / 'desk_impact',
                                     data_dir=tmp_path / 'data' / 'projects'),
                         clock=lambda: '2026-11-04T12:00:00Z')
    routes.wire(links=links)
    yield client, links, tmp_path
    routes.wire(links=None)


def _post(client, body='Hello world', version_body=None):
    p = client.post('/api/desk/pieces', json={'campaign_id': CID, 'kind': 'post', 'title': 'T',
                                              'body': body}).get_json()
    kw = {} if version_body is None else {'body': version_body}
    v = client.post(f"/api/desk/pieces/{p['id']}/versions",
                    json=dict({'account_id': 'ch-x'}, **kw)).get_json()['versions'][-1]
    return p['id'], v['id']


def _stored(pid):
    return _pieces.get_stored_piece(pid)


def _req(client, which, pid, vid, dest=DEST):
    return client.post(f'/api/desk/impact/links/{which}',
                       json={'piece_id': pid, 'version_id': vid, 'destination': dest})


def _n_links(links):
    return len(links._store.list_links('alpha', CID))


def test_preview_writes_nothing_and_insert_stores_exactly_the_preview(env):
    client, links, tmp = env
    pid, vid = _post(client, version_body='Own body')
    before = (tmp / 'data' / 'desk.json').read_bytes()
    pv = _req(client, 'preview', pid, vid).get_json()
    assert (tmp / 'data' / 'desk.json').read_bytes() == before
    assert _n_links(links) == 0
    done = _req(client, 'insert', pid, vid).get_json()
    assert done['body_after'] == pv['body_after'] == pv['prepared_payload']
    piece = _stored(pid)
    ver = next(v for v in piece['versions'] if v['id'] == vid)
    assert ver['body'] == pv['prepared_payload']
    # the publisher is handed exactly this
    assert _pieces._effective_body(piece, ver) == pv['prepared_payload']
    assert ver['body'].startswith('Own body ') and 'utm_content=imlk-' in ver['body']
    assert 'ref=desk' not in ver['body']


def test_reinserting_changes_nothing_and_the_row_is_not_duplicated(env):
    client, links, tmp = env
    pid, vid = _post(client, version_body='x')
    first = _req(client, 'insert', pid, vid).get_json()
    snap = (tmp / 'data' / 'desk.json').read_bytes()
    again = _req(client, 'insert', pid, vid).get_json()
    assert again['already_present'] is True and again['url'] == first['url']
    assert (tmp / 'data' / 'desk.json').read_bytes() == snap
    assert _n_links(links) == 1


def test_a_version_with_no_body_gets_a_copy_and_the_shared_body_is_untouched(env):
    client, _, _ = env
    pid, vid = _post(client, body='Shared text')
    out = _req(client, 'insert', pid, vid).get_json()
    piece = _stored(pid)
    ver = next(v for v in piece['versions'] if v['id'] == vid)
    assert piece['body'] == 'Shared text'
    assert ver['body'] == out['body_after'] and ver['body'].startswith('Shared text ')


def test_only_the_target_version_changes(env):
    client, _, _ = env
    pid, vid = _post(client, version_body='mine')
    before = _stored(pid)
    _req(client, 'insert', pid, vid)
    after = _stored(pid)
    for k in before:
        if k not in ('versions', 'updated_at'):
            assert after[k] == before[k], k
    skip = ('body', 'revision', 'updated_at')
    assert ({k: v for k, v in after['versions'][0].items() if k not in skip}
            == {k: v for k, v in before['versions'][0].items() if k not in skip})


@pytest.mark.parametrize('state_name', ['approved', 'scheduled', 'sending', 'submitted',
                                         'verified_published'])
def test_approved_and_sent_versions_are_refused_and_left_alone(env, state_name):
    client, links, tmp = env
    pid, vid = _post(client, version_body='locked')
    path = tmp / 'data' / 'desk.json'
    store = json.loads(path.read_text(encoding='utf-8'))
    for v in store['pieces'][pid]['versions']:
        v['state'] = state_name
    path.write_text(json.dumps(store), encoding='utf-8')
    snap = path.read_bytes()
    r = _req(client, 'insert', pid, vid)
    assert r.status_code == 409 and r.get_json()['code'] == 'locked'
    assert path.read_bytes() == snap
    assert _n_links(links) == 0
    assert _req(client, 'preview', pid, vid).status_code == 409


def test_a_conflicting_destination_is_422_and_stores_nothing(env):
    client, links, _ = env
    pid, vid = _post(client, version_body='b')
    r = _req(client, 'insert', pid, vid, 'https://example.com/p?utm_source=newsletter')
    assert r.status_code == 422 and 'utm_source' in r.get_json()['keys']
    piece = _stored(pid)
    assert _pieces._effective_body(piece, piece['versions'][0]) == 'b'
    assert _n_links(links) == 0


def test_unknown_piece_and_version_are_404(env):
    client, _, _ = env
    pid, vid = _post(client)
    assert _req(client, 'insert', 'nope', vid).status_code == 404
    assert _req(client, 'insert', pid, 'nope').status_code == 404


def test_every_route_but_status_is_404_while_the_flag_is_off(env, monkeypatch):
    client, _, tmp = env
    pid, vid = _post(client, version_body='b')
    monkeypatch.setattr(state, 'CONFIG', {}, raising=False)
    snap = (tmp / 'data' / 'desk.json').read_bytes()
    assert _req(client, 'insert', pid, vid).status_code == 404
    assert _req(client, 'preview', pid, vid).status_code == 404
    assert client.get('/api/desk/impact/links/status').get_json() == {'enabled': False}
    assert (tmp / 'data' / 'desk.json').read_bytes() == snap
    monkeypatch.setattr(state, 'CONFIG', {ins.CONFIG_KEY: 'true'}, raising=False)
    assert ins.enabled() is False        # only a real boolean True turns it on
