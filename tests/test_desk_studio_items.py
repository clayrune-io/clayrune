"""Deleting what Studio made, and Undo (mc/desk_studio_items.py).

Pinned:

  * deleting a draft takes its storyboard off the server and moves the scene
    pictures ONLY IT uses out of the material library; restore puts the same board
    (same rev, scenes, title) and the same bytes back at the same paths;
  * a picture another storyboard or a campaign piece also uses stays in the library;
  * a draft whose latest render is queued/rendering is refused (409), a finished
    one is not;
  * a library file a campaign piece carries is refused with the campaign's name,
    one a Studio draft's scene uses is refused, a free one is trashed and restored;
  * a token is single use, must be well formed, and cannot be forged into a path;
  * `usage()` reports the same facts the delete enforces.
"""
import io
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes, desk_studio_items_routes  # noqa: E402
from mc import desk_engines as _engines  # noqa: E402
from mc import desk_studio_items as _items  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
CID = 'camp-1'
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 64


@pytest.fixture
def env(tmp_path, monkeypatch):
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
    monkeypatch.setattr(_engines, 'JOBS_PATH', tmp_path / 'jobs.json')
    app.register_blueprint(desk_routes.bp)
    app.register_blueprint(desk_studio_items_routes.bp)
    client = app.test_client()
    r = client.post('/api/desk/campaigns?shape=v1', json={
        'id': CID, 'state': 'draft', 'projectId': 'alpha', 'rules': {},
        'map': {'stop': 'what', 'done': []},
        'plan': {'brief': 'b', 'title': 'Windows beta', 'accounts': [], 'cadence': {}, 'end': {}}})
    assert r.status_code == 201, r.get_json()
    return client, uploads


def _scene(sid, picture=None):
    return {'id': sid, 'label': f'Scene {sid}', 'line': 'A line.', 'duration_sec': 3, 'picture': picture, 'edited': False}


def _upload(client, owner_id, name='hero.png', data=PNG):
    r = client.post(f'/api/desk/studio/{owner_id}/storyboard/pictures',
                    data={'file': (io.BytesIO(data), name)}, content_type='multipart/form-data')
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _draft(client, uploads, owner_id='studio-a', pictures=1, title='New video'):
    scenes = []
    for i in range(pictures):
        pic = _upload(client, owner_id, f'hero{i}.png', PNG + bytes([i]))
        scenes.append(_scene(f's{i}', {'path': pic['path'], 'title': pic['title']}))
    scenes.append(_scene('plain'))
    r = client.put(f'/api/desk/studio/{owner_id}/storyboard', json={'rev': 0, 'scenes': scenes, 'title': title})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _file(uploads, rel):
    return uploads / rel


def _paths(board):
    return [s['picture']['path'] for s in board['scenes'] if s['picture']]


def _ids(client):
    return [b['id'] for b in client.get('/api/desk/studio/storyboards').get_json()['storyboards']]


def _piece_with_asset(client, rel):
    pid = client.post('/api/desk/pieces', json={'campaign_id': CID, 'kind': 'video', 'title': 'A video'}).get_json()['id']
    r = client.post(f'/api/desk/pieces/{pid}/assets', json={'path': rel})
    assert r.status_code == 201, r.get_json()
    return pid


def _render(status, owner_id='studio-a', created='2026-10-03T10:00:00Z'):
    store = _engines._read_store()
    store['renders'][f'rnd-{created}'] = {'render_id': f'rnd-{created}', 'status': status, 'created_at': created,
                                          'owner': {'kind': 'studio', 'id': owner_id}}
    _engines._write_store(store)


# -- drafts ------------------------------------------------------------------------

def test_delete_draft_trashes_board_and_its_own_pictures_and_restore_is_exact(env):
    client, uploads = env
    board = _draft(client, uploads, pictures=2)
    pics = _paths(board)
    assert len(pics) == 2 and all(_file(uploads, p).is_file() for p in pics)

    r = client.delete('/api/desk/studio/studio-a')
    assert r.status_code == 200, r.get_json()
    out = r.get_json()
    assert out['files'] == 2 and len(out['token']) == 32
    assert _ids(client) == []
    assert not any(_file(uploads, p).exists() for p in pics)            # gone from the library
    assert client.get('/api/desk/studio/studio-a/storyboard').get_json()['rev'] == 0
    assert (uploads / 'desk' / '_trash' / out['token']).is_dir()

    back = client.post(f"/api/desk/studio/trash/{out['token']}/restore")
    assert back.status_code == 200 and back.get_json() == {'kind': 'draft', 'id': 'studio-a'}
    again = client.get('/api/desk/studio/studio-a/storyboard').get_json()
    assert again['rev'] == board['rev'] and again['title'] == 'New video'
    assert [s['id'] for s in again['scenes']] == [s['id'] for s in board['scenes']]
    assert _paths(again) == pics
    assert all(_file(uploads, p).read_bytes().startswith(PNG) for p in pics)   # the same bytes
    assert not (uploads / 'desk' / '_trash' / out['token']).exists()
    assert _ids(client) == ['studio-a']


def test_a_picture_another_board_or_a_piece_uses_stays_in_the_library(env):
    client, uploads = env
    a = _draft(client, uploads, 'studio-a', pictures=1)
    shared = _paths(a)[0]
    # a second draft's scene points at the SAME file
    r = client.put('/api/desk/studio/studio-b/storyboard',
                   json={'rev': 0, 'scenes': [_scene('x', {'path': shared, 'title': 'hero'})], 'title': 'Other'})
    assert r.status_code == 200, r.get_json()
    out = client.delete('/api/desk/studio/studio-a').get_json()
    assert out['files'] == 0 and _file(uploads, shared).is_file()
    # ...and the same when a campaign piece carries it
    c = _draft(client, uploads, 'studio-c', pictures=1)
    held = _paths(c)[0]
    _piece_with_asset(client, held)
    out = client.delete('/api/desk/studio/studio-c').get_json()
    assert out['files'] == 0 and _file(uploads, held).is_file()
    # undo still restores the boards
    assert client.post(f"/api/desk/studio/trash/{out['token']}/restore").status_code == 200
    assert 'studio-c' in _ids(client)


def test_a_draft_still_rendering_is_refused_a_finished_one_is_not(env):
    client, uploads = env
    _draft(client, uploads)
    for status in ('queued', 'rendering'):
        _render(status)
        r = client.delete('/api/desk/studio/studio-a')
        assert r.status_code == 409 and 'Rendering' in r.get_json()['error']
        assert _ids(client) == ['studio-a']
    _render('ready', created='2026-10-03T11:00:00Z')                    # the NEWEST render decides
    assert client.delete('/api/desk/studio/studio-a').status_code == 200


def test_unknown_or_malformed_draft(env):
    client, _ = env
    assert client.delete('/api/desk/studio/never-saved').status_code == 404
    assert client.delete('/api/desk/studio/bad.id').status_code in (400, 404)


def test_restore_refuses_when_the_id_was_taken_again(env):
    client, uploads = env
    _draft(client, uploads)
    token = client.delete('/api/desk/studio/studio-a').get_json()['token']
    client.put('/api/desk/studio/studio-a/storyboard', json={'rev': 0, 'scenes': [_scene('n')]})
    r = client.post(f'/api/desk/studio/trash/{token}/restore')
    assert r.status_code == 409


# -- files -------------------------------------------------------------------------

def _lib_file(uploads, name='clip.png', folder='image/Studio'):
    d = uploads / 'desk' / 'library' / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(PNG)
    return f'desk/library/{folder}/{name}'


def test_delete_and_restore_a_free_library_file(env):
    client, uploads = env
    rel = _lib_file(uploads)
    r = client.delete('/api/desk/studio/files', query_string={'path': rel})
    assert r.status_code == 200, r.get_json()
    assert not _file(uploads, rel).exists()
    token = r.get_json()['token']
    assert client.post(f'/api/desk/studio/trash/{token}/restore').get_json() == {'kind': 'file', 'id': rel}
    assert _file(uploads, rel).read_bytes() == PNG


def test_a_file_a_campaign_piece_carries_is_refused_with_its_name(env):
    client, uploads = env
    rel = _lib_file(uploads)
    _piece_with_asset(client, rel)
    r = client.delete('/api/desk/studio/files', query_string={'path': rel})
    assert r.status_code == 409
    assert r.get_json()['error'] == 'Attached to Windows beta, detach it there first'
    assert _file(uploads, rel).is_file()


def test_a_picture_a_draft_scene_uses_is_refused(env):
    client, uploads = env
    board = _draft(client, uploads)
    r = client.delete('/api/desk/studio/files', query_string={'path': _paths(board)[0]})
    assert r.status_code == 409 and 'draft' in r.get_json()['error']


@pytest.mark.parametrize('bad', ['', '../../etc/passwd', 'desk/library/image/Studio/missing.png', 'desk/other.png'])
def test_a_path_outside_the_library_or_missing_is_refused(env, bad):
    client, uploads = env
    (uploads / 'desk').mkdir(exist_ok=True)
    (uploads / 'desk' / 'other.png').write_bytes(PNG)
    r = client.delete('/api/desk/studio/files', query_string={'path': bad})
    assert r.status_code in (400, 404)
    assert (uploads / 'desk' / 'other.png').is_file()


# -- tokens and usage --------------------------------------------------------------

def test_a_token_is_single_use_and_cannot_be_a_path(env):
    client, uploads = env
    token = client.delete('/api/desk/studio/files', query_string={'path': _lib_file(uploads)}).get_json()['token']
    assert client.post(f'/api/desk/studio/trash/{token}/restore').status_code == 200
    assert client.post(f'/api/desk/studio/trash/{token}/restore').status_code == 404
    for bad in ('..', 'x' * 32, 'A' * 32, '0' * 31):
        assert client.post(f'/api/desk/studio/trash/{bad}/restore').status_code == 404


def test_usage_reports_what_the_delete_enforces(env):
    client, uploads = env
    board = _draft(client, uploads)
    pic = _paths(board)[0]
    held = _lib_file(uploads, 'held.png')
    _piece_with_asset(client, held)
    _render('rendering')
    u = client.get('/api/desk/studio/usage').get_json()
    assert u['rendering'] == ['studio-a']
    assert u['files'][pic]['draft'] == {'id': 'studio-a', 'title': 'New video'} and u['files'][pic]['attached'] is None
    assert u['files'][held]['attached']['campaign_title'] == 'Windows beta'
    assert _lib_file(uploads, 'free.png') not in u['files']
