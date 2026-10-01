"""Desk v1 R1-W S9a (MC-1020) - the storyboard store (plan M25/M25b, §5).

Pinned:

  * a storyboard belongs to a VIDEO piece (one, shared by its versions) or to a
    standalone Studio item; a missing owner has an empty board at rev 0, an
    unknown or non-video piece is refused;
  * the scene shape is `{id, label, line, duration_sec, picture, edited}`, order is
    the array index, `pending_edits` are stored, and a malformed list is refused
    whole with a `problems` entry per fault (nothing is half-saved);
  * a PUT is guarded by `rev`: the stored rev wins, a stale or missing one is 409
    (carrying the current rev) / 400, and a good write is rev + 1;
  * a scene picture must be an image under data/uploads/desk/library/image:
    `..`, an absolute path, a symlink out, a video and a missing file are refused,
    and an uploaded picture lands in the library's Storyboards folder, with a
    /api/serve-image `src`, and a refused upload leaves no file behind;
  * deleting a piece (or its campaign) takes its storyboard with it.
"""
import io
import os
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import desk_routes  # noqa: E402
from mc import desk as _desk  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
CID = 'camp-sb'
PNG = b'\x89PNG\r\n\x1a\n' + b'0' * 64


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_routes, '_require_human_passcode', lambda _data: None)
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
    r = client.post('/api/desk/campaigns?shape=v1', json={
        'id': CID, 'state': 'draft', 'projectId': 'alpha', 'rules': {},
        'map': {'stop': 'what', 'done': []},
        'plan': {'brief': 'b', 'title': 'Board', 'accounts': [], 'cadence': {}, 'end': {}}})
    assert r.status_code == 201, r.get_json()
    return client, uploads


def _piece(client, kind='video'):
    r = client.post('/api/desk/pieces', json={'campaign_id': CID, 'kind': kind, 'title': f'A {kind}'})
    assert r.status_code == 201, r.get_json()
    return r.get_json()['id']


def _scene(sid, **kw):
    return dict({'id': sid, 'label': f'Scene {sid}', 'line': 'A line.', 'duration_sec': 3,
                 'picture': None, 'edited': False}, **kw)


def _put(client, pid, rev, scenes, **kw):
    return client.put(f'/api/desk/pieces/{pid}/storyboard',
                      json=dict({'rev': rev, 'scenes': scenes}, **kw))


def _lib_png(uploads, name='shot.png', folder='image/Launch'):
    d = uploads / 'desk' / 'library' / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(PNG)
    return f'desk/library/{folder}/{name}'


# -- owners and shape --------------------------------------------------------------

def test_empty_board_is_rev_zero_and_owner_must_be_a_video_piece(env):
    client, _ = env
    pid = _piece(client)
    r = client.get(f'/api/desk/pieces/{pid}/storyboard')
    assert r.status_code == 200
    assert r.get_json() == {'owner': {'kind': 'piece', 'id': pid}, 'rev': 0, 'title': '',
                            'scenes': [], 'pending_edits': [], 'updated_at': None}
    assert client.get('/api/desk/pieces/nope/storyboard').status_code == 404
    post = _piece(client, kind='post')
    r = client.get(f'/api/desk/pieces/{post}/storyboard')
    assert r.status_code == 400 and 'video' in r.get_json()['error']
    assert _put(client, post, 0, []).status_code == 400


def test_put_round_trips_scene_shape_order_and_pending_edits(env):
    client, _ = env
    pid = _piece(client)
    scenes = [_scene('b', duration_sec=2.5, edited=True), _scene('a'), _scene('c', line='')]
    pending = [{'id': 'pe-1', 'label': 'Reordered “b”'}]
    r = _put(client, pid, 0, scenes, pending_edits=pending)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert body['rev'] == 1
    assert [s['id'] for s in body['scenes']] == ['b', 'a', 'c']          # array index is the order
    assert body['scenes'][0] == {'id': 'b', 'label': 'Scene b', 'line': 'A line.',
                                 'duration_sec': 2.5, 'picture': None, 'edited': True}
    assert body['pending_edits'] == pending
    again = client.get(f'/api/desk/pieces/{pid}/storyboard').get_json()
    assert again['scenes'] == body['scenes'] and again['pending_edits'] == pending and again['rev'] == 1
    # the piece's other routes do not carry it (it is its own store)
    assert 'scenes' not in client.get(f'/api/desk/pieces?campaign_id={CID}').get_json()[0]


def test_two_pieces_keep_their_own_boards_and_studio_items_theirs(env):
    client, _ = env
    a, b = _piece(client), _piece(client)
    assert _put(client, a, 0, [_scene('x')]).status_code == 200
    assert client.get(f'/api/desk/pieces/{b}/storyboard').get_json()['scenes'] == []
    r = client.put('/api/desk/studio/studio-1/storyboard',
                   json={'rev': 0, 'scenes': [_scene('s1')], 'title': ' My clip '})
    assert r.status_code == 200 and r.get_json()['title'] == 'My clip'
    got = client.get('/api/desk/studio/studio-1/storyboard').get_json()
    assert got['owner'] == {'kind': 'studio', 'id': 'studio-1'} and [s['id'] for s in got['scenes']] == ['s1']
    assert client.get('/api/desk/studio/other/storyboard').get_json()['scenes'] == []
    # a piece may not be given a Studio title
    assert _put(client, a, 1, [], title='x').status_code == 400
    assert client.get('/api/desk/studio/bad id/storyboard').status_code in (400, 404)


def test_studio_list_names_only_studio_items_with_title_and_scene_count(env):
    client, _ = env
    assert client.get('/api/desk/studio/storyboards').get_json() == {'storyboards': []}
    pid = _piece(client)
    assert _put(client, pid, 0, [_scene('p1')]).status_code == 200            # a piece's board is not listed
    client.put('/api/desk/studio/studio-a/storyboard', json={'rev': 0, 'scenes': [_scene('a1'), _scene('a2')], 'title': 'Teaser'})
    client.put('/api/desk/studio/studio-b/storyboard', json={'rev': 0, 'scenes': []})
    got = {b['id']: b for b in client.get('/api/desk/studio/storyboards').get_json()['storyboards']}
    assert set(got) == {'studio-a', 'studio-b'}
    assert got['studio-a']['title'] == 'Teaser' and got['studio-a']['scenes'] == 2
    assert got['studio-b']['title'] == '' and got['studio-b']['scenes'] == 0


@pytest.mark.parametrize('mutate, fragment', [
    (lambda s: s.pop('label'), 'label is required'),
    (lambda s: s.update(label='  '), 'label is required'),
    (lambda s: s.update(id='has space'), 'id must be'),
    (lambda s: s.update(duration_sec=0), 'duration_sec'),
    (lambda s: s.update(duration_sec=-1), 'duration_sec'),
    (lambda s: s.update(duration_sec='3'), 'duration_sec'),
    (lambda s: s.update(duration_sec=True), 'duration_sec'),
    (lambda s: s.update(duration_sec=99999), 'duration_sec'),
    (lambda s: s.update(line=5), 'line must be text'),
    (lambda s: s.update(edited='yes'), 'edited must be'),
    (lambda s: s.update(picture='x.png'), 'picture must be'),
])
def test_malformed_scene_is_refused_whole_with_a_problem(env, mutate, fragment):
    client, _ = env
    pid = _piece(client)
    bad = _scene('bad')
    mutate(bad)
    r = _put(client, pid, 0, [_scene('ok'), bad])
    assert r.status_code == 400
    assert any(fragment in p and p.startswith('scene 2') for p in r.get_json()['problems']), r.get_json()
    # nothing half-saved: the good scene did not land and the rev did not move
    after = client.get(f'/api/desk/pieces/{pid}/storyboard').get_json()
    assert after['scenes'] == [] and after['rev'] == 0


def test_duplicate_scene_ids_pending_ids_and_non_list_are_refused(env):
    client, _ = env
    pid = _piece(client)
    r = _put(client, pid, 0, [_scene('a'), _scene('a')])
    assert r.status_code == 400 and any('duplicate id a' in p for p in r.get_json()['problems'])
    r = _put(client, pid, 0, [], pending_edits=[{'id': 'p', 'label': 'x'}, {'id': 'p', 'label': 'y'}])
    assert r.status_code == 400 and any('duplicate id p' in p for p in r.get_json()['problems'])
    r = _put(client, pid, 0, [], pending_edits=[{'id': 'p'}])
    assert r.status_code == 400
    assert _put(client, pid, 0, 'nope').status_code == 400
    assert _put(client, pid, 0, [_scene(f's{i}') for i in range(201)]).status_code == 400


# -- the rev guard -----------------------------------------------------------------

def test_stale_rev_is_409_with_the_current_rev_and_changes_nothing(env):
    client, _ = env
    pid = _piece(client)
    assert _put(client, pid, 0, [_scene('a')]).get_json()['rev'] == 1
    assert _put(client, pid, 1, [_scene('a'), _scene('b')]).get_json()['rev'] == 2
    r = _put(client, pid, 1, [_scene('z')])                              # a second tab still on rev 1
    assert r.status_code == 409
    body = r.get_json()
    assert 'revision 2' in body['error'] and 'current_rev=2' in body['problems']
    assert [s['id'] for s in client.get(f'/api/desk/pieces/{pid}/storyboard').get_json()['scenes']] == ['a', 'b']
    # a rev from the future is stale too, and a replay of the same rev conflicts
    assert _put(client, pid, 9, []).status_code == 409
    assert _put(client, pid, 2, [_scene('c')]).status_code == 200
    assert _put(client, pid, 2, [_scene('d')]).status_code == 409


@pytest.mark.parametrize('rev', [None, 'one', -1, True, 1.5])
def test_missing_or_malformed_rev_is_400(env, rev):
    client, _ = env
    pid = _piece(client)
    body = {'scenes': []}
    if rev is not None:
        body['rev'] = rev
    r = client.put(f'/api/desk/pieces/{pid}/storyboard', json=body)
    assert r.status_code == 400 and 'rev' in r.get_json()['error']
    assert client.put(f'/api/desk/pieces/{pid}/storyboard', data='x').status_code == 400


def test_undo_is_just_another_put_of_the_earlier_list(env):
    client, _ = env
    pid = _piece(client)
    first = [_scene('a'), _scene('b')]
    r1 = _put(client, pid, 0, first).get_json()
    r2 = _put(client, pid, r1['rev'], list(reversed(first))).get_json()
    r3 = _put(client, pid, r2['rev'], first).get_json()
    assert [s['id'] for s in r3['scenes']] == ['a', 'b'] and r3['rev'] == 3


# -- pictures ----------------------------------------------------------------------

def test_picture_in_the_library_is_kept_and_served_via_serve_image(env):
    client, uploads = env
    pid = _piece(client)
    rel = _lib_png(uploads)
    r = _put(client, pid, 0, [_scene('a', picture={'path': rel, 'title': 'Shot'})])
    assert r.status_code == 200, r.get_json()
    pic = r.get_json()['scenes'][0]['picture']
    assert pic['path'] == rel and pic['kind'] == 'image' and pic['title'] == 'Shot'
    assert pic['src'].startswith('/api/serve-image?path=')
    # a response `src` / extra keys sent back are ignored, never stored
    r = _put(client, pid, 1, [_scene('a', picture=dict(pic, src='http://evil/x'))])
    assert r.status_code == 200
    assert r.get_json()['scenes'][0]['picture']['src'].startswith('/api/serve-image?path=')


def _outside_png(uploads):
    other = uploads / 'chat'
    other.mkdir(exist_ok=True)
    (other / 'x.png').write_bytes(PNG)
    return 'chat/x.png'


def test_picture_path_traversal_and_outside_paths_are_refused(env, tmp_path):
    client, uploads = env
    pid = _piece(client)
    _lib_png(uploads)
    secret = tmp_path / 'secret.png'
    secret.write_bytes(PNG)
    cases = {
        'dotdot': 'desk/library/image/Launch/../../../../../secret.png',
        'dotdot-sibling': 'desk/library/image/../../x.png',
        'absolute': str(secret),
        'under uploads but not the library': _outside_png(uploads),
        'library video root': 'desk/library/video/clip.png',
        'empty': '',
    }
    for name, path in cases.items():
        r = _put(client, pid, 0, [_scene('a', picture={'path': path})])
        assert r.status_code in (400, 404), (name, r.get_json())
        assert any(p.startswith('scene 1') for p in r.get_json().get('problems', [])), (name, r.get_json())
    assert client.get(f'/api/desk/pieces/{pid}/storyboard').get_json()['rev'] == 0


def test_picture_symlink_out_of_the_library_is_refused(env, tmp_path):
    client, uploads = env
    pid = _piece(client)
    outside = tmp_path / 'outside.png'
    outside.write_bytes(PNG)
    d = uploads / 'desk' / 'library' / 'image' / 'Links'
    d.mkdir(parents=True)
    try:
        os.symlink(outside, d / 'link.png')
    except (OSError, NotImplementedError):
        pytest.skip('symlinks need privileges on this platform')
    r = _put(client, pid, 0, [_scene('a', picture={'path': 'desk/library/image/Links/link.png'})])
    assert r.status_code == 400


def test_picture_must_be_an_existing_image(env):
    client, uploads = env
    pid = _piece(client)
    d = uploads / 'desk' / 'library' / 'image' / 'Launch'
    d.mkdir(parents=True)
    (d / 'clip.mp4').write_bytes(b'0' * 32)
    (d / 'note.txt').write_text('x')
    for path in ('desk/library/image/Launch/clip.mp4', 'desk/library/image/Launch/note.txt'):
        r = _put(client, pid, 0, [_scene('a', picture={'path': path})])
        assert r.status_code == 400 and 'must be an image' in r.get_json()['problems'][0]
    r = _put(client, pid, 0, [_scene('a', picture={'path': 'desk/library/image/Launch/gone.png'})])
    assert r.status_code == 400 and 'does not exist' in r.get_json()['problems'][0]


def test_a_picture_that_vanishes_reads_as_null_not_as_a_path(env):
    client, uploads = env
    pid = _piece(client)
    rel = _lib_png(uploads)
    assert _put(client, pid, 0, [_scene('a', picture={'path': rel})]).status_code == 200
    (uploads / rel).unlink()
    got = client.get(f'/api/desk/pieces/{pid}/storyboard').get_json()
    assert got['scenes'][0]['picture'] is None and got['scenes'][0]['id'] == 'a'


def test_hand_edited_store_cannot_point_outside_the_library(env):
    client, uploads = env
    pid = _piece(client)
    (uploads / 'chat').mkdir()
    (uploads / 'chat' / 'x.png').write_bytes(PNG)
    assert _put(client, pid, 0, [_scene('a')]).status_code == 200
    store = _desk._read_store()
    store['storyboards'][f'piece:{pid}']['scenes'][0]['picture'] = {'path': 'chat/x.png', 'kind': 'image'}
    _desk._write_store(store)
    assert client.get(f'/api/desk/pieces/{pid}/storyboard').get_json()['scenes'][0]['picture'] is None


def test_picture_upload_lands_in_the_library_storyboards_folder(env):
    client, uploads = env
    pid = _piece(client)
    r = client.post(f'/api/desk/pieces/{pid}/storyboard/pictures',
                    data={'file': (io.BytesIO(PNG), 'My Shot.png')}, content_type='multipart/form-data')
    assert r.status_code == 201, r.get_json()
    pic = r.get_json()
    assert pic['kind'] == 'image' and pic['title'] == 'My Shot.png'
    assert pic['path'].startswith('desk/library/image/Storyboards/')
    assert (uploads / pic['path']).read_bytes() == PNG
    assert pic['src'].startswith('/api/serve-image?path=')
    # and the very path it returns is accepted back on a PUT (and shows in the library)
    assert _put(client, pid, 0, [_scene('a', picture={'path': pic['path'], 'title': pic['title']})]).status_code == 200
    lib = client.get('/api/desk/materials').get_json()['library']['image']
    assert any(f['title'] == 'Storyboards' and f['files'] == 1 for f in lib)
    # the standalone owner uses the same folder
    r = client.post('/api/desk/studio/studio-9/storyboard/pictures',
                    data={'file': (io.BytesIO(PNG), 'b.jpg')}, content_type='multipart/form-data')
    assert r.status_code == 201 and r.get_json()['path'].startswith('desk/library/image/Storyboards/')


def test_picture_upload_refusals_leave_no_file_behind(env):
    client, uploads = env
    pid = _piece(client)
    post = _piece(client, kind='post')
    base = f'/api/desk/pieces/{pid}/storyboard/pictures'

    def up(url, data, name):
        return client.post(url, data={'file': (io.BytesIO(data), name)}, content_type='multipart/form-data')

    assert up(base, b'0' * 16, 'clip.mp4').status_code == 400           # a video is not a scene picture
    assert up(base, b'0' * 16, 'notes.txt').status_code == 400
    assert up(base, b'', 'empty.png').status_code == 400
    assert up('/api/desk/pieces/nope/storyboard/pictures', PNG, 'a.png').status_code == 404
    assert up(f'/api/desk/pieces/{post}/storyboard/pictures', PNG, 'a.png').status_code == 400
    assert client.post(base, data={}, content_type='multipart/form-data').status_code == 400
    folder = uploads / 'desk' / 'library' / 'image' / 'Storyboards'
    assert not folder.exists() or list(folder.iterdir()) == []


# -- lifecycle ---------------------------------------------------------------------

def test_deleting_a_piece_or_its_campaign_takes_the_storyboard_with_it(env):
    client, _ = env
    a, b = _piece(client), _piece(client)
    assert _put(client, a, 0, [_scene('x')]).status_code == 200
    assert _put(client, b, 0, [_scene('y')]).status_code == 200
    assert client.delete(f'/api/desk/pieces/{a}').status_code == 200
    assert f'piece:{a}' not in _desk._read_store().get('storyboards', {})
    assert f'piece:{b}' in _desk._read_store()['storyboards']
    assert client.delete(f'/api/desk/campaigns/{CID}').status_code == 200
    assert f'piece:{b}' not in _desk._read_store().get('storyboards', {})
