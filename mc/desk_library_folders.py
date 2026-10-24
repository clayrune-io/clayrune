"""Mixed-media library folders; display renames never invalidate saved paths."""
from __future__ import annotations

import json
import shutil
import threading
import uuid
from pathlib import Path
from urllib.parse import unquote

from mc import desk, desk_pieces as pieces, desk_project_media as project_media
from mc import desk_studio_items as studio_items
from mc.atomic_json import write_json_atomic
from mc.core import _log

_lock = threading.RLock()


def _metadata_path() -> Path:
    return _state_path('_library_folders.json')


def _state_path(name: str) -> Path:
    path = pieces._uploads_root() / 'desk' / name
    if not project_media._under(path.resolve(), pieces._uploads_root()):
        raise pieces.PieceError('That library location is not allowed.', 403)
    return path


def _save_metadata(data: dict) -> None:
    path = _metadata_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, data)


def _metadata() -> dict:
    path = _metadata_path()
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
        raise pieces.PieceError('The library folder records could not be read.', 503)
    return data


def _name(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80 or any(ord(c) < 32 for c in value):
        raise pieces.PieceError('Give the folder a name of 1 to 80 characters.')
    return value.strip()


def _base() -> Path:
    return _state_path('library')


def _directories(key: str) -> list[Path]:
    # Keys come from server discovery or generated metadata, never arbitrary
    # paths. Still validate here before any write/move.
    rel = Path(key)
    if rel.is_absolute() or rel.drive or '..' in rel.parts or '\\' in key or ':' in key:
        raise pieces.PieceError('That library folder is not allowed.', 403)
    dirs = [_base() / kind / rel for kind in pieces.ASSET_KINDS]
    for directory in dirs:
        if not project_media._under(directory.resolve(), _base().resolve()):
            raise pieces.PieceError('That library folder is not allowed.', 403)
    return dirs


def list_folders() -> list[dict]:
    with _lock:
        metadata = _metadata()
        keys = set(metadata)
        for kind in pieces.ASSET_KINDS:
            kdir = _base() / kind
            if kdir.is_dir():
                keys.update(d.relative_to(kdir).as_posix() for d in project_media.library_folders(kdir))
        out = []
        provenance = _provenance()
        for key in sorted(keys):
            record = metadata.get(key, {})
            if record.get('deleted'):
                continue
            items = []
            for kind, directory in zip(pieces.ASSET_KINDS, _directories(key)):
                if directory.is_dir():
                    items.extend(pieces._library_items(kind, directory, pieces._uploads_root()))
            for item in items:
                if item['path'] in provenance:
                    item['provenance'] = provenance[item['path']]
                    item['title'] = provenance[item['path']].get('source_name') or item['title']
            out.append({'id': key, 'title': record.get('title') or key, 'files': len(items), 'items': items})
        return out


def _folder(key) -> dict:
    if not isinstance(key, str):
        raise pieces.PieceError('Choose a library folder.')
    folder = next((f for f in list_folders() if f['id'] == key), None)
    if folder is None:
        raise pieces.PieceError('That library folder no longer exists.', 404)
    return folder


def create(title) -> dict:
    with _lock:
        title = _name(title)
        _unique(title)
        key = 'Folder-' + uuid.uuid4().hex
        metadata = _metadata()
        metadata[key] = {'title': title}
        _save_metadata(metadata)
        return _folder(key)


def _unique(title: str, key: str = '') -> None:
    if any(f['id'] != key and f['title'].casefold() == title.casefold() for f in list_folders()):
        raise pieces.PieceError('A folder with that name already exists.', 409)


def rename(key, title) -> dict:
    with _lock:
        _folder(key)
        title = _name(title)
        _unique(title, key)
        metadata = _metadata()
        metadata[key] = dict(metadata.get(key, {}), title=title)
        _save_metadata(metadata)
        return _folder(key)


def decorate_materials(library: dict) -> None:
    """Keep the existing video/image API; only display titles change."""
    if pieces.UPLOADS_ROOT is None:
        return
    metadata = _metadata()
    provenance = _provenance()
    for folders in library.values():
        for folder in folders:
            key = folder['id'].partition(':')[2]
            if key in metadata:
                folder['title'] = metadata[key]['title']
            for item in folder['items']:
                if item['path'] in provenance:
                    item['provenance'] = provenance[item['path']]
                    item['title'] = provenance[item['path']].get('source_name') or item['title']


def _provenance() -> dict:
    path = _state_path('_explorer_copies.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def with_provenance(item: dict) -> dict:
    if pieces.UPLOADS_ROOT is None:
        return item
    provenance = _provenance().get(item.get('path'))
    return dict(item, provenance=provenance) if provenance else item


def delete(key) -> dict:
    with _lock, desk._store_lock:
        folder = _folder(key)
        store = desk._read_store()
        attached, boards = studio_items._references(store)
        # Also protect article/reference-image paths held elsewhere in the store.
        # Conservative substring matching is preferable to breaking a draft.
        serialized = unquote(json.dumps(store, ensure_ascii=False)).replace('\\\\', '/')
        for item in folder['items']:
            path = item['path']
            absolute = str(pieces._uploads_root() / path).replace('\\', '/')
            if path in attached or path in boards or path in serialized or absolute in serialized:
                raise pieces.PieceError('This folder contains a file used by a piece or draft. Remove that reference first.', 409)
        directories = [d for d in _directories(key) if d.exists()]
        # Parent folders with unknown content are refused, never silently hidden.
        for directory in directories:
            if any(p.is_dir() or p.is_symlink() or pieces.asset_kind_for(p.name) is None for p in directory.iterdir()):
                raise pieces.PieceError('This folder contains other folders or files. Remove those first.', 409)
        token = uuid.uuid4().hex
        trash = _state_path('_folder_trash') / token
        trash.mkdir(parents=True)
        moved = []
        metadata = _metadata()
        try:
            write_json_atomic(trash / 'manifest.json', {'folder': folder['id'], 'title': folder['title']})
            for directory in directories:
                destination = trash / directory.parent.name
                shutil.move(str(directory), str(destination))
                moved.append((directory, destination))
            metadata.pop(key, None)
            _save_metadata(metadata)
        except BaseException:
            for original, destination in reversed(moved):
                shutil.move(str(destination), str(original))
            raise
        return {'deleted': True}


def copy_files(key, selections) -> dict:
    from mc import desk_file_explorer as explorer, desk_explorer_file
    if not isinstance(selections, list) or not 1 <= len(selections) <= 50:
        raise pieces.PieceError('Choose between 1 and 50 pictures or videos.')
    with _lock:
        folder = _folder(key)
        items, errors = [], []
        for selection in selections:
            dest = None
            try:
                if not isinstance(selection, dict):
                    raise pieces.PieceError('Choose a picture or video.')
                root, source, stream = desk_explorer_file.open_media(selection.get('root', ''), selection.get('path', ''))
                with stream:
                    stat = source.stat()
                    kind = pieces.asset_kind_for(source.name)
                    if not kind:
                        raise pieces.PieceError('Only pictures and videos can be added.', 415)
                    if stat.st_size > pieces.MAX_UPLOAD_BYTES[kind]:
                        raise pieces.PieceError('That file is too large.', 413)
                    _directories(key)
                    dest, kind = pieces._write_library_file(source.name, stream, key)
                _, checked = explorer.authorize(selection['root'], selection['path'])
                after = checked.stat()
                if checked != source or (after.st_ino, after.st_mtime_ns, after.st_size) != (stat.st_ino, stat.st_mtime_ns, stat.st_size):
                    raise pieces.PieceError('That file changed while copying. Try again.', 409)
                rel = pieces._rel_of(dest.resolve())
                item = {'id': rel, 'path': rel, 'kind': kind, 'title': source.name,
                        'src': pieces._src_for(rel, kind), 'play': pieces._play_for(rel, kind),
                        'provenance': {'source_path': str(source), 'source_name': source.name,
                                       'location': root['title'], 'mtime_ns': stat.st_mtime_ns, 'size': stat.st_size}}
                receipts = _state_path('_explorer_copies.json')
                saved = json.loads(receipts.read_text(encoding='utf-8')) if receipts.exists() else {}
                saved[rel] = item['provenance']
                write_json_atomic(receipts, saved)
                items.append(item)
            except (pieces.PieceError, OSError, ValueError) as e:
                if not isinstance(e, pieces.PieceError):
                    _log(f'[desk-library] copy failed: {e}', flush=True)
                if dest is not None:
                    dest.unlink(missing_ok=True)
                errors.append({'title': Path(str(selection.get('path', 'File'))).name if isinstance(selection, dict) else 'File',
                               'root': selection.get('root') if isinstance(selection, dict) else None,
                               'path': selection.get('path') if isinstance(selection, dict) else None,
                               'error': str(e) if isinstance(e, pieces.PieceError) else 'Could not copy this file. Try again.'})
        return {'items': items, 'errors': errors, 'folder': folder['title']}
