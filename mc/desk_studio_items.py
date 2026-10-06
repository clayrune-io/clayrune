"""Deleting what Studio made, with an Undo that brings it all back.

A row in Studio's Recent list is one of two things on the server:

  * a DRAFT: a standalone storyboard, `store['storyboards']['studio:<id>']` in
    `data/desk.json` (mc/desk_storyboard.py). Its scene pictures are files in the
    material library's `image/Storyboards/` folder, one file per upload.
  * a FILE in the material library (a saved image or a rendered video).

DELETE MOVES, NEVER ERASES. A deleted draft's board and a deleted file go to
`data/uploads/desk/_trash/<token>/` (a manifest plus the files), outside the
library so the Material library no longer lists them. `restore()` puts every
byte back where it was, so Undo is exact: the same board at the same revision,
the same files at the same paths. Entries older than TRASH_KEEP_DAYS are purged
the next time something is deleted.

WHICH FILES GO WITH A DRAFT. Only a scene picture that nothing else uses: no
other storyboard's scene and no piece's asset points at the same path. A picture
shared with anything else stays in the library, untouched.

WHAT CANNOT BE DELETED (409, with the reason the page shows):
  * a draft whose latest render is still queued or rendering (no cancellation);
  * a library file a campaign piece carries as an asset ("Attached to <campaign>"),
    so a campaign never loses a piece's media silently;
  * a library file a Studio draft's scene uses (delete the draft instead).
`usage()` reports the same facts up front so the page can disable the control.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

from mc import desk as _desk
from mc.atomic_json import write_json_atomic
from mc import desk_engines as _engines
from mc import desk_pieces as _pieces
from mc import desk_storyboard as _storyboard
from mc.core import _log, now_iso

PieceError = _pieces.PieceError

TRASH_DIR = ('desk', '_trash')            # under UPLOADS_ROOT, beside (not inside) the library
TRASH_KEEP_DAYS = 30
RENDERING = ('queued', 'rendering')

_TOKEN_CHARS = set('0123456789abcdef')


# -- paths ------------------------------------------------------------------------

def _trash_root() -> Path:
    return _pieces._uploads_root().joinpath(*TRASH_DIR)


def _library_root() -> Path:
    return _pieces._uploads_root().joinpath(*_pieces.LIBRARY_ROOT)


def _check_token(token) -> str:
    if not (isinstance(token, str) and len(token) == 32 and set(token) <= _TOKEN_CHARS):
        raise PieceError('that is not a restore token', 404)
    return token


def _library_file(rel) -> Path:
    """`rel` (relative to data/uploads) to a real file under the material library, or
    PieceError. Same containment order as everywhere else: realpath first."""
    if not isinstance(rel, str) or not rel.strip():
        raise PieceError('a file path is required')
    try:
        real = (_pieces._uploads_root() / rel).resolve()
        base = _library_root().resolve()
        real.relative_to(base)
    except (OSError, ValueError):
        raise PieceError('that file is not in the material library')
    if not real.is_file():
        raise PieceError('that file is not in the material library', 404)
    return real


# -- who uses a library file ------------------------------------------------------

def _campaign_title(store: dict, campaign_id) -> str:
    camp = (store.get('campaigns') or {}).get(campaign_id) or {}
    return (camp.get('plan') or {}).get('title') or camp.get('title') or 'a campaign'


def _norm(rel) -> str:
    return str(rel or '').replace('\\', '/').strip('/')


def _references(store: dict) -> tuple[dict, dict]:
    """`(attached, in_boards)`: library path -> the campaign piece carrying it as an
    asset, and path -> every Studio/piece storyboard whose scenes use it."""
    attached: dict[str, dict] = {}
    for piece in _pieces._pieces(store).values():
        for a in piece.get('assets') or []:
            path = _norm(a.get('path'))
            if path and path not in attached:
                cid = piece.get('campaign_id')
                attached[path] = {'piece_id': piece.get('id'), 'campaign_id': cid,
                                  'campaign_title': _campaign_title(store, cid)}
    in_boards: dict[str, list] = {}
    for key, board in (store.get('storyboards') or {}).items():
        if not isinstance(board, dict):
            continue
        kind, _, oid = key.partition(':')
        for scene in board.get('scenes') or []:
            pic = scene.get('picture')
            path = _norm(pic.get('path')) if isinstance(pic, dict) else ''
            if path:
                in_boards.setdefault(path, []).append(
                    {'key': key, 'kind': kind, 'id': oid, 'title': board.get('title') or ''})
    return attached, in_boards


def _rendering_owners() -> set[str]:
    """Ids of the Studio drafts whose newest render is still going."""
    try:
        with _engines._lock:
            renders = list(_engines._read_store().get('renders', {}).values())
    except RuntimeError as e:
        _log(f'[desk] Studio delete could not read the render records: {e}', flush=True)
        raise PieceError('could not tell whether this is still rendering: the render records are unreadable', 503)
    newest: dict[str, dict] = {}
    for r in renders:
        owner = r.get('owner') or {}
        oid = str(owner.get('id') or '')
        if owner.get('kind') != 'studio' or not oid:
            continue
        cur = newest.get(oid)
        if cur is None or (r.get('created_at') or '') >= (cur.get('created_at') or ''):
            newest[oid] = r
    return {oid for oid, r in newest.items() if r.get('status') in RENDERING}


def usage() -> dict:
    """What the Recent list needs to know before it offers a delete:
    `{rendering: [draft id], files: {path: {attached: {...}|None, draft: {id,title}|None}}}`.
    A path nothing uses is simply absent."""
    rendering = _rendering_owners()
    with _desk._store_lock:
        store = _desk._read_store()
        attached, in_boards = _references(store)
    files: dict[str, dict] = {}
    for path in set(attached) | set(in_boards):
        draft = next(({'id': b['id'], 'title': b['title']} for b in in_boards.get(path, []) if b['kind'] == 'studio'), None)
        piece_board = bool([b for b in in_boards.get(path, []) if b['kind'] == 'piece'])
        files[path] = {'attached': attached.get(path), 'draft': draft, 'piece_board': piece_board}
    return {'rendering': sorted(rendering), 'files': files}


# -- trash ------------------------------------------------------------------------

def _purge_old(root: Path) -> None:
    """Drop trash entries past TRASH_KEEP_DAYS. Best effort: a purge that cannot run
    must never stop a delete."""
    cutoff = time.time() - TRASH_KEEP_DAYS * 86400
    try:
        entries = [d for d in root.iterdir() if d.is_dir()]
    except OSError:
        return
    for d in entries:
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d)
        except OSError as e:
            _log(f'[desk] could not purge old Studio trash {d.name}: {e}', flush=True)


def _stash(token: str, manifest: dict, rels: list[str]) -> Path:
    """Move each library file in `rels` into the trash entry for `token` and write the
    manifest. A failure part way puts what moved back and raises."""
    uploads = _pieces._uploads_root()
    root = _trash_root()
    root.mkdir(parents=True, exist_ok=True)
    _purge_old(root)
    entry = root / token
    (entry / 'files').mkdir(parents=True)
    moved: list[tuple[Path, Path]] = []
    try:
        for i, rel in enumerate(rels):
            src = uploads / rel
            dst = entry / 'files' / str(i)
            shutil.move(str(src), str(dst))
            moved.append((src, dst))
        manifest['files'] = [{'rel': rel, 'slot': str(i)} for i, rel in enumerate(rels)]
        write_json_atomic(entry / 'manifest.json', manifest, indent=2, ensure_ascii=False)
    except BaseException:
        for src, dst in reversed(moved):
            try:
                shutil.move(str(dst), str(src))
            except OSError as e:
                _log(f'[desk] could not put back {src.name} after a failed delete: {e}', flush=True)
        shutil.rmtree(entry, ignore_errors=True)
        raise
    return entry


def _put_back(entry: Path, manifest: dict) -> None:
    uploads = _pieces._uploads_root()
    for f in manifest.get('files') or []:
        dst = uploads / f['rel']
        src = entry / 'files' / f['slot']
        if not src.is_file():
            continue
        if dst.exists():
            raise PieceError(f'cannot restore: {dst.name} already exists in the library', 409)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))


# -- delete -----------------------------------------------------------------------

def delete_draft(item_id: str) -> dict:
    """Trash a Studio draft and the scene pictures only it uses. -> `{token, files}`.
    404 for a draft the server holds no storyboard for (the page never saved it)."""
    _storyboard._check_owner_id(item_id)
    if item_id in _rendering_owners():
        raise PieceError('Rendering, wait for it to finish', 409)
    key = f'studio:{item_id}'
    with _desk._store_lock:
        store = _desk._read_store()
        boards = _storyboard._boards(store)
        board = boards.get(key)
        if board is None:
            raise PieceError('that draft is not saved on the server', 404)
        attached, in_boards = _references(store)
        mine: list[str] = []
        for scene in board.get('scenes') or []:
            pic = scene.get('picture')
            path = _norm(pic.get('path')) if isinstance(pic, dict) else ''
            if not path or path in mine or path in attached:
                continue
            if any(b['key'] != key for b in in_boards.get(path, [])):
                continue
            try:
                _library_file(path)
            except PieceError:
                continue
            mine.append(path)
        token = uuid.uuid4().hex
        manifest = {'token': token, 'kind': 'draft', 'id': item_id, 'board': board, 'deleted_at': now_iso()}
        _stash(token, manifest, mine)
        try:
            del boards[key]
            _desk._write_store(store)
        except BaseException:
            _put_back(_trash_root() / token, manifest)
            shutil.rmtree(_trash_root() / token, ignore_errors=True)
            raise
    return {'token': token, 'files': len(mine)}


def delete_file(rel: str) -> dict:
    """Trash one library file. -> `{token}`. Refused while a campaign piece carries it
    or a Studio draft's scene uses it."""
    real = _library_file(rel)
    rel = _pieces._rel_of(real)
    with _desk._store_lock:
        store = _desk._read_store()
        attached, in_boards = _references(store)
    if rel in attached:
        raise PieceError(f"Attached to {attached[rel]['campaign_title']}, detach it there first", 409)
    if rel in in_boards:
        raise PieceError('Used in a storyboard, delete that draft instead', 409)
    token = uuid.uuid4().hex
    _stash(token, {'token': token, 'kind': 'file', 'id': rel, 'deleted_at': now_iso()}, [rel])
    return {'token': token}


# -- restore ----------------------------------------------------------------------

def restore(token: str) -> dict:
    """Undo a delete: files back at their paths, the draft's board back at its
    revision. -> `{kind, id}`. 404 once the entry is gone (purged, or restored)."""
    token = _check_token(token)
    entry = _trash_root() / token
    try:
        manifest = json.loads((entry / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise PieceError('there is nothing to restore (it was already restored or has expired)', 404)
    with _desk._store_lock:
        store = _desk._read_store()
        boards = _storyboard._boards(store)
        key = f"studio:{manifest.get('id')}"
        if manifest.get('kind') == 'draft' and key in boards:
            raise PieceError('a draft with that id exists again, so the deleted one was not restored', 409)
        _put_back(entry, manifest)
        if manifest.get('kind') == 'draft':
            boards[key] = manifest['board']
            _desk._write_store(store)
    shutil.rmtree(entry, ignore_errors=True)
    return {'kind': manifest.get('kind'), 'id': manifest.get('id')}
