"""P27 (MC-1179 pass 07): import and restore write only where they should.

Import used to join archive member-name segments onto a base path without
checking the result stayed under it; restore wrote each member to the
absolute ``dest`` its own manifest recorded, with no containment check at all.
The archive is someone else's file on the cross-machine import path, so a
hostile member name or dest was an arbitrary file write as the server user.

Each hostile case is asserted by what is on DISK afterwards (the escape
target does not exist, and no other member of the same import was written
either), not by a flag, because the fix has to fail the whole operation
before the first write.
"""
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import backup as bk  # noqa: E402
from tests.test_backup import fake_install  # noqa: E402,F401  (fixture)
from tests.test_backup_phase2 import _make_machine, fake_install_a  # noqa: E402,F401  (fixtures)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _forge(archive: Path, out: Path, members: dict[str, tuple[str, bytes, dict]]) -> Path:
    """Copy ``archive`` to ``out`` adding manifest entries. ``members`` maps a
    MANIFEST KEY to ``(zip_member_name, payload, extra_meta)``; the zip member
    name can differ from the key (a backslash key cannot be stored as a zip
    name on Windows, which rewrites separators)."""
    with zipfile.ZipFile(archive) as src:
        manifest = json.loads(src.read('manifest.json'))
        with zipfile.ZipFile(out, 'w') as dst:
            for item in src.infolist():
                if item.filename != 'manifest.json':
                    dst.writestr(item, src.read(item.filename))
            for key, (zname, payload, extra) in members.items():
                dst.writestr(zname, payload)
                meta = {'sha256': _sha(payload), 'bytes': len(payload), 'category': 'records'}
                meta.update(extra)
                manifest['files'][key] = meta
            dst.writestr('manifest.json', json.dumps(manifest))
    return out


def _escaped(tmp_path: Path, name: str) -> list[Path]:
    return [p for p in tmp_path.rglob(name)]


# ── import ──────────────────────────────────────────────────────────────────

@pytest.fixture
def archive_a(fake_install_a):
    return Path(bk.export_project('proj_x', vault=False)['path'])


@pytest.fixture
def machine_b(archive_a, fake_install_a, tmp_path, monkeypatch):
    """Machine B with a different project path; machine A's checkout moved
    away so the import has to remap."""
    fake_install_a['checkout'].rename(fake_install_a['checkout'].with_name('checkout_gone'))
    b = _make_machine(tmp_path, 'machineB', monkeypatch)
    new_path = tmp_path / 'machineB' / 'newcheckout'
    new_path.mkdir(parents=True)
    b['new_path'] = new_path
    return b


MEASURED = [
    # (manifest key, zip member name, escape file name)
    ('records/memory/proj_x/' + '../' * 5 + 'escaped_a.txt',
     'records/memory/proj_x/' + '../' * 5 + 'escaped_a.txt', 'escaped_a.txt'),
    ('records/projects/..\\..\\..\\escaped_b.json',
     'records/projects/alias_b.json', 'escaped_b.json'),
    ('unprotected/nongit/proj_x/../../escaped_c.txt',
     'unprotected/nongit/proj_x/../../escaped_c.txt', 'escaped_c.txt'),
]


@pytest.mark.parametrize('key,zname,escape', MEASURED)
def test_remap_refuses_each_measured_member_name(archive_a, machine_b, key, zname, escape):
    paths = bk._paths()
    with pytest.raises(bk.BackupError) as e:
        bk._remap_import_dest(key, paths, old_id='proj_x', new_id='proj_x',
                              new_path=str(machine_b['new_path']),
                              new_encoded=bk._encode_project_path(str(machine_b['new_path'])))
    assert key in str(e.value)


@pytest.mark.parametrize('key,zname,escape', MEASURED)
def test_import_of_hostile_member_fails_whole_import_and_writes_nothing(
        archive_a, machine_b, tmp_path, key, zname, escape):
    forged = _forge(archive_a, tmp_path / 'forged.crbackup', {key: (zname, b'PWNED', {})})
    with pytest.raises(bk.BackupError) as e:
        bk.import_project(forged, new_project_path=str(machine_b['new_path']))
    assert key in str(e.value)
    # The escape target does not exist anywhere under the sandbox...
    assert _escaped(tmp_path, escape) == []
    # ...and nothing else from the same import was written first.
    assert not (machine_b['root'] / 'data' / 'projects' / 'proj_x.json').exists()
    assert list(machine_b['new_path'].iterdir()) == []


def test_import_refuses_a_records_file_that_is_not_a_project_or_sidecar(
        archive_a, machine_b, tmp_path):
    """Import never ran _validate_records_write, so records/projects/<any>.json
    landed in data/projects and became a "project" (the DATA_DIR rule)."""
    key = 'records/projects/not_a_project.json'
    forged = _forge(archive_a, tmp_path / 'forged.crbackup', {key: (key, b'{}', {})})
    with pytest.raises(bk.BackupError) as e:
        bk.import_project(forged, new_project_path=str(machine_b['new_path']))
    assert key in str(e.value)
    data_dir = machine_b['root'] / 'data' / 'projects'
    assert not (data_dir / 'not_a_project.json').exists()
    assert not (data_dir / 'proj_x.json').exists()


def test_import_as_copy_still_round_trips(archive_a, fake_install_a, tmp_path):
    """A renamed (import-as-copy) project record is not in the manifest's id
    list; the records check must be told the id the import is writing."""
    new_path = tmp_path / 'copy_checkout'
    new_path.mkdir()
    report = bk.import_project(archive_a, project_resolution='import-as-copy',
                               new_project_path=str(new_path))
    assert report['status'] == 'applied'
    assert report['final_project_id'] == 'proj_x-imported'
    assert not report['warnings'], report['warnings']
    data_dir = fake_install_a['root'] / 'data' / 'projects'
    assert (data_dir / 'proj_x-imported.json').is_file()
    assert (data_dir / 'proj_x-imported_agent_log.json').is_file()


# ── restore ─────────────────────────────────────────────────────────────────

def _full_archive(tmp_path):
    return Path(bk.create_backup(categories={'transcripts': False})['path'])


def test_restore_refuses_a_dest_outside_every_allowed_root(fake_install, tmp_path):
    archive = _full_archive(tmp_path)
    outside = tmp_path / 'elsewhere' / 'pwned.txt'
    key = 'records/extra/pwned.txt'
    forged = _forge(archive, tmp_path / 'forged.crbackup',
                    {key: (key, b'PWNED', {'dest': str(outside)})})
    # A legit member changed on disk after the backup: if restore wrote
    # anything before refusing, this would be reverted.
    cfg = fake_install['root'] / 'config.json'
    cfg.write_text(json.dumps({'k': 'changed-after-backup'}), encoding='utf-8')
    with pytest.raises(bk.BackupError) as e:
        bk.restore_backup(forged)
    assert key in str(e.value)
    assert not outside.exists()
    assert json.loads(cfg.read_text(encoding='utf-8')) == {'k': 'changed-after-backup'}


def test_restore_refuses_a_dest_that_climbs_out_with_dotdot(fake_install, tmp_path):
    archive = _full_archive(tmp_path)
    data_dir = fake_install['root'] / 'data' / 'projects'
    sneaky = data_dir / '..' / '..' / '..' / 'escaped_restore.txt'
    key = 'records/extra/sneaky.txt'
    forged = _forge(archive, tmp_path / 'forged.crbackup',
                    {key: (key, b'PWNED', {'dest': str(sneaky)})})
    with pytest.raises(bk.BackupError) as e:
        bk.restore_backup(forged)
    assert key in str(e.value)
    assert _escaped(tmp_path, 'escaped_restore.txt') == []


def test_restore_refuses_a_member_name_with_dotdot(fake_install, tmp_path):
    archive = _full_archive(tmp_path)
    key = 'records/../../escaped_name.txt'
    ok_dest = fake_install['root'] / 'data' / 'projects' / 'proj_a_agent_log.json'
    forged = _forge(archive, tmp_path / 'forged.crbackup',
                    {key: (key, b'PWNED', {'dest': str(ok_dest)})})
    with pytest.raises(bk.BackupError) as e:
        bk.restore_backup(forged)
    assert key in str(e.value)


def test_restore_in_the_real_paths_still_round_trips(fake_install, tmp_path):
    """Real-path mode (dest_root None): every member this module wrote lands
    under a _paths() root or a registered project path, so the guard lets it
    through."""
    archive = _full_archive(tmp_path)
    untracked = fake_install['checkout'] / 'untracked_small.txt'
    cfg = fake_install['root'] / 'config.json'
    untracked.unlink()
    cfg.write_text('{}', encoding='utf-8')
    report = bk.restore_backup(archive)
    for cat, stats in report['per_category'].items():
        assert not stats['errors'], f'{cat}: {stats["errors"]}'
        assert stats['refused'] == 0
    assert untracked.read_text(encoding='utf-8') == 'untracked but real work'
    assert json.loads(cfg.read_text(encoding='utf-8')) == {'k': 'v'}


def test_restore_into_a_sandbox_root_is_unchanged(fake_install, tmp_path):
    archive = _full_archive(tmp_path)
    sandbox = tmp_path / 'restored'
    report = bk.restore_backup(archive, dest_root=sandbox)
    assert report['restored_categories']
    assert list(sandbox.rglob('untracked_small.txt'))
