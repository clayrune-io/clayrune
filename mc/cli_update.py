"""Built-in daily update of the agent CLIs Clayrune dispatches to (MC-1025).

WHY. The daily check used to be tools/cli-version-check.py plus a schedule on
one operator's box, so no other install ever got it: a Mac sat on codex 0.153.0
(no gpt-6 catalog) until every run failed 400. This is the same job run by the
server for every install, ON by default (Settings -> Providers, config
`cli_auto_update_enabled`).

THE RULES (each one is a past incident, not taste):
- Update with the updater that matches HOW the binary was installed
  (`mc/cli_install.py`). An unknown method is REPORTED, never guessed -- npm
  over a standalone install leaves two copies and PATH picks one at random.
- `latest` comes from the package registry (`npm view` is a version oracle
  only, it never selects the updater), else the vendor's own source (GitHub
  releases for codex, PyPI for aider), else it stays UNKNOWN and nothing is
  updated. A version is never guessed.
- A CLI a live session or registered child process is using is SKIPPED, and
  retried on the next hourly tick (MC-991: an in-place write over a running
  Windows exe EBUSYs and can leave the CLI downgraded). Process enumeration
  that FAILS is not "nothing running": it also skips.
- Nothing is ever killed.
- State lives in a sidecar OUTSIDE data/projects (DATA_DIR pollution rule,
  CLAUDE.md): `data/cli_update_state.json`, wired by server.py.

Every outward dependency (subprocess, HTTP, process list, in-use probe, clock)
is a parameter so the tests drive the whole decision tree without touching the
machine.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from mc import cli_install, process_sweep
from mc.atomic_json import write_json_atomic
from mc.core import _log

# Wired by server.py at startup (same seam as allowance_state.wire): a sibling
# of data/allowance_state.json, never a member of data/projects/.
STATE_PATH: Optional[Path] = None

# The CLIs this loop knows how to look at. A runtime outside this tuple
# (goose, kiro, ...) has no version oracle or updater here and is left alone.
CLI_NAMES = ('claude', 'codex', 'gemini', 'qwen', 'opencode', 'aider')

# A CLI is re-checked once its last check is older than this; the loop wakes
# hourly, so a daily cadence survives restarts without a second timer.
CHECK_INTERVAL_S = 23 * 3600
# ...except one we could not touch because it was in use: that retries hourly.
RETRY_STATUSES = frozenset({'skipped_in_use'})
UPDATE_TIMEOUT_S = 600

_GITHUB_RELEASES = {'codex': 'openai/codex'}
# npm package that serves as the VERSION ORACLE per CLI. For codex/claude this
# says nothing about how they were installed (see cli_install.update_command).
_NPM_ORACLE = {'codex': '@openai/codex', 'claude': '@anthropic-ai/claude-code',
               **cli_install.NPM_PACKAGES}
_VER = re.compile(r'(\d+\.\d+\.\d+)')

_lock = threading.Lock()
_STATE: Dict[str, Any] = {'clis': {}}


# ── state ────────────────────────────────────────────────────────────────────

def wire(state_path) -> None:
    global STATE_PATH
    STATE_PATH = Path(state_path)
    _load()


def _load() -> None:
    global _STATE
    _STATE = {'clis': {}}
    if STATE_PATH is None or not STATE_PATH.exists():
        return
    try:
        data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        if isinstance(data, dict) and isinstance(data.get('clis'), dict):
            _STATE = data
    except Exception as e:
        _log(f"[cli-update] could not read {STATE_PATH}: {e}", flush=True)


def _save() -> None:
    if STATE_PATH is None:
        return
    try:
        write_json_atomic(STATE_PATH, _STATE, indent=2)
    except Exception as e:
        _log(f"[cli-update] could not write {STATE_PATH}: {e}", flush=True)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec='seconds')


def _day(iso: Optional[str]) -> str:
    return (iso or '')[:10]


# ── probes ───────────────────────────────────────────────────────────────────

def _run(argv: List[str], timeout: int = 60, env: Optional[dict] = None) -> Tuple[int, str]:
    """(rc, combined output). Never raises. stdin=DEVNULL for the same reason as
    tools/cli-version-check.py: an inherited stdin handle is invalid under a
    console-less launch and raises before the child runs."""
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if sys.platform == 'win32' else 0
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL, encoding='utf-8', errors='replace',
                           env=env, creationflags=flags)
        return r.returncode, ((r.stdout or '') + (r.stderr or '')).strip()
    except Exception as e:
        return 1, '%s: %s' % (type(e).__name__, e)


def _http_json(url: str, timeout: int = 15) -> Any:
    req = urllib.request.Request(url, headers={'User-Agent': 'clayrune-cli-update',
                                               'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _ver(text: Optional[str]) -> Optional[str]:
    m = _VER.search(text or '')
    return m.group(1) if m else None


def _cmp(a: Optional[str], b: Optional[str]) -> int:
    """-1/0/1 on dotted versions; 0 when either side is unknown."""
    if not a or not b:
        return 0
    pa = [int(x) for x in a.split('.')]
    pb = [int(x) for x in b.split('.')]
    return (pa > pb) - (pa < pb)


def installed_version(binary: Any, run_fn: Callable = _run) -> Optional[str]:
    rc, out = run_fn([str(binary), '--version'], 60)
    return _ver(out) if rc == 0 or out else None


def npm_exe(binary: Any) -> Optional[str]:
    """npm from the SAME bin dir as the binary when it is there (an nvm or
    custom-prefix install), else the one on PATH. Beside-the-binary is also the
    npm that owns that copy, so it can never update a shadow."""
    here = os.path.dirname(os.path.realpath(str(binary)))
    for d in (here, os.path.dirname(str(binary))):
        for exe in ('npm.cmd', 'npm'):
            cand = os.path.join(d, exe)
            if d and os.path.isfile(cand):
                return cand
    return shutil.which('npm') or shutil.which('npm.cmd')


def latest_version(name: str, binary: Any, run_fn: Callable = _run,
                   http_fn: Callable = _http_json) -> Tuple[Optional[str], str]:
    """(version, source). source names what answered, or says why nothing did.
    npm registry first (a version oracle only), then the vendor's own feed;
    (None, ...) when neither answers -- the caller reports 'latest unknown'."""
    pkg = _NPM_ORACLE.get(name)
    notes = []
    if pkg:
        npm = npm_exe(binary)
        if npm:
            rc, out = run_fn([npm, 'view', pkg, 'version'], 60)
            v = _ver(out) if rc == 0 else None
            if v:
                return v, 'npm'
            notes.append('npm view failed')
        else:
            notes.append('npm not found')
    repo = _GITHUB_RELEASES.get(name)
    if repo:
        try:
            tag = (http_fn('https://api.github.com/repos/%s/releases/latest' % repo) or {}).get('tag_name')
            v = _ver(tag)
            if v:
                return v, 'github'
            notes.append('github release had no version')
        except Exception as e:
            notes.append('github: %s' % type(e).__name__)
    pypi = cli_install.PYPI_PACKAGES.get(name)
    if pypi:
        try:
            v = _ver(((http_fn('https://pypi.org/pypi/%s/json' % pypi) or {}).get('info') or {}).get('version'))
            if v:
                return v, 'pypi'
            notes.append('pypi had no version')
        except Exception as e:
            notes.append('pypi: %s' % type(e).__name__)
    return None, '; '.join(notes) or 'no version source for this CLI'


def build_argv(name: str, binary: Any, command: str,
               platform: Optional[str] = None) -> Tuple[Optional[List[str]], Optional[dict], str]:
    """(argv, env, reason). `command` is cli_install.update_command's string for
    this binary; only those fixed shapes are turned into argv, so nothing here
    is composed from user input. env prepends the binary's own bin dir to PATH
    so an nvm-installed npm finds its node. argv None => reason says why."""
    plat = platform or sys.platform
    env = dict(os.environ)
    bin_dir = os.path.dirname(os.path.realpath(str(binary)))
    env['PATH'] = bin_dir + os.pathsep + env.get('PATH', '')
    if command == 'claude update':
        return [str(binary), 'update'], env, ''
    if '|' in command:
        if command.startswith('powershell'):
            return (['powershell', '-ExecutionPolicy', 'ByPass', '-c',
                     command.split(' -c ', 1)[1].strip('"')], env, '')
        sh = shutil.which('sh')
        return ([sh, '-c', command], env, '') if sh else (None, None, 'sh not found')
    argv = shlex.split(command, posix=(plat != 'win32'))
    if argv[0] == 'npm':
        npm = npm_exe(binary)
        if not npm:
            return None, None, 'npm not found, so the npm-installed copy cannot be updated from here'
        argv[0] = npm
    elif argv[0] == 'python':
        py = cli_install.sibling_python(binary)
        if not py:
            return None, None, 'no Python interpreter beside the binary'
        argv[0] = py
    else:
        exe = shutil.which(argv[0])
        if not exe:
            return None, None, '%s not found on PATH' % argv[0]
        argv[0] = exe
    return argv, env, ''


def install_root(name: str, binary: Any) -> Tuple[str, bool]:
    """(directory an update rewrites, exact). The npm package dir or the
    standalone package tree; else the binary's own dir, and then `exact` is
    True: that dir is shared (~/.local/bin), so only the binary itself counts
    as a locker, not every exe beside it."""
    try:
        real = os.path.realpath(str(binary))
    except Exception:
        real = str(binary)
    parts = real.replace('\\', '/').split('/')
    low = [p.lower() for p in parts]
    if 'node_modules' in low:
        i = len(low) - 1 - low[::-1].index('node_modules')
        take = 2 if i + 1 < len(parts) and parts[i + 1].startswith('@') else 1
        return os.path.normpath('/'.join(parts[:i + 1 + take])), False
    if 'standalone' in low:
        return os.path.normpath('/'.join(parts[:low.index('standalone') + 1])), False
    pkg = _NPM_ORACLE.get(name)
    if pkg:  # a Windows npm shim sits in the prefix, the package dir beside it
        cand = os.path.join(os.path.dirname(real), 'node_modules', *pkg.split('/'))
        if os.path.isdir(cand):
            return cand, False
    return os.path.dirname(real), True


def os_users(name: str, binary: Any,
             list_fn: Callable = process_sweep.list_processes) -> Tuple[Optional[List[str]], str]:
    """Processes running out of the CLI's install root.
    ([...], '') when enumerated; (None, reason) when enumeration FAILED --
    which the caller treats as "in use", never as "nothing running".
    (Enumeration is Windows-only in process_sweep; elsewhere it returns [] and
    replacing a running binary is safe, the session probe still applies.)"""
    procs = list_fn()
    if procs is None:
        return None, 'process enumeration failed'
    root, exact = install_root(name, binary)
    me = os.path.normcase(os.path.realpath(str(binary)))
    hits = []
    for p in procs:
        exe = p.get('exe')
        if not exe:
            continue
        if exact:
            if os.path.normcase(os.path.realpath(exe)) == me:
                hits.append(p)
        elif process_sweep._under_dir(exe, root):
            hits.append(p)
    return ['pid %s (%s)' % (p.get('pid'), p.get('name')) for p in hits], ''


# ── one CLI ──────────────────────────────────────────────────────────────────

def check_one(name: str, binary: Any, *, in_use_fn: Callable[[str], List[str]],
              now: float, run_fn: Callable = _run, http_fn: Callable = _http_json,
              list_fn: Callable = process_sweep.list_processes,
              platform: Optional[str] = None) -> Dict[str, Any]:
    """Look at one installed CLI and, if it is behind and nothing is using it,
    update it with the updater matching how it was installed. Returns the
    record to store; never raises."""
    rec: Dict[str, Any] = {'checked_ts': now, 'checked_at': _iso(now), 'path': str(binary)}
    try:
        inst = installed_version(binary, run_fn)
        rec['installed'] = inst
        if not inst:
            return {**rec, 'status': 'version_unreadable',
                    'detail': '`%s --version` printed no version' % name}
        latest, src = latest_version(name, binary, run_fn, http_fn)
        rec['latest'], rec['latest_source'] = latest, src
        if not latest:
            return {**rec, 'status': 'latest_unknown',
                    'detail': 'could not learn the latest version (%s)' % src}
        if _cmp(latest, inst) <= 0:
            return {**rec, 'status': 'up_to_date'}

        command = cli_install.update_command(name, binary, platform)
        method = cli_install.install_method(binary, platform, name)
        rec['install_method'] = method or 'unknown'
        if not command:
            return {**rec, 'status': 'unknown_install_method',
                    'detail': ('%s is behind %s but Clayrune cannot tell how it was installed, '
                               'so it is not guessing an updater. Update it with the tool '
                               'you installed it with.' % (inst, latest))}
        argv, env, why = build_argv(name, binary, command, platform)
        if not argv:
            return {**rec, 'status': 'updater_unavailable', 'detail': why}

        reasons = list(in_use_fn(name) or [])
        users, fail = os_users(name, binary, list_fn)
        if users is None:
            reasons.append(fail)
        else:
            reasons.extend(users)
        if reasons:
            return {**rec, 'status': 'skipped_in_use', 'in_use_by': reasons[:5],
                    'detail': 'in use: ' + '; '.join(reasons[:3])}

        rc, out = run_fn(argv, UPDATE_TIMEOUT_S, env)
        after = installed_version(binary, run_fn)
        rec['installed_after'] = after
        if rc == 0 and _cmp(after, inst) > 0:
            return {**rec, 'status': 'updated',
                    'last_update': {'from': inst, 'to': after, 'at': _iso(now)}}
        if rc == 0 and after == inst:
            return {**rec, 'status': 'update_no_change',
                    'detail': ('the updater finished but the version is still %s '
                               '(latest published: %s)' % (inst, latest))}
        detail = out[-300:] or 'updater exited %s' % rc
        if after and _cmp(after, inst) < 0:
            detail = 'DOWNGRADED %s -> %s by a failed update. %s' % (inst, after, detail)
        return {**rec, 'status': 'update_failed', 'detail': detail}
    except Exception as e:
        _log(f"[cli-update] {name} check failed: {e}", flush=True)
        return {**rec, 'status': 'check_error', 'detail': '%s: %s' % (type(e).__name__, e)}


# ── the pass ─────────────────────────────────────────────────────────────────

def _due(entry: Optional[dict], now: float) -> bool:
    if not entry:
        return True
    age = now - float(entry.get('checked_ts') or 0)
    return age >= (3600 if entry.get('status') in RETRY_STATUSES else CHECK_INTERVAL_S)


def run_once(*, enabled_fn: Callable[[], bool], runtimes_fn: Callable[[], list],
             in_use_fn: Callable[[str], List[str]], now_fn: Callable[[], float] = time.time,
             run_fn: Callable = _run, http_fn: Callable = _http_json,
             list_fn: Callable = process_sweep.list_processes,
             platform: Optional[str] = None, force: bool = False) -> Dict[str, Any]:
    """One pass over the installed CLIs that are due. With the toggle OFF it
    does nothing at all: no probe, no subprocess, no sidecar write."""
    if not enabled_fn():
        return {'ok': True, 'skipped': True, 'reason': 'cli_auto_update_enabled is false'}
    now = now_fn()
    out: Dict[str, Any] = {}
    for rt in runtimes_fn():
        name = getattr(rt, 'name', '')
        if name not in CLI_NAMES:
            continue
        with _lock:
            prior = _STATE['clis'].get(name)
        if not force and not _due(prior, now):
            continue
        try:
            binary = rt.resolve_binary()
        except Exception:
            binary = None
        if not binary or not os.path.isfile(str(binary)):
            rec = {'checked_ts': now, 'checked_at': _iso(now), 'status': 'not_installed'}
        else:
            rec = check_one(name, binary, in_use_fn=in_use_fn, now=now, run_fn=run_fn,
                            http_fn=http_fn, list_fn=list_fn, platform=platform)
        # "updated to X on date" must outlive tomorrow's up_to_date record.
        if 'last_update' not in rec and prior and prior.get('last_update'):
            rec['last_update'] = prior['last_update']
        with _lock:
            _STATE['clis'][name] = rec
            _save()
        out[name] = rec['status']
        if rec['status'] in ('updated', 'update_failed', 'skipped_in_use', 'unknown_install_method'):
            _log(f"[cli-update] {name}: {rec['status']} {rec.get('detail') or ''}".rstrip(), flush=True)
    return {'ok': True, 'checked': out}


# ── provider row ─────────────────────────────────────────────────────────────

def provider_summary(name: str) -> Optional[Dict[str, Any]]:
    """What the provider row shows for this CLI, or None before any check."""
    with _lock:
        rec = dict(_STATE['clis'].get(name) or {})
    if not rec or rec.get('status') == 'not_installed':
        return None
    st, inst, latest = rec.get('status'), rec.get('installed'), rec.get('latest')
    when = _day(rec.get('checked_at'))
    lu = rec.get('last_update') or {}
    if st == 'updated':
        text = 'Auto-updated %s → %s on %s' % (lu.get('from'), lu.get('to'), _day(lu.get('at')))
    elif st == 'up_to_date':
        text = 'Up to date (checked %s)' % when
    elif st == 'skipped_in_use':
        text = 'Update to %s waiting: %s. Retries hourly.' % (latest, rec.get('detail'))
    elif st == 'latest_unknown':
        text = 'Latest version unknown (checked %s): %s' % (when, rec.get('detail'))
    elif st in ('update_failed', 'update_no_change', 'unknown_install_method',
                'updater_unavailable', 'version_unreadable', 'check_error'):
        label = {'update_failed': 'Auto-update failed',
                 'update_no_change': 'Auto-update changed nothing',
                 'unknown_install_method': 'Not auto-updated',
                 'updater_unavailable': 'Not auto-updated',
                 'version_unreadable': 'Version check failed',
                 'check_error': 'Version check failed'}[st]
        text = '%s (%s): %s' % (label, when, rec.get('detail'))
    else:
        text = ''
    if lu and st != 'updated':
        text += ' · last auto-update %s → %s on %s' % (lu.get('from'), lu.get('to'), _day(lu.get('at')))
    return {'status': st, 'text': text, 'checked_at': rec.get('checked_at'),
            'installed': inst, 'latest': latest, 'last_update': lu or None}
