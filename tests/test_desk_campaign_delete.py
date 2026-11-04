"""Deleting a Desk campaign sticks across a reload, and Undo puts all of it back.

Ron 2026-10-30 ('FL3 V2'): a draft deleted from the Desk main screen came back
on reopening the Desk. The browser only ever spliced its own arrays; the
server kept the record, so the next GET /api/desk/workspace re-hydrated it.
The browser half is pinned by tools/smoke/desk-v1-delete-draft-live.mjs; this
pins the server half the browser now relies on:

  * DELETE removes the draft from the workspace a fresh load reads, and from
    the store file on disk (a "reload" = a new read of the file);
  * POST .../restore (the Desk's Undo) brings back the campaign, its pieces
    and their storyboards exactly as deleted;
  * a second restore, a restore of something never deleted, and a restore over
    an id that exists again are refusals with a status, never a silent 200;
  * trash older than KEEP_HOURS is pruned on the next delete, and says so.
"""
import json
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_campaign_trash as _trash  # noqa: E402
from mc import desk_pieces as _pieces  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
DRAFT = {'id': 'camp-draft-fl3v2', 'state': 'draft', 'projectId': 'alpha',
         'plan': {'title': 'FL3 V2 campaign'}}


@pytest.fixture
def env(tmp_path):
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=tmp_path,
    )
    app.register_blueprint(desk_routes.bp)
    return app.test_client(), tmp_path / 'desk.json'


def _workspace_ids(client):
    r = client.get('/api/desk/workspace')
    assert r.status_code == 200
    return [c['id'] for c in r.get_json()['campaigns']]


def _create_draft(client):
    r = client.post('/api/desk/campaigns?shape=v1', json=DRAFT)
    assert r.status_code in (200, 201), r.get_json()
    return r.get_json()['id']


def test_deleted_draft_stays_deleted_after_reload(env):
    client, path = env
    cid = _create_draft(client)
    assert cid in _workspace_ids(client)

    r = client.delete(f'/api/desk/campaigns/{cid}')
    assert r.status_code == 200 and r.get_json() == {'ok': True}

    # A reload is a fresh GET, and the file on disk is what it reads.
    assert cid not in _workspace_ids(client)
    on_disk = json.loads(path.read_text(encoding='utf-8'))
    assert cid not in on_disk['campaigns']
    assert cid in on_disk[_trash.TRASH_KEY]


def test_deleting_twice_is_a_404_not_a_silent_ok(env):
    client, _ = env
    cid = _create_draft(client)
    assert client.delete(f'/api/desk/campaigns/{cid}').status_code == 200
    r = client.delete(f'/api/desk/campaigns/{cid}')
    assert r.status_code == 404 and r.get_json()['error'] == 'campaign not found'


def test_undo_restores_campaign_pieces_and_storyboard(env):
    client, path = env
    cid = _create_draft(client)
    piece = _pieces.create_piece(cid, 'post', 'first post')
    pid = piece['id']
    with _desk._store_lock:
        store = _desk._read_store()
        store.setdefault('storyboards', {})[f'piece:{pid}'] = {'rev': 3, 'scenes': [{'id': 's1'}]}
        _desk._write_store(store)
    before = json.loads(path.read_text(encoding='utf-8'))

    assert client.delete(f'/api/desk/campaigns/{cid}').status_code == 200
    gone = json.loads(path.read_text(encoding='utf-8'))
    assert pid not in gone['pieces'] and f'piece:{pid}' not in gone.get('storyboards', {})

    r = client.post(f'/api/desk/campaigns/{cid}/restore?shape=v1')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['id'] == cid and r.get_json()['state'] == 'draft'
    assert cid in _workspace_ids(client)
    back = json.loads(path.read_text(encoding='utf-8'))
    assert back['campaigns'][cid] == before['campaigns'][cid]
    assert back['pieces'][pid] == before['pieces'][pid]
    assert back['storyboards'][f'piece:{pid}'] == before['storyboards'][f'piece:{pid}']
    assert cid not in back[_trash.TRASH_KEY]


def test_restore_refusals_carry_a_status(env):
    client, _ = env
    assert client.post('/api/desk/campaigns/camp-never/restore').status_code == 404
    cid = _create_draft(client)
    assert client.delete(f'/api/desk/campaigns/{cid}').status_code == 200
    assert client.post(f'/api/desk/campaigns/{cid}/restore').status_code == 200
    assert client.post(f'/api/desk/campaigns/{cid}/restore').status_code == 404

    # Deleted, then a new campaign took the same id: restore must not overwrite it.
    assert client.delete(f'/api/desk/campaigns/{cid}').status_code == 200
    _create_draft(client)
    r = client.post(f'/api/desk/campaigns/{cid}/restore')
    assert r.status_code == 409


def test_old_trash_is_pruned_on_the_next_delete_and_logged(env, monkeypatch):
    client, _ = env
    logged = []
    monkeypatch.setattr(_trash, '_log', lambda m, **k: logged.append(m))
    cid = _create_draft(client)
    assert client.delete(f'/api/desk/campaigns/{cid}').status_code == 200
    with _desk._store_lock:
        store = _desk._read_store()
        store[_trash.TRASH_KEY][cid]['deleted_at'] = '2000-01-01T00:00:00Z'
        _desk._write_store(store)

    other = client.post('/api/desk/campaigns?shape=v1', json={**DRAFT, 'id': 'camp-draft-other'})
    assert other.status_code in (200, 201)
    assert client.delete('/api/desk/campaigns/camp-draft-other').status_code == 200
    trash = _desk._read_store()[_trash.TRASH_KEY]
    assert cid not in trash and 'camp-draft-other' in trash
    assert any('pruned 1' in m and cid in m for m in logged)
