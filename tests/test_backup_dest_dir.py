"""Tests for the configurable backup destination (MC-945 follow-up):
mc/backup.py's dest_dir plumbing through create_backup/list_backups, the
repo/DATA_DIR refusal in validate_backup_dest_dir, and the two routes that
carry it (POST /api/backup/create, GET /api/backup/list) plus the new
GET /api/backup/dest-dir and the PUT /api/config validation gate.

Same fake-install isolation idiom as tests/test_backup.py: env vars point
MC_DATA_DIR / HOME / CLAYRUNE_HOME at a temp tree. state.CONFIG is a live
shared dict (mc/blueprints/settings_routes.py's pattern), so every test that
sets backup_dest_dir restores it via monkeypatch.setattr — never a bare
assignment that would leak into other tests in the same process.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import backup as bk  # noqa: E402
from mc import state as _state  # noqa: E402


@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    root = tmp_path / 'repo'
    home = tmp_path / 'home'
    clayrune = tmp_path / 'clayrune'
    (root / 'data' / 'projects').mkdir(parents=True)
    (home / '.claude' / 'agents').mkdir(parents=True)
    monkeypatch.setenv('MC_DATA_DIR', str(root))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('CLAYRUNE_HOME', str(clayrune))
    monkeypatch.setattr(_state, 'CONFIG', {})
    # REPO_ROOT is a fixed module constant (Path(__file__)'s real checkout),
    # not reactive to MC_DATA_DIR — fake it the same way test_backup.py does
    # (test_own_data_dir_not_double_swept_as_unprotected) so the "inside the
    # repo" refusal is exercisable against this fake install, not the real one.
    monkeypatch.setattr(bk, 'REPO_ROOT', root)
    return {'root': root, 'home': home, 'clayrune': clayrune}


@pytest.fixture
def fake_install_split_data_dir(tmp_path, monkeypatch):
    """DATA_DIR redirected OUTSIDE the repo checkout via MC_DATA_DIR — an
    advanced-deployment shape where data/projects/ is not a subdirectory of
    REPO_ROOT, so the two refusal rules (repo root vs data/projects) are
    independently reachable instead of the repo-root check always firing
    first (it's the broader match whenever data_dir nests under repo_root,
    which it does by default — see fake_install above)."""
    data_root = tmp_path / 'data-root'
    fake_repo = tmp_path / 'unrelated-repo-checkout'
    home = tmp_path / 'home'
    clayrune = tmp_path / 'clayrune'
    (data_root / 'data' / 'projects').mkdir(parents=True)
    fake_repo.mkdir(parents=True)
    (home / '.claude' / 'agents').mkdir(parents=True)
    monkeypatch.setenv('MC_DATA_DIR', str(data_root))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('CLAYRUNE_HOME', str(clayrune))
    monkeypatch.setattr(_state, 'CONFIG', {})
    monkeypatch.setattr(bk, 'REPO_ROOT', fake_repo)
    return {'data_root': data_root, 'fake_repo': fake_repo, 'home': home, 'clayrune': clayrune}


# ── validate_backup_dest_dir ─────────────────────────────────────────────────

def test_validate_refuses_repo_root(fake_install):
    with pytest.raises(bk.BackupError, match='inside the repo'):
        bk.validate_backup_dest_dir(fake_install['root'])


def test_validate_refuses_repo_subdir(fake_install):
    sub = fake_install['root'] / 'some' / 'nested' / 'dir'
    with pytest.raises(bk.BackupError, match='inside the repo'):
        bk.validate_backup_dest_dir(sub)


def test_validate_refuses_data_projects_nested_under_repo_via_repo_rule(fake_install):
    """The common case: data/projects/ lives under the repo, so the broader
    repo-root refusal fires first — still refused, just via the other rule."""
    with pytest.raises(bk.BackupError, match='inside the repo'):
        bk.validate_backup_dest_dir(fake_install['root'] / 'data' / 'projects')


def test_validate_refuses_data_projects(fake_install_split_data_dir):
    with pytest.raises(bk.BackupError, match='data/projects'):
        bk.validate_backup_dest_dir(fake_install_split_data_dir['data_root'] / 'data' / 'projects')


def test_validate_refuses_data_projects_subdir(fake_install_split_data_dir):
    with pytest.raises(bk.BackupError, match='data/projects'):
        bk.validate_backup_dest_dir(
            fake_install_split_data_dir['data_root'] / 'data' / 'projects' / 'nested')


def test_validate_accepts_external_dir(fake_install, tmp_path):
    external = tmp_path / 'external-drive' / 'clayrune-backups'
    resolved = bk.validate_backup_dest_dir(external)
    assert resolved == external.resolve()


# ── create_backup / list_backups honour dest_dir ────────────────────────────

def test_create_backup_writes_to_explicit_dest_dir(fake_install, tmp_path):
    dest = tmp_path / 'external-drive' / 'backups'
    result = bk.create_backup(categories={'records': True}, dest_dir=dest)
    written = Path(result['path'])
    assert written.parent == dest.resolve()
    assert written.exists()
    # Default location (~/.clayrune/backups) got nothing.
    assert not (fake_install['clayrune'] / 'backups').exists()


def test_create_backup_refuses_dest_dir_inside_repo(fake_install):
    with pytest.raises(bk.BackupError, match='inside the repo'):
        bk.create_backup(categories={'records': True}, dest_dir=fake_install['root'])


def test_list_backups_reads_explicit_dest_dir(fake_install, tmp_path):
    dest = tmp_path / 'external-drive' / 'backups'
    bk.create_backup(categories={'records': True}, dest_dir=dest, label='x')
    items = bk.list_backups(dest_dir=dest)
    assert len(items) == 1
    assert items[0]['path'] == str(sorted(dest.glob('*.crbackup'))[0])
    # Default location still lists nothing.
    assert bk.list_backups() == []


def test_list_backups_refuses_dest_dir_inside_data_projects(fake_install_split_data_dir):
    with pytest.raises(bk.BackupError, match='data/projects'):
        bk.list_backups(dest_dir=fake_install_split_data_dir['data_root'] / 'data' / 'projects')


# ── configured default (state.CONFIG['backup_dest_dir']) ───────────────────

def test_paths_backup_dir_honours_config(fake_install, tmp_path, monkeypatch):
    custom = tmp_path / 'my-nas' / 'clayrune-backups'
    monkeypatch.setattr(_state, 'CONFIG', {'backup_dest_dir': str(custom)})
    assert bk._paths()['backup_dir'] == custom


def test_paths_backup_dir_falls_back_when_unset(fake_install):
    assert bk._paths()['backup_dir'] == fake_install['clayrune'] / 'backups'


def test_create_backup_uses_configured_default(fake_install, tmp_path, monkeypatch):
    custom = tmp_path / 'my-nas' / 'clayrune-backups'
    monkeypatch.setattr(_state, 'CONFIG', {'backup_dest_dir': str(custom)})
    result = bk.create_backup(categories={'records': True})
    assert Path(result['path']).parent == custom.resolve()


def test_effective_backup_dir_helpers(fake_install, tmp_path, monkeypatch):
    assert bk.effective_backup_dir_config() is None
    assert bk.effective_backup_dir() == str(fake_install['clayrune'] / 'backups')
    custom = tmp_path / 'my-nas' / 'clayrune-backups'
    monkeypatch.setattr(_state, 'CONFIG', {'backup_dest_dir': str(custom)})
    assert bk.effective_backup_dir_config() == str(custom)
    assert bk.effective_backup_dir() == str(custom)


# ── routes: POST /api/backup/create, GET /api/backup/list, GET .../dest-dir ─

@pytest.fixture
def client():
    from mc.blueprints import backup_routes
    app = Flask(__name__)
    app.register_blueprint(backup_routes.bp)
    return app.test_client()


def test_route_create_accepts_dest_dir(fake_install, tmp_path, client):
    dest = tmp_path / 'external-drive' / 'backups'
    resp = client.post('/api/backup/create', json={
        'categories': {'records': True}, 'dest_dir': str(dest)})
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert Path(data['path']).parent == dest.resolve()


def test_route_create_refuses_dest_dir_inside_repo(fake_install, client):
    resp = client.post('/api/backup/create', json={
        'categories': {'records': True}, 'dest_dir': str(fake_install['root'])})
    assert resp.status_code == 400
    assert 'inside the repo' in resp.get_json()['error']


def test_route_list_accepts_dest_dir_query_arg(fake_install, tmp_path, client):
    dest = tmp_path / 'external-drive' / 'backups'
    bk.create_backup(categories={'records': True}, dest_dir=dest)
    resp = client.get('/api/backup/list', query_string={'dest_dir': str(dest)})
    assert resp.status_code == 200
    assert len(resp.get_json()['backups']) == 1


def test_route_list_refuses_dest_dir_inside_data_projects(fake_install_split_data_dir, client):
    resp = client.get('/api/backup/list', query_string={
        'dest_dir': str(fake_install_split_data_dir['data_root'] / 'data' / 'projects')})
    assert resp.status_code == 400
    assert 'data/projects' in resp.get_json()['error']


def test_route_dest_dir_reports_configured_and_effective(fake_install, tmp_path, monkeypatch, client):
    resp = client.get('/api/backup/dest-dir')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['configured'] is None
    assert data['effective'] == str(fake_install['clayrune'] / 'backups')

    custom = tmp_path / 'my-nas' / 'clayrune-backups'
    monkeypatch.setattr(_state, 'CONFIG', {'backup_dest_dir': str(custom)})
    resp = client.get('/api/backup/dest-dir')
    data = resp.get_json()
    assert data['configured'] == str(custom)
    assert data['effective'] == str(custom)


# ── PUT /api/config validates backup_dest_dir before persisting ────────────

@pytest.fixture
def config_client(fake_install, tmp_path, monkeypatch):
    from mc.blueprints import settings_routes
    app = Flask(__name__)
    app.register_blueprint(settings_routes.bp)
    settings_routes.wire(
        config_path=tmp_path / 'config.json',
        projects_base=tmp_path / 'projects',
        settings_path=tmp_path / 'settings.json',
    )
    return app.test_client()


def test_config_put_refuses_bad_backup_dest_dir(fake_install, config_client):
    resp = config_client.put('/api/config', json={'backup_dest_dir': str(fake_install['root'])})
    assert resp.status_code == 400
    assert 'inside the repo' in resp.get_json()['error']
    assert 'backup_dest_dir' not in _state.CONFIG


def test_config_put_accepts_good_backup_dest_dir(fake_install, tmp_path, config_client):
    custom = tmp_path / 'my-nas' / 'clayrune-backups'
    resp = config_client.put('/api/config', json={'backup_dest_dir': str(custom)})
    assert resp.status_code == 200
    assert resp.get_json()['updated'] == ['backup_dest_dir']
    assert _state.CONFIG['backup_dest_dir'] == str(custom)
