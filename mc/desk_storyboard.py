"""The Desk v1 storyboard store (MC-1021 R1-W S9a = MC-1020; plan §2.G M25/M25b, §5).

A STORYBOARD is the ordered scene list a video is built from. Before this module
it lived only in the page (`videoDetail[familyId].scenes`), so a reload threw the
work away.

ONE STORYBOARD PER OWNER (decided 2026-10-01, journal 3ad3c1fb "DECIDED WITHOUT
RON", MC-1020 item 4): a video PIECE owns one storyboard shared by all of its
versions, and a standalone Studio item (MC-1024, no campaign, no piece) owns its
own, keyed by the id the page minted for the draft. They live in
`store['storyboards']` inside `data/desk.json` under `_store_lock`, as
`piece:<id>` / `studio:<id>`: the same file as every other Desk store, outside
DATA_DIR (the LOAD-BEARING rule in CLAUDE.md), so it is not a project record.

SHAPE. `{rev, scenes: [Scene], pending_edits: [{id, label}], story, updated_at}`,
where `story` is the free text the scenes were (or are to be) made from (optional:
a board stored before it existed reads as '', and a PUT that omits it keeps the
stored one), and a Scene is `{id, label, line, duration_sec, picture: AssetRef|null, edited}`. ORDER
IS THE ARRAY INDEX: there is no sort key, so an insert, a remove or a reorder is
just a different list. `pending_edits` is stored (a reload must not lose the
queued edits). An AssetRef is `{path, kind: 'image', title}` with `path` relative
to `data/uploads` and under the material library; `src` (the /api/serve-image
URL) is derived on every read, never stored.

WRITES ARE A WHOLE-LIST PUT GUARDED BY `rev`. The caller sends the rev it last
read; a different stored rev is a 409 carrying the current one, so two tabs (or a
tab and an agent) never silently overwrite each other. A write is a successful
`rev + 1`. A PUT never creates a second copy of anything, and an Undo is simply
another PUT of the earlier list.

PICTURES live in `data/uploads/desk/library/image/Storyboards/` (written by the
same `_write_library_file` M21/M22 use), so `/api/serve-image` already allows
them. The stored path is re-checked against that library root on every PUT AND
every read: a hand-edited store, `..`, an absolute path or a symlink out of the
library cannot make a scene point anywhere else. Dropping a scene or replacing
its picture never deletes the file: it is library material, and Undo must be
able to bring the scene back.

EXAMPLE (placeholder) scenes are the client's own; the client filters them out
before it PUTs, and nothing here knows about them.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from mc import desk as _desk
from mc import desk_pieces as _pieces
from mc.core import _log, now_iso

PieceError = _pieces.PieceError

STORYBOARD_FOLDER = _pieces.STORYBOARD_FOLDER  # <library>/image/Storyboards/
MAX_SCENES = 200
MAX_PENDING = 500
MAX_LABEL = 200
MAX_LINE = 2000
MAX_STORY = 20000
MAX_PENDING_LABEL = 300
MAX_DURATION_SEC = 3600

_CLIENT_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')


def _owner_key(owner_kind: str, owner_id: str) -> str:
    return f'{owner_kind}:{owner_id}'


def _check_owner_id(owner_id) -> str:
    if not (isinstance(owner_id, str) and _CLIENT_ID.match(owner_id)):
        raise PieceError('an id must be 1-80 letters, digits, - or _')
    return owner_id


def _check_owner(store: dict, owner_kind: str, owner_id: str) -> None:
    """A piece storyboard needs a real VIDEO piece. A studio one needs only a
    well-formed id (the draft lives in the browser until it is first saved)."""
    _check_owner_id(owner_id)
    if owner_kind == 'piece':
        piece = _pieces._find(store, owner_id)
        if piece.get('kind') != 'video':
            raise PieceError('only a video piece has a storyboard')
    elif owner_kind != 'studio':
        raise PieceError('unknown storyboard owner')


def _boards(store: dict) -> dict:
    return store.setdefault('storyboards', {})


def _library_image_root() -> Path:
    return _pieces._uploads_root().joinpath(*_pieces.LIBRARY_ROOT, 'image')


def _resolve_picture(rel) -> Path:
    """`rel` (relative to data/uploads) to an existing image file UNDER the
    material library's image root, or PieceError. The realpath collapses `..`
    and symlinks before the containment check."""
    if not isinstance(rel, str) or not rel.strip():
        raise PieceError('a picture needs a path under the material library')
    base = _library_image_root()
    try:
        real = (_pieces._uploads_root() / rel).resolve()
        base = base.resolve()
    except (OSError, ValueError):
        raise PieceError('that picture path is not valid')
    try:
        real.relative_to(base)
    except ValueError:
        raise PieceError('a scene picture must live in the material library (data/uploads/desk/library/image)')
    if _pieces.asset_kind_for(real.name) != 'image':
        raise PieceError('a scene picture must be an image (png, jpg, gif, webp)')
    if not real.is_file():
        raise PieceError('that picture does not exist in the material library', 404)
    return real


def _picture_out(pic) -> dict | None:
    """A stored AssetRef to what the page reads, with `src` derived. A picture
    that no longer resolves (file deleted, store edited by hand) reads as null
    rather than as a path outside the library: the scene stays, the picture is
    gone, and the page says so by drawing no thumbnail."""
    if not isinstance(pic, dict):
        return None
    try:
        real = _resolve_picture(pic.get('path'))
    except PieceError:
        _log(f'[desk] storyboard picture no longer resolves: {pic.get("path")!r}', flush=True)
        return None
    rel = _pieces._rel_of(real)
    return {'path': rel, 'kind': 'image', 'title': pic.get('title') or real.name,
            'src': _pieces._src_for(rel, 'image')}


def _board_out(board: dict | None, owner_kind: str, owner_id: str) -> dict:
    board = board or {}
    return {
        'owner': {'kind': owner_kind, 'id': owner_id},
        'rev': int(board.get('rev') or 0),
        'title': board.get('title') or '',
        'story': board.get('story') or '',
        'scenes': [{'id': s['id'], 'label': s.get('label') or '', 'line': s.get('line') or '',
                    'duration_sec': s.get('duration_sec'), 'picture': _picture_out(s.get('picture')),
                    'edited': bool(s.get('edited'))}
                   for s in board.get('scenes') or []],
        'pending_edits': [{'id': p['id'], 'label': p.get('label') or ''}
                          for p in board.get('pending_edits') or []],
        'updated_at': board.get('updated_at'),
    }


# -- validation -------------------------------------------------------------------

def _clean_duration(value, problems: list[str], where: str):
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or not (0 < value <= MAX_DURATION_SEC):
        problems.append(f'{where}: duration_sec must be a number above 0 and at most {MAX_DURATION_SEC}')
        return None
    return value


def _clean_scene(raw, i: int, problems: list[str]):
    where = f'scene {i + 1}'
    if not isinstance(raw, dict):
        problems.append(f'{where}: must be an object')
        return None
    n = len(problems)
    sid = raw.get('id')
    if not (isinstance(sid, str) and _CLIENT_ID.match(sid)):
        problems.append(f'{where}: id must be 1-80 letters, digits, - or _')
    label = raw.get('label')
    if not isinstance(label, str) or not label.strip():
        problems.append(f'{where}: label is required')
    elif len(label.strip()) > MAX_LABEL:
        problems.append(f'{where}: label is too long (max {MAX_LABEL} characters)')
    line = raw.get('line', '')
    if line is None:
        line = ''
    if not isinstance(line, str):
        problems.append(f'{where}: line must be text')
    elif len(line.strip()) > MAX_LINE:
        problems.append(f'{where}: line is too long (max {MAX_LINE} characters)')
    duration = _clean_duration(raw.get('duration_sec'), problems, where)
    edited = raw.get('edited', False)
    if not isinstance(edited, bool):
        problems.append(f'{where}: edited must be true or false')
    picture = None
    pic = raw.get('picture')
    if pic is not None:
        if not isinstance(pic, dict):
            problems.append(f'{where}: picture must be null or {{path, title?}}')
        else:
            try:
                real = _resolve_picture(pic.get('path'))
                title = pic.get('title')
                if title is not None and not isinstance(title, str):
                    raise PieceError('picture title must be text')
                picture = {'path': _pieces._rel_of(real), 'kind': 'image',
                           'title': ((title or '').strip() or real.name)[:_pieces.MAX_TITLE]}
            except PieceError as e:
                problems.append(f'{where}: {e}')
    if len(problems) > n:
        return None
    return {'id': sid, 'label': str(label).strip(), 'line': str(line).strip(), 'duration_sec': duration,
            'picture': picture, 'edited': edited}


def _clean_pending(raw, problems: list[str]) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_PENDING:
        problems.append(f'pending_edits must be a list of at most {MAX_PENDING} items')
        return []
    out, seen = [], set()
    for i, p in enumerate(raw):
        where = f'pending edit {i + 1}'
        if not isinstance(p, dict):
            problems.append(f'{where}: must be an object')
            continue
        pid, label = p.get('id'), p.get('label')
        if not (isinstance(pid, str) and _CLIENT_ID.match(pid)):
            problems.append(f'{where}: id must be 1-80 letters, digits, - or _')
        elif pid in seen:
            problems.append(f'{where}: duplicate id {pid}')
        elif not isinstance(label, str) or not label.strip():
            problems.append(f'{where}: label is required')
        elif len(label.strip()) > MAX_PENDING_LABEL:
            problems.append(f'{where}: label is too long (max {MAX_PENDING_LABEL} characters)')
        else:
            seen.add(pid)
            out.append({'id': pid, 'label': label.strip()})
    return out


def _clean_scenes(raw, problems: list[str]) -> list[dict]:
    if not isinstance(raw, list):
        problems.append('scenes must be a list')
        return []
    if len(raw) > MAX_SCENES:
        problems.append(f'a storyboard can carry at most {MAX_SCENES} scenes')
        return []
    out, seen = [], set()
    for i, s in enumerate(raw):
        clean = _clean_scene(s, i, problems)
        if clean is None:
            continue
        if clean['id'] in seen:
            problems.append(f'scene {i + 1}: duplicate id {clean["id"]}')
            continue
        seen.add(clean['id'])
        out.append(clean)
    return out


# -- store access -----------------------------------------------------------------

def get_storyboard(owner_kind: str, owner_id: str) -> dict:
    """The stored storyboard, or an empty one at rev 0 for an owner that has none
    yet (an empty board is the honest answer, not a 404). 404 only for a piece
    that does not exist."""
    with _desk._store_lock:
        store = _desk._read_store()
        _check_owner(store, owner_kind, owner_id)
        board = _boards(store).get(_owner_key(owner_kind, owner_id))
    return _board_out(board, owner_kind, owner_id)


def list_studio_storyboards() -> list[dict]:
    """The standalone Studio items that have a saved storyboard, newest first, as
    `{id, title, scenes, updated_at}`. The page's Recent list reads this: a draft's
    id is minted in the browser, so without it a reload would lose the way back."""
    with _desk._store_lock:
        boards = dict(_boards(_desk._read_store()))
    out = []
    for key, board in boards.items():
        kind, _, oid = key.partition(':')
        if kind != 'studio' or not isinstance(board, dict):
            continue
        out.append({'id': oid, 'title': board.get('title') or '', 'scenes': len(board.get('scenes') or []),
                    'updated_at': board.get('updated_at')})
    out.sort(key=lambda b: b['updated_at'] or '', reverse=True)
    return out


def put_storyboard(owner_kind: str, owner_id: str, body) -> dict:
    """Replace the whole list. `body` = `{rev, scenes, pending_edits?, title?, story?}`.
    A `rev` that is not the stored one is a 409 whose `problems` names the current
    rev; a successful write is `rev + 1`. Returns the stored storyboard."""
    if not isinstance(body, dict):
        raise PieceError('body must be a JSON object')
    rev = body.get('rev')
    if isinstance(rev, bool) or not isinstance(rev, int) or rev < 0:
        raise PieceError('rev is required: the revision you last read (0 for a new storyboard)')
    problems: list[str] = []
    scenes = _clean_scenes(body.get('scenes'), problems)
    pending = _clean_pending(body.get('pending_edits'), problems)
    title = body.get('title')
    if title is not None:
        if not isinstance(title, str) or len(title.strip()) > _pieces.MAX_TITLE:
            problems.append(f'title must be text of at most {_pieces.MAX_TITLE} characters')
            title = None
        elif owner_kind != 'studio':
            problems.append('only a Studio item keeps a title here: a piece has its own')
            title = None
    story = body.get('story')
    if story is not None and (not isinstance(story, str) or len(story) > MAX_STORY):
        problems.append(f'story must be text of at most {MAX_STORY} characters')
    if problems:
        raise PieceError('the storyboard is not valid', 400, problems)
    with _desk._store_lock:
        store = _desk._read_store()
        _check_owner(store, owner_kind, owner_id)
        boards = _boards(store)
        key = _owner_key(owner_kind, owner_id)
        board = boards.get(key) or {}
        current = int(board.get('rev') or 0)
        if rev != current:
            raise PieceError(f'this storyboard changed since you loaded it (it is at revision {current}, '
                             f'you had {rev}): reload it, then make the change again', 409,
                             [f'current_rev={current}'])
        new = {'rev': current + 1, 'scenes': scenes, 'pending_edits': pending, 'updated_at': now_iso(),
               'story': story if isinstance(story, str) else (board.get('story') or '')}
        if board.get('thread'):
            new['thread'] = board['thread']   # the agent chat rides with the board (mc/desk_story_chat.py)
        if owner_kind == 'studio':
            new['title'] = (title.strip() if isinstance(title, str) else board.get('title')) or ''
        boards[key] = new
        _desk._write_store(store)
        return _board_out(new, owner_kind, owner_id)


def save_picture(owner_kind: str, owner_id: str, filename: str, stream) -> dict:
    """Write an uploaded scene picture into the material library
    (`image/Storyboards/`) and return its AssetRef `{path, kind, title, src}`.
    IMAGES only: a video is refused here, before a byte is kept. The scene is not
    changed: the caller PUTs the list with the new picture on it."""
    with _desk._store_lock:
        _check_owner(_desk._read_store(), owner_kind, owner_id)
    if _pieces.asset_kind_for(filename) != 'image':
        raise PieceError('a scene picture must be an image (png, jpg, gif, webp)')
    dest = _pieces._write_library_file(filename, stream, STORYBOARD_FOLDER)[0]
    real = dest.resolve()
    rel = _pieces._rel_of(real)
    return {'path': rel, 'kind': 'image', 'title': Path(filename).name[:_pieces.MAX_TITLE] or real.name,
            'src': _pieces._src_for(rel, 'image')}
