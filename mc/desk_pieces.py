"""The Desk v1 piece store (MC-1021 R1-W S4; docs/desk_v1/R1W_WIRING_PLAN.md §2.D/§2.E).

A PIECE is one thing a campaign says (a post, an article, a video, an image);
its VERSIONS are that piece on each account, each with its own state. Before
this module the only pieces in existence were the fixture's `families`.

WHERE IT LIVES: `store['pieces']` inside `data/desk.json`, under the same
`_store_lock` as every other Desk store. That file is a sibling of DATA_DIR,
never a member (the LOAD-BEARING DATA_DIR rule in CLAUDE.md), so a piece is not
a project record and needs no `EXCLUDED_SIDECAR_SUFFIXES` entry.

THE APPROVAL GATE IS NOT HERE. `update_version` can move a version between
`drafting`, `needs_review`, `planned`, `skipped` and `archived` and nothing
else: it can never write `approved`, `scheduled`, `sending`, `submitted` or
`verified_published`. Those belong to the human approve route and the publish
tick, neither of which exists yet (plan M19, slice S7). A body or an account on
a version that already carries an approval cannot be changed behind that
approval: the version has to be sent back to `needs_review` first, which clears
the stamp.

ASSETS are references, not copies. A piece carries any number of them, and a
campaign any number of pieces of one kind (Ron, 2026-09-29). An asset's `path`
is stored RELATIVE to `data/uploads/` and is re-checked against that root every
time it is read or attached, so a hand-edited store cannot point
`/api/serve-image` outside it, and moving the data directory moves the assets
with it.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from urllib.parse import quote

from mc import desk as _desk
from mc.core import _log, now_iso

# Wired by server.py (desk_routes.wire(uploads_root=...)): `data/uploads`. None
# means nothing can be attached or listed, never "anywhere".
UPLOADS_ROOT: Path | None = None

PIECE_KINDS = ('post', 'article', 'video', 'image')
# desk-v1-kit.js VERSION_STATES, the 15 words every surface renders.
VERSION_STATES = (
    'planned', 'drafting', 'needs_review', 'blocked', 'approved', 'scheduled',
    'sending', 'submitted', 'verified_published', 'you_reported', 'unknown_outcome',
    'failed', 'held', 'skipped', 'archived',
)
# The ONLY states the plain PATCH may write (plan M18). Everything else is set by
# the human approve route or the publish tick.
WRITABLE_STATES = ('drafting', 'needs_review', 'planned', 'skipped', 'archived')
# Once a version has been handed to a platform nothing about it may be edited,
# and its piece may not be deleted (plan M16: "409 once any version is `sending`
# or later").
SENT_STATES = ('sending', 'submitted', 'verified_published', 'you_reported',
               'unknown_outcome', 'failed')
# A human approved this exact text for this exact account. Editing either needs
# the approval withdrawn first.
APPROVED_STATES = ('approved', 'scheduled')

ASSET_KINDS = ('image', 'video')
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
VIDEO_EXTS = ('.mp4', '.mov', '.webm', '.m4v')
# Per-file caps for an upload (bytes). A video is the big one; an image over
# 25 MB is a mistake, not a screenshot.
MAX_UPLOAD_BYTES = {'image': 25 * 1024 * 1024, 'video': 500 * 1024 * 1024}
LIBRARY_ROOT = ('desk', 'library')       # under UPLOADS_ROOT: <kind>/<folder>/<file>
UPLOAD_FOLDER = 'Uploads'               # where a file picked from the computer lands
STUDIO_FOLDER = 'Studio'                # where a file Studio made lands (no campaign)

MAX_TITLE = 200
MAX_BODY = 100_000
MAX_CLAIMS = 200
MAX_ASSETS = 50

_CLIENT_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')
_SAFE_NAME = re.compile(r'[^A-Za-z0-9._ -]+')


class PieceError(ValueError):
    """A refusal with the HTTP status the route should answer."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _uploads_root() -> Path:
    if UPLOADS_ROOT is None:
        raise PieceError('the uploads directory is not wired', 503)
    return Path(UPLOADS_ROOT).resolve()


def asset_kind_for(filename: str) -> str | None:
    ext = os.path.splitext(filename or '')[1].lower()
    if ext in IMAGE_EXTS:
        return 'image'
    if ext in VIDEO_EXTS:
        return 'video'
    return None


def _resolve_under_uploads(rel: str) -> Path:
    """`rel` (relative to data/uploads) to a real file path, or PieceError. The
    realpath collapses `..` and symlinks BEFORE the containment check, the same
    order /api/serve-image uses."""
    if not isinstance(rel, str) or not rel.strip():
        raise PieceError('an asset needs a path under data/uploads')
    root = _uploads_root()
    try:
        real = (root / rel).resolve()
    except (OSError, ValueError):
        raise PieceError('that asset path is not valid')
    try:
        real.relative_to(root)
    except ValueError:
        raise PieceError('an asset must live under data/uploads')
    if not real.is_file():
        raise PieceError('that file does not exist under data/uploads', 404)
    return real


def _rel_of(real: Path) -> str:
    return real.relative_to(_uploads_root()).as_posix()


def _src_for(rel: str, kind: str) -> str | None:
    """The URL the browser loads a thumbnail from: /api/serve-image, which serves
    images only, so a video has none (the surfaces draw a blank ▶ tile)."""
    root = UPLOADS_ROOT
    if root is None or kind != 'image':
        return None
    return '/api/serve-image?path=' + quote(str(Path(root).resolve() / rel), safe='')


# -- shapes -----------------------------------------------------------------------

def _clean_text(value, limit: int, what: str, *, required: bool = False) -> str:
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise PieceError(f'{what} must be text')
    value = value.strip()
    if required and not value:
        raise PieceError(f'{what} is required')
    if len(value) > limit:
        raise PieceError(f'{what} is too long (max {limit} characters)')
    return value


def _clean_claims(claims) -> list[dict]:
    if claims is None:
        return []
    if not isinstance(claims, list) or len(claims) > MAX_CLAIMS:
        raise PieceError(f'claims must be a list of at most {MAX_CLAIMS}')
    out, seen = [], set()
    for c in claims:
        if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not _CLIENT_ID.match(c['id']):
            raise PieceError('every claim needs an id of 1-80 letters, digits, - or _')
        if c['id'] in seen:
            raise PieceError(f"duplicate claim id {c['id']!r}")
        seen.add(c['id'])
        src = c.get('source')
        if src is not None and not isinstance(src, str):
            raise PieceError('a claim source must be text or null')
        out.append({'id': c['id'], 'text': _clean_text(c.get('text'), 1000, 'claim text'),
                    'source': (src or '').strip() or None})
    return out


def _clean_source(source):
    if source is None:
        return None
    if not isinstance(source, dict) or not isinstance(source.get('kind'), str) or not source['kind'].strip():
        raise PieceError('source must be {"kind": "...", "ref": "..."} or null')
    ref = source.get('ref')
    if ref is not None and not isinstance(ref, str):
        raise PieceError('source ref must be text')
    return {'kind': source['kind'].strip()[:40], 'ref': (ref or '')[:300] or None}


def _clean_word_count(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value > 1_000_000:
        raise PieceError('word_count must be a number between 0 and 1,000,000, or null')
    return int(value)


def _clean_when(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise PieceError('scheduled_at must be an ISO 8601 time or null')
    try:
        _desk._parse_dt(value)
    except ValueError:
        raise PieceError('scheduled_at must be an ISO 8601 time or null')
    return value


def _new_version(account_id: str, *, body: str = '', vid: str | None = None,
                 fmt: str | None = None) -> dict:
    return {
        'id': vid or _desk._new_id('v'), 'account_id': account_id, 'state': 'drafting',
        'body': body, 'revision': 0, 'claims_state': {}, 'scheduled_at': None,
        'slot_id': None, 'approved': None, 'receipt': None, 'failure': None,
        'format': fmt,
    }


def _claim_verdict(piece: dict, ver: dict, claim: dict) -> str:
    """`blocked` while a claim has no source and nobody has accepted or removed
    it for this version; `ok` otherwise. Derived here so every reader agrees."""
    if claim.get('source'):
        return 'ok'
    status = ((ver.get('claims_state') or {}).get(claim['id']) or {}).get('status')
    return 'ok' if status in ('accepted', 'removed', 'edited') else 'blocked'


def v1_piece(piece: dict) -> dict:
    """A stored piece in the shape the v1 surfaces read (the fixture's `family`):
    camelCase, a version's account as `channelId`, `scheduled_at` as `publishAt`,
    a receipt's `posted_at` as `publishedAt`, each asset carrying a `src`. A
    fresh dict: nothing the caller does to it reaches the store."""
    out = {
        'id': piece['id'], 'campaignId': piece.get('campaign_id'), 'projectId': piece.get('project_id'),
        'kind': piece.get('kind'), 'title': piece.get('title') or '',
        'body': piece.get('body') or '', 'wordCount': piece.get('word_count'),
        'source': piece.get('source'), 'createdAt': piece.get('created_at'),
        'updatedAt': piece.get('updated_at'),
        'assets': [{'id': a['id'], 'kind': a.get('kind'), 'title': a.get('title') or '',
                    'path': a.get('path'), 'src': _src_for(a.get('path') or '', a.get('kind') or '')}
                   for a in (piece.get('assets') or [])],
        'versions': [],
    }
    if piece.get('draft_status'):
        out['draft'] = {'status': piece['draft_status']}
    claims = piece.get('claims') or []
    for v in piece.get('versions') or []:
        ver = {
            'id': v['id'], 'channelId': v.get('account_id'), 'state': v.get('state'),
            'revision': v.get('revision') or 0, 'body': v.get('body') or '',
            'claimsState': dict(v.get('claims_state') or {}),
            'approved': v.get('approved'), 'failure': v.get('failure'),
        }
        if v.get('format'):
            ver['format'] = v['format']
        if v.get('scheduled_at'):
            ver['publishAt'] = v['scheduled_at']
        receipt = v.get('receipt')
        if receipt:
            ver['receipt'] = receipt
            if receipt.get('posted_at'):
                ver['publishedAt'] = receipt['posted_at']
        if claims:
            ver['claims'] = [{'id': c['id'],
                              'text': ((v.get('claims_state') or {}).get(c['id']) or {}).get('revised_text') or c['text'],
                              'source': c.get('source'), 'verdict': _claim_verdict(piece, v, c)}
                             for c in claims]
        out['versions'].append(ver)
    return out


# -- store access -----------------------------------------------------------------

def _pieces(store: dict) -> dict:
    return store.setdefault('pieces', {})


def _campaign_of(store: dict, campaign_id) -> dict:
    camp = (store.get('campaigns') or {}).get(campaign_id) if isinstance(campaign_id, str) else None
    if camp is None:
        raise PieceError('campaign not found', 404)
    return camp


def _find(store: dict, piece_id: str) -> dict:
    piece = _pieces(store).get(piece_id)
    if piece is None:
        raise PieceError('piece not found', 404)
    return piece


def _find_version(piece: dict, version_id: str) -> dict:
    for v in piece.get('versions') or []:
        if v.get('id') == version_id:
            return v
    raise PieceError('version not found', 404)


def list_pieces(campaign_id: str | None = None) -> list[dict]:
    with _desk._store_lock:
        store = _desk._read_store()
    rows = [p for p in _pieces(store).values()
            if campaign_id is None or p.get('campaign_id') == campaign_id]
    rows.sort(key=lambda p: (p.get('created_at') or '', p['id']))
    return [v1_piece(p) for p in rows]


def get_piece(piece_id: str) -> dict:
    with _desk._store_lock:
        store = _desk._read_store()
    return v1_piece(_find(store, piece_id))


def create_piece(campaign_id: str, kind: str, title: str = '', *, piece_id: str | None = None,
                 body=None, word_count=None, source=None, claims=None) -> dict:
    if kind not in PIECE_KINDS:
        raise PieceError(f'kind must be one of {", ".join(PIECE_KINDS)}')
    if piece_id is not None and not (isinstance(piece_id, str) and _CLIENT_ID.match(piece_id)):
        raise PieceError('piece id must be 1-80 letters, digits, - or _')
    title = _clean_text(title, MAX_TITLE, 'title') or f'New {kind}'
    body = _clean_text(body, MAX_BODY, 'body')
    word_count = _clean_word_count(word_count)
    source = _clean_source(source)
    claims = _clean_claims(claims)
    with _desk._store_lock:
        store = _desk._read_store()
        camp = _campaign_of(store, campaign_id)
        pieces = _pieces(store)
        if piece_id is not None and piece_id in pieces:
            raise PieceError('a piece with that id already exists', 409)
        now = now_iso()
        piece = {
            'id': piece_id or _desk._new_id('piece'), 'campaign_id': campaign_id,
            'project_id': camp.get('project_id'), 'kind': kind, 'title': title,
            'body': body, 'word_count': word_count, 'source': source,
            'assets': [], 'claims': claims, 'versions': [], 'draft_status': None,
            'created_at': now, 'updated_at': now,
        }
        pieces[piece['id']] = piece
        _desk._write_store(store)
        return v1_piece(piece)


_PIECE_PATCHABLE = ('title', 'body', 'word_count', 'source', 'claims', 'draft_status')
_DRAFT_STATUSES = ('drafting', 'in_review')


def update_piece(piece_id: str, patch: dict) -> dict:
    if not isinstance(patch, dict):
        raise PieceError('body must be a JSON object')
    unknown = sorted(k for k in patch if k not in _PIECE_PATCHABLE)
    if unknown:
        raise PieceError(f'cannot change: {", ".join(unknown)}')
    clean: dict = {}
    if 'title' in patch:
        clean['title'] = _clean_text(patch['title'], MAX_TITLE, 'title', required=True)
    if 'body' in patch:
        clean['body'] = _clean_text(patch['body'], MAX_BODY, 'body')
    if 'word_count' in patch:
        clean['word_count'] = _clean_word_count(patch['word_count'])
    if 'source' in patch:
        clean['source'] = _clean_source(patch['source'])
    if 'claims' in patch:
        clean['claims'] = _clean_claims(patch['claims'])
    if 'draft_status' in patch:
        ds = patch['draft_status']
        if ds is not None and ds not in _DRAFT_STATUSES:
            raise PieceError(f'draft_status must be null or one of {", ".join(_DRAFT_STATUSES)}')
        clean['draft_status'] = ds
    with _desk._store_lock:
        store = _desk._read_store()
        piece = _find(store, piece_id)
        if ('body' in clean or 'claims' in clean) and any(
                v.get('state') in APPROVED_STATES + SENT_STATES for v in piece.get('versions') or []):
            raise PieceError('a version of this piece is already approved or sent; '
                             'send it back to review before changing the piece text', 409)
        piece.update(clean)
        piece['updated_at'] = now_iso()
        _desk._write_store(store)
        return v1_piece(piece)


def delete_piece(piece_id: str) -> bool:
    """False when there is no such piece. 409 (PieceError) once a version has
    been handed to a platform: the ledger and the receipt still point at it."""
    with _desk._store_lock:
        store = _desk._read_store()
        pieces = _pieces(store)
        piece = pieces.get(piece_id)
        if piece is None:
            return False
        sent = [v['id'] for v in piece.get('versions') or [] if v.get('state') in SENT_STATES]
        if sent:
            raise PieceError('this piece has a version that is sending or already sent '
                             f'({", ".join(sent)}); it cannot be deleted', 409)
        del pieces[piece_id]
        _desk._write_store(store)
        return True


def delete_campaign_pieces(store: dict, campaign_id: str) -> int:
    """Called by `desk.delete_campaign` with the store already held under the lock:
    a deleted campaign must not leave pieces nobody can reach. Never raises."""
    pieces = _pieces(store)
    gone = [pid for pid, p in pieces.items() if p.get('campaign_id') == campaign_id]
    for pid in gone:
        del pieces[pid]
    return len(gone)


# -- versions ---------------------------------------------------------------------

def _known_account(account_id) -> bool:
    if not isinstance(account_id, str) or not account_id:
        return False
    with _desk._store_lock:
        presences = _desk._read_store().get('presences') or {}
    return any(a.get('id') == account_id for a in _desk.v1_accounts(presences))


def add_version(piece_id: str, account_id: str, *, body=None, version_id: str | None = None,
                fmt=None) -> dict:
    if version_id is not None and not (isinstance(version_id, str) and _CLIENT_ID.match(version_id)):
        raise PieceError('version id must be 1-80 letters, digits, - or _')
    if not _known_account(account_id):
        raise PieceError('that account is not connected to this workspace')
    body = _clean_text(body, MAX_BODY, 'body')
    if fmt is not None and (not isinstance(fmt, str) or len(fmt) > 20):
        raise PieceError('format must be short text')
    with _desk._store_lock:
        store = _desk._read_store()
        piece = _find(store, piece_id)
        for v in piece.get('versions') or []:
            if version_id and v['id'] == version_id:
                raise PieceError('a version with that id already exists', 409)
            if v.get('account_id') == account_id and v.get('state') not in ('archived', 'skipped'):
                raise PieceError('this piece is already on that account', 409)
        ver = _new_version(account_id, body=body, vid=version_id, fmt=fmt or None)
        piece.setdefault('versions', []).append(ver)
        piece['updated_at'] = now_iso()
        _desk._write_store(store)
        return v1_piece(piece)


_VERSION_PATCHABLE = ('account_id', 'body', 'claims_state', 'revision', 'scheduled_at', 'state', 'format')
_CLAIM_STATUSES = ('accepted', 'edited', 'removed', 'pending')


def _clean_claims_state(value, piece: dict) -> dict:
    if not isinstance(value, dict):
        raise PieceError('claims_state must be an object keyed by claim id')
    known = {c['id'] for c in piece.get('claims') or []}
    out = {}
    for cid, entry in value.items():
        if cid not in known:
            raise PieceError(f'unknown claim {cid!r}')
        if not isinstance(entry, dict) or entry.get('status') not in _CLAIM_STATUSES:
            raise PieceError(f'claim {cid!r} needs a status of {", ".join(_CLAIM_STATUSES)}')
        rev = entry.get('revised_text')
        if rev is not None and not isinstance(rev, str):
            raise PieceError('revised_text must be text or null')
        n = entry.get('revision', 0)
        if isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise PieceError('a claim revision must be a whole number')
        out[cid] = {'status': entry['status'], 'revised_text': rev, 'revision': n}
    return out


def update_version(piece_id: str, version_id: str, patch: dict) -> dict:
    if not isinstance(patch, dict):
        raise PieceError('body must be a JSON object')
    unknown = sorted(k for k in patch if k not in _VERSION_PATCHABLE)
    if unknown:
        raise PieceError(f'cannot change: {", ".join(unknown)}')
    state = patch.get('state')
    if 'state' in patch and state not in WRITABLE_STATES:
        # The line this module exists to hold: no PATCH, from anyone, can mark a
        # version approved, scheduled, sending, submitted or published.
        raise PieceError(f'state can only be set to {", ".join(WRITABLE_STATES)} here; '
                         'approval is its own human action', 400)
    clean: dict = {}
    if 'body' in patch:
        clean['body'] = _clean_text(patch['body'], MAX_BODY, 'body')
    if 'scheduled_at' in patch:
        clean['scheduled_at'] = _clean_when(patch['scheduled_at'])
    if 'format' in patch:
        f = patch['format']
        if f is not None and (not isinstance(f, str) or len(f) > 20):
            raise PieceError('format must be short text or null')
        clean['format'] = f or None
    if 'revision' in patch:
        r = patch['revision']
        if isinstance(r, bool) or not isinstance(r, int) or r < 0 or r > 10_000:
            raise PieceError('revision must be a whole number')
        clean['revision'] = r
    if 'account_id' in patch and not _known_account(patch['account_id']):
        raise PieceError('that account is not connected to this workspace')
    with _desk._store_lock:
        store = _desk._read_store()
        piece = _find(store, piece_id)
        ver = _find_version(piece, version_id)
        cur = ver.get('state')
        if cur in SENT_STATES:
            raise PieceError(f'this version is {cur}; it cannot be changed', 409)
        if 'claims_state' in patch:
            clean['claims_state'] = _clean_claims_state(patch['claims_state'], piece)
        if cur in APPROVED_STATES and state != 'needs_review' and state not in ('skipped', 'archived'):
            locked = [k for k in ('body', 'claims_state', 'account_id', 'format') if k in patch]
            if locked:
                raise PieceError(f'this version is {cur}: send it back to review before changing '
                                 f'its {", ".join(locked)}', 409)
        if 'account_id' in patch:
            acc = patch['account_id']
            for other in piece.get('versions') or []:
                if other is not ver and other.get('account_id') == acc \
                        and other.get('state') not in ('archived', 'skipped'):
                    raise PieceError('this piece is already on that account', 409)
            clean['account_id'] = acc
        ver.update(clean)
        if state is not None:
            ver['state'] = state
            if state != cur and cur in APPROVED_STATES:
                # Withdrawing the approval is the point of leaving these states:
                # a stale stamp on an unreviewed version would read as consent.
                ver['approved'] = None
                ver['slot_id'] = None
        piece['updated_at'] = now_iso()
        _desk._write_store(store)
        return v1_piece(piece)


# -- assets -----------------------------------------------------------------------

def _asset_title(real: Path, title) -> str:
    t = _clean_text(title, MAX_TITLE, 'title') if title else ''
    return t or real.name


def add_asset(piece_id: str, *, path: str, title=None, asset_id: str | None = None) -> dict:
    """Attach a file already under data/uploads (an upload this module saved, a
    library file, a generated output). Returns the piece."""
    if asset_id is not None and not (isinstance(asset_id, str) and _CLIENT_ID.match(asset_id)):
        raise PieceError('asset id must be 1-80 letters, digits, - or _')
    real = _resolve_under_uploads(path)
    kind = asset_kind_for(real.name)
    if kind is None:
        raise PieceError('only image (png, jpg, gif, webp) and video (mp4, mov, webm, m4v) files can be attached')
    rel = _rel_of(real)
    with _desk._store_lock:
        store = _desk._read_store()
        piece = _find(store, piece_id)
        assets = piece.setdefault('assets', [])
        if len(assets) >= MAX_ASSETS:
            raise PieceError(f'a piece can carry at most {MAX_ASSETS} assets', 409)
        if asset_id and any(a['id'] == asset_id for a in assets):
            raise PieceError('an asset with that id already exists', 409)
        assets.append({'id': asset_id or _desk._new_id('asset'), 'kind': kind,
                       'title': _asset_title(real, title), 'path': rel, 'added_at': now_iso()})
        piece['updated_at'] = now_iso()
        _desk._write_store(store)
        return v1_piece(piece)


def remove_asset(piece_id: str, asset_id: str) -> dict:
    """Detach an asset. The FILE stays: it is library material another piece may
    use, and deleting user media on an Undo is not what Undo means."""
    with _desk._store_lock:
        store = _desk._read_store()
        piece = _find(store, piece_id)
        assets = piece.get('assets') or []
        keep = [a for a in assets if a.get('id') != asset_id]
        if len(keep) == len(assets):
            raise PieceError('asset not found', 404)
        piece['assets'] = keep
        piece['updated_at'] = now_iso()
        _desk._write_store(store)
        return v1_piece(piece)


def _write_library_file(filename: str, stream, folder_name: str) -> tuple[Path, str]:
    """Write `stream` into the material library under `<kind>/<folder_name>/`
    and return `(dest, kind)`. Validates the type and the size while writing,
    and removes the partial file on any failure, so a refused write leaves
    nothing behind."""
    kind = asset_kind_for(filename)
    if kind is None:
        raise PieceError('only image (png, jpg, gif, webp) and video (mp4, mov, webm, m4v) files can be uploaded')
    stem, ext = os.path.splitext(Path(filename).name)
    safe = _SAFE_NAME.sub('_', stem).strip(' ._')[:60] or 'upload'
    folder = _uploads_root().joinpath(*LIBRARY_ROOT, kind, folder_name)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f'{safe}-{uuid.uuid4().hex[:8]}{ext.lower()}'
    cap = MAX_UPLOAD_BYTES[kind]
    written = 0
    try:
        with open(dest, 'wb') as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > cap:
                    raise PieceError(f'that {kind} is over the {cap // (1024 * 1024)} MB limit', 413)
                out.write(chunk)
        if written == 0:
            raise PieceError('that file is empty')
    except BaseException:
        try:
            dest.unlink()
        except OSError:
            pass
        raise
    return dest, kind


def save_upload(piece_id: str, filename: str, stream, *, title=None, asset_id: str | None = None) -> dict:
    """Write an uploaded file into the material library's Uploads folder and
    attach it. Validates the piece, the type and the size BEFORE keeping a byte,
    and removes the file if attaching then fails, so a refused upload leaves
    nothing behind."""
    if asset_kind_for(filename) is None:
        raise PieceError('only image (png, jpg, gif, webp) and video (mp4, mov, webm, m4v) files can be uploaded')
    with _desk._store_lock:
        _find(_desk._read_store(), piece_id)
    dest, _kind = _write_library_file(filename, stream, UPLOAD_FOLDER)
    try:
        return add_asset(piece_id, path=_rel_of(dest.resolve()), title=title or Path(filename).name,
                         asset_id=asset_id)
    except BaseException:
        try:
            dest.unlink()
        except OSError:
            pass
        raise


def save_to_library(filename: str, stream, *, title=None) -> dict:
    """Studio's save (MC-1024): write a file Studio made into the material
    library's Studio folder, attached to NO piece and no campaign. Returns the
    library item `{id, kind, title, path, src}`, the same shape `materials()`
    lists, so a What source picker can offer it at once."""
    dest, kind = _write_library_file(filename, stream, STUDIO_FOLDER)
    real = dest.resolve()
    rel = _rel_of(real)
    return {'id': rel, 'kind': kind, 'title': _asset_title(real, title), 'path': rel, 'src': _src_for(rel, kind)}


# -- materials (plan M22) ----------------------------------------------------------

def _library_items(kind: str, folder: Path, rel_root: Path) -> list[dict]:
    items = []
    exts = IMAGE_EXTS if kind == 'image' else VIDEO_EXTS
    try:
        files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in exts]
    except OSError as e:
        _log(f'[desk] material folder unreadable {folder.name}: {e}', flush=True)
        return items
    files.sort(key=lambda f: (-f.stat().st_mtime, f.name))
    for f in files:
        rel = f.resolve().relative_to(rel_root).as_posix()
        items.append({'id': rel, 'kind': kind, 'title': f.name, 'path': rel, 'src': _src_for(rel, kind)})
    return items


def materials(campaign_id: str | None = None) -> dict:
    """`{library:{video[],image[]}, articles[], online:{video[],image[]}, recent[]}`.

    LIBRARY: every folder under `data/uploads/desk/library/<video|image>/` is a
    folder, each file in it an `items[]` entry (so the UI can offer the FILES, not
    just the folder). `articles`: article pieces in the Desk, other than the
    campaign being worked on, as something an article piece can point at. `online`
    stays empty: no drive connector exists, and an empty list is the honest
    answer, not a stand-in. `recent`: the newest library files, for Home's shelf."""
    root = _uploads_root() if UPLOADS_ROOT is not None else None
    library: dict = {'video': [], 'image': []}
    recent: list[tuple[float, dict]] = []
    if root is not None:
        base = root.joinpath(*LIBRARY_ROOT)
        for kind in ('video', 'image'):
            kdir = base / kind
            try:
                folders = sorted(d for d in kdir.iterdir() if d.is_dir()) if kdir.is_dir() else []
            except OSError as e:
                _log(f'[desk] material library unreadable ({kind}): {e}', flush=True)
                folders = []
            for d in folders:
                items = _library_items(kind, d, root)
                library[kind].append({
                    'id': f'{kind}:{d.name}', 'title': d.name, 'files': len(items),
                    'thumb': next((i['src'] for i in items if i['src']), None), 'items': items})
                for i in items:
                    try:
                        recent.append(((root / i['path']).stat().st_mtime,
                                       {'id': i['id'], 'kind': kind, 'title': i['title']}))
                    except OSError:
                        pass
    recent.sort(key=lambda t: -t[0])
    with _desk._store_lock:
        store = _desk._read_store()
    articles = [{'id': p['id'], 'title': p.get('title') or '', 'words': p.get('word_count') or 0,
                 'projectId': p.get('project_id')}
                for p in sorted(_pieces(store).values(), key=lambda p: (p.get('created_at') or '', p['id']))
                if p.get('kind') == 'article' and p.get('campaign_id') != campaign_id]
    return {'library': library, 'articles': articles, 'online': {'video': [], 'image': []},
            'recent': [r for _, r in recent[:8]]}
