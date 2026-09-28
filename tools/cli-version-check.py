#!/usr/bin/env python3
"""Check the agent CLIs Clayrune dispatches to, and optionally update them.

WHY THIS EXISTS. MC spawns `claude`, `gemini`, `codex` (and the other
AgentRuntime binaries) by name, off PATH. Nothing in the product ever looked at
what VERSION it got. On 2026-09-09 that was measured: `gemini` on PATH resolved
to 0.20.0 from December 2025 while `npm ls -g` reported 0.57.0 -- two npm
prefixes existed (the old `AppData\\Roaming\\npm` on PATH, the current
`.npm-global` NOT on PATH), so every global install for nine months landed
somewhere PATH could not see. `claude` was 14 patch versions behind.

That is the failure this guards, and it is why the check is not just
"installed vs latest": a plain version comparison would have reported gemini
0.20.0 -> 0.59.0, run `npm i -g`, upgraded the copy PATH does NOT use, and
reported success while changing nothing. SHADOWING IS CHECKED FIRST and is
reported as its own finding, because an update applied to the wrong copy is
indistinguishable from a working one.

UPDATE METHOD IS PER-CLI, not per-package-manager. `claude` here is the native
installer build in ~/.local/bin, whose updater is `claude update`; running
`npm i -g @anthropic-ai/claude-code` against it installs a SECOND copy and
creates exactly the shadowing this tool exists to catch. So each entry carries
its own update command and we never derive one from the package name.

Report-only by default. `--apply` performs updates; `--json` prints machine
output for the scheduled run. Exit code is non-zero when something still needs
a human, so a scheduler can alert on it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mc import process_sweep as _process_sweep  # noqa: E402

_VER = re.compile(r'(\d+\.\d+\.\d+)')

DEFAULT_HOST = 'http://localhost:5199'


class CLI:
    """One agent CLI: how to find it, how to read its version, how to update.

    `npm_package` is the source of truth for "latest" even when the install is
    native -- Anthropic and OpenAI publish the same version numbers to npm that
    their standalone installers ship, so `npm view` is a cheap version oracle
    regardless of how the binary got there. It is NOT an instruction to update
    via npm; that is `update_cmd`'s job, deliberately kept separate.

    A None `update_cmd` means report-only: the CLI is managed by something we
    should not second-guess (pip/uv, a vendor installer).
    """

    def __init__(self, name, npm_package, update_cmd):
        self.name = name
        self.npm_package = npm_package
        self.update_cmd = update_cmd


CLIS = [
    CLI('claude', '@anthropic-ai/claude-code', ['claude', 'update']),
    CLI('gemini', '@google/gemini-cli', ['npm', 'install', '-g', '@google/gemini-cli@latest']),
    CLI('codex', '@openai/codex', ['npm', 'install', '-g', '@openai/codex@latest']),
    CLI('opencode', 'opencode-ai', ['npm', 'install', '-g', 'opencode-ai@latest']),
    CLI('aider', None, None),
    CLI('goose', None, None),
]


def _run(cmd, timeout=120):
    """Run a command, return (rc, combined output). Never raises.

    stdin=DEVNULL is required, not cosmetic: with no stdin argument the child
    inherits the parent's stdin handle, which is invalid under pytest's
    capture and equally under any launch with no console (scheduler,
    pythonw, a detached service) -- subprocess.run then raises OSError
    WinError 6 ("the handle is invalid") before the child even runs. Dave
    caught this via list_processes() returning [] under pytest while working
    fine standalone (MC-991 review of 894ac19).
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)
        return r.returncode, ((r.stdout or '') + (r.stderr or '')).strip()
    except Exception as e:
        return 1, '%s: %s' % (type(e).__name__, e)


def _npm(args, timeout=60):
    """npm is npm.cmd on Windows; resolve it rather than assuming a shell."""
    exe = shutil.which('npm') or shutil.which('npm.cmd')
    if not exe:
        return 1, 'npm not found on PATH'
    return _run([exe] + args, timeout=timeout)


def _prefix_fallback(name):
    """Find `name` under npm's configured prefix when PATH does not carry it.

    Without this the tool is blind in exactly the environment it is meant to
    audit: this script runs from whatever shell the scheduler hands it, and on
    2026-09-09 that shell's PATH lacked ~/.npm-global while the SERVER resolved
    gemini there fine. `shutil.which` returned None, the row became
    'not_installed', and a working 0.59.0 install vanished from the report --
    a version auditor that silently omits a CLI is the failure it exists to
    catch. MC's own runtimes probe these same locations.
    """
    rc, out = _npm(['config', 'get', 'prefix'], timeout=30)
    roots = []
    if rc == 0 and out and not out.startswith('undefined'):
        roots.append(out.strip())
    roots.append(os.path.join(os.path.expanduser('~'), '.npm-global'))
    for root in roots:
        for rel in (name + '.cmd', name + '.exe', name,
                    os.path.join('bin', name), os.path.join('bin', name + '.cmd')):
            cand = os.path.join(root, rel)
            if os.path.isfile(cand):
                return cand
    return None


def installed_version(name):
    """(version, resolved_path) for the copy PATH actually resolves."""
    path = shutil.which(name) or _prefix_fallback(name)
    if not path:
        return None, None
    rc, out = _run([path, '--version'], timeout=60)
    if rc != 0 and not out:
        return None, path
    m = _VER.search(out)
    return (m.group(1) if m else None), path


def latest_version(pkg):
    if not pkg:
        return None
    rc, out = _npm(['view', pkg, 'version'])
    if rc != 0:
        return None
    m = _VER.search(out)
    return m.group(1) if m else None


def shadow_check(name, resolved_path):
    """Other copies of `name` on PATH that are NOT the one PATH resolves.

    A non-empty result means an update may land on a copy nobody runs -- report
    it rather than silently "succeeding".
    """
    if not resolved_path:
        return []
    # Compare DIRECTORIES, not full paths. npm writes several wrappers for one
    # install (`gemini`, `gemini.cmd`, `gemini.ps1` side by side), so a
    # path-level comparison flags every npm CLI as shadowing itself. A real
    # shadow is a second copy in a DIFFERENT directory on PATH -- that is the
    # case where PATH order, not the package manager, decides what runs.
    others = []
    winner_dir = os.path.normcase(os.path.abspath(os.path.dirname(resolved_path)))
    seen_dirs = {winner_dir}
    for d in (os.environ.get('PATH') or '').split(os.pathsep):
        if not d:
            continue
        norm_dir = os.path.normcase(os.path.abspath(d))
        if norm_dir in seen_dirs:
            continue
        for ext in ('', '.cmd', '.exe', '.ps1'):
            cand = os.path.join(d, name + ext)
            if os.path.isfile(cand):
                others.append(os.path.normcase(os.path.abspath(cand)))
                seen_dirs.add(norm_dir)
                break
    return others


def _npm_prefix():
    """npm's configured global install prefix, or None if npm can't answer."""
    rc, out = _npm(['config', 'get', 'prefix'], timeout=30)
    if rc == 0 and out and not out.startswith('undefined'):
        return out.strip()
    return None


def npm_package_dir(cli):
    """Directory `npm install -g` will overwrite for this CLI, or None when
    the CLI isn't npm-managed (native installer, e.g. `claude update`) or npm
    has no configured prefix.

    This is where MC-991 happened: `npm install -g` writes in place under
    `<prefix>/node_modules/<package>`, regardless of which copy PATH
    currently resolves (see shadow_check) -- so THIS is the directory a
    preflight must check for lockers before an install is attempted, not the
    resolved binary's own directory.
    """
    if not cli.update_cmd or cli.update_cmd[0] != 'npm' or not cli.npm_package:
        return None
    prefix = _npm_prefix()
    if not prefix:
        return None
    return os.path.join(prefix, 'node_modules', *cli.npm_package.split('/'))


def aside_dir_for(cli):
    """Where rename_aside moves a locked file for this CLI -- OUTSIDE
    npm_package_dir entirely (same npm prefix, a dedicated subtree per
    package), or None when npm_package_dir itself would be None.

    Measured (code review of e61ce33, Dave, via a copied PING.EXE probe):
    renaming a locked exe to a NEW NAME INSIDE the package dir still leaves
    `shutil.rmtree(pkg_dir)` -- which is what an in-place `npm install -g`
    does to retire the old tree -- raising PermissionError WinError 5. Moving
    the file OUTSIDE the tree first (same volume, so os.rename still works)
    is what lets the rmtree succeed while the process keeps running.
    """
    if not cli.npm_package:
        return None
    prefix = _npm_prefix()
    if not prefix:
        return None
    return os.path.join(prefix, '.clayrune-locked-aside', *cli.npm_package.split('/'))


def list_processes():
    """All running processes as dicts (pid, ppid, name, exe, start_epoch, plus
    cmdline/kernel_100ns/user_100ns from the shared implementation), or None
    when enumeration FAILED (PowerShell missing, non-zero exit, no output,
    unparseable JSON) -- distinct from `[]`, which means Windows genuinely
    has no matching processes right now, or this isn't Windows at all (the
    feature is a no-op there; that's not a failure).

    None must never be read as "no lockers": a caller doing a preflight
    check that treated enumeration failure as an empty result would let a
    known-EBUSY npm install proceed anyway -- exactly the MC-991 regression
    (Dave's review of 894ac19, D4). Every call site is required to check for
    None and fail closed rather than default to "found nothing".

    Delegates the actual PowerShell/CIM query to mc.process_sweep (MC-991
    Phase 2's server-side orphan sweep needs the identical enumeration, so
    there is exactly one implementation, not two) -- `run_fn=_run` keeps this
    module's own tests able to stub the process call via `monkeypatch.setattr
    (cvc, '_run', ...)`, same as before this delegation.
    """
    return _process_sweep.list_processes(run_fn=_run)


_under_dir = _process_sweep._under_dir


def processes_locking(dir_path, procs):
    """Processes in `procs` whose executable resolves under `dir_path`, or
    None when `procs` is None (list_processes() enumeration failed) --
    propagated rather than treated as an empty list, so a preflight check
    fails closed instead of concluding "no lockers" from data it never got.
    """
    if procs is None:
        return None
    return [p for p in procs if _under_dir(p.get('exe'), dir_path)]


# Shells/runtimes that commonly sit BETWEEN a real launcher and the CLI they
# spawned (`cmd /c npm-cli.js` -> `node` -> the vendor exe, or a shebang
# script run through bash). None of these is itself "what started the
# process" for orphan-detection purposes -- walk through them. Shared with
# mc.process_sweep (MC-991 Phase 2) rather than duplicated.
_WRAPPER_NAMES = _process_sweep._WRAPPER_NAMES


def orphaned_processes(dir_path, procs, now=None, min_age_hours=24.0, max_chain_depth=8):
    """Processes under `dir_path` whose ancestor chain goes dead before
    reaching a live, non-wrapper process, and that have run at least
    `min_age_hours`.

    A dead DIRECT parent isn't sufficient signal by itself: MC-991's own
    orphaned codex (PID 45812) had a LIVE parent (node 37048, wrapper) and a
    LIVE grandparent (cmd 56332, wrapper) -- the chain only went dead at the
    great-grandparent (33956). Checking only `ppid` missed exactly the
    process this feature exists to find (Dave's review of e61ce33). Walk up
    through _WRAPPER_NAMES and call it orphaned only once the walk hits a pid
    that resolves to no running process at all; a live, non-wrapper ancestor
    found along the way means someone (a shell, a service, Explorer) still
    owns the chain, and it's not reported.

    Report-only -- NEVER killed. A dead ancestor's pid can already have been
    recycled onto an unrelated process by the OS (see memory:
    never-taskkill-tree-on-stale-pid); "ancestor missing" is evidence of an
    orphan, not proof, and killing on it would risk exactly that mistake.

    The ancestor walk itself is `mc.process_sweep.chain_is_dead` (shared with
    MC-991 Phase 2's server-side sweep rather than duplicated) -- it also
    treats a "parent" whose recorded start time is LATER than this process's
    own as dead (a recycled PID can't really be the parent of something that
    predates it), a case this report-only function never previously needed to
    tell apart from a live ancestor.
    """
    now = time.time() if now is None else now
    by_pid = {p['pid']: p for p in procs if p.get('pid') is not None}
    out = []
    for p in processes_locking(dir_path, procs):
        start = p.get('start_epoch')
        if start is None:
            continue
        age_hours = (now - start) / 3600.0
        if age_hours < min_age_hours:
            continue

        if _process_sweep.chain_is_dead(p, by_pid, max_chain_depth):
            out.append({'pid': p.get('pid'), 'ppid': p.get('ppid'), 'name': p.get('name'),
                        'exe': p.get('exe'), 'age_hours': round(age_hours, 1)})
    return out


def rename_aside(path, aside_dir):
    """Rename a locked file into `aside_dir` -- OUTSIDE the package tree it
    normally lives in (see aside_dir_for's docstring for why: an in-place
    npm install retires the WHOLE package dir via rename/rmtree, and an
    aside file left inside that dir still blocks deleting it). Windows lets
    you rename/move a file a running process still has open (the loader
    shares delete/rename access on the image mapping); the process keeps
    running against the old inode under its new path. Returns the new path,
    or None on failure.
    """
    if not path or not aside_dir or not os.path.isfile(path):
        return None
    try:
        os.makedirs(aside_dir, exist_ok=True)
    except OSError:
        return None
    aside = os.path.join(
        aside_dir, '%s.locked-aside-%s' % (os.path.basename(path), time.strftime('%Y%m%dT%H%M%S')))
    try:
        os.rename(path, aside)
        return aside
    except OSError:
        return None


def sweep_aside_files(dir_path, procs):
    """Remove `.locked-aside-*` files a previous rename_aside left behind in
    its aside_dir_for destination, once nothing still has them open. A
    rename-aside can't delete the old file -- npm has to succeed first -- so
    a later run cleans up what an earlier one left; skips (does not delete)
    any still pointed at by a live process.
    """
    removed, skipped = [], []
    if not dir_path or not os.path.isdir(dir_path):
        return {'removed': removed, 'skipped': skipped}
    locked = {os.path.normcase(os.path.abspath(p['exe']))
              for p in procs if p.get('exe')}
    for root, _dirs, files in os.walk(dir_path):
        for fn in files:
            if '.locked-aside-' not in fn:
                continue
            full = os.path.join(root, fn)
            if os.path.normcase(os.path.abspath(full)) in locked:
                skipped.append(full)
                continue
            try:
                os.remove(full)
                removed.append(full)
            except OSError:
                skipped.append(full)
    return {'removed': removed, 'skipped': skipped}


def _cmp(a, b):
    """-1 / 0 / 1 on dotted versions; 0 when either side is unknown."""
    if not a or not b:
        return 0
    try:
        pa = [int(x) for x in a.split('.')]
        pb = [int(x) for x in b.split('.')]
    except ValueError:
        return 0
    return (pa > pb) - (pa < pb)


def check_one(cli, apply_updates=False):
    inst, path = installed_version(cli.name)
    if not path:
        return {'name': cli.name, 'status': 'not_installed'}

    latest = latest_version(cli.npm_package)
    shadows = shadow_check(cli.name, path)
    row = {
        'name': cli.name, 'installed': inst, 'latest': latest, 'path': path,
        'shadowed_by': shadows, 'status': 'ok', 'updated': False,
    }

    # Diagnostic-only, independent of --apply/--behind: surface orphaned CLI
    # processes and sweep stale rename-aside files from a prior run, so a
    # plain report already shows what a locked-exe update would run into.
    pkg_dir = npm_package_dir(cli)
    aside_dir = aside_dir_for(cli) if pkg_dir else None
    if pkg_dir:
        procs = list_processes()
        if procs is None:
            # Enumeration failed -- say so rather than silently reporting
            # "no orphans found", which a reader can't tell apart from a
            # real clean check. Report-only, so we still finish this row.
            row['process_enumeration_failed'] = True
        else:
            if aside_dir:
                swept = sweep_aside_files(aside_dir, procs)
                if swept['removed']:
                    row['swept_aside_files'] = swept['removed']
            orphans = orphaned_processes(pkg_dir, procs)
            if orphans:
                row['orphaned_processes'] = orphans

    behind = _cmp(latest, inst) > 0
    if behind and shadows:
        row['status'] = 'outdated_and_shadowed'
    elif behind:
        row['status'] = 'outdated'
    elif shadows:
        row['status'] = 'shadowed'

    if apply_updates and behind and cli.update_cmd:
        # Resolve argv[0] (npm -> npm.cmd on Windows): a bare 'npm' raised
        # FileNotFoundError here and every npm update silently failed.
        cmd = list(cli.update_cmd)
        cmd[0] = shutil.which(cmd[0]) or shutil.which(cmd[0] + '.cmd') or cmd[0]

        # Preflight: a process with the CLI's own package dir open for
        # execution EBUSYs an in-place npm write (MC-991, codex 2026-09-25
        # and 2026-09-28). Rename each locker aside first; if any rename
        # fails, don't attempt a known-failing install at all. Retried once
        # (pkg_dir-managed CLIs only) if the FIRST attempt still downgrades
        # the CLI -- a locker preflight missed, or one that showed up
        # mid-install.
        rc, out, after = 1, '', inst
        blocked = False
        max_attempts = 2 if pkg_dir else 1
        for attempt in range(1, max_attempts + 1):
            if pkg_dir:
                procs_now = list_processes()
                if procs_now is None:
                    # Enumeration failed -- do NOT default to "no lockers".
                    # That default is exactly how MC-991 shipped a preflight
                    # that always passed and let a known-EBUSY npm install
                    # run anyway (Dave's review of 894ac19, D4).
                    row['status'] = 'preflight_failed'
                    row['preflight_error'] = 'process enumeration failed -- npm not invoked'
                    blocked = True
                    break
                lockers = processes_locking(pkg_dir, procs_now)
                if lockers and not aside_dir:
                    # No npm prefix -> nowhere safe to move a locker to.
                    # Don't guess; block instead.
                    row['status'] = 'blocked_by_running_process'
                    row['blocking_processes'] = [
                        {'pid': p.get('pid'), 'name': p.get('name'), 'exe': p.get('exe')}
                        for p in lockers]
                    blocked = True
                    break
                if lockers:
                    renamed, rename_ok = [], True
                    for p in lockers:
                        aside = rename_aside(p.get('exe'), aside_dir)
                        if aside is None:
                            rename_ok = False
                            break
                        renamed.append((p['exe'], aside))
                    if not rename_ok:
                        for orig, aside in renamed:
                            try:
                                os.rename(aside, orig)
                            except OSError:
                                pass
                        row['status'] = 'blocked_by_running_process'
                        row['blocking_processes'] = [
                            {'pid': p.get('pid'), 'name': p.get('name'), 'exe': p.get('exe')}
                            for p in lockers]
                        blocked = True
                        break
                    row.setdefault('renamed_aside', []).extend(a for _orig, a in renamed)

            rc, out = _run(cmd, timeout=600)
            after, _ = installed_version(cli.name)
            if rc == 0 and _cmp(after, inst) > 0:
                break
            if _cmp(after, inst) < 0 and attempt < max_attempts:
                continue
            break

        if not blocked:
            row['installed_after'] = after
            row['updated'] = (rc == 0 and _cmp(after, inst) > 0)
            if not row['updated']:
                row['update_error'] = out[-400:]
                # A failed npm in-place upgrade can leave the package PARTIALLY
                # replaced, so what runs afterwards is OLDER than what we started
                # with -- a regression this job itself caused. Measured 2026-09-25:
                # codex 0.156.1 -> 0.155.1 when a live codex process held the .exe
                # and npm's rename hit EPERM. It must never read as plain
                # "outdated", which is indistinguishable from "we did nothing".
                if _cmp(after, inst) < 0:
                    row['status'] = 'downgraded_by_failed_update'
                    row['downgraded_from'] = inst
            elif _cmp(latest, after) <= 0:
                row['status'] = 'shadowed' if shadows else 'ok'
    return row


def model_upgrades_check(host, apply_updates) -> dict:
    """POST /api/model-upgrades/run on the running Clayrune server and return
    its report dict, or an {'error': ...} dict when the server can't be
    reached -- never raises, matching this tool's report-only-by-default
    contract for the daily scheduled run that calls --model-upgrades.

    This is the ONLY way a model pin is auto-upgraded: the gate itself (which
    pin, which price, whether it's a genuine improvement) lives entirely in
    mc/model_upgrade.py and runs server-side. This function does not decide
    anything -- it just asks the server to run its own gate and prints what
    came back.
    """
    url = f'{host}/api/model-upgrades/run'
    body = json.dumps({'apply': apply_updates}).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        return {'error': f'could not reach {url}: {e}'}
    except Exception as e:
        return {'error': f'{type(e).__name__}: {e}'}


def process_sweep_before_apply(host) -> dict:
    """POST /api/system/process-sweep on the running Clayrune server before
    this script attempts any --apply install, so a week-old orphaned CLI
    process (MC-991's original codex.exe) is gone before it can lock a
    package dir again -- same call-seam pattern as `model_upgrades_check`:
    this function does not decide anything, the server runs its own gate
    (config 'process_sweep_enabled'/'process_sweep_dry_run', mc/process_sweep.py)
    and this just reports what came back. Best-effort -- a server that can't
    be reached must not block an --apply run; the per-CLI rename-aside
    preflight already handles a locked package dir independently of this.
    """
    url = f'{host}/api/system/process-sweep'
    req = urllib.request.Request(url, data=b'{}', method='POST',
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.URLError as e:
        return {'ok': False, 'error': f'could not reach {url}: {e}'}
    except Exception as e:
        return {'ok': False, 'error': f'{type(e).__name__}: {e}'}


def main():
    ap = argparse.ArgumentParser(description='Check/update Clayrune agent CLIs.')
    ap.add_argument('--apply', action='store_true', help='perform updates, not just report')
    ap.add_argument('--json', action='store_true', help='machine-readable output')
    ap.add_argument('--model-upgrades', action='store_true',
                    help='run the model pin auto-upgrade gate on the running '
                         'Clayrune server instead of checking CLI versions; '
                         '--apply applies what the gate approves (default: '
                         'preview only, same as the server\'s '
                         'model_auto_upgrade_enabled=false path)')
    ap.add_argument('--host', default=DEFAULT_HOST,
                    help=f'Clayrune server base URL (default: {DEFAULT_HOST})')
    args = ap.parse_args()

    if args.model_upgrades:
        report = model_upgrades_check(args.host, args.apply)
        print(json.dumps(report, indent=2))
        if report.get('error'):
            return 1
        # Needs a human: a price gap the daily run couldn't fill, or a
        # vendor-announced retirement priced against a NOW-more-expensive
        # successor (the "email Ron" case in the spec -- this script only
        # reports it, the scheduled AGENT sends the email per AGENT_RULES.md).
        needs_human = bool(report.get('unknown_price')) or any(
            row.get('retirement_urgent') for row in report.get('more_expensive') or [])
        return 1 if needs_human else 0

    if args.apply:
        # Best-effort: sweep orphaned agent-CLI processes on the running
        # server before any install attempt, so a stale locker (the
        # original MC-991 codex.exe) is gone before it can lock a package
        # dir again. Never blocks -- see process_sweep_before_apply's
        # docstring for why an unreachable server must not stop --apply.
        sweep_report = process_sweep_before_apply(args.host)
        if sweep_report.get('error'):
            print(f'[process-sweep] preflight skipped: {sweep_report["error"]}', file=sys.stderr)

    # Report every row, including not_installed. Hiding those was how a
    # resolution failure looked identical to "we don't run that CLI here".
    # not_installed is informational and does NOT set the exit code -- not
    # every box runs all six.
    live = [check_one(c, args.apply) for c in CLIS]

    if args.json:
        print(json.dumps({'clis': live}, indent=2))
    else:
        for r in live:
            line = '%-10s %-12s latest=%-12s %s' % (
                r['name'], str(r.get('installed')), str(r.get('latest')), r['status'])
            if r.get('updated'):
                line += ' -> %s' % r.get('installed_after')
            print(line)
            for s in r.get('shadowed_by') or []:
                print('           SHADOW: %s (PATH resolves %s)' % (s, r['path']))
            if r.get('update_error'):
                print('           UPDATE FAILED: %s' % r['update_error'])

    return 1 if any(r['status'] not in ('ok', 'not_installed') for r in live) else 0


if __name__ == '__main__':
    sys.exit(main())
