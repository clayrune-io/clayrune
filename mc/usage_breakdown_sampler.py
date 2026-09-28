"""MC-998 Phase 2 — vendor-agnostic sampling + session-fact logic for the
Usage Breakdown store (docs/USAGE_BREAKDOWN_SPEC.md's "Sampling and durable
facts" + "Metrics and computation" tables).

Deliberately pure functions taking already-fetched data, not doing any I/O
or network fetch of their own: the spec requires sharing the EXISTING 60s
vendor caches (mc/blueprints/system_routes.py's `_fetch_oauth_usage_limits`
and `_fetch_codex_usage_detail`) rather than issuing new requests, so the
integration point (server.py / system_routes.py) owns calling those and
handing this module their result plus the cache's own observation
timestamp. That split also keeps this module testable without a Flask app
or a real vendor credential.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from mc.usage_breakdown_store import UsageBreakdownStore

# (window_kind, window_scope, key-into-_fetch_oauth_usage_limits()'s dict)
CLAUDE_WINDOWS = (
    ('5h', 'all', 'five_hour'),
    ('7d', 'all', 'seven_day'),
    ('7d', 'opus', 'seven_day_opus'),
    ('7d', 'sonnet', 'seven_day_sonnet'),
)
# (window_kind, key-into-_fetch_codex_usage_detail()'s dict); Codex has one
# account-wide scope, no per-model split.
CODEX_WINDOWS = (
    ('5h', 'five_hour'),
    ('7d', 'weekly'),
)


def _iso_from_epoch(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def sample_claude(store: UsageBreakdownStore, *, usage_limits: Optional[dict],
                   fetched_at_epoch: Optional[float]) -> int:
    """Persist one allowance_sample row per populated Claude window.

    `fetched_at_epoch` must be the OAuth cache's own fetch timestamp (the
    actual successful response time) -- not `time.time()` at call time. It
    is identical across repeated calls served from the shared 60s TTL cache,
    which is exactly the store's dedup key: a cache hit naturally inserts
    zero new rows without this module needing to know it was a cache hit.
    Returns the count of rows newly inserted (0 on no data / no fetch time).
    """
    if not isinstance(usage_limits, dict) or fetched_at_epoch is None:
        return 0
    observed_at = _iso_from_epoch(fetched_at_epoch)
    inserted = 0
    for window_kind, window_scope, key in CLAUDE_WINDOWS:
        win = usage_limits.get(key)
        if not isinstance(win, dict):
            continue
        util = win.get('utilization')
        if util is None:
            continue
        if store.record_allowance_sample(
            provider='claude', window_kind=window_kind, window_scope=window_scope,
            raw_utilization=util, resets_at=win.get('resets_at'),
            source_observed_at=observed_at, source_version='oauth_usage_v1',
        ):
            inserted += 1
    return inserted


def sample_codex(store: UsageBreakdownStore, *, detail: Optional[dict]) -> int:
    """Persist one allowance_sample row per populated Codex window from
    `_fetch_codex_usage_detail()`'s result.

    `event_at` (the rollout record's own `timestamp` field -- real event
    time) is the preferred dedup identity; `sampled_at` (rollout file mtime,
    which can repeat across polls of an unchanged file, or lag behind the
    record it describes) is the fallback only when a line predates that
    field. Returns the count of rows newly inserted.
    """
    if not isinstance(detail, dict):
        return 0
    observed_at = detail.get('event_at') or detail.get('sampled_at')
    if not observed_at:
        return 0
    inserted = 0
    for window_kind, key in CODEX_WINDOWS:
        win = detail.get(key)
        if not isinstance(win, dict):
            continue
        util = win.get('utilization')
        if util is None:
            continue
        if store.record_allowance_sample(
            provider='codex', window_kind=window_kind, window_scope='all',
            raw_utilization=util, resets_at=win.get('resets_at'),
            source_observed_at=observed_at, source_version='rollout_rate_limits_v1',
        ):
            inserted += 1
    return inserted


# ── session facts ────────────────────────────────────────────────────────

_TERMINAL_STATUS = {'completed': 'completed', 'error': 'error', 'stopped': 'error'}


def session_fact_from_entry(entry: dict, *, project_id: str,
                             housekeeping: bool = False, included: bool = True) -> dict:
    """Build the `fields` dict for `UsageBreakdownStore.upsert_session_fact`
    from an agent_log-shaped entry (or a live in-memory session dict using
    the same field names -- both share the shape built in
    `agent_routes._log_agent_completion_body`).

    Token-category rules per the spec's "Metrics and computation" table:
      - Claude: prefer transcript-derived flat `input_tokens`/`output_tokens`
        (+ `cache_read_tokens`) when positive -- `coverage='complete'`.
        Falls back to the nested `usage` dict (`coverage='partial'`, matches
        the spec's per-turn-vs-cumulative caveat: a legacy/interrupted entry
        may only carry the last turn there). `input_processed_total` sums
        fresh + cache write + cache read.
      - Codex (and any other provider whose adapter only ever populates
        nested `usage`): `usage.input_tokens` IS the input total;
        `usage.cached_input_tokens` is a subset and is never added again.
      - No usable evidence at all -> every token field stays None and
        `token_coverage='unavailable'` -- never a fabricated 0.

    `status` folds the entry's 'stopped' into 'error' (both are terminal,
    non-completed outcomes) and leaves anything else (e.g. 'in_progress')
    as 'running'. `ended_at` is only stamped for a terminal status -- a
    running session's fact carries no end time.
    """
    provider = (entry.get('provider') or 'claude').lower()
    status = _TERMINAL_STATUS.get(entry.get('status') or '', 'running')

    _raw_usage = entry.get('usage')
    nested: dict = _raw_usage if isinstance(_raw_usage, dict) else {}
    fresh = cache_write = cache_read = output = reasoning = processed_total = None
    token_source = 'unavailable'
    coverage = 'unavailable'

    if provider == 'claude':
        top_in = entry.get('input_tokens')
        top_out = entry.get('output_tokens')
        if top_in or top_out:
            fresh = int(top_in or 0)
            cache_read = int(entry.get('cache_read_tokens') or 0)
            cache_write = int(nested.get('cache_creation_input_tokens') or 0)
            output = int(top_out or 0)
            processed_total = fresh + cache_write + cache_read
            token_source, coverage = 'transcript', 'complete'
        elif nested:
            n_fresh = int(nested.get('input_tokens') or 0)
            n_cw = int(nested.get('cache_creation_input_tokens') or 0)
            n_cr = int(nested.get('cache_read_input_tokens') or 0)
            n_out = int(nested.get('output_tokens') or 0)
            if n_fresh or n_cw or n_cr or n_out:
                fresh, cache_write, cache_read, output = n_fresh, n_cw, n_cr, n_out
                processed_total = fresh + cache_write + cache_read
                token_source, coverage = 'nested_usage', 'partial'
    elif nested:
        inp = int(nested.get('input_tokens') or 0)
        out = int(nested.get('output_tokens') or 0)
        if inp or out:
            fresh, output, processed_total = inp, out, inp
            reasoning = nested.get('reasoning_output_tokens')
            token_source, coverage = 'nested_usage', 'complete'

    character = entry.get('character')
    if isinstance(character, dict):
        character = character.get('name')

    return {
        'provider': provider,
        'project_id': project_id,
        'character': character,
        'trigger_type': entry.get('trigger_type') or 'unknown',
        'requested_model': entry.get('model') or entry.get('agent_model'),
        'observed_model': entry.get('observed_model') or entry.get('model'),
        'status': status,
        'started_at': entry.get('started_at'),
        'ended_at': entry.get('ts') if status != 'running' else None,
        'input_fresh': fresh,
        'input_cache_write': cache_write,
        'input_cache_read': cache_read,
        'input_processed_total': processed_total,
        'output_tokens': output,
        'output_reasoning': reasoning,
        'token_source': token_source,
        'token_coverage': coverage,
        'included': included,
        'housekeeping': housekeeping,
        'parent_session_id': entry.get('parent_session_id') or entry.get('_notify_session') or None,
    }


def should_sample_interval_seconds(*, any_session_active: bool) -> int:
    """60s while any provider session runs, 300s (5 min) while idle -- the
    two cadences the spec's Sampling section requires."""
    return 60 if any_session_active else 300
