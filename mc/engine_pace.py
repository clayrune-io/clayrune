"""MC-1071: pure, stateless weekly pace policy for named characters.

Character is resolved metadata (scope/name/engine). Usage is the same
provider -> {utilization, resets_at} shape as /api/system/usage. The result
is a character reference and a reason only when a configured swap applies.
No I/O or logging here; the dispatch adapter reports unavailable readings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Mapping


def character_ref(character: Mapping) -> str:
    scope, name = character.get('scope'), character.get('name')
    return f'{scope}:{name}' if scope in ('global', 'project') and name else ''


def alternate_for(character: Mapping, config: Mapping) -> str:
    alternates = config.get('engine_pace_alternates', {})
    if not isinstance(alternates, dict):
        return ''
    alternate = alternates.get(character_ref(character))
    if not isinstance(alternate, str):
        return ''
    scope, _, name = alternate.strip().partition(':')
    if scope not in ('global', 'project') or not name.strip():
        return ''
    return f'{scope}:{name.strip()}'


def _percent(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and 0 <= value <= 100 else None


def weekly_reading(character: Mapping, usage: Mapping, now: datetime):
    """Return (utilization, elapsed %) or None for unknown/stale windows."""
    engine = character.get('engine') or {}
    provider = engine.get('provider', '') if isinstance(engine, dict) else ''
    entry = usage.get(provider)
    if not isinstance(entry, dict):
        return None
    utilization = _percent(entry.get('utilization'))
    reset = entry.get('resets_at')
    if utilization is None or not isinstance(reset, str):
        return None
    try:
        resets_at = datetime.fromisoformat(reset.replace('Z', '+00:00'))
        if resets_at.tzinfo is None or now.tzinfo is None:
            return None
        remaining = resets_at.astimezone(timezone.utc) - now.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None
    window = timedelta(days=7)
    if remaining <= timedelta(0) or remaining > window:
        return None
    return utilization, 100 * (1 - remaining / window)


def decide(character: Mapping, usage: Mapping, now: datetime,
           config: Mapping) -> tuple[str, str | None]:
    """Route once to the user's named alternate, never recursively.

    Strict comparisons: exactly at the margin or ceiling stays as asked.
    A reset that has elapsed invalidates the reading, even at 100% usage.
    """
    original = character_ref(character)
    alternate = alternate_for(character, config)
    if config.get('engine_pace_enabled', True) is False or not alternate or alternate == original:
        return original, None
    reading = weekly_reading(character, usage, now)
    if reading is None:
        return original, None
    utilization, elapsed = reading
    margin = _percent(config.get('engine_pace_margin_points', 5))
    ceiling = _percent(config.get('engine_pace_ceiling_percent', 85))
    if margin is None or ceiling is None:
        return original, None
    provider = (character.get('engine') or {}).get('provider', '')
    if utilization > ceiling:
        return alternate, f'{provider} weekly usage {utilization:g}% exceeds the {ceiling:g}% ceiling'
    if utilization > elapsed + margin:
        return alternate, (f'{provider} weekly usage {utilization:g}% is ahead of '
                           f'{elapsed:.1f}% elapsed by more than {margin:g} points')
    return original, None
