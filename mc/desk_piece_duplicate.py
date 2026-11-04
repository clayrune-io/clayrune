"""C4: atomically copy saved post content into an unapproved draft.

Media remain library references. New attachment/version identities keep later
edits independent; no schedule, approval, outcome or claim verdict is inherited.
"""
from copy import deepcopy

from mc import desk, desk_pieces as pieces
from mc.core import now_iso


def duplicate_post(piece_id: str, campaign_id=None, *, new_id=None) -> dict:
    if new_id is not None and not (isinstance(new_id, str) and pieces._CLIENT_ID.fullmatch(new_id)):
        raise pieces.PieceError('piece id must be 1-80 letters, digits, - or _')
    with desk._store_lock:
        store = desk._read_store()
        source = pieces._find(store, piece_id)
        if source.get('kind') != 'post':
            raise pieces.PieceError('only posts can be duplicated in the composer')
        target_id = source['campaign_id'] if campaign_id is None else campaign_id
        target = pieces._campaign_of(store, target_id)
        if target.get('state') == 'dropped':
            raise pieces.PieceError('restore this campaign before adding a post', 409)
        all_pieces = pieces._pieces(store)
        if new_id is not None and new_id in all_pieces:
            raise pieces.PieceError('a piece with that id already exists', 409)
        now = now_iso()
        assets = []
        for asset in source.get('assets') or []:
            # Refuse the whole operation if a saved attachment is unavailable.
            real = pieces._resolve_under_uploads(asset.get('path'))
            kind = pieces.asset_kind_for(real.name)
            if kind is None:
                raise pieces.PieceError(pieces.MEDIA_FORMAT_ERROR)
            assets.append({
                'id': desk._new_id('asset'), 'kind': kind,
                'title': asset.get('title') or real.name,
                'path': pieces._rel_of(real), 'added_at': now,
                **({'provenance': deepcopy(asset['provenance'])} if asset.get('provenance') else {}),
            })
        bound = (target.get('plan') or {}).get('accounts') or []
        known = store.get('accounts') or {}
        versions, seen = [], set()
        for version in source.get('versions') or []:
            account = version.get('account_id')
            if (account not in bound or account not in known or account in seen
                    or version.get('state') in ('archived', 'skipped')):
                continue
            seen.add(account)
            versions.append(pieces._new_version(account, body=version.get('body') or '',
                                                fmt=version.get('format')))
        post = {
            'id': new_id or desk._new_id('piece'), 'campaign_id': target_id,
            'project_id': target.get('project_id'), 'kind': 'post',
            'title': (source.get('title') or 'New post')[:pieces.MAX_TITLE - 7] + ' (copy)',
            'body': source.get('body') or '', 'word_count': source.get('word_count'),
            'source': deepcopy(source.get('source')), 'claims': deepcopy(source.get('claims') or []),
            'assets': assets, 'versions': versions, 'draft_status': 'drafting',
            'created_at': now, 'updated_at': now,
        }
        all_pieces[post['id']] = post
        desk._write_store(store)
        return pieces.v1_piece(post)
