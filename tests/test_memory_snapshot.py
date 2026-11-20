"""mc/memory_snapshot.py: the daily copy of every project's memory directory.

A temporary home and temporary memory directories; the clock is the `now`
argument, so nothing sleeps. Covers: a daily run writes and verifies; day 15
prunes exactly the oldest automatic snapshot; manual snapshots survive; a
failed or unverified snapshot prunes nothing and is not counted; disabled means
no write; an unusable destination logs once per day and writes nothing
anywhere; no pass while a restore is in progress; the settings route refuses
bad values.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import memory_snapshot as ms  # noqa: E402
from mc import state as _state  # noqa: E402

D0 = datetime(2026, 1, 1, 3, 0, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)
HOUR = timedelta(hours=1)


def _tree(root: Path) -> list[str]:
    """Every file and directory under root, relative, sorted."""
    return sorted(str(p.relative_to(root)).replace('\\', '/') for p in root.rglob('*'))


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.setenv('USERPROFILE', str(home))
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.delenv('MC_DATA_DIR', raising=False)
    assert Path.home() == home
    logs: list[str] = []
    monkeypatch.setattr(ms, '_say', logs.append)
    monkeypatch.setattr(ms, '_dest_error_logged', {})
    monkeypatch.setattr(ms, '_stale_marker_logged', set())
    monkeypatch.setattr(ms, '_restores', 0)
    monkeypatch.setattr(ms, '_extra_forbidden', [])
    mem = tmp_path / 'mem'

    def add(pid: str, with_index: bool = True) -> dict:
        d = mem / pid
        d.mkdir(parents=True)
        if with_index:
            (d / 'MEMORY.md').write_text(f'index of {pid}', encoding='utf-8')
            (d / 'MEMORY_ARCHIVE.md').write_text('archive', encoding='utf-8')
        (d / 'a_topic.md').write_text('topic', encoding='utf-8')
        (d / 'delivery_stats.json').write_text('{}', encoding='utf-8')
        return {'id': pid, 'project_path': str(tmp_path / 'src' / pid)}

    def path_fn(p: dict) -> Path:
        return mem / p['id'] / 'MEMORY.md'

    def run(now, projects, config=None):
        return ms.run_once(now, config=config or {}, projects=projects, memory_path_fn=path_fn)

    return SimpleNamespace(home=home, mem=mem, logs=logs, add=add, tmp=tmp_path, path_fn=path_fn,
                           run=run, root=home / '.clayrune' / 'memory-snapshots')


def _names(root: Path) -> list[str]:
    return sorted(d.name for d in root.iterdir()) if root.is_dir() else []


# ── a daily run writes and verifies ──────────────────────────────────────────

def test_daily_run_writes_and_verifies(env):
    alpha = env.add('alpha')
    report = env.run(D0, [alpha])
    assert report['ran'] is True and report['projects']['alpha']['ok'] is True
    assert _names(env.root) == ['20260101T030000Z-auto-alpha']
    snap = env.root / '20260101T030000Z-auto-alpha'
    assert ms.verify_snapshot(snap) == {'ok': True, 'good': 3, 'count': 3, 'problems': []}
    man = json.loads((snap / 'manifest.json').read_text(encoding='utf-8'))
    # Copy order is load-bearing, and a snapshot holds the *.md files only.
    assert [f['name'] for f in man['files']] == ['MEMORY.md', 'MEMORY_ARCHIVE.md', 'a_topic.md']
    assert sorted(p.name for p in (snap / 'memory').iterdir()) == ['MEMORY.md', 'MEMORY_ARCHIVE.md', 'a_topic.md']
    assert man['label'] == 'auto-alpha'
    state = ms.load_state()
    assert state['projects']['alpha']['last_success_at'] == '2026-01-01T03:00:00Z'
    assert any('alpha' in line and 'wrote and verified' in line for line in env.logs)


def test_runs_once_per_day_per_project(env):
    alpha = env.add('alpha')
    env.run(D0, [alpha])
    assert env.run(D0 + 23 * HOUR, [alpha]) == {'ran': False, 'reason': 'not_due'}
    assert len(_names(env.root)) == 1
    assert env.run(D0 + DAY, [alpha])['ran'] is True
    assert len(_names(env.root)) == 2


def test_every_project_with_a_memory_directory_and_only_those(env):
    alpha, beta = env.add('alpha'), env.add('beta')
    no_index = env.add('gamma', with_index=False)          # directory, no MEMORY.md
    no_path = {'id': 'delta', 'project_path': ''}           # flat fallback file, no directory
    report = env.run(D0, [alpha, beta, no_index, no_path])
    assert sorted(report['projects']) == ['alpha', 'beta']
    assert _names(env.root) == ['20260101T030000Z-auto-alpha', '20260101T030000Z-auto-beta']
    assert not any('gamma' in line or 'delta' in line for line in env.logs)


def test_an_unsafe_project_id_gets_its_own_label():
    assert ms.auto_label('alpha') == 'auto-alpha'
    a, b = ms.auto_label('a b'), ms.auto_label('a_b')
    assert a != b and a.startswith('auto-a_b-') and b == 'auto-a_b'


# ── retention ────────────────────────────────────────────────────────────────

def _fourteen_days(env, project) -> list[str]:
    for i in range(14):
        assert env.run(D0 + i * DAY, [project])['projects'][project['id']]['ok'] is True
    names = _names(env.root)
    assert len(names) == 14
    return names


def test_day_15_prunes_exactly_the_oldest_automatic_snapshot(env):
    alpha = env.add('alpha')
    before = _fourteen_days(env, alpha)
    report = env.run(D0 + 14 * DAY, [alpha])
    assert report['projects']['alpha']['removed'] == ['20260101T030000Z-auto-alpha']
    after = _names(env.root)
    assert len(after) == 14
    assert set(before) - set(after) == {'20260101T030000Z-auto-alpha'}
    assert set(after) - set(before) == {'20260115T030000Z-auto-alpha'}


def test_keep_setting_is_honoured(env):
    alpha = env.add('alpha')
    for i in range(5):
        env.run(D0 + i * DAY, [alpha], {'memory_snapshot_keep': 3})
    assert _names(env.root) == ['20260103T030000Z-auto-alpha', '20260104T030000Z-auto-alpha',
                                '20260105T030000Z-auto-alpha']


def test_manual_and_other_projects_snapshots_survive(env):
    alpha, beta = env.add('alpha'), env.add('beta')
    env.root.mkdir(parents=True)
    old = datetime(2025, 6, 1, tzinfo=timezone.utc)
    src = env.mem / 'alpha'
    keepers = [
        ms.create_snapshot(src, env.root, 'manual', now=old)[0].name,
        ms.create_snapshot(src, env.root, 'pre-s3', now=old)[0].name,
        ms.create_snapshot(src, env.root, 'x-auto-alpha', now=old)[0].name,   # ends like ours, is not
        ms.create_snapshot(src, env.root, 'auto-alpha2', now=old)[0].name,    # another id's label
    ]
    (env.root / 'notes.txt').write_text('mine', encoding='utf-8')
    env.run(old, [beta])                                                       # beta's only snapshot, very old
    for i in range(16):
        env.run(D0 + i * DAY, [alpha])
    names = _names(env.root)
    for k in keepers + ['20250601T000000Z-auto-beta', 'notes.txt']:
        assert k in names, k
    assert len([n for n in names if n.endswith('-auto-alpha') and not n.endswith('x-auto-alpha')]) == 14


def test_a_failed_write_prunes_nothing_and_leaves_no_debris(env, monkeypatch):
    alpha = env.add('alpha')
    before = _fourteen_days(env, alpha)
    real_copy = ms.shutil.copy2
    calls = []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise OSError('disk full')
        return real_copy(src, dst)
    monkeypatch.setattr(ms.shutil, 'copy2', flaky)
    report = env.run(D0 + 14 * DAY, [alpha])
    assert report['projects']['alpha']['ok'] is False
    assert _names(env.root) == before                      # nothing pruned, no half snapshot left
    assert any('alpha' in line and 'disk full' in line for line in env.logs)
    rec = ms.load_state()['projects']['alpha']
    assert rec['last_status'] == 'error' and 'disk full' in rec['last_error']
    assert rec['last_success_at'] == '2026-01-14T03:00:00Z'
    # The failure does not cost a day: the next hourly tick tries again.
    monkeypatch.setattr(ms.shutil, 'copy2', real_copy)
    assert env.run(D0 + 14 * DAY + HOUR, [alpha])['projects']['alpha']['ok'] is True


def test_an_unverified_copy_prunes_nothing(env, monkeypatch):
    alpha = env.add('alpha')
    before = _fourteen_days(env, alpha)
    real_verify = ms.verify_snapshot

    def verify(snap):
        if Path(snap).name.startswith('20260115'):
            return {'ok': False, 'good': 2, 'count': 3, 'problems': [('mismatch', 'a_topic.md')]}
        return real_verify(snap)
    monkeypatch.setattr(ms, 'verify_snapshot', verify)
    report = env.run(D0 + 14 * DAY, [alpha])
    assert report['projects']['alpha']['ok'] is False
    assert _names(env.root) == before
    assert any('alpha' in line and 'did not verify' in line and 'a_topic.md' in line for line in env.logs)


def test_a_snapshot_that_no_longer_verifies_is_not_counted(env):
    alpha = env.add('alpha')
    before = _fourteen_days(env, alpha)
    (env.root / '20260106T030000Z-auto-alpha' / 'memory' / 'a_topic.md').write_text('rot', encoding='utf-8')
    report = env.run(D0 + 14 * DAY, [alpha])
    # 13 good old ones + the new one = 14: nothing is over the limit.
    assert report['projects']['alpha']['removed'] == []
    assert _names(env.root) == sorted(before + ['20260115T030000Z-auto-alpha'])


# ── disabled / destination ───────────────────────────────────────────────────

def test_disabled_means_no_write(env):
    alpha = env.add('alpha')
    assert env.run(D0, [alpha], {'memory_snapshot_enabled': False}) == {'ran': False, 'reason': 'disabled'}
    assert _tree(env.home) == []
    assert env.logs == []


def test_a_missing_destination_logs_once_a_day_and_writes_nothing_anywhere(env):
    alpha = env.add('alpha')
    gone = env.tmp / 'unplugged' / 'snaps'
    cfg = {'memory_snapshot_dest_dir': str(gone)}
    for hour in range(3):
        report = env.run(D0 + hour * HOUR, [alpha], cfg)
        assert report['ran'] is False and report['reason'] == 'destination'
    assert len(env.logs) == 1 and 'does not exist' in env.logs[0] and 'nothing written' in env.logs[0]
    env.run(D0 + DAY, [alpha], cfg)
    assert len(env.logs) == 2
    assert not gone.exists() and not gone.parent.exists()
    assert _tree(env.home) == []                           # no fallback to the default, no state file


def test_an_unwritable_destination_logs_and_writes_nothing_anywhere(env, monkeypatch):
    alpha = env.add('alpha')
    dest = env.tmp / 'readonly'
    dest.mkdir()

    def refuse(d):
        raise PermissionError(13, 'Permission denied', str(d))
    monkeypatch.setattr(ms, '_probe_writable', refuse)
    report = env.run(D0, [alpha], {'memory_snapshot_dest_dir': str(dest)})
    assert report['ran'] is False and report['reason'] == 'destination'
    assert len(env.logs) == 1 and 'not writable' in env.logs[0]
    assert _tree(dest) == [] and _tree(env.home) == []


def test_a_destination_that_is_a_file_writes_nothing(env):
    alpha = env.add('alpha')
    dest = env.tmp / 'a-file'
    dest.write_text('x', encoding='utf-8')
    assert env.run(D0, [alpha], {'memory_snapshot_dest_dir': str(dest)})['reason'] == 'destination'
    assert dest.read_text(encoding='utf-8') == 'x' and _tree(env.home) == []


@pytest.mark.parametrize('inside', ['repo', 'data_projects', 'wired'])
def test_a_destination_inside_the_repo_or_data_projects_is_refused(env, monkeypatch, inside):
    alpha = env.add('alpha')
    if inside == 'repo':
        dest = ms._REPO_ROOT / 'tests'
    elif inside == 'data_projects':
        monkeypatch.setenv('MC_DATA_DIR', str(env.tmp / 'install'))
        dest = env.tmp / 'install' / 'data' / 'projects'
        dest.mkdir(parents=True)
    else:
        dest = env.tmp / 'wired-data' / 'projects' / 'deeper'
        dest.mkdir(parents=True)
        monkeypatch.setattr(ms, '_extra_forbidden', [env.tmp / 'wired-data' / 'projects'])
    before = _tree(dest)
    report = env.run(D0, [alpha], {'memory_snapshot_dest_dir': str(dest)})
    assert report['reason'] == 'destination' and 'may not live in the repo' in report['detail']
    assert _tree(dest) == before and _tree(env.home) == []


def test_a_configured_destination_is_used(env):
    alpha = env.add('alpha')
    dest = env.tmp / 'second-disk'
    dest.mkdir()
    env.run(D0, [alpha], {'memory_snapshot_dest_dir': str(dest)})
    assert _names(dest) == ['20260101T030000Z-auto-alpha']
    assert not env.root.exists()


# ── restore in progress ──────────────────────────────────────────────────────

def test_no_pass_while_a_restore_is_in_progress(env):
    alpha = env.add('alpha')
    with ms.restoring('test restore'):
        assert ms.restore_in_progress() is True
        assert env.run(D0, [alpha]) == {'ran': False, 'reason': 'restore_in_progress'}
        assert not env.root.exists()
    assert ms.restore_in_progress() is False
    assert env.run(D0, [alpha])['ran'] is True


def test_another_process_restore_marker_blocks_and_a_stale_one_does_not(env):
    alpha = env.add('alpha')
    marker = env.home / '.clayrune' / 'memory-restore-active' / '4242-abcd1234'
    marker.parent.mkdir(parents=True)
    marker.write_text('other process', encoding='utf-8')
    assert env.run(D0, [alpha])['reason'] == 'restore_in_progress'
    old = marker.stat().st_mtime - ms.RESTORE_MARKER_STALE_S - 60
    os.utime(marker, (old, old))
    assert env.run(D0, [alpha])['ran'] is True
    assert sum('ignoring restore marker 4242-abcd1234' in line for line in env.logs) == 1


def test_a_restore_that_raises_still_clears_the_mark(env):
    with pytest.raises(RuntimeError):
        with ms.restoring('boom'):
            raise RuntimeError('restore failed')
    assert ms.restore_in_progress() is False
    assert _tree(env.home / '.clayrune' / 'memory-restore-active') == []


# The three server routes that write a memory directory from a backup hold the
# mark for exactly as long as the write runs; a dry run does not take it.

@pytest.fixture
def backup_client(env, monkeypatch):
    from mc.blueprints import backup_routes
    seen: list[tuple[str, bool]] = []

    def stub(name, result):
        def fn(*a, **kw):
            seen.append((name if not kw.get('dry_run') else name + ':dry', ms.restore_in_progress()))
            return result
        return fn
    monkeypatch.setattr(backup_routes, '_require_human_passcode', lambda data: None)
    monkeypatch.setattr(backup_routes, '_is_unattended', lambda *a: False)
    monkeypatch.setattr(backup_routes._backup, 'restore_backup', stub('restore', {'restored_categories': []}))
    monkeypatch.setattr(backup_routes._backup, 'import_project', stub('import', {}))
    monkeypatch.setattr(backup_routes._backup, 'rollback', stub('rollback', {}))
    app = Flask(__name__)
    app.register_blueprint(backup_routes.bp)
    return app.test_client(), seen


def test_restore_import_and_rollback_routes_hold_the_mark(backup_client):
    client, seen = backup_client
    assert client.post('/api/backup/restore', json={'path': 'x.crbackup'}).status_code == 200
    assert client.post('/api/backup/import', json={'path': 'x.crbackup', 'apply': True}).status_code == 200
    assert client.post('/api/backup/rollback/alpha/snap1', json={}).status_code == 200
    assert client.post('/api/backup/rollback/alpha/snap1', json={'dry_run': True}).status_code == 200
    assert seen == [('restore', True), ('import', True), ('rollback', True), ('rollback:dry', False)]
    assert ms.restore_in_progress() is False


def test_a_route_restore_that_fails_clears_the_mark(backup_client, monkeypatch):
    from mc.blueprints import backup_routes
    client, _seen = backup_client

    def boom(*a, **kw):
        raise backup_routes._backup.BackupError('bad archive')
    monkeypatch.setattr(backup_routes._backup, 'restore_backup', boom)
    assert client.post('/api/backup/restore', json={'path': 'x.crbackup'}).status_code == 400
    assert ms.restore_in_progress() is False


# ── never raises ─────────────────────────────────────────────────────────────

def test_a_pass_never_raises(env, monkeypatch):
    def boom():
        raise RuntimeError('projects unreadable')
    monkeypatch.setattr(ms, '_projects_fn', boom)
    monkeypatch.setattr(ms, '_memory_path_fn', env.path_fn)
    report = ms.run_once(D0, config={})
    assert report['ran'] is False and report['reason'] == 'error'
    assert any('projects unreadable' in line for line in env.logs)


def test_one_project_failing_does_not_stop_the_others(env):
    alpha, beta = env.add('alpha'), env.add('beta')

    def path_fn(p):
        if p['id'] == 'alpha':
            raise OSError('bad path')
        return env.path_fn(p)
    report = ms.run_once(D0, config={}, projects=[alpha, beta], memory_path_fn=path_fn)
    assert sorted(report['projects']) == ['beta']
    assert any('alpha' in line and 'bad path' in line for line in env.logs)


def test_importing_or_unwired_start_runs_nothing(env, monkeypatch):
    monkeypatch.setattr(ms, '_projects_fn', None)
    started = []
    monkeypatch.setattr(ms.threading, 'Thread', lambda **kw: started.append(kw))
    ms.start()
    assert started == []


# ── PUT /api/config validates the three settings ─────────────────────────────

@pytest.fixture
def config_client(env, monkeypatch):
    from mc.blueprints import settings_routes
    monkeypatch.setattr(_state, 'CONFIG', {})
    app = Flask(__name__)
    app.register_blueprint(settings_routes.bp)
    settings_routes.wire(
        config_path=env.tmp / 'config.json',
        projects_base=env.tmp / 'projects',
        settings_path=env.tmp / 'settings.json',
    )
    # The passcode gate has its own tests (tests/test_human_proof_guard.py).
    monkeypatch.setattr(settings_routes, '_require_human_passcode', lambda data: None)
    return app.test_client()


def test_config_put_accepts_the_three_settings(config_client, env):
    dest = env.tmp / 'second-disk'
    resp = config_client.put('/api/config', json={
        'memory_snapshot_enabled': False, 'memory_snapshot_keep': 7,
        'memory_snapshot_dest_dir': f'  {dest}  '})
    assert resp.status_code == 200, resp.get_json()
    assert _state.CONFIG['memory_snapshot_enabled'] is False
    assert _state.CONFIG['memory_snapshot_keep'] == 7
    assert _state.CONFIG['memory_snapshot_dest_dir'] == str(dest)
    assert config_client.put('/api/config', json={'memory_snapshot_dest_dir': ''}).status_code == 200
    assert _state.CONFIG['memory_snapshot_dest_dir'] == ''


@pytest.mark.parametrize('body', [
    {'memory_snapshot_enabled': 'no'},
    {'memory_snapshot_keep': 0}, {'memory_snapshot_keep': 'ten'}, {'memory_snapshot_keep': True},
    {'memory_snapshot_keep': 1.5},
    {'memory_snapshot_dest_dir': 7},
    {'memory_snapshot_dest_dir': str(ms._REPO_ROOT / 'data' / 'projects')},
    {'memory_snapshot_dest_dir': str(ms._REPO_ROOT / 'docs')},
])
def test_config_put_refuses_bad_values(config_client, body):
    resp = config_client.put('/api/config', json=body)
    assert resp.status_code == 400, resp.get_json()
    assert not any(k in _state.CONFIG for k in body)
