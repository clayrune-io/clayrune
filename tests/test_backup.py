"""Round-trip tests for mc/backup.py — Phase 1 (docs/BACKUP_EXPORT_SPEC.md).

Builds a small fake install (fake repo root, fake HOME, fake ~/.clayrune) via
env vars, exactly the isolation mc/backup.py was designed for (every path is
resolved through _paths(), never a module-level constant, so monkeypatching
MC_DATA_DIR / HOME / CLAYRUNE_HOME before each call is enough — no reload
needed, unlike server.py's DATA_DIR).
"""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import backup as bk  # noqa: E402


def _git(cwd, *args):
    subprocess.run(['git'] + list(args), cwd=str(cwd), check=True,
                   capture_output=True, text=True)


@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    """Fake repo root + HOME + ~/.clayrune, with two registered projects: one
    git checkout (untracked + ignored + oversize files), one non-git dir
    (with a junk subdirectory)."""
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

    # Project A: a git checkout.
    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    _git(checkout, 'init', '-q')
    _git(checkout, 'config', 'user.email', 'a@b.c')
    _git(checkout, 'config', 'user.name', 'test')
    (checkout / 'tracked.txt').write_text('tracked', encoding='utf-8')
    _git(checkout, 'add', 'tracked.txt')
    _git(checkout, 'commit', '-q', '-m', 'init')
    (checkout / 'untracked_small.txt').write_text('untracked but real work', encoding='utf-8')
    (checkout / '.gitignore').write_text('ignored_file.txt\n', encoding='utf-8')
    _git(checkout, 'add', '.gitignore')
    _git(checkout, 'commit', '-q', '-m', 'gitignore')
    (checkout / 'ignored_file.txt').write_text('should never be swept', encoding='utf-8')
    big = checkout / 'oversize.bin'
    big.write_bytes(b'0' * (bk.CHECKOUT_FILE_CEILING + 1))

    # Project B: a whole non-git directory, with a junk subdir.
    nongit = tmp_path / 'nongit'
    (nongit / 'node_modules').mkdir(parents=True)
    (nongit / 'node_modules' / 'junk.js').write_text('should be excluded', encoding='utf-8')
    (nongit / 'real_data.csv').write_text('a,b,c\n1,2,3\n', encoding='utf-8')

    (root / 'data' / 'projects' / 'proj_a.json').write_text(
        json.dumps({'id': 'proj_a', 'project_path': str(checkout), 'backlog': []}), encoding='utf-8')
    (root / 'data' / 'projects' / 'proj_a_agent_log.json').write_text(
        json.dumps({'events': ['sidecar data']}), encoding='utf-8')
    (root / 'data' / 'projects' / 'proj_b.json').write_text(
        json.dumps({'id': 'proj_b', 'project_path': str(nongit), 'backlog': []}), encoding='utf-8')
    (root / 'config.json').write_text(json.dumps({'k': 'v'}), encoding='utf-8')
    (root / 'data' / 'uploads' / 'photo.png').write_bytes(b'\x89PNG fake')

    # A memory vault under the fake CLAUDE home for proj_a.
    encoded = bk._encode_project_path(str(checkout))
    mem_dir = home / '.claude' / 'projects' / encoded / 'memory'
    mem_dir.mkdir(parents=True)
    (mem_dir / 'MEMORY.md').write_text('# memory\n', encoding='utf-8')
    (mem_dir / 'topic_x.md').write_text('topic detail', encoding='utf-8')

    # A transcript.
    tdir = home / '.claude' / 'projects' / encoded
    (tdir / 'session1.jsonl').write_text('{"role":"user"}\n', encoding='utf-8')

    return {'root': root, 'home': home, 'clayrune': clayrune,
           'checkout': checkout, 'nongit': nongit}


def test_size_preview_defaults_everything_on(fake_install):
    preview = bk.size_preview()
    for name in ('records', 'artifacts', 'media', 'transcripts', 'unprotected'):
        assert preview['categories'][name]['enabled'] is True
    assert preview['categories']['records']['bytes'] > 0
    assert preview['categories']['vault']['status'] == 'not_available'


def test_unprotected_respects_gitignore_and_ceiling(fake_install):
    preview = bk.size_preview()
    lines = preview['categories']['unprotected']['directories']
    checkout_line = next(l for l in lines if l['project_id'] == 'proj_a')
    assert checkout_line['kind'] == 'checkout'
    assert checkout_line['files'] == 1  # only untracked_small.txt: ignored + oversize both excluded
    oversize_warnings = [w for w in preview['warnings'] if w['kind'] == 'oversize_skipped']
    assert len(oversize_warnings) == 1
    assert 'oversize.bin' in oversize_warnings[0]['path']


def test_unprotected_nongit_excludes_junk_dirs(fake_install):
    preview = bk.size_preview()
    lines = preview['categories']['unprotected']['directories']
    nongit_line = next(l for l in lines if l['project_id'] == 'proj_b')
    assert nongit_line['kind'] == 'nongit'
    assert nongit_line['files'] == 1  # real_data.csv only — node_modules/junk.js excluded


def test_junkdir_skip_is_named_with_its_bytes(fake_install):
    """Spec §4.8: 'each exclusion that fires is listed in the export report
    with the bytes it skipped' — junk dirs must not vanish silently."""
    preview = bk.size_preview()
    junk_warnings = [w for w in preview['warnings'] if w['kind'] == 'junkdir_skipped']
    assert len(junk_warnings) == 1
    assert junk_warnings[0]['path'].endswith('node_modules')
    assert junk_warnings[0]['bytes'] > 0  # junk.js's real size, not zero


def test_own_data_dir_not_double_swept_as_unprotected(fake_install, monkeypatch):
    """Regression: found for real on this install. A project whose
    project_path IS the Clayrune repo itself must not have its own data/
    subtree swept again as 'unprotected' — it's already archived whole under
    records/artifacts/media, and any file under data/ that isn't gitignored
    (agent_labels.json wasn't) would otherwise be captured twice, with the
    two captures able to disagree if the file changes mid-backup."""
    root = fake_install['root']
    monkeypatch.setattr(bk, 'REPO_ROOT', root)
    _git(root, 'init', '-q')
    _git(root, 'config', 'user.email', 'a@b.c')
    _git(root, 'config', 'user.name', 'test')
    # Mirrors the real repo's actual .gitignore (config.json IS ignored
    # there) — everything else under data/ in this fixture relies on the
    # self-reference guard under test, not on gitignore, exactly like the
    # real data/agent_labels.json gap this regression is named after.
    (root / '.gitignore').write_text('config.json\n', encoding='utf-8')
    _git(root, 'add', '.gitignore')
    _git(root, 'commit', '-q', '-m', 'init')
    (root / 'data' / 'projects' / 'proj_c.json').write_text(
        json.dumps({'id': 'proj_c', 'project_path': str(root), 'backlog': []}), encoding='utf-8')
    (root / 'real_work.py').write_text('# not under data/', encoding='utf-8')

    preview = bk.size_preview()
    lines = preview['categories']['unprotected']['directories']
    self_line = next(l for l in lines if l['project_id'] == 'proj_c')
    skip_warnings = [w for w in preview['warnings'] if w['kind'] == 'own_data_dir_skipped']
    assert skip_warnings, 'expected the self-reference guard to fire and log it'
    # Everything under data/ (proj_a/b/c.json + sidecar + uploads/media) is
    # skipped by the guard; only the genuinely-unprotected real_work.py remains.
    assert self_line['files'] == 1


def test_create_full_default_then_restore_round_trip(fake_install, tmp_path):
    result = bk.create_backup()
    archive = Path(result['path'])
    assert archive.exists()
    assert archive.name.endswith('-full.crbackup')  # nothing unticked
    assert result['files_written'] > 0

    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read('manifest.json'))
    assert manifest['format'] == 1
    assert all(manifest['categories'][c] for c in
              ('records', 'artifacts', 'media', 'transcripts', 'unprotected'))
    assert manifest['categories']['vault'] is False
    assert manifest['vault_status'] == 'not_available'

    sandbox = tmp_path / 'restored'
    report = bk.restore_backup(archive, dest_root=sandbox)
    assert set(report['restored_categories']) == {'records', 'artifacts', 'media', 'transcripts', 'unprotected'}
    assert report['absent_categories'] == ['vault']
    for cat, stats in report['per_category'].items():
        assert not stats['errors'], f'{cat}: {stats["errors"]}'

    # The tracked-untracked file made it through, sandboxed under its
    # original absolute path.
    restored_untracked = list(sandbox.rglob('untracked_small.txt'))
    assert len(restored_untracked) == 1
    assert restored_untracked[0].read_text(encoding='utf-8') == 'untracked but real work'

    # The ignored + oversize files never travelled at all.
    assert not list(sandbox.rglob('ignored_file.txt'))
    assert not list(sandbox.rglob('oversize.bin'))
    assert not list(sandbox.rglob('junk.js'))

    # Records: the project json AND its sidecar both restored.
    assert list(sandbox.rglob('proj_a.json'))
    assert list(sandbox.rglob('proj_a_agent_log.json'))

    # Memory copy order preserved MEMORY.md content.
    restored_mem = list(sandbox.rglob('MEMORY.md'))
    assert restored_mem and restored_mem[0].read_text(encoding='utf-8') == '# memory\n'


def test_excluding_a_category_is_announced_before_restore(fake_install, tmp_path):
    result = bk.create_backup(categories={'transcripts': False, 'media': False})
    archive = Path(result['path'])
    assert 'minus-media-transcripts' in archive.name

    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read('manifest.json'))
    assert manifest['categories']['media'] is False
    assert manifest['categories']['transcripts'] is False

    announcement = bk.announcement_for(manifest)
    assert 'no media' in announcement
    assert 'no transcripts' in announcement

    report = bk.restore_backup(archive, dest_root=tmp_path / 'restored2')
    assert set(report['absent_categories']) == {'media', 'transcripts', 'vault'}
    assert 'media' not in report['restored_categories']
    assert 'transcripts' not in report['restored_categories']
    assert 'records' in report['restored_categories']


def test_unticking_every_category_refuses(fake_install):
    with pytest.raises(bk.BackupError):
        bk.create_backup(categories={c: False for c in bk.DEFAULT_CATEGORIES})


def test_tampered_archive_aborts_before_any_write(fake_install, tmp_path):
    result = bk.create_backup(categories={'unprotected': False, 'transcripts': False})
    archive = Path(result['path'])

    tampered = tmp_path / 'tampered.crbackup'
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(tampered, 'w') as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename.endswith('proj_a.json') and 'manifest' not in item.filename:
                data = data + b'CORRUPTED'
            dst.writestr(item, data)

    sandbox = tmp_path / 'should_stay_empty'
    with pytest.raises(bk.BackupIntegrityError):
        bk.restore_backup(tampered, dest_root=sandbox)
    assert not sandbox.exists() or not list(sandbox.rglob('*'))


def test_records_write_validation_refuses_stray_filename(fake_install, tmp_path):
    """A hand-edited archive claiming a file belongs in DATA_DIR under a name
    that isn't a known project id or sidecar suffix must be refused, not
    written — the tamper defense from spec §5. Exercised with dest_root=None
    (real-path mode) so the DATA_DIR check actually applies."""
    result = bk.create_backup(categories={'artifacts': False, 'media': False,
                                          'transcripts': False, 'unprotected': False})
    archive = Path(result['path'])

    with zipfile.ZipFile(archive) as zf:
        manifest = json.loads(zf.read('manifest.json'))
    arcname = next(k for k in manifest['files'] if k.endswith('proj_a.json'))
    manifest['files']['records/projects/evil_not_a_project.json'] = dict(manifest['files'][arcname])
    manifest['files']['records/projects/evil_not_a_project.json']['dest'] = str(
        Path(bk._data_root()) / 'data' / 'projects' / 'evil_not_a_project.json')

    poisoned = tmp_path / 'poisoned.crbackup'
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(poisoned, 'w') as dst:
        for item in src.infolist():
            if item.filename == 'manifest.json':
                continue
            dst.writestr(item, src.read(item.filename))
        payload = src.read(arcname)
        dst.writestr('records/projects/evil_not_a_project.json', payload)
        dst.writestr('manifest.json', json.dumps(manifest))

    report = bk.restore_backup(poisoned)  # real-path mode: dest_root=None
    assert report['per_category']['records']['refused'] >= 1
    stray = bk._data_root() / 'data' / 'projects' / 'evil_not_a_project.json'
    assert not stray.exists()
