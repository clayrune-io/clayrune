"""The tracked-link insertion seam for a Desk version (MC-1152 P2, DARK).

Spec: `docs/desk_v1/CHANNEL_IMPACT_SPEC.md` sections 6 and 12 (P2). This is the
one place a Desk-generated link enters a post body, and it only runs when a
human asks for it for one version and one destination (`insert`), or asks what
that would look like (`preview`). Nothing rewrites a pasted URL, a historic
draft, a released payload or a receipt.

DARK. `enabled()` reads the config key `desk_impact_links_enabled`, default
False, and the routes in `mc/blueprints/desk_impact_routes.py` answer 404 while
it is off. No route, scheduler or startup path calls this module otherwise.

PREVIEW == PAYLOAD. `preview` and `insert` run the same `_plan`, so the body
`preview` returns is the body `insert` stores, and the publisher later receives
exactly `desk_pieces._effective_body` of it (the same function `desk_tick`
hands to `desk_publish.publish` and to the manual publishing task). The
approval hash covers that body, so a link inserted after approval would be
caught: `desk_pieces.update_version` refuses a body change on an approved or
sent version, and `_plan` refuses first with the same reason.

WHAT INSERT WRITES, in order: one manifest row (idempotent -- the same version
and destination always yield the same row), then the version's body. If the body
write is refused, the row stays; it is unreferenced and the next attempt reuses
it. A version with no body of its own (it shows the post's shared body) gets a
copy of that body plus the link; the shared body is not touched, and later edits
to it no longer reach that version. Inserting a link already in the body again
changes nothing.
"""
from __future__ import annotations

from typing import Any

from mc import desk as _desk
from mc import desk_pieces as _pieces
from mc import state
from mc.desk_impact_links import LinkRefused, TrackedLinks
from mc.desk_impact_store import desk_campaign_resolver

CONFIG_KEY = 'desk_impact_links_enabled'


def enabled() -> bool:
    """True only when the operator has switched the feature on. Default off."""
    return (state.CONFIG or {}).get(CONFIG_KEY, False) is True


class InsertRefused(Exception):
    """A tracked link cannot be inserted here. `status` is the HTTP status the
    route answers; `code` is a `LinkRefused` code or one of `locked`,
    `archived`, `no_campaign`."""

    def __init__(self, status: int, code: str, message: str, keys: tuple[str, ...] = ()):
        super().__init__(message)
        self.status = status
        self.code = code
        self.keys = tuple(keys)


def _plan(links: TrackedLinks, piece_id: Any, version_id: Any, destination: Any,
          *, write: bool) -> dict:
    piece = _pieces.get_stored_piece(piece_id)                  # PieceError 404
    ver = next((v for v in piece.get('versions') or [] if v.get('id') == version_id), None)
    if ver is None:
        raise _pieces.PieceError('version not found', 404)
    if piece.get('archive'):
        raise InsertRefused(409, 'archived', 'restore this archived post before editing it')
    state_now = ver.get('state')
    if state_now in _pieces.APPROVED_STATES + _pieces.SENT_STATES:
        raise InsertRefused(409, 'locked', f'this version is {state_now}: send it back to review '
                            'before adding a link; an approved or sent post is never edited')
    camp = desk_campaign_resolver(piece.get('campaign_id') or '')
    if not camp or not camp.get('project_id'):
        raise InsertRefused(404, 'no_campaign', 'this post has no campaign to track against')
    with _desk._store_lock:
        account = (_desk._read_store().get('accounts') or {}).get(ver.get('account_id')) or {}
    args = dict(account_id=ver.get('account_id'), platform=account.get('platform'),
                post_id=piece_id, version_id=version_id, destination=destination)
    try:
        res = (links.issue if write else links.preview)(camp['project_id'], camp['id'], **args)
    except LinkRefused as e:
        raise InsertRefused(422, e.code, str(e), e.keys) from e
    url = res['url']
    raw = ver.get('body') or piece.get('body') or ''
    present = url in raw
    # The store strips a body (`desk_pieces._clean_text`), so strip here and the
    # preview is byte-for-byte what is saved.
    after = (raw if present else raw + ('' if not raw or raw[-1].isspace() else ' ') + url).strip()
    return {'link': res['link'], 'url': url, 'issued': res['issued'],
            'coverage_note': res['coverage_note'], 'already_present': present,
            'body_after': after, 'prepared_payload': after,
            'version_id': version_id, 'post_id': piece_id}


def preview(links: TrackedLinks, piece_id: Any, version_id: Any, destination: Any) -> dict:
    """What `insert` would put in the body. Writes nothing."""
    return _plan(links, piece_id, version_id, destination, write=False)


def insert(links: TrackedLinks, piece_id: Any, version_id: Any, destination: Any) -> dict:
    """Issue the link and append it to the version's body. Raises `InsertRefused`
    or `desk_pieces.PieceError` (which carries its own `status`)."""
    out = _plan(links, piece_id, version_id, destination, write=True)
    if not out['already_present']:
        _pieces.update_version(piece_id, version_id, {'body': out['body_after']})
    return out
