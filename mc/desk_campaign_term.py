"""MC-1125: server-owned terms on campaigns that have already started.

Drafts may propose a term for the Start gate. After Start, a normal save may
only change its end by changing the plan end; Renew owns the next term.
"""
from datetime import datetime, timedelta, timezone

MAX_TERM_DAYS = 90


def _date(value: object) -> datetime:
    dt = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def prepare_campaign_patch(camp: dict, patch: dict) -> dict:
    """Drop stale/client-authored terms; derive a changed plan end before the
    caller checks widening. Keep the current history row consistent, leaving
    closed terms and human approval snapshots untouched. No store I/O here.
    """
    if not (camp.get('started_at') or camp.get('approved')
            or camp.get('state') not in ('draft', 'proposed')):
        return patch
    patch = {k: v for k, v in patch.items() if k != 'term'}
    term = camp.get('term')
    plan = patch.get('plan')
    if not isinstance(term, dict) or not term.get('starts') or not isinstance(plan, dict):
        return patch
    end = (plan.get('end') or {}).get('date')
    if not end:
        return patch  # Removing the plan deadline must not create an unbounded term.
    try:
        limit = _date(term['starts']) + timedelta(days=MAX_TERM_DAYS)
        ends = min(_date(end), limit)
        old_end = _date(term['ends']) if term.get('ends') else None
        previous = ((camp.get('plan') or {}).get('end') or {}).get('date')
        # Also repair an old inconsistent term on a plan save, but never roll
        # back a newer stored term when an unchanged plan is saved again.
        if end == previous and old_end is not None and old_end >= ends:
            return patch
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('campaign end and term start/end must be valid ISO dates') from exc
    nxt = {**term, 'ends': ends.date().isoformat() if len(str(end).strip()) <= 10 else ends.isoformat()}
    patch['term'] = nxt
    if isinstance(camp.get('terms'), list):
        camp['terms'] = [dict(nxt) if t.get('index') == term.get('index') else t
                         for t in camp['terms']]
    return patch
