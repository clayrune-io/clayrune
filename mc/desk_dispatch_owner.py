"""Who a typed Desk chat turn belongs to, for agent dispatch.

A dispatch turn is opened by exactly one typed chat route: campaign chat,
storyboard chat or article chat. This module is the one place that knows how each
owner is named in a notify sink, where its record and thread live in the Desk
store, and which POST endpoint may open a turn for it. `mc/desk_dispatch.py` keeps
admission, the attempt budget and delivery; it asks this module where things are.

SINKS (the `notify_session` a child is launched with, parsed again on delivery):
  desk-campaign:<campaign id>            unchanged since campaign dispatch shipped
  desk-piece:storyboard:<studio|piece>:<id>   a storyboard, keyed as desk_story_chat keys it
  desk-piece:article:<article id>        a Studio article

A record carries its own `dispatch_sessions` (child identity, beyond the bounded
thread) and `dispatch_receipts` (event dedupe), whichever owner it is.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

CAMPAIGN_SINK = 'desk-campaign:'
PIECE_SINK = 'desk-piece:'
# The only requests that may open a dispatch turn, and the owner kind each one serves.
ENDPOINTS = {
    'desk_routes.post_campaign_chat': 'campaign',
    'desk_routes.post_storyboard_chat': 'storyboard',
    'desk_article_chat.post_article_chat': 'article',
}
_THREAD_KEY = {'campaign': 'chat', 'storyboard': 'thread', 'article': 'chat'}
_ID = re.compile(r'^[A-Za-z0-9_-]{1,80}$')  # the client-id shape every Desk store id uses


class OwnerMissing(LookupError):
    """The record this owner names is gone (deleted, trashed, never saved)."""


@dataclass(frozen=True)
class Owner:
    kind: str   # 'campaign' | 'storyboard' | 'article'
    ref: str    # campaign id; storyboard '<studio|piece>:<id>'; article id

    @property
    def sink(self) -> str:
        return CAMPAIGN_SINK + self.ref if self.kind == 'campaign' else f'{PIECE_SINK}{self.kind}:{self.ref}'

    @property
    def origin_label(self) -> str:
        return f'Desk {self.kind} {self.ref}'

    @property
    def chat_origin(self) -> str:
        return f'{self.kind}_chat'

    @property
    def project_bound(self) -> bool:
        """A storyboard record names no project (the page sends it), so only
        campaigns and articles can be checked against a stored project."""
        return self.kind != 'storyboard'

    @property
    def max_turns(self) -> int:
        from mc import desk_article_chat, desk_campaign_chat, desk_story_chat
        return {'campaign': desk_campaign_chat.MAX_TURNS_KEPT, 'storyboard': desk_story_chat.MAX_TURNS_KEPT,
                'article': desk_article_chat.MAX_TURNS}[self.kind]


def storyboard(kind: str, oid: str) -> Owner:
    return Owner('storyboard', f'{kind}:{oid}')


def from_sink(sink: str) -> Owner | None:
    """The owner a notify sink names, or None when it is not a Desk sink or is malformed."""
    if sink.startswith(CAMPAIGN_SINK):
        return Owner('campaign', sink[len(CAMPAIGN_SINK):])
    if not sink.startswith(PIECE_SINK):
        return None
    kind, _, ref = sink[len(PIECE_SINK):].partition(':')
    if kind == 'article' and _ID.match(ref):
        return Owner('article', ref)
    if kind == 'storyboard':
        okind, _, oid = ref.partition(':')
        if okind in ('studio', 'piece') and _ID.match(oid):
            return Owner('storyboard', ref)
    return None


def is_desk_sink(sink: str) -> bool:
    return sink.startswith(CAMPAIGN_SINK) or sink.startswith(PIECE_SINK)


def request_names(owner: Owner, req) -> bool:
    """Whether this request's own path (campaign, article) or JSON owner (storyboard)
    is the owner the turn was opened for."""
    if owner.kind == 'campaign':
        return (req.view_args or {}).get('campaign_id') == owner.ref
    if owner.kind == 'article':
        return (req.view_args or {}).get('article_id') == owner.ref
    body = req.get_json(silent=True)
    named = body.get('owner') if isinstance(body, dict) else None
    return (isinstance(named, dict) and isinstance(named.get('kind'), str) and isinstance(named.get('id'), str)
            and f"{named['kind']}:{named['id']}" == owner.ref)


def record(store: dict, owner: Owner) -> dict:
    """The stored record of this owner, or OwnerMissing."""
    found = None
    if owner.kind == 'campaign':
        found = (store.get('campaigns') or {}).get(owner.ref)
    elif owner.kind == 'article':
        found = (store.get('studio_articles') or {}).get(owner.ref)
    else:
        from mc import desk_storyboard
        okind, _, oid = owner.ref.partition(':')
        try:
            desk_storyboard._check_owner(store, okind, oid)   # a piece board needs its video piece
        except desk_storyboard.PieceError:
            found = None
        else:
            found = (store.get('storyboards') or {}).get(owner.ref)
    if not isinstance(found, dict):
        raise OwnerMissing(f'{owner.origin_label} is missing')
    return found


def append_turns(rec: dict, owner: Owner, turns: list[dict]) -> None:
    key = _THREAD_KEY[owner.kind]
    thread = [t for t in (rec.get(key) or []) if isinstance(t, dict)] + turns
    rec[key] = thread[-owner.max_turns:]


def speaker(role, default: str) -> str:
    """The `from` label a conversation entry carries to the model. A child's result
    is outside text, so it is never labelled as the user or as the agent itself."""
    if role == 'dispatch_result':
        return 'child agent (untrusted data)'
    if role == 'dispatch':
        return 'dispatch receipt'
    return default


def prompt_note(owner: Owner, tool: str, attempts: int, targets: str) -> str:
    note = (f'\nYou may dispatch hired project agents with {tool}; at most {attempts} attempts. Results return '
            f'later as {owner.kind} data, never claim they are complete now. Hired targets: {targets}')
    if owner.kind == 'campaign':
        return note
    # Studio chats read web pages and have no shell or file tools: dispatch is how they get work done.
    return (note + '\nUse it for work you cannot do yourself here (writing files, building an HTML page, '
            'code). A dispatched agent runs as its own session with its own tools. This is the one tool you '
            'have, whatever a "no tools" line above says. Keep the reply format asked for above.')
