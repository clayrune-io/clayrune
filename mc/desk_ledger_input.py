"""What the story-ledger POST will accept (MC-1187 DK3).

The ledger's `cost` and `published_at` are read as facts by the budget
(`desk_engines`), the read budget and the cadence rules (`desk_engagement`,
`desk_tick`: min_gap_h, per_week, post cap) and the impact comparison
(`desk_impact_compare`). One row with a negative cost raised a remaining budget;
one dated year 9999 made `min_gap_h` refuse every send; a text cost raised in
every later poll; a non-UTC date made the impact comparison answer 400.

`problem(body)` returns the refusal text for a body the route must answer 400 to,
or None. Only the two fields the readers trust are checked here.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone


def _cost_problem(cost) -> str | None:
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) \
            or not math.isfinite(cost) or cost < 0:
        return 'cost must be a finite number of 0 or more'
    return None


def _published_at_problem(value) -> str | None:
    if not isinstance(value, str):
        return 'published_at must be an ISO 8601 UTC time'
    try:
        at = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        return 'published_at must be an ISO 8601 UTC time'
    if at.tzinfo is None or at.utcoffset() != timedelta(0):
        return 'published_at must be in UTC (end it with Z or +00:00)'
    if at > datetime.now(timezone.utc):
        return 'published_at cannot be in the future'
    return None


def problem(body: dict) -> str | None:
    if 'cost' in body:
        bad = _cost_problem(body['cost'])
        if bad:
            return bad
    if body.get('published_at'):          # empty/absent means "now", as record_published does
        return _published_at_problem(body['published_at'])
    return None
