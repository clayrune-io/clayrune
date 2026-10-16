"""MC-1103: hourly in-use retries sharing one bounded daily registry answer.

checked_ts tracks the last attempt; registry_checked_ts anchors the daily
check and must not slide forward every time an occupied CLI is retried.
Legacy records use checked_ts as their initial registry timestamp.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

CHECK_INTERVAL_S = 23 * 3600
RETRY_INTERVAL_S = 3600
RETRY_STATUSES = frozenset({'skipped_in_use'})


def registry_timestamp(entry: Mapping[str, Any]) -> float:
    return float(entry.get('registry_checked_ts', entry.get('checked_ts')) or 0)


def due(entry: Optional[Mapping[str, Any]], now: float) -> bool:
    if not entry or now - registry_timestamp(entry) >= CHECK_INTERVAL_S:
        return True
    return (entry.get('status') in RETRY_STATUSES
            and now - float(entry.get('checked_ts') or 0) >= RETRY_INTERVAL_S)


def cached_latest(entry: Optional[Mapping[str, Any]], now: float,
                  *, force: bool = False) -> Optional[Tuple[str, str]]:
    """Only an in-use retry within its original daily window uses the cache."""
    if (force or not entry or entry.get('status') not in RETRY_STATUSES
            or not 0 <= now - registry_timestamp(entry) < CHECK_INTERVAL_S):
        return None
    latest = entry.get('latest')
    if not isinstance(latest, str) or not latest:
        return None
    return latest, str(entry.get('latest_source') or 'cached daily check')
