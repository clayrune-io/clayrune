"""P28 (MC-1179 pass 07): restore-point routes never turn a URL segment into
a path outside the restore-points directory, and an archive label cannot
place the archive outside the chosen directory.

``project_id`` and ``snap_id`` used to go straight into
``restore_points_dir / project_id / snap_id``; DELETE then ``rmtree``d the
result if it held a ``manifest.json``. Each hostile case is asserted by what
is on DISK afterwards (the outside directory and its manifest are still
there, untouched), not by a flag.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import backup as bk  # noqa: E402
from mc.state import agent_sessions  # noqa: E402
from tests.test_backup import fake_install  # noqa: E402,F401  (fixture)
from tests.test_backup_phase2 import _make_machine, fake_install_a  # noqa: E402,F401
from tests.test_backup_phase3 import fake_install as rp_install  # noqa: E402,F401


@pytest.fixture
def client(rp_install):
    from mc.blueprints import backup_routes
    agent_sessions.clear()
    app = Flask(__name__)
    app.register_blueprint(backup_routes.bp)
    yield app.test_client()
    agent_sessions.clear()


def _outside(rp_install) -> Path:
    """A directory outside the restore-points tree that holds a manifest.json
    — the thing the report's DELETE removed."""
    d = rp_install['clayrune'] / 'unrelated_app'
    d.mkdir(parents=True)
    (d / 'manifest.json').write_text(json.dumps({'label': 'original', 'pinned': False}),
                                     encoding='utf-8')
    (d / 'keep.txt').write_text('keep', encoding='utf-8')
    return d


def _intact(d: Path) -> bool:
    return (d / 'keep.txt').is_file() and json.loads(
        (d / 'manifest.json').read_text(encoding='utf-8')) == {'label': 'original', 'pinned': False}


# ── the report's measured DELETE ────────────────────────────────────────────

def test_route_delete_with_backslash_traversal_leaves_outside_dir(client, rp_install):
    outside = _outside(rp_install)
    res = client.delete('/api/backup/restore-point/foo/..%5C..%5Cunrelated_app')
    assert res.status_code in (400, 404), res.get_data(as_text=True)
    assert _intact(outside)


def test_route_delete_with_dotdot_project_leaves_outside_dir(client, rp_install):
    outside = _outside(rp_install)
    # restore_points_dir / '..' / 'unrelated_app' == clayrune/unrelated_app
    res = bk_delete_via_route(client, '%2E%2E', 'unrelated_app')
    assert res.status_code in (400, 404), res.get_data(as_text=True)
    assert _intact(outside)


def bk_delete_via_route(client, project_id, snap_id):
    return client.delete(f'/api/backup/restore-point/{project_id}/{snap_id}')


def test_function_delete_refuses_dotdot_project(rp_install):
    outside = _outside(rp_install)
    with pytest.raises(bk.BackupError):
        bk.delete_restore_point('..', 'unrelated_app')
    assert _intact(outside)


def test_patch_label_and_pin_do_not_rewrite_outside_manifest(client, rp_install):
    outside = _outside(rp_install)
    for body in ({'label': 'pwned'}, {'pinned': True}):
        res = client.patch('/api/backup/restore-point/%2E%2E/unrelated_app', json=body)
        assert res.status_code in (400, 404)
    assert _intact(outside)


def test_get_list_does_not_list_a_directory_outside(client, rp_install):
    _outside(rp_install)
    res = client.get('/api/backup/restore-point/%2E%2E')
    assert res.status_code in (400, 404)
    with pytest.raises(bk.BackupError):
        bk.list_restore_points('..')


@pytest.mark.parametrize('snap', ['..', '.', 'x', '20260101T000000000000', 'a/b',
                                  '20260101T000000000000-a\\..\\..\\b',
                                  '20260101T000000000000-' + 'a' * 200])
def test_snap_id_must_match_the_shape_this_module_writes(rp_install, snap):
    bk.create_restore_point('proj_x', label='real')
    for fn in (lambda: bk.delete_restore_point('proj_x', snap),
               lambda: bk.label_restore_point('proj_x', snap, 'x'),
               lambda: bk.pin_restore_point('proj_x', snap, True)):
        with pytest.raises(bk.BackupError):
            fn()


def test_unknown_project_with_no_directory_is_refused_for_writes_and_lists_empty(rp_install):
    # LIST keeps its existing contract (test_list_unknown_project_returns_empty):
    # a well-formed unknown id has nothing on disk. Anything that changes or
    # deletes needs a registered project or an existing directory.
    assert bk.list_restore_points('not_a_project') == []
    sid = '20260101T000000000000-x'
    for fn in (lambda: bk.delete_restore_point('not_a_project', sid),
               lambda: bk.label_restore_point('not_a_project', sid, 'x'),
               lambda: bk.pin_restore_point('not_a_project', sid, True)):
        with pytest.raises(bk.BackupError):
            fn()


# ── DELETE is refused to an unattended caller ───────────────────────────────

def test_delete_refused_to_unattended_caller_and_point_survives(client, rp_install):
    made = bk.create_restore_point('proj_x', label='keepme')
    agent_sessions['s1'] = {'status': 'running', 'project_id': 'proj_x', 'trigger_type': 'schedule'}
    res = client.delete(f"/api/backup/restore-point/proj_x/{made['snap_id']}")
    assert res.status_code == 403
    assert Path(made['path'], 'manifest.json').is_file()


# ── legitimate use still works ──────────────────────────────────────────────

def test_attended_delete_and_list_and_patch_roundtrip(client, rp_install):
    made = bk.create_restore_point('proj_x', label='real one')
    sid = made['snap_id']
    assert [p['snap_id'] for p in client.get('/api/backup/restore-point/proj_x').get_json()['restore_points']] == [sid]
    assert client.patch(f'/api/backup/restore-point/proj_x/{sid}', json={'pinned': True}).status_code == 200
    assert client.patch(f'/api/backup/restore-point/proj_x/{sid}', json={'label': 'renamed'}).status_code == 200
    res = client.delete(f'/api/backup/restore-point/proj_x/{sid}')
    assert res.status_code == 200 and res.get_json()['deleted'] is True
    assert not Path(made['path']).exists()


def test_restore_points_of_a_deleted_project_stay_reachable(rp_install):
    """rollback exists to recover a project whose record was deleted, so a
    project dir that already exists on disk is accepted without a record."""
    made = bk.create_restore_point('proj_x', label='before delete')
    (rp_install['root'] / 'data' / 'projects' / 'proj_x.json').unlink()
    assert [p['snap_id'] for p in bk.list_restore_points('proj_x')] == [made['snap_id']]
    assert bk.delete_restore_point('proj_x', made['snap_id'])['deleted'] is True


def test_rollback_auto_snapshot_and_labels_with_odd_characters_round_trip(rp_install):
    made = bk.create_restore_point('proj_x', label='héllo wörld / ../ x')
    assert Path(made['path']).parent == (rp_install['clayrune'] / 'restore-points' / 'proj_x').resolve() \
        or Path(made['path']).parent == rp_install['clayrune'] / 'restore-points' / 'proj_x'
    report = bk.rollback('proj_x', made['snap_id'])
    assert report['pre_rollback_snapshot']
    assert bk.delete_restore_point('proj_x', report['pre_rollback_snapshot'])['deleted']


# ── hostile archive label ───────────────────────────────────────────────────

@pytest.mark.parametrize('label', ['../../escaped', '..\\..\\escaped', '/abs/escaped', '/../../escaped',
                                   '\\..\\..\\escaped', 'C:\\escaped', 'a/../../escaped'])
def test_create_backup_label_cannot_place_archive_outside_dest(fake_install, tmp_path, label):
    dest = tmp_path / 'chosen' / 'backups'
    try:
        result = bk.create_backup(categories={'records': True}, dest_dir=dest, label=label)
    except OSError:
        result = None  # refused by the OS is also not an escape
    inside = {p.resolve() for p in dest.rglob('*.crbackup')} if dest.exists() else set()
    outside = [p for p in tmp_path.rglob('*.crbackup') if p.resolve() not in inside]
    assert outside == [], f'archive written outside {dest}: {outside}'
    assert result is not None
    assert Path(result['path']).resolve().parent == dest.resolve()


@pytest.mark.parametrize('label', ['../../escaped', '..\\..\\escaped', '/../../escaped',
                                   '\\..\\..\\escaped'])
def test_export_project_label_cannot_place_archive_outside_dest(fake_install_a, tmp_path, label):
    dest = tmp_path / 'chosen' / 'exports'
    try:
        result = bk.export_project('proj_x', categories={'records': True}, vault=False, dest_dir=dest, label=label)
    except OSError:
        result = None
    inside = {p.resolve() for p in dest.rglob('*.crbackup')} if dest.exists() else set()
    outside = [p for p in tmp_path.rglob('*.crbackup') if p.resolve() not in inside]
    assert outside == [], f'archive written outside {dest}: {outside}'
    assert result is not None
    assert Path(result['path']).resolve().parent == dest.resolve()


def test_plain_label_still_appears_in_archive_name(fake_install, tmp_path):
    dest = tmp_path / 'external' / 'backups'
    result = bk.create_backup(categories={'records': True}, dest_dir=dest, label='before-upgrade')
    assert Path(result['path']).name.endswith('-before-upgrade.crbackup')
