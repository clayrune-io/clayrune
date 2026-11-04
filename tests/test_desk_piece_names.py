"""Desk v1 - serial default names for unnamed pieces (Ron, 2026-10-31).

An unnamed piece is `Video1`, `Video2`, `Post1`, ... (no space), numbered per
campaign per kind, owned by the server. Pinned:

  * a blank title, a missing title and the old client placeholder `New <kind>`
    (any case) all become the next serial; a title the user typed is kept;
  * the number is one above the highest in the SAME campaign and kind, so a delete
    never re-issues a number below the max, and another campaign or kind never
    shifts it;
  * pieces already stored with a placeholder are renamed once, per campaign per
    kind in created_at order, continuing after any existing serial, idempotently;
  * the serial rides back as `defaultTitle` so the rename box can restore it.
"""
import json
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes  # noqa: E402
from mc import desk_piece_names as _names  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_routes, '_require_human_passcode', lambda data: None)
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=uploads,
    )
    app.register_blueprint(desk_routes.bp)
    client = app.test_client()
    for cid in ('camp-a', 'camp-b'):
        r = client.post('/api/desk/campaigns?shape=v1', json={
            'id': cid, 'state': 'draft', 'projectId': 'alpha', 'rules': {},
            'map': {'stop': 'what', 'done': []},
            'plan': {'brief': 'b', 'title': cid, 'accounts': [], 'cadence': {}, 'end': {}}})
        assert r.status_code == 201, r.get_json()
    return client, tmp_path / 'desk.json'


def _make(client, kind, campaign='camp-a', **kw):
    r = client.post('/api/desk/pieces', json=dict({'campaign_id': campaign, 'kind': kind}, **kw))
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def test_blank_and_placeholder_titles_become_serials(env):
    client, _ = env
    assert _make(client, 'video')['title'] == 'Video1'                    # title omitted
    assert _make(client, 'video', title='')['title'] == 'Video2'          # blank
    assert _make(client, 'video', title='   ')['title'] == 'Video3'       # whitespace
    assert _make(client, 'video', title='New video')['title'] == 'Video4'  # old client placeholder
    assert _make(client, 'video', title='NEW VIDEO')['title'] == 'Video5'  # any case
    assert _make(client, 'video', title='New piece')['title'] == 'Video6'  # kind-less placeholder
    assert [_make(client, k)['title'] for k in ('post', 'image', 'article')] == ['Post1', 'Image1', 'Article1']


def test_a_typed_title_is_kept_and_never_numbered(env):
    client, _ = env
    assert _make(client, 'post', title='Launch notes')['title'] == 'Launch notes'
    assert _make(client, 'post', title='New image')['title'] == 'New image'  # another kind's placeholder: user text
    assert _make(client, 'post')['title'] == 'Post1'


def test_serial_is_one_above_the_highest_so_a_delete_never_reissues_below_max(env):
    client, _ = env
    ids = [_make(client, 'video')['id'] for _ in range(3)]                # Video1..3
    assert client.delete(f'/api/desk/pieces/{ids[1]}').status_code in (200, 204)  # gap at 2
    assert _make(client, 'video')['title'] == 'Video4'                    # not Video2
    assert client.delete(f'/api/desk/pieces/{ids[2]}').status_code in (200, 204)
    assert _make(client, 'video')['title'] == 'Video5'                    # Video4 is still the max


def test_serial_counts_per_campaign_and_per_kind(env):
    client, _ = env
    _make(client, 'video'); _make(client, 'video')
    assert _make(client, 'video', campaign='camp-b')['title'] == 'Video1'
    assert _make(client, 'post')['title'] == 'Post1'
    assert _make(client, 'video')['title'] == 'Video3'


def test_a_renamed_piece_keeps_its_serial_as_default_and_holds_the_number(env):
    client, _ = env
    p = _make(client, 'video')
    assert p['defaultTitle'] == 'Video1'
    r = client.patch(f"/api/desk/pieces/{p['id']}", json={'title': 'Demo reel'})
    assert r.get_json()['title'] == 'Demo reel' and r.get_json()['defaultTitle'] == 'Video1'
    assert _make(client, 'video')['title'] == 'Video2'                    # Video1 is spoken for
    assert _make(client, 'post', title='Named')['defaultTitle'] is None   # a typed title has none


def test_patch_still_refuses_a_blank_title(env):
    client, _ = env
    p = _make(client, 'post')
    assert client.patch(f"/api/desk/pieces/{p['id']}", json={'title': ''}).status_code == 400


# -- backfill -------------------------------------------------------------------

def _piece(pid, camp, kind, title, created, **kw):
    return dict({'id': pid, 'campaign_id': camp, 'kind': kind, 'title': title, 'body': '',
                 'assets': [], 'claims': [], 'versions': [], 'created_at': created}, **kw)


def test_backfill_numbers_per_campaign_per_kind_in_created_order_after_existing_serials():
    store = {'pieces': {p['id']: p for p in [
        _piece('v-late', 'c1', 'video', 'New video', '2026-10-03'),
        _piece('v-early', 'c1', 'video', 'New video', '2026-10-01'),
        _piece('v-named', 'c1', 'video', 'Video7', '2026-10-02'),
        _piece('v-typed', 'c1', 'video', 'My reel', '2026-10-02'),
        _piece('p-1', 'c1', 'post', 'New post', '2026-10-01'),
        _piece('p-other', 'c2', 'post', 'new post', '2026-10-01'),
        _piece('a-1', 'c1', 'article', 'New piece', '2026-10-01'),
        _piece('i-wrongkind', 'c1', 'image', 'New video', '2026-10-01'),  # another kind's words: typed
    ]}}
    assert _names.backfill(store) == 5
    t = {pid: p['title'] for pid, p in store['pieces'].items()}
    assert t == {'v-late': 'Video9', 'v-early': 'Video8', 'v-named': 'Video7', 'v-typed': 'My reel',
                 'p-1': 'Post1', 'p-other': 'Post1', 'a-1': 'Article1', 'i-wrongkind': 'New video'}
    assert store['pieces']['v-early']['default_title'] == 'Video8'
    assert 'default_title' not in store['pieces']['v-typed']


def test_backfill_is_idempotent():
    store = {'pieces': {p['id']: p for p in [
        _piece('a', 'c1', 'post', 'New post', '2026-10-01'), _piece('b', 'c1', 'post', 'New post', '2026-10-02')]}}
    assert _names.backfill(store) == 2
    once = json.dumps(store, sort_keys=True)
    assert _names.backfill(store) == 0
    assert json.dumps(store, sort_keys=True) == once


def test_read_store_backfills_a_legacy_store_and_create_continues_after_it(env):
    client, store_path = env
    data = json.loads(store_path.read_text(encoding='utf-8'))
    data['pieces'] = {p['id']: p for p in [
        _piece('old-1', 'camp-a', 'video', 'New video', '2026-10-01T00:00:00Z'),
        _piece('old-2', 'camp-a', 'video', 'New video', '2026-10-02T00:00:00Z')]}
    store_path.write_text(json.dumps(data), encoding='utf-8')
    rows = client.get('/api/desk/pieces?campaign_id=camp-a').get_json()
    assert [r['title'] for r in rows] == ['Video1', 'Video2']
    assert _make(client, 'video')['title'] == 'Video3'
    # create wrote the store, so the backfilled names are now persisted
    saved = json.loads(store_path.read_text(encoding='utf-8'))['pieces']
    assert saved['old-1']['title'] == 'Video1' and saved['old-2']['title'] == 'Video2'
