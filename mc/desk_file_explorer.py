"""Read-only, bounded host browsing for Studio. Every request rechecks live roots."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from mc import desk_pieces as pieces, desk_project_media as project_media
from mc.core import _log

MAX_VISITS = project_media.MAX_VISITS
MAX_FILES = project_media.MAX_FILES
MAX_DEPTH = project_media.MAX_DEPTH
_home: Path | None = None
_load_projects: Callable[[], list] = lambda: []
PROTECTED_DIRS = {'.clayrune', '.ssh', '.aws', '.gnupg', '.azure', '.git',
                  'browser_profiles', 'browser_profiles_named', 'user data',
                  'chrome', 'chromium', 'edge', 'firefox', 'profiles',
                  'safari', 'brave-browser', 'bravesoftware', 'opera', 'opera software', 'vivaldi',
                  'node_modules', '__pycache__', 'venv', '.venv'}


def wire(*, load_projects_fn: Callable[[], list], home: Path | None = None) -> None:
    global _home, _load_projects
    _home = (home or Path.home()).resolve()
    _load_projects = load_projects_fn


def roots() -> list[dict]:
    if _home is None:
        raise pieces.PieceError('File browsing is not available yet.', 503)
    out = [{'id': 'home', 'title': 'Home', 'root': _home}]
    for project in _load_projects():
        raw = project.get('project_path')
        if not isinstance(raw, str) or not Path(raw).is_absolute():
            continue
        path = Path(raw).resolve()
        if not path.is_dir() or len(path.parts) < 3:
            continue
        key = hashlib.sha256(str(path).encode()).hexdigest()[:24]
        out.append({'id': 'project-' + key, 'title': project.get('name') or path.name, 'root': path})
    return out


def _secret(name: str) -> bool:
    name = name.casefold()
    return (name.startswith(('.env', 'id_rsa', 'id_ed25519', 'id_dsa'))
            or 'credential' in name or name.endswith(('.key', '.pem', '.p12', '.pfx', '.kdbx'))
            or name in {'secrets.json', 'secrets.toml', 'cookies', 'login data', 'local state'})


def _check_parts(path: Path) -> None:
    for part in path.parts[1:]:
        if _secret(part) or part.casefold() in PROTECTED_DIRS or part.startswith('.') or ':' in part:
            raise pieces.PieceError('That location is private and cannot be browsed.', 403)


def _check_profiles(path: Path, root: Path) -> None:
    # Browser profiles may have arbitrary user-chosen names. Check structural
    # markers as well as directory names, without reading any profile contents.
    for folder in (path, *path.parents):
        if not project_media._under(folder, root):
            break
        if folder.is_dir() and any((folder / marker).is_file() for marker in ('Local State', 'Cookies', 'Login Data')):
            raise pieces.PieceError('Browser profiles are private and cannot be browsed.', 403)


def authorize(root_id: str, relative: str = '', *, directory: bool = False,
              locations: list[dict] | None = None) -> tuple[dict, Path]:
    root = next((r for r in (locations if locations is not None else roots()) if r['id'] == root_id), None)
    if root is None:
        raise pieces.PieceError('That location is no longer available.', 403)
    if not isinstance(relative, str) or len(relative) > 4096 or '\x00' in relative:
        raise pieces.PieceError('That location is not allowed.', 403)
    rel = Path(relative)
    if rel.is_absolute() or rel.drive or '..' in rel.parts or '\\' in relative:
        raise pieces.PieceError('That location is not allowed.', 403)
    candidate = root['root'] / rel
    _check_parts(candidate)
    real = candidate.resolve()
    _check_parts(real)
    # Unlike project-media's extra uploads/media roots, this surface grants ONLY
    # the selected home/project root. A removed project loses access immediately.
    if not project_media._under(real, root['root']):
        raise pieces.PieceError('That location is outside the selected folder.', 403)
    _check_profiles(real if directory else real.parent, root['root'])
    # Hidden/system Windows directories must not be reachable by direct requests.
    for node in (candidate, *candidate.parents):
        if not project_media._under(node, root['root']):
            break
        attrs = getattr(node.stat(), 'st_file_attributes', 0)
        if attrs & 6:  # FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
            raise pieces.PieceError('That location is private and cannot be browsed.', 403)
    if directory and not real.is_dir():
        raise pieces.PieceError('That folder no longer exists.', 404)
    if not directory:
        # Reuse the source media guard for preview/copy (not All files listing).
        real = project_media._authorize(str(real), [root['root']])
    return root, real


def shortcuts() -> list[dict]:
    locations = roots()
    out = []
    for label in ('Home', 'Pictures', 'Videos', 'Desktop', 'Downloads'):
        relative = '' if label == 'Home' else label
        try:
            authorize('home', relative, directory=True)
            out.append({'root': 'home', 'path': relative, 'title': label})
        except (pieces.PieceError, OSError):
            continue
    for root in locations[1:]:
        try:
            authorize(root['id'], directory=True)
            out.append({'root': root['id'], 'path': '', 'title': root['title']})
        except (pieces.PieceError, OSError):
            continue
    return out


def browse(root_id: str = 'home', relative: str = '', *, query: str = '', all_files: bool = False) -> dict:
    locations = roots()
    root, start = authorize(root_id, relative, directory=True, locations=locations)
    if not isinstance(query, str) or len(query) > 200:
        raise pieces.PieceError('Search must be at most 200 characters.')
    query = query.strip().casefold()
    pending = [(start, 0)]
    seen: set[Path] = set()
    rows: list[dict] = []
    visits = 0
    capped = False
    while pending:
        folder, depth = pending.pop()
        if folder in seen:
            continue
        seen.add(folder)
        with os.scandir(folder) as entries:
            for entry in entries:
                visits += 1
                if visits > MAX_VISITS or len(rows) >= MAX_FILES:
                    capped = True
                    break
                path = Path(entry.path)
                rel = path.relative_to(root['root']).as_posix()
                try:
                    is_dir = entry.is_dir()
                    if not is_dir and not entry.is_file():
                        continue
                    # Listing all files still uses the SAME location guard. It
                    # never exposes arbitrary file bodies or makes them copyable.
                    authorize(root_id, rel if is_dir else path.parent.relative_to(root['root']).as_posix(), directory=True, locations=locations)
                    _check_parts(path)
                    _check_parts(path.resolve())
                    _check_profiles(path.resolve() if is_dir else path.resolve().parent, root['root'])
                    if not project_media._under(path.resolve(), root['root']) or getattr(path.stat(), 'st_file_attributes', 0) & 6:
                        continue
                    if query and is_dir:
                        if depth < MAX_DEPTH:
                            pending.append((path.resolve(), depth + 1))
                        else:
                            capped = True
                    kind = pieces.asset_kind_for(entry.name) if not is_dir else None
                    if (not is_dir and not kind and not all_files) or (query and query not in entry.name.casefold()):
                        continue
                    row = {'title': entry.name, 'path': rel, 'directory': is_dir, 'kind': kind,
                           'selectable': bool(kind), 'size': path.stat().st_size if not is_dir else None}
                    if kind:
                        url = '/api/desk/explorer/preview?' + urlencode({'root': root_id, 'path': rel})
                        row['src' if kind == 'image' else 'play'] = url
                    rows.append(row)
                except pieces.PieceError:
                    continue
                except OSError as e:
                    _log(f'[desk-explorer] entry unavailable: {e}', flush=True)
        if capped and (visits > MAX_VISITS or len(rows) >= MAX_FILES):
            break
    rows.sort(key=lambda r: (not r['directory'], r['title'].casefold(), r['path']))
    parts = Path(relative).parts if relative else ()
    crumbs = [{'title': root['title'], 'path': ''}]
    crumbs.extend({'title': part, 'path': Path(*parts[:i + 1]).as_posix()} for i, part in enumerate(parts))
    return {'root': root_id, 'path': relative, 'breadcrumbs': crumbs, 'items': rows,
            'limited': capped, 'shortcuts': shortcuts()}
