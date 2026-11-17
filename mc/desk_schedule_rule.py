"""What may change an APPROVED version's send time (MC-1187 DK2).

A human approved this text for this account at the time it carried. The publish
tick sends whatever is `scheduled` and due, so a writable `scheduled_at` is a
second way to arm or fire an approved post. The one thing the person is allowed
to do without a fresh approval is nudge the time within the day they approved
(tests/test_desk_pieces.py: "an approved version keeps its approval through a
time change"; "moving it to another day goes back through review").

For a version in an approved state (`approved`, `scheduled`), a new `scheduled_at`
that is not part of the same PATCH as `needs_review` (which drops the approval) is
accepted only if ALL hold:

  * the caller is a human (the route passes `human=not is_unattended_caller()`;
    the library default is False, so nothing that forgets to say so can move it);
  * it is a time, not null (null leaves a `scheduled` version never due and never
    held, desk_tick.py);
  * it is on the same calendar day as the current slot, in the install's
    timezone (`user_timezone` in config, empty = this machine's; the same rule the
    Desk calendar uses to place a chip, static/js/desk-v1-calendar.js `_dayKey`);
  * it is not in the past (the tick would send it at once).

Restating the time it already has is not a change and is always accepted.
"""
from __future__ import annotations

from datetime import datetime, timezone, tzinfo

from mc import state as _state
from mc.core import _log


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _zone() -> tzinfo:
    """The install timezone. An empty or unresolvable `user_timezone` falls back to
    this machine's local zone (Windows has no IANA database unless `tzdata` is
    installed); the fallback is logged, never silent."""
    name = ((_state.CONFIG or {}).get('user_timezone') or '').strip()
    if name:
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(name)
        except Exception as e:  # ZoneInfoNotFoundError, ValueError, OSError
            _log(f'[desk] user_timezone {name!r} unusable ({type(e).__name__}); '
                 "using this machine's timezone for the approved-time rule")
    return datetime.now().astimezone().tzinfo or timezone.utc


def _aware(value) -> datetime | None:
    from mc import desk
    if not value:
        return None
    try:
        return desk._parse_dt(value)
    except (ValueError, TypeError):
        return None


def check_time_change(current, new, *, human: bool, error):
    """Raise `error(message, status)` unless the move is allowed. `error` is
    `desk_pieces.PieceError`, passed in because desk_pieces imports this module."""
    cur_dt, new_dt = _aware(current), _aware(new)
    if cur_dt is not None and new_dt is not None and cur_dt == new_dt:
        return
    if not human:
        raise error('this version is approved: only a person can move its time; '
                    'send it back to review to change it any other way', 403)
    if new_dt is None:
        raise error('an approved version needs a send time: send it back to review '
                    'before clearing it', 409)
    if new_dt < _now():
        raise error('that time has already passed: send the version back to review '
                    'to change it', 409)
    zone = _zone()
    if cur_dt is None or cur_dt.astimezone(zone).date() != new_dt.astimezone(zone).date():
        raise error('an approved version can only move within the day it was approved '
                    'for: send it back to review to move it to another day', 409)
