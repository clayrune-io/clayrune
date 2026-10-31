"""Uploaded clip scenes preserve containment, revision guards and zero vendor work."""
import io
from pathlib import Path

import pytest

from mc import desk as desk
from mc import desk_engines as engines
from mc import desk_storyboard as storyboard
from mc import desk_storyboard_media as media
from mc import desk_pieces as pieces
from tests.test_desk_storyboard import env, _piece, _scene, _put  # noqa: F401


def upload(client, pid, name='capture.mp4', mime='video/mp4'):
    return client.post(f'/api/desk/pieces/{pid}/storyboard/pictures',
                       data={'file': (io.BytesIO(b'clip bytes'), name, mime)})


@pytest.mark.parametrize('owner', ['piece', 'studio'])
def test_video_asset_round_trip_and_library(env, owner):
    client, uploads = env
    pid = _piece(client)
    response = upload(client, pid)
    assert response.status_code == 201
    ref = response.get_json()
    assert ref['kind'] == 'video'
    assert ref['path'].startswith('desk/library/video/Storyboards/')
    assert ref['src'].startswith('/api/serve-file?inline=1&path=')
    assert (uploads / ref['path']).read_bytes() == b'clip bytes'
    board = storyboard.put_storyboard(owner, pid, {'rev': 0, 'scenes': [_scene('clip', picture=ref, duration_sec=2.375)]})
    assert board['scenes'][0]['picture'] == ref
    assert storyboard.get_storyboard(owner, pid)['scenes'][0]['duration_sec'] == 2.375
    library = client.get('/api/desk/materials').get_json()['library']['video']
    assert any(it['path'] == ref['path'] for folder in library for it in folder['items'])


@pytest.mark.parametrize('path', ['../clip.mp4', '/clip.mp4', 'C:/clip.mp4', r'C:\clip.mp4',
                                 'desk/library/video/Storyboards/../Storyboards/clip.mp4',
                                 'desk/library/image/clip.mp4'])
def test_video_path_escapes_refused_on_write_and_read(env, path):
    client, _ = env
    pid = _piece(client)
    assert _put(client, pid, 0, [_scene('clip', picture={'path': path, 'kind': 'video'})]).status_code == 400
    assert _put(client, pid, 0, [_scene('clip')]).status_code == 200
    store = desk._read_store()
    store['storyboards'][f'piece:{pid}']['scenes'][0]['picture'] = {'path': path, 'kind': 'video'}
    desk._write_store(store)
    assert storyboard.get_storyboard('piece', pid)['scenes'][0]['picture'] is None


def test_symlink_video_escape_is_refused(env, tmp_path):
    client, uploads = env
    pid = _piece(client)
    target = tmp_path / 'outside.mp4'
    target.write_bytes(b'outside')
    folder = uploads / 'desk/library/video/Storyboards'
    folder.mkdir(parents=True)
    link = folder / 'clip.mp4'
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip('host cannot create symlinks')
    assert _put(client, pid, 0, [_scene('clip', picture={'path': 'desk/library/video/Storyboards/clip.mp4', 'kind': 'video'})]).status_code == 400


@pytest.mark.parametrize('name', ['capture', 'capture.txt', 'capture.unknown'])
def test_mime_media_without_a_known_extension_is_kept_in_library(env, name):
    client, _ = env
    pid = _piece(client)
    response = upload(client, pid, name=name, mime='video/mp4')
    assert response.status_code == 201
    ref = response.get_json()
    assert media.resolve_asset(ref['path'], 'video').is_file()
    library = client.get('/api/desk/materials').get_json()['library']['video']
    assert any(it['path'] == ref['path'] for folder in library for it in folder['items'])


def test_seed_marker_survives_delete_and_stale_seed_is_refused(env):
    client, _ = env
    pid = _piece(client)
    ref = upload(client, pid).get_json()
    body = {'rev': 0, 'assets_seeded': True, 'scenes': [_scene('clip', picture=ref)]}
    assert storyboard.put_storyboard('piece', pid, body)['assets_seeded'] is True
    with pytest.raises(storyboard.PieceError) as conflict:
        storyboard.put_storyboard('piece', pid, body)
    assert conflict.value.status == 409
    assert storyboard.put_storyboard('piece', pid, {'rev': 1, 'scenes': []})['assets_seeded'] is True
    assert storyboard.get_storyboard('piece', pid)['scenes'] == []


def test_scene_clip_uses_video_cap_and_cleans_up_refused_upload(env, monkeypatch):
    client, uploads = env
    pid = _piece(client)
    client.application.config['MAX_CONTENT_LENGTH'] = 2
    assert upload(client, pid).status_code == 201  # route overrides the smaller app body cap
    before = set(uploads.rglob('*'))
    monkeypatch.setitem(pieces.MAX_UPLOAD_BYTES, 'video', 4)
    assert upload(client, pid).status_code == 413
    assert set(uploads.rglob('*')) == before


def test_upload_refuses_symlink_library_folder_before_writing(env, tmp_path):
    client, uploads = env
    pid = _piece(client)
    target = tmp_path / 'external'
    target.mkdir()
    folder = uploads / 'desk/library/video'
    folder.mkdir(parents=True)
    try:
        (folder / 'Storyboards').symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('host cannot create symlinks')
    assert upload(client, pid).status_code == 400
    assert list(target.iterdir()) == []


def test_clip_render_refused_before_schema_credentials_or_pricing(env, monkeypatch):
    client, _ = env
    pid = _piece(client)
    ref = upload(client, pid).get_json()
    _put(client, pid, 0, [_scene('clip', picture=ref)])
    monkeypatch.setattr(engines, '_owner_scope', lambda *a: ('piece', pid, None, None))
    monkeypatch.setattr(engines, '_schema_creds', lambda *a: pytest.fail('clip reached vendor schema/credentials'))
    with pytest.raises(engines.Refused) as refused:
        engines._plan_render({'owner': {'kind': 'piece', 'id': pid}, 'engine_id': 'unused'}, unattended=False)
    assert refused.value.code == 'local_clips_pending'


def test_svg_is_never_media():
    """/api/serve-file serves uploads inline, so an .svg must not pass as an image."""
    from mc.desk_pieces import asset_kind_for
    assert asset_kind_for('logo.svg') is None
    assert asset_kind_for('clip.mp4') == 'video'
    assert asset_kind_for('photo.png') == 'image'
