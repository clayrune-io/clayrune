"""Canonical UTC window identity for cached impact reads (MC-1152 P3).

P1 validates both Z and +00:00 without changing their spelling. Collection keys
must additionally make those equivalent inputs share the same cached series.
"""
from mc import desk_impact_schema as S
from mc.desk_impact_sources.base import Window


def canonical_utc(name: str, stamp: str) -> str:
    return S.parse_utc(name, stamp).isoformat()


def canonical_window(window: Window) -> Window:
    clean = S.validate_window(window.as_dict())
    return Window(canonical_utc('window.start', clean['start']) if clean['start'] else None,
                  canonical_utc('window.end', clean['end']), clean['granularity'], clean['timezone'])
