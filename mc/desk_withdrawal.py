"""A post the person withdrew stays withdrawn (MC-1187 DK1).

Deleting, archiving or pausing is the person saying "stop". Undo of those must
never put a version back with the human approval stamp it had before: the stamp
says a human approved this text for this account AT THAT TIME, and the next
publish tick sends whatever carries it. `desk_piece_lifecycle.restore_deleted_locked`
(piece trash restore) already returns approved versions as `needs_review`; this
module is that rule for the other three ways back:

  * unarchive                          (desk_piece_lifecycle.archive, restoring)
  * campaign restore                   (desk_campaign_trash.restore)
  * PATCH state=running on a campaign the person paused, finished or dropped

Imports of `desk_pieces` are lazy: `desk_campaign_trash` is imported by `desk`,
which `desk_pieces` imports.
"""
from __future__ import annotations

from mc.core import _log

# Campaign states the person left a campaign in on purpose. Resuming one of these
# is a human decision; `draft`/`proposed` are not withdrawals and are unchanged.
WITHDRAWN_CAMPAIGN_STATES = ('paused', 'done', 'dropped')


class ResumeRefused(ValueError):
    """An unattended caller asked to resume a withdrawn campaign (HTTP 403)."""


def withdraw_version(version: dict) -> bool:
    """Return an approved/scheduled version to review: approval stamp and slot are
    cleared by `desk_pieces._set_version_state`. True when it changed."""
    from mc import desk_pieces as pieces
    if version.get('state') in pieces.APPROVED_STATES:
        pieces._set_version_state(version, 'needs_review')
        return True
    return False


def withdraw_piece(piece: dict) -> int:
    """`withdraw_version` for every live version and every version held in the
    piece's archive snapshot (so a later unarchive cannot re-arm it either)."""
    n = sum(withdraw_version(v) for v in piece.get('versions') or [] if isinstance(v, dict))
    saved = (piece.get('archive') or {}).get('versions') or {}
    n += sum(withdraw_version(v) for v in saved.values() if isinstance(v, dict))
    return n


def check_resume(prev_state, patch: dict) -> None:
    """Raise ResumeRefused for a request that moves a paused, done or dropped
    campaign to `running`. The caller passes this only for an unattended caller."""
    if (patch or {}).get('state') == 'running' and prev_state in WITHDRAWN_CAMPAIGN_STATES:
        _log(f'[desk] unattended resume of a {prev_state} campaign refused')
        raise ResumeRefused(f'this campaign is {prev_state}; an unattended agent session '
                            'cannot resume it, only a person can')
