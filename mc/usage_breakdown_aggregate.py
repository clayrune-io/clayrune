"""MC-998 phase 4b — pure aggregation for the Usage Breakdown dashboard
(docs/USAGE_BREAKDOWN_SPEC.md "Metrics and computation",
"Percentage attribution and uncertainty", "Dashboard layout and states").

Takes already-fetched store rows (never touches sqlite itself, never a vendor
network call) so it is testable with hand-built fixtures. The Flask route in
mc/blueprints/system_routes.py owns fetching from `UsageBreakdownStore` and
handing this module the rows.

Deliberate scope cut from the full spec, disclosed here rather than silently:
the spec's percentage-attribution machinery (tokens-per-point, estimated
share, `Unattributed / uncertain`) is implemented at the AGGREGATE
(whole-bar) level only -- one segmented bar per provider/window, exactly as
"Dashboard layout and states" describes it ("a segmented bar of estimated
Clayrune work and `Unattributed / uncertain`"). It is NOT split per project/
character/trigger/model row; the spec's ranking table is token/LOC-sorted,
never percentage-sorted, and "Overlapping sessions" proportional splitting
(a per-session, sub-bar allocation) is out of scope for this pass. This
sidesteps having to prove per-row percentage attribution never claims false
precision -- acceptance check 3 ("simultaneous sessions never yield exact
project percentages") holds structurally because no row is ever given one.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Optional

_MAX_INTERVAL_MINUTES = 10
_MIN_ELIGIBLE_INTERVALS = 5
_MIN_ELIGIBLE_SESSIONS = 3
# Backlog 4668eafc follow-up 2 (pooled estimator): the gate also needs enough
# TOTAL vendor movement across the contributing intervals. raw_utilization is
# whole points, so the pooled ratio is only as good as the points it divides
# by -- a handful of intervals that together moved the counter 1pp is a
# quantisation artefact, not a rate.
_MIN_CALIBRATION_TOTAL_PP = 5.0
_BOOTSTRAP_RESAMPLES = 2000
_BOOTSTRAP_SEED = 998  # fixed: the same history must always print the same range
_BOOTSTRAP_LOW_PCT, _BOOTSTRAP_HIGH_PCT = 10, 90
# MC-998 follow-up: the Anthropic usage endpoint returns resets_at with
# sub-second jitter on every poll (measured 2026-09-29: 241 samples inside
# one 5h/7d window, 241 distinct raw values -- e.g. 19:00:00.444543,
# 18:59:59.567670, 19:00:00.005273). A genuine reset moves resets_at by a
# whole window (5h or 7d), so any tolerance well under that can never merge
# two real windows together.
_RESET_JITTER_TOLERANCE_S = 120
# Backlog 4668eafc follow-up (defect 3): resets_at can lag the actual reset --
# measured 2026-09-29, the claude 7d bar's raw_utilization went 100 -> 0
# between two samples 5 minutes apart while resets_at held at its OLD value
# for both (~2026-10-02T02:00), so `_same_reset` never saw a reset and
# `compute_bar_change` returned a fabricated -64pp swing. raw_utilization
# only ever climbs within one window and drops back near zero at a reset, so
# any drop bigger than ordinary sampling noise IS a reset boundary, whether
# or not resets_at has caught up yet.
_RESET_DROP_TOLERANCE_PP = 2.0

RANKING_DIMENSIONS = ('project', 'character', 'trigger', 'model', 'provider')
_DIMENSION_FACT_KEY = {
    'project': 'project_id',
    'character': 'character',
    'trigger': 'trigger_type',
    'model': 'observed_model',
    'provider': 'provider',
}


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _same_reset(a: Optional[str], b: Optional[str],
                 tolerance_s: float = _RESET_JITTER_TOLERANCE_S) -> bool:
    """True when two `resets_at` values are the same reset boundary within
    jitter, not a genuine reset. String equality (the original check) treats
    every single sample pair as a reset crossing once jitter is present --
    live proof: GET /api/system/usage/breakdown with 241/241 distinct
    resets_at values in-window returned `reset_crossed` for a range where no
    reset happened. Fails closed: unparseable or missing values are never
    treated as the same reset, matching the prior (conservative) behaviour
    on bad data."""
    dt_a, dt_b = _parse_iso(a), _parse_iso(b)
    if dt_a is None or dt_b is None:
        return False
    return abs((dt_a - dt_b).total_seconds()) <= tolerance_s


def _reset_boundary_crossed(earlier_util: Optional[float], later_util: Optional[float],
                             tolerance_pp: float = _RESET_DROP_TOLERANCE_PP) -> bool:
    """True when `later_util` fell more than `tolerance_pp` below
    `earlier_util` -- a drop this size is never legitimate usage (a window's
    raw_utilization only climbs between resets), so it marks a reset boundary
    even when `resets_at` itself hasn't moved yet (defect 3, see
    `_RESET_DROP_TOLERANCE_PP`). Missing values are never treated as a
    crossing -- same fail-closed posture as `_same_reset`."""
    if earlier_util is None or later_util is None:
        return False
    return (earlier_util - later_util) > tolerance_pp


def reset_drop_starts(samples: list[dict]) -> set[str]:
    """`source_observed_at` of every sample that begins a fresh window after a
    raw_utilization reset drop, for callers that must split a constant-
    `resets_at` run at the real reset (the default range and the windows
    picker). Same predicate and same sample filter as `compute_bar_change`
    (quality 'ok', non-null raw_utilization, `source_observed_at` order), so
    a range that begins at one of these never reads `reset_crossed`. Measured
    2026-09-29: the claude 7d window went 100 -> 0 with `resets_at` unchanged."""
    ok = sorted((s for s in samples if s.get('quality') == 'ok'
                 and s.get('raw_utilization') is not None),
                key=lambda s: s['source_observed_at'])
    return {cur['source_observed_at'] for prev, cur in zip(ok, ok[1:])
            if _reset_boundary_crossed(prev['raw_utilization'], cur['raw_utilization'])}


def _in_range(ts: Optional[str], start: Optional[datetime], end: Optional[datetime]) -> bool:
    """Inclusive at both ends (finding 8, P2-8): a completed window's own
    picker bounds ARE a bounding sample's source_observed_at, so an
    exclusive end silently drops that sample. The caller is responsible for
    passing source-observation time, never receipt time, as start/end."""
    dt = _parse_iso(ts)
    if dt is None:
        return False
    if start is not None and dt < start:
        return False
    if end is not None and dt > end:
        return False
    return True


# ── totals + rankings (session_fact / code_delta, no allowance samples) ────

_TOKEN_KEYS = ('input_fresh', 'input_cache_write', 'input_cache_read',
               'input_processed_total', 'output_tokens')
# Per-model category counters (session_fact / session_checkpoint `model_usage`,
# schema v7): {model: {<each key below>: cumulative tokens}}.
_MODEL_CATS = ('input_fresh', 'input_cache_write', 'input_cache_read', 'output_tokens')


def _segment_ttl(start_row: dict, end_row: dict, write_delta: Optional[int]):
    """(5m, 1h) cache-write delta for one segment, or (None, None) when the
    split cannot be established: a NULL counter at either end (older rows,
    Codex), a counter that went backwards, or a split that does not add up to
    the segment's own combined-write delta. The TTL figures are a split OF
    `input_cache_write`, never additional to it -- an unknown split is
    reported as unknown, never guessed."""
    if write_delta is None:
        return None, None
    vals = []
    for k in ('cache_write_5m', 'cache_write_1h'):
        a, b = start_row.get(k), end_row.get(k)
        if a is None or b is None or b < a:
            return None, None
        vals.append(b - a)
    if sum(vals) != write_delta:
        return None, None
    return vals[0], vals[1]


def _segment_model_usage(start_row: dict, end_row: dict, turn: dict) -> Optional[dict]:
    """{model: {cat: delta}} for one segment, or None when unknown: either
    end carries no per-model counters (pre-v7 rows, Codex), a model's counter
    went backwards, or the per-model deltas do not sum to the segment's own
    category deltas (then the per-model evidence cannot be trusted for it)."""
    a, b = start_row.get('model_usage'), end_row.get('model_usage')
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    if any(m not in b for m in a):
        return None
    out: dict = {}
    sums = dict.fromkeys(_MODEL_CATS, 0)
    for m, cats in b.items():
        base = a.get(m) or {}
        d = {}
        for k in _MODEL_CATS:
            v = int((cats or {}).get(k) or 0) - int(base.get(k) or 0)
            if v < 0:
                return None
            d[k] = v
            sums[k] += v
        if any(d.values()):
            out[m] = d
    if any(turn.get(k) is None or turn[k] != sums[k] for k in _MODEL_CATS):
        return None
    return out


def _session_provider(ck: Optional[dict], fact: Optional[dict]) -> Optional[str]:
    """The provider a session belongs to, from whichever evidence exists.
    A checkpoint's baseline is captured at dispatch (before a session_fact
    exists), so it is authoritative when present; a session with NO
    checkpoint at all (finding 2, P1-2: a housekeeping dispatch that returns
    before checkpoint capture, or an old baseline dropped by retention)
    still has a provider on its session_fact, and must not become invisible
    just because the checkpoint table has nothing for it."""
    baseline = (ck or {}).get('baseline')
    if baseline and baseline.get('provider'):
        return baseline['provider']
    if fact and fact.get('provider'):
        return fact['provider']
    completions = (ck or {}).get('completions') or []
    if completions and completions[0].get('provider'):
        return completions[0]['provider']
    return None


def _segment_delta(start_row: dict, start_at: datetime, end_row: dict, end_at: datetime) -> dict:
    """One measured segment between two consecutive checkpoints of ANY type
    (turn_start/sample_tick/completion) -- the per-key token delta plus the
    END row's own `token_coverage` (whether ITS cumulative snapshot was
    trustworthy; the start row already had to carry usable counters or the
    caller would not have paired it here). A delta needs a counter at BOTH
    ends: `None` at either side makes the key `None`, never "cumulative
    minus zero".

    A counter that went BACKWARDS is a reset, not zero usage (MC-1007): the
    MC session_id survives a provider-conversation rollover, but the
    counters are the new transcript's cumulative totals and restart low.
    The segment's true delta is then unknown, so every key is `None` and
    the segment is 'unavailable' -- never clamped to a confirmed zero."""
    turn = {'start': start_at, 'end': end_at, 'fact_only': False,
            'token_coverage': end_row.get('token_coverage') or 'unavailable'}
    reset = False
    for k in _TOKEN_KEYS:
        a, b = start_row.get(k), end_row.get(k)
        turn[k] = b - a if (a is not None and b is not None) else None
        reset = reset or (turn[k] is not None and turn[k] < 0)
    if reset:
        for k in _TOKEN_KEYS:
            turn[k] = None
    if turn['input_processed_total'] is None and turn['output_tokens'] is None:
        turn['token_coverage'] = 'unavailable'
    turn['ttl_5m'], turn['ttl_1h'] = _segment_ttl(start_row, end_row, turn['input_cache_write'])
    turn['model_usage'] = _segment_model_usage(start_row, end_row, turn)
    return turn


def _session_turns(ck: Optional[dict]) -> list[dict]:
    """[{'start','end', <each _TOKEN_KEYS delta>, 'token_coverage','fact_only'}]
    -- one entry per SEGMENT (P1-3 finding 3, extended by the sample_tick
    follow-up below), each holding that segment's OWN token delta, never the
    session's cumulative lifetime total charged again to every interval it
    overlaps.

    A turn's OUTER span is (this turn's start marker -> its completion). The
    start marker is the most recent 'turn_start' row observed_at strictly
    after the PREVIOUS completion (or baseline, for turn 1) and at-or-before
    this completion -- i.e. the turn_start a Mode B session writes when a
    follow-up send actually begins the turn (schema v5, MC-998 turn-start
    fix). When no such row exists (old data written before this fix, or a
    turn_start that was itself aged out by 90-day retention) the span falls
    back to the previous checkpoint, exactly the prior (conservative)
    behaviour: idle time between turns gets folded into the turn.

    The point of preferring turn_start: the span BETWEEN a completion and
    the next turn's turn_start -- a Mode B session sitting idle, sometimes
    for hours, between turns -- is simply never emitted as a turn or an
    unmeasured span here. It isn't zero-length coverage, it's absent: a
    calibration interval that falls entirely inside it never sees this
    session at all, so it can never straddle or block that interval.

    Schema v6 (backlog 4668eafc follow-up, sample_tick checkpoints): the
    outer (start marker -> completion) span is then SPLIT at every
    'sample_tick' row that landed strictly inside it, in timestamp order --
    one segment per (boundary -> next boundary) pair instead of one segment
    the whole turn must fit inside. A tick is stamped with the SAME
    `source_observed_at` an allowance sample used for its own reading, so a
    turn spanning several allowance samples yields one segment ending
    exactly at each interval boundary it was alive for, letting
    `_eligible_intervals` find it fully contained without requiring the
    entire turn to fit in one <=10-minute pair (measured 2026-09-29: without
    this, 165/166 shape-eligible interval pairs were refused because a
    Clayrune turn routinely outlives the sampler's own pairing window).

    With no baseline (aged out by 90-day retention while later completions
    remain) the pairs start at completions[0]; the span before it is
    unmeasured and `_session_evidence` reports it as such. A session with no
    completions yet (still running, mid-turn) has no turn evidence at all
    from this function -- `_session_evidence` handles that OPEN turn's own
    ticks separately, since there is no completion here to pair them with."""
    baseline = (ck or {}).get('baseline')
    completions = (ck or {}).get('completions') or []
    turn_starts = (ck or {}).get('turn_starts') or []
    sample_ticks = (ck or {}).get('sample_ticks') or []
    turns = []
    prev = baseline
    prev_at = _parse_iso(prev.get('observed_at')) if prev else None
    for cur in completions:
        c_at = _parse_iso(cur.get('observed_at'))
        if c_at is None:
            continue
        start_row, start_at = prev, prev_at
        used_turn_start = False
        if prev_at is not None:
            candidates = []
            for ts in turn_starts:
                ts_at = _parse_iso(ts.get('observed_at'))
                if ts_at is not None and prev_at < ts_at <= c_at:
                    candidates.append((ts_at, ts))
            if candidates:
                start_at, start_row = max(candidates, key=lambda pair: pair[0])
                used_turn_start = True
        if (not used_turn_start and start_row is not None
                and start_row.get('checkpoint_type') == 'completion'
                and all(start_row.get(k) is not None and cur.get(k) is not None
                        and start_row.get(k) == cur.get(k) for k in _TOKEN_KEYS)):
            # Defect 1 aggregator-side guard (backlog 4668eafc): a completion
            # -> completion pair with no turn_start row between them AND
            # identical cumulative totals is the phantom hour-long-idle-reap
            # checkpoint (`_log_agent_completion_body` used to write a second
            # 'completion' at reap time, ~1h after the real one, same totals
            # -- fixed at the write side 2026-09-30, but rows written before
            # that fix are already in the DB). Treating it as a real turn
            # reports an hour of idle time as a running turn that straddles
            # every calibration interval inside it (measured: 82/110 interval
            # checks refused as 'crossing'). Refuse to build ANY segment for
            # this pair -- the idle span becomes absent evidence, same as a
            # missing turn_start's idle-time handling above.
            prev, prev_at = cur, c_at
            continue
        if start_at is not None and start_row is not None:
            ticks_in_span = []
            for tk in sample_ticks:
                tk_at = _parse_iso(tk.get('observed_at'))
                if tk_at is not None and start_at < tk_at < c_at:
                    ticks_in_span.append((tk_at, tk))
            ticks_in_span.sort(key=lambda pair: pair[0])
            boundary_row, boundary_at = start_row, start_at
            for tk_at, tk in ticks_in_span:
                turns.append(_segment_delta(boundary_row, boundary_at, tk, tk_at))
                boundary_row, boundary_at = tk, tk_at
            turns.append(_segment_delta(boundary_row, boundary_at, cur, c_at))
        prev, prev_at = cur, c_at
    return turns


def _fact_is_running(fact: dict) -> bool:
    """A fact describes a live session when its status says so (the dispatch
    writer re-marks a RESUMED session's completed fact `running` at every
    turn start -- round 3, P1-2) or it has no end time at all."""
    return fact.get('status') == 'running' or not fact.get('ended_at')


def _session_evidence(ck: Optional[dict], fact: Optional[dict]) -> tuple[list[dict], list[tuple]]:
    """(measured turns, unmeasured spans) for one session. An unmeasured
    span is `(start, end)` -- either bound None for "unknown / still open"
    -- covering time the session was (or may have been) consuming tokens
    with no counter delta to show for it. Calibration and window totals
    both treat any overlap with one as incomplete coverage, never as zero
    and never as belonging to a neighbouring turn:

      - still running after its last checkpoint: a first turn (baseline, no
        completion, no fact) or a resumed turn (fact re-marked running) --
        `(last, None)`. Treating the last completion as the session's end
        is what let five overlapping intervals unlock calibration (round 3,
        P1-2).
      - the fact is AHEAD of the checkpoint history: it ended after the last
        completion row (a v2 store that discarded later completions and was
        migrated to v3, or a failed checkpoint write) -- `(last, fact_end)`;
        or it ended at the same instant with different counters, which
        makes that last turn's delta untrustworthy (round 3, P1-3b).
      - no baseline but completions (baseline aged out by retention):
        everything before the first completion.
      - closed by `reconcile_dead_sessions` (status 'ended_unknown', the
        session died mid-turn): `(last, ended_at)` through the branch
        above, ended_at being when it was observed gone -- bounded, so it
        no longer overlaps anything after that.

    Schema v6 (backlog 4668eafc follow-up, sample_tick checkpoints): before
    any of the above, every 'sample_tick' row observed strictly after `last`
    further measures the still-open turn in boundary-aligned segments (same
    `_segment_delta` pairing `_session_turns` uses for closed turns) and
    pushes `last`/`last_at` forward to the LATEST tick -- only the span
    after that final tick is genuinely unmeasured/open. A session with no
    ticks after `last` behaves exactly as before (the whole open span is one
    unmeasured tuple); this is what lets a long-running or crashed
    (ended_unknown) session's PRIOR ticked segments count toward calibration
    even though its final, tick-less tail still can't.

    A fact with no checkpoint at all (housekeeping, pre-checkpoint history)
    is one `fact_only` turn from started_at to ended_at carrying the fact's
    own totals once it has finished, so window totals can still show it;
    calibration never trusts such a turn. Without a start time it has no
    placeable span and is skipped, as before."""
    baseline = (ck or {}).get('baseline')
    completions = (ck or {}).get('completions') or []
    turn_starts = (ck or {}).get('turn_starts') or []
    sample_ticks = (ck or {}).get('sample_ticks') or []
    if not baseline and not completions:
        if not fact:
            return [], []
        f_start, f_end = _parse_iso(fact.get('started_at')), _parse_iso(fact.get('ended_at'))
        if f_start is None:
            return [], []
        if _fact_is_running(fact) or f_end is None:
            return [], [(f_start, None)]
        if fact.get('status') == 'ended_unknown':
            return [], [(f_start, f_end)]  # closed by reconcile: never a measured turn
        turn = {'start': f_start, 'end': f_end, 'fact_only': True,
                'token_coverage': fact.get('token_coverage') or 'unavailable'}
        for k in _TOKEN_KEYS:
            turn[k] = fact.get(k)
        zero = {'cache_write_5m': 0, 'cache_write_1h': 0, 'model_usage': {}}
        turn['ttl_5m'], turn['ttl_1h'] = _segment_ttl(zero, fact, turn['input_cache_write'])
        turn['model_usage'] = _segment_model_usage(zero, fact, turn)
        return [turn], []

    turns = _session_turns(ck)
    unmeasured: list[tuple] = []
    if not baseline:
        unmeasured.append((_parse_iso(fact.get('started_at')) if fact else None,
                           _parse_iso(completions[0].get('observed_at'))))
    completion_last = (completions[-1] if completions else baseline) or {}
    completion_last_at = _parse_iso(completion_last.get('observed_at'))
    # MC-998 turn-start fix: a turn_start written AFTER the last completion
    # (or after baseline, for a session still on turn 1) marks a NEW turn
    # that has begun but not yet completed -- the "still running"/"ahead of
    # checkpoint history" spans below must start there, not at the last
    # completion, or the idle time before that turn_start gets folded into
    # "unmeasured" and blocks every interval it happened to sit through --
    # exactly the bug this fix removes from the measured-turn path above.
    open_start_at, open_start = None, None
    for ts in turn_starts:
        ts_at = _parse_iso(ts.get('observed_at'))
        if ts_at is not None and completion_last_at is not None and ts_at > completion_last_at:
            if open_start_at is None or ts_at > open_start_at:
                open_start_at, open_start = ts_at, ts
    last = open_start if open_start is not None else completion_last
    last_at = open_start_at if open_start is not None else completion_last_at

    # Schema v6 follow-up: measure the still-open turn's OWN ticks, in
    # timestamp order, exactly like `_session_turns` splits a closed turn --
    # only the span after the final tick stays in `unmeasured` below.
    if last_at is not None:
        open_ticks = []
        for tk in sample_ticks:
            tk_at = _parse_iso(tk.get('observed_at'))
            if tk_at is not None and tk_at > last_at:
                open_ticks.append((tk_at, tk))
        open_ticks.sort(key=lambda pair: pair[0])
        for tk_at, tk in open_ticks:
            turns.append(_segment_delta(last, last_at, tk, tk_at))
            last, last_at = tk, tk_at

    if (fact and _fact_is_running(fact)) or (not fact and not completions):
        unmeasured.append((last_at, None))
    elif fact:
        f_end = _parse_iso(fact.get('ended_at'))
        if last_at is not None and f_end is not None:
            if not completions or f_end > last_at:
                unmeasured.append((last_at, f_end))
            elif f_end == last_at and any(fact.get(k) != last.get(k)
                                          for k in ('input_processed_total', 'output_tokens')):
                if turns:
                    t = turns.pop()
                    unmeasured.append((t['start'], t['end']))
    return turns, unmeasured


def _window_overlaps(s: Optional[datetime], e: Optional[datetime],
                     start: Optional[datetime], end: Optional[datetime]) -> bool:
    """Span (s, e] shares time with the window [start, end]. Touching at one
    instant is NOT overlap: a turn that completed exactly at a window's
    start belongs to the window before it, never to both."""
    return ((s is None or end is None or s < end)
            and (e is None or start is None or e > start))


def _window_contains(t: dict, start: Optional[datetime], end: Optional[datetime]) -> bool:
    return (start is None or t['start'] >= start) and (end is None or t['end'] <= end)


def _scoped_row(row: dict, fact: Optional[dict], window_scope: str) -> Optional[dict]:
    """Restrict a rolled-up session row to `window_scope` (the dashboard's
    All / Opus / Sonnet selector), using the same `_scope_matches` the
    calibration uses. Returns the row (possibly narrowed to the matching
    models of a mixed session), None when none of the session belongs to the
    scope, or the row flagged `_scope_unknown` when part of its tokens carry
    no model evidence at all -- never silently included, never silently
    dropped: `compute_totals` leaves a flagged row out of the sums and counts
    it in `model_unknown_session_count`."""
    mu = row.get('model_usage') or {}
    unsplit = row.get('_model_unsplit') or {}
    whole = _scope_matches(fact, window_scope)
    has_unsplit = any(unsplit.get(k) for k in _MODEL_CATS)
    if has_unsplit and whole is None:
        row['_scope_unknown'] = True
        return row
    kept = {m: c for m, c in mu.items() if _scope_matches({'observed_model': m}, window_scope)}
    if not kept and whole is not True:
        return None
    parts = dict.fromkeys(_MODEL_CATS, 0)
    for c in kept.values():
        for k in _MODEL_CATS:
            parts[k] += c.get(k, 0)
    if whole is True:
        for k in _MODEL_CATS:
            parts[k] += unsplit.get(k, 0)
    if len(kept) != len(mu) or (whole is not True and has_unsplit):
        # A narrowed mixed session: LOC cannot be split by model, and the
        # per-model counters carry no TTL split, so the narrowed write total
        # has an unknown TTL.
        row['_loc_attributable'] = False
        if row.get('cache_write_5m') is not None or row.get('cache_write_ttl_unknown'):
            row['cache_write_5m'] = row['cache_write_1h'] = None
            row['cache_write_ttl_unknown'] = parts['input_cache_write']
    row['input_fresh'] = parts['input_fresh']
    row['input_cache_write'] = parts['input_cache_write']
    row['input_cache_read'] = parts['input_cache_read']
    row['input_processed_total'] = parts['input_fresh'] + parts['input_cache_write'] + parts['input_cache_read']
    row['output_tokens'] = parts['output_tokens']
    row['model_usage'] = kept or None
    row['_model_unsplit'] = unsplit if whole is True else {}
    return row


def filter_facts_in_range(session_facts: list[dict], checkpoints: dict[str, dict], *, provider: str,
                           range_start: Optional[str], range_end: Optional[str],
                           window_scope: str = 'all') -> tuple[list[dict], int]:
    """Per-session rows carrying only the token deltas MEASURED inside
    [range_start, range_end], plus a count of same-provider sessions whose
    in-range work could not be measured.

    Round 3 finding 4 (P2-4, docs/_journal/4668eafc-mc998-fenn-review.md):
    a window's totals come from the session's appended checkpoint HISTORY.
    Each turn (checkpoint -> next checkpoint) fully inside the range
    contributes its own delta, so a two-turn session shows its later
    400-token turn in the later window -- not "not yet measurable", and not
    its 500-token lifetime total. Only what cannot be established is
    withheld: a turn straddling a range boundary, or any unmeasured span
    (`_session_evidence`) overlapping the range, marks the session
    incomplete. A session can do both -- contribute its contained turns AND
    count as incomplete.

    Sessions come from the UNION of facts and checkpoints: a first-turn
    session has a baseline checkpoint and no fact yet, and must show as
    incomplete instead of leaving the window reading "No runs" (round 3,
    P1-3a).

    Each row is a copy of the session's fact (or a bare row for a fact-less
    session) with its token fields replaced by the in-range sums.
    `_loc_attributable` is True only when the session's ENTIRE evidence lies
    inside the range: code_delta is a per-session lifetime count that cannot
    be split by turn, so every other row reports its LOC as unavailable.

    `window_scope` ('all' / 'opus' / 'sonnet') narrows every row to that
    model class (`_scoped_row`). Each row also carries the cache-write TTL
    roll-up (`cache_write_5m` / `cache_write_1h` -- None when no segment's
    split is known -- and `cache_write_ttl_unknown`, the combined-write
    tokens whose split is unknown) and the per-model roll-up (`model_usage`
    over segments with per-model evidence, `_model_unsplit` over the rest).

    Returns (rows, incomplete_session_count)."""
    start = _parse_iso(range_start)
    end = _parse_iso(range_end)
    facts_by_session = {f['session_id']: f for f in session_facts if f.get('session_id')}
    rows: list[dict] = []
    incomplete = 0
    for sid in list(facts_by_session) + [s for s in checkpoints if s not in facts_by_session]:
        fact = facts_by_session.get(sid)
        ck = checkpoints.get(sid)
        if _session_provider(ck, fact) != provider:
            continue
        turns, unmeasured = _session_evidence(ck, fact)
        contained = [t for t in turns if _window_contains(t, start, end)]
        crossing = [t for t in turns if t not in contained
                    and _window_overlaps(t['start'], t['end'], start, end)]
        if crossing or any(_window_overlaps(s, e, start, end) for s, e in unmeasured):
            incomplete += 1
        if not contained:
            continue
        row = dict(fact) if fact else {'session_id': sid, 'provider': provider}
        counted = [t for t in contained if t['token_coverage'] != 'unavailable']
        for k in _TOKEN_KEYS:
            vals = [t[k] for t in counted if t[k] is not None]
            row[k] = sum(vals) if vals else None
        coverages = {t['token_coverage'] for t in contained}
        row['token_coverage'] = ('complete' if coverages == {'complete'}
                                 else 'unavailable' if not counted else 'partial')
        row['_loc_attributable'] = len(contained) == len(turns) and not unmeasured
        ttl_known = [t for t in counted if t.get('ttl_5m') is not None]
        row['cache_write_5m'] = sum(t['ttl_5m'] for t in ttl_known) if ttl_known else None
        row['cache_write_1h'] = sum(t['ttl_1h'] for t in ttl_known) if ttl_known else None
        row['cache_write_ttl_unknown'] = sum(t['input_cache_write'] or 0
                                             for t in counted if t.get('ttl_5m') is None)
        mu_total: dict = {}
        unsplit = dict.fromkeys(_MODEL_CATS, 0)
        split_segments = 0
        for t in counted:
            if t.get('model_usage') is None:
                for k in _MODEL_CATS:
                    unsplit[k] += t[k] or 0
                continue
            split_segments += 1
            for m, cats in t['model_usage'].items():
                acc = mu_total.setdefault(m, dict.fromkeys(_MODEL_CATS, 0))
                for k in _MODEL_CATS:
                    acc[k] += cats.get(k, 0)
        row['model_usage'] = mu_total if split_segments else None
        row['_model_unsplit'] = unsplit
        if window_scope != 'all':
            row = _scoped_row(row, fact, window_scope)
            if row is None:
                continue
        rows.append(row)
    return rows, incomplete


def _row_ttl(f: dict) -> tuple:
    """(5m, 1h, unknown) cache-write tokens for one row. Rows from
    `filter_facts_in_range` carry the roll-up; a hand-built or whole-fact row
    with only `cache_write_5m`/`cache_write_1h` counts as known when both are
    set, otherwise its whole write total is unknown."""
    if 'cache_write_ttl_unknown' in f:
        return f.get('cache_write_5m'), f.get('cache_write_1h'), f.get('cache_write_ttl_unknown') or 0
    t5, t1 = f.get('cache_write_5m'), f.get('cache_write_1h')
    if t5 is not None and t1 is not None:
        return t5, t1, 0
    return None, None, f.get('input_cache_write') or 0


def compute_totals(facts: list[dict], code_deltas: dict[str, dict], *,
                    incomplete_coverage_session_count: int = 0) -> dict:
    """Sum token/LOC categories across `facts`. A field stays `None` (not 0)
    when every contributing session had `token_coverage='unavailable'` for
    it -- a reported 0 must mean a confirmed zero, per the spec's "no
    fabricated zero" rule. `incomplete_coverage_session_count` (finding 4)
    surfaces sessions that overlap the range but whose in-range token delta
    could not be measured, so their exclusion from the sums above is visible
    rather than looking like they simply didn't exist."""
    sums = {'input_fresh': 0, 'input_cache_write': 0, 'input_cache_read': 0,
            'input_processed_total': 0, 'output_tokens': 0}
    have_any_token_data = False
    unavailable_token_rows = 0
    ttl_5m = ttl_1h = ttl_unknown = 0
    ttl_known_any = False
    # A row flagged `_scope_unknown` (filter_facts_in_range under a model
    # scope) has tokens with no model evidence: out of every sum below, but
    # counted so the exclusion is visible.
    model_unknown = sum(1 for f in facts if f.get('_scope_unknown'))
    facts = [f for f in facts if not f.get('_scope_unknown')]
    for f in facts:
        if f.get('token_coverage') == 'unavailable':
            unavailable_token_rows += 1
            continue
        have_any_token_data = True
        for k in sums:
            v = f.get(k)
            if v is not None:
                sums[k] += v
        t5, t1, unk = _row_ttl(f)
        if t5 is not None:
            ttl_known_any = True
            ttl_5m += t5
            ttl_1h += t1
        ttl_unknown += unk

    loc_added = loc_deleted = 0
    have_any_loc_data = False
    loc_unavailable_rows = 0
    for f in facts:
        cd = code_deltas.get(f.get('session_id') or '') if f.get('_loc_attributable', True) else None
        if not cd or cd.get('status') != 'ok':
            loc_unavailable_rows += 1
            continue
        have_any_loc_data = True
        loc_added += cd.get('added') or 0
        loc_deleted += cd.get('deleted') or 0

    tokens = {k: (v if have_any_token_data else None) for k, v in sums.items()}
    # The TTL figures are a split OF input_cache_write, never added to it.
    # 5m/1h stay None (unknown, not 0) until some segment's split is known.
    tokens['cache_write_5m'] = ttl_5m if have_any_token_data and ttl_known_any else None
    tokens['cache_write_1h'] = ttl_1h if have_any_token_data and ttl_known_any else None
    tokens['cache_write_ttl_unknown'] = ttl_unknown if have_any_token_data else None
    return {
        'session_count': len(facts),
        'model_unknown_session_count': model_unknown,
        'tokens': tokens,
        'token_coverage_unavailable_count': unavailable_token_rows,
        'loc': {'added': loc_added if have_any_loc_data else None,
                'deleted': loc_deleted if have_any_loc_data else None},
        'loc_unavailable_count': loc_unavailable_rows,
        'incomplete_coverage_session_count': incomplete_coverage_session_count,
    }


def compute_rankings(facts: list[dict], code_deltas: dict[str, dict], *,
                      dimension: str, sort_by: str = 'input') -> dict:
    """Group `facts` by `dimension` (one of RANKING_DIMENSIONS), summing
    tokens/LOC per group. Returns {'rows': [...], 'unknown_count': int,
    'missing_data_count': int} per the spec's "Show `Unknown` group and
    missing-data row counts for each dimension."
    """
    if dimension not in RANKING_DIMENSIONS:
        raise ValueError(f'unknown ranking dimension: {dimension}')
    key = _DIMENSION_FACT_KEY[dimension]
    groups: dict[str, dict] = {}
    unknown_count = 0
    missing_data_count = 0
    mixed_session_count = 0
    whole_session_count = 0

    def _group(label: str) -> dict:
        return groups.setdefault(label, {
            'label': label, 'input_processed_total': 0, 'output_tokens': 0,
            'input_fresh': 0, 'input_cache_write': 0, 'input_cache_read': 0,
            'added': 0, 'session_count': 0, 'has_token_data': False, 'has_loc_data': False,
        })

    for f in facts:
        if f.get('_scope_unknown'):
            continue  # counted in totals.model_unknown_session_count, not ranked
        has_tokens = f.get('token_coverage') != 'unavailable'
        if not has_tokens:
            missing_data_count += 1
        cd = code_deltas.get(f.get('session_id') or '') if f.get('_loc_attributable', True) else None
        loc_ok = bool(cd and cd.get('status') == 'ok')

        if dimension == 'model' and has_tokens:
            # A session that ran under several models is split ACROSS them by
            # its per-model counters (schema v7); segments without that
            # evidence (older rows, Codex) stay attributed whole to the
            # session's observed model, as before, and are counted so the
            # UI can say so.
            mu = f.get('model_usage') or {}
            unsplit = f.get('_model_unsplit')
            if unsplit is None and not mu:      # hand-built row: whole session
                unsplit = {k: f.get(k) or 0 for k in _MODEL_CATS}
            unsplit = unsplit or {}
            portions = {m: dict(c) for m, c in mu.items()}
            if any(unsplit.get(k) for k in _MODEL_CATS) or not portions:
                label = f.get(key) or 'Unknown'
                if label == 'Unknown':
                    unknown_count += 1
                whole_session_count += 1
                p = portions.setdefault(label, dict.fromkeys(_MODEL_CATS, 0))
                for k in _MODEL_CATS:
                    p[k] += unsplit.get(k, 0)
            if len(mu) > 1:
                mixed_session_count += 1
            for label, c in portions.items():
                g = _group(label)
                g['session_count'] += 1
                g['has_token_data'] = True
                g['input_fresh'] += c['input_fresh']
                g['input_cache_write'] += c['input_cache_write']
                g['input_cache_read'] += c['input_cache_read']
                g['input_processed_total'] += c['input_fresh'] + c['input_cache_write'] + c['input_cache_read']
                g['output_tokens'] += c['output_tokens']
                if loc_ok and len(portions) == 1:
                    g['has_loc_data'] = True
                    g['added'] += cd.get('added') or 0
            continue

        label = f.get(key) or 'Unknown'
        if label == 'Unknown':
            unknown_count += 1
        g = _group(label)
        g['session_count'] += 1
        if has_tokens:
            g['has_token_data'] = True
            g['input_processed_total'] += f.get('input_processed_total') or 0
            g['output_tokens'] += f.get('output_tokens') or 0
            g['input_fresh'] += f.get('input_fresh') or 0
            g['input_cache_write'] += f.get('input_cache_write') or 0
            g['input_cache_read'] += f.get('input_cache_read') or 0
        if loc_ok:
            g['has_loc_data'] = True
            g['added'] += cd.get('added') or 0

    sort_key = {'input': 'input_processed_total', 'output': 'output_tokens',
                'added': 'added'}.get(sort_by, 'input_processed_total')
    rows = sorted(groups.values(), key=lambda g: g[sort_key], reverse=True)
    for r in rows:
        for k in ('input_processed_total', 'output_tokens', 'input_fresh',
                  'input_cache_write', 'input_cache_read'):
            r[k] = r[k] if r['has_token_data'] else None
        r['added'] = r['added'] if r['has_loc_data'] else None
        del r['has_token_data']
        del r['has_loc_data']
    out = {'rows': rows, 'unknown_count': unknown_count, 'missing_data_count': missing_data_count}
    if dimension == 'model':
        out['mixed_session_count'] = mixed_session_count
        out['whole_session_attribution_count'] = whole_session_count
    return out


# ── tokens-per-point calibration (allowance_sample pairs + session_checkpoint) ─

def _scope_matches(fact: Optional[dict], window_scope: str) -> Optional[bool]:
    """True/False when determinable from the fact's observed_model, None
    when it can't be determined yet (no session_fact -- a still-running
    session whose baseline checkpoint exists but hasn't completed)."""
    if window_scope == 'all':
        return True
    if not fact:
        return None
    model = (fact.get('observed_model') or '').lower()
    if not model:
        return None
    if window_scope == 'opus':
        return 'opus' in model
    if window_scope == 'sonnet':
        return 'sonnet' in model
    return None


def _eligible_intervals(samples: list[dict], checkpoints: dict[str, dict], facts_by_session: dict[str, dict],
                         *, provider: str, window_scope: str) -> list[dict]:
    """Consecutive fresh-sample pairs meeting the spec's eligibility rule:
    same provider/window/scope identity (guaranteed -- `samples` is already
    scoped to one), <=10 minutes apart, non-negative delta (0 IS kept --
    see below), quality 'ok' at both endpoints, no reset crossed (same
    `resets_at`).

    Follow-up 2 (pooled estimator): pairs with `delta == 0` are returned too.
    `raw_utilization` is whole points, so a pair's delta is 0 for most pairs
    and 1 on the minute the counter ticks over; the consumption behind a tick
    accrued over the delta-0 pairs before it. Keeping only delta>=1 pairs
    paired a whole bucket's worth of vendor movement with one minute's tokens
    and read ~5x low. Callers pool tokens and delta across ALL of these; a
    single pair's tokens/delta is meaningless and is never taken.

    Per finding 2 (P1-2, "2026-09-28 re-review"): the ORIGINAL fix only
    walked `checkpoints.items()`, so a session with NO checkpoint row at all
    (a housekeeping dispatch that returns before checkpoint capture, an old
    baseline dropped by 90-day retention, or a pre-checkpoint historical
    fact) was invisible to this loop -- it could overlap an interval that
    then still reported "exactly one active session" and unlocked
    calibration. This walks the UNION of `checkpoints` and
    `facts_by_session` instead, falling back to a fact's own
    started_at/ended_at for overlap detection when no checkpoint exists --
    such a session can never be calibratable (no turn-level delta to trust)
    but its mere overlap still marks the interval incomplete, exactly like a
    still-running or partially-overlapping session does.

    Per finding 3 (P1-3, "2026-09-28 re-review"): a session's lifetime may
    now span several completion checkpoints (one per TURN, not one per
    session). `_session_turns`/`_session_evidence` derive each SEGMENT's own
    delta; a segment is only counted toward an interval when it is FULLY
    CONTAINED in [t_a, t_b]. Any segment that overlaps the interval WITHOUT
    being fully contained (the session was mid-turn across a boundary) marks
    the interval incomplete -- its tokens cannot be split between intervals,
    so they are withheld here rather than fabricated as belonging to this
    one. A session can contribute segments to more than one interval; each
    interval only ever sees the segments that actually happened inside it.

    Backlog 4668eafc follow-up (schema v6, sample_tick checkpoints): a
    Clayrune turn routinely outlives this function's own <=10-minute pairing
    window and up to 9 sessions run at once, so requiring the WHOLE turn to
    fit inside one interval left almost nothing containable (measured
    2026-09-29: 165/166 shape-eligible pairs refused). `_session_turns` now
    splits a turn at every 'sample_tick' row -- stamped with the SAME
    `source_observed_at` an allowance sample used for t_a/t_b -- into
    segments that end exactly at each interval boundary the turn was alive
    for, so a long turn contributes several small contained segments instead
    of one that can never fit. A session with no tick at BOTH t_a and t_b
    still can't be measured for that interval and correctly stays excluded.
    """
    out = []
    ordered = sorted((s for s in samples if s.get('quality') == 'ok'
                       and s.get('raw_utilization') is not None
                       and s.get('source_observed_at')),
                      key=lambda s: s['source_observed_at'])
    all_sids = set(checkpoints.keys()) | set(facts_by_session.keys())
    # A session's evidence doesn't depend on the interval, so derive it once.
    # Follow-up 2 keeps delta-0 pairs, which took the per-interval loop from
    # ~350 to ~2000 pairs; re-deriving evidence per pair put the breakdown
    # route at 16s+ and the dashboard gave up on it.
    evidence = []
    for sid in all_sids:
        ck = checkpoints.get(sid)
        fact = facts_by_session.get(sid)
        if _session_provider(ck, fact) != provider:
            continue
        scope_ok = _scope_matches(fact, window_scope)
        if scope_ok is False:
            continue  # confirmed different model scope -- not this window's class
        # Round 3 P1-2/P1-3b: unmeasured spans (a still-running first or
        # RESUMED turn after the last completion, a fact ahead of its
        # checkpoint history) overlap exactly like a session does --
        # the last completion is not the session's end.
        turns, unmeasured = _session_evidence(ck, fact)
        evidence.append((sid, scope_ok, turns, unmeasured))
    for a, b in zip(ordered, ordered[1:]):
        if not _same_reset(a.get('resets_at'), b.get('resets_at')):
            continue  # a reset happened between these two readings
        if _reset_boundary_crossed(a['raw_utilization'], b['raw_utilization']):
            continue  # resets_at hadn't caught up yet (defect 3) -- still a reset
        t_a, t_b = _parse_iso(a['source_observed_at']), _parse_iso(b['source_observed_at'])
        if t_a is None or t_b is None or t_b <= t_a:
            continue
        if (t_b - t_a) > timedelta(minutes=_MAX_INTERVAL_MINUTES):
            continue
        delta = b['raw_utilization'] - a['raw_utilization']
        if delta < 0:
            continue  # sub-tolerance dip (<= _RESET_DROP_TOLERANCE_PP): not a measurable rate

        session_ids: set[str] = set()
        coverage_complete = True
        input_processed_total = 0
        output_tokens = 0
        for sid, scope_ok, turns, unmeasured in evidence:
            # `_window_overlaps` (exclusive touch -- a span ending exactly at
            # t_a belongs to the PRIOR interval, not this one), not a
            # `>=`/inclusive check: schema v6 sample_tick segments are, by
            # construction, stamped at the SAME source_observed_at as the
            # allowance samples that define t_a/t_b, so a segment ending
            # exactly at t_a is the routine case now, not an edge case. An
            # inclusive check here made EVERY interval's immediately
            # preceding segment misread as "crossing" it.
            gap = any(_window_overlaps(s, e, t_a, t_b) for s, e in unmeasured)
            contained = [t for t in turns if t['start'] >= t_a and t['end'] <= t_b]
            crossing = [t for t in turns if t not in contained
                        and _window_overlaps(t['start'], t['end'], t_a, t_b)]
            if not (gap or contained or crossing):
                continue  # doesn't overlap this interval at all
            session_ids.add(sid)
            if scope_ok is None:
                coverage_complete = False  # scope unconfirmed -- unmeasurable
                continue
            if gap or crossing:
                coverage_complete = False  # unmeasured time, or a turn straddles the boundary
                continue
            if any(t['token_coverage'] != 'complete' or t['fact_only']
                   or t['input_processed_total'] is None or t['output_tokens'] is None
                   for t in contained):
                coverage_complete = False  # a fact-only span is never a measured delta
                continue
            for t in contained:
                input_processed_total += t['input_processed_total']
                output_tokens += t['output_tokens']

        out.append({
            'start': t_a, 'end': t_b, 'delta_pp': delta,
            'session_ids': session_ids,
            'coverage_complete': coverage_complete,
            'input_processed_total': input_processed_total,
            'output_tokens': output_tokens,
        })
    return out


def _contributing(iv: dict) -> bool:
    """An interval contributes to calibration when it is fully measured, has
    at least one Clayrune session in it, and moves at least one side of the
    ratio (tokens or vendor points). A measured pair with no tokens and no
    movement adds nothing to either sum and must not pad the count gate."""
    return bool(iv['coverage_complete'] and iv['session_ids']
                and (iv['input_processed_total'] + iv['output_tokens'] > 0 or iv['delta_pp'] > 0))


def _contiguous_runs(intervals: list[dict]) -> list[list[dict]]:
    """Group contributing intervals into maximal runs where each one starts
    exactly where the previous ended (adjacent allowance samples, nothing
    excluded between them). Used as the bootstrap's resampling unit."""
    runs: list[list[dict]] = []
    for iv in sorted(intervals, key=lambda i: i['start']):
        if runs and runs[-1][-1]['end'] == iv['start']:
            runs[-1].append(iv)
        else:
            runs.append([iv])
    return runs


def _pooled_ratio_range(runs: list[list[dict]], *, numerator) -> tuple[float, float]:
    """10th-90th percentile of the pooled ratio under a BLOCK bootstrap over
    contiguous runs: resample whole runs with replacement, pool
    sum(numerator)/sum(delta) on each resample. Runs, not single pairs, are
    the unit because adjacent pairs share a quantisation bucket (a tick is
    paid for by the pairs before it), so pairs are not independent; a run is
    the largest span the data lets us treat as exchangeable. This is an
    observed-variation range for the ratio, not a confidence interval and
    not a bound on outside use. Resamples whose delta sums to 0 have no
    ratio and are skipped."""
    rng = random.Random(_BOOTSTRAP_SEED)
    sums = [(sum(numerator(iv) for iv in r), sum(iv['delta_pp'] for iv in r)) for r in runs]
    n = len(sums)
    ratios = []
    for _ in range(_BOOTSTRAP_RESAMPLES):
        tok = delta = 0.0
        for _k in range(n):
            t, d = sums[rng.randrange(n)]
            tok += t
            delta += d
        if delta > 0:
            ratios.append(tok / delta)
    if not ratios:
        raise ValueError('no bootstrap resample had a positive delta')
    ratios.sort()
    return _percentile(ratios, _BOOTSTRAP_LOW_PCT), _percentile(ratios, _BOOTSTRAP_HIGH_PCT)


def compute_calibration(samples: list[dict], checkpoints: dict[str, dict], facts_by_session: dict[str, dict],
                         *, provider: str, window_scope: str) -> dict:
    """Pooled workload-per-point and input-per-point over the eligible
    intervals in the given (already 90-day-scoped) history.

    ESTIMATOR (backlog 4668eafc follow-up 2): ratio of sums --
    sum(tokens) / sum(delta_pp) over every coverage-complete, session-bearing
    interval, INCLUDING delta-0 intervals. The previous estimator took the
    median of per-interval tokens/delta over delta>=1 pairs only; because
    `raw_utilization` is whole points those pairs are the minute the counter
    ticked, whose consumption accrued over the delta-0 pairs before it, so it
    read ~5x low on live data (estimate 171pp against 47pp observed).

    GATE: >= 5 contributing intervals (`_contributing`: fully measured,
    session-bearing, moves tokens or points -- a zero/zero pair does not pad
    the count), >= 3 distinct sessions among them, AND >= 5pp of total vendor
    delta and non-zero total tokens across them. An interval with zero
    overlapping sessions contributes nothing (its delta belongs to
    `Unattributed activity`) but is not itself an error.

    RANGE: `*_lo`/`*_hi` are the 10th/90th percentile of the pooled ratio
    under a block bootstrap over contiguous runs (`_pooled_ratio_range`),
    not the spread of single-pair ratios the old p10/p90 reported.
    """
    intervals = _eligible_intervals(samples, checkpoints, facts_by_session,
                                     provider=provider, window_scope=window_scope)
    # Ron, 2026-09-29: any number of concurrent Clayrune sessions may share an
    # interval. Tokens-per-point is a property of the plan, not of concurrency:
    # the interval already sums every overlapping session's measured delta, and
    # coverage_complete refuses the interval if ANY of them is unmeasured. The
    # old single-session rule left ~17% of intervals usable on a box that runs
    # up to 9 agents at once, so calibration never qualified.
    calibratable = [iv for iv in intervals if _contributing(iv)]
    distinct_sessions = set().union(*(iv['session_ids'] for iv in calibratable)) if calibratable else set()
    total_delta = sum(iv['delta_pp'] for iv in calibratable)
    total_input = sum(iv['input_processed_total'] for iv in calibratable)
    total_workload = total_input + sum(iv['output_tokens'] for iv in calibratable)
    if (len(calibratable) < _MIN_ELIGIBLE_INTERVALS or len(distinct_sessions) < _MIN_ELIGIBLE_SESSIONS
            or total_delta < _MIN_CALIBRATION_TOTAL_PP or total_workload <= 0):
        return {'status': 'insufficient_samples', 'eligible_interval_count': len(calibratable),
                'distinct_session_count': len(distinct_sessions), 'total_delta_pp': total_delta,
                'all_intervals': intervals}

    runs = _contiguous_runs(calibratable)
    workload_lo, workload_hi = _pooled_ratio_range(
        runs, numerator=lambda iv: iv['input_processed_total'] + iv['output_tokens'])
    input_lo, input_hi = _pooled_ratio_range(runs, numerator=lambda iv: iv['input_processed_total'])
    return {
        'status': 'ok',
        'eligible_interval_count': len(calibratable),
        'distinct_session_count': len(distinct_sessions),
        'total_delta_pp': total_delta,
        'run_count': len(runs),
        'estimator': 'pooled_ratio',
        'range_method': 'block_bootstrap_p10_p90',
        'workload_per_point': total_workload / total_delta,
        'workload_per_point_lo': workload_lo,
        'workload_per_point_hi': workload_hi,
        'input_per_point': total_input / total_delta,
        'input_per_point_lo': input_lo,
        'input_per_point_hi': input_hi,
        'all_intervals': intervals,
    }


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        raise ValueError('empty distribution')
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (pct / 100.0) * (len(sorted_values) - 1)
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    frac = k - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def compute_bar_change(samples: list[dict], *, range_start: Optional[str],
                        range_end: Optional[str]) -> dict:
    """`later.raw_utilization - earlier.raw_utilization` for the fresh
    readings within [range_start, range_end), only when no reset boundary
    was crossed (constant `resets_at` across the range, AND no raw_utilization
    drop bigger than sampling noise -- defect 3, backlog 4668eafc follow-up:
    `resets_at` can lag the actual reset by hours, so a range straddling a
    real reset with a stale-but-constant `resets_at` used to fall through to
    a fabricated negative `delta_pp` -- measured -64pp on the 7d window --
    instead of `reset_crossed`)."""
    start, end = _parse_iso(range_start), _parse_iso(range_end)
    in_range = sorted(
        (s for s in samples if s.get('quality') == 'ok' and s.get('raw_utilization') is not None
         and _in_range(s.get('source_observed_at'), start, end)),
        key=lambda s: s['source_observed_at'])
    if len(in_range) < 2:
        return {'status': 'insufficient_samples', 'delta_pp': None}
    anchor = in_range[0].get('resets_at')
    if any(not _same_reset(anchor, s.get('resets_at')) for s in in_range[1:]):
        return {'status': 'reset_crossed', 'delta_pp': None}
    if any(_reset_boundary_crossed(prev['raw_utilization'], cur['raw_utilization'])
           for prev, cur in zip(in_range, in_range[1:])):
        return {'status': 'reset_crossed', 'delta_pp': None}
    delta = in_range[-1]['raw_utilization'] - in_range[0]['raw_utilization']
    return {'status': 'ok', 'delta_pp': delta,
            'earliest': in_range[0]['source_observed_at'], 'latest': in_range[-1]['source_observed_at']}


def compute_segmented_bar(bar_change: dict, totals: dict, calibration: dict) -> dict:
    """One segmented-bar estimate: [estimated Clayrune contribution,
    Unattributed / uncertain]. Per spec: no valid calibration -> the WHOLE
    bar change is Unattributed; estimate exceeding the observed delta is
    flagged, never clamped or inverted into a fabricated negative bucket.
    """
    if bar_change.get('status') != 'ok':
        return {'status': bar_change.get('status', 'unavailable'), 'estimated_pp': None,
                'unattributed_pp': None, 'range_pp': None}
    delta = bar_change['delta_pp']
    if calibration.get('status') != 'ok':
        return {'status': 'insufficient_calibration', 'estimated_pp': None,
                'unattributed_pp': delta, 'range_pp': None, 'bar_change_pp': delta}

    processed_plus_output = ((totals['tokens'].get('input_processed_total') or 0)
                              + (totals['tokens'].get('output_tokens') or 0))
    # ONE workload definition (processed input + output) in numerator and in
    # the calibration denominator. The old low end divided fresh input +
    # output (6M of 1.26B) by a rate calibrated on ALL input + output, so it
    # measured a different thing and the range read 0.1%-41%. The range is
    # the calibration rate's observed variation (p10/p90 of the pooled
    # workload-per-point), not bounds on what Clayrune work was attributed.
    estimated_pp = processed_plus_output / calibration['workload_per_point']
    low_pp = processed_plus_output / calibration['workload_per_point_hi']
    high_pp = processed_plus_output / calibration['workload_per_point_lo']
    unattributed = delta - estimated_pp
    status = 'ok'
    if unattributed < 0:
        status = 'estimate_exceeds_observed'
    return {
        'status': status, 'bar_change_pp': delta, 'estimated_pp': estimated_pp,
        'unattributed_pp': max(unattributed, 0.0) if status == 'ok' else unattributed,
        'range_pp': [low_pp, high_pp],
    }


# ── dashboard assembly ──────────────────────────────────────────────────────

def build_breakdown(*, provider: str, window_kind: str, window_scope: str,
                     range_start: Optional[str], range_end: Optional[str],
                     dimension: str, sort_by: str,
                     range_samples: list[dict], calibration_samples: list[dict],
                     session_facts: list[dict], checkpoints: dict[str, dict],
                     code_deltas: dict[str, dict], coverage_begins: Optional[str]) -> dict:
    """Assemble one provider/window/range Breakdown payload. Caller (the
    Flask route) is responsible for fetching `range_samples` (this
    provider/window/scope's allowance_sample rows for the display range),
    `calibration_samples` (the full 90-day history for calibration, NOT
    range-limited), `session_facts` + `checkpoints` (session_id ->
    {'baseline':row,'completion':row}, also the full 90-day history --
    calibration and the window-totals fix both need to see a session's
    checkpoint pair regardless of the display range), and `code_deltas`
    (session_id -> row)."""
    facts_by_session = {f['session_id']: f for f in session_facts if f.get('session_id')}
    facts_in_range, incomplete_count = filter_facts_in_range(
        session_facts, checkpoints, provider=provider, range_start=range_start, range_end=range_end,
        window_scope=window_scope)
    totals = compute_totals(facts_in_range, code_deltas, incomplete_coverage_session_count=incomplete_count)
    rankings = compute_rankings(facts_in_range, code_deltas, dimension=dimension, sort_by=sort_by)
    calibration = compute_calibration(calibration_samples, checkpoints, facts_by_session,
                                       provider=provider, window_scope=window_scope)
    bar_change = compute_bar_change(range_samples, range_start=range_start, range_end=range_end)
    segmented_bar = compute_segmented_bar(bar_change, totals, calibration)

    empty_state = None
    if totals['session_count'] == 0:
        # Finding 4 (P2-4, "2026-09-28 re-review"): a session that overlaps
        # this window but whose in-range delta couldn't be isolated (still
        # mid-turn across the boundary) is excluded from `facts_in_range`,
        # same as before -- but "no session survived the filter" must not
        # collapse to the same "No runs" a truly-empty window shows. The
        # work happened; it just isn't measurable in this window yet.
        empty_state = ('incomplete_coverage' if incomplete_count > 0
                       else 'model_unknown' if totals['model_unknown_session_count'] > 0
                       else 'no_runs')
    elif not range_samples:
        empty_state = 'no_vendor_percentage'
    elif coverage_begins is None:
        empty_state = 'sampling_not_begun'

    return {
        'provider': provider, 'window_kind': window_kind, 'window_scope': window_scope,
        'range_start': range_start, 'range_end': range_end,
        'coverage_begins': coverage_begins,
        'empty_state': empty_state,
        'totals': totals,
        'rankings': rankings,
        'dimension': dimension, 'sort_by': sort_by,
        'tokens_per_point': {
            'status': calibration.get('status'),
            # Wire names are the UI/smoke contract (system-status.js,
            # tools/smoke): 'median' is now the POOLED ratio and p10/p90 the
            # block-bootstrap range -- `estimator`/`range_method` say so for
            # anything reading the raw JSON.
            'median': calibration.get('input_per_point'),
            'p10': calibration.get('input_per_point_lo'),
            'p90': calibration.get('input_per_point_hi'),
            'estimator': calibration.get('estimator'),
            'range_method': calibration.get('range_method'),
            'total_delta_pp': calibration.get('total_delta_pp'),
            'run_count': calibration.get('run_count'),
            'sample_count': calibration.get('eligible_interval_count', 0),
            'note': 'Indicative: account-wide bar',
        },
        'bar_change': bar_change,
        'segmented_bar': segmented_bar,
    }
