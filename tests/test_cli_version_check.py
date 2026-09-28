"""tools/cli-version-check.py -- MC-991: preflight a locked npm package dir
before an in-place `npm install -g` attempts to overwrite it.

Never runs a real npm install (all update commands are stubbed). The one
real-process test (test_processes_locking_finds_only_the_process_under_the_dir)
spawns a copy of PING.EXE (not one of tests/conftest.py's blocked model-CLI
names) under its own control and kills only that PID when done, per the
process-hygiene rule.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows-only feature')


def _load_cvc():
    path = Path(__file__).resolve().parent.parent / 'tools' / 'cli-version-check.py'
    spec = importlib.util.spec_from_file_location('cli_version_check', path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def cvc():
    return _load_cvc()


def _fake_cli(cvc, name='codex', pkg='@openai/codex'):
    return cvc.CLI(name, pkg, ['npm', 'install', '-g', '%s@latest' % pkg])


# ── npm_package_dir ───────────────────────────────────────────────────────

def test_npm_package_dir_joins_scoped_package_under_prefix(cvc, monkeypatch):
    monkeypatch.setattr(cvc, '_npm_prefix', lambda: r'C:\fake\npmprefix')
    cli = _fake_cli(cvc)
    got = cvc.npm_package_dir(cli)
    assert got == os.path.join(r'C:\fake\npmprefix', 'node_modules', '@openai', 'codex')


def test_npm_package_dir_none_for_non_npm_cli(cvc, monkeypatch):
    monkeypatch.setattr(cvc, '_npm_prefix', lambda: r'C:\fake\npmprefix')
    cli = cvc.CLI('claude', '@anthropic-ai/claude-code', ['claude', 'update'])
    assert cvc.npm_package_dir(cli) is None


def test_npm_package_dir_none_when_prefix_unknown(cvc, monkeypatch):
    monkeypatch.setattr(cvc, '_npm_prefix', lambda: None)
    assert cvc.npm_package_dir(_fake_cli(cvc)) is None


# ── list_processes is a no-op off Windows ────────────────────────────────

def test_list_processes_noop_on_non_windows(cvc, monkeypatch):
    monkeypatch.setattr(cvc.sys, 'platform', 'linux')
    assert cvc.list_processes() == []


# ── preflight: real spawned process, under-dir filtering ────────────────

def test_processes_locking_finds_only_the_process_under_the_dir(cvc, tmp_path):
    inside_dir = tmp_path / 'pkg' / 'node_modules' / '@openai' / 'codex'
    inside_dir.mkdir(parents=True)
    outside_dir = tmp_path / 'elsewhere'
    outside_dir.mkdir()

    ping_src = r'C:\Windows\System32\PING.EXE'
    inside_exe = inside_dir / 'locker.exe'
    outside_exe = outside_dir / 'locker.exe'
    shutil.copy(ping_src, inside_exe)
    shutil.copy(ping_src, outside_exe)

    inside_proc = subprocess.Popen(
        [str(inside_exe), '-n', '20', '127.0.0.1'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    outside_proc = subprocess.Popen(
        [str(outside_exe), '-n', '20', '127.0.0.1'],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(0.5)  # let both processes actually start
        procs = cvc.list_processes()
        assert procs, 'list_processes() returned nothing -- PowerShell enumeration failed'

        lockers = cvc.processes_locking(str(inside_dir), procs)
        locker_pids = {p['pid'] for p in lockers}
        assert inside_proc.pid in locker_pids
        assert outside_proc.pid not in locker_pids

        # Rename-aside against the REAL running process, to a location
        # OUTSIDE the package tree (same volume): Windows allows renaming a
        # file a process still has open for execution, and moving it out of
        # the tree first is what lets the tree itself be removed afterwards
        # (Dave's review of e61ce33 -- an aside file left INSIDE the tree
        # still blocks the rmtree an in-place npm install does).
        locked_exe = next(p['exe'] for p in lockers if p['pid'] == inside_proc.pid)
        aside_dir = tmp_path / 'aside'
        aside = cvc.rename_aside(locked_exe, str(aside_dir))
        assert aside is not None
        assert os.path.isfile(aside)
        assert not os.path.exists(locked_exe)
        assert cvc._under_dir(aside, str(aside_dir))
        assert not cvc._under_dir(aside, str(inside_dir)), \
            'aside file must be moved OUTSIDE the package tree'
        assert inside_proc.poll() is None, 'renaming the exe killed the running process'

        # This is the property that actually matters: an in-place npm
        # install retires the WHOLE package dir via rmtree/rename. If the
        # aside file were still inside it, this would raise WinError 5.
        shutil.rmtree(str(tmp_path / 'pkg'))
        assert not (tmp_path / 'pkg').exists()
        assert inside_proc.poll() is None, 'removing the package tree killed the running process'
    finally:
        inside_proc.terminate()
        outside_proc.terminate()
        inside_proc.wait(timeout=10)
        outside_proc.wait(timeout=10)


# ── check_one: rename-aside success lets the install proceed ────────────

def test_rename_aside_success_lets_install_proceed(cvc, tmp_path, monkeypatch):
    pkg_dir = tmp_path / 'node_modules' / '@openai' / 'codex'
    pkg_dir.mkdir(parents=True)
    locked_exe = pkg_dir / 'codex.exe'
    locked_exe.write_bytes(b'stub')

    cli = _fake_cli(cvc)
    monkeypatch.setattr(cvc, 'npm_package_dir', lambda c: str(pkg_dir))
    monkeypatch.setattr(cvc, 'aside_dir_for', lambda c: str(tmp_path / 'aside'))
    monkeypatch.setattr(cvc, 'list_processes', lambda: [
        {'pid': 4242, 'ppid': 100, 'name': 'codex', 'exe': str(locked_exe), 'start_epoch': time.time()},
    ])

    versions = iter(['0.155.1', '0.158.0'])  # before install, after install
    monkeypatch.setattr(cvc, 'installed_version', lambda name: (next(versions), r'C:\fake\codex.cmd'))
    monkeypatch.setattr(cvc, 'latest_version', lambda pkg: '0.158.0')
    monkeypatch.setattr(cvc, 'shadow_check', lambda name, path: [])

    run_calls = []

    def fake_run(cmd, timeout=120):
        run_calls.append(cmd)
        return 0, 'ok'

    monkeypatch.setattr(cvc, '_run', fake_run)
    monkeypatch.setattr(cvc.shutil, 'which', lambda x: x)

    row = cvc.check_one(cli, apply_updates=True)

    assert len(run_calls) == 1, 'npm install should have run exactly once'
    assert row['updated'] is True
    assert row['installed_after'] == '0.158.0'
    assert 'renamed_aside' in row and row['renamed_aside']
    assert row['status'] != 'blocked_by_running_process'
    assert all(cvc._under_dir(a, str(tmp_path / 'aside')) for a in row['renamed_aside']), \
        'aside files must land outside pkg_dir, not next to the original'
    assert not any(cvc._under_dir(a, str(pkg_dir)) for a in row['renamed_aside'])


def test_rename_aside_failure_blocks_install_npm_not_invoked(cvc, tmp_path, monkeypatch):
    pkg_dir = tmp_path / 'node_modules' / '@openai' / 'codex'
    pkg_dir.mkdir(parents=True)
    locked_exe = pkg_dir / 'codex.exe'
    locked_exe.write_bytes(b'stub')

    cli = _fake_cli(cvc)
    monkeypatch.setattr(cvc, 'npm_package_dir', lambda c: str(pkg_dir))
    monkeypatch.setattr(cvc, 'aside_dir_for', lambda c: str(tmp_path / 'aside'))
    monkeypatch.setattr(cvc, 'list_processes', lambda: [
        {'pid': 4242, 'ppid': 100, 'name': 'codex', 'exe': str(locked_exe), 'start_epoch': time.time()},
    ])
    monkeypatch.setattr(cvc, 'rename_aside', lambda path, aside_dir: None)  # simulate a rename that can't succeed
    monkeypatch.setattr(cvc, 'installed_version', lambda name: ('0.155.1', r'C:\fake\codex.cmd'))
    monkeypatch.setattr(cvc, 'latest_version', lambda pkg: '0.158.0')
    monkeypatch.setattr(cvc, 'shadow_check', lambda name, path: [])

    run_calls = []
    monkeypatch.setattr(cvc, '_run', lambda cmd, timeout=120: run_calls.append(cmd) or (0, 'ok'))
    monkeypatch.setattr(cvc.shutil, 'which', lambda x: x)

    row = cvc.check_one(cli, apply_updates=True)

    assert run_calls == [], 'a known-failing install must never be attempted'
    assert row['status'] == 'blocked_by_running_process'
    assert row['blocking_processes'][0]['pid'] == 4242
    assert row['updated'] is False
    assert os.path.isfile(locked_exe), 'the locked file must be left in place, not disappear'


def test_blocked_when_npm_prefix_unknown_no_safe_aside_location(cvc, tmp_path, monkeypatch):
    """No npm prefix -> aside_dir_for returns None -> nowhere safe to move a
    locker to. Must block rather than guess a location or skip the check."""
    pkg_dir = tmp_path / 'node_modules' / '@openai' / 'codex'
    pkg_dir.mkdir(parents=True)
    locked_exe = pkg_dir / 'codex.exe'
    locked_exe.write_bytes(b'stub')

    cli = _fake_cli(cvc)
    monkeypatch.setattr(cvc, 'npm_package_dir', lambda c: str(pkg_dir))
    monkeypatch.setattr(cvc, 'aside_dir_for', lambda c: None)
    monkeypatch.setattr(cvc, 'list_processes', lambda: [
        {'pid': 4242, 'ppid': 100, 'name': 'codex', 'exe': str(locked_exe), 'start_epoch': time.time()},
    ])
    monkeypatch.setattr(cvc, 'installed_version', lambda name: ('0.155.1', r'C:\fake\codex.cmd'))
    monkeypatch.setattr(cvc, 'latest_version', lambda pkg: '0.158.0')
    monkeypatch.setattr(cvc, 'shadow_check', lambda name, path: [])

    run_calls = []
    monkeypatch.setattr(cvc, '_run', lambda cmd, timeout=120: run_calls.append(cmd) or (0, 'ok'))
    monkeypatch.setattr(cvc.shutil, 'which', lambda x: x)

    row = cvc.check_one(cli, apply_updates=True)

    assert run_calls == []
    assert row['status'] == 'blocked_by_running_process'
    assert row['blocking_processes'][0]['pid'] == 4242


def test_retry_once_after_downgrade_then_succeeds(cvc, tmp_path, monkeypatch):
    pkg_dir = tmp_path / 'node_modules' / '@openai' / 'codex'
    pkg_dir.mkdir(parents=True)

    cli = _fake_cli(cvc)
    monkeypatch.setattr(cvc, 'npm_package_dir', lambda c: str(pkg_dir))
    monkeypatch.setattr(cvc, 'aside_dir_for', lambda c: str(tmp_path / 'aside'))
    monkeypatch.setattr(cvc, 'list_processes', lambda: [])  # preflight finds nothing both times
    monkeypatch.setattr(cvc, 'shadow_check', lambda name, path: [])
    monkeypatch.setattr(cvc, 'latest_version', lambda pkg: '0.158.0')

    versions = iter(['0.155.1', '0.154.1', '0.158.0'])  # start, after attempt1 (downgrade), after attempt2
    monkeypatch.setattr(cvc, 'installed_version', lambda name: (next(versions), r'C:\fake\codex.cmd'))
    monkeypatch.setattr(cvc.shutil, 'which', lambda x: x)

    run_calls = []
    monkeypatch.setattr(cvc, '_run', lambda cmd, timeout=120: run_calls.append(cmd) or (0, 'ok'))

    row = cvc.check_one(cli, apply_updates=True)

    assert len(run_calls) == 2, 'exactly one retry after a downgrade'
    assert row['updated'] is True
    assert row['installed_after'] == '0.158.0'


# ── sweep_aside_files ─────────────────────────────────────────────────────

def test_sweep_removes_stale_aside_files_but_skips_locked_ones(cvc, tmp_path):
    pkg_dir = tmp_path / 'pkg'
    pkg_dir.mkdir()
    stale = pkg_dir / 'codex.exe.locked-aside-20260901T000000'
    still_locked = pkg_dir / 'codex.exe.locked-aside-20260928T000000'
    stale.write_bytes(b'x')
    still_locked.write_bytes(b'x')

    procs = [{'pid': 1, 'exe': str(still_locked)}]
    result = cvc.sweep_aside_files(str(pkg_dir), procs)

    assert str(stale) in result['removed']
    assert not stale.exists()
    assert str(still_locked) in result['skipped']
    assert still_locked.exists()


def test_sweep_noop_when_dir_missing(cvc, tmp_path):
    result = cvc.sweep_aside_files(str(tmp_path / 'does-not-exist'), [])
    assert result == {'removed': [], 'skipped': []}


# ── orphaned_processes ────────────────────────────────────────────────────

def test_orphaned_processes_reports_dead_ancestor_older_than_24h(cvc):
    now = time.time()
    pkg_dir = r'C:\fake\node_modules\@openai\codex'
    procs = [
        # orphan: 26h old, parent pid 33956 not alive
        {'pid': 45812, 'ppid': 33956, 'name': 'codex', 'exe': pkg_dir + r'\vendor\codex.exe',
         'start_epoch': now - 26 * 3600},
        # not orphaned: parent (200, a wrapper) is alive, AND its own
        # non-wrapper ancestor (1, explorer.exe) is alive too -- the whole
        # chain is owned, not just the direct parent.
        {'pid': 300, 'ppid': 200, 'name': 'codex', 'exe': pkg_dir + r'\vendor\other.exe',
         'start_epoch': now - 48 * 3600},
        {'pid': 200, 'ppid': 1, 'name': 'cmd', 'exe': r'C:\Windows\System32\cmd.exe', 'start_epoch': now - 100},
        {'pid': 1, 'ppid': None, 'name': 'explorer.exe', 'exe': r'C:\Windows\explorer.exe',
         'start_epoch': now - 999999},
        # too young: dead parent but only 2h old
        {'pid': 400, 'ppid': 9999, 'name': 'codex', 'exe': pkg_dir + r'\vendor\young.exe',
         'start_epoch': now - 2 * 3600},
        # outside pkg_dir entirely
        {'pid': 500, 'ppid': 8888, 'name': 'other', 'exe': r'C:\elsewhere\other.exe',
         'start_epoch': now - 48 * 3600},
    ]

    out = cvc.orphaned_processes(pkg_dir, procs, now=now)
    pids = {p['pid'] for p in out}
    assert pids == {45812}
    assert out[0]['ppid'] == 33956
    assert out[0]['age_hours'] >= 24


def test_orphaned_processes_walks_through_wrapper_chain(cvc):
    """MC-991's exact shape (Dave's review of e61ce33): codex 45812's DIRECT
    parent (node 37048) and grandparent (cmd 56332) are both alive wrappers --
    only the great-grandparent (33956) is dead. A ppid-only check reports this
    as NOT orphaned; walking through _WRAPPER_NAMES must still find it."""
    now = time.time()
    pkg_dir = r'C:\fake\node_modules\@openai\codex'
    procs = [
        {'pid': 45812, 'ppid': 37048, 'name': 'codex', 'exe': pkg_dir + r'\vendor\codex.exe',
         'start_epoch': now - 168 * 3600},  # started 2026-09-21, ~week old
        {'pid': 37048, 'ppid': 56332, 'name': 'node.exe', 'exe': r'C:\Program Files\nodejs\node.exe',
         'start_epoch': now - 168 * 3600},
        {'pid': 56332, 'ppid': 33956, 'name': 'cmd.exe', 'exe': r'C:\Windows\System32\cmd.exe',
         'start_epoch': now - 168 * 3600},
        # 33956 (the scheduler's own process) is dead -- absent from procs entirely.

        # control: same wrapper shape, but the top ancestor IS alive -> not orphaned.
        {'pid': 999, 'ppid': 998, 'name': 'codex', 'exe': pkg_dir + r'\vendor\live.exe',
         'start_epoch': now - 168 * 3600},
        {'pid': 998, 'ppid': 997, 'name': 'node.exe', 'exe': r'C:\Program Files\nodejs\node.exe',
         'start_epoch': now - 168 * 3600},
        {'pid': 997, 'ppid': 1, 'name': 'services.exe', 'exe': r'C:\Windows\System32\services.exe',
         'start_epoch': now - 999999},
    ]

    out = cvc.orphaned_processes(pkg_dir, procs, now=now)
    pids = {p['pid'] for p in out}
    assert pids == {45812}, 'must walk past the alive node/cmd wrappers to find the dead great-grandparent'
