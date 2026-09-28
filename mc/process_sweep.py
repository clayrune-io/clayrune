"""Server-side orphan sweep for agent CLI processes — MC-991 Phase 2.

Phase 1 (tools/cli-version-check.py, landed 81e6ff6) only REPORTS orphaned CLI
processes it finds locking an npm package dir during a version check; it never
kills. Ron approved auto-kill for the general case on 2026-09-28 after a
week-old orphaned codex.exe (PID 45812, launcher chain dead) locked the npm
install twice and downgraded Codex — see the standing position
`position_whetherclayrunemayautokillorphanedagentcliproces.md` for the full
ruling and its strict criteria. This module is that sweep.

Leaf module (mc.core + stdlib only — the mc/process_ledger.py precedent): no
`server` or `mc.blueprints` import, so it can be unit-tested without booting
the app AND imported by tools/cli-version-check.py, a separate process, via a
`sys.path.insert(0, <repo root>)` (see that script).

A PID is only ever a kill candidate when ALL of these hold, checked in
`run_sweep`:
  1. its exe path or command line references a known install dir for one of
     AGENT_CLI_NAMES (`matched_cli_name`),
  2. its FULL ancestor chain is dead, walking through shell/runtime wrappers —
     a parent whose recorded start time is LATER than the child's counts as
     dead too (a recycled PID can't be the real parent of a process that
     predates it) (`chain_is_dead`),
  3. it is not itself, nor a descendant of, any PID in the caller-supplied
     `root_pids` — Clayrune's live process registry, the on-disk child-PID
     ledger, and any revived/re-adopted session all feed that set at the call
     site; this module never reaches into server state itself (`descendant_closure`),
  4. it is at least `min_age_hours` old,
  5. its CPU time barely moved across a short sample window (`idle_pids`).

Enumeration failure at EITHER sampling point fails closed — no kill is
attempted, the report says why. Kills are always by the specific PID's
process tree, never by image name (the agent process-hygiene rule, which
binds every agent including this one's caller — this module only executes
`kill_fn`, which the call site wires to the real `_kill_pid`).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from typing import Dict, List, Set

AGENT_CLI_NAMES = ('claude', 'codex', 'gemini', 'opencode', 'qwen')

# Shells/runtimes that commonly sit BETWEEN a real launcher and the CLI they
# spawned (`cmd /c npm-cli.js` -> `node` -> the vendor exe). None of these is
# itself "what started the process" for orphan-detection purposes — walk
# through them. Mirrors tools/cli-version-check.py's `_WRAPPER_NAMES`.
_WRAPPER_NAMES = frozenset({
    'cmd.exe', 'cmd', 'node.exe', 'node', 'conhost.exe', 'conhost',
    'powershell.exe', 'powershell', 'pwsh.exe', 'pwsh',
    'bash.exe', 'bash', 'sh.exe', 'sh',
})


def _run(cmd, timeout=30):
    """Run a command, return (rc, combined output). Never raises.

    stdin=DEVNULL: see tools/cli-version-check.py's `_run` docstring — with no
    stdin argument the child inherits the parent's (possibly invalid) stdin
    handle under pytest capture or a console-less launch (scheduler, service).
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, encoding='utf-8', errors='replace')
        return r.returncode, ((r.stdout or '') + (r.stderr or '')).strip()
    except Exception as e:
        return 1, '%s: %s' % (type(e).__name__, e)


def list_processes(run_fn=None):
    """All running processes as dicts (pid, ppid, name, exe, cmdline,
    start_epoch, kernel_100ns, user_100ns), or None when enumeration FAILED —
    distinct from `[]`, which means Windows genuinely has no matching
    processes, or this isn't Windows at all (no-op there).

    kernel_100ns/user_100ns are Win32_Process's cumulative CPU time (100ns
    FILETIME ticks) — read once here so a caller can compute a CPU-time delta
    across two calls without a second CIM shape.

    None must never be read as "no lockers" or "nothing running" by a caller —
    every call site here fails closed on it (mirrors tools/cli-version-check.py's
    identically-named function and its D4 regression note; that script's own
    `list_processes` now delegates here via `run_fn=` so the PowerShell/CIM
    query has exactly one implementation, per MC-991 Phase 2's reuse ask —
    the `run_fn` indirection is what lets its tests keep patching its own
    module-level `_run` and still reach this call).
    """
    run_fn = run_fn or _run
    if sys.platform != 'win32':
        return []
    exe = shutil.which('powershell') or shutil.which('powershell.exe')
    if not exe:
        return None
    ps_cmd = (
        "Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, "
        "Name, ExecutablePath, CommandLine, KernelModeTime, UserModeTime, "
        "@{N='StartEpoch';E={ if ($_.CreationDate) { "
        "[int64]([datetimeoffset]$_.CreationDate).ToUnixTimeSeconds() } else { $null } }} "
        "| ConvertTo-Json -Compress"
    )
    rc, out = run_fn([exe, '-NoProfile', '-NonInteractive', '-Command', ps_cmd], timeout=30)
    if rc != 0 or not out:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    if isinstance(data, dict):
        data = [data]
    procs = []
    for p in data or []:
        if not isinstance(p, dict):
            continue
        procs.append({
            'pid': p.get('ProcessId'),
            'ppid': p.get('ParentProcessId'),
            'name': p.get('Name'),
            'exe': p.get('ExecutablePath'),
            'cmdline': p.get('CommandLine'),
            'start_epoch': p.get('StartEpoch'),
            'kernel_100ns': p.get('KernelModeTime'),
            'user_100ns': p.get('UserModeTime'),
        })
    return procs


def _under_dir(path, dir_path):
    if not path or not dir_path:
        return False
    norm_dir = os.path.normcase(os.path.abspath(dir_path)) + os.sep
    return os.path.normcase(os.path.abspath(path)).startswith(norm_dir)


def _references_dir(cmdline, dir_path):
    """True when `cmdline` mentions `dir_path` — catches node-wrapped CLIs
    (`node <path>\\gemini.js ...`) where ExecutablePath resolves to node.exe
    itself, not the package directory `_under_dir` alone would need."""
    if not cmdline or not dir_path:
        return False
    return os.path.normcase(os.path.abspath(dir_path)) in os.path.normcase(cmdline)


def _npm_prefix():
    npm = shutil.which('npm') or shutil.which('npm.cmd')
    if not npm:
        return None
    rc, out = _run([npm, 'config', 'get', 'prefix'], timeout=30)
    if rc == 0 and out and not out.startswith('undefined'):
        return out.strip()
    return None


def resolve_known_install_dirs(names=AGENT_CLI_NAMES):
    """dict CLI name -> sorted list of directories believed to hold that
    CLI's own files: the directory PATH resolves it to, plus the usual npm
    global-prefix fallback locations (mirrors tools/cli-version-check.py's
    `_prefix_fallback`, generalized across all five CLIs instead of one).

    A name absent from the result means we could not locate it at all — its
    processes never match `matched_cli_name` and are never sweep candidates
    (fail toward NOT flagging an unresolvable CLI's processes as orphans)."""
    prefix = _npm_prefix()
    roots = [r for r in (prefix, os.path.join(os.path.expanduser('~'), '.npm-global')) if r]
    dirs: Dict[str, List[str]] = {}
    for name in names:
        found: Set[str] = set()
        path = shutil.which(name)
        if path:
            found.add(os.path.normcase(os.path.abspath(os.path.dirname(path))))
        for root in roots:
            for rel in (name, name + '.cmd', name + '.exe', os.path.join('bin', name)):
                cand = os.path.join(root, rel)
                if os.path.isfile(cand):
                    found.add(os.path.normcase(os.path.abspath(os.path.dirname(cand))))
        if found:
            dirs[name] = sorted(found)
    return dirs


def matched_cli_name(proc, install_dirs):
    """The AGENT_CLI_NAMES entry `proc` appears to run from, via its exe path
    or a command-line reference to a known install dir, or None."""
    exe = proc.get('exe')
    cmdline = proc.get('cmdline') or ''
    for name, dirs in (install_dirs or {}).items():
        for d in dirs:
            if _under_dir(exe, d) or _references_dir(cmdline, d):
                return name
    return None


def chain_is_dead(proc, by_pid, max_depth=8):
    """Walk `proc`'s ancestor chain through _WRAPPER_NAMES. True once the walk
    hits a pid with no running process at all, OR a "parent" whose recorded
    start time is LATER than the child's own (the PID was recycled onto an
    unrelated, newer process — it cannot really be this process's parent).
    False as soon as a live, non-wrapper ancestor is found — someone (a
    shell, a service, Explorer) still owns the chain. Mirrors
    tools/cli-version-check.py's `orphaned_processes` walk, plus the
    recycled-parent clause MC-991 Phase 2 adds."""
    ppid = proc.get('ppid')
    child_start = proc.get('start_epoch')
    for _ in range(max_depth):
        if ppid is None:
            return False
        parent = by_pid.get(ppid)
        if parent is None:
            return True
        p_start = parent.get('start_epoch')
        if child_start is not None and p_start is not None and p_start > child_start:
            return True
        if (parent.get('name') or '').lower() not in _WRAPPER_NAMES:
            return False
        ppid = parent.get('ppid')
        if p_start is not None:
            child_start = p_start
    return False


def find_candidate_orphans(procs, install_dirs, now=None, min_age_hours=24.0, max_chain_depth=8):
    """Processes that look like an orphaned agent CLI: a known-CLI image,
    a fully dead ancestor chain, at least `min_age_hours` old. Does NOT yet
    account for protection (root_pids) or CPU idleness — `run_sweep` layers
    those on afterward, since protection needs a caller-supplied set and
    idleness needs a second sample."""
    now = time.time() if now is None else now
    by_pid = {p['pid']: p for p in procs if p.get('pid') is not None}
    out = []
    for p in procs:
        cli_name = matched_cli_name(p, install_dirs)
        if not cli_name:
            continue
        start = p.get('start_epoch')
        if start is None:
            continue
        age_hours = (now - start) / 3600.0
        if age_hours < min_age_hours:
            continue
        if not chain_is_dead(p, by_pid, max_chain_depth):
            continue
        out.append({
            'pid': p.get('pid'), 'ppid': p.get('ppid'), 'name': p.get('name'),
            'exe': p.get('exe'), 'cmdline': p.get('cmdline'), 'cli_name': cli_name,
            'age_hours': round(age_hours, 1), 'start_epoch': start,
        })
    return out


def descendant_closure(root_pids, procs):
    """`root_pids` plus every live descendant found by walking ppid links
    forward over `procs` — protects a whole session's process tree (e.g. an
    MCP-server child of a registered agent PID), not just the registered PID
    itself."""
    root_pids = set(p for p in (root_pids or ()) if p is not None)
    if not root_pids:
        return set()
    children_of: Dict[int, List[int]] = {}
    for p in procs:
        ppid, pid = p.get('ppid'), p.get('pid')
        if ppid is None or pid is None:
            continue
        children_of.setdefault(ppid, []).append(pid)
    out: Set[int] = set()
    stack = list(root_pids)
    while stack:
        pid = stack.pop()
        if pid in out:
            continue
        out.add(pid)
        stack.extend(children_of.get(pid, ()))
    return out


def _cpu_seconds(proc):
    k, u = proc.get('kernel_100ns'), proc.get('user_100ns')
    if k is None or u is None:
        return None
    try:
        return (float(k) + float(u)) / 1e7
    except (TypeError, ValueError):
        return None


def idle_pids(pids, procs_before, procs_after, cpu_epsilon=0.05):
    """PIDs from `pids` whose CPU time moved by at most `cpu_epsilon` seconds
    between two enumerations. A PID missing from `procs_after` (exited
    between samples) is dropped — nothing left to kill, not a bug. A PID
    whose CPU time can't be read on either side is left OUT (not idle) — the
    same fail-toward-sparing posture as every other check in this module."""
    before = {p['pid']: p for p in procs_before if p.get('pid') is not None}
    after = {p['pid']: p for p in procs_after if p.get('pid') is not None}
    out: Set[int] = set()
    for pid in pids:
        b, a = before.get(pid), after.get(pid)
        if b is None or a is None:
            continue
        cb, ca = _cpu_seconds(b), _cpu_seconds(a)
        if cb is None or ca is None:
            continue
        if (ca - cb) <= cpu_epsilon:
            out.add(pid)
    return out


def run_sweep(*, dry_run, root_pids=None, min_age_hours=24.0, cpu_window_s=2.0,
              cpu_epsilon=0.05, kill_fn, log_fn=None, sleep_fn=time.sleep,
              now_fn=time.time, list_processes_fn=None):
    """Find, and either report (dry_run) or kill, orphaned agent CLI
    processes. See module docstring for the full criteria chain.

    `kill_fn(pid, tree=True) -> bool` performs the actual kill — the call
    site wires this to the real `_kill_pid`; this module never kills by
    image name, only the specific candidate PID's tree.

    Returns a report dict: {ok, dry_run, error?, candidates, protected_skipped,
    not_idle_skipped, killed}. `killed` entries carry `action`:
    'would_kill' (dry_run) / 'killed' / 'kill_failed'.
    """
    list_processes_fn = list_processes_fn or list_processes
    log_fn = log_fn or (lambda entry: None)

    procs1 = list_processes_fn()
    if procs1 is None:
        return {'ok': False, 'error': 'enumeration_failed', 'dry_run': dry_run,
                'candidates': [], 'protected_skipped': [], 'not_idle_skipped': [], 'killed': []}

    install_dirs = resolve_known_install_dirs()
    candidates = find_candidate_orphans(procs1, install_dirs, now=now_fn(), min_age_hours=min_age_hours)
    if not candidates:
        return {'ok': True, 'dry_run': dry_run, 'candidates': [], 'protected_skipped': [],
                'not_idle_skipped': [], 'killed': []}

    protected = descendant_closure(root_pids, procs1)
    filtered = [c for c in candidates if c['pid'] not in protected]
    protected_hits = [c for c in candidates if c['pid'] in protected]
    if not filtered:
        return {'ok': True, 'dry_run': dry_run, 'candidates': candidates,
                'protected_skipped': protected_hits, 'not_idle_skipped': [], 'killed': []}

    sleep_fn(cpu_window_s)
    procs2 = list_processes_fn()
    if procs2 is None:
        return {'ok': False, 'error': 'enumeration_failed_second_sample', 'dry_run': dry_run,
                'candidates': candidates, 'protected_skipped': protected_hits,
                'not_idle_skipped': [], 'killed': []}

    idle = idle_pids([c['pid'] for c in filtered], procs1, procs2, cpu_epsilon)
    to_kill = [c for c in filtered if c['pid'] in idle]
    not_idle = [c for c in filtered if c['pid'] not in idle]

    killed = []
    for c in to_kill:
        entry = dict(c)
        if dry_run:
            entry['action'] = 'would_kill'
        else:
            entry['action'] = 'killed' if kill_fn(c['pid'], tree=True) else 'kill_failed'
        killed.append(entry)
        log_fn(entry)

    return {'ok': True, 'dry_run': dry_run, 'candidates': candidates,
            'protected_skipped': protected_hits, 'not_idle_skipped': not_idle, 'killed': killed}
