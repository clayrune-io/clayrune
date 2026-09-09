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

_VER = re.compile(r'(\d+\.\d+\.\d+)')


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


def installed_version(name):
    """(version, resolved_path) for the copy PATH actually resolves."""
    path = shutil.which(name)
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

    behind = _cmp(latest, inst) > 0
    if behind and shadows:
        row['status'] = 'outdated_and_shadowed'
    elif behind:
        row['status'] = 'outdated'
    elif shadows:
        row['status'] = 'shadowed'

    if apply_updates and behind and cli.update_cmd:
        rc, out = _run(cli.update_cmd, timeout=600)
        after, _ = installed_version(cli.name)
        row['installed_after'] = after
        row['updated'] = (rc == 0 and _cmp(after, inst) > 0)
        if not row['updated']:
            row['update_error'] = out[-400:]
        elif _cmp(latest, after) <= 0:
            row['status'] = 'shadowed' if shadows else 'ok'
    return row


def main():
    ap = argparse.ArgumentParser(description='Check/update Clayrune agent CLIs.')
    ap.add_argument('--apply', action='store_true', help='perform updates, not just report')
    ap.add_argument('--json', action='store_true', help='machine-readable output')
    args = ap.parse_args()

    rows = [check_one(c, args.apply) for c in CLIS]
    live = [r for r in rows if r.get('status') != 'not_installed']

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

    return 1 if any(r['status'] != 'ok' for r in live) else 0


if __name__ == '__main__':
    sys.exit(main())
