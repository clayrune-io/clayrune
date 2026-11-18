"""P29 (MC-1179): backups move members a chunk at a time.

Before: restore and import staged every selected member as bytes before the
first write, and create read each file whole, so a file too large to hold was
logged as a skipped file in a backup that looked complete. These tests use
small archives; "too large to hold" is simulated by making the whole-file read
raise MemoryError, and the JSON hold limit is lowered, never a big file built.
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
from mc import backup_stream  # noqa: E402
from tests.test_backup import fake_install  # noqa: E402,F401  (fixture)
from tests.test_backup_member_paths import (  # noqa: E402,F401  (fixtures)
    fake_install_a, archive_a, machine_b)


def _manifest(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as zf:
        return json.loads(zf.read('manifest.json'))


def _spy_reads(monkeypatch) -> list[str]:
    """Record every member name read whole via ZipFile.read."""
    seen: list[str] = []
    real = zipfile.ZipFile.read

    def spy(self, name, pwd=None):
        seen.append(name if isinstance(name, str) else name.filename)
        return real(self, name, pwd)

    monkeypatch.setattr(zipfile.ZipFile, 'read', spy)
    return seen


def _data_members(seen: list[str]) -> list[str]:
    # manifest.json, and the project records the path guard reads to find a
    # project's path, are small and read whole by design.
    return [n for n in seen
            if n != 'manifest.json' and not n.startswith('records/projects/')
            and n != 'secrets/vault.enc']


# -- restore -----------------------------------------------------------------

def test_restore_never_reads_a_data_member_whole(fake_install, monkeypatch):
    archive = Path(bk.create_backup(categories={'transcripts': False})['path'])
    target = fake_install['checkout'] / 'untracked_small.txt'
    photo = fake_install['root'] / 'data' / 'uploads' / 'photo.png'
    target.unlink()
    photo.unlink()
    seen = _spy_reads(monkeypatch)
    report = bk.restore_backup(archive)
    assert _data_members(seen) == [], f'read whole: {_data_members(seen)}'
    assert target.read_text(encoding='utf-8') == 'untracked but real work'
    assert photo.read_bytes() == b'\x89PNG fake'
    assert all(not s['errors'] for s in report['per_category'].values())


def test_restore_bad_hash_still_aborts_before_any_write(fake_install, tmp_path):
    archive = Path(bk.create_backup(categories={'transcripts': False})['path'])
    manifest = _manifest(archive)
    # Corrupt one member's recorded hash; every other member must stay unwritten.
    victim = next(k for k in manifest['files'] if k.endswith('photo.png'))
    manifest['files'][victim]['sha256'] = '0' * 64
    bad = tmp_path / 'bad.crbackup'
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(bad, 'w') as dst:
        for item in src.infolist():
            if item.filename != 'manifest.json':
                dst.writestr(item, src.read(item.filename))
        dst.writestr('manifest.json', json.dumps(manifest))
    target = fake_install['checkout'] / 'untracked_small.txt'
    target.unlink()
    with pytest.raises(bk.BackupIntegrityError):
        bk.restore_backup(bad)
    assert not target.exists()


def test_restore_keeps_a_differing_unprotected_file_and_writes_the_conflict_copy(fake_install):
    archive = Path(bk.create_backup(categories={'transcripts': False})['path'])
    target = fake_install['checkout'] / 'untracked_small.txt'
    target.write_text('edited after the backup', encoding='utf-8')
    report = bk.restore_backup(archive)
    assert target.read_text(encoding='utf-8') == 'edited after the backup'
    conflict = target.with_name(target.name + '.crbak-restored')
    assert conflict.read_text(encoding='utf-8') == 'untracked but real work'
    assert report['per_category']['unprotected']['conflicts'] == 1


# -- import ------------------------------------------------------------------

def test_import_never_reads_a_data_member_whole(archive_a, machine_b, monkeypatch):
    seen = _spy_reads(monkeypatch)
    report = bk.import_project(archive_a, new_project_path=str(machine_b['new_path']))
    assert report['status'] == 'applied'
    assert _data_members(seen) == [], f'read whole: {_data_members(seen)}'
    assert (machine_b['root'] / 'data' / 'projects' / 'proj_x.json').is_file()


# -- create ------------------------------------------------------------------

def test_create_keeps_a_file_too_large_to_hold(fake_install, monkeypatch):
    """The whole-file read raises MemoryError for non-JSON files. Before the
    fix each one was recorded as 'parse failed' and left out of the archive."""
    real = bk._read_and_maybe_reserialize

    def too_big(entry):
        if not entry.reserialize_json:
            raise MemoryError('simulated: file too large to hold')
        return real(entry)

    monkeypatch.setattr(bk, '_read_and_maybe_reserialize', too_big)
    result = bk.create_backup(categories={'transcripts': False})
    manifest = _manifest(Path(result['path']))
    photo_key = next(k for k in manifest['files'] if k.endswith('photo.png'))
    assert [w for w in manifest['warnings'] if w['kind'] == 'unreadable'] == [], manifest['warnings']
    meta = manifest['files'][photo_key]
    assert meta['sha256'] == hashlib.sha256(b'\x89PNG fake').hexdigest()
    assert meta['bytes'] == len(b'\x89PNG fake')
    with zipfile.ZipFile(result['path']) as zf:
        assert zf.read(photo_key) == b'\x89PNG fake'


def test_create_archives_a_json_file_over_the_hold_limit_as_is_and_says_so(
        fake_install, monkeypatch):
    monkeypatch.setattr(backup_stream, 'MAX_JSON_HOLD_BYTES', 10)
    result = bk.create_backup(categories={'transcripts': False})
    manifest = _manifest(Path(result['path']))
    rec = fake_install['root'] / 'data' / 'projects' / 'proj_a.json'
    key = 'records/projects/proj_a.json'
    with zipfile.ZipFile(result['path']) as zf:
        assert zf.read(key) == rec.read_bytes()
    assert manifest['files'][key]['sha256'] == hashlib.sha256(rec.read_bytes()).hexdigest()
    assert any(w['kind'] == 'unverified' and w['path'] == str(rec)
               for w in manifest['warnings']), manifest['warnings']


def test_create_round_trips_through_restore(fake_install):
    archive = Path(bk.create_backup(categories={'transcripts': False})['path'])
    photo = fake_install['root'] / 'data' / 'uploads' / 'photo.png'
    photo.unlink()
    bk.restore_backup(archive)
    assert photo.read_bytes() == b'\x89PNG fake'


def test_create_still_records_a_vanished_file(fake_install, monkeypatch):
    photo = fake_install['root'] / 'data' / 'uploads' / 'photo.png'
    real_add = backup_stream.add_file

    def gone(zf, arcname, src):
        if Path(src) == photo:
            photo.unlink()
        return real_add(zf, arcname, src)

    monkeypatch.setattr(backup_stream, 'add_file', gone)
    result = bk.create_backup(categories={'transcripts': False})
    manifest = _manifest(Path(result['path']))
    assert any(w['kind'] == 'vanished' and w['path'] == str(photo) for w in manifest['warnings'])
    assert not any(k.endswith('photo.png') for k in manifest['files'])


# -- helpers -----------------------------------------------------------------

def test_member_equals_file(tmp_path):
    z = tmp_path / 'a.zip'
    with zipfile.ZipFile(z, 'w') as zf:
        zf.writestr('m', b'x' * 10)
    p = tmp_path / 'f'
    with zipfile.ZipFile(z) as zf:
        for content, want in ((b'x' * 10, True), (b'x' * 9, False),
                              (b'x' * 11, False), (b'y' * 10, False)):
            p.write_bytes(content)
            assert backup_stream.member_equals_file(zf, 'm', p) is want, content
