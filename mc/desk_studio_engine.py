"""A standalone Studio item's own engine pick (Ron 2026-11-18).

A Studio video or article with no campaign carries its own `engine = {provider?,
model?, effort?}` beside its agent. An item that belongs to a campaign runs on the
CAMPAIGN's pick instead (`how.engine`, `mc/desk_campaign_engine.py`), whether or not
that campaign has one: the item's own pick is not consulted, so the page can show the
campaign's pick read-only and what it shows is what runs.

This file adds no second validator. `check_save` is `desk_campaign_engine.validate`
(400 on a pick the live catalog does not offer) and `apply` is
`desk_campaign_engine.apply_pick` (409 when the pick cannot run, never a fallback to
another engine); both come with the wording for an item instead of a campaign.

WHERE THE PICK LIVES. An article keeps it on its server record (`put_article` reads
`engine`, so it survives a reload). A standalone video is held by the page like its
agent is (`item.agent`), and the page sends it with each storyboard call (`engine`
beside `agent`); the call validates it the moment it uses it.
"""
from __future__ import annotations

from typing import Optional

from mc import desk_campaign_engine as _ce
from mc.core import _log
from mc.desk_pieces import PieceError


def pick_of(item: Optional[dict]) -> Optional[dict]:
    """The item's stored pick, or None for "agent default"."""
    return _ce.stored_pick((item or {}).get('engine'))


def agent_ref_of_project(project_id: Optional[str]) -> Optional[str]:
    """The project's Desk agent: who an article conversation runs as."""
    if not project_id:
        return None
    from mc import desk
    return (desk.get_presence(project_id) or {}).get('desk_agent') or None


def agent_provider(project: Optional[dict], ref: Optional[str]) -> str:
    """The provider the item's agent runs on (the default engine's when there is no
    agent), which an effort-only pick is checked against; '' when it cannot be read."""
    from mc import desk_story
    try:
        rec = desk_story._agent(ref, project) if ref else None
        return _ce._agent_provider(project, (rec or {}).get('engine'))
    except Exception as e:   # a missing agent is refused by the call itself; here it only means "unknown"
        _log(f'[desk] studio engine pick: could not read the provider of agent {ref}: {e}', flush=True)
        return ''


def check_save(raw, project: Optional[dict], agent_ref: Optional[str], stored: Optional[dict] = None) -> Optional[dict]:
    """The item's pick, validated and normalised (None: agent default), or EngineError
    (400). A pick equal to the one already stored is not re-checked: an article is
    re-saved on every keystroke with its pick, and a catalog that moved on since must
    not make the draft unsaveable. It is refused when used instead."""
    pick = _ce.stored_pick(raw)
    if pick is not None and pick == stored:
        return stored
    effort_only = isinstance(raw, dict) and raw.get('effort') and not raw.get('provider')
    return _ce.validate(raw, agent_provider=agent_provider(project, agent_ref) if effort_only else '')


def apply(camp: Optional[dict], item_pick: Optional[dict], project: Optional[dict], rec: Optional[dict],
          default_engine, *, available: bool = True):
    """(engine, effort) for one Studio model call. `camp` is the campaign the item
    belongs to (its pick rules, even when it has none); with no campaign the item's own
    pick is used. EngineError (409) when the pick cannot run: nothing is called."""
    if camp is not None:
        return _ce.apply(camp, project, rec, default_engine, available=available)
    return _ce.apply_pick(item_pick, project, rec, default_engine, available=available, whose=_ce.ITEM)


def owner_campaign(owner) -> Optional[dict]:
    """The campaign a storyboard owner `{kind, id}` belongs to: a piece's campaign.
    None when no owner was sent, for a standalone Studio item, and for a piece that
    has no campaign. PieceError (400) for an owner that is not `{kind, id}`."""
    if owner is None:
        return None
    if not isinstance(owner, dict) or owner.get('kind') not in ('studio', 'piece'):
        raise PieceError('owner must be {kind, id}', 400)
    if owner['kind'] != 'piece':
        return None
    from mc import desk, desk_storyboard
    oid = desk_storyboard._check_owner_id(owner.get('id'))
    with desk._store_lock:
        return _ce.for_piece(desk._read_store(), oid)


def article_campaign(store: dict, art: dict) -> Optional[dict]:
    """The campaign a Studio article is for or in (`campaign_id`), or None."""
    cid = (art or {}).get('campaign_id')
    camps = store.get('campaigns')
    camp = camps.get(cid) if isinstance(camps, dict) and cid else None
    return camp if isinstance(camp, dict) else None
