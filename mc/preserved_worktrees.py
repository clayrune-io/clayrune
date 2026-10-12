"""Read-only survey of the agent worktrees `gc_stale` keeps "for the human".

`agent_worktree.gc_stale` merges an orphaned session's worktree back and
removes it, but PRESERVES any worktree still holding unmerged or uncommitted
work. Until now the only trace of that was a log line from the startup gc
(preserved=608 on 2026-10-10). This module answers "which ones, and what is in
them" without touching any of them.

READ-ONLY BY CONSTRUCTION. The only git subcommands issued here are
`rev-parse --verify`, `worktree list --porcelain`, `for-each-ref`, `rev-list
--count`, `log --no-walk` and `status --porcelain`. Nothing here removes,
prunes, merges, resets or checks out a worktree or branch, and none of it
writes to disk. What happens to a preserved worktree is the human's call.

"Preserved" follows the gc's own verdict, not a re-derivation of it. The
worktrees listed are those `remove()` would refuse, i.e. the predicate in
`agent_worktree.has_unmerged_work` (raw `git status --porcelain` dirty, OR
commits not reachable from `_base_ref`, OR a base ref that cannot be
resolved), restricted to sessions that are not live. The base ref, the
worktree path, the session-id listing and the runtime-path exclusion all come
from `agent_worktree`; they are not copied here.

COST. 500+ worktrees would be 2000+ serial git processes done naively. Per
project this does ONE `worktree list`, ONE `for-each-ref --merged` (branches
already in the base need no count), ONE batched `log --no-walk` for commit
times, and then only `rev-list --count` per unmerged tip plus `status` per
worktree, the latter two run on a small thread pool under a wall-clock budget.
Results are cached per project for CACHE_TTL seconds and computed
single-flight, so a burst of requests costs one survey.

DISK USE. The whole `.clayrune/agents` folder (hundreds of folders, 800k+
files, ~30 s to walk) is measured by `start_measure`: one background daemon
thread per project, never on a request path. The result is cached with a
wall-clock timestamp and served as-is (with its age) until a later explicit
request re-measures; a request that finds no result sees "measuring" or
"unmeasured", it never waits. The walk uses `os.scandir`, counts apparent file
sizes (not on-disk clusters; hardlinks count once per name), and does not
descend into a symlink, a junction or any other reparse point.

UNREGISTERED. `unregistered_folders` lists the folders directly under
`.clayrune/agents` that `git worktree list --porcelain` does not know, comparing
paths through `agent_worktree._norm_path` (case-folded, one slash style, no
trailing slash). If git's list cannot be read the answer is None, never "all of
them".
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import mc.agent_worktree as _wt
import mc.project_sync as _sync

CACHE_TTL = 60.0          # seconds a project's survey is reused
MAX_WORKTREES = 1500      # hard cap on worktrees examined per project
WORKERS = 8               # concurrent read-only git processes
STATUS_BUDGET = 90.0      # wall-clock seconds for the per-worktree status phase
SIZE_BUDGET = 20.0        # wall-clock seconds for the opt-in size walk
GIT_TIMEOUT = 20
DISK_BUDGET = 600.0       # wall-clock ceiling for one background disk measurement
_REPARSE_POINT = 0x400    # FILE_ATTRIBUTE_REPARSE_POINT: symlink, junction, mount point

_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_flight: dict[tuple, threading.Lock] = {}
# project id -> {'running': bool, 'started_at': epoch | None, 'last': dict | None}
_disk: dict[str, dict] = {}


def _flight_lock(key: tuple) -> threading.Lock:
    with _cache_lock:
        return _flight.setdefault(key, threading.Lock())


def _git(cwd: str, args: list[str]) -> tuple[bool, str]:
    return _sync.git_run(cwd, args, timeout=GIT_TIMEOUT)


def _porcelain_worktrees(base: str) -> dict[str, dict]:
    """normalised path -> {'head': sha, 'branch': short name | None}"""
    ok, out = _git(base, ['worktree', 'list', '--porcelain'])
    res: dict[str, dict] = {}
    if not ok:
        return res
    for block in out.split('\n\n'):
        rec: dict = {}
        path = ''
        for line in block.splitlines():
            key, _, val = line.partition(' ')
            if key == 'worktree':
                path = val
            elif key == 'HEAD':
                rec['head'] = val
            elif key == 'branch':
                rec['branch'] = val[len('refs/heads/'):] if val.startswith('refs/heads/') else val
        if path:
            rec.setdefault('branch', None)
            res[_wt._norm_path(path)] = rec
    return res


def _dir_size(root: Path, deadline: float) -> tuple[int | None, bool]:
    """Bytes under `root`, never following a symlink or junction (the linked
    runtime dirs point at the MAIN checkout's .venv/node_modules/data). Returns
    (bytes, complete); (None, False) when the deadline ran out before the walk
    finished."""
    total = 0
    stack = [str(root)]
    while stack:
        if time.monotonic() > deadline:
            return None, False
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if _wt._is_link(e.path):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total, True


def _is_reparse(entry: os.DirEntry) -> bool:
    """True for a symlink, a Windows junction or any other reparse point, read
    from the directory listing itself (no extra syscall on Windows). A junction
    is not `is_symlink()`, hence the attribute check."""
    try:
        if entry.is_symlink():
            return True
        return bool(getattr(entry.stat(follow_symlinks=False), 'st_file_attributes', 0)
                    & _REPARSE_POINT)
    except OSError:
        return True      # cannot tell: do not walk into it


def _top_is_dir(entry: os.DirEntry) -> bool:
    try:
        return entry.is_dir()      # follows a link: same rule as list_worktrees
    except OSError:
        return False


def _walk_tree(root: str, deadline: float) -> tuple[int, int, int, bool]:
    """(bytes, files, errors, complete) under `root`. Links and reparse points
    are neither counted nor entered; an unreadable directory or file adds to
    `errors` and the walk goes on. `complete` is False when `deadline` (a
    time.monotonic() value) passed first."""
    total = files = errors = 0
    stack = [root]
    while stack:
        if time.monotonic() > deadline:
            return total, files, errors, False
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if _is_reparse(e):
                            continue
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            files += 1
                    except OSError:
                        errors += 1
        except OSError:
            errors += 1
    return total, files, errors, True


def measure_agents_dir(project: dict, budget: float = DISK_BUDGET) -> dict | None:
    """Synchronous walk of every folder directly under the project's
    `.clayrune/agents`. Returns

      {'bytes', 'files', 'folders', 'errors', 'complete', 'elapsed_s',
       'measured_at', 'per_folder': {name: {'bytes', 'files', 'link'}}}

    or None when the project has no agents dir. A top-level folder that is
    itself a link is listed (`link`: True) with 0 bytes and not entered. Loose
    files directly in the agents dir (the gc cache) are not folders and are not
    counted. This is slow on a big tree: callers use `start_measure`."""
    root = _wt.worktree_root(project)
    if root is None or not root.is_dir():
        return None
    t0 = time.monotonic()
    deadline = t0 + budget
    per: dict[str, dict] = {}
    total = nfiles = errors = 0
    complete = True
    try:
        with os.scandir(root) as it:
            tops = sorted((e for e in it if _top_is_dir(e)), key=lambda e: e.name)
    except OSError:
        return None
    for e in tops:
        if _is_reparse(e):
            per[e.name] = {'bytes': 0, 'files': 0, 'link': True}
            continue
        b, f, er, ok = _walk_tree(e.path, deadline)
        total += b
        nfiles += f
        errors += er
        if not ok:
            complete = False    # this folder is half-counted: in the totals, not in per_folder
            break
        per[e.name] = {'bytes': b, 'files': f, 'link': False}
    return {'bytes': total, 'files': nfiles, 'folders': len(per), 'errors': errors,
            'complete': complete, 'elapsed_s': round(time.monotonic() - t0, 2),
            'measured_at': time.time(), 'per_folder': per}


def start_measure(project: dict, max_age: float | None = None) -> bool:
    """Start `measure_agents_dir` on a daemon thread unless one is already
    running for this project. With `max_age` (seconds) an existing result that
    young is kept and nothing starts. Returns True when a thread was started.
    The previous result stays visible through `disk_status` while it runs."""
    pid = project.get('id', '')
    with _cache_lock:
        st = _disk.setdefault(pid, {'running': False, 'started_at': None, 'last': None})
        if st['running']:
            return False
        last = st['last']
        if max_age is not None and last and time.time() - last['measured_at'] < max_age:
            return False
        st['running'] = True
        st['started_at'] = time.time()

    def _run():
        res = None
        try:
            res = measure_agents_dir(project)
        except Exception:
            res = None
        finally:
            with _cache_lock:
                st['running'] = False
                if res is not None:
                    st['last'] = res

    threading.Thread(target=_run, name=f'pw-disk-{pid}', daemon=True).start()
    return True


def disk_status(project: dict) -> dict:
    """What is known about the project's agents-dir size right now. Never
    walks. `state` is 'unmeasured' (no result, none running), 'measuring' (a
    walk is running; the previous result, if any, is still reported) or
    'measured'. `age_s` is seconds since `measured_at`."""
    with _cache_lock:
        st = _disk.get(project.get('id', ''))
        running = bool(st and st['running'])
        last = st['last'] if st else None
        since = st['started_at'] if running else None
    now = time.time()
    out = {'state': 'measuring' if running else ('measured' if last else 'unmeasured'),
           'bytes': None, 'files': None, 'folders': None, 'complete': None,
           'errors': None, 'elapsed_s': None, 'measured_at': None, 'age_s': None,
           'measuring_for_s': round(now - since, 1) if since else None}
    if last:
        out.update({k: last[k] for k in ('bytes', 'files', 'folders', 'complete',
                                         'errors', 'elapsed_s', 'measured_at')})
        out['age_s'] = round(now - last['measured_at'], 1)
    return out


def unregistered_folders(project: dict, git_wts: dict | None = None) -> list[dict] | None:
    """Folders directly under `.clayrune/agents` that git does not list as a
    worktree, as [{'name', 'path'}] sorted by name. `git_wts` is a
    `_porcelain_worktrees` result when the caller already has one. None when
    git's list is unavailable (empty: a repo always lists its own checkout) or
    there is no agents dir; [] when every folder is registered."""
    root = _wt.worktree_root(project)
    base = project.get('project_path') or ''
    if root is None or not root.is_dir():
        return None
    known = git_wts if git_wts is not None else _porcelain_worktrees(base)
    if not known:
        return None
    try:
        with os.scandir(root) as it:
            names = sorted(e.name for e in it if _top_is_dir(e))
    except OSError:
        return None
    return [{'name': n, 'path': str(root / n)} for n in names
            if not _is_known(root / n, known)]


def _is_known(folder: Path, known: dict) -> bool:
    """Is `folder` one of git's worktree paths. Normalised comparison first; a
    folder that misses is retried through its resolved path, so a project path
    reached through a link (git prints the real one) is not reported wholesale
    as unregistered."""
    if _wt._norm_path(str(folder)) in known:
        return True
    try:
        return _wt._norm_path(os.path.realpath(folder)) in known
    except OSError:
        return False


def unregistered_with_sizes(project: dict, unregistered: list[dict] | None) -> list[dict] | None:
    """Copy of `unregistered_folders` rows with `size_bytes` / `files` filled
    from the last disk measurement (None when that folder was not measured)."""
    if unregistered is None:
        return None
    with _cache_lock:
        st = _disk.get(project.get('id', ''))
        per = st['last']['per_folder'] if st and st['last'] else {}
    out = []
    for u in unregistered:
        m = per.get(u['name'])
        out.append(dict(u, size_bytes=m['bytes'] if m else None,
                        files=m['files'] if m else None, link=bool(m and m['link'])))
    return out


def items_with_disk(project: dict, items: list[dict]) -> tuple[list[dict], dict]:
    """Copies of the survey `items` with `size_bytes` filled from the last disk
    measurement (an explicit size=1 walk's number is kept), plus the sum over
    the items no live session holds:

      {'bytes', 'files', 'measured', 'folders', 'complete'}

    `bytes` is None until a measurement exists. `complete` is False when some
    folder was not measured (so the sum is a lower bound). Never walks."""
    with _cache_lock:
        st = _disk.get(project.get('id', ''))
        per = st['last']['per_folder'] if st and st['last'] else {}
    out, total, files, measured, folders = [], 0, 0, 0, 0
    for it in items:
        it = dict(it)
        m = per.get(Path(it['path']).name)
        if it.get('size_bytes') is None and m:
            it['size_bytes'] = m['bytes']
        out.append(it)
        if it.get('live_session'):
            continue
        folders += 1
        if m:
            total += m['bytes']
            files += m['files']
            measured += 1
    return out, {'bytes': total if per else None, 'files': files if per else None,
                 'measured': measured, 'folders': folders,
                 'complete': bool(per) and measured == folders}


def survey_project(project: dict, is_live: Callable[[str], bool] | None = None,
                   include_size: bool = False) -> dict:
    """Uncached survey of one project. Never raises; a git failure degrades the
    affected field to None. Returns

      {'project_id', 'worktrees_total', 'preserved', 'in_use', 'base',
       'partial', 'unregistered': [...] | None, 'items': [...]}

    where each item is a worktree holding unmerged/uncommitted work (see module
    docstring). `preserved` counts items with no live session; `in_use` counts
    items whose session is live (gc skips those)."""
    pid = project.get('id', '')
    base = project.get('project_path') or ''
    out = {'project_id': pid, 'worktrees_total': 0, 'preserved': 0, 'in_use': 0,
           'base': None, 'partial': False, 'items': [], 'unregistered': None}
    ids = _wt.list_worktrees(project) if base else []
    out['worktrees_total'] = len(ids)
    if not ids:
        return out
    if len(ids) > MAX_WORKTREES:
        ids = sorted(ids)[:MAX_WORKTREES]
        out['partial'] = True

    ref = _wt._base_ref(project)
    out['base'] = ref
    git_wts = _porcelain_worktrees(base)
    out['unregistered'] = unregistered_folders(project, git_wts)
    ok, _ = _git(base, ['rev-parse', '--verify', ref])
    base_ok = ok
    merged_tips: set[str] = set()
    if base_ok:
        ok, tips = _git(base, ['for-each-ref', f'--merged={ref}',
                               '--format=%(objectname)', 'refs/heads/clayrune/agent/'])
        if ok:
            merged_tips = {t.strip() for t in tips.splitlines() if t.strip()}

    rows = []
    for sid in sorted(ids):
        wt = _wt.worktree_path(project, sid)
        if wt is None:
            continue
        info = git_wts.get(_wt._norm_path(str(wt)))
        rows.append({'sid': sid, 'wt': wt, 'info': info,
                     'head': (info or {}).get('head')})

    # Commits ahead of the base, once per distinct tip. Tips already merged
    # into the base need no git call.
    ahead_by_sha: dict[str, int | None] = {}
    todo = sorted({r['head'] for r in rows if r['head']} - merged_tips)
    for sha in merged_tips:
        ahead_by_sha[sha] = 0
    if base_ok and todo:
        def _count(sha):
            ok, o = _git(base, ['rev-list', '--count', f'{ref}..{sha}'])
            try:
                return sha, int(o.strip()) if ok else None
            except ValueError:
                return sha, None
        with ThreadPoolExecutor(WORKERS) as ex:
            ahead_by_sha.update(ex.map(_count, todo))
    else:
        for sha in todo:
            ahead_by_sha[sha] = None   # base unknown: gc fails closed, so do we

    # Last-commit time for every distinct tip, a few hundred shas per call.
    commit_at: dict[str, tuple[int, str]] = {}
    shas = sorted({r['head'] for r in rows if r['head']})
    for i in range(0, len(shas), 150):
        ok, o = _git(base, ['log', '--no-walk=unsorted', '--format=%H%x1f%ct%x1f%cI',
                            *shas[i:i + 150], '--'])
        if not ok:
            continue
        for line in o.splitlines():
            parts = line.split('\x1f')
            if len(parts) == 3 and parts[1].isdigit():
                commit_at[parts[0]] = (int(parts[1]), parts[2])

    # Dirty state per worktree: the one inherently per-worktree call. `raw` is
    # what the gc sees (`_sync._dirty`); `real` drops the linked-runtime paths
    # (`dirty_outside_runtime`) that leak the MAIN tree's untracked files into
    # every worktree. Only worktrees the raw check flags need the second call.
    deadline = time.monotonic() + STATUS_BUDGET

    def _dirty_pair(r):
        if r['info'] is None or time.monotonic() > deadline:
            return r['sid'], None, None
        wts = str(r['wt'])
        ok, o = _git(wts, ['status', '--porcelain'])
        if not ok:
            return r['sid'], None, None
        raw = bool(o.strip())
        return r['sid'], raw, (_wt.dirty_outside_runtime(project, r['wt']) if raw else False)

    with ThreadPoolExecutor(WORKERS) as ex:
        dirty = {sid: (raw, real) for sid, raw, real in ex.map(_dirty_pair, rows)}
    if any(v[0] is None for r in rows if r['info'] is not None
           for v in [dirty[r['sid']]]):
        out['partial'] = True

    items = []
    for r in rows:
        sid, head = r['sid'], r['head']
        raw, real = dirty.get(sid, (None, None))
        ahead = ahead_by_sha.get(head) if head else None
        # has_unmerged_work, restated over the batched facts: dirty, or ahead,
        # or "cannot prove it is merged" (fails closed, so unknowns are kept).
        if raw is False and ahead == 0:
            continue
        reasons = []
        if r['info'] is None:
            reasons.append('unregistered')       # dir exists, git has no such worktree
        elif ahead is None:
            reasons.append('base_unresolved' if not base_ok else 'ahead_unknown')
        elif ahead > 0:
            reasons.append('commits_ahead')
        if raw is None and r['info'] is not None:
            reasons.append('dirty_unchecked')
        elif raw:
            reasons.append('uncommitted' if real else 'runtime_files_only')
        live = bool(is_live(sid)) if is_live else False
        ts = commit_at.get(head, (None, None))
        items.append({
            'session_id': sid,
            'path': str(r['wt']),
            'branch': (r['info'] or {}).get('branch') or _wt.branch_name(sid),
            'registered': r['info'] is not None,
            'head': head[:12] if head else None,
            'last_commit_at': ts[1],
            'last_commit_ts': ts[0],
            'commits_ahead': ahead,
            'dirty': real,
            'dirty_raw': raw,
            'live_session': live,
            'held_for': reasons,
            'size_bytes': None,
        })
        out['in_use' if live else 'preserved'] += 1

    # Newest work first: the most recently touched worktrees are the ones a
    # person is most likely to still care about, and the size walk below is
    # time-boxed, so this is also the order it spends its budget in.
    items.sort(key=lambda i: i['last_commit_ts'] or 0, reverse=True)
    if include_size:
        deadline = time.monotonic() + SIZE_BUDGET
        for it in items:
            size, complete = _dir_size(Path(it['path']), deadline)
            if not complete:
                break
            it['size_bytes'] = size
        out['size_walked'] = sum(1 for it in items if it['size_bytes'] is not None)
        out['size_complete'] = out['size_walked'] == len(items)
    out['items'] = items
    return out


def survey_cached(project: dict, is_live: Callable[[str], bool] | None = None,
                  include_size: bool = False, refresh: bool = False) -> dict:
    """`survey_project` behind a CACHE_TTL cache with one in-flight survey per
    (project, size) key. Adds `cached` and `age_s` to a copy of the result."""
    key = (project.get('id', ''), bool(include_size))
    now = time.monotonic()

    def _hit():
        with _cache_lock:
            ent = _cache.get(key)
        if ent and not refresh and now - ent[0] < CACHE_TTL:
            return dict(ent[1], cached=True, age_s=round(now - ent[0], 1))
        return None

    if (r := _hit()) is not None:
        return r
    with _flight_lock(key):
        now = time.monotonic()
        if (r := _hit()) is not None:     # another request surveyed while we waited
            return r
        t0 = time.monotonic()
        res = survey_project(project, is_live=is_live, include_size=include_size)
        res['elapsed_s'] = round(time.monotonic() - t0, 2)
        with _cache_lock:
            _cache[key] = (time.monotonic(), res)
        return dict(res, cached=False, age_s=0.0)


def clear_cache() -> None:
    """Test hook."""
    with _cache_lock:
        _cache.clear()
        _disk.clear()
