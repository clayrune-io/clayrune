"""Tests for the Backup panel's second round of fixes:

  * size_preview() returns a per-directory breakdown for EVERY category, not
    just 'unprotected' (the panel renders one disclosure component for all of
    them, so every category has to supply the same shape).
  * POST /api/backup/create {"async": true} hands back a job id immediately
    and reports real progress while a worker thread writes the archive — the
    synchronous contract the CLI and tests/test_backup_dest_dir.py rely on
    stays exactly as it was.
  * The .partial temp file is unique per run (it used to be a pure function
    of the category set, so two concurrent creates collided — measured on
    this install as [WinError 32] after ~15.6GB), is removed when a write
    fails, and stale ones get swept.

Same fake-install isolation idiom as tests/test_backup.py.
"""
from __future__ import annotations

import json
import os
import sys
import time
import zipfile
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
    (root / 'data' / 'uploads').mkdir(parents=True)
    (root / 'data' / 'media').mkdir(parents=True)
    (home / '.claude' / 'agents').mkdir(parents=True)
    monkeypatch.setenv('MC_DATA_DIR', str(root))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('CLAYRUNE_HOME', str(clayrune))
    monkeypatch.setattr(_state, 'CONFIG', {})
    monkeypatch.setattr(bk, 'REPO_ROOT', root)

    work = tmp_path / 'work'
    work.mkdir()
    (work / 'notes.txt').write_text('unprotected work', encoding='utf-8')
    (root / 'data' / 'projects' / 'proj_a.json').write_text(
        json.dumps({'id': 'proj_a', 'project_path': str(work), 'backlog': []}), encoding='utf-8')
    (root / 'config.json').write_text(json.dumps({'k': 'v'}), encoding='utf-8')
    (root / 'data' / 'uploads' / 'photo.png').write_bytes(b'fake png payload')
    (root / 'data' / 'media' / 'clip.bin').write_bytes(b'media bytes')

    encoded = bk._encode_project_path(str(work))
    mem_dir = home / '.claude' / 'projects' / encoded / 'memory'
    mem_dir.mkdir(parents=True)
    (mem_dir / 'MEMORY.md').write_text('# memory\n', encoding='utf-8')
    (home / '.claude' / 'projects' / encoded / 'session1.jsonl').write_text(
        '{"role":"user"}\n', encoding='utf-8')
    (home / '.claude' / 'agents' / 'char.md').write_text('a character', encoding='utf-8')
    return {'root': root, 'home': home, 'clayrune': clayrune, 'work': work}


# ── size_preview: every category gets a breakdown ───────────────────────────

def test_every_category_returns_a_directory_breakdown(fake_install):
    preview = bk.size_preview()
    for name in ('records', 'artifacts', 'media', 'transcripts', 'unprotected'):
        cat = preview['categories'][name]
        assert 'directories' in cat, f'{name} has no breakdown'
        assert cat['directories'], f'{name} breakdown is empty'
        for line in cat['directories']:
            assert line['path'] and line['bytes'] >= 0 and line['files'] >= 1


def test_breakdown_rows_sum_to_the_category_total(fake_install):
    preview = bk.size_preview()
    for name in ('records', 'artifacts', 'media', 'transcripts'):
        cat = preview['categories'][name]
        assert sum(l['bytes'] for l in cat['directories']) == cat['bytes']
        assert sum(l['files'] for l in cat['directories']) == cat['files']


def test_breakdown_paths_are_real_directories(fake_install):
    """The rows are what the panel prints, so they have to be paths a human
    can act on — the same contract 'unprotected' already had."""
    preview = bk.size_preview()
    paths = [l['path'] for l in preview['categories']['records']['directories']]
    assert str(fake_install['root'] / 'data' / 'projects') in paths
    assert any(Path(p).is_dir() for p in paths)
    media = preview['categories']['media']['directories']
    assert media[0]['path'] == str(fake_install['root'] / 'data' / 'uploads')


def test_breakdown_is_sorted_largest_first(fake_install):
    dirs = bk.size_preview()['categories']['records']['directories']
    assert dirs == sorted(dirs, key=lambda l: -l['bytes'])


def test_disabled_category_still_carries_an_empty_breakdown(fake_install):
    preview = bk.size_preview(categories={'media': False})
    assert preview['categories']['media']['enabled'] is False
    assert preview['categories']['media']['directories'] == []


# ── create_backup progress callback ─────────────────────────────────────────

def test_progress_callback_reports_monotonic_bytes_and_a_total(fake_install):
    seen = []
    result = bk.create_backup(progress_cb=seen.append)
    assert seen, 'progress callback never fired'
    assert [p['bytes_written'] for p in seen] == sorted(p['bytes_written'] for p in seen)
    assert seen[-1]['files_written'] == result['files_written']
    assert seen[-1]['total_files'] >= seen[-1]['files_written']
    assert seen[-1]['total_bytes'] > 0
    assert seen[-1]['current_file']


def test_progress_callback_exception_never_breaks_the_backup(fake_install):
    def boom(_p):
        raise RuntimeError('poller went away')
    result = bk.create_backup(progress_cb=boom)
    assert Path(result['path']).exists()


def test_synchronous_create_is_unchanged_without_a_callback(fake_install):
    result = bk.create_backup()
    assert Path(result['path']).exists()
    with zipfile.ZipFile(result['path']) as zf:
        assert json.loads(zf.read('manifest.json'))['format'] == bk.FORMAT_VERSION


# ── .partial temp files: unique, cleaned up, swept ──────────────────────────

def test_partial_temp_name_is_unique_per_run(fake_install, tmp_path):
    a = bk._partial_path_for(tmp_path, 'clayrune-2026-01-01-full.crbackup')
    b = bk._partial_path_for(tmp_path, 'clayrune-2026-01-01-full.crbackup')
    assert a != b, 'two runs with the same categories would collide on one temp file'
    assert a.name.endswith('.partial') and str(os.getpid()) in a.name


def test_successful_create_leaves_no_partial_behind(fake_install):
    result = bk.create_backup()
    dest = Path(result['path']).parent
    assert list(dest.glob('.*.partial')) == []


def test_failed_create_removes_its_own_partial(fake_install, monkeypatch):
    def explode(_data):
        raise RuntimeError('disk went away mid-write')
    monkeypatch.setattr(bk, '_sha256_bytes', explode)
    with pytest.raises(RuntimeError):
        bk.create_backup()
    dest = bk._paths()['backup_dir']
    assert list(dest.glob('.*.partial')) == [], 'a failed run orphaned its temp archive'


def test_sweep_removes_stale_partials_only(fake_install, tmp_path):
    d = tmp_path / 'backups'
    d.mkdir()
    fresh = d / '.clayrune-fresh.crbackup.1-aaaa.partial'
    stale = d / '.clayrune-stale.crbackup.2-bbbb.partial'
    keep = d / 'clayrune-2026-01-01-full.crbackup'
    for f in (fresh, stale, keep):
        f.write_bytes(b'x' * 10)
    old = time.time() - (bk.STALE_PARTIAL_AGE + 60)
    os.utime(stale, (old, old))

    removed = bk.sweep_stale_partials(d)

    assert [r['path'] for r in removed] == [str(stale)]
    assert removed[0]['bytes'] == 10
    assert not stale.exists()
    assert fresh.exists(), 'a partial young enough to still be being written was deleted'
    assert keep.exists(), 'a finished archive was deleted'


def test_list_backups_sweeps_but_can_be_asked_not_to(fake_install, tmp_path):
    d = tmp_path / 'backups'
    d.mkdir()
    stale = d / '.clayrune-old.crbackup.9-cccc.partial'
    stale.write_bytes(b'orphan')
    old = time.time() - (bk.STALE_PARTIAL_AGE + 60)
    os.utime(stale, (old, old))

    bk.list_backups(dest_dir=d, sweep_stale=False)
    assert stale.exists()

    bk.list_backups(dest_dir=d)
    assert not stale.exists()


# ── routes: async job + progress polling ────────────────────────────────────

@pytest.fixture
def client():
    from mc.blueprints import backup_routes
    app = Flask(__name__)
    app.register_blueprint(backup_routes.bp)
    return app.test_client()


def _await_job(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f'/api/backup/create/status/{job_id}')
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()
        if data['status'] != 'running':
            return data
        time.sleep(0.05)
    raise AssertionError('async backup job never finished')


def test_route_async_create_returns_a_job_id_then_completes(fake_install, tmp_path, client):
    dest = tmp_path / 'external' / 'backups'
    resp = client.post('/api/backup/create', json={
        'categories': {'records': True}, 'dest_dir': str(dest), 'async': True})
    assert resp.status_code == 202, resp.get_json()
    body = resp.get_json()
    assert body['job_id'] and body['status'] == 'running'

    final = _await_job(client, body['job_id'])
    assert final['status'] == 'done', final
    assert final['error'] is None
    assert Path(final['result']['path']).parent == dest.resolve()
    assert Path(final['result']['path']).exists()
    assert final['result']['files_written'] > 0
    assert final['bytes_written'] > 0
    assert final['total_files'] >= final['files_written']


def test_route_async_create_reports_a_failure_on_the_job(fake_install, client, monkeypatch):
    def explode(*_a, **_k):
        raise RuntimeError('enumeration blew up')
    monkeypatch.setattr(bk, '_iter_registered_projects', explode)
    resp = client.post('/api/backup/create', json={'categories': {'records': True}, 'async': True})
    assert resp.status_code == 202
    final = _await_job(client, resp.get_json()['job_id'])
    assert final['status'] == 'error'
    assert 'enumeration blew up' in final['error']


def test_route_async_create_still_refuses_a_bad_destination_up_front(fake_install, client):
    resp = client.post('/api/backup/create', json={
        'categories': {'records': True}, 'dest_dir': str(fake_install['root']), 'async': True})
    assert resp.status_code == 400
    assert 'inside the repo' in resp.get_json()['error']


def test_route_status_404s_for_an_unknown_job(fake_install, client):
    resp = client.get('/api/backup/create/status/deadbeef')
    assert resp.status_code == 404


def test_route_create_without_async_is_still_synchronous(fake_install, tmp_path, client):
    """The CLI and tests/test_backup_dest_dir.py depend on this: no `async`
    key means the response IS the finished backup, not a job handle."""
    dest = tmp_path / 'sync' / 'backups'
    resp = client.post('/api/backup/create', json={
        'categories': {'records': True}, 'dest_dir': str(dest)})
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'job_id' not in data
    assert Path(data['path']).exists()
