"""Size/space-budgeted explorer transfers, staged outside the library lock."""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from mc import desk_explorer_file, desk_file_explorer as explorer, desk_pieces as pieces
from mc.atomic_json import write_json_atomic
from mc.core import _log

MAX_TOTAL_BYTES = 1024 ** 3
MIN_FREE_BYTES = 2 * 1024 ** 3
_space_lock = threading.Lock()
_reserved = 0


@contextmanager
def reserve_space(size: int):
    global _reserved
    with _space_lock:
        if size > MAX_TOTAL_BYTES:
            raise pieces.PieceError('Choose at most 1 GiB of files per copy.', 413)
        if shutil.disk_usage(pieces._uploads_root()).free < size + _reserved + MIN_FREE_BYTES:
            raise pieces.PieceError('Not enough free space. Keep at least 2 GiB free after copying.', 507)
        _reserved += size
    try:
        yield
    finally:
        with _space_lock:
            _reserved -= size


def _error(selection, error) -> dict:
    if not isinstance(error, pieces.PieceError):
        _log(f'[desk-library] copy failed: {error}', flush=True)
    return {'title': Path(str(selection.get('path', 'File'))).name if isinstance(selection, dict) else 'File',
            'root': selection.get('root') if isinstance(selection, dict) else None,
            'path': selection.get('path') if isinstance(selection, dict) else None,
            'error': str(error) if isinstance(error, pieces.PieceError) else 'Could not copy this file. Try again.'}


def _stage(stream, target: Path, size: int) -> None:
    written = 0
    with target.open('xb') as out:
        while True:
            chunk = stream.read(min(1024 * 1024, size - written + 1))
            if not chunk:
                break
            if written + len(chunk) > size:
                raise pieces.PieceError('That file changed while copying. Try again.', 409)
            if shutil.disk_usage(pieces._uploads_root()).free < MIN_FREE_BYTES + len(chunk):
                raise pieces.PieceError('Not enough free space to finish copying.', 507)
            out.write(chunk)
            written += len(chunk)
    if written != size:
        raise pieces.PieceError('That file changed while copying. Try again.', 409)


def copy_files(key, selections) -> dict:
    from mc import desk_library_folders as folders
    if not isinstance(selections, list) or not 1 <= len(selections) <= 50:
        raise pieces.PieceError('Choose between 1 and 50 pictures or videos.')
    with folders._lock:
        folder = folders._folder(key)
        folders._directories(key)
    items, errors, sources = [], [], []
    # Open/verify all sources before reserving space or writing payload bytes.
    # Handles stay open so a pathname swap cannot change the transfer's source.
    with ExitStack() as handles:
        for selection in selections:
            try:
                if not isinstance(selection, dict):
                    raise pieces.PieceError('Choose a picture or video.')
                root, source, stream = desk_explorer_file.open_media(selection.get('root', ''), selection.get('path', ''))
                handles.enter_context(stream)
                info = os.fstat(stream.fileno())
                kind = pieces.asset_kind_for(source.name)
                if not kind:
                    raise pieces.PieceError('Only pictures and videos can be added.', 415)
                if info.st_size > pieces.MAX_UPLOAD_BYTES[kind]:
                    raise pieces.PieceError('That file is too large.', 413)
                if not info.st_size:
                    raise pieces.PieceError('That file is empty.')
                sources.append((selection, root, source, stream, info, kind))
            except (pieces.PieceError, OSError, ValueError) as e:
                errors.append(_error(selection, e))
        if not sources:
            return {'items': items, 'errors': errors, 'folder': folder['title']}
        with reserve_space(sum(s[4].st_size for s in sources)):
            stage_root = folders._state_path('_explorer_staging')
            stage_root.mkdir(parents=True, exist_ok=True)
            # Only this request's generated temporary directory is removed.
            with tempfile.TemporaryDirectory(dir=stage_root) as temporary:
                for selection, root, source, stream, info, kind in sources:
                    dest = None
                    try:
                        stem = pieces._SAFE_NAME.sub('_', source.stem).strip(' ._')[:60] or 'upload'
                        staged = Path(temporary) / (stem + '-' + uuid.uuid4().hex + source.suffix.lower())
                        _stage(stream, staged, info.st_size)
                        _, checked = explorer.authorize(selection['root'], selection['path'])
                        after = checked.stat()
                        if checked != source or (after.st_dev, after.st_ino, after.st_mtime_ns, after.st_size) != (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size):
                            raise pieces.PieceError('That file changed while copying. Try again.', 409)
                        # Recheck the folder under the mutation lock. A delete
                        # during staging wins; never recreate a deleted folder.
                        with folders._lock:
                            folders._folder(key)
                            directories = folders._directories(key)
                            target = directories[list(pieces.ASSET_KINDS).index(kind)]
                            target.mkdir(parents=True, exist_ok=True)
                            dest = target / staged.name
                            staged.replace(dest)  # Same filesystem; no payload I/O under lock.
                            rel = pieces._rel_of(dest.resolve())
                            item = {'id': rel, 'path': rel, 'kind': kind, 'title': source.name,
                                    'src': pieces._src_for(rel, kind), 'play': pieces._play_for(rel, kind),
                                    'provenance': {'source_path': str(source), 'source_name': source.name,
                                                   'location': root['title'], 'mtime_ns': info.st_mtime_ns, 'size': info.st_size}}
                            try:
                                saved = folders._provenance()
                                saved[rel] = item['provenance']
                                write_json_atomic(folders._state_path('_explorer_copies.json'), saved)
                            except BaseException:
                                dest.unlink(missing_ok=True)
                                dest = None
                                raise
                            items.append(item)
                    except (pieces.PieceError, OSError, ValueError) as e:
                        errors.append(_error(selection, e))
    return {'items': items, 'errors': errors, 'folder': folder['title']}
