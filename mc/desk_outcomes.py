"""Pure receipt measurements and the MC-968 low-reach heuristic.

Ledger rows are successful publication receipts (including human-reported posts),
not queued attempts. The heuristic describes reach, never proves suppression.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import math

POSSIBLY_SUPPRESSED_IMPRESSIONS = 5


def threshold_from_config(config: dict) -> float:
    value = config.get('desk_suppression_impressions', POSSIBLY_SUPPRESSED_IMPRESSIONS)
    return float(value) if _number(value) is not None else float(POSSIBLY_SUPPRESSED_IMPRESSIONS)


def validate_threshold(value) -> float:
    number = _number(value)
    if number is None:
        raise ValueError('desk_suppression_impressions must be a finite non-negative number')
    return number


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _date(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def receipt_outcomes(row: dict, *, now: datetime,
                     threshold: float = POSSIBLY_SUPPRESSED_IMPRESSIONS) -> dict:
    """Latest observed numbers and tri-state `possibly_suppressed`.

    Invalid latest observations invalidate that metric; an older low reading
    must not survive an invalid newer one. Malformed observation times likewise
    cannot establish ordering. Young readings can be displayed, but do not
    establish a verdict even when the post itself is now old enough.
    """
    published = _date(row.get('published_at'))
    measurements = {}
    for metric in ('impressions', 'replies'):
        entries = [o for o in row.get('outcomes') or []
                   if isinstance(o, dict) and o.get('metric') == metric]
        dated = [(_date(o.get('at')), o) for o in entries]
        if not entries or any(at is None for at, _ in dated):
            measurements[metric] = (None, None)
            continue
        at, entry = max(reversed(dated), key=lambda pair: pair[0] or now)
        valid_time = (published is not None and at is not None
                      and published <= at <= now)
        measurements[metric] = (_number(entry.get('value')) if valid_time else None, at)
    impressions, observed = measurements['impressions']
    verdict = None
    if (row.get('id') and published is not None and observed is not None
            and impressions is not None and observed - published >= timedelta(hours=24)
            and _number(threshold) is not None):
        verdict = impressions < threshold
    return {'id': row.get('id'), 'published_at': row.get('published_at'),
            'body': str(row.get('body') or '')[:160], 'platform': row.get('platform'),
            'impressions': impressions, 'replies': measurements['replies'][0],
            'possibly_suppressed': verdict}
