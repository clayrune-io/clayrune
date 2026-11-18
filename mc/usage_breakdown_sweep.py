"""Which sessions can touch which allowance-sample interval (P44 / OS48).

`usage_breakdown_aggregate._eligible_intervals` used to test every
allowance-sample pair against every session's whole segment list. On the
live install that was ~29,000 pairs x ~1,400 sessions and took ~100 s per
Claude request; a session that ended a week before a pair was still scanned
for it. A pair is only ever affected by a session whose time bounds reach it,
so this module hands the caller, per pair, just those sessions.

Pure: takes the evidence tuples the aggregate already builds, never touches
the store, and never decides eligibility -- the caller still runs its full
per-session check on every session returned here. The bounds test is
deliberately a SUPERSET of "overlaps or is contained in the pair" (an
inclusive comparison on a session's whole extent), so a session this module
drops could never have counted, and the result is identical to scanning all
of them.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime
from typing import Optional, Sequence, TypeVar

# An evidence entry is `(session_id, scope_ok, turns, unmeasured)`, the shape
# `_eligible_intervals` builds; only `[2]` and `[3]` are read here.
Evidence = TypeVar('Evidence', bound=tuple)


class TurnIndex:
    """One session's measured turns, narrowed to the ones that can reach a
    pair. A resumed chat can own hundreds of turns, and scanning all of them
    for every pair it reaches was the second half of the cost. Turns come out
    of `_session_turns` chained in time order; when that holds (checked, not
    assumed) the turns that can overlap or be contained in [t_a, t_b] are one
    contiguous slice found by bisect. Any session whose turns are not in that
    order is simply scanned in full, as before."""

    __slots__ = ('_turns', '_starts', '_ends', '_ordered')

    def __init__(self, turns: Sequence[dict]):
        self._turns = turns
        self._starts = [t['start'] for t in turns]
        self._ends = [t['end'] for t in turns]
        n = len(turns)
        self._ordered = all(self._starts[i] <= self._ends[i] for i in range(n)) and all(
            self._starts[i] <= self._starts[i + 1] and self._ends[i] <= self._ends[i + 1]
            for i in range(n - 1))

    def reaching(self, t_a: datetime, t_b: datetime) -> Sequence[dict]:
        """Every turn that overlaps or is contained in [t_a, t_b] (a superset,
        in the original order); the rest could never have counted."""
        if not self._ordered:
            return self._turns
        return self._turns[bisect_left(self._ends, t_a):bisect_right(self._starts, t_b)]


def _extent(turns: Sequence[dict], unmeasured: Sequence[tuple]) -> tuple[Optional[datetime], Optional[datetime], bool]:
    """(earliest, latest, bounded) over every span the session owns.
    `earliest` None = reaches back without limit; `bounded` False = at least
    one span is still open at its end, so the session reaches forward without
    limit and is never retired."""
    lo: Optional[datetime] = None
    hi: Optional[datetime] = None
    has_lo = False
    unlimited_back = False
    bounded = True
    for t in turns:
        a, b = t['start'], t['end']
        lo_t, hi_t = (a, b) if a <= b else (b, a)  # a malformed span still lands inside the extent
        if not has_lo or lo_t < lo:  # type: ignore[operator]
            lo, has_lo = lo_t, True
        if hi is None or hi_t > hi:
            hi = hi_t
    for s, e in unmeasured:
        if s is None:
            unlimited_back = True
        if e is None:
            bounded = False
        for v in (s, e):
            if v is None:
                continue
            if not has_lo or v < lo:  # type: ignore[operator]
                lo, has_lo = v, True
            if hi is None or v > hi:
                hi = v
    return (None if unlimited_back else lo), hi, bounded


def sessions_by_interval(evidence: Sequence[Evidence],
                         windows: Sequence[tuple[datetime, datetime]]) -> list[list[Evidence]]:
    """For each (t_a, t_b) in `windows`, the evidence entries whose extent
    reaches [t_a, t_b], in the original `evidence` order. Linear in
    windows + sessions + (window, reaching-session) pairs: windows are swept
    in start order against sessions kept in start order, and a session is
    retired once its last span ended before the sweep's current window start
    (windows are visited by non-decreasing start, so it can never matter
    again). Does not assume `windows` arrives sorted."""
    n = len(windows)
    out: list[list[Evidence]] = [[] for _ in range(n)]
    if not n or not evidence:
        return out

    # (lo, original index, hi, bounded). lo None sorts first: reaches back forever.
    spans = []
    for idx, entry in enumerate(evidence):
        turns, unmeasured = entry[2], entry[3]
        lo, hi, bounded = _extent(turns, unmeasured)
        if lo is None and hi is None and bounded and not turns and not unmeasured:
            continue  # owns no span at all: the caller's own check would `continue` it
        spans.append((lo, idx, hi, bounded))
    spans.sort(key=lambda sp: (0, sp[1]) if sp[0] is None else (1, sp[0], sp[1]))

    order = sorted(range(n), key=lambda i: windows[i][0])
    admitted = 0
    active: list[tuple] = []  # (idx, lo, hi, bounded)
    latest_end: Optional[datetime] = None
    for wi in order:
        t_a, t_b = windows[wi]
        if latest_end is None or t_b > latest_end:
            latest_end = t_b
        # admit every session that starts at or before the furthest window end seen so far
        while admitted < len(spans) and (spans[admitted][0] is None or spans[admitted][0] <= latest_end):
            lo, idx, hi, bounded = spans[admitted]
            active.append((idx, lo, hi, bounded))
            admitted += 1
        keep = []
        hits: list[int] = []
        for entry in active:
            idx, lo, hi, bounded = entry
            if bounded and hi is not None and hi < t_a:
                continue  # ended before this window starts, and every later one starts no earlier
            keep.append(entry)
            if (lo is None or lo <= t_b) and (not bounded or hi is None or hi >= t_a):
                hits.append(idx)
        active = keep
        hits.sort()
        out[wi] = [evidence[i] for i in hits]
    return out
