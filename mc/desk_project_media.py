"""Indexed project media for Desk; discovery never runs in a material GET.

The cache and copy receipts live in data/media, never data/projects. Source
paths are re-authorized against CURRENT project roots before preview or copy.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from flask import Blueprint, jsonify, request, send_file

from mc import desk_pieces as pieces
from mc.atomic_json import write_json_atomic
from mc.core import _log

bp = Blueprint('desk_project_media', __name__)
MAX_FILES = 2000
MAX_DEPTH = 8
MAX_VISITS = 20000
MAX_CHAT_BYTES = 2 * 1024 * 1024
REFRESH_SECONDS = 300
PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
SKIP_DIRS = {'.git', 'node_modules', 'venv', '.venv', '__pycache__',
             'dist', 'build', '.clayrune', '_scratch'}
_load_projects: Callable[[], list] = lambda: []
_media_dir: Path | None = None
_uploads: Path | None = None
_snapshot: dict = {'items': [], 'updated_at': None}
_refresh_lock = threading.Lock()
_copy_lock = threading.Lock()
_state_lock = threading.Lock()
_wake = threading.Event()
_worker: threading.Thread | None = None
_last_error: str | None = None


def wire(*, load_projects_fn: Callable[[], list], media_dir: Path, uploads_root: Path) -> None:
    global _load_projects, _media_dir, _uploads, _snapshot
    _load_projects = load_projects_fn
    _media_dir, _uploads = Path(media_dir).resolve(), Path(uploads_root).resolve()
    _snapshot = {'items': [], 'updated_at': None}
    cache = _media_dir / '_desk_project_index.json'
    if cache.is_file():
        try:
            value = json.loads(cache.read_text(encoding='utf-8'))
            if isinstance(value, dict) and isinstance(value.get('items'), list):
                _snapshot = value
        except (OSError, ValueError) as e:
            _log(f'[desk-project-media] cache read failed: {e}', flush=True)


def _roots(projects: list) -> list[Path]:
    roots = []
    for p in projects:
        raw = p.get('project_path')
        if not isinstance(raw, str) or not raw.strip():
            continue
        root = Path(raw).resolve()
        if root.is_absolute() and len(root.parts) >= 3:
            roots.append(root)
    return roots + [p for p in (_uploads, _media_dir) if p is not None]


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _authorize(raw: str, roots: list[Path]) -> Path:
    path = Path(raw)
    if not path.is_absolute() or '..' in path.parts:
        raise pieces.PieceError('that media path is not allowed', 403)
    real = path.resolve()
    if not any(_under(real, root) for root in roots):
        raise pieces.PieceError('that media is outside your projects', 403)
    if pieces.asset_kind_for(real.name) is None:
        raise pieces.PieceError('that is not a supported picture or video', 415)
    if not real.is_file():
        raise pieces.PieceError('that source file no longer exists; refresh the list', 404)
    return real


def _chat_rows(pid: str) -> list[dict]:
    if _media_dir is None:
        return []
    safe = re.sub(r'[^A-Za-z0-9_.-]', '_', pid)
    path = _media_dir / f'{safe}.jsonl'
    if not path.is_file():
        return []
    rows = []
    try:
        with path.open('rb') as f:
            size = f.seek(0, 2)
            if size > MAX_CHAT_BYTES:
                f.seek(-MAX_CHAT_BYTES, 2)
                f.readline()
                _log(f'[desk-project-media] chat byte cap reached for {pid}', flush=True)
            else:
                f.seek(0)
            for line in f:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and isinstance(row.get('path'), str):
                        rows.append(row)
                except ValueError as e:
                    _log(f'[desk-project-media] invalid chat row for {pid}: {e}', flush=True)
    except OSError as e:
        _log(f'[desk-project-media] chat read failed for {pid}: {e}', flush=True)
    return rows


def _scan(root: Path, pid: str):
    """Bound directory entries as well as media count, depth and cycles."""
    pending = [(root, 0)]
    seen: set[Path] = set()
    visited = 0
    depth_logged = False
    while pending:
        folder, depth = pending.pop()
        real = folder.resolve()
        if real in seen or not _under(real, root):
            continue
        seen.add(real)
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    visited += 1
                    if visited > MAX_VISITS:
                        _log(f'[desk-project-media] entry cap reached for {pid}', flush=True)
                        return
                    path = Path(entry.path)
                    resolved = path.resolve()
                    if not _under(resolved, root) or entry.is_symlink():
                        continue
                    # Common uploads have no project owner unless a chat index attributes them.
                    if _uploads and _under(resolved, _uploads):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.lower() in SKIP_DIRS:
                            continue
                        if depth >= MAX_DEPTH:
                            if not depth_logged:
                                _log(f'[desk-project-media] depth cap reached for {pid}; deeper folders skipped (first: {path})', flush=True)
                                depth_logged = True
                        else:
                            pending.append((path, depth + 1))
                    elif entry.is_file(follow_symlinks=False) and pieces.asset_kind_for(entry.name):
                        yield resolved
        except OSError as e:
            _log(f'[desk-project-media] scan failed for {pid} at {folder}: {e}', flush=True)


def refresh() -> dict:
    """Build a replacement snapshot in a worker (or explicitly in a diagnostic)."""
    global _snapshot
    if _media_dir is None:
        raise pieces.PieceError('project media is not wired', 503)
    with _refresh_lock:
        projects = _load_projects()
        roots = _roots(projects)
        items = []
        for project in projects:
            pid = str(project.get('id') or '')
            title = str(project.get('name') or pid)
            found: dict[str, dict] = {}
            root = Path(project['project_path']).resolve() if project.get('project_path') else None

            def add(raw: str, origin: str, meta: dict) -> None:
                try:
                    path = _authorize(raw, roots)
                    if _uploads and _under(path, _uploads / 'desk'):
                        return
                    stat = path.stat()
                except (OSError, ValueError):
                    return
                key = str(path)
                if key in found or len(found) >= MAX_FILES:
                    return
                folder = path.parent.relative_to(root).as_posix() if root and _under(path, root) else 'Chat files'
                row = {'id': hashlib.sha256((pid + '\0' + key).encode()).hexdigest(),
                       'project_id': pid, 'project_name': title, 'path': key,
                       'title': path.name, 'folder': folder, 'kind': pieces.asset_kind_for(path.name),
                       'size': stat.st_size, 'mtime': stat.st_mtime, 'mtime_ns': stat.st_mtime_ns,
                       'origin': 'upload' if origin == 'chat' and _uploads and _under(path, _uploads) else origin}
                for field in ('session_id', 'agent_name'):
                    if meta.get(field):
                        row[field] = str(meta[field])
                found[key] = row

            # Chat attribution wins over a later scan of the same file.
            for row in reversed(_chat_rows(pid)):
                if len(found) >= MAX_FILES:
                    break
                add(row['path'], 'chat', row)
            if root and root in roots and root.is_dir():
                for path in _scan(root, pid):
                    if str(path) not in found:
                        add(str(path), 'scan', {})
                    if len(found) >= MAX_FILES:
                        break
            if len(found) >= MAX_FILES:
                _log(f'[desk-project-media] file cap ({MAX_FILES}) reached for {pid}', flush=True)
            items.extend(found.values())
        value = {'items': sorted(items, key=lambda i: (-i['mtime'], i['path'])), 'updated_at': time.time()}
        _media_dir.mkdir(parents=True, exist_ok=True)
        write_json_atomic(_media_dir / '_desk_project_index.json', value)
        with _state_lock:
            _snapshot = value
        return value


def _listed(*, project_id: str | None = None, folder: str | None = None,
            query: str = '', kinds: tuple[str, ...] = ()) -> list[dict]:
    """Read index metadata only; refresh and _source own filesystem checks."""
    projects = {str(p['id']): p for p in _load_projects()}
    with _state_lock:
        rows = _snapshot['items']
    query = query.strip().casefold()
    return [dict(row, project_name=projects[row['project_id']].get('name') or row['project_id'])
            for row in rows if row.get('project_id') in projects
            and (project_id is None or row['project_id'] == project_id)
            and (folder is None or row['folder'] == folder)
            and (not kinds or row['kind'] in kinds)
            and (not query or query in ' '.join(str(v) for v in (
                projects[row['project_id']].get('name'), row['title'], row['folder'],
                row.get('agent_name', ''))).casefold())]


def _preview_url(item_id: str) -> str:
    return '/api/desk/project-media/file?id=' + quote(item_id, safe='')


def grouped(*, query: str = '', kinds: tuple[str, ...] = ()) -> list[dict]:
    """Project/folder summaries only, in newest-file order; no stat per row."""
    groups: dict[str, dict] = {}
    folders: dict[tuple[str, str], dict] = {}
    for row in _listed(query=query, kinds=kinds):
        pid = row['project_id']
        group = groups.setdefault(pid, {'project_id': pid, 'title': row['project_name'], 'files': 0,
                                        'thumb': None, 'folders': []})
        group['files'] += 1
        if group['thumb'] is None and row['kind'] == 'image':
            group['thumb'] = _preview_url(row['id'])
        key = (pid, row['folder'])
        if key not in folders:
            folders[key] = {'title': row['folder'], 'files': 0}
            group['folders'].append(folders[key])
        folders[key]['files'] += 1
    return list(groups.values())


def items_page(project_id: str, *, folder: str | None = None, query: str = '',
               kinds: tuple[str, ...] = (), offset: int = 0, limit: int = PAGE_SIZE) -> dict:
    if not project_id:
        raise pieces.PieceError('a project id is required')
    if offset < 0 or limit < 1:
        raise pieces.PieceError('offset must be nonnegative and limit must be positive')
    limit = min(limit, MAX_PAGE_SIZE)
    rows = _listed(project_id=project_id, folder=folder, query=query, kinds=kinds)
    items = []
    for row in rows[offset:offset + limit]:
        item = {key: row[key] for key in ('id', 'project_id', 'title', 'folder', 'kind')}
        if row.get('agent_name'):
            item['agent_name'] = row['agent_name']
        item['src' if row['kind'] == 'image' else 'play'] = _preview_url(row['id'])
        items.append(item)
    return {'items': items, 'total': len(rows), 'offset': offset, 'limit': limit,
            'next_offset': offset + limit if offset + limit < len(rows) else None}


def _source(item_id: str) -> tuple[dict, Path]:
    with _state_lock:
        row = next((dict(r) for r in _snapshot['items'] if r.get('id') == item_id), None)
    projects = _load_projects()
    if row is None or row['project_id'] not in {p.get('id') for p in projects}:
        raise pieces.PieceError('that project file is no longer listed; refresh the list', 404)
    project = next(p for p in projects if p.get('id') == row['project_id'])
    row['project_name'] = project.get('name') or row['project_id']
    return row, _authorize(row['path'], _roots(projects))


def copy_to_library(item_id: str) -> dict:
    """Return a durable library item. Same source + mtime + size reuses a copy."""
    if _media_dir is None:
        raise pieces.PieceError('project media is not wired', 503)
    with _copy_lock:
        row, source = _source(item_id)
        stat = source.stat()
        provenance = {'source_path': str(source), 'project_id': row['project_id'],
                      'project_name': row['project_name'], 'mtime_ns': stat.st_mtime_ns, 'size': stat.st_size}
        for field in ('agent_name', 'session_id'):
            if row.get(field):
                provenance[field] = row[field]
        key = hashlib.sha256(json.dumps({k: provenance[k] for k in
                             ('source_path', 'project_id', 'mtime_ns', 'size')}, sort_keys=True).encode()).hexdigest()
        receipt_path = _media_dir / '_desk_project_copies.json'
        receipts = json.loads(receipt_path.read_text(encoding='utf-8')) if receipt_path.exists() else {}
        previous = receipts.get(key)
        if previous:
            try:
                pieces._resolve_under_uploads(previous['path'])
                return previous
            except pieces.PieceError:
                pass  # A library copy can have been moved to Studio's trash.
        name = re.sub(r'[^A-Za-z0-9._ -]+', '_', row['project_name']).strip(' ._')[:60] or 'Project'
        folder = pieces._uploads_root().joinpath(*pieces.LIBRARY_ROOT, row['kind'], 'Projects', name)
        if not _under(folder.resolve(), pieces._uploads_root()):
            raise pieces.PieceError('that library folder is not allowed', 403)
        # Folder depth is explicit, never derived from an arbitrary source path.
        with source.open('rb') as stream:
            dest, kind = pieces._write_library_file(source.name, stream, f'Projects/{name}')
        try:
            after = source.stat()
            if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                raise pieces.PieceError('that source changed while copying; try again', 409)
            rel = pieces._rel_of(dest.resolve())
            item = {'id': rel, 'kind': kind, 'title': source.name, 'path': rel,
                    'src': pieces._src_for(rel, kind), 'play': pieces._play_for(rel, kind),
                    'provenance': provenance}
            receipts[key] = item
            write_json_atomic(receipt_path, receipts)
            return item
        except BaseException:
            dest.unlink(missing_ok=True)
            raise


def library_folders(kind_dir: Path) -> list[Path]:
    """Existing single-level shelves plus Projects/<name> copies."""
    folders = []
    for folder in sorted(kind_dir.iterdir()):
        if not folder.is_dir() or not _under(folder.resolve(), kind_dir.resolve()):
            continue
        if folder.name == 'Projects':
            folders.extend(f for f in sorted(folder.iterdir()) if f.is_dir()
                           and _under(f.resolve(), kind_dir.resolve()))
        else:
            folders.append(folder)
    return folders


def _provenance_map() -> dict:
    if _media_dir is None:
        return {}
    path = _media_dir / '_desk_project_copies.json'
    if not path.exists():
        return {}
    try:
        receipts = json.loads(path.read_text(encoding='utf-8'))
        return {item['path']: item['provenance'] for item in receipts.values()}
    except (OSError, ValueError, KeyError, AttributeError) as e:
        _log(f'[desk-project-media] copy provenance read failed: {e}', flush=True)
        return {}


def with_provenance(item: dict) -> dict:
    from mc import desk_library_folders
    provenance = _provenance_map().get(item.get('path'))
    return dict(item, provenance=provenance) if provenance else desk_library_folders.with_provenance(item)


def decorate_library(library: dict) -> None:
    provenance = _provenance_map()
    for folders in library.values():
        for folder in folders:
            for item in folder['items']:
                if item['path'] in provenance:
                    item['provenance'] = provenance[item['path']]


def _loop() -> None:
    global _last_error
    while True:
        _wake.clear()
        try:
            refresh()
            _last_error = None
        except Exception as e:
            _log(f'[desk-project-media] refresh failed: {e}', flush=True)
            _last_error = 'Could not refresh project files. Try again.'
        _wake.wait(REFRESH_SECONDS)


def start() -> None:
    global _worker
    with _state_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name='desk-project-media', daemon=True)
            _worker.start()


@bp.get('/api/desk/project-media/file')
def preview_route():
    try:
        _row, path = _source(request.args.get('id') or '')
        return send_file(path, as_attachment=False, conditional=True, max_age=0)
    except pieces.PieceError as e:
        return jsonify({'error': str(e)}), e.status


@bp.post('/api/desk/project-media/copy')
def copy_route():
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise pieces.PieceError('a project file id is required')
        return jsonify(copy_to_library(data.get('id') or '')), 201
    except pieces.PieceError as e:
        return jsonify({'error': str(e)}), e.status
    except (OSError, ValueError) as e:
        _log(f'[desk-project-media] copy failed: {e}', flush=True)
        return jsonify({'error': 'Could not save that project file to the library. Try again.'}), 503


@bp.post('/api/desk/project-media/refresh')
def refresh_route():
    start()
    _wake.set()
    return jsonify({'refreshing': True}), 202


@bp.get('/api/desk/project-media')
def list_route():
    return jsonify({'projects': grouped(query=request.args.get('q', ''),
                                       kinds=tuple(request.args.getlist('kind'))),
                    'updated_at': _snapshot.get('updated_at'),
                    'refreshing': _refresh_lock.locked() or _wake.is_set(), 'error': _last_error})


@bp.get('/api/desk/project-media/items')
def items_route():
    try:
        try:
            offset = int(request.args.get('offset', '0'))
            limit = int(request.args.get('limit', str(PAGE_SIZE)))
        except ValueError:
            raise pieces.PieceError('offset and limit must be integers')
        return jsonify(items_page(request.args.get('project_id', ''),
                                  folder=request.args.get('folder'), query=request.args.get('q', ''),
                                  kinds=tuple(request.args.getlist('kind')), offset=offset, limit=limit))
    except pieces.PieceError as e:
        return jsonify({'error': str(e)}), e.status
