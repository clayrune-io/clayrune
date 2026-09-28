"""Unit tests for mc/process_sweep.py -- MC-991 Phase 2: server-side
auto-kill of orphaned agent CLI processes (codex/claude/gemini/opencode/qwen).

Leaf-module tests, no `import server` needed -- everything here drives
`run_sweep` and its helpers directly with fabricated process dicts, per the
mc/process_ledger.py precedent (see tests/test_process_ledger.py). The
config-toggle wiring (`process_sweep_enabled`/`process_sweep_dry_run`) lives
in mc/blueprints/system_routes.py's `run_process_sweep`, not in this module --
that is covered by tests/test_system_routes_process_sweep.py, which reloads
`server` to get a wired instance.

Every test here fabricates its own process dicts; no real process is ever
started or killed (kill_fn is always a stub or an assertion-recording
callable), so this file needs none of the process-hygiene precautions that
apply to a test spawning a real PID.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

import mc.process_sweep as ps


# ── import smoke + no-import-cycle (leaf-module contract) ──────────────────

def test_import_smoke():
    for name in ('list_processes', 'matched_cli_name', 'chain_is_dead',
                 'find_candidate_orphans', 'descendant_closure', 'idle_pids',
                 'run_sweep', 'resolve_known_install_dirs', 'AGENT_CLI_NAMES'):
        assert hasattr(ps, name), f'missing {name}'


def test_no_import_cycle():
    """Must import leaf modules only -- never server or a blueprint (this
    module is imported by both mc.blueprints.system_routes AND the separate
    tools/cli-version-check.py process, so an accidental server import would
    make the tool pull in the whole Flask app)."""
    import ast
    from pathlib import Path
    src = Path(ps.__file__).read_text(encoding='utf-8')
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith('server'), alias.name
                assert 'mc.blueprints' not in alias.name, alias.name
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            assert not mod.startswith('server'), mod
            assert 'mc.blueprints' not in mod, mod


# ── helpers to build fake process dicts ─────────────────────────────────────

def _proc(pid, ppid, name, exe='', cmdline='', start_epoch=None, kernel_100ns: int | None = 0,
           user_100ns: int | None = 0) -> dict:
    return {
        'pid': pid, 'ppid': ppid, 'name': name, 'exe': exe, 'cmdline': cmdline,
        'start_epoch': start_epoch, 'kernel_100ns': kernel_100ns, 'user_100ns': user_100ns,
    }


INSTALL_DIRS = {'codex': [r'c:\fake\npm\node_modules\@openai\codex\vendor']}


def _orphan_proc(pid=45812, ppid=99999, age_hours=26.0, now=None, **kw):
    now = now if now is not None else time.time()
    exe = r'C:\fake\npm\node_modules\@openai\codex\vendor\codex.exe'
    return _proc(pid, ppid, 'codex.exe', exe=exe, start_epoch=now - age_hours * 3600, **kw)


# ── matched_cli_name (criterion a) ──────────────────────────────────────────

def test_matched_cli_name_by_exe_path():
    p = _proc(1, 0, 'codex.exe', exe=r'c:\fake\npm\node_modules\@openai\codex\vendor\codex.exe')
    assert ps.matched_cli_name(p, INSTALL_DIRS) == 'codex'


def test_matched_cli_name_by_cmdline_when_exe_is_node():
    p = _proc(1, 0, 'node.exe', exe=r'C:\Program Files\nodejs\node.exe',
               cmdline=r'node "c:\fake\npm\node_modules\@openai\codex\vendor\codex.js"')
    assert ps.matched_cli_name(p, INSTALL_DIRS) == 'codex'


def test_matched_cli_name_none_outside_install_dir():
    p = _proc(1, 0, 'notepad.exe', exe=r'C:\Windows\notepad.exe')
    assert ps.matched_cli_name(p, INSTALL_DIRS) is None


# ── resolve_known_install_dirs (Dave's review of cfa95c7): must resolve to
# each CLI's OWN package dir, never the shared npm prefix every globally-
# installed npm tool (npm itself, pm2, an unrelated MCP server) also sits
# under. Real filesystem, real function -- no INSTALL_DIRS stand-in. ───────

@pytest.fixture()
def fake_npm_prefix(tmp_path, monkeypatch):
    """A prefix dir with codex + gemini installed as real npm packages,
    PLUS npm's own package and pm2 (globally-installed, unrelated tools
    that share the same prefix on a real box) -- the exact collision Dave
    measured."""
    prefix = tmp_path / 'npm-global'
    node_modules = prefix / 'node_modules'
    (node_modules / '@openai' / 'codex' / 'vendor').mkdir(parents=True)
    (node_modules / '@openai' / 'codex' / 'vendor' / 'codex.exe').write_text('x')
    (node_modules / '@google' / 'gemini-cli' / 'dist').mkdir(parents=True)
    (node_modules / '@google' / 'gemini-cli' / 'dist' / 'gemini.js').write_text('x')
    (node_modules / 'npm' / 'bin').mkdir(parents=True)
    (node_modules / 'npm' / 'bin' / 'npm-cli.js').write_text('x')
    (node_modules / 'pm2' / 'bin').mkdir(parents=True)
    (node_modules / 'pm2' / 'bin' / 'pm2').write_text('x')
    monkeypatch.setattr(ps, '_npm_prefix', lambda: str(prefix))
    monkeypatch.setattr(ps, '_native_install_dirs', lambda name: [])
    return prefix


def test_resolve_known_install_dirs_excludes_shared_prefix_root(fake_npm_prefix):
    prefix = fake_npm_prefix
    dirs = ps.resolve_known_install_dirs(names=('codex',))
    norm_prefix = os.path.normcase(str(prefix))
    assert norm_prefix not in [os.path.normcase(d) for d in dirs['codex']]
    for d in dirs['codex']:
        assert 'codex' in d.lower()


def test_resolve_known_install_dirs_never_includes_npm_or_pm2(fake_npm_prefix):
    dirs = ps.resolve_known_install_dirs(names=('codex', 'gemini'))
    all_dirs = [d for v in dirs.values() for d in v]
    assert not any('npm' in d.lower().split(os.sep)[-2:] for d in all_dirs)
    assert not any('pm2' in d.lower() for d in all_dirs)


def test_matched_cli_name_ignores_npm_cli_process(fake_npm_prefix):
    dirs = ps.resolve_known_install_dirs(names=('codex', 'gemini'))
    npm_js = str(fake_npm_prefix / 'node_modules' / 'npm' / 'bin' / 'npm-cli.js')
    p = _proc(1, 0, 'node.exe', exe=r'C:\Program Files\nodejs\node.exe',
               cmdline='node "%s" install -g @openai/codex@latest' % npm_js)
    assert ps.matched_cli_name(p, dirs) is None


def test_matched_cli_name_ignores_pm2_process(fake_npm_prefix):
    dirs = ps.resolve_known_install_dirs(names=('codex', 'gemini'))
    pm2 = str(fake_npm_prefix / 'node_modules' / 'pm2' / 'bin' / 'pm2')
    p = _proc(1, 0, 'node.exe', exe=r'C:\Program Files\nodejs\node.exe', cmdline='node "%s"' % pm2)
    assert ps.matched_cli_name(p, dirs) is None


def test_matched_cli_name_still_matches_real_codex_under_its_own_package_dir(fake_npm_prefix):
    """The PID 45812 case this whole module exists for: codex.exe living
    under its own node_modules/@openai/codex dir must still match."""
    dirs = ps.resolve_known_install_dirs(names=('codex', 'gemini'))
    exe = str(fake_npm_prefix / 'node_modules' / '@openai' / 'codex' / 'vendor' / 'codex.exe')
    p = _proc(1, 0, 'codex.exe', exe=exe)
    assert ps.matched_cli_name(p, dirs) == 'codex'


def test_matched_cli_name_correct_per_package_not_first_dict_entry(fake_npm_prefix):
    """A gemini process must be labelled 'gemini', not whichever CLI name
    happens to iterate first in the install-dirs dict."""
    dirs = ps.resolve_known_install_dirs(names=('codex', 'gemini'))
    exe = str(fake_npm_prefix / 'node_modules' / '@google' / 'gemini-cli' / 'dist' / 'gemini.js')
    p = _proc(1, 0, 'node.exe', exe=r'C:\Program Files\nodejs\node.exe', cmdline='node "%s"' % exe)
    assert ps.matched_cli_name(p, dirs) == 'gemini'


def test_run_sweep_end_to_end_spares_npm_and_pm2_but_kills_real_codex_orphan(fake_npm_prefix, monkeypatch):
    """Full run_sweep against the real resolve_known_install_dirs (not the
    fake INSTALL_DIRS stand-in used elsewhere in this file): an idle, old,
    dead-chain npm-cli.js and pm2 process must NOT be swept even though
    every other criterion is met; a real orphaned codex.exe must still be."""
    now = time.time()
    codex_exe = str(fake_npm_prefix / 'node_modules' / '@openai' / 'codex' / 'vendor' / 'codex.exe')
    npm_js = str(fake_npm_prefix / 'node_modules' / 'npm' / 'bin' / 'npm-cli.js')
    pm2_bin = str(fake_npm_prefix / 'node_modules' / 'pm2' / 'bin' / 'pm2')
    orphan_codex = _proc(1, 99999, 'codex.exe', exe=codex_exe, start_epoch=now - 26 * 3600)
    orphan_npm = _proc(2, 99998, 'node.exe', exe=r'C:\Program Files\nodejs\node.exe',
                        cmdline='node "%s"' % npm_js, start_epoch=now - 26 * 3600)
    orphan_pm2 = _proc(3, 99997, 'node.exe', exe=r'C:\Program Files\nodejs\node.exe',
                        cmdline='node "%s"' % pm2_bin, start_epoch=now - 26 * 3600)
    report, killed, _ = _run_sweep_with([orphan_codex, orphan_npm, orphan_pm2])
    assert killed == [1]


# ── chain_is_dead (criterion b, including recycled-PID) ─────────────────────

def test_chain_is_dead_true_when_parent_missing():
    now = time.time()
    p = _proc(10, 999, 'codex.exe', start_epoch=now - 30 * 3600)
    assert ps.chain_is_dead(p, {10: p}) is True


def test_chain_is_dead_false_when_live_nonwrapper_ancestor():
    now = time.time()
    child = _proc(10, 20, 'codex.exe', start_epoch=now - 30 * 3600)
    wrapper = _proc(20, 30, 'cmd.exe', start_epoch=now - 40 * 3600)
    shell = _proc(30, None, 'explorer.exe', start_epoch=now - 999999)
    by_pid = {10: child, 20: wrapper, 30: shell}
    assert ps.chain_is_dead(child, by_pid) is False


def test_chain_is_dead_walks_through_multiple_wrappers_to_dead_end():
    now = time.time()
    child = _proc(10, 20, 'codex.exe', start_epoch=now - 30 * 3600)
    w1 = _proc(20, 30, 'node.exe', start_epoch=now - 31 * 3600)
    w2 = _proc(30, 999, 'cmd.exe', start_epoch=now - 32 * 3600)  # ppid 999 not present -> dead
    by_pid = {10: child, 20: w1, 30: w2}
    assert ps.chain_is_dead(child, by_pid) is True


def test_chain_is_dead_recycled_pid_counts_as_dead():
    """A 'parent' whose recorded start time is LATER than the child's own
    cannot really be the process that spawned it -- the OS recycled that PID
    onto something newer and unrelated. Per the MC-991 Phase 2 brief, this
    counts as dead (the real ancestor is gone), not as a live owner."""
    now = time.time()
    child = _proc(10, 20, 'codex.exe', start_epoch=now - 48 * 3600)
    # "parent" 20 started only 5 minutes ago -- long AFTER the child.
    recycled = _proc(20, None, 'cmd.exe', start_epoch=now - 300)
    by_pid = {10: child, 20: recycled}
    assert ps.chain_is_dead(child, by_pid) is True


def test_chain_is_dead_max_depth_stops_infinite_loop():
    now = time.time()
    # A long chain of live wrappers, each older than the last, with no dead
    # end within max_depth -- must return False (fail toward NOT killing),
    # not hang.
    procs = {}
    prev_start = now
    pid = 1
    for i in range(20):
        prev_start -= 3600
        procs[pid] = _proc(pid, pid + 1, 'cmd.exe', start_epoch=prev_start)
        pid += 1
    procs[pid] = _proc(pid, None, 'explorer.exe', start_epoch=prev_start - 999999)
    child = procs[1]
    assert ps.chain_is_dead(child, procs, max_depth=3) is False


# ── find_candidate_orphans (criteria a, b, d combined) ──────────────────────

def test_find_candidate_orphans_age_gate():
    now = time.time()
    young = _orphan_proc(pid=1, age_hours=2.0, now=now)   # too young
    old = _orphan_proc(pid=2, age_hours=26.0, now=now)     # old enough
    procs = [young, old]
    out = ps.find_candidate_orphans(procs, INSTALL_DIRS, now=now, min_age_hours=24.0)
    assert [c['pid'] for c in out] == [2]


def test_find_candidate_orphans_skips_missing_start_epoch():
    now = time.time()
    p = _orphan_proc(pid=1, now=now)
    p['start_epoch'] = None
    out = ps.find_candidate_orphans([p], INSTALL_DIRS, now=now)
    assert out == []


# ── descendant_closure (criterion c: registry / ledger / restart re-adoption) ──

def test_descendant_closure_protects_root_and_children():
    procs = [
        _proc(1, None, 'x'), _proc(2, 1, 'y'), _proc(3, 2, 'z'), _proc(4, None, 'unrelated'),
    ]
    out = ps.descendant_closure({1}, procs)
    assert out == {1, 2, 3}


def test_descendant_closure_restart_readoption():
    """A server restart orphans the PREVIOUS server's live children in
    memory (tracked_processes resets), but they're still tracked on disk in
    mc_child_pids.json. The call site feeds that ledger's PIDs into
    root_pids alongside the live registry -- this test proves a PID that is
    ONLY in that ledger (not the live in-memory registry) is still
    protected, simulating the exact restart window."""
    now = time.time()
    survivor = _orphan_proc(pid=45812, ppid=1, age_hours=30.0, now=now)  # orphan-shaped otherwise
    procs = [survivor]
    # Ledger-only root_pids (as if tracked_processes is empty right after
    # restart but mc_child_pids.json still lists this PID).
    ledger_root_pids = {45812}
    protected = ps.descendant_closure(ledger_root_pids, procs)
    assert 45812 in protected


def test_descendant_closure_empty_roots_returns_empty():
    procs = [_proc(1, None, 'x')]
    assert ps.descendant_closure(set(), procs) == set()
    assert ps.descendant_closure(None, procs) == set()


# ── idle_pids (criterion e) ─────────────────────────────────────────────────

def test_idle_pids_true_when_cpu_time_barely_moves():
    before = [_proc(1, 0, 'codex.exe', kernel_100ns=1000, user_100ns=1000)]
    after = [_proc(1, 0, 'codex.exe', kernel_100ns=1000, user_100ns=1000)]
    assert ps.idle_pids([1], before, after) == {1}


def test_idle_pids_false_when_cpu_time_moves_past_epsilon():
    before = [_proc(1, 0, 'codex.exe', kernel_100ns=0, user_100ns=0)]
    after = [_proc(1, 0, 'codex.exe', kernel_100ns=int(1.0 * 1e7), user_100ns=0)]  # +1s CPU
    assert ps.idle_pids([1], before, after) == set()


def test_idle_pids_excludes_pid_that_exited_between_samples():
    before = [_proc(1, 0, 'codex.exe', kernel_100ns=0, user_100ns=0)]
    after: list = []  # gone
    assert ps.idle_pids([1], before, after) == set()


def test_idle_pids_excludes_unreadable_cpu_time():
    before = [_proc(1, 0, 'codex.exe', kernel_100ns=None, user_100ns=None)]
    after = [_proc(1, 0, 'codex.exe', kernel_100ns=None, user_100ns=None)]
    assert ps.idle_pids([1], before, after) == set()


# ── run_sweep: each criterion, held alone, must prevent a kill ──────────────

def _run_sweep_with(procs1, procs2=None, root_pids=None, dry_run=False, min_age_hours=24.0,
                     kill_fn=None, install_dirs=None):
    procs2 = procs2 if procs2 is not None else procs1
    calls = {'seq': [procs1, procs2], 'i': 0}

    def list_processes_fn():
        i = calls['i']
        calls['i'] += 1
        return calls['seq'][min(i, len(calls['seq']) - 1)]

    killed_pids = []

    def default_kill_fn(pid, tree=True):
        killed_pids.append(pid)
        return True

    logged = []
    report = ps.run_sweep(
        dry_run=dry_run, root_pids=root_pids, min_age_hours=min_age_hours,
        cpu_window_s=0.0, kill_fn=kill_fn or default_kill_fn,
        log_fn=lambda e: logged.append(e), sleep_fn=lambda s: None,
        now_fn=time.time, list_processes_fn=list_processes_fn,
    )
    if install_dirs is not None:
        pass  # resolve_known_install_dirs is not injectable; see monkeypatch tests below
    return report, killed_pids, logged


def test_run_sweep_kills_a_genuine_orphan(monkeypatch):
    now = time.time()
    orphan = _orphan_proc(pid=45812, ppid=99999, age_hours=26.0, now=now)
    monkeypatch.setattr(ps, 'resolve_known_install_dirs', lambda: INSTALL_DIRS)
    report, killed, logged = _run_sweep_with([orphan])
    assert report['ok'] is True
    assert killed == [45812]
    assert report['killed'][0]['action'] == 'killed'
    assert logged and logged[0]['pid'] == 45812


def test_run_sweep_criterion_a_wrong_image_not_killed(monkeypatch):
    now = time.time()
    p = _proc(1, 99999, 'notepad.exe', exe=r'C:\Windows\notepad.exe', start_epoch=now - 30 * 3600)
    monkeypatch.setattr(ps, 'resolve_known_install_dirs', lambda: INSTALL_DIRS)
    report, killed, _ = _run_sweep_with([p])
    assert killed == []
    assert report['candidates'] == []


def test_run_sweep_criterion_b_live_ancestor_not_killed(monkeypatch):
    now = time.time()
    exe = r'C:\fake\npm\node_modules\@openai\codex\vendor\codex.exe'
    child = _proc(10, 20, 'codex.exe', exe=exe, start_epoch=now - 30 * 3600)
    shell = _proc(20, None, 'explorer.exe', start_epoch=now - 999999)
    monkeypatch.setattr(ps, 'resolve_known_install_dirs', lambda: INSTALL_DIRS)
    report, killed, _ = _run_sweep_with([child, shell])
    assert killed == []
    assert report['candidates'] == []


def test_run_sweep_criterion_c_protected_by_root_pids_not_killed(monkeypatch):
    now = time.time()
    orphan = _orphan_proc(pid=45812, ppid=99999, age_hours=26.0, now=now)
    monkeypatch.setattr(ps, 'resolve_known_install_dirs', lambda: INSTALL_DIRS)
    report, killed, _ = _run_sweep_with([orphan], root_pids={45812})
    assert killed == []
    assert report['protected_skipped'] and report['protected_skipped'][0]['pid'] == 45812


def test_run_sweep_criterion_c_protected_via_ancestor_pid_tree(monkeypatch):
    """root_pids protects the whole descendant tree, not just the exact PID
    -- a registered session PID whose orphan-shaped process is a CHILD of
    it must also be spared."""
    now = time.time()
    session_root = _proc(99, None, 'claude.exe', start_epoch=now - 999999)
    orphan_child = _orphan_proc(pid=45812, ppid=99, age_hours=26.0, now=now)
    monkeypatch.setattr(ps, 'resolve_known_install_dirs', lambda: INSTALL_DIRS)
    report, killed, _ = _run_sweep_with([orphan_child, session_root], root_pids={99})
    assert killed == []


def test_run_sweep_criterion_d_too_young_not_killed(monkeypatch):
    now = time.time()
    young = _orphan_proc(pid=1, ppid=99999, age_hours=2.0, now=now)
    monkeypatch.setattr(ps, 'resolve_known_install_dirs', lambda: INSTALL_DIRS)
    report, killed, _ = _run_sweep_with([young])
    assert killed == []
    assert report['candidates'] == []


def test_run_sweep_criterion_e_active_cpu_not_killed(monkeypatch):
    now = time.time()
    orphan_before = _orphan_proc(pid=45812, ppid=99999, age_hours=26.0, now=now,
                                  kernel_100ns=0, user_100ns=0)
    orphan_after = dict(orphan_before, kernel_100ns=int(2.0 * 1e7))  # +2s CPU -> active
    monkeypatch.setattr(ps, 'resolve_known_install_dirs', lambda: INSTALL_DIRS)
    report, killed, _ = _run_sweep_with([orphan_before], procs2=[orphan_after])
    assert killed == []
    assert report['not_idle_skipped'] and report['not_idle_skipped'][0]['pid'] == 45812


# ── recycled-PID at the run_sweep level ─────────────────────────────────────

def test_run_sweep_recycled_parent_still_killed(monkeypatch):
    """The child looks orphaned because its recorded ppid now belongs to an
    unrelated, newer process (recycled PID) -- still a valid kill, per
    chain_is_dead's recycled-PID clause."""
    now = time.time()
    exe = r'C:\fake\npm\node_modules\@openai\codex\vendor\codex.exe'
    child = _proc(10, 20, 'codex.exe', exe=exe, start_epoch=now - 48 * 3600)
    recycled = _proc(20, None, 'cmd.exe', start_epoch=now - 300)
    monkeypatch.setattr(ps, 'resolve_known_install_dirs', lambda: INSTALL_DIRS)
    report, killed, _ = _run_sweep_with([child, recycled])
    assert killed == [10]


# ── dry_run mode ─────────────────────────────────────────────────────────────

def test_run_sweep_dry_run_reports_without_killing(monkeypatch):
    now = time.time()
    orphan = _orphan_proc(pid=45812, ppid=99999, age_hours=26.0, now=now)
    monkeypatch.setattr(ps, 'resolve_known_install_dirs', lambda: INSTALL_DIRS)
    report, killed, logged = _run_sweep_with([orphan], dry_run=True)
    assert killed == []
    assert report['dry_run'] is True
    assert report['killed'][0]['action'] == 'would_kill'
    assert logged and logged[0]['action'] == 'would_kill'


# ── enumeration failure: fail closed ────────────────────────────────────────

def test_run_sweep_enumeration_failure_first_sample_fails_closed(monkeypatch):
    monkeypatch.setattr(ps, 'resolve_known_install_dirs', lambda: INSTALL_DIRS)
    report = ps.run_sweep(dry_run=False, kill_fn=lambda pid, tree=True: True,
                          list_processes_fn=lambda: None)
    assert report['ok'] is False
    assert report['error'] == 'enumeration_failed'
    assert report['killed'] == []


def test_run_sweep_enumeration_failure_second_sample_fails_closed(monkeypatch):
    now = time.time()
    orphan = _orphan_proc(pid=45812, ppid=99999, age_hours=26.0, now=now)
    monkeypatch.setattr(ps, 'resolve_known_install_dirs', lambda: INSTALL_DIRS)
    calls = {'i': 0}

    def list_processes_fn():
        calls['i'] += 1
        return [orphan] if calls['i'] == 1 else None

    report = ps.run_sweep(dry_run=False, kill_fn=lambda pid, tree=True: True,
                          list_processes_fn=list_processes_fn, sleep_fn=lambda s: None,
                          now_fn=time.time)
    assert report['ok'] is False
    assert report['error'] == 'enumeration_failed_second_sample'
    assert report['killed'] == []


def test_run_sweep_no_candidates_short_circuits_before_second_sample(monkeypatch):
    """When nothing matches criteria a/b/d, run_sweep must not even attempt
    the second (CPU-idle) enumeration -- covered by asserting list_processes_fn
    is called exactly once."""
    monkeypatch.setattr(ps, 'resolve_known_install_dirs', lambda: INSTALL_DIRS)
    calls = {'n': 0}

    def list_processes_fn():
        calls['n'] += 1
        return []

    report = ps.run_sweep(dry_run=False, kill_fn=lambda pid, tree=True: True,
                          list_processes_fn=list_processes_fn, sleep_fn=lambda s: None)
    assert report['ok'] is True
    assert report['candidates'] == []
    assert calls['n'] == 1


def test_list_processes_non_windows_returns_empty_list(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    assert ps.list_processes() == []


def test_list_processes_enumeration_failure_returns_none(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(ps.shutil, 'which', lambda *a, **kw: None)
    assert ps.list_processes() is None


def test_references_dir_requires_path_boundary():
    """A sibling package sharing a name prefix (`@openai\codex-mcp`) must not
    match the `@openai\codex` package dir via the command line (Dave review)."""
    d = os.path.join('C:' + os.sep, 'npm', 'node_modules', '@openai', 'codex')
    base = os.path.normcase(os.path.abspath(d))
    assert ps._references_dir('node ' + base + os.sep + 'bin' + os.sep + 'codex.js', d)
    assert ps._references_dir('"' + base + '" --flag', d)
    assert ps._references_dir('node ' + base, d)
    assert not ps._references_dir('node ' + base + '-mcp' + os.sep + 'bin.js', d)
