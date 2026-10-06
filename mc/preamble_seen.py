"""Which standing positions this conversation has already seen in full (MC-1057).

`memory_turn.refresh_for_turn` renders a standing position in FULL the first
time a session sees it and as a one-line reference afterwards (the compact
form, `memory_turn._render_position_compact`). That ledger lives on the live
session dict -- never in `data/projects/` (DATA_DIR rule, CLAUDE.md) -- so a
server restart, a respawn or a rollover (`reset`) starts it empty, and an empty
ledger means "show full". It can only err toward showing too much.

THE GAP THIS CLOSES: the dispatch-time read floor already puts the matching
positions, in full, into the system prompt (`_build_agent_context`). The
ledger did not know that, so the first live turn that matched the same
position rendered it in full a second time. `seed` records those hits so the
first live turn already treats them as seen.

A position the context has NOT seen in full is never marked seen here: only
hits the caller says the system prompt rendered are recorded, with the content
hash of the file they were rendered from, so a position edited afterwards
re-renders in full.

Leaf module: stdlib only, no mc.memory import (callers pass hashes in).
"""
from __future__ import annotations

# {file: {'hash': str, 'full_turn': int}} on the live session dict. The key is
# defined here and aliased by mc.memory_turn so the two cannot drift.
POS_STATE_KEY = '_mem_turn_pos_state'
# Turn counter key shared with memory_turn (one live-turn refresh = +1).
TURN_IDX_KEY = '_mem_turn_turn_index'


def mark_full(session, fname: str, digest: str, turn: int) -> None:
    """Record that `fname` was shown in full at `turn` with content `digest`."""
    session.setdefault(POS_STATE_KEY, {})[fname] = {'hash': digest, 'full_turn': turn}


def is_seen(session, fname: str, digest: str) -> bool:
    """True when `fname` was already shown in full with this exact content.
    An empty digest (unreadable file) is never "seen" -- fail toward full."""
    if not digest or not isinstance(session, dict):
        return False
    prev = (session.get(POS_STATE_KEY) or {}).get(fname)
    return bool(prev) and prev.get('hash') == digest


def seed(session, pos_hits, hash_of) -> int:
    """Mark the dispatch/revival-rendered position hits as seen in full.

    `hash_of(hit)` returns the content hash of the file the hit came from ('' on
    failure -- skipped, so the position still renders in full later). A hit
    already in the ledger is left alone. Returns how many were recorded.
    Never raises."""
    if not isinstance(session, dict):
        return 0
    added = 0
    try:
        for h in pos_hits:
            fname = str(h.get('file'))
            digest = hash_of(h)
            if not digest or fname in (session.get(POS_STATE_KEY) or {}):
                continue
            mark_full(session, fname, digest, 0)
            added += 1
    except Exception:
        pass
    return added


def forget(session) -> None:
    """Empty the ledger (respawn / rollover): the next render is full again."""
    if isinstance(session, dict):
        session.pop(POS_STATE_KEY, None)
