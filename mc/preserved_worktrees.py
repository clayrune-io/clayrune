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

_cache: dict[tuple, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_flight: dict[tuple, threading.Lock] = {}


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


def survey_project(project: dict, is_live: Callable[[str], bool] | None = None,
                   include_size: bool = False) -> dict:
    """Uncached survey of one project. Never raises; a git failure degrades the
    affected field to None. Returns

      {'project_id', 'worktrees_total', 'preserved', 'in_use', 'base',
       'partial', 'items': [...]}

    where each item is a worktree holding unmerged/uncommitted work (see module
    docstring). `preserved` counts items with no live session; `in_use` counts
    items whose session is live (gc skips those)."""
    pid = project.get('id', '')
    base = project.get('project_path') or ''
    out = {'project_id': pid, 'worktrees_total': 0, 'preserved': 0, 'in_use': 0,
           'base': None, 'partial': False, 'items': []}
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
