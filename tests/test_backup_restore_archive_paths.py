"""Restore after the project records were lost (MC-1179 pass 07, P27 follow-up).

A full backup exists for the day the records are gone. The first P27 cut
allowed a restore to write only under ``_paths()`` roots and registered
project paths, so with the records deleted every 'unprotected' member was
refused and the archive could not be restored at all. A project path named by
the archive's OWN record (verified, absolute) now counts as a root.

Each refusal is asserted by what is on DISK afterwards, not by a flag.
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


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _full_archive() -> Path:
    return Path(bk.create_backup(categories={'transcripts': False})['path'])


def _delete_all_project_records(root: Path) -> None:
    for f in (root / 'data' / 'projects').glob('*.json'):
        f.unlink()


def _with_forged_record(archive: Path, out: Path, project_path, *, bad_sha: bool = False,
                        unprotected_dest: str = '') -> Path:
    """Copy ``archive`` adding a manifest-listed project 'evil' whose record
    carries ``project_path``, and optionally an unprotected member whose
    recorded dest is ``unprotected_dest``."""
    with zipfile.ZipFile(archive) as src:
        manifest = json.loads(src.read('manifest.json'))
        rec = json.dumps({'id': 'evil', 'project_path': project_path}).encode()
        data_dir = Path(next(m['dest'] for k, m in manifest['files'].items()
                             if k.startswith('records/projects/'))).parent
        manifest['projects'].append({'id': 'evil', 'project_path': '', 'git_remote': None,
                                     'git_head': None})
        manifest['files']['records/projects/evil.json'] = {
            'sha256': '0' * 64 if bad_sha else _sha(rec), 'bytes': len(rec),
            'category': 'records', 'dest': str(data_dir / 'evil.json')}
        extra: dict[str, bytes] = {}
        if unprotected_dest:
            extra['unprotected/evil/pwn.txt'] = b'PWNED'
            manifest['files']['unprotected/evil/pwn.txt'] = {
                'sha256': _sha(b'PWNED'), 'bytes': 5, 'category': 'unprotected',
                'dest': unprotected_dest}
        with zipfile.ZipFile(out, 'w') as dst:
            for item in src.infolist():
                if item.filename != 'manifest.json':
                    dst.writestr(item, src.read(item.filename))
            dst.writestr('records/projects/evil.json', rec)
            for name, payload in extra.items():
                dst.writestr(name, payload)
            dst.writestr('manifest.json', json.dumps(manifest))
    return out


# ── the case a full backup exists for ───────────────────────────────────────

def test_full_restore_after_every_project_record_is_deleted(fake_install):
    archive = _full_archive()
    untracked = fake_install['checkout'] / 'untracked_small.txt'
    record = fake_install['root'] / 'data' / 'projects' / 'proj_a.json'
    _delete_all_project_records(fake_install['root'])
    untracked.unlink()
    assert not record.exists()
    report = bk.restore_backup(archive)
    for cat, stats in report['per_category'].items():
        assert not stats['errors'], f'{cat}: {stats["errors"]}'
        assert stats['refused'] == 0
    assert untracked.read_text(encoding='utf-8') == 'untracked but real work'
    assert json.loads(record.read_text(encoding='utf-8'))['id'] == 'proj_a'
    # Where the checkout lives came from the archive's own record: listed.
    assert str(fake_install['checkout']) in report['project_paths_from_archive']


def test_records_only_restore_after_records_are_deleted(fake_install):
    archive = _full_archive()
    record = fake_install['root'] / 'data' / 'projects' / 'proj_a.json'
    _delete_all_project_records(fake_install['root'])
    report = bk.restore_backup(archive, categories=['records'])
    assert report['restored_categories'] == ['records']
    assert not report['per_category']['records']['errors']
    assert json.loads(record.read_text(encoding='utf-8'))['id'] == 'proj_a'


def test_registered_projects_are_not_reported_as_from_archive(fake_install):
    report = bk.restore_backup(_full_archive())
    assert report['project_paths_from_archive'] == []


# ── a forged archive still gains nothing ────────────────────────────────────

def test_forged_member_under_no_known_root_is_refused_and_nothing_written(fake_install, tmp_path):
    archive = _full_archive()
    outside = tmp_path / 'elsewhere' / 'pwn.txt'
    forged = _with_forged_record(archive, tmp_path / 'forged.crbackup',
                                 str(tmp_path / 'declared_elsewhere'),
                                 unprotected_dest=str(outside))
    untracked = fake_install['checkout'] / 'untracked_small.txt'
    _delete_all_project_records(fake_install['root'])
    untracked.unlink()
    with pytest.raises(bk.BackupError) as e:
        bk.restore_backup(forged)
    assert 'unprotected/evil/pwn.txt' in str(e.value)
    assert not outside.exists()
    # The whole restore failed first: no other member was written either.
    assert not untracked.exists()
    assert list((fake_install['root'] / 'data' / 'projects').glob('*.json')) == []


@pytest.mark.parametrize('project_path', ['rel_root', 123, '', None, ['x']])
def test_forged_record_with_relative_or_non_string_project_path_adds_no_root(
        fake_install, tmp_path, monkeypatch, project_path):
    monkeypatch.chdir(tmp_path)  # a relative 'rel_root' would resolve here
    archive = _full_archive()
    target = tmp_path / 'rel_root' / 'pwn.txt'
    forged = _with_forged_record(archive, tmp_path / 'forged.crbackup', project_path,
                                 unprotected_dest=str(target))
    with pytest.raises(bk.BackupError) as e:
        bk.restore_backup(forged)
    assert 'unprotected/evil/pwn.txt' in str(e.value)
    assert not target.exists()


def test_forged_record_with_a_bad_sha_adds_no_root(fake_install, tmp_path):
    archive = _full_archive()
    declared = tmp_path / 'declared'
    forged = _with_forged_record(archive, tmp_path / 'forged.crbackup', str(declared),
                                 bad_sha=True, unprotected_dest=str(declared / 'pwn.txt'))
    with pytest.raises(bk.BackupError):
        bk.restore_backup(forged)
    assert not declared.exists()


def test_a_filesystem_root_project_path_adds_no_root(fake_install, tmp_path):
    archive = _full_archive()
    forged = _with_forged_record(archive, tmp_path / 'forged.crbackup', str(Path(tmp_path.anchor)),
                                 unprotected_dest=str(tmp_path / 'pwn_anywhere.txt'))
    with pytest.raises(bk.BackupError):
        bk.restore_backup(forged)
    assert not (tmp_path / 'pwn_anywhere.txt').exists()


def test_a_verified_absolute_project_path_in_the_archive_is_accepted_and_reported(
        fake_install, tmp_path):
    archive = _full_archive()
    declared = tmp_path / 'declared'
    forged = _with_forged_record(archive, tmp_path / 'forged.crbackup', str(declared),
                                 unprotected_dest=str(declared / 'pwn.txt'))
    report = bk.restore_backup(forged)
    assert (declared / 'pwn.txt').read_bytes() == b'PWNED'
    assert report['project_paths_from_archive'] == [str(declared)]
