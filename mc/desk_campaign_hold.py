"""A campaign whose plan end date has passed: what a held post says, and when Renew is real.

Renew opens the next TERM (`desk.renew_campaign`), but `plan.end.date` is a
separate bound the publisher checks first (`desk_tick._cadence_refusal`). Once
that date has passed, a new term does not let anything out, so "renew" is the
wrong thing to tell the person. The fix that works is the one that already
exists on screen: move Ends later in Brief > Limits, then approve the new bounds
in Launch. Nothing here extends a date or approves anything.

`static/js/desk-v1-campaign-ended.js` mirrors `plan_end_passed` for the
Launch panel and the held-post card; `CODE` is what lets the card offer its
button (same shape as MC-1114's `post_permission`).
"""
from datetime import datetime, timedelta, timezone

CODE = 'campaign_ended'


class Refusal(str):
    """A refusal reason that carries the machine code a screen can act on."""
    code = CODE


def _parse(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).strip().replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def plan_end_passed(camp: dict, now: datetime) -> str | None:
    """The plan's end date (YYYY-MM-DD) when it has fully passed, else None.
    The day itself still posts: the cutoff is the end of that day."""
    end = ((camp.get('plan') or {}).get('end') or {}).get('date')
    if not end:
        return None
    ends = _parse(end)
    if ends is None or now < ends + timedelta(days=1):
        return None
    return str(end)[:10]


def ended_message(date: str) -> str:
    return f'This campaign ended on {date}. Extend its end date, then approve to keep posting.'
