"""MC-964 T4 — a vendor exhaustion record with NO reset time cannot pin a vendor forever.

The 33-hour Codex blackout (docs/MEMORY_OVERHAUL_PLAN.md RC3) was a standing
record that nothing could contradict: the only run that could refute it was
the run the record refused. Codex got a token-free probe (`heal`). Gemini and
Qwen have none, and their exhaustion records never carry a reset time
(`allowance_state._generic_text_exhaustion`), so `_is_expired` never clears
them: the same deadlock was still open for both.

Policy, in one place (this module is pure — it holds no state; the record and
its transitions live in `mc.allowance_state`):

  * A record with no `resets_at` that is older than the TTL
    (`allowance_unverified_ttl_hours`, default 24; 0 / none / off disables)
    becomes **unverified**: still shown, still refused to every reader that is
    only LOOKING, but the dispatch gate lets exactly ONE trial run through.
  * The trial run's own outcome is the evidence: a success clears the record
    (the existing success path), a fresh exhaustion event re-records it with a
    new `recorded_at` — so the next trial is a full TTL away.
  * A trial that never reports back (cancelled, crashed, refused for another
    reason) must not become the next deadlock, so a grant lapses after
    `TRIAL_LAPSE_S` and the next dispatch may claim a new one.

Dave's decision, 2026-10-15 (DECIDED WITHOUT RON, journal b2d85e51): 24h TTL,
one trial; reverse by setting the TTL to 0 / none.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from mc import state

CONFIG_KEY = 'allowance_unverified_ttl_hours'
DEFAULT_TTL_HOURS = 24.0

# How long a claimed trial blocks further claims before it is presumed lost.
TRIAL_LAPSE_S = 30 * 60

_OFF_WORDS = {'', '0', 'none', 'off', 'false', 'no', 'disabled'}


def ttl_hours(config: Optional[dict] = None) -> Optional[float]:
    """The configured TTL in hours, or None when the feature is disabled.
    Absent key -> the default. 0, negative, null, false or an off-word -> None.
    An unparseable value falls back to the default rather than silently
    disabling a safety net."""
    cfg = state.CONFIG if config is None else config
    if CONFIG_KEY not in cfg:
        return DEFAULT_TTL_HOURS
    raw = cfg[CONFIG_KEY]
    if raw is None or raw is False:
        return None
    if isinstance(raw, str):
        if raw.strip().lower() in _OFF_WORDS:
            return None
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TTL_HOURS
    return hours if hours > 0 else None


def _parse(iso_ts) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(iso_ts).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def age_seconds(entry: dict, now: Optional[datetime] = None) -> Optional[float]:
    """Seconds since the record was written, or None if `recorded_at` is
    missing / unparseable (a record with no age is never judged stale)."""
    dt = _parse(entry.get('recorded_at'))
    if dt is None:
        return None
    return ((now or datetime.now(timezone.utc)) - dt).total_seconds()


def age_label(entry: dict, now: Optional[datetime] = None) -> str:
    """'25h' / '3d 2h' style age for a refusal; '' when unknown."""
    secs = age_seconds(entry, now)
    if secs is None or secs < 0:
        return ''
    hours = int(secs // 3600)
    if hours < 48:
        return f'{hours}h'
    return f'{hours // 24}d {hours % 24}h'


def is_stale_unreset(entry: dict, ttl: Optional[float],
                     now: Optional[datetime] = None) -> bool:
    """True when `entry` has no reset time and has outlived `ttl` hours."""
    if ttl is None or entry.get('resets_at'):
        return False
    secs = age_seconds(entry, now)
    return secs is not None and secs >= ttl * 3600.0


def is_unverified(entry: Optional[dict]) -> bool:
    return bool(entry) and entry.get('status') == 'unverified'


def trial_active(entry: dict, now: Optional[datetime] = None) -> bool:
    """A trial was granted and has not yet lapsed."""
    started = _parse(entry.get('trial_started_at'))
    if started is None:
        return False
    elapsed = ((now or datetime.now(timezone.utc)) - started).total_seconds()
    return elapsed < TRIAL_LAPSE_S
