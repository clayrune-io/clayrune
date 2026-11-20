"""Daily copy of every project's memory directory, and the snapshot code itself.

Two things live here, and `tools/memory-snapshot.py` calls the first:

  1. THE SNAPSHOT CODE (`create_snapshot`, `verify_snapshot`, `ordered_files`,
     `sha256`). One implementation; the command-line tool and the daily job
     below both call it, so a snapshot is the same thing whoever takes it:
     the top-level `*.md` files of one memory directory plus a `manifest.json`
     of their sizes and hashes.
  2. THE DAILY JOB (`run_once`, `start`). Once a day it snapshots every project
     that has a memory directory, verifies the copy it just wrote, and keeps
     the newest `memory_snapshot_keep` (default 14) of ITS OWN snapshots per
     project.

WHERE SNAPSHOTS LIVE. `~/.clayrune/memory-snapshots` unless
`memory_snapshot_dest_dir` names another directory. Never under the repo and
never under `data/projects/` (`load_projects()` reads every `*.json` there as a
project record; CLAUDE.md, DATA_DIR rule): a configured destination inside
either is refused. A configured destination that is missing or cannot be
written is logged once per day and nothing is written anywhere. There is no
fallback to the default directory: a copy of private notes must not land
somewhere the user did not choose. The job never creates a configured
destination either (a missing folder can be an unplugged drive).

COPY ORDER IS LOAD-BEARING. MEMORY.md first, then MEMORY_ARCHIVE.md, then topic
files. Floor eviction moves lines OUT of MEMORY.md and INTO the archive, so if
an eviction lands mid-snapshot this order yields at worst a DUPLICATED line.
The reverse order can lose one outright.

WHAT THE JOB MAY DELETE. Only snapshot directories named
`<stamp>-auto-<project id>`: the label prefix `auto-` is reserved for the job
(the tool refuses it for a manual `--label`). After a snapshot is written AND
verified, the job re-verifies its own snapshots for that project, keeps the
newest `keep` that still verify, and removes the rest of its own that are older
than the oldest one kept. A snapshot that fails to write or verify is removed
by the same pass that created it (its own debris, so a full disk does not fill
further every hour); it is never counted and never causes a prune. Manual
snapshots, and every directory the job did not name, are never touched.

NOT WHILE A RESTORE RUNS. `restoring()` marks a restore in progress, in this
process and (through a marker file under the Clayrune home) across processes. The
job checks it before the pass and again before each project. A marker older
than `RESTORE_MARKER_STALE_S` is a crashed restore's leftover: it is logged and
ignored, so one crash cannot switch the nightly copy off for good.

STATE. `memory_snapshot_state.json` in the Clayrune home, next to the restore
markers (`~/.clayrune` unless CLAYRUNE_HOME says otherwise: both paths go
through `mc.secrets_store.clayrune_home()`): per project, the last attempt,
the last success and the last error. Due is computed from the last SUCCESS, so a
failed run is retried at the next hourly tick, not a day later.

Importing this module starts nothing and needs nothing but the standard
library, so the tool can list and verify with no server and no Flask. `start()`
is called once, by server.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Generator, Iterable, Optional

from mc.atomic_json import write_json_atomic

PROTECTED = ('MEMORY.md', 'MEMORY_ARCHIVE.md')
AUTO_LABEL_PREFIX = 'auto-'
DEFAULT_KEEP = 14
INTERVAL_S = 24 * 3600
TICK_S = 3600                     # how often the daemon re-checks what is due
BOOT_DELAY_S = 90                 # let the server finish booting first
RESTORE_MARKER_STALE_S = 6 * 3600
_STAMP_FMT = '%Y%m%dT%H%M%SZ'
_STAMP_LEN = 16                   # len('20260816T120000Z')
_REPO_ROOT = Path(__file__).resolve().parent.parent


class SnapshotError(Exception):
    """A snapshot could not be taken or did not verify."""


def _say(msg: str) -> None:
    # Imported here, not at the top: mc.core pulls in Flask, and the tool must
    # be able to list and verify snapshots without it.
    try:
        from mc.core import _log
    except Exception:
        print(f"[memory-snapshot] {msg}", file=sys.stderr, flush=True)
        return
    _log(f"[memory-snapshot] {msg}", flush=True)


def _clayrune_home() -> Path:
    """The operator-state directory, through the one resolver every reader in
    mc/ uses. It honours CLAYRUNE_HOME, which is how tests/conftest.py keeps the
    whole suite out of the real one. The restore markers and the job's state
    file live here. Imported late for the same reason as in _say."""
    from mc.secrets_store import clayrune_home
    return clayrune_home()


# ── the snapshot code (shared with tools/memory-snapshot.py) ─────────────────

def default_root() -> Path:
    return Path.home() / '.clayrune' / 'memory-snapshots'


def sha256(p) -> str:
    h = hashlib.sha256()
    with open(p, 'rb') as fh:
        for chunk in iter(lambda: fh.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def ordered_files(src: Path) -> list[Path]:
    """MEMORY.md, then the archive, then everything else. See module docstring."""
    files = sorted(Path(src).glob('*.md'))
    head = [f for f in files if f.name == 'MEMORY.md']
    arch = [f for f in files if f.name == 'MEMORY_ARCHIVE.md']
    rest = [f for f in files if f.name not in PROTECTED]
    return head + arch + rest


def snapshot_dir(root: Path, label: str, now: Optional[datetime] = None) -> Path:
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(_STAMP_FMT)
    return Path(root) / f'{stamp}-{label}'


def create_snapshot(src: Path, root: Path, label: str,
                    now: Optional[datetime] = None) -> tuple[Path, dict]:
    """Copy one memory directory into `root/<stamp>-<label>`. Returns the new
    directory and its manifest. Raises SnapshotError before anything is created
    when `src` is not a memory directory."""
    src = Path(src)
    if not (src / 'MEMORY.md').is_file():
        # An empty or wrong directory (an agent worktree resolves its own empty
        # slot, not the project's) would write a 0-file "rollback" and say ok.
        raise SnapshotError(f'refusing to snapshot {src}: it has no MEMORY.md, so it is '
                            "not this project's memory directory")
    when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    dest = snapshot_dir(root, label, when)
    (dest / 'memory').mkdir(parents=True)
    entries = []
    for f in ordered_files(src):
        target = dest / 'memory' / f.name
        shutil.copy2(f, target)
        entries.append({'name': f.name, 'size': target.stat().st_size,
                        'sha256': sha256(target)})
    manifest = {
        'created': when.isoformat(),
        'label': label,
        'source': str(src),
        'count': len(entries),
        'files': entries,
    }
    write_json_atomic(dest / 'manifest.json', manifest, indent=2)
    return dest, manifest


def verify_snapshot(snap) -> dict:
    """Re-hash a snapshot against its own manifest.

    Returns {'ok', 'good', 'count', 'problems'}; `problems` is a list of
    ('missing' | 'mismatch', file name) in manifest order. Raises when the
    manifest itself cannot be read."""
    snap = Path(snap)
    man = json.loads((snap / 'manifest.json').read_text(encoding='utf-8'))
    good = 0
    problems: list[tuple[str, str]] = []
    for e in man['files']:
        p = snap / 'memory' / e['name']
        if not p.exists():
            problems.append(('missing', e['name']))
        elif sha256(p) == e['sha256']:
            good += 1
        else:
            problems.append(('mismatch', e['name']))
    return {'ok': not problems and good == man['count'], 'good': good,
            'count': man['count'], 'problems': problems}


# ── restore in progress ──────────────────────────────────────────────────────

_restores = 0
_restores_lock = threading.Lock()
_stale_marker_logged: set[str] = set()


def _marker_dir() -> Path:
    return _clayrune_home() / 'memory-restore-active'


@contextmanager
def restoring(what: str = '') -> Generator[None, None, None]:
    """Wrap anything that writes into a memory directory from a backup or a
    snapshot. The daily job does not run while one is open. Never raises on its
    own account: a marker that cannot be written is logged and the restore goes
    ahead (the in-process count still holds)."""
    global _restores
    marker: Optional[Path] = None
    with _restores_lock:
        _restores += 1
    try:
        try:
            d = _marker_dir()
            d.mkdir(parents=True, exist_ok=True)
            marker = d / f'{os.getpid()}-{uuid.uuid4().hex[:8]}'
            marker.write_text(what or 'restore', encoding='utf-8')
        except Exception as e:
            marker = None
            _say(f"could not write the restore marker ({what or 'restore'}): {e}")
        yield
    finally:
        with _restores_lock:
            _restores -= 1
        if marker is not None:
            try:
                marker.unlink()
            except Exception as e:
                _say(f"could not remove the restore marker {marker.name}: {e}")


def restore_in_progress(now_ts: Optional[float] = None) -> bool:
    with _restores_lock:
        if _restores > 0:
            return True
    t = time.time() if now_ts is None else now_ts
    try:
        markers = list(_marker_dir().iterdir())
    except FileNotFoundError:
        return False
    except Exception as e:
        _say(f"could not read the restore markers, assuming none: {e}")
        return False
    live = False
    for m in markers:
        try:
            age = t - m.stat().st_mtime
        except Exception:
            continue  # removed between the listing and the stat: that restore ended
        if age < RESTORE_MARKER_STALE_S:
            live = True
        elif m.name not in _stale_marker_logged:
            _stale_marker_logged.add(m.name)
            _say(f"ignoring restore marker {m.name}: {int(age // 3600)}h old, "
                 "left by a restore that did not finish")
    return live


# ── settings ─────────────────────────────────────────────────────────────────

def enabled(cfg: dict) -> bool:
    """ON unless explicitly switched off."""
    return bool(cfg.get('memory_snapshot_enabled', True))


def validate_keep(value) -> int:
    """An integer >= 1, or ValueError. Used by the settings route and the job."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value or int(value) < 1:
        raise ValueError('memory_snapshot_keep must be an integer >= 1')
    return int(value)


def keep_count(cfg: dict) -> int:
    raw = cfg.get('memory_snapshot_keep', DEFAULT_KEEP)
    try:
        return validate_keep(raw)
    except ValueError:
        _say(f"memory_snapshot_keep is {raw!r}, not an integer >= 1; using {DEFAULT_KEEP}")
        return DEFAULT_KEEP


def _is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _forbidden_roots() -> list[Path]:
    """The repo and data/projects/, plus whatever server.py wired (its own data
    root can differ from the repo's when MC_DATA_DIR is set)."""
    data_root = Path(os.environ.get('MC_DATA_DIR') or _REPO_ROOT)
    return [_REPO_ROOT, data_root / 'data' / 'projects', *_extra_forbidden]


def dest_refusal(dest_dir) -> Optional[str]:
    """Why a configured destination may not be used at all, or None. Checked by
    the settings route before the value is saved, and by the job every pass."""
    try:
        resolved = Path(str(dest_dir)).expanduser().resolve()
    except Exception as e:
        return f'memory_snapshot_dest_dir cannot be resolved: {e}'
    for root in _forbidden_roots():
        try:
            if _is_within(resolved, root.resolve()):
                return (f'memory_snapshot_dest_dir is inside {root} ({resolved}); snapshots '
                        'are private notes and may not live in the repo or data/projects/')
        except Exception as e:
            return f'memory_snapshot_dest_dir could not be checked against {root}: {e}'
    return None


def _probe_writable(d: Path) -> None:
    """Raise if a directory cannot be created in `d`. A directory, because that
    is what a snapshot is, and it leaves nothing behind."""
    probe = d / f'.probe-{os.getpid()}-{uuid.uuid4().hex[:8]}'
    probe.mkdir()
    probe.rmdir()


def resolve_destination(cfg: dict) -> tuple[Optional[Path], Optional[str]]:
    """(directory, None) when snapshots can be written there, else (None, why).
    Never returns a directory other than the one the settings name."""
    raw = str(cfg.get('memory_snapshot_dest_dir') or '').strip()
    try:
        if raw:
            refusal = dest_refusal(raw)
            if refusal:
                return None, refusal
            root = Path(raw).expanduser()
            if not root.is_dir():
                return None, f'destination {root} does not exist or is not a directory'
        else:
            root = default_root()
            root.mkdir(parents=True, exist_ok=True)
        _probe_writable(root)
    except Exception as e:
        return None, f'destination {raw or default_root()} is not writable: {e}'
    return root, None


# ── retention ────────────────────────────────────────────────────────────────

def auto_label(project_id: str) -> str:
    """The job's label for one project: `auto-<id>`. An id with characters that
    are unsafe in a directory name gets them replaced and a short hash of the
    real id appended, so two ids can never share a label."""
    pid = str(project_id)
    safe = re.sub(r'[^A-Za-z0-9_.-]', '_', pid).strip('.') or '_'
    if safe != pid:
        safe = f'{safe}-{hashlib.sha256(pid.encode("utf-8")).hexdigest()[:8]}'
    return AUTO_LABEL_PREFIX + safe


def own_snapshots(root: Path, label: str) -> list[Path]:
    """The job's snapshot directories for one label, newest first. Matched on
    the exact name `<stamp>-<label>`, never on a substring."""
    if not label.startswith(AUTO_LABEL_PREFIX):
        return []
    out = []
    for d in Path(root).iterdir():
        name = d.name
        if (len(name) == _STAMP_LEN + 1 + len(label) and name[_STAMP_LEN] == '-'
                and name[_STAMP_LEN + 1:] == label and d.is_dir()):
            try:
                datetime.strptime(name[:_STAMP_LEN], _STAMP_FMT)
            except ValueError:
                continue
            out.append(d)
    return sorted(out, key=lambda p: p.name, reverse=True)


def prune(root: Path, label: str, keep: int) -> list[str]:
    """Keep the newest `keep` of the job's snapshots for `label` that still
    verify; remove its older ones. Returns the names removed. A snapshot that no
    longer verifies does not count toward `keep`; it goes only once it is older
    than every snapshot kept."""
    keep = max(1, int(keep))
    good: list[Path] = []
    broken: list[Path] = []
    for d in own_snapshots(root, label):
        try:
            ok = verify_snapshot(d)['ok']
        except Exception:
            ok = False
        (good if ok else broken).append(d)
    doomed = good[keep:]
    if len(good) >= keep:
        oldest_kept = good[keep - 1].name
        doomed += [b for b in broken if b.name < oldest_kept]
    removed = []
    for d in doomed:
        try:
            shutil.rmtree(d)
        except Exception as e:
            _say(f"retention could not remove {d}: {e}")
            continue
        removed.append(d.name)
    return removed


# ── the daily job ────────────────────────────────────────────────────────────

_config_fn: Callable[[], dict] = lambda: {}
_projects_fn: Optional[Callable[[], Iterable[dict]]] = None
_memory_path_fn: Optional[Callable[[dict], Path]] = None
_extra_forbidden: list[Path] = []
_dest_error_logged: dict[str, str] = {}     # destination -> 'day|reason' last logged
_run_lock = threading.Lock()


def wire(config_fn: Callable[[], dict], projects_fn: Callable[[], Iterable[dict]],
         memory_path_fn: Callable[[dict], Path], forbidden_roots: Iterable[Path] = ()) -> None:
    """Called once by server.py. `memory_path_fn` is mc.memory._get_memory_path:
    passed in, not imported, so this module stays importable without it."""
    global _config_fn, _projects_fn, _memory_path_fn, _extra_forbidden
    _config_fn = config_fn
    _projects_fn = projects_fn
    _memory_path_fn = memory_path_fn
    _extra_forbidden = [Path(p) for p in forbidden_roots]


def state_path() -> Path:
    return _clayrune_home() / 'memory_snapshot_state.json'


def load_state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except Exception as e:
        _say(f"state unreadable, treating every project as never copied: {e}")
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict) -> None:
    try:
        p = state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, state, indent=2, ensure_ascii=False)
    except Exception as e:
        _say(f"could not save state: {e}")


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _due(rec: dict, now: datetime) -> bool:
    last = rec.get('last_success_at') if isinstance(rec, dict) else None
    if not last:
        return True
    try:
        then = datetime.strptime(last, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except Exception:
        return True
    return (now - then).total_seconds() >= INTERVAL_S


def memory_dir_of(project: dict, memory_path_fn: Callable[[dict], Path]) -> Optional[Path]:
    """The project's memory directory, or None when it has none. A project with
    no `project_path` keeps one flat file in a directory it shares with every
    other such project; that is not a memory directory and is not copied."""
    if not project.get('project_path'):
        return None
    d = Path(memory_path_fn(project)).parent
    return d if (d / 'MEMORY.md').is_file() else None


def _log_dest_error(key: str, reason: str, now: datetime) -> None:
    """Once per day per destination (again if the reason changes). Kept in
    memory: this path writes nothing, so a server restart may log it once more."""
    stamp = f'{now.strftime("%Y-%m-%d")}|{reason}'
    if _dest_error_logged.get(key) == stamp:
        return
    _dest_error_logged[key] = stamp
    _say(f"no snapshot taken, nothing written anywhere: {reason}")


def _snapshot_one(pid: str, src: Path, root: Path, keep: int, now: datetime) -> dict:
    """One project: write, verify, then prune. Raises SnapshotError on failure,
    having removed whatever this call itself created."""
    label = auto_label(pid)
    dest = snapshot_dir(root, label, now)
    if dest.exists():
        raise SnapshotError(f'{dest.name} already exists')
    try:
        dest, manifest = create_snapshot(src, root, label, now=now)
        result = verify_snapshot(dest)
        if not result['ok']:
            bad = ', '.join(f'{kind} {name}' for kind, name in result['problems'][:5])
            raise SnapshotError(f"the copy did not verify ({result['good']}/{result['count']} ok"
                                f"{': ' + bad if bad else ''})")
    except Exception as e:
        if dest.exists():
            try:
                shutil.rmtree(dest)
            except Exception as rm_err:
                _say(f"{pid}: could not remove the failed snapshot {dest}: {rm_err}")
        raise e if isinstance(e, SnapshotError) else SnapshotError(f'{type(e).__name__}: {e}')
    removed: list[str] = []
    try:
        removed = prune(root, label, keep)
    except Exception as e:
        _say(f"{pid}: retention failed (the new snapshot is kept): {e}")
    return {'snapshot': dest.name, 'files': manifest['count'],
            'bytes': sum(f['size'] for f in manifest['files']), 'removed': removed}


def run_once(now: Optional[datetime] = None, *, config: Optional[dict] = None,
             projects: Optional[Iterable[dict]] = None,
             memory_path_fn: Optional[Callable[[dict], Path]] = None) -> dict:
    """One pass: snapshot every project that is due. Never raises. The keyword
    arguments default to what server.py wired; tests pass their own."""
    if not _run_lock.acquire(blocking=False):
        return {'ran': False, 'reason': 'already_running'}
    try:
        return _run_once(now or datetime.now(timezone.utc), config, projects, memory_path_fn)
    except Exception as e:
        _say(f"pass failed: {type(e).__name__}: {e}")
        return {'ran': False, 'reason': 'error', 'detail': str(e)}
    finally:
        _run_lock.release()


def _run_once(now: datetime, config, projects, memory_path_fn) -> dict:
    cfg = config if config is not None else (_config_fn() or {})
    if not enabled(cfg):
        return {'ran': False, 'reason': 'disabled'}
    path_fn = memory_path_fn or _memory_path_fn
    if projects is None:
        projects = _projects_fn() if _projects_fn else []
    if path_fn is None:
        return {'ran': False, 'reason': 'not_wired'}

    state = load_state()
    recs = state.get('projects')
    if not isinstance(recs, dict):
        recs = state['projects'] = {}
    due: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for p in projects:
        pid = str((p or {}).get('id') or '')
        if not pid or pid in seen:
            continue
        seen.add(pid)
        try:
            src = memory_dir_of(p, path_fn)
        except Exception as e:
            _say(f"{pid}: could not resolve the memory directory: {e}")
            continue
        if src is not None and _due(recs.get(pid) or {}, now):
            due.append((pid, src))
    if not due:
        return {'ran': False, 'reason': 'not_due'}

    if restore_in_progress():
        _say(f"{len(due)} project(s) due, but a restore is in progress; trying again next tick")
        return {'ran': False, 'reason': 'restore_in_progress'}
    root, why = resolve_destination(cfg)
    if root is None:
        _log_dest_error(str(cfg.get('memory_snapshot_dest_dir') or ''), why or 'destination unusable', now)
        return {'ran': False, 'reason': 'destination', 'detail': why}
    keep = keep_count(cfg)

    report: dict = {'ran': True, 'root': str(root), 'projects': {}}
    for pid, src in sorted(due):
        if restore_in_progress():
            _say(f"{pid}: a restore started mid-pass; stopping, the rest run next tick")
            report['stopped'] = 'restore_in_progress'
            break
        rec = recs.get(pid)
        if not isinstance(rec, dict):
            rec = recs[pid] = {}
        rec['last_attempt_at'] = _iso(now)
        try:
            done = _snapshot_one(pid, src, root, keep, now)
        except Exception as e:
            rec['last_status'] = 'error'
            rec['last_error'] = str(e)[:500]
            report['projects'][pid] = {'ok': False, 'error': str(e)}
            _say(f"{pid}: snapshot failed, nothing pruned: {e}")
            continue
        rec.update({'last_success_at': _iso(now), 'last_status': 'ok', 'last_error': None,
                    'last_snapshot': done['snapshot']})
        report['projects'][pid] = {'ok': True, **done}
        _say(f"{pid}: wrote and verified {done['snapshot']} ({done['files']} files, "
             f"{done['bytes']} bytes)"
             + (f"; removed {len(done['removed'])} older automatic snapshot(s)" if done['removed'] else ''))
    state['last_run_at'] = _iso(now)
    _save_state(state)
    return report


def _loop() -> None:
    from mc import obs
    time.sleep(BOOT_DELAY_S)
    while True:
        try:
            obs.heartbeat('memory-snapshot')
            run_once()
        except Exception as e:  # run_once never raises; belt and braces for the thread
            _say(f"loop error: {e}")
        time.sleep(TICK_S)


def start() -> None:
    """Server-process only (server.py boot). Importing this module starts nothing."""
    if _projects_fn is None or _memory_path_fn is None:
        return
    threading.Thread(target=_loop, daemon=True, name='memory-snapshot').start()
