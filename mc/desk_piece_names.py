"""Serial default names for unnamed Desk pieces (Ron, 2026-10-31).

A piece the user never named is called `Video1`, `Video2`, `Post1`, ... (kind
capitalised, no space, the number counting up within ONE campaign and ONE kind),
not "New video": a card with that title reads as the New video command rather than
as a piece that exists. The server owns the name (`desk_pieces.create_piece`), so
every client shows the same one.

Pure functions over the plain `store['pieces']` dict: no store I/O and no import
of `mc.desk` (which imports `desk_pieces`), so `desk._migrate_store` can call
`backfill` without a cycle. Callers hold `desk._store_lock`.

The next number is one above the highest serial still in the campaign, so deleting
`Video2` while `Video3` exists never re-issues 2. The scan also counts each piece's
`default_title`, so a piece renamed away from `Video3` still holds that number and
can be restored to it (the rename box cleared) without colliding with a newer piece.
"""

from __future__ import annotations

import re

# The title the client used to send for an unnamed piece (`New <kind>`), plus the
# kind-less form `_newTitle` produced when it did not know the kind.
_PLACEHOLDER = re.compile(r'^new (post|article|video|image|piece)$', re.IGNORECASE)


def serial_prefix(kind: str) -> str:
    return str(kind or 'piece').strip().capitalize()


def _serial_re(kind: str) -> re.Pattern:
    return re.compile(r'^' + re.escape(serial_prefix(kind)) + r'(\d+)$', re.IGNORECASE)


def is_placeholder(title, kind: str) -> bool:
    """True for a blank title or the old `New <kind>` / `New piece` placeholder.
    `New <other kind>` on a piece of this kind is a name the user typed."""
    t = str(title or '').strip()
    if not t:
        return True
    m = _PLACEHOLDER.match(t)
    return bool(m) and m.group(1).lower() in (str(kind or '').lower(), 'piece')


def next_serial(pieces: dict, campaign_id, kind: str) -> str:
    """`<Kind><n>` with n one above the highest serial of `kind` in `campaign_id`."""
    pat = _serial_re(kind)
    top = 0
    for p in pieces.values():
        if p.get('campaign_id') != campaign_id or p.get('kind') != kind:
            continue
        for name in (p.get('title'), p.get('default_title')):
            m = pat.match(str(name or '').strip())
            if m:
                top = max(top, int(m.group(1)))
    return f'{serial_prefix(kind)}{top + 1}'


def backfill(store: dict) -> int:
    """Give every stored piece still carrying a placeholder title its serial name.

    Numbered per campaign per kind in `created_at` order (id breaks a tie), each
    continuing after the highest serial already in that group. IDEMPOTENT, and run
    on every read like the other store migrations: once a piece has a serial it is
    no longer a placeholder, so a second run changes nothing, and the order is
    deterministic so two reads of one unpersisted store agree. Returns the number
    of pieces renamed."""
    pieces = (store.get('pieces') or {})
    todo = sorted((p for p in pieces.values() if is_placeholder(p.get('title'), p.get('kind'))),
                  key=lambda p: (p.get('created_at') or '', p.get('id') or ''))
    for p in todo:
        name = next_serial(pieces, p.get('campaign_id'), p.get('kind'))
        p['title'] = name
        p['default_title'] = name
    return len(todo)
