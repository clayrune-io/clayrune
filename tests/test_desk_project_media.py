"""Project discovery is bounded, and sources never become a piece's asset."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from flask import Flask

from mc import desk, desk_pieces as pieces, desk_project_media as media, media as chat_media
from mc.blueprints import desk_routes


def put(root, rel, data=b'project media'):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / 'product'
    root.mkdir()
    uploads = tmp_path / 'data' / 'uploads'
    uploads.mkdir(parents=True)
    media_dir = tmp_path / 'data' / 'media'
    projects = [{'id': 'p', 'name': 'Product', 'project_path': str(root)}]
    for name in ('_load_projects', '_media_dir', '_uploads', '_snapshot'):
        monkeypatch.setattr(media, name, getattr(media, name))
    monkeypatch.setattr(pieces, 'UPLOADS_ROOT', uploads)
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'data' / 'desk.json')
    media.wire(load_projects_fn=lambda: projects, media_dir=media_dir, uploads_root=uploads)
    app = Flask(__name__)
    app.register_blueprint(media.bp)
    return root, uploads, media_dir, projects, app.test_client()


def test_scan_groups_newest_by_project_folder_and_avoids_data_dir(env, tmp_path):
    root, uploads, media_dir, projects, client = env
    old = put(root, 'clips/a.mp4')
    os.utime(old, (1, 1))
    newest = put(root, 'shots/b.png')
    put(root, 'readme.txt')
    value = media.refresh()
    assert [r['path'] for r in value['items']] == [str(newest), str(old)]
    assert not (tmp_path / 'data' / 'projects').exists()
    assert (media_dir / '_desk_project_index.json').is_file()
    group = media.grouped()[0]
    assert (group['project_id'], group['title'], group['files']) == ('p', 'Product', 2)
    assert [f['title'] for f in group['folders']] == ['shots', 'clips']
    assert group['thumb'] == group['items'][0]['src']
    video = group['items'][1]
    assert video['origin'] == 'scan' and video['size'] == old.stat().st_size
    response = client.get(video['play'], headers={'Range': 'bytes=0-3'})
    assert response.status_code == 206 and response.data == b'proj'
    response.close()
    # A GET never performs directory discovery, even after a new file appears.
    put(root, 'new.png')
    assert client.get('/api/desk/project-media').get_json()['projects'][0]['files'] == 2


@pytest.mark.parametrize('folder', sorted(media.SKIP_DIRS))
def test_scan_skips_dependency_and_worktree_folders(env, folder):
    root = env[0]
    put(root, f'{folder}/hidden.mp4')
    put(root, 'work/visible.mp4')
    assert [i['title'] for i in media.refresh()['items']] == ['visible.mp4']


def test_file_depth_and_entry_caps_are_observable(env, monkeypatch):
    root = env[0]
    logs = []
    monkeypatch.setattr(media, '_log', lambda msg, **kwargs: logs.append(msg))
    for n in range(5):
        put(root, f'{n}.png')
    monkeypatch.setattr(media, 'MAX_FILES', 2)
    assert len(media.refresh()['items']) == 2
    assert any('file cap' in line for line in logs)
    monkeypatch.setattr(media, 'MAX_FILES', 2000)
    monkeypatch.setattr(media, 'MAX_VISITS', 2)
    assert len(media.refresh()['items']) <= 2
    assert any('entry cap' in line for line in logs)
    monkeypatch.setattr(media, 'MAX_VISITS', 20000)
    monkeypatch.setattr(media, 'MAX_DEPTH', 0)
    put(root, 'deeper/hidden.mp4')
    assert not any(i['kind'] == 'video' for i in media.refresh()['items'])
    assert any('depth cap' in line for line in logs)


def test_chat_paths_merge_with_scan_and_attribute_uploads(env, tmp_path):
    root, uploads, media_dir, projects, client = env
    image = put(root, 'made.png')
    upload = put(uploads, 'agent_abc.mp4')
    put(uploads, 'unattributed.png')
    copied = put(uploads, 'desk/library/image/Studio/already.png')
    outside = put(tmp_path, 'outside.mp4')
    media_dir.mkdir()
    rows = [{'path': str(path), 'session_id': 's1', 'agent_name': 'Maker'}
            for path in (image, upload, copied, outside)]
    (media_dir / 'p.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    out = media.refresh()['items']
    assert len(out) == 2
    assert {r['origin'] for r in out} == {'chat', 'upload'}
    assert all(r['session_id'] == 's1' and r['agent_name'] == 'Maker' for r in out)
    # A broad registered root still must not claim unattributed shared uploads.
    projects.append({'id': 'broad', 'name': 'Broad', 'project_path': str(tmp_path)})
    assert not any(i['title'] == 'unattributed.png' for i in media.refresh()['items'])


def test_sources_outside_roots_and_traversal_are_refused(env, tmp_path):
    root, uploads, media_dir, projects, client = env
    put(tmp_path, 'private.png')
    for path in (str(tmp_path / 'private.png'), str(root / '..' / 'private.png'), 'relative.png'):
        with pytest.raises(pieces.PieceError, match='outside|not allowed'):
            media._authorize(path, media._roots(projects))
    assert client.get('/api/desk/project-media/file?id=../../private.png').status_code == 404
    assert client.post('/api/desk/project-media/copy', json={'path': str(tmp_path / 'private.png')}).status_code == 404
    assert client.post('/api/desk/project-media/copy', json=['bad']).status_code == 400


def test_symlink_escape_refused_at_discovery_preview_and_copy(env, tmp_path):
    root, uploads, media_dir, projects, client = env
    outside = put(tmp_path, 'private.png')
    link = root / 'escape.png'
    try:
        link.symlink_to(outside)
    except OSError as e:
        pytest.skip(f'symlink creation unavailable: {e}')
    assert media.refresh()['items'] == []
    source = put(root, 'valid.png')
    row = media.refresh()['items'][0]
    source.unlink()
    source.symlink_to(outside)
    assert media.grouped() == []
    assert client.get('/api/desk/project-media/file?id=' + row['id']).status_code == 403
    assert client.post('/api/desk/project-media/copy', json={'id': row['id']}).status_code == 403


def test_current_project_roots_and_missing_files_are_rechecked(env):
    root, uploads, media_dir, projects, client = env
    file = put(root, 'made.mp4')
    row = media.refresh()['items'][0]
    file.unlink()
    assert client.post('/api/desk/project-media/copy', json={'id': row['id']}).status_code == 404
    put(root, 'made.mp4')
    projects[0]['project_path'] = str(root / 'different')
    assert media.grouped() == []
    assert client.get('/api/desk/project-media/file?id=' + row['id']).status_code == 403
    projects.clear()
    assert client.post('/api/desk/project-media/copy', json={'id': row['id']}).status_code == 404


def test_copy_is_durable_deduplicated_concurrent_and_visible_in_materials(env):
    root, uploads, media_dir, projects, client = env
    source = put(root, 'campaign/final.mp4')
    row = media.refresh()['items'][0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        copies = list(pool.map(media.copy_to_library, [row['id']] * 4))
    assert len({item['path'] for item in copies}) == 1
    item = copies[0]
    assert item['path'].startswith('desk/library/video/Projects/Product/')
    assert item['provenance']['source_path'] == str(source)
    assert item['provenance']['mtime_ns'] == source.stat().st_mtime_ns
    assert (uploads / item['path']).read_bytes() == b'project media'
    materials = pieces.materials()
    assert materials['projects'][0]['project_id'] == 'p'
    assert materials['online'] == {'video': [], 'image': []}
    folder = materials['library']['video'][0]
    assert folder['title'] == 'Projects/Product' and folder['files'] == 1
    assert folder['items'][0]['provenance'] == item['provenance']
    source.unlink()
    assert (uploads / item['path']).read_bytes() == b'project media'
    receipts = json.loads((media_dir / '_desk_project_copies.json').read_text())
    assert list(receipts.values())[0]['provenance'] == item['provenance']


def test_copied_asset_uses_existing_delete_guard_and_survives_source_loss(env):
    from mc import desk_studio_items
    root, uploads, media_dir, projects, client = env
    source = put(root, 'a.png')
    row = media.refresh()['items'][0]
    item = media.copy_to_library(row['id'])
    campaign = desk.create_campaign('Test campaign', 'Test', project_id='p', voiceless_ok=True)
    piece = pieces.create_piece(campaign['id'], 'image')
    attached = pieces.add_asset(piece['id'], path=item['path'])
    assert attached['assets'][0]['path'] == item['path']
    assert attached['assets'][0]['provenance'] == item['provenance']
    assert not Path(attached['assets'][0]['path']).is_absolute()
    with pytest.raises(pieces.PieceError, match='Attached to') as exc:
        desk_studio_items.delete_file(item['path'])
    assert exc.value.status == 409
    source.unlink()
    assert (uploads / pieces.get_piece(piece['id'])['assets'][0]['path']).is_file()


def test_source_change_new_copy_and_deleted_library_copy_recreated(env):
    root, uploads, media_dir, projects, client = env
    source = put(root, 'a.png')
    row = media.refresh()['items'][0]
    one = media.copy_to_library(row['id'])
    source.write_bytes(b'changed and longer')
    two = media.copy_to_library(row['id'])
    assert one['path'] != two['path']
    (uploads / two['path']).unlink()
    three = media.copy_to_library(row['id'])
    assert three['path'] != two['path']
    assert (uploads / three['path']).read_bytes() == b'changed and longer'


def test_copy_obeys_upload_size_cap_and_leaves_no_partial(env, monkeypatch):
    root, uploads, media_dir, projects, client = env
    put(root, 'large.mp4')
    row = media.refresh()['items'][0]
    monkeypatch.setitem(pieces.MAX_UPLOAD_BYTES, 'video', 2)
    response = client.post('/api/desk/project-media/copy', json={'id': row['id']})
    assert response.status_code == 413
    assert list(uploads.rglob('*.mp4')) == []


def test_cache_survives_rewire_without_scanning(env, monkeypatch):
    root, uploads, media_dir, projects, client = env
    put(root, 'a.png')
    media.refresh()
    media.wire(load_projects_fn=lambda: projects, media_dir=media_dir, uploads_root=uploads)
    monkeypatch.setattr(media, '_scan', lambda *args: pytest.fail('GET scanned folders'))
    assert pieces.materials()['projects'][0]['files'] == 1


def test_forward_video_markers_real_files_dedupe_and_agent_name(env, monkeypatch):
    root, uploads, media_dir, projects, client = env
    monkeypatch.setattr(chat_media, 'MEDIA_DIR', media_dir)
    media_dir.mkdir()
    video = put(root, 'campaign clip.MP4')
    assert chat_media.record_from_text('p', 's', f'[file:{video}|Play it]', agent_name='Maker') == 1
    rows = chat_media.list_media('p')
    assert rows[0]['kind'] == 'video' and rows[0]['agent_name'] == 'Maker'
    video.unlink()
    assert chat_media.list_media('p')[0]['missing'] is True
    assert chat_media.record_from_text('p', 's', f'[file:{video}]') == 0
    assert chat_media.extract('https://example.com/x.mp4') == []


def test_chat_newest_attribution_wins_and_byte_tail_is_bounded(env, monkeypatch):
    root, uploads, media_dir, projects, client = env
    source = put(root, 'a.png')
    media_dir.mkdir()
    path = media_dir / 'p.jsonl'
    rows = [{'path': str(source), 'session_id': 'old'}, {'path': str(source), 'session_id': 'new'}]
    path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    assert media.refresh()['items'][0]['session_id'] == 'new'
    logs = []
    monkeypatch.setattr(media, '_log', lambda msg, **kw: logs.append(msg))
    monkeypatch.setattr(media, 'MAX_CHAT_BYTES', len(json.dumps(rows[1])) + 3)
    assert media.refresh()['items'][0]['session_id'] == 'new'
    assert any('chat byte cap' in line for line in logs)


def test_source_changes_during_copy_remove_partial_and_do_not_write_receipt(env, monkeypatch):
    root, uploads, media_dir, projects, client = env
    source = put(root, 'a.png')
    row = media.refresh()['items'][0]
    original = pieces._write_library_file
    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        source.write_bytes(b'changed while copying')
        return result
    monkeypatch.setattr(pieces, '_write_library_file', changing)
    assert client.post('/api/desk/project-media/copy', json={'id': row['id']}).status_code == 409
    assert list(uploads.rglob('*.png')) == []
    assert not (media_dir / '_desk_project_copies.json').exists()


def test_library_destination_symlink_escape_is_refused(env, tmp_path):
    root, uploads, media_dir, projects, client = env
    put(root, 'a.png')
    row = media.refresh()['items'][0]
    outside = tmp_path / 'private'
    outside.mkdir()
    parent = uploads / 'desk/library/image'
    parent.mkdir(parents=True)
    try:
        (parent / 'Projects').symlink_to(outside, target_is_directory=True)
    except OSError as e:
        pytest.skip(f'symlink creation unavailable: {e}')
    assert client.post('/api/desk/project-media/copy', json={'id': row['id']}).status_code == 403
    assert list(outside.iterdir()) == []


def test_worker_retries_after_failure_and_keeps_last_success(env, monkeypatch):
    root = env[0]
    put(root, 'a.png')
    original = media.refresh
    original()
    times = []
    calls = []
    monkeypatch.setattr(media, '_last_error', None)
    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise OSError('test disk offline')
        return original()
    class Wake:
        def clear(self):
            pass
        def wait(self, seconds):
            times.append(seconds)
            if len(times) == 1:
                assert media._last_error and len(media.grouped()) == 1
            else:
                assert media._last_error is None
                raise StopIteration
    monkeypatch.setattr(media, 'refresh', flaky)
    monkeypatch.setattr(media, '_wake', Wake())
    with pytest.raises(StopIteration):
        media._loop()
    assert times == [300, 300] and len(calls) == 2
