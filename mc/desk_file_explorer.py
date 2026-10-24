"""Read-only, bounded host browsing for Studio. Every request rechecks live roots."""
from __future__ import annotations

import hashlib
import os
import stat
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from mc import desk_pieces as pieces, desk_project_media as project_media
from mc.core import _log
from mc import desk_explorer_guard as guard, desk_explorer_search as search

MAX_VISITS = 3000
MAX_FILES = project_media.MAX_FILES
MAX_DEPTH = project_media.MAX_DEPTH
_home: Path | None = None
_load_projects: Callable[[], list] = lambda: []


def wire(*, load_projects_fn: Callable[[], list], home: Path | None = None) -> None:
    global _home, _load_projects
    _home = (home or Path.home()).absolute()
    _load_projects = load_projects_fn


def roots() -> list[dict]:
    if _home is None:
        raise pieces.PieceError('File browsing is not available yet.', 503)
    out = [{'id': 'home', 'title': 'Home', 'root': _home}]
    for project in _load_projects():
        raw = project.get('project_path')
        if not isinstance(raw, str) or not Path(raw).is_absolute():
            continue
        path = Path(raw).absolute()
        if not path.is_dir() or len(path.parts) < 3:
            continue
        key = hashlib.sha256(str(path).encode()).hexdigest()[:24]
        out.append({'id': 'project-' + key, 'title': project.get('name') or path.name, 'root': path})
    return out


def _check_parts(path: Path) -> None:
    guard.check_parts(path, _home)


def authorize(root_id: str, relative: str = '', *, directory: bool = False,
              locations: list[dict] | None = None) -> tuple[dict, Path]:
    root = next((r for r in (locations if locations is not None else roots()) if r['id'] == root_id), None)
    if root is None:
        raise pieces.PieceError('That location is no longer available.', 403)
    if not isinstance(relative, str) or len(relative) > 4096 or '\x00' in relative:
        raise pieces.PieceError('That location is not allowed.', 403)
    rel = Path(relative)
    if rel.anchor or rel.drive or '..' in rel.parts or '\\' in relative:
        raise pieces.PieceError('That location is not allowed.', 403)
    candidate = root['root'] / rel
    guard.check_chain(candidate, _home)
    real = candidate.resolve()
    guard.check_chain(real, _home)
    # Unlike project-media's extra uploads/media roots, this surface grants ONLY
    # the selected home/project root. A removed project loses access immediately.
    if not project_media._under(real, root['root']):
        raise pieces.PieceError('That location is outside the selected folder.', 403)
    if directory and not real.is_dir():
        raise pieces.PieceError('That folder no longer exists.', 404)
    if not directory:
        # Reuse the source media guard for preview/copy (not All files listing).
        real = project_media._authorize(str(real), [root['root']])
    return root, real


def shortcuts(locations: list[dict] | None = None) -> list[dict]:
    locations = roots() if locations is None else locations
    out = []
    for label in ('Home', 'Pictures', 'Videos', 'Desktop', 'Downloads'):
        relative = '' if label == 'Home' else label
        try:
            authorize('home', relative, directory=True, locations=locations)
            out.append({'root': 'home', 'path': relative, 'title': label})
        except (pieces.PieceError, OSError):
            continue
    for root in locations[1:]:
        try:
            authorize(root['id'], directory=True, locations=locations)
            out.append({'root': root['id'], 'path': '', 'title': root['title']})
        except (pieces.PieceError, OSError):
            continue
    return out


def browse(root_id: str = 'home', relative: str = '', *, query: str = '', all_files: bool = False,
           stopped: Callable[[], bool] | None = None, deadline: float | None = None) -> dict:
    deadline = time.monotonic() + search.MAX_SECONDS if deadline is None else deadline
    def interrupted():
        return time.monotonic() >= deadline or (stopped is not None and stopped())

    locations = roots()
    root, start = authorize(root_id, relative, directory=True, locations=locations)
    if not isinstance(query, str) or len(query) > 200:
        raise pieces.PieceError('Search must be at most 200 characters.')
    query = query.strip().casefold()
    navigation = shortcuts(locations)
    pending = [(start, 0)]
    seen: set[Path] = set()
    rows: list[dict] = []
    visits = 0
    capped = False
    while pending:
        if interrupted():
            capped = True
            break
        folder, depth = pending.pop()
        if folder in seen:
            continue
        seen.add(folder)
        # Validate a directory once, then lstat each immediate child. Links are
        # never traversed, so there is no need to resolve every entry repeatedly.
        try:
            authorize(root_id, folder.relative_to(root['root']).as_posix(), directory=True, locations=locations)
        except (pieces.PieceError, OSError) as e:
            _log(f'[desk-explorer] folder unavailable: {e}', flush=True)
            continue
        with os.scandir(folder) as entries:
            for entry in entries:
                if visits >= MAX_VISITS or len(rows) >= MAX_FILES or interrupted():
                    capped = True
                    break
                visits += 1
                path = Path(entry.path)
                rel = path.relative_to(root['root']).as_posix()
                try:
                    info = guard.check_node(path)
                    is_dir = stat.S_ISDIR(info.st_mode)
                    if not is_dir and not stat.S_ISREG(info.st_mode):
                        continue
                    # Listing all files still uses the SAME location guard. It
                    # never exposes arbitrary file bodies or makes them copyable.
                    _check_parts(path)
                    if is_dir:
                        guard.check_profile(path)
                    if query and is_dir:
                        if depth < MAX_DEPTH:
                            pending.append((path, depth + 1))
                        else:
                            capped = True
                    kind = pieces.asset_kind_for(entry.name) if not is_dir else None
                    if (not is_dir and not kind and not all_files) or (query and query not in entry.name.casefold()):
                        continue
                    row = {'title': entry.name, 'path': rel, 'directory': is_dir, 'kind': kind,
                           'selectable': bool(kind), 'size': info.st_size if not is_dir else None}
                    if kind:
                        url = '/api/desk/explorer/preview?' + urlencode({'root': root_id, 'path': rel})
                        row['src' if kind == 'image' else 'play'] = url
                    rows.append(row)
                except pieces.PieceError:
                    continue
                except OSError as e:
                    _log(f'[desk-explorer] entry unavailable: {e}', flush=True)
        if capped and (visits >= MAX_VISITS or len(rows) >= MAX_FILES or interrupted()):
            break
    rows.sort(key=lambda r: (not r['directory'], r['title'].casefold(), r['path']))
    parts = Path(relative).parts if relative else ()
    crumbs = [{'title': root['title'], 'path': ''}]
    crumbs.extend({'title': part, 'path': Path(*parts[:i + 1]).as_posix()} for i, part in enumerate(parts))
    return {'root': root_id, 'path': relative, 'breadcrumbs': crumbs, 'items': rows,
            'limited': capped, 'truncated': capped, 'shortcuts': navigation}
