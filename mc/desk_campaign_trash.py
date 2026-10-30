"""Deleted campaigns, kept long enough for the Desk's Undo to put them back.

Why this exists (Ron 2026-10-30, 'FL3 V2'): the Desk deleted a draft only in
the browser's memory and never called the server, so the next workspace load
brought it back. Deleting server-side fixes that, but the Desk promises an
Undo on every delete (desk-v1-store.js `write`), and re-POSTing the campaign
would drop what a create cannot carry: its pieces, their storyboards, the
agent chat, approvals. So `desk.delete_campaign` moves the whole record set
here instead of discarding it, and `restore` moves it back.

Both functions take the store dict the caller already holds under
`desk._store_lock`; neither reads or writes the file itself.

RETENTION. An entry is pruned on the next delete once it is older than
`KEEP_HOURS`. Long enough to outlast a session's Undo history (the Desk
header's Undo and Ctrl/Cmd+Z reach back further than the toast), short enough
that a deleted draft does not linger. A prune is logged, never silent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mc.core import _log, now_iso

KEEP_HOURS = 24
TRASH_KEY = 'campaign_trash'


def _parse(ts) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _prune(trash: dict) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=KEEP_HOURS)
    old = [cid for cid, e in trash.items()
           if not isinstance(e, dict) or (_parse(e.get('deleted_at')) or cutoff) <= cutoff]
    for cid in old:
        trash.pop(cid, None)
    if old:
        _log(f'[desk] campaign trash: pruned {len(old)} deleted campaign(s) older than {KEEP_HOURS}h: {old}')


def stash(store: dict, campaign_id: str) -> bool:
    """Moves the campaign, its pieces and their storyboards out of the live
    store into the trash. False when there is no such campaign."""
    campaigns = store.get('campaigns') or {}
    if campaign_id not in campaigns:
        return False
    trash = store.setdefault(TRASH_KEY, {})
    _prune(trash)
    camp = campaigns.pop(campaign_id)
    pieces = store.get('pieces') or {}
    boards = store.get('storyboards') or {}
    gone_pieces, gone_boards = {}, {}
    for pid in [k for k, p in pieces.items() if isinstance(p, dict) and p.get('campaign_id') == campaign_id]:
        gone_pieces[pid] = pieces.pop(pid)
        key = f'piece:{pid}'                       # mc/desk_storyboard.py owner key
        if key in boards:
            gone_boards[key] = boards.pop(key)
    trash[campaign_id] = {'campaign': camp, 'pieces': gone_pieces,
                          'storyboards': gone_boards, 'deleted_at': now_iso()}
    return True


class RestoreError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def restore(store: dict, campaign_id: str) -> dict:
    """Puts a deleted campaign back exactly as it was deleted. Returns the
    stored campaign record; raises RestoreError (404 nothing to restore, 409 a
    campaign or piece with that id exists again)."""
    trash = store.get(TRASH_KEY) or {}
    entry = trash.get(campaign_id)
    if not isinstance(entry, dict) or not isinstance(entry.get('campaign'), dict):
        raise RestoreError('that campaign is not in the trash (deleted more than '
                           f'{KEEP_HOURS} hours ago, or never deleted)', 404)
    campaigns = store.setdefault('campaigns', {})
    pieces = store.setdefault('pieces', {})
    if campaign_id in campaigns:
        raise RestoreError('a campaign with that id exists again', 409)
    clash = [pid for pid in (entry.get('pieces') or {}) if pid in pieces]
    if clash:
        raise RestoreError(f'pieces with these ids exist again: {clash}', 409)
    campaigns[campaign_id] = entry['campaign']
    pieces.update(entry.get('pieces') or {})
    if entry.get('storyboards'):
        store.setdefault('storyboards', {}).update(entry['storyboards'])
    trash.pop(campaign_id, None)
    return entry['campaign']
