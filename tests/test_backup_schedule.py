"""Tests for scheduled auto-backup + retention (MC-983, BACKUP_EXPORT_SPEC.md
§7 Phase 4): overdue calc, catch-up-once semantics, retention pruning only
scheduled archives, failure notifying without deleting anything, the
Phase-1 vault marker on a scheduled run, and config validation for the two
new keys.

Same fake-install isolation idiom as tests/test_backup_dest_dir.py: env vars
point MC_DATA_DIR / HOME / CLAYRUNE_HOME at a temp tree, state.CONFIG is
reset per test via monkeypatch.setattr.
"""
from __future__ import annotations

import json
import sys
import zipfile
from datetime import datetime, timedelta, timezone
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
    monkeypatch.setattr(bk, 'REPO_ROOT', root)
    return {'root': root, 'home': home, 'clayrune': clayrune}


# ── is_backup_overdue ────────────────────────────────────────────────────────

def test_overdue_off_or_unset_cadence_never_due():
    assert bk.is_backup_overdue(None, None) is False
    assert bk.is_backup_overdue('off', None) is False
    assert bk.is_backup_overdue('bogus', None) is False


def test_overdue_never_run_before_is_due_immediately():
    assert bk.is_backup_overdue('daily', None) is True
    assert bk.is_backup_overdue('weekly', None) is True


def test_overdue_unparseable_last_run_is_due():
    assert bk.is_backup_overdue('daily', 'not-a-timestamp') is True


def test_overdue_daily_math():
    now = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
    just_under = (now - timedelta(hours=23, minutes=59)).strftime('%Y-%m-%dT%H:%M:%SZ')
    just_over = (now - timedelta(days=1, minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    assert bk.is_backup_overdue('daily', just_under, now=now) is False
    assert bk.is_backup_overdue('daily', just_over, now=now) is True


def test_overdue_weekly_math():
    now = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
    just_under = (now - timedelta(days=6, hours=23)).strftime('%Y-%m-%dT%H:%M:%SZ')
    just_over = (now - timedelta(days=7, minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    assert bk.is_backup_overdue('weekly', just_under, now=now) is False
    assert bk.is_backup_overdue('weekly', just_over, now=now) is True


def test_overdue_a_machine_off_for_weeks_is_still_just_due_once():
    """A gap of any size beyond the interval reports the SAME due=True as a
    gap of exactly one interval — the caller (the daemon loop) runs at most
    one backup per check either way, so a week-long outage never queues up
    multiple catch-up runs."""
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)
    ancient = (now - timedelta(days=200)).strftime('%Y-%m-%dT%H:%M:%SZ')
    barely = (now - timedelta(days=1, seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    assert bk.is_backup_overdue('daily', ancient, now=now) is True
    assert bk.is_backup_overdue('daily', barely, now=now) is True


# ── next_run_at ──────────────────────────────────────────────────────────────

def test_next_run_at_none_without_cadence_or_history():
    assert bk.next_run_at(None, None) is None
    assert bk.next_run_at('off', '2026-09-01T00:00:00Z') is None
    assert bk.next_run_at('daily', None) is None


def test_next_run_at_computed():
    last = '2026-09-25T12:00:00Z'
    assert bk.next_run_at('daily', last) == '2026-09-26T12:00:00Z'
    assert bk.next_run_at('weekly', last) == '2026-10-02T12:00:00Z'


# ── catch-up-once: run once, then no longer overdue ─────────────────────────

def test_catch_up_runs_once_then_not_overdue(fake_install, tmp_path):
    """Simulates the daemon's own tick logic (is_backup_overdue → run →
    persist last_run_at) without the thread/sleep loop: a machine off for
    200 days is overdue, a single create_backup(scheduled=True) plus a
    schedule-state save clears the overdue flag immediately — no second run
    fires on the very next check."""
    dest = tmp_path / 'dest'
    _state.CONFIG['backup_dest_dir'] = str(dest)
    state = {'last_run_at': (datetime.now(timezone.utc) - timedelta(days=200))
             .strftime('%Y-%m-%dT%H:%M:%SZ')}
    assert bk.is_backup_overdue('daily', state['last_run_at']) is True

    result = bk.create_backup(categories={'records': True}, dest_dir=dest, scheduled=True)
    state['last_run_at'] = result['manifest']['created_at']
    bk.save_schedule_state(state)

    assert bk.is_backup_overdue('daily', state['last_run_at']) is False
    # A second identical check (as the next hourly tick would do) agrees.
    reloaded = bk.load_schedule_state()
    assert bk.is_backup_overdue('daily', reloaded['last_run_at']) is False


def test_schedule_state_persists_outside_data_dir(fake_install):
    """CLAUDE.md's DATA_DIR-pollution rule: schedule state must not live
    under data/projects/ (which load_projects() would misread as a
    project) — it lives beside the archives in ~/.clayrune."""
    bk.save_schedule_state({'last_run_at': '2026-09-26T00:00:00Z'})
    assert bk._schedule_state_path().parent == fake_install['clayrune']
    assert not (fake_install['root'] / 'data' / 'projects' / 'backup_schedule_state.json').exists()


def test_schedule_state_missing_file_is_never_run(fake_install):
    assert bk.load_schedule_state() == {}


# ── vault marker on a scheduled run (Phase 1 has no vault category at all
#    for whole-install backup, scheduled or manual — spec §7 Phase 1) ───────

def test_scheduled_backup_never_includes_vault(fake_install, tmp_path):
    dest = tmp_path / 'dest'
    result = bk.create_backup(categories={'records': True}, dest_dir=dest, scheduled=True)
    manifest = result['manifest']
    assert manifest['vault_status'] == 'not_available'
    assert manifest['categories']['vault'] is False
    assert manifest['contains_secrets'] is False
    assert manifest['scheduled'] is True


def test_manual_backup_is_not_marked_scheduled(fake_install, tmp_path):
    dest = tmp_path / 'dest'
    result = bk.create_backup(categories={'records': True}, dest_dir=dest)
    assert result['manifest']['scheduled'] is False


# ── prune_scheduled_backups: only scheduled archives, only beyond `keep` ────

def _make_archive(dest: Path, name: str, *, scheduled: bool, created_at: str) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    p = dest / name
    with zipfile.ZipFile(p, 'w') as zf:
        zf.writestr('manifest.json', json.dumps({
            'scheduled': scheduled, 'created_at': created_at,
        }))
    return p


def test_prune_keeps_newest_n_scheduled_only(tmp_path):
    dest = tmp_path / 'dest'
    manual = _make_archive(dest, 'manual.crbackup', scheduled=False,
                           created_at='2026-01-01T00:00:00Z')
    sched = [
        _make_archive(dest, f'sched-{i}.crbackup', scheduled=True,
                     created_at=f'2026-0{i}-01T00:00:00Z')
        for i in range(1, 6)  # 5 scheduled archives, Jan..May
    ]
    removed = bk.prune_scheduled_backups(dest, keep=2)
    remaining = {f.name for f in dest.glob('*.crbackup')}

    assert manual.exists()  # never touched, regardless of age
    # Newest 2 scheduled (April, May) survive; Jan/Feb/Mar are gone.
    assert remaining == {'manual.crbackup', 'sched-4.crbackup', 'sched-5.crbackup'}
    assert len(removed) == 3
    assert all(not Path(r['path']).exists() for r in removed)


def test_prune_never_touches_manual_archives_even_when_over_keep(tmp_path):
    dest = tmp_path / 'dest'
    manuals = [_make_archive(dest, f'manual-{i}.crbackup', scheduled=False,
                             created_at=f'2026-0{i}-01T00:00:00Z') for i in range(1, 4)]
    removed = bk.prune_scheduled_backups(dest, keep=1)
    assert removed == []
    assert all(m.exists() for m in manuals)


def test_prune_keep_floors_at_one(tmp_path):
    dest = tmp_path / 'dest'
    for i in range(1, 4):
        _make_archive(dest, f'sched-{i}.crbackup', scheduled=True,
                      created_at=f'2026-0{i}-01T00:00:00Z')
    removed = bk.prune_scheduled_backups(dest, keep=0)
    remaining = {f.name for f in dest.glob('*.crbackup')}
    assert len(remaining) == 1  # keep=0 treated as keep=1, never zero
    assert len(removed) == 2


def test_prune_empty_dir_is_a_no_op(tmp_path):
    assert bk.prune_scheduled_backups(tmp_path / 'does-not-exist', keep=3) == []


def test_prune_leaves_unreadable_archive_alone(tmp_path):
    dest = tmp_path / 'dest'
    dest.mkdir(parents=True)
    bad = dest / 'corrupt.crbackup'
    bad.write_bytes(b'not a zip file')
    removed = bk.prune_scheduled_backups(dest, keep=1)
    assert removed == []
    assert bad.exists()


# ── _run_scheduled_backup_once (mc/blueprints/backup_routes.py) ────────────

@pytest.fixture
def routes(fake_install):
    from mc.blueprints import backup_routes
    return backup_routes


def test_run_scheduled_backup_once_success_persists_state_and_prunes(
        fake_install, tmp_path, monkeypatch, routes):
    dest = tmp_path / 'dest'
    _state.CONFIG['backup_dest_dir'] = str(dest)
    _state.CONFIG['backup_keep'] = 2

    # Seed 3 pre-existing scheduled archives so this run's retention pass has
    # something to prune (the new one just written becomes newest).
    for i in range(1, 4):
        _make_archive(dest, f'sched-{i}.crbackup', scheduled=True,
                      created_at=f'2026-0{i}-01T00:00:00Z')

    notified = []
    monkeypatch.setattr(routes, '_notify_backup_failure', lambda reason: notified.append(reason))

    routes._run_scheduled_backup_once()

    state = bk.load_schedule_state()
    assert state['last_status'] == 'success'
    assert state['last_error'] is None
    assert state['last_run_at']
    assert state['last_success_at'] == state['last_run_at']
    assert state['consecutive_failures'] == 0
    assert notified == []  # success never notifies

    remaining = sorted(dest.glob('*.crbackup'))
    assert len(remaining) == 2  # backup_keep=2: newest new run + 1 prior


def test_run_scheduled_backup_once_success_resets_a_prior_failure_streak(
        fake_install, tmp_path, monkeypatch, routes):
    dest = tmp_path / 'dest'
    _state.CONFIG['backup_dest_dir'] = str(dest)
    bk.save_schedule_state({'consecutive_failures': 4, 'last_notified_error': 'disk full',
                           'last_status': 'error', 'last_error': 'disk full'})
    monkeypatch.setattr(routes, '_notify_backup_failure', lambda reason: None)

    routes._run_scheduled_backup_once()

    state = bk.load_schedule_state()
    assert state['last_status'] == 'success'
    assert state['consecutive_failures'] == 0
    assert 'last_notified_error' not in state


def test_run_scheduled_backup_once_failure_notifies_and_deletes_nothing(
        fake_install, tmp_path, monkeypatch, routes):
    dest = tmp_path / 'dest'
    _state.CONFIG['backup_dest_dir'] = str(dest)
    _state.CONFIG['backup_keep'] = 1

    existing = [_make_archive(dest, f'sched-{i}.crbackup', scheduled=True,
                              created_at=f'2026-0{i}-01T00:00:00Z') for i in range(1, 3)]

    def _boom(*a, **kw):
        raise bk.BackupError('disk full')
    monkeypatch.setattr(bk, 'create_backup', _boom)

    notified = []
    monkeypatch.setattr(routes, '_notify_backup_failure', lambda reason: notified.append(reason))

    routes._run_scheduled_backup_once()

    state = bk.load_schedule_state()
    assert state['last_status'] == 'error'
    assert 'disk full' in state['last_error']
    assert state['consecutive_failures'] == 1
    assert 'last_success_at' not in state  # no success has ever happened
    assert len(notified) == 1
    assert 'disk full' in notified[0]
    # Failure never deletes: both pre-existing archives are untouched.
    assert all(f.exists() for f in existing)


def test_failure_then_next_tick_is_still_overdue(fake_install, tmp_path, monkeypatch, routes):
    """The bug the review caught: overdue must be computed off
    last_success_at, so a failed daily/weekly backup is retried on the very
    next hourly tick, not stalled for a whole cadence."""
    dest = tmp_path / 'dest'
    _state.CONFIG['backup_dest_dir'] = str(dest)
    monkeypatch.setattr(bk, 'create_backup', lambda *a, **kw: (_ for _ in ()).throw(bk.BackupError('disk full')))
    monkeypatch.setattr(routes, '_notify_backup_failure', lambda reason: None)

    routes._run_scheduled_backup_once()

    state = bk.load_schedule_state()
    assert bk.is_backup_overdue('daily', state.get('last_success_at')) is True


def test_three_consecutive_identical_failures_notify_once(fake_install, tmp_path, monkeypatch, routes):
    dest = tmp_path / 'dest'
    _state.CONFIG['backup_dest_dir'] = str(dest)
    monkeypatch.setattr(bk, 'create_backup', lambda *a, **kw: (_ for _ in ()).throw(bk.BackupError('disk full')))
    notified = []
    monkeypatch.setattr(routes, '_notify_backup_failure', lambda reason: notified.append(reason))

    for _ in range(3):
        routes._run_scheduled_backup_once()

    state = bk.load_schedule_state()
    assert state['consecutive_failures'] == 3
    assert len(notified) == 1  # only the first failure of the streak paged


def test_failure_streak_notifies_again_when_error_text_changes(
        fake_install, tmp_path, monkeypatch, routes):
    dest = tmp_path / 'dest'
    _state.CONFIG['backup_dest_dir'] = str(dest)
    notified = []
    monkeypatch.setattr(routes, '_notify_backup_failure', lambda reason: notified.append(reason))

    monkeypatch.setattr(bk, 'create_backup', lambda *a, **kw: (_ for _ in ()).throw(bk.BackupError('disk full')))
    routes._run_scheduled_backup_once()
    routes._run_scheduled_backup_once()  # same error, no 2nd notify
    monkeypatch.setattr(bk, 'create_backup', lambda *a, **kw: (_ for _ in ()).throw(bk.BackupError('dest unreachable')))
    routes._run_scheduled_backup_once()  # different error, notifies again

    assert notified == ['disk full', 'dest unreachable']
    state = bk.load_schedule_state()
    assert state['consecutive_failures'] == 3


def test_notify_backup_failure_calls_push_inbox(fake_install, monkeypatch, routes):
    calls = []

    def fake_notify_push(title, body, **kw):
        calls.append((title, body, kw))
        return {}
    monkeypatch.setattr('mc.blueprints.push_mobile._notify_push', fake_notify_push)

    routes._notify_backup_failure('disk full')
    assert len(calls) == 1
    title, body, kw = calls[0]
    assert 'disk full' in body
    assert kw.get('kind') == 'agent'


def test_notify_backup_failure_itself_failing_is_swallowed(fake_install, monkeypatch, routes):
    def boom(*a, **kw):
        raise RuntimeError('push subsystem down')
    monkeypatch.setattr('mc.blueprints.push_mobile._notify_push', boom)
    routes._notify_backup_failure('disk full')  # must not raise


# ── GET /api/backup/schedule-status ─────────────────────────────────────────

@pytest.fixture
def client(routes):
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    return app.test_client()


def test_schedule_status_route_off_by_default(fake_install, client):
    resp = client.get('/api/backup/schedule-status')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['cadence'] == 'off'
    assert data['keep'] == 3
    assert data['last_run_at'] is None
    assert data['overdue'] is False


def test_schedule_status_route_reports_overdue_and_next_run(fake_install, monkeypatch, client):
    _state.CONFIG['backup_schedule'] = 'daily'
    old = (datetime.now(timezone.utc) - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
    bk.save_schedule_state({'last_run_at': old, 'last_success_at': old, 'last_status': 'success'})
    resp = client.get('/api/backup/schedule-status')
    data = resp.get_json()
    assert data['cadence'] == 'daily'
    assert data['overdue'] is True
    assert data['last_success_at'] == old


def test_schedule_status_route_overdue_survives_a_failed_attempt(fake_install, client):
    """A failed attempt bumps last_run_at (display) but must NOT bump
    last_success_at — overdue/next_run_at are computed off the latter, so a
    failure never buys the daemon a whole extra cadence before it tries
    again. This is the exact bug the review caught: previously overdue was
    computed off last_run_at, so a failed weekly backup went unretried for
    a week."""
    _state.CONFIG['backup_schedule'] = 'daily'
    old_success = (datetime.now(timezone.utc) - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
    recent_failure = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    bk.save_schedule_state({
        'last_run_at': recent_failure, 'last_success_at': old_success,
        'last_status': 'error', 'last_error': 'disk full',
    })
    resp = client.get('/api/backup/schedule-status')
    data = resp.get_json()
    assert data['overdue'] is True  # still overdue despite a recent attempt
    assert data['last_status'] == 'error'


# ── PUT /api/config validates backup_schedule / backup_keep ────────────────

@pytest.fixture
def config_client(fake_install, tmp_path):
    from mc.blueprints import settings_routes
    app = Flask(__name__)
    app.register_blueprint(settings_routes.bp)
    settings_routes.wire(
        config_path=tmp_path / 'config.json',
        projects_base=tmp_path / 'projects',
        settings_path=tmp_path / 'settings.json',
    )
    return app.test_client()


@pytest.mark.parametrize('value', ['off', 'daily', 'weekly'])
def test_config_put_accepts_valid_backup_schedule(config_client, value):
    resp = config_client.put('/api/config', json={'backup_schedule': value})
    assert resp.status_code == 200, resp.get_json()
    assert _state.CONFIG['backup_schedule'] == value


def test_config_put_refuses_bad_backup_schedule(config_client):
    resp = config_client.put('/api/config', json={'backup_schedule': 'monthly'})
    assert resp.status_code == 400
    assert 'backup_schedule' not in _state.CONFIG


def test_config_put_accepts_valid_backup_keep(config_client):
    resp = config_client.put('/api/config', json={'backup_keep': 5})
    assert resp.status_code == 200, resp.get_json()
    assert _state.CONFIG['backup_keep'] == 5


@pytest.mark.parametrize('value', [0, -1, 'three', True, 1.5])
def test_config_put_refuses_bad_backup_keep(config_client, value):
    resp = config_client.put('/api/config', json={'backup_keep': value})
    assert resp.status_code == 400
    assert 'backup_keep' not in _state.CONFIG
