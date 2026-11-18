"""The exploration read-floor's view of `_proposed/` (MC-1179 ML25).

`distiller.exploration_read_floor` runs at every dispatch that has a task. It
used to call `list_proposed()`, the review queue's lister, which reads every
artifact of every project (twice each: once for the frontmatter, once more for
a snippet the read-floor throws away) before the caller filters by scope.
`_proposed/` only grows on its own, so that cost grew with it: 2,448 artifacts
and 520 to 705 ms per dispatch on the reviewed install.

This module is the read-floor's own, narrower lister:

  * **Scope first.** Only `<project_id>/` and `global/` are opened. Another
    project's directory is never listed, let alone read. The match is on the
    exact directory NAME taken from a listing of `_proposed/`, never on a path
    built from `project_id`, so a case-insensitive filesystem or an id with a
    separator in it cannot widen the scope.
  * **Read once, and only when changed.** Each artifact's frontmatter is kept
    against the file's (name, mtime_ns, size). An artifact that has not changed
    since the last dispatch costs one directory listing and one stat, no read.
  * **Frontmatter only.** The body snippet is the caller's job, for the top-k
    it actually returns.

The cache is a pure function of the files: a changed stamp (`origin:` is what
`_UNATTENDED_LOOP_RULE` filters on), a new artifact and a deleted one are all
seen on the next call, because every call re-lists the two scope directories
and re-stats every artifact in them. `stat()` is the real one, not the
directory entry's cached copy. An artifact that could not be read is not
cached and is skipped, exactly as `_read_proposed_meta` skips it.

Stdlib only; the frontmatter parser and the project-id check are passed in by
the caller so this module does not import the distiller.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

_GLOBAL_DIR = 'global'
_CROSS_PROJECT = 'cross-project'

# {scope directory path: {artifact directory name: (signature, meta)}}
# One inner dict per scope directory, rebuilt and swapped whole on every scan,
# so an artifact that left `_proposed/` leaves the cache with it.
_cache: dict[str, dict[str, tuple[tuple[str, int, int], dict]]] = {}
_cache_lock = threading.Lock()


def _first_markdown(d: Path) -> Path | None:
    """The file `_read_proposed_meta` would pick: the first regular `.md`
    file in listing order."""
    with os.scandir(d) as it:
        for e in it:
            if e.name.endswith('.md') and e.is_file():
                return d / e.name
    return None


def _scan_scope(scope_dir: Path, scope: str,
                split_frontmatter: Callable[[str], tuple[dict, str]]) -> list[dict]:
    key = str(scope_dir)
    with _cache_lock:
        old = _cache.get(key, {})
    new: dict[str, tuple[tuple[str, int, int], dict]] = {}
    out: list[dict] = []
    for sub in scope_dir.iterdir():
        try:
            if not sub.is_dir():
                continue
            f = _first_markdown(sub)
            if f is None:
                continue
            st = f.stat()
            sig = (f.name, st.st_mtime_ns, st.st_size)
            hit = old.get(sub.name)
            if hit is not None and hit[0] == sig:
                meta = hit[1]
            else:
                fm = split_frontmatter(
                    f.read_text(encoding='utf-8', errors='replace'))[0]
                meta = {
                    'path': str(f),
                    'scope': scope,
                    'kind': fm.get('kind', f.stem.lower()),
                    'name': fm.get('name', sub.name),
                    # 'interactive' | 'unattended' | '' (pre-stamp artifact).
                    'origin': fm.get('origin', ''),
                    'created_at': fm.get('created_at', ''),
                }
            new[sub.name] = (sig, meta)
            out.append(meta)
        except Exception:
            continue      # unreadable artifact: skipped and not cached
    with _cache_lock:
        _cache[key] = new
    return out


def in_scope_artifacts(proposed: Path, project_id: str,
                       is_valid_project_id: Callable[[str], bool],
                       split_frontmatter: Callable[[str], tuple[dict, str]],
                       ) -> list[dict]:
    """Frontmatter of every artifact under `<proposed>/<project_id>/` and
    `<proposed>/global/`, as {path, scope, kind, name, origin, created_at},
    in directory-listing order. `scope` is the project id, or 'cross-project'
    for `global/`, the same values `list_proposed()` reports.

    Raises what the top-level listing raises; the caller is best-effort.
    """
    if not proposed.exists():
        return []
    own = project_id if is_valid_project_id(project_id) else None
    out: list[dict] = []
    for entry in proposed.iterdir():
        name = entry.name
        if name == _GLOBAL_DIR:
            scope = _CROSS_PROJECT
        elif own is not None and name == own:
            scope = own
        else:
            continue
        if not entry.is_dir():
            continue
        out.extend(_scan_scope(entry, scope, split_frontmatter))
    return out


def forget() -> None:
    """Drop every cached entry (tests; nothing in the product needs it)."""
    with _cache_lock:
        _cache.clear()
