"""Host browse/copy scope cannot widen through path spelling, aliases or files."""
import os
import tempfile
from pathlib import Path

import pytest
from flask import Flask

from mc import desk, desk_pieces as pieces, desk_file_explorer as explorer
from mc import desk_library_folders as folders, desk_project_media as project_media
from mc.blueprints import desk_file_explorer_routes, desk_library_folders_routes


def put(root, path, data=b'media'):
    dest = root / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Windows' default pytest temp is under real AppData, now deliberately
    # forbidden even when a project/root is registered inside it.
    repo = Path(__file__).resolve().parents[1]
    for parent in repo.parents:
        if parent.name == '.clayrune':
            repo = parent.parent
            break
    scratch = repo / '_scratch'
    scratch.mkdir(exist_ok=True)
    temporary = tempfile.TemporaryDirectory(dir=scratch)
    tmp_path = Path(temporary.name)
    home = tmp_path / 'home'
    home.mkdir()
    project = tmp_path / 'project'
    project.mkdir()
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    projects = [{'id': 'p', 'name': 'My project', 'project_path': str(project)}]
    monkeypatch.setattr(explorer, '_home', home)
    monkeypatch.setattr(explorer, '_load_projects', lambda: projects)
    monkeypatch.setattr(pieces, 'UPLOADS_ROOT', uploads)
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(project_media, '_media_dir', None)
    monkeypatch.setattr(project_media, '_load_projects', lambda: [])
    monkeypatch.setattr(project_media, '_snapshot', {'items': []})
    app = Flask(__name__)
    app.register_blueprint(desk_file_explorer_routes.bp)
    app.register_blueprint(desk_library_folders_routes.bp)
    try:
        yield home, project, uploads, projects, app.test_client()
    finally:
        temporary.cleanup()


def test_default_media_all_files_and_shortcuts(env):
    home, project, _, _, client = env
    put(home, 'Pictures/photo.png')
    put(home, 'note.txt')
    put(home, 'movie.mp4')
    result = client.get('/api/desk/explorer').get_json()
    assert [r['title'] for r in result['items']] == ['Pictures', 'movie.mp4']
    assert [s['title'] for s in result['shortcuts']] == ['Home', 'Pictures', 'My project']
    assert result['breadcrumbs'] == [{'path': '', 'title': 'Home'}]
    result = client.get('/api/desk/explorer?filter=all').get_json()
    note = next(i for i in result['items'] if i['title'] == 'note.txt')
    assert not note['selectable'] and 'src' not in note
    assert client.get('/api/desk/explorer/preview?root=home&path=note.txt').status_code == 415
    image = client.get('/api/desk/explorer?path=Pictures').get_json()['items'][0]
    response = client.get(image['src'])
    assert response.data == b'media'
    response.close()
    response = client.get(image['src'], headers={'Range': 'bytes=0-2'})
    assert response.status_code == 206 and response.data == b'med'
    response.close()


def test_opened_handle_must_match_current_authorized_file(env, monkeypatch, tmp_path):
    from mc import desk_explorer_file
    put(env[0], 'photo.png')
    outside = put(tmp_path, 'private/photo.png')
    monkeypatch.setattr(desk_explorer_file, '_opened_path', lambda stream: outside)
    response = env[-1].get('/api/desk/explorer/preview?root=home&path=photo.png')
    assert response.status_code == 409
    folder = folders.create('Photos')
    out = folders.copy_files(folder['id'], [{'root': 'home', 'path': 'photo.png'}])
    assert out['items'] == [] and out['errors']


@pytest.mark.parametrize('path', ['../outside', 'Pictures/../../outside', '/etc', 'C:/Windows',
                                  'Pictures/../', 'Pictures\\..\\', 'safe.png:stream', '\x00'])
def test_traversal_absolute_ads_and_outside_root_refused(env, path):
    client = env[-1]
    assert client.get('/api/desk/explorer', query_string={'root': 'home', 'path': path}).status_code == 403


@pytest.mark.parametrize('secret', ['.env', '.env.local', 'test.key', 'test.PEM', 'id_rsa', 'id_rsa.pub',
                                     'my-credentials.png', 'CredentialStore', '.clayrune', '.ssh', '.aws',
                                     'browser_profiles_named', 'User Data', 'Chrome', '.hidden', 'secrets.json'])
def test_secrets_not_listed_searched_previewed_or_copied(env, secret):
    home, _, _, _, client = env
    put(home, f'{secret}/secret.png')
    put(home, 'safe.png')
    rows = client.get('/api/desk/explorer?filter=all&q=secret').get_json()['items']
    assert rows == []
    assert client.get('/api/desk/explorer', query_string={'root': 'home', 'path': secret}).status_code == 403
    assert client.get('/api/desk/explorer/preview', query_string={'root': 'home', 'path': secret + '/secret.png'}).status_code == 403
    folder = folders.create('Photos')
    copied = folders.copy_files(folder['id'], [{'root': 'home', 'path': secret + '/secret.png'}])
    assert copied['items'] == [] and copied['errors']


@pytest.mark.parametrize('secret', ['.env.png', 'credentials.png', 'private.key', 'id_rsa.png'])
def test_secret_file_names_excluded_in_all_files(env, secret):
    put(env[0], secret)
    assert env[-1].get('/api/desk/explorer?filter=all').get_json()['items'] == []


def test_current_project_roots_and_revocation(env):
    _, project, _, projects, client = env
    put(project, 'work.png')
    key = explorer.roots()[1]['id']
    assert explorer.authorize(key, 'work.png')[1] == project / 'work.png'
    projects.clear()
    assert client.get('/api/desk/explorer', query_string={'root': key}).status_code == 403


def test_arbitrarily_named_browser_profile_is_private(env):
    home, _, _, _, client = env
    put(home, 'My browsing/Local State', b'not read')
    put(home, 'My browsing/screenshot.png')
    assert client.get('/api/desk/explorer?filter=all').get_json()['items'] == []
    assert client.get('/api/desk/explorer?root=home&path=My%20browsing').status_code == 403
    assert client.get('/api/desk/explorer/preview?root=home&path=My%20browsing/screenshot.png').status_code == 403


def _link(link, target):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as e:
        pytest.fail(f'Scope guard symlink test could not run: {e}')


def test_symlink_escape_and_secret_alias(env, tmp_path):
    home, _, _, _, client = env
    outside = tmp_path / 'outside'
    put(outside, 'outside.png')
    _link(home / 'escape', outside)
    secret = put(home, '.ssh/key.png')
    _link(home / 'alias.png', secret)
    assert client.get('/api/desk/explorer?filter=all').get_json()['items'] == []
    assert client.get('/api/desk/explorer?root=home&path=escape').status_code == 403
    assert client.get('/api/desk/explorer/preview?root=home&path=alias.png').status_code == 403
    folder = folders.create('Test')
    out = folders.copy_files(folder['id'], [{'root': 'home', 'path': 'escape/outside.png'}])
    assert out['items'] == [] and out['errors']


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction semantics')
def test_windows_junction_escape(env, tmp_path):
    import subprocess
    home, _, _, _, client = env
    outside = tmp_path / 'outside'
    put(outside, 'outside.png')
    link = home / 'junction'
    # Test-owned directory junction, removed without recursive target deletion.
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
    assert result.returncode == 0, result.stderr
    try:
        assert client.get('/api/desk/explorer?root=home&path=junction').status_code == 403
        assert client.get('/api/desk/explorer?filter=all').get_json()['items'] == []
    finally:
        link.rmdir()


def test_recursive_search_and_caps(env, monkeypatch):
    home, _, _, _, client = env
    put(home, 'Pictures/sub/photo.png')
    put(home, 'Other/match.png')
    out = client.get('/api/desk/explorer?path=Pictures&q=photo').get_json()
    assert [i['path'] for i in out['items']] == ['Pictures/sub/photo.png']
    monkeypatch.setattr(explorer, 'MAX_DEPTH', 0)
    out = client.get('/api/desk/explorer?path=Pictures&q=photo').get_json()
    assert out['items'] == [] and out['limited']
    monkeypatch.setattr(explorer, 'MAX_FILES', 1)
    out = client.get('/api/desk/explorer').get_json()
    assert len(out['items']) == 1 and out['limited']


def test_folder_mixed_copy_rename_picker_compatibility_and_provenance(env):
    home, _, uploads, _, client = env
    original = put(home, 'photo.png')
    put(home, 'movie.mp4')
    folder = client.post('/api/desk/library/folders', json={'title': 'My media'}).get_json()
    out = client.post('/api/desk/library/folders/copy', json={'id': folder['id'], 'files': [
        {'root': 'home', 'path': 'photo.png'}, {'root': 'home', 'path': 'movie.mp4'}]}).get_json()
    assert out['errors'] == [] and len(out['items']) == 2
    assert original.exists()
    assert out['items'][0]['provenance']['source_path'] == str(original)
    assert len(folders.list_folders()) == 1 and folders.list_folders()[0]['files'] == 2
    assert {i['title'] for i in folders.list_folders()[0]['items']} == {'photo.png', 'movie.mp4'}
    assert client.patch('/api/desk/library/folders', json={'id': folder['id'], 'title': 'Renamed'}).status_code == 200
    materials = pieces.materials()
    assert materials['library']['image'][0]['title'] == materials['library']['video'][0]['title'] == 'Renamed'
    assert materials['library']['image'][0]['items'][0]['provenance']['source_path'] == str(original)
    original.unlink()
    assert all((uploads / i['path']).read_bytes() == b'media' for i in out['items'])
    assert len(folders.list_folders()[0]['items']) == 2


def test_legacy_folders_stay_listed_and_paths_unchanged(env):
    uploads = env[2]
    image = put(uploads, 'desk/library/image/Uploads/old.png')
    video = put(uploads, 'desk/library/video/Uploads/old.mp4')
    project = put(uploads, 'desk/library/image/Projects/Other/project.png')
    assert {f['id'] for f in folders.list_folders()} == {'Uploads', 'Projects/Other'}
    folders.rename('Uploads', 'Old media')
    assert image.exists() and video.exists() and project.exists()
    assert next(f for f in folders.list_folders() if f['id'] == 'Uploads')['files'] == 2


@pytest.mark.parametrize('store', [
    {'pieces': {'p': {'assets': [{'path': 'desk/library/image/Uploads/a.png'}]}}},
    {'storyboards': {'studio:s': {'scenes': [{'picture': {'path': 'desk/library/image/Uploads/a.png'}}]}}},
    {'studio_articles': {'a': {'tabs': [{'body': '![Photo](desk/library/image/Uploads/a.png)'}]}}},
])
def test_folder_delete_refuses_referenced_media(env, store):
    put(env[2], 'desk/library/image/Uploads/a.png')
    desk._write_store(store)
    response = env[-1].delete('/api/desk/library/folders', json={'id': 'Uploads'})
    assert response.status_code == 409
    assert folders.list_folders()[0]['files'] == 1


def test_delete_moves_to_recoverable_trash_and_keeps_original(env):
    home, _, uploads, _, client = env
    original = put(home, 'photo.png')
    folder = folders.create('Photos')
    out = folders.copy_files(folder['id'], [{'root': 'home', 'path': 'photo.png'}])
    assert client.delete('/api/desk/library/folders', json={'id': folder['id']}).status_code == 200
    assert folders.list_folders() == [] and original.exists()
    assert not (uploads / out['items'][0]['path']).exists()
    assert len(list((uploads / 'desk/_folder_trash').glob('*/image/*'))) == 1


def test_deleted_legacy_folder_can_receive_future_uploads(env):
    put(env[2], 'desk/library/image/Studio/old.png')
    folders.delete('Studio')
    assert folders.list_folders() == []
    put(env[2], 'desk/library/image/Studio/new.png')
    assert folders.list_folders()[0]['title'] == 'Studio'


def test_copy_limits_partial_errors_and_bad_bodies(env, monkeypatch):
    home, _, _, _, client = env
    put(home, 'photo.png')
    put(home, 'empty.png', b'')
    put(home, 'note.txt')
    folder = folders.create('Photos')
    out = folders.copy_files(folder['id'], [{'root': 'home', 'path': p} for p in ('photo.png', 'empty.png', 'note.txt')])
    assert len(out['items']) == 1 and len(out['errors']) == 2
    assert out['errors'][0]['root'] == 'home' and out['errors'][0]['path'] == 'empty.png'
    monkeypatch.setitem(pieces.MAX_UPLOAD_BYTES, 'image', 1)
    assert folders.copy_files(folder['id'], [{'root': 'home', 'path': 'photo.png'}])['errors']
    assert client.post('/api/desk/library/folders/copy', json={'id': folder['id'], 'files': []}).status_code == 400
    for method in ('post', 'patch', 'delete'):
        assert getattr(client, method)('/api/desk/library/folders', json=[]).status_code == 400
    assert client.post('/api/desk/library/folders', json={'title': 'Photos'}).status_code == 409


def test_library_destination_link_escape_refused(env, tmp_path):
    folder = folders.create('Photos')
    put(env[0], 'photo.png')
    outside = tmp_path / 'outside'
    outside.mkdir()
    parent = env[2] / 'desk/library/image'
    parent.mkdir(parents=True)
    _link(parent / folder['id'], outside)
    response = env[-1].post('/api/desk/library/folders/copy', json={
        'id': folder['id'], 'files': [{'root': 'home', 'path': 'photo.png'}]})
    assert response.status_code == 403
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('query,expected', [
    ('mp4', ['a.mp4', 'b.MP4', 'x.mp4.png']),         # substring, case-insensitive
    ('*.mp4', ['a.mp4', 'b.MP4']),                     # glob is a whole-name match
    ('*.MP4', ['a.mp4', 'b.MP4']),
    ('?.mp4', ['a.mp4', 'b.MP4']),
    ('a.*', ['a.mp4']),
    ('*.avi', ['c.avi']),
    ('*.mov', []),
])
def test_wildcard_query_is_a_glob_and_plain_query_is_a_substring(env, query, expected):
    home, _, _, _, client = env
    for name in ('a.mp4', 'b.MP4', 'x.mp4.png', 'c.avi', 'note.txt'):
        put(home, f'Videos/{name}')
    out = client.get('/api/desk/explorer', query_string={'path': 'Videos', 'q': query}).get_json()
    assert sorted(i['title'] for i in out['items']) == sorted(expected)


def test_glob_matches_in_subfolders_and_guards_still_apply(env):
    home, _, _, _, client = env
    put(home, 'Videos/deep/er/clip.mp4')
    put(home, '.hidden/secret.mp4')
    put(home, 'node_modules/pkg/leak.mp4')
    out = client.get('/api/desk/explorer?q=*.mp4').get_json()
    assert [i['path'] for i in out['items']] == ['Videos/deep/er/clip.mp4']


def test_bracket_in_query_is_literal(env):
    home, _, _, _, client = env
    put(home, 'Videos/a[1].mp4')
    put(home, 'Videos/a1.mp4')
    out = client.get('/api/desk/explorer', query_string={'path': 'Videos', 'q': 'a[1]*'}).get_json()
    assert [i['title'] for i in out['items']] == ['a[1].mp4']


def test_breadth_first_finds_shallow_matches_in_tree_wider_than_budget(env, monkeypatch):
    home, _, _, _, client = env
    # Each d<n> holds a shallow match (d<n>/s/m.mp4) and a wide, deep decoy
    # subtree (d<n>/w/<a>/<b>/f.txt). A depth-first walk burns the whole budget
    # inside one decoy; breadth-first reaches every depth-2 match first.
    for n in range(4):
        put(home, f'd{n}/s/m.mp4')
        for a in range(5):
            for b in range(5):
                put(home, f'd{n}/w/{a}/{b}/f.txt')
    monkeypatch.setattr(explorer, 'MAX_VISITS', 40)
    out = client.get('/api/desk/explorer?q=mp4').get_json()
    assert sorted(i['path'] for i in out['items']) == [f'd{n}/s/m.mp4' for n in range(4)]
    assert out['truncated']


def test_search_scans_folders_shallowest_first(env, monkeypatch):
    home, _, _, _, client = env
    for path in ('a/b/c/x.png', 'a/b/y.png', 'a/z.png', 'q/r/s.png', 'top.png'):
        put(home, path)
    seen = []
    real = explorer.authorize
    def record(root_id, relative='', **kw):
        if relative in {'.', 'a', 'q', 'a/b', 'q/r', 'a/b/c'}:  # the scan loop, not shortcuts()
            seen.append(0 if relative == '.' else len(Path(relative).parts))
        return real(root_id, relative, **kw)
    monkeypatch.setattr(explorer, 'authorize', record)
    client.get('/api/desk/explorer?q=png')
    assert seen and seen == sorted(seen)


def test_refused_entries_do_not_spend_the_visit_budget(env, monkeypatch):
    home, _, _, _, client = env
    for n in range(30):
        put(home, f'.dot{n}/x.png')
    put(home, 'wanted.png')
    monkeypatch.setattr(explorer, 'MAX_VISITS', 3)
    out = client.get('/api/desk/explorer?q=wanted').get_json()
    assert [i['path'] for i in out['items']] == ['wanted.png']


def test_display_path_is_the_authorized_host_folder(env):
    home, project, _, _, client = env
    put(home, 'Videos/clips/a.mp4')
    here = client.get('/api/desk/explorer').get_json()
    assert here['display_path'] == str(home.resolve())
    nested = client.get('/api/desk/explorer?path=Videos/clips').get_json()
    assert nested['display_path'] == str((home / 'Videos' / 'clips').resolve())
    # Search mode names the folder being searched, not where each match lives.
    found = client.get('/api/desk/explorer?path=Videos&q=a.mp4').get_json()
    assert found['display_path'] == str((home / 'Videos').resolve())
    assert [i['title'] for i in found['items']] == ['a.mp4']
    # A project root reports the project's own folder.
    root = next(s for s in here['shortcuts'] if s['title'] == 'My project')
    proj = client.get(f"/api/desk/explorer?root={root['root']}").get_json()
    assert proj['display_path'] == str(project.resolve())


@pytest.mark.parametrize('path', ['../outside', 'Videos/../../outside', '/etc', 'C:/Windows'])
def test_display_path_never_appears_in_refusals(env, path):
    home, _, _, _, client = env
    response = client.get('/api/desk/explorer', query_string={'path': path})
    assert response.status_code in (403, 404)
    body = response.get_data(as_text=True)
    assert 'display_path' not in body and str(home) not in body
