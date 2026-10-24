"""MC-1111 audit regressions F1-F6; no real user files or server required."""
import os
import stat
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from mc import desk_explorer_copy as transfer, desk_explorer_guard as guard
from mc import desk_explorer_search as search, desk_file_explorer as explorer
from mc import desk_library_folders as folders, desk_pieces as pieces
from tests.test_desk_file_explorer import env, put  # noqa: F401 -- shared isolated fixture


@pytest.mark.parametrize('name', ['AppData', 'APPLICATION DATA', 'Local Settings', 'ProgramData',
                                  'NetHood', 'PrintHood', 'Recent', 'SendTo', 'Start Menu',
                                  'Templates', 'Cookies', 'Library'])
def test_private_os_names_block_listing_search_preview_copy(env, name):
    home, _, _, _, client = env
    put(home, name + '/cached.png')
    assert client.get('/api/desk/explorer', query_string={'filter': 'all', 'q': 'cached'}).get_json()['items'] == []
    assert client.get('/api/desk/explorer', query_string={'path': name}).status_code == 403
    assert client.get('/api/desk/explorer/preview', query_string={'root': 'home', 'path': name + '/cached.png'}).status_code == 403
    folder = folders.create('Media')
    result = folders.copy_files(folder['id'], [{'root': 'home', 'path': name + '/cached.png'}])
    assert not result['items'] and len(result['errors']) == 1


@pytest.mark.skipif(os.name != 'nt', reason='Real Windows directory junction regression')
@pytest.mark.parametrize('name', ['Local Settings', 'Application Data', 'Pictures-alias'])
def test_in_home_appdata_junction_is_not_an_alias_escape(env, name):
    home, _, _, _, client = env
    target = put(home, 'AppData/Local/Cache/photo.png').parent
    link = home / name
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
    assert result.returncode == 0, result.stderr
    try:
        assert link.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
        assert client.get('/api/desk/explorer', query_string={'path': name}).status_code == 403
        assert client.get('/api/desk/explorer?filter=all&q=photo').get_json()['items'] == []
        assert client.get('/api/desk/explorer/preview', query_string={'root': 'home', 'path': name + '/photo.png'}).status_code == 403
        folder = folders.create('Media')
        assert not folders.copy_files(folder['id'], [{'root': 'home', 'path': name + '/photo.png'}])['items']
    finally:
        link.rmdir()  # Remove ONLY our junction, never its target tree.


@pytest.mark.skipif(os.name != 'nt', reason='Real Windows junction above registered root')
def test_registered_root_cannot_erase_reparse_ancestor(env):
    home, project, _, projects, client = env
    put(project, 'nested/photo.png')
    link = home / 'linked-project'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(project)], capture_output=True)
    assert result.returncode == 0, result.stderr
    try:
        projects[0]['project_path'] = str(link / 'nested')
        root = explorer.roots()[1]['id']
        assert client.get('/api/desk/explorer', query_string={'root': root}).status_code == 403
    finally:
        link.rmdir()


@pytest.mark.skipif(os.name != 'nt', reason='Real in-root Windows junction')
def test_reparse_itself_is_refused_even_when_target_is_public(env):
    home, _, _, _, client = env
    target = put(home, 'Pictures/Cache/photo.png').parent
    link = home / 'public-alias'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
    assert result.returncode == 0, result.stderr
    try:
        assert client.get('/api/desk/explorer?path=public-alias').status_code == 403
        assert client.get('/api/desk/explorer/preview?root=home&path=public-alias/photo.png').status_code == 403
        assert 'public-alias' not in [r['title'] for r in client.get('/api/desk/explorer').get_json()['items']]
        folder = folders.create('Media')
        assert not folders.copy_files(folder['id'], [{'root': 'home', 'path': 'public-alias/photo.png'}])['items']
    finally:
        link.rmdir()


def test_mac_hidden_flag_is_refused_without_windows_attributes(env, monkeypatch):
    home = env[0]
    hidden = put(home, 'Private/photo.png').parent
    lstat = Path.lstat
    def info(path, *args, **kwargs):
        if path == hidden:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_flags=stat.UF_HIDDEN)
        return lstat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', info)
    assert env[-1].get('/api/desk/explorer?path=Private').status_code == 403


@pytest.mark.parametrize('marker', ['cookies.sqlite', 'key4.db', 'logins.json'])
def test_firefox_arbitrary_profile_markers_block_descendants(env, marker):
    home, _, _, _, client = env
    put(home, 'Unrelated name/' + marker)
    put(home, 'Unrelated name/storage/cache.png')
    assert client.get('/api/desk/explorer?filter=all&q=cache').get_json()['items'] == []
    assert client.get('/api/desk/explorer?path=Unrelated%20name/storage').status_code == 403
    assert client.get('/api/desk/explorer/preview?root=home&path=Unrelated%20name/storage/cache.png').status_code == 403
    folder = folders.create('Media')
    assert not folders.copy_files(folder['id'], [{'root': 'home', 'path': 'Unrelated name/storage/cache.png'}])['items']


def test_search_visit_and_time_budgets_and_cancel(env, monkeypatch):
    for n in range(6):
        put(env[0], f'photos/{n}.png')
    monkeypatch.setattr(explorer, 'MAX_VISITS', 2)
    out = explorer.browse(relative='photos', query='.png')
    assert len(out['items']) == 2 and out['truncated'] and out['limited']
    out = explorer.browse(relative='photos', query='.png', deadline=0)
    assert out['items'] == [] and out['truncated']


def test_search_checks_elapsed_time_during_scan_not_only_at_start(env, monkeypatch):
    home = env[0]
    for n in range(10):
        put(home, f'photos/{n}.png')
    clock = [0.0]
    check = guard.check_node
    def node(path):
        if path.parent == home / 'photos':
            clock[0] += 0.51
        return check(path)
    monkeypatch.setattr(guard, 'check_node', node)
    monkeypatch.setattr(explorer, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    out = explorer.browse(relative='photos', query='.png')
    assert out['truncated'] and 0 < len(out['items']) < 10
    out = explorer.browse(relative='photos', query='.png', stopped=lambda: True)
    assert out['items'] == [] and out['truncated']


def test_newest_client_request_cancels_and_waits_for_previous_route(env, monkeypatch):
    entered, cancelled = threading.Event(), threading.Event()
    active, maximum = 0, 0
    lock = threading.Lock()
    def browse(*args, **kwargs):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
        try:
            if not entered.is_set():
                entered.set()
                while not kwargs['stopped']():
                    assert not cancelled.wait(0.005)
                cancelled.set()
            else:
                assert cancelled.is_set()
            return {'items': [], 'truncated': True}
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(explorer, 'browse', browse)
    app = env[-1].application
    def get(number):
        return app.test_client().get(f'/api/desk/explorer?client=tab&search={number}&q=a')
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(get, 1)
        assert entered.wait(1)
        second = pool.submit(get, 2)
        assert first.result(timeout=3).status_code == second.result(timeout=3).status_code == 200
    assert cancelled.is_set() and maximum == 1 and not search._slots


def test_cancel_is_targeted_and_clients_are_independent(env):
    with search.flight('tab1', '2') as a, search.flight('tab2', '1') as b:
        search.cancel('tab1', '1')
        assert not a.cancel.is_set()
        search.cancel('tab1', '2')
        assert a.cancel.is_set() and not b.cancel.is_set()
    assert not search._slots
    app = env[-1].application
    with app.test_request_context('/api/desk/explorer?client=tab&search=3', environ_base={'REMOTE_ADDR': '127.0.0.1'}):
        from mc.blueprints.desk_file_explorer_routes import _client
        key = _client()
    with search.flight(key, '3') as lease:
        response = env[-1].delete('/api/desk/explorer/search?client=tab&search=3')
        assert response.status_code == 200 and lease.cancel.is_set()


def test_search_registry_capacity_and_wait_deadline(monkeypatch):
    monkeypatch.setattr(search, 'MAX_CLIENTS', 1)
    with search.flight('tab'):
        with pytest.raises(pieces.PieceError) as caught:
            with search.flight('other'):
                pytest.fail('Capacity bypass')
        assert caught.value.status == 429
        monkeypatch.setattr(search, 'MAX_SECONDS', 0.01)
        with search.flight('tab') as waiting:
            assert waiting.stopped() and not waiting.acquired
    assert not search._slots


@pytest.mark.parametrize('remote,headers', [('192.168.1.20', {}), ('127.0.0.1', {'Cf-Ray': 'test'}),
                                           ('127.0.0.1', {'Cf-Access-Jwt-Assertion': 'test'}),
                                           ('::1', {'Cf-Connecting-Ip': '203.0.113.1'})])
def test_all_files_refused_for_remote_even_through_loopback_tunnel(env, remote, headers):
    put(env[0], 'private-document.txt')
    client = env[-1]
    opts = {'environ_overrides': {'REMOTE_ADDR': remote}, 'headers': headers}
    assert client.get('/api/desk/explorer?filter=all&q=private', **opts).status_code == 403
    out = client.get('/api/desk/explorer?q=private', **opts).get_json()
    assert out['items'] == [] and out['allow_all_files'] is False


def test_copy_aggregate_preflight_and_free_space_leave_no_payload(env, monkeypatch):
    home, _, uploads, _, client = env
    put(home, 'one.png')
    put(home, 'two.png')
    folder = folders.create('Media')
    body = {'id': folder['id'], 'files': [{'root': 'home', 'path': p} for p in ['one.png', 'two.png']]}
    monkeypatch.setattr(transfer, 'MAX_TOTAL_BYTES', 8)
    assert client.post('/api/desk/library/folders/copy', json=body).status_code == 413
    assert not (uploads / 'desk/_explorer_staging').exists()
    monkeypatch.setattr(transfer, 'MAX_TOTAL_BYTES', 100)
    monkeypatch.setattr(transfer.shutil, 'disk_usage', lambda path: SimpleNamespace(free=transfer.MIN_FREE_BYTES + 9))
    assert client.post('/api/desk/library/folders/copy', json=body).status_code == 507
    assert not (uploads / 'desk/_explorer_staging').exists()
    assert folders.list_folders()[0]['files'] == 0 and transfer._reserved == 0


def test_concurrent_copy_reservations_do_not_double_allocate(env, monkeypatch):
    monkeypatch.setattr(transfer.shutil, 'disk_usage', lambda path: SimpleNamespace(free=transfer.MIN_FREE_BYTES + 10))
    with transfer.reserve_space(6):
        with pytest.raises(pieces.PieceError) as caught:
            with transfer.reserve_space(6):
                pytest.fail('Space was double allocated')
        assert caught.value.status == 507
    assert transfer._reserved == 0
    with transfer.reserve_space(10):
        pass


def test_copy_transfer_releases_library_lock_and_delete_wins(env, monkeypatch):
    put(env[0], 'photo.png')
    folder = folders.create('Media')
    original = transfer._stage
    def stage(*args):
        # A DIFFERENT thread can list/rename/delete while bytes are transferred.
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(folders.rename, folder['id'], 'Renamed').result(timeout=1)['title'] == 'Renamed'
            assert pool.submit(folders.delete, folder['id']).result(timeout=1)['deleted']
        original(*args)
    monkeypatch.setattr(transfer, '_stage', stage)
    out = folders.copy_files(folder['id'], [{'root': 'home', 'path': 'photo.png'}])
    assert not out['items'] and len(out['errors']) == 1
    assert not folders.list_folders()
    assert not list((env[2] / 'desk/_explorer_staging').iterdir())
    assert not list((env[2] / 'desk/library').rglob('*.png'))
    assert transfer._reserved == 0


@pytest.mark.parametrize('data,size', [(b'growth', 3), (b'short', 9)])
def test_source_growth_or_shrink_is_bounded(env, data, size):
    with pytest.raises(pieces.PieceError) as caught:
        transfer._stage(BytesIO(data), env[2] / 'test.png', size)
    assert caught.value.status == 409
    assert (env[2] / 'test.png').stat().st_size <= size


def test_free_space_lost_mid_transfer_cleans_up(env, monkeypatch):
    put(env[0], 'photo.png')
    folder = folders.create('Media')
    calls = 0
    def free(path):
        nonlocal calls
        calls += 1
        return SimpleNamespace(free=transfer.MIN_FREE_BYTES + (100 if calls == 1 else 0))
    monkeypatch.setattr(transfer.shutil, 'disk_usage', free)
    out = folders.copy_files(folder['id'], [{'root': 'home', 'path': 'photo.png'}])
    assert not out['items'] and out['errors']
    assert folders.list_folders()[0]['files'] == 0 and transfer._reserved == 0
    assert not list((env[2] / 'desk/_explorer_staging').iterdir())
