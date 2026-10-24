"""Whole-post archive and reversible removal, using Desk's existing records/trash."""
from __future__ import annotations

from copy import deepcopy
import uuid
import json
import time

from mc import desk, desk_pieces as pieces
from mc.core import now_iso, _log


def archive(piece_id: str, restoring: bool = False) -> dict:
    with desk._store_lock:
        store = desk._read_store()
        piece = pieces._find(store, piece_id)
        saved = piece.get('archive')
        if restoring:
            if saved:
                # Only records actually suspended by archive are restored. A sent
                # version may have gained a receipt meanwhile; keep that history.
                before = saved['versions']
                for version in piece.get('versions') or []:
                    if version['id'] in before:
                        if version.get('state') != 'archived':
                            raise pieces.PieceError('an archived version changed; reload before restoring', 409)
                        original = deepcopy(before[version['id']])
                        version.clear()
                        version.update(original)
                piece.pop('archive', None)
        elif not saved:
            before = {}
            for version in piece.get('versions') or []:
                if version.get('state') not in pieces.SENT_STATES + ('archived',):
                    before[version['id']] = deepcopy(version)
                    pieces._set_version_state(version, 'archived')
            piece['archive'] = {'at': now_iso(), 'versions': before}
        piece['updated_at'] = now_iso()
        desk._write_store(store)
        return pieces.v1_piece(piece)


def trash(piece_id: str) -> dict:
    from mc import desk_studio_items as items
    with desk._store_lock:
        store = desk._read_store()
        piece = pieces._find(store, piece_id)
        # The existing delete path owns the sent-history refusal and cascade.
        pieces._check_delete(piece)
        token = uuid.uuid4().hex
        board = (store.get('storyboards') or {}).get(f'piece:{piece_id}')
        items._stash(token, {'token': token, 'kind': 'piece', 'id': piece_id,
                            'piece': piece, 'board': board, 'deleted_at': now_iso()}, [])
        pieces._delete_piece_locked(store, piece_id)
        desk._write_store(store)
        return {'token': token}


def restore_deleted_locked(store: dict, manifest: dict) -> dict:
    """Called by the EXISTING Studio token restore, with Desk lock held."""
    piece = deepcopy(manifest['piece'])
    pieces._campaign_of(store, piece['campaign_id'])
    if piece['id'] in pieces._pieces(store):
        raise pieces.PieceError('a piece with that id exists; the deleted one was not restored', 409)
    # Deletion withdraws approval. Undo must never grant a new human approval.
    for version in piece.get('versions') or []:
        if version.get('state') in pieces.APPROVED_STATES:
            pieces._set_version_state(version, 'needs_review')
    if piece.get('archive'):
        for version in piece['archive']['versions'].values():
            if version.get('state') in pieces.APPROVED_STATES:
                pieces._set_version_state(version, 'needs_review')
    pieces._pieces(store)[piece['id']] = piece
    if manifest.get('board') is not None:
        key = f"piece:{piece['id']}"
        if key in (store.get('storyboards') or {}):
            raise pieces.PieceError('that storyboard id already exists', 409)
        store.setdefault('storyboards', {})[key] = deepcopy(manifest['board'])
    return pieces.v1_piece(piece)


def trash_references() -> dict:
    """Keep library files used by an undoable post available for its restore."""
    from mc import desk_studio_items as items, desk_article_media
    if pieces.UPLOADS_ROOT is None:
        return {}
    root = items._trash_root()
    if not root.is_dir():
        return {}
    out = {}
    try:
        for entry in root.iterdir():
            if not entry.is_dir() or entry.stat().st_mtime < time.time() - items.TRASH_KEEP_DAYS * 86400:
                continue
            manifest_path = entry / 'manifest.json'
            if not manifest_path.is_file():
                continue
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            if manifest.get('kind') != 'piece':
                continue
            piece = manifest['piece']
            paths = [a.get('path') for a in piece.get('assets') or []]
            paths += desk_article_media.paths(piece.get('body'))
            for version in piece.get('versions') or []:
                paths += desk_article_media.paths(version.get('body'))
            for scene in (manifest.get('board') or {}).get('scenes') or []:
                paths.append((scene.get('picture') or {}).get('path'))
            for path in paths:
                if path:
                    out[path] = {'piece_id': piece['id'], 'campaign_id': piece['campaign_id'],
                                 'campaign_title': 'a deleted post that can still be restored'}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        _log(f'[desk] deleted-post media reference check failed: {exc}', flush=True)
        raise pieces.PieceError('could not check deleted-post media references; try again later', 503) from exc
    return out
