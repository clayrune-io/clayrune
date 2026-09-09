"""Tests for the Backup panel's third round of fixes (MC-945):

  * A running create can be CANCELLED. Cooperative, never a killed thread:
    create_backup() polls ``cancel_cb`` between entries and raises
    BackupCancelled, which unwinds through the existing cleanup so the
    multi-GB .partial temp is deleted rather than stranded. 'cancelled' is a
    terminal state of its own — the user asked for it, so it must not be
    reported as 'error'.
  * A reopened panel can REATTACH: GET /api/backup/jobs lists active and
    recently-finished jobs, because the job_id only ever lived in the JS
    state of the tab that started it and died with it.
  * The job registry stays bounded — by age AND by count — while still
    holding a finished job long enough for a reopened panel to show its
    result instead of a blank form.

Same fake-install isolation idiom as tests/test_backup_progress.py.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import backup as bk  # noqa: E402
from mc import state as _state  # noqa: E402
from mc.blueprints import backup_routes  # noqa: E402


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
    # Enough records that a cancel can land mid-write rather than after it.
    for i in range(40):
        (root / 'data' / 'projects' / f'proj_{i:02d}.json').write_text(
            json.dumps({'id': f'proj_{i:02d}', 'project_path': '', 'backlog': []}), encoding='utf-8')
    return {'root': root, 'home': home, 'clayrune': clayrune, 'work': work}


@pytest.fixture
def client(monkeypatch):
    """A blueprint test client over a CLEAN job registry — the registry is a
    module global, so without this a test would see another test's jobs."""
    monkeypatch.setattr(backup_routes, '_backup_jobs', {})
    app = Flask(__name__)
    app.register_blueprint(backup_routes.bp)
    return app.test_client()


def _partials(d: Path) -> list[Path]:
    return list(d.glob('.*.partial')) if d.is_dir() else []


def _await_terminal(client, job_id, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f'/api/backup/create/status/{job_id}')
        assert resp.status_code == 200, resp.get_json()
        data = resp.get_json()
        if data['status'] in ('done', 'error', 'cancelled'):
            return data
        time.sleep(0.02)
    raise AssertionError('async backup job never reached a terminal state')


# ── create_backup(cancel_cb=…) ──────────────────────────────────────────────

def test_cancel_mid_write_raises_and_leaves_no_partial(fake_install, tmp_path):
    """The whole point: an aborted 48GB write must not strand its temp file."""
    dest = tmp_path / 'external' / 'backups'
    seen = []

    def cancel_after_two():
        seen.append(1)
        return len(seen) > 2

    with pytest.raises(bk.BackupCancelled):
        bk.create_backup(categories={'records': True}, dest_dir=dest, cancel_cb=cancel_after_two)

    assert _partials(dest) == [], 'the .partial temp survived a cancel'
    assert list(dest.glob('*.crbackup')) == [], 'a cancelled run published an archive'


def test_cancel_before_the_write_starts_is_honoured(fake_install, tmp_path):
    """Enumeration alone runs for minutes on a real install, so a cancel that
    arrives during it must not be ignored until the first file is written."""
    dest = tmp_path / 'external' / 'backups'
    with pytest.raises(bk.BackupCancelled) as e:
        bk.create_backup(categories={'records': True}, dest_dir=dest, cancel_cb=lambda: True)
    assert 'before any file was written' in str(e.value)
    assert _partials(dest) == []


def test_cancelled_is_a_backup_error_subclass(fake_install):
    """Callers that only know BackupError still handle it; callers that care
    about the difference catch BackupCancelled FIRST."""
    assert issubclass(bk.BackupCancelled, bk.BackupError)


def test_a_cancel_cb_that_never_fires_changes_nothing(fake_install, tmp_path):
    dest = tmp_path / 'external' / 'backups'
    result = bk.create_backup(categories={'records': True}, dest_dir=dest, cancel_cb=lambda: False)
    assert Path(result['path']).exists()
    assert _partials(dest) == []


def test_a_broken_cancel_cb_never_aborts_a_good_write(fake_install, tmp_path):
    """Same rule progress_cb already follows: a broken callback is the
    caller's bug, not a reason to throw away the user's backup."""
    def boom():
        raise RuntimeError('cancel check exploded')
    dest = tmp_path / 'external' / 'backups'
    result = bk.create_backup(categories={'records': True}, dest_dir=dest, cancel_cb=boom)
    assert Path(result['path']).exists()


def test_no_cancel_cb_is_the_unchanged_default(fake_install, tmp_path):
    dest = tmp_path / 'external' / 'backups'
    result = bk.create_backup(categories={'records': True}, dest_dir=dest)
    assert Path(result['path']).exists()


# ── POST /api/backup/create/cancel/<job_id> ─────────────────────────────────

def test_route_cancel_stops_a_running_job_as_cancelled_not_failed(
        fake_install, tmp_path, client, monkeypatch):
    real_read = bk._read_and_maybe_reserialize

    def slow_read(entry):
        time.sleep(0.02)   # keep the job running long enough to cancel it
        return real_read(entry)
    monkeypatch.setattr(bk, '_read_and_maybe_reserialize', slow_read)

    dest = tmp_path / 'external' / 'backups'
    resp = client.post('/api/backup/create', json={
        'categories': {'records': True}, 'dest_dir': str(dest), 'async': True})
    assert resp.status_code == 202
    job_id = resp.get_json()['job_id']

    cancel = client.post(f'/api/backup/create/cancel/{job_id}')
    assert cancel.status_code == 200, cancel.get_json()
    assert cancel.get_json()['cancelled'] is True
    assert cancel.get_json()['status'] == 'cancelling'

    final = _await_terminal(client, job_id)
    assert final['status'] == 'cancelled', final
    assert final['error'] is None, 'a cancel was reported as a failure'
    assert final['cancelled_reason']
    assert _partials(dest) == [], 'the worker left its .partial behind on cancel'
    assert list(dest.glob('*.crbackup')) == []


def test_route_cancel_404s_for_an_unknown_job(fake_install, client):
    assert client.post('/api/backup/create/cancel/deadbeef').status_code == 404


def test_route_cancel_on_a_finished_job_reports_rather_than_pretends(
        fake_install, tmp_path, client):
    dest = tmp_path / 'external' / 'backups'
    resp = client.post('/api/backup/create', json={
        'categories': {'records': True}, 'dest_dir': str(dest), 'async': True})
    job_id = resp.get_json()['job_id']
    assert _await_terminal(client, job_id)['status'] == 'done'

    late = client.post(f'/api/backup/create/cancel/{job_id}')
    assert late.status_code == 200
    body = late.get_json()
    assert body['cancelled'] is False and body['status'] == 'done'


# ── GET /api/backup/jobs — the reattach path ────────────────────────────────

def test_jobs_route_lists_a_running_job_so_a_reopened_panel_can_find_it(
        fake_install, tmp_path, client, monkeypatch):
    real_read = bk._read_and_maybe_reserialize

    def slow_read(entry):
        time.sleep(0.02)
        return real_read(entry)
    monkeypatch.setattr(bk, '_read_and_maybe_reserialize', slow_read)

    dest = tmp_path / 'external' / 'backups'
    job_id = client.post('/api/backup/create', json={
        'categories': {'records': True}, 'dest_dir': str(dest), 'async': True}).get_json()['job_id']

    listing = client.get('/api/backup/jobs')
    assert listing.status_code == 200
    active = listing.get_json()['active']
    assert [j['job_id'] for j in active] == [job_id]
    j = active[0]
    for field in ('status', 'started_at', 'bytes_written', 'total_bytes', 'current_file'):
        assert field in j, f'{field} missing — the panel renders it on reattach'

    client.post(f'/api/backup/create/cancel/{job_id}')
    _await_terminal(client, job_id)


def test_jobs_route_keeps_a_finished_job_queryable_with_its_result(
        fake_install, tmp_path, client):
    dest = tmp_path / 'external' / 'backups'
    job_id = client.post('/api/backup/create', json={
        'categories': {'records': True}, 'dest_dir': str(dest), 'async': True}).get_json()['job_id']
    _await_terminal(client, job_id)

    body = client.get('/api/backup/jobs').get_json()
    assert body['active'] == []
    assert [j['job_id'] for j in body['recent']] == [job_id]
    done = body['recent'][0]
    assert done['status'] == 'done'
    assert done['finished_at']
    assert Path(done['result']['path']).exists()
    assert done['result']['bytes'] > 0
    assert done['result']['categories']['records'] is True


def test_job_result_does_not_carry_the_whole_manifest(fake_install, tmp_path, client):
    """One entry per archived file — hundreds of MB of JSON on a real install,
    echoed by every 700ms status poll, for data no caller reads."""
    dest = tmp_path / 'external' / 'backups'
    job_id = client.post('/api/backup/create', json={
        'categories': {'records': True}, 'dest_dir': str(dest), 'async': True}).get_json()['job_id']
    final = _await_terminal(client, job_id)
    assert 'manifest' not in final['result']
    assert final['result']['files_written'] > 0


def test_sync_create_still_returns_the_full_manifest(fake_install, tmp_path, client):
    """Slimming the async job result must not change the synchronous contract."""
    dest = tmp_path / 'sync' / 'backups'
    data = client.post('/api/backup/create', json={
        'categories': {'records': True}, 'dest_dir': str(dest)}).get_json()
    assert data['manifest']['files']


def test_status_and_jobs_never_leak_internal_ttl_bookkeeping(
        fake_install, tmp_path, client):
    dest = tmp_path / 'external' / 'backups'
    job_id = client.post('/api/backup/create', json={
        'categories': {'records': True}, 'dest_dir': str(dest), 'async': True}).get_json()['job_id']
    final = _await_terminal(client, job_id)
    assert 'finished_at_mono' not in final
    assert all('finished_at_mono' not in j for j in client.get('/api/backup/jobs').get_json()['recent'])


# ── registry retention ──────────────────────────────────────────────────────

def _job(job_id, status, started_at, age_s=0.0):
    return {'job_id': job_id, 'status': status, 'started_at': started_at,
            'finished_at': None if status == 'running' else started_at,
            'finished_at_mono': None if status == 'running' else time.time() - age_s}


def test_finished_jobs_expire_by_age_but_running_ones_never_do(client, monkeypatch):
    reg = backup_routes._backup_jobs
    reg['old'] = _job('old', 'done', '2026-01-01T00:00:00Z',
                      age_s=backup_routes._FINISHED_JOB_TTL + 60)
    reg['fresh'] = _job('fresh', 'done', '2026-01-02T00:00:00Z', age_s=1)
    reg['live'] = _job('live', 'running', '2020-01-01T00:00:00Z')

    body = client.get('/api/backup/jobs').get_json()
    assert 'old' not in reg, 'a finished job outlived its TTL'
    assert [j['job_id'] for j in body['recent']] == ['fresh']
    assert [j['job_id'] for j in body['active']] == ['live'], 'a running job was pruned by age'


def test_finished_jobs_are_capped_by_count(client, monkeypatch):
    monkeypatch.setattr(backup_routes, '_MAX_FINISHED_JOBS', 3)
    reg = backup_routes._backup_jobs
    for i in range(10):
        reg[f'j{i}'] = _job(f'j{i}', 'done', f'2026-01-{i + 1:02d}T00:00:00Z', age_s=1)

    kept = [j['job_id'] for j in client.get('/api/backup/jobs').get_json()['recent']]
    assert len(kept) == 3
    assert set(kept) == {'j7', 'j8', 'j9'}, f'the wrong three survived: {kept}'


def test_a_just_finished_job_survives_long_enough_to_be_reattached(client):
    """Retention is a UX property here, not only a memory one: prune too
    eagerly and a reopened panel shows a blank form where the user's finished
    backup should be."""
    assert backup_routes._FINISHED_JOB_TTL >= 60 * 60
    assert backup_routes._MAX_FINISHED_JOBS >= 5
