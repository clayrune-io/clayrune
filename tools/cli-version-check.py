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
    """Run a command, return (rc, combined output). Never raises."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
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


def list_processes():
    """All running processes as dicts (pid, ppid, name, exe, start_epoch).

    Windows-only -- returns [] on every other platform, and on any
    enumeration failure, so preflight/orphan-reporting degrade to "found no
    lockers" rather than a half-working guess. psutil isn't a dependency of
    this project (checked 2026-09-28), so this shells to PowerShell
    Get-CimInstance, per MC-991's brief.
    """
    if sys.platform != 'win32':
        return []
    exe = shutil.which('powershell') or shutil.which('powershell.exe')
    if not exe:
        return []
    ps_cmd = (
        "Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, "
        "Name, ExecutablePath, @{N='StartEpoch';E={ if ($_.CreationDate) { "
        "[int64]([datetimeoffset]$_.CreationDate).ToUnixTimeSeconds() } else { $null } }} "
        "| ConvertTo-Json -Compress"
    )
    rc, out = _run([exe, '-NoProfile', '-NonInteractive', '-Command', ps_cmd], timeout=30)
    if rc != 0 or not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
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
            'start_epoch': p.get('StartEpoch'),
        })
    return procs


def _under_dir(path, dir_path):
    if not path or not dir_path:
        return False
    norm_dir = os.path.normcase(os.path.abspath(dir_path)) + os.sep
    return os.path.normcase(os.path.abspath(path)).startswith(norm_dir)


def processes_locking(dir_path, procs):
    """Processes in `procs` whose executable resolves under `dir_path`."""
    return [p for p in procs if _under_dir(p.get('exe'), dir_path)]


def orphaned_processes(dir_path, procs, now=None, min_age_hours=24.0):
    """Processes under `dir_path` whose parent pid belongs to no currently
    running process, and that have run at least `min_age_hours`.

    Report-only -- NEVER killed. A dead ancestor's pid can already have been
    recycled onto an unrelated process by the OS (see memory:
    never-taskkill-tree-on-stale-pid); "ancestor missing" is evidence of an
    orphan, not proof, and killing on it would risk exactly that mistake.
    """
    now = time.time() if now is None else now
    alive = {p['pid'] for p in procs if p.get('pid') is not None}
    out = []
    for p in processes_locking(dir_path, procs):
        ppid = p.get('ppid')
        start = p.get('start_epoch')
        if ppid is None or ppid in alive or start is None:
            continue
        age_hours = (now - start) / 3600.0
        if age_hours >= min_age_hours:
            out.append({'pid': p.get('pid'), 'ppid': ppid, 'name': p.get('name'),
                        'exe': p.get('exe'), 'age_hours': round(age_hours, 1)})
    return out


def rename_aside(path):
    """Rename a locked file aside so npm can write a fresh one at the
    original name. Windows lets you rename a file a running process still
    has open (the loader shares delete/rename access on the image mapping);
    the process keeps running against the old inode under its new name.
    Returns the new path, or None on failure.
    """
    if not path or not os.path.isfile(path):
        return None
    aside = '%s.locked-aside-%s' % (path, time.strftime('%Y%m%dT%H%M%S'))
    try:
        os.rename(path, aside)
        return aside
    except OSError:
        return None


def sweep_aside_files(dir_path, procs):
    """Remove `.locked-aside-*` files a previous rename_aside left behind,
    once nothing still has them open. A rename-aside can't delete the old
    file -- npm has to succeed first -- so a later run cleans up what an
    earlier one left; skips (does not delete) any still pointed at by a live
    process.
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
    if pkg_dir:
        procs = list_processes()
        swept = sweep_aside_files(pkg_dir, procs)
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
                lockers = processes_locking(pkg_dir, list_processes())
                if lockers:
                    renamed, rename_ok = [], True
                    for p in lockers:
                        aside = rename_aside(p.get('exe'))
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
