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

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Union

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


def _normalize_resets_at(resets_at: Optional[str]) -> Optional[str]:
    """Round `resets_at` to the nearest minute before it's ever stored.

    The Anthropic usage endpoint returns this value with sub-second jitter
    on every poll of the SAME window (measured 2026-09-29: 241 samples, 241
    distinct raw values), so two samples of one window rarely carry the
    identical string. `mc.usage_breakdown_aggregate` now tolerates jitter
    when comparing existing rows, but new rows should be written clean --
    leaves the raw value untouched (never guess) if it doesn't parse."""
    if not resets_at:
        return resets_at
    try:
        dt = datetime.fromisoformat(resets_at.replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return resets_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    rounded = dt.replace(second=0, microsecond=0)
    if dt.second >= 30:
        rounded += timedelta(minutes=1)
    return rounded.isoformat()


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
            raw_utilization=util, resets_at=_normalize_resets_at(win.get('resets_at')),
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
            raw_utilization=util, resets_at=_normalize_resets_at(win.get('resets_at')),
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
            # `cached_input_tokens` is a SUBSET of `input_tokens` (spec's
            # metrics table) -- split it into cache_read so `input_fresh`
            # reflects only the non-cached portion. `processed_total` stays
            # `inp` (the already-inclusive total); adding cached again would
            # double-count it a second way (finding #9, review journal).
            cached = int(nested.get('cached_input_tokens') or 0)
            cache_read = min(cached, inp)
            fresh = inp - cache_read
            output, processed_total = out, inp
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


def baseline_checkpoint_fields(session_id: str, *, provider: str, observed_at: str) -> dict:
    """The dispatch-time 'baseline' `session_checkpoint` row: zero cumulative
    tokens at `observed_at` (the dispatch time). `token_coverage='complete'`
    -- a zero baseline is a confirmed zero, not missing data, so it never
    trips the "unavailable" no-fabricated-zero rule downstream. `provider` is
    known at dispatch (the session dict's own field), captured here so
    calibration can filter by provider before this session ever gets a
    session_fact row (which only exists after completion)."""
    return {
        'session_id': session_id, 'provider': provider, 'checkpoint_type': 'baseline',
        'observed_at': observed_at,
        'input_fresh': 0, 'input_cache_write': 0, 'input_cache_read': 0,
        'input_processed_total': 0, 'output_tokens': 0, 'output_reasoning': 0,
        'token_coverage': 'complete',
    }


def turn_start_checkpoint_fields(session_id: str, *, provider: str, observed_at: str,
                                  fact: Optional[dict] = None) -> dict:
    """The 'turn_start' `session_checkpoint` row written at the moment a NEW
    turn begins -- a follow-up send or a respawn-resume into an EXISTING
    session (schema v5, MC-998 turn-start fix). A session's very first turn
    never gets one: the dispatch-time 'baseline' already serves as turn 1's
    start marker.

    Carries forward the session's cumulative counters AS OF THIS INSTANT --
    unchanged since its last completion, because the new turn hasn't
    produced a single token yet -- read from `fact` (the store's current
    `session_fact` row for this session, passed in by the caller) so the
    idle span before this row always deltas to zero in `_session_turns`.
    With no `fact` (a first-completion-never-landed edge case) every token
    field stays None and `token_coverage='unavailable'`, the same
    no-fabricated-zero rule `completion_checkpoint_fields` follows --
    `_session_turns` then reports that turn's delta as unavailable rather
    than a false zero, never a false zero disguised as measured idle time."""
    fact = fact or {}
    return {
        'session_id': session_id, 'provider': provider or fact.get('provider') or 'claude',
        'checkpoint_type': 'turn_start', 'observed_at': observed_at,
        'input_fresh': fact.get('input_fresh'), 'input_cache_write': fact.get('input_cache_write'),
        'input_cache_read': fact.get('input_cache_read'),
        'input_processed_total': fact.get('input_processed_total'),
        'output_tokens': fact.get('output_tokens'), 'output_reasoning': fact.get('output_reasoning'),
        'token_coverage': fact.get('token_coverage') or 'unavailable',
    }


def completion_checkpoint_fields(fact: dict, *, session_id: str, observed_at: str) -> dict:
    """The completion-time 'completion' `session_checkpoint` row, built from
    the same `fact` dict `session_fact_from_entry` just produced -- the
    checkpoint and the session_fact snapshot must never disagree on the
    session's final cumulative totals (or its provider), so this reads its
    fields rather than re-deriving them from the raw entry."""
    return {
        'session_id': session_id, 'provider': fact.get('provider') or 'claude',
        'checkpoint_type': 'completion', 'observed_at': observed_at,
        'input_fresh': fact.get('input_fresh'), 'input_cache_write': fact.get('input_cache_write'),
        'input_cache_read': fact.get('input_cache_read'),
        'input_processed_total': fact.get('input_processed_total'),
        'output_tokens': fact.get('output_tokens'), 'output_reasoning': fact.get('output_reasoning'),
        'token_coverage': fact.get('token_coverage') or 'unavailable',
    }


def should_sample_interval_seconds(*, any_session_active: bool) -> int:
    """60s while any provider session runs, 300s (5 min) while idle -- the
    two cadences the spec's Sampling section requires."""
    return 60 if any_session_active else 300


# Backstop for a launch the caller's live set cannot see yet. The server's
# live set already includes every dispatch whose durable record (baseline
# checkpoint included) was written before Popen and whose session is not
# registered yet (state.pending_launches); this only covers a writer that
# does not register there. Anything with durable activity newer than this
# is left alone.
RECONCILE_GRACE_SECONDS = 120
# Used ONLY when the live-session registry could not be read at all: close
# an open session once its newest durable activity is a day old. Longer
# than any single agent turn this install has run, and it caps what one
# crash can block at a day instead of the 90-day retention window. A
# heuristic, not proof of death -- which is why it never runs while the
# registry answers.
RECONCILE_FALLBACK_MAX_AGE_SECONDS = 24 * 3600


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def reconcile_dead_sessions(store: UsageBreakdownStore, *,
                            live_session_ids: Union[Optional[set], Callable[[], Optional[set]]],
                            now: Optional[datetime] = None,
                            process_started_at: Optional[str] = None) -> list[str]:
    """Close every session the store still holds open (a 'running' fact, or
    a baseline-only first turn) whose MC session is no longer live -- it
    crashed mid-turn, or the server restarted and nothing re-adopted it.
    Left open, its span never ends and overlaps every later calibration
    interval and window until the 90-day prune. And reopen any session this
    closed earlier that turns out to be live after all.

    Liveness is `live_session_ids`: every MC session whose provider process
    is still alive, whatever its status -- a Mode B session is 'idle'
    between turns and while it waits on an mc:question, with the process
    (and its automatic next turn) still there -- plus every launch whose
    durable record exists but whose session is not registered yet (round
    4). Pass it as a zero-argument CALLABLE: it is then read AFTER the store
    snapshot, so a turn that starts in between is either seen live or has
    written a newer generation that makes the close a no-op
    (`close_session_ended_unknown`). A plain set is accepted for tests.

    A session is closed at `now`, the first moment it was observed gone --
    not at its last checkpoint, which for a resumed turn predates the whole
    crashed turn and would leave the interval it died in looking isolated.
    The overshoot is at most one sampler tick (60s while any session runs);
    across a server restart the gap has no allowance samples, and a sample
    pair spanning it exceeds the 10-minute interval limit anyway.

    `process_started_at` (this server process's start) tightens that for a
    session whose newest activity predates it: it belonged to the previous
    server process and died no later than that process did, so it is
    closed at this process's start rather than at `now` -- a server left
    off for a day must not mark that whole day's windows incomplete.

    A live set of None means the registry could not be read: fall back to
    RECONCILE_FALLBACK_MAX_AGE_SECONDS of durable inactivity, and reopen
    nothing. Returns the closed session ids."""
    now = now or datetime.now(timezone.utc)
    booted = _parse_ts(process_started_at)
    rows = store.list_open_sessions()
    ended_unknown = store.list_ended_unknown_session_ids()
    live = live_session_ids() if callable(live_session_ids) else live_session_ids
    if live is not None:
        for sid in ended_unknown:
            if sid in live:
                store.mark_session_running(sid)
    closed = []
    for row in rows:
        sid = row['session_id']
        last = _parse_ts(row.get('last_activity_at'))
        age = (now - last).total_seconds() if last else None
        if live is None:
            if age is None or age < RECONCILE_FALLBACK_MAX_AGE_SECONDS:
                continue
        elif sid in live or (age is not None and age < RECONCILE_GRACE_SECONDS):
            continue
        end = booted if (booted and last and last < booted < now) else now
        if store.close_session_ended_unknown(sid, provider=row.get('provider') or 'claude',
                                             started_at=row.get('started_at'),
                                             ended_at=end.isoformat(),
                                             generation=row.get('generation')):
            closed.append(sid)
    return closed
