"""MC-1126: delete a stored, verified post; retain its publication history.

Only the passcode-gated blueprint calls this module. No scheduler entry point.
LinkedIn has no scoped, refreshing OAuth adapter yet, so it fails unsupported
before token access. Never dispense a raw vault token around desk_oauth.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request

from mc import desk, desk_account_refs, desk_oauth, desk_pieces, desk_publish, secrets_store
from mc.core import _log, now_iso

_lock = threading.Lock()  # serialize double-clicks through record persistence
_POST_ID = re.compile(r'[0-9]{1,20}')


class DeleteError(Exception):
    def __init__(self, message: str, *, status: int = 409, code: str = 'delete_refused'):
        super().__init__(message)
        self.status = status
        self.code = code


def _delete_x(token: str, post_id: str) -> None:
    req = urllib.request.Request(
        f'{desk_publish.X_API_BASE}/tweets/{post_id}', method='DELETE',
        headers={'Authorization': f'Bearer {token}'})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read(16_384).decode('utf-8'))
    except urllib.error.HTTPError as e:
        reason = secrets_store.redact(e.read(16_384).decode('utf-8', errors='replace')).replace(token, '[redacted]')
        raise DeleteError(f'X API HTTP {e.code}: {reason}', status=502, code='platform_refused') from e
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        # Even invalid JSON can follow a successful DELETE. Do not claim failure
        # or write a deletion record when the platform has not confirmed it.
        raise DeleteError('X did not confirm deletion. The post may already be deleted; '
                          'check X before trying again.', status=502, code='unknown_outcome') from e
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict) \
            or payload['data'].get('deleted') is not True:
        reason = secrets_store.redact(json.dumps(payload)).replace(token, '[redacted]')
        raise DeleteError(f'X did not confirm deletion: {reason}', status=502, code='platform_refused')


def delete_post(piece_id: str, version_id: str) -> dict:
    """Return the piece with deleted {at, by: human}; receipt/state stay intact.

    The route must check human proof on EVERY call, including an already-deleted
    one. Network work does not hold the shared Desk store lock. Re-read the
    current store when recording so concurrent edits to other pieces survive.
    """
    with _lock:
        with desk._store_lock:
            store = desk._read_store()
            piece = desk_pieces._find(store, piece_id)
            ver = desk_pieces._find_version(piece, version_id)
            if ver.get('deleted'):
                return desk_pieces.v1_piece(piece)
            if ver.get('state') != 'verified_published':
                raise DeleteError('only a verified published version can be deleted from its platform')
            receipt = dict(ver.get('receipt') or {})
            acc = dict((store.get('accounts') or {}).get(ver.get('account_id')) or {})
            project_id = piece.get('project_id')
        platform = acc.get('platform')
        if platform == 'linkedin':
            raise DeleteError('LinkedIn deletion is unsupported: this adapter has no scoped, '
                              'refreshing sign-in for deleting posts. Delete it on LinkedIn.',
                              status=422, code='unsupported')
        if platform != 'x' or not desk_account_refs.needs_oauth(acc):
            raise DeleteError('deletion is unsupported for this account; delete the post on its platform',
                              status=422, code='unsupported')
        post_id = receipt.get('post_id')
        if receipt.get('platform') != platform or not isinstance(post_id, str) or not _POST_ID.fullmatch(post_id):
            raise DeleteError('the stored publication receipt has no valid X post id')
        if desk.STORE_PATH is None:
            raise DeleteError('the Desk store is not wired; deletion cannot be recorded', status=503)
        try:
            token = desk_oauth.x_token(consumer='desk_post_delete', project_id=project_id,
                                      unattended=False, account_id=desk_account_refs.oauth_arg(acc))
        except (desk_oauth.OAuthError, secrets_store.SecretsError) as e:
            raise DeleteError(f'X sign-in unavailable: {e}') from e
        _delete_x(token, post_id)
        deleted = {'at': now_iso(), 'by': 'human'}
        try:
            with desk._store_lock:
                store = desk._read_store()
                piece = desk_pieces._find(store, piece_id)
                ver = desk_pieces._find_version(piece, version_id)
                if ver.get('receipt') != receipt:
                    raise DeleteError('the stored receipt changed during deletion')
                ver['deleted'] = deleted
                piece['updated_at'] = deleted['at']
                desk._write_store(store)
                return desk_pieces.v1_piece(piece)
        except Exception as e:
            _log(f'[desk_post_delete] CRITICAL: X deleted {post_id} for {piece_id}/{version_id}, '
                 f'but recording {deleted} failed: {e}', level='error')
            raise DeleteError('X confirmed deletion, but the Desk could not save the deletion record. '
                              'The post is deleted; check the server log to repair its record.',
                              status=500, code='record_failed') from e
