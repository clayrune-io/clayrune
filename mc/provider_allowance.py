"""Per-provider allowance, one assembly for every route that reports it.

MC-1147. `/api/system/usage` already knew each engine's weekly usage
(`provider_weekly_usage`), but nothing an agent asks about engines pointed
there, so an agent deciding where to send work inferred a provider's headroom
from its last failed run. `/api/engine/preferences` now carries the same
numbers per provider as `allowance`.

The assembly lives here, not in either route, so the two cannot drift: both
call `build_weekly_usage` on the same two cached readers
(`system_routes._fetch_oauth_usage_limits`, `_fetch_codex_weekly_usage`).
A provider with no real signal gets `used_percent: None` -- never a 0.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from mc import allowance_state as _allowance_state
from mc.core import _log

# Where each provider's number comes from, for `allowance.source` when the
# reading itself does not say (Codex's reading says 'rollout' or 'probe').
_CLAUDE_SOURCE = 'oauth_usage_limits'

_RECORD_FIELDS = ('limit_kind', 'resets_at', 'resets_at_display',
                  'recorded_at', 'verified', 'status')


def build_weekly_usage(usage_limits: Optional[dict],
                       codex_weekly: Optional[dict]) -> Dict[str, dict]:
    """`provider_weekly_usage` for /api/system/usage (MC-966): one real weekly
    % per provider, keyed by name. Only providers with a real reading appear.

    Entries are fresh dicts: the exhaustion overlay mutates them, and the
    caller's cache entries must not leak `exhausted` into later reads.
    """
    weekly: Dict[str, dict] = {}
    seven_day = (usage_limits or {}).get('seven_day')
    if seven_day and seven_day.get('utilization') is not None:
        weekly['claude'] = {
            'utilization': seven_day.get('utilization'),
            'resets_at': seven_day.get('resets_at'),
        }
    if codex_weekly and codex_weekly.get('utilization') is not None:
        weekly['codex'] = dict(codex_weekly)
    # A vendor mid-exhaustion should read as full/red even if its last-sampled
    # weekly % predates the block -- but only for a provider that already has
    # a real bar; exhaustion alone is not a substitute for a missing weekly %.
    for vendor, entry in _allowance_state.all_states().items():
        if vendor in weekly:
            weekly[vendor]['exhausted'] = True
            weekly[vendor]['exhausted_display'] = _allowance_state.resets_clause(entry)
    return weekly


def allowance_view(provider: str, weekly: Dict[str, dict]) -> Dict[str, Any]:
    """`{used_percent, resets_at, sampled_at, source, exhausted_record}` for
    one provider. `exhausted_record` is Clayrune's own ALLOWANCE_EXHAUSTED
    record (set when a run hit the limit), independent of any % reading."""
    entry = weekly.get(provider) or {}
    record = _allowance_state.get(provider)
    source = entry.get('source')
    if source is None and provider == 'claude' and entry:
        source = _CLAUDE_SOURCE
    return {
        'used_percent': entry.get('utilization'),
        'resets_at': entry.get('resets_at'),
        'sampled_at': entry.get('sampled_at'),
        'source': source,
        'exhausted_record': ({k: record.get(k) for k in _RECORD_FIELDS}
                             if record else None),
    }


def _sources() -> Tuple[Optional[dict], Optional[dict]]:
    # The routes' own cached readers, called lazily like
    # mc/engine_pace_dispatch.provider_usage (importing system_routes at module
    # load would make this module depend on a blueprint).
    from mc.blueprints import system_routes
    return (system_routes._fetch_oauth_usage_limits(),
            system_routes._fetch_codex_weekly_usage())


def attach_to_providers(providers: list) -> None:
    """Add `allowance` to each `{'name': ...}` dict in place. A failed read
    leaves every provider with an all-None allowance and a log line, never an
    error response: the preferences view must load without this."""
    try:
        weekly = build_weekly_usage(*_sources())
    except Exception as e:
        _log(f"[provider-allowance] usage read failed: {e}", flush=True)
        weekly = {}
    for p in providers:
        p['allowance'] = allowance_view(p.get('name', ''), weekly)
