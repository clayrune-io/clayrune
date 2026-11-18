"""Ordinary timing waits; it does not hold (MC-1179 OS33).

A reviewed integration is sticky once held (the product has no automatic
reconciliation), so a hold must mean "a person has to look". Two things that
are only a matter of a few seconds used to produce one:

  * the last worker marks its workstream complete with a curl while its turn is
    still running, so the first tick finds a live session;
  * the landing lock is held by someone else for longer than `landing()` waits.

Both now park the attempt as `running` with a `waiting_since` stamp and let the
driver's next tick try again. The wait is bounded by `GRACE_SECONDS`; after
that it is a hold again, because a worker that never ends is not timing.

Only a tick with nothing in flight may wait. A candidate that is already merged,
smoked or under review has pinned git state, and a contributor becoming active
under it is drift, which holds as before.
"""
from __future__ import annotations

from datetime import datetime, timezone

from mc.core import now_iso
from mc.merge_review_gate import ReviewRefused

GRACE_SECONDS = 900


class NotYet(ReviewRefused):
    """A contributing session is still in its turn; try again shortly."""

    def __init__(self, message: str):
        super().__init__(409, message)


def _elapsed(since: str) -> float:
    try:
        began = datetime.fromisoformat(since.replace('Z', '+00:00'))
        return (datetime.now(timezone.utc) - began).total_seconds()
    except (AttributeError, ValueError):
        return GRACE_SECONDS + 1   # an unreadable stamp must not wait for ever


def may_wait(st: dict) -> bool:
    """True while nothing is merged or pinned for the current workstream."""
    return st.get('current') is None


def park(st: dict, reason: str) -> bool:
    """Record a wait on `st`. True: keep waiting. False: the grace is spent."""
    since = st.get('waiting_since')
    if since is None:
        st['waiting_since'] = now_iso()
        st['waiting_reason'] = reason
        return True
    st['waiting_reason'] = reason
    return _elapsed(since) <= GRACE_SECONDS


def resume(st: dict) -> None:
    """The tick got past the wait; drop its marker (saved with the next write)."""
    st.pop('waiting_since', None)
    st.pop('waiting_reason', None)
