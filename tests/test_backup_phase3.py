"""Tests for mc/backup.py Phase 3a — restore points (backend only,
docs/BACKUP_EXPORT_SPEC.md §4.3, build order §7 Phase 3).

Same fake-install isolation idiom as tests/test_backup.py and
tests/test_backup_phase2.py: env vars point MC_DATA_DIR / HOME / CLAYRUNE_HOME
at a temp tree so every call is self-contained.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import backup as bk  # noqa: E402


def _git(cwd, *args):
    subprocess.run(['git'] + list(args), cwd=str(cwd), check=True,
                   capture_output=True, text=True)


@pytest.fixture
def fake_install(tmp_path, monkeypatch):
    """One registered project ('proj_x') with a memory vault (index +
    topic file), AGENT_RULES.md, and a sidecar."""
    root = tmp_path / 'repo'
    home = tmp_path / 'home'
    clayrune = tmp_path / 'clayrune'
    (root / 'data' / 'projects').mkdir(parents=True)
    (home / '.claude' / 'agents').mkdir(parents=True)
    monkeypatch.setenv('MC_DATA_DIR', str(root))
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('CLAYRUNE_HOME', str(clayrune))

    checkout = tmp_path / 'checkout'
    checkout.mkdir()
    _git(checkout, 'init', '-q')
    _git(checkout, 'config', 'user.email', 'a@b.c')
    _git(checkout, 'config', 'user.name', 'test')
    (checkout / 'tracked.txt').write_text('tracked', encoding='utf-8')
    _git(checkout, 'add', 'tracked.txt')
    _git(checkout, 'commit', '-q', '-m', 'init')
    (checkout / 'AGENT_RULES.md').write_text('# rules v1\n', encoding='utf-8')

    (root / 'data' / 'projects' / 'proj_x.json').write_text(
        json.dumps({'id': 'proj_x', 'project_path': str(checkout), 'backlog': []}), encoding='utf-8')
    (root / 'data' / 'projects' / 'proj_x_agent_log.json').write_text(
        json.dumps({'events': ['v1']}), encoding='utf-8')

    encoded = bk._encode_project_path(str(checkout))
    mem_dir = home / '.claude' / 'projects' / encoded / 'memory'
    mem_dir.mkdir(parents=True)
    (mem_dir / 'MEMORY.md').write_text('# memory v1\n', encoding='utf-8')
    (mem_dir / 'MEMORY_ARCHIVE.md').write_text('# archive v1\n', encoding='utf-8')
    (mem_dir / 'topic_x.md').write_text('topic v1', encoding='utf-8')

    return {'root': root, 'home': home, 'clayrune': clayrune, 'checkout': checkout, 'mem_dir': mem_dir}


# ── Create / list ─────────────────────────────────────────────────────────

def test_create_captures_record_sidecar_memory_and_rules(fake_install):
    r = bk.create_restore_point('proj_x', label='before change')
    assert not r['warnings']
    root = Path(r['path'])
    assert (root / 'records' / 'proj_x.json').is_file()
    assert (root / 'records' / 'proj_x_agent_log.json').is_file()
    assert (root / 'memory' / 'MEMORY.md').read_text(encoding='utf-8') == '# memory v1\n'
    assert (root / 'memory' / 'MEMORY_ARCHIVE.md').read_text(encoding='utf-8') == '# archive v1\n'
    assert (root / 'memory' / 'topic_x.md').read_text(encoding='utf-8') == 'topic v1'
    assert (root / 'rules' / 'AGENT_RULES.md').read_text(encoding='utf-8') == '# rules v1\n'
    assert r['manifest']['label'] == 'before change'
    assert r['manifest']['pinned'] is False


def test_create_unknown_project_raises(fake_install):
    with pytest.raises(bk.BackupError):
        bk.create_restore_point('does_not_exist')


def test_no_mcp_json_in_a_restore_point(fake_install):
    """spec §4.3's list is record + sidecars + memory vault + AGENT_RULES.md
    only — no .mcp.json, unlike export_project's broader per-project slice."""
    (Path(bk._paths()['data_dir']).parent)  # sanity: paths resolve
    checkout = fake_install['checkout']
    (checkout / '.mcp.json').write_text('{}', encoding='utf-8')
    r = bk.create_restore_point('proj_x')
    root = Path(r['path'])
    assert not (root / 'mcp_project').exists()
    assert 'mcp_project' not in ' '.join(r['manifest']['files'].keys())


def test_list_returns_newest_first(fake_install):
    r1 = bk.create_restore_point('proj_x', label='first')
    r2 = bk.create_restore_point('proj_x', label='second')
    points = bk.list_restore_points('proj_x')
    assert [p['snap_id'] for p in points] == [r2['snap_id'], r1['snap_id']]


def test_list_unknown_project_returns_empty(fake_install):
    assert bk.list_restore_points('nope') == []


# ── Label / pin / delete ────────────────────────────────────────────────

def test_label_updates_without_renaming_directory(fake_install):
    r = bk.create_restore_point('proj_x', label='orig')
    snap_id = r['snap_id']
    updated = bk.label_restore_point('proj_x', snap_id, 'renamed')
    assert updated['label'] == 'renamed'
    # snap_id (and therefore the on-disk directory) is stable across a relabel.
    assert bk._restore_point_dir(bk._paths(), 'proj_x', snap_id).is_dir()
    points = bk.list_restore_points('proj_x')
    assert points[0]['label'] == 'renamed'
    assert points[0]['snap_id'] == snap_id


def test_pin_exempts_from_retention(fake_install):
    pinned = bk.create_restore_point('proj_x', label='keep-forever', pin=True)
    for i in range(bk.RETENTION_KEEP + 3):
        bk.create_restore_point('proj_x', label=f'churn-{i}')
    points = bk.list_restore_points('proj_x')
    ids = {p['snap_id'] for p in points}
    assert pinned['snap_id'] in ids, 'a pinned point must survive retention regardless of age'
    unpinned = [p for p in points if not p['pinned']]
    assert len(unpinned) == bk.RETENTION_KEEP


def test_pin_toggle_via_pin_restore_point(fake_install):
    r = bk.create_restore_point('proj_x')
    m = bk.pin_restore_point('proj_x', r['snap_id'], True)
    assert m['pinned'] is True
    m = bk.pin_restore_point('proj_x', r['snap_id'], False)
    assert m['pinned'] is False


def test_delete_removes_it(fake_install):
    r = bk.create_restore_point('proj_x')
    assert bk.delete_restore_point('proj_x', r['snap_id'])['deleted'] is True
    assert not Path(r['path']).exists()
    assert bk.list_restore_points('proj_x') == []


def test_delete_unknown_raises(fake_install):
    with pytest.raises(bk.BackupError):
        bk.delete_restore_point('proj_x', 'nope-not-real')


def test_label_unknown_raises(fake_install):
    with pytest.raises(bk.BackupError):
        bk.label_restore_point('proj_x', 'nope-not-real', 'x')


# ── Retention (spec §4.3: keep last N=10 + pins) ────────────────────────

def test_retention_prunes_oldest_unpinned_beyond_ten(fake_install):
    ids = [bk.create_restore_point('proj_x', label=f'p{i}')['snap_id']
          for i in range(bk.RETENTION_KEEP + 5)]
    points = bk.list_restore_points('proj_x')
    assert len(points) == bk.RETENTION_KEEP
    surviving = {p['snap_id'] for p in points}
    # the 5 oldest (created first) must be the ones pruned
    assert set(ids[:5]).isdisjoint(surviving)
    assert set(ids[5:]).issubset(surviving)


# ── Rollback: what it reverses, what it doesn't ─────────────────────────

def _mutate_project_state(fake_install):
    """Simulate 'what a session did since the restore point' — changes the
    record, the sidecar, both memory files, and AGENT_RULES.md."""
    root = fake_install['root']
    (root / 'data' / 'projects' / 'proj_x.json').write_text(
        json.dumps({'id': 'proj_x', 'project_path': str(fake_install['checkout']), 'backlog': ['new item']}),
        encoding='utf-8')
    (root / 'data' / 'projects' / 'proj_x_agent_log.json').write_text(
        json.dumps({'events': ['v1', 'v2']}), encoding='utf-8')
    mem_dir = fake_install['mem_dir']
    (mem_dir / 'MEMORY.md').write_text('# memory v2 (superseded stuff removed)\n', encoding='utf-8')
    (mem_dir / 'MEMORY_ARCHIVE.md').write_text('# archive v2\n', encoding='utf-8')
    (mem_dir / 'topic_x.md').write_text('topic v2', encoding='utf-8')
    (fake_install['checkout'] / 'AGENT_RULES.md').write_text('# rules v2\n', encoding='utf-8')


def test_rollback_restores_record_sidecar_topic_and_rules(fake_install):
    r = bk.create_restore_point('proj_x', label='snap1')
    _mutate_project_state(fake_install)

    report = bk.rollback('proj_x', r['snap_id'])
    assert not report['errors'], report['errors']
    assert not report['refused'], report['refused']
    assert report['restored']['record'] == 2  # proj_x.json + agent_log sidecar
    assert report['restored']['rules'] == 1
    assert report['restored']['memory_topic'] == 1
    assert report['restored']['memory_index'] == 0, 'index files must NOT restore by default'

    root = fake_install['root']
    rec = json.loads((root / 'data' / 'projects' / 'proj_x.json').read_text(encoding='utf-8'))
    assert rec['backlog'] == []
    sidecar = json.loads((root / 'data' / 'projects' / 'proj_x_agent_log.json').read_text(encoding='utf-8'))
    assert sidecar['events'] == ['v1']
    assert (fake_install['checkout'] / 'AGENT_RULES.md').read_text(encoding='utf-8') == '# rules v1\n'
    assert (fake_install['mem_dir'] / 'topic_x.md').read_text(encoding='utf-8') == 'topic v1'

    # Index files were left exactly as the mutation left them — untouched.
    assert (fake_install['mem_dir'] / 'MEMORY.md').read_text(encoding='utf-8') == \
        '# memory v2 (superseded stuff removed)\n'
    assert (fake_install['mem_dir'] / 'MEMORY_ARCHIVE.md').read_text(encoding='utf-8') == '# archive v2\n'


def test_rollback_with_restore_memory_index_true_also_restores_index(fake_install):
    r = bk.create_restore_point('proj_x', label='snap1')
    _mutate_project_state(fake_install)

    report = bk.rollback('proj_x', r['snap_id'], restore_memory_index=True)
    assert report['restored']['memory_index'] == 2
    assert (fake_install['mem_dir'] / 'MEMORY.md').read_text(encoding='utf-8') == '# memory v1\n'
    assert (fake_install['mem_dir'] / 'MEMORY_ARCHIVE.md').read_text(encoding='utf-8') == '# archive v1\n'


def test_rollback_creates_a_pre_rollback_snapshot(fake_install):
    r = bk.create_restore_point('proj_x', label='snap1')
    _mutate_project_state(fake_install)
    before_ids = {p['snap_id'] for p in bk.list_restore_points('proj_x')}

    report = bk.rollback('proj_x', r['snap_id'])
    assert 'pre_rollback_snapshot' in report
    after_ids = {p['snap_id'] for p in bk.list_restore_points('proj_x')}
    new_ids = after_ids - before_ids
    assert report['pre_rollback_snapshot'] in new_ids

    # And the pre-rollback snapshot really does carry the mutated (v2) state
    # — proving rollback is itself reversible.
    pre_manifest = bk._load_restore_point_manifest(bk._paths(), 'proj_x', report['pre_rollback_snapshot'])
    pre_root = bk._restore_point_dir(bk._paths(), 'proj_x', report['pre_rollback_snapshot'])
    assert (pre_root / 'memory' / 'MEMORY.md').read_text(encoding='utf-8') == \
        '# memory v2 (superseded stuff removed)\n'
    assert pre_manifest['project_id'] == 'proj_x'


def test_rollback_reports_cannot_reverse_as_structured_data(fake_install):
    r = bk.create_restore_point('proj_x')
    report = bk.rollback('proj_x', r['snap_id'])
    kinds = {c['kind'] for c in report['cannot_reverse']}
    assert kinds == {'repo', 'side_effects', 'global_state', 'memory_index'}
    for c in report['cannot_reverse']:
        assert isinstance(c['detail'], str) and c['detail']
    mem_entry = next(c for c in report['cannot_reverse'] if c['kind'] == 'memory_index')
    assert 'MEMORY.md' in mem_entry['diff']
    assert 'MEMORY_ARCHIVE.md' in mem_entry['diff']


def test_rollback_memory_index_diff_reflects_real_changes(fake_install):
    r = bk.create_restore_point('proj_x')
    _mutate_project_state(fake_install)
    report = bk.rollback('proj_x', r['snap_id'], dry_run=True)
    mem_entry = next(c for c in report['cannot_reverse'] if c['kind'] == 'memory_index')
    assert mem_entry['changed'] is True
    assert mem_entry['diff']['MEMORY.md']['identical'] is False
    assert 'memory v1' in mem_entry['diff']['MEMORY.md']['diff']
    assert 'memory v2' in mem_entry['diff']['MEMORY.md']['diff']


def test_rollback_repo_delta_reported(fake_install):
    r = bk.create_restore_point('proj_x')
    checkout = fake_install['checkout']
    (checkout / 'more.txt').write_text('x', encoding='utf-8')
    _git(checkout, 'add', 'more.txt')
    _git(checkout, 'commit', '-q', '-m', 'second commit')

    report = bk.rollback('proj_x', r['snap_id'])
    repo_entry = next(c for c in report['cannot_reverse'] if c['kind'] == 'repo')
    assert repo_entry['changed'] is True
    assert 'does not touch git' in repo_entry['detail']


# ── Dry run: no writes, real content ────────────────────────────────────

def test_dry_run_writes_nothing(fake_install):
    r = bk.create_restore_point('proj_x')
    _mutate_project_state(fake_install)

    before_ids = {p['snap_id'] for p in bk.list_restore_points('proj_x')}
    report = bk.rollback('proj_x', r['snap_id'], dry_run=True)
    assert report['dry_run'] is True
    assert 'pre_rollback_snapshot' not in report
    assert report['restored'] == {'record': 0, 'memory_topic': 0, 'memory_index': 0, 'rules': 0}
    assert set(report['would_restore']) == {'record', 'memory_topic', 'rules'}

    root = fake_install['root']
    rec = json.loads((root / 'data' / 'projects' / 'proj_x.json').read_text(encoding='utf-8'))
    assert rec['backlog'] == ['new item'], 'dry_run must not touch live state'
    after_ids = {p['snap_id'] for p in bk.list_restore_points('proj_x')}
    assert after_ids == before_ids, 'dry_run must not create a pre-rollback snapshot either'


def test_dry_run_would_restore_includes_memory_index_when_requested(fake_install):
    r = bk.create_restore_point('proj_x')
    report = bk.rollback('proj_x', r['snap_id'], restore_memory_index=True, dry_run=True)
    assert 'memory_index' in report['would_restore']


# ── Attended-only gate ───────────────────────────────────────────────────

def test_rollback_refuses_unattended(fake_install):
    r = bk.create_restore_point('proj_x')
    with pytest.raises(bk.BackupError):
        bk.rollback('proj_x', r['snap_id'], unattended=True)


def test_rollback_dry_run_allowed_unattended(fake_install):
    r = bk.create_restore_point('proj_x')
    report = bk.rollback('proj_x', r['snap_id'], unattended=True, dry_run=True)
    assert report['dry_run'] is True


def test_rollback_unknown_snapshot_raises(fake_install):
    with pytest.raises(bk.BackupError):
        bk.rollback('proj_x', 'does-not-exist')


# ── Tamper defense: sha256 must match before any write ──────────────────

def test_rollback_aborts_before_any_write_on_hash_mismatch(fake_install):
    r = bk.create_restore_point('proj_x')
    _mutate_project_state(fake_install)
    root = Path(r['path'])
    (root / 'records' / 'proj_x.json').write_bytes(
        (root / 'records' / 'proj_x.json').read_bytes() + b'CORRUPTED')

    with pytest.raises(bk.BackupIntegrityError):
        bk.rollback('proj_x', r['snap_id'])

    live = json.loads((fake_install['root'] / 'data' / 'projects' / 'proj_x.json').read_text(encoding='utf-8'))
    assert live['backlog'] == ['new item'], 'a hash mismatch must abort before any write, including rules/memory'
    assert (fake_install['checkout'] / 'AGENT_RULES.md').read_text(encoding='utf-8') == '# rules v2\n'


# ── DATA_DIR tamper defense (mirrors test_backup.py's records-write test) ──

def test_rollback_refuses_stray_records_filename(fake_install, monkeypatch):
    r = bk.create_restore_point('proj_x')
    root = Path(r['path'])
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))

    payload = (root / 'records' / 'proj_x.json').read_bytes()
    evil_path = root / 'records' / 'evil_not_a_project.json'
    evil_path.write_bytes(payload)
    manifest['files']['records/evil_not_a_project.json'] = {
        'sha256': bk._sha256_bytes(payload), 'bytes': len(payload), 'category': 'record'}
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')

    report = bk.rollback('proj_x', r['snap_id'])
    assert any('evil_not_a_project' in msg for msg in report['refused'])
    stray = bk._paths()['data_dir'] / 'evil_not_a_project.json'
    assert not stray.exists()
