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

import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

_MAX_INTERVAL_MINUTES = 10
_MIN_ELIGIBLE_INTERVALS = 5
_MIN_ELIGIBLE_SESSIONS = 3

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


def _session_turns(ck: Optional[dict]) -> list[dict]:
    """[{'start','end', <each _TOKEN_KEYS delta>, 'token_coverage','fact_only'}]
    -- one entry per TURN (P1-3, finding 3): consecutive checkpoint pairs
    (baseline -> completions[0], completions[0] -> completions[1], ...),
    each holding that turn's OWN token delta, never the session's cumulative
    lifetime total charged again to every interval it overlaps. With no
    baseline (aged out by 90-day retention while later completions remain)
    the pairs start at completions[0]; the span before it is unmeasured and
    `_session_evidence` reports it as such. A session with no completions
    yet (still running, mid-turn) has no turn evidence at all -- an empty
    list, not a fabricated one spanning "start to now".

    A delta needs a counter at BOTH ends: a turn whose previous checkpoint
    carried no counter (an `unavailable` completion) is itself
    `unavailable`, never "this cumulative minus zero"."""
    baseline = (ck or {}).get('baseline')
    completions = (ck or {}).get('completions') or []
    chain = ([baseline] if baseline else []) + list(completions)
    turns = []
    for prev, cur in zip(chain, chain[1:]):
        p_at, c_at = _parse_iso(prev.get('observed_at')), _parse_iso(cur.get('observed_at'))
        if p_at is None or c_at is None:
            continue
        turn = {'start': p_at, 'end': c_at, 'fact_only': False,
                'token_coverage': cur.get('token_coverage') or 'unavailable'}
        for k in _TOKEN_KEYS:
            a, b = prev.get(k), cur.get(k)
            turn[k] = max(b - a, 0) if (a is not None and b is not None) else None
        if turn['input_processed_total'] is None and turn['output_tokens'] is None:
            turn['token_coverage'] = 'unavailable'
        turns.append(turn)
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

    A fact with no checkpoint at all (housekeeping, pre-checkpoint history)
    is one `fact_only` turn from started_at to ended_at carrying the fact's
    own totals once it has finished, so window totals can still show it;
    calibration never trusts such a turn. Without a start time it has no
    placeable span and is skipped, as before."""
    baseline = (ck or {}).get('baseline')
    completions = (ck or {}).get('completions') or []
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
        return [turn], []

    turns = _session_turns(ck)
    unmeasured: list[tuple] = []
    if not baseline:
        unmeasured.append((_parse_iso(fact.get('started_at')) if fact else None,
                           _parse_iso(completions[0].get('observed_at'))))
    last = (completions[-1] if completions else baseline) or {}
    last_at = _parse_iso(last.get('observed_at'))
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


def filter_facts_in_range(session_facts: list[dict], checkpoints: dict[str, dict], *, provider: str,
                           range_start: Optional[str], range_end: Optional[str]) -> tuple[list[dict], int]:
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
        rows.append(row)
    return rows, incomplete


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
    for f in facts:
        if f.get('token_coverage') == 'unavailable':
            unavailable_token_rows += 1
            continue
        have_any_token_data = True
        for k in sums:
            v = f.get(k)
            if v is not None:
                sums[k] += v

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

    return {
        'session_count': len(facts),
        'tokens': {k: (v if have_any_token_data else None) for k, v in sums.items()},
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
    for f in facts:
        label = f.get(key) or 'Unknown'
        if label == 'Unknown':
            unknown_count += 1
        g = groups.setdefault(label, {
            'label': label, 'input_processed_total': 0, 'output_tokens': 0,
            'added': 0, 'session_count': 0, 'has_token_data': False, 'has_loc_data': False,
        })
        g['session_count'] += 1
        if f.get('token_coverage') != 'unavailable':
            g['has_token_data'] = True
            g['input_processed_total'] += f.get('input_processed_total') or 0
            g['output_tokens'] += f.get('output_tokens') or 0
        else:
            missing_data_count += 1
        cd = code_deltas.get(f.get('session_id') or '') if f.get('_loc_attributable', True) else None
        if cd and cd.get('status') == 'ok':
            g['has_loc_data'] = True
            g['added'] += cd.get('added') or 0

    sort_key = {'input': 'input_processed_total', 'output': 'output_tokens',
                'added': 'added'}.get(sort_by, 'input_processed_total')
    rows = sorted(groups.values(), key=lambda g: g[sort_key], reverse=True)
    for r in rows:
        r['input_processed_total'] = r['input_processed_total'] if r['has_token_data'] else None
        r['output_tokens'] = r['output_tokens'] if r['has_token_data'] else None
        r['added'] = r['added'] if r['has_loc_data'] else None
        del r['has_token_data']
        del r['has_loc_data']
    return {'rows': rows, 'unknown_count': unknown_count, 'missing_data_count': missing_data_count}


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
    scoped to one), <=10 minutes apart, positive delta >=1pp, quality 'ok'
    at both endpoints, no reset crossed (same `resets_at`).

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
    session). `_session_turns` derives each turn's own delta; a turn is only
    counted toward an interval when it is FULLY CONTAINED in [t_a, t_b).
    Any turn that overlaps the interval WITHOUT being fully contained (the
    session was mid-turn across a boundary) marks the interval incomplete --
    that turn's tokens cannot be split between intervals, so they are
    withheld here rather than fabricated as belonging to this one. A session
    can contribute turns to more than one interval; each interval only ever
    sees the turns that actually happened inside it.
    """
    out = []
    ordered = sorted((s for s in samples if s.get('quality') == 'ok'
                       and s.get('raw_utilization') is not None
                       and s.get('source_observed_at')),
                      key=lambda s: s['source_observed_at'])
    all_sids = set(checkpoints.keys()) | set(facts_by_session.keys())
    for a, b in zip(ordered, ordered[1:]):
        if a.get('resets_at') != b.get('resets_at'):
            continue  # a reset happened between these two readings
        t_a, t_b = _parse_iso(a['source_observed_at']), _parse_iso(b['source_observed_at'])
        if t_a is None or t_b is None or t_b <= t_a:
            continue
        if (t_b - t_a) > timedelta(minutes=_MAX_INTERVAL_MINUTES):
            continue
        delta = b['raw_utilization'] - a['raw_utilization']
        if delta < 1.0:
            continue

        session_ids: set[str] = set()
        coverage_complete = True
        input_processed_total = 0
        output_tokens = 0
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
            gap = any((s is None or s < t_b) and (e is None or e >= t_a) for s, e in unmeasured)
            contained = [t for t in turns if t['start'] >= t_a and t['end'] <= t_b]
            crossing = [t for t in turns if t not in contained
                        and t['start'] < t_b and t['end'] >= t_a]
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


def compute_calibration(samples: list[dict], checkpoints: dict[str, dict], facts_by_session: dict[str, dict],
                         *, provider: str, window_scope: str) -> dict:
    """Median + 10th/90th percentile workload-per-point and input-per-point
    over ALL eligible intervals in the given (already 90-day-scoped)
    history, per "A calibration value requires at least five eligible
    intervals from at least three distinct sessions in the same
    provider/window class. Those intervals must have only one Clayrune
    session active." (finding 2, P1-2). An interval with zero overlapping
    sessions contributes nothing to calibration (its delta belongs to
    `Unattributed activity` instead, per spec) but is not itself an error.
    """
    intervals = _eligible_intervals(samples, checkpoints, facts_by_session,
                                     provider=provider, window_scope=window_scope)
    # Ron, 2026-09-29: any number of concurrent Clayrune sessions may share an
    # interval. Tokens-per-point is a property of the plan, not of concurrency:
    # the interval already sums every overlapping session's measured delta, and
    # coverage_complete refuses the interval if ANY of them is unmeasured. The
    # old single-session rule left ~17% of intervals usable on a box that runs
    # up to 9 agents at once, so calibration never qualified.
    calibratable = [iv for iv in intervals if iv['coverage_complete'] and iv['session_ids']]
    distinct_sessions = set().union(*(iv['session_ids'] for iv in calibratable)) if calibratable else set()
    if len(calibratable) < _MIN_ELIGIBLE_INTERVALS or len(distinct_sessions) < _MIN_ELIGIBLE_SESSIONS:
        return {'status': 'insufficient_samples', 'eligible_interval_count': len(calibratable),
                'distinct_session_count': len(distinct_sessions), 'all_intervals': intervals}

    workload_per_point = sorted(
        (iv['input_processed_total'] + iv['output_tokens']) / iv['delta_pp'] for iv in calibratable)
    input_per_point = sorted(iv['input_processed_total'] / iv['delta_pp'] for iv in calibratable)
    return {
        'status': 'ok',
        'eligible_interval_count': len(calibratable),
        'distinct_session_count': len(distinct_sessions),
        'workload_per_point_median': statistics.median(workload_per_point),
        'workload_per_point_p10': _percentile(workload_per_point, 10),
        'workload_per_point_p90': _percentile(workload_per_point, 90),
        'input_per_point_median': statistics.median(input_per_point),
        'input_per_point_p10': _percentile(input_per_point, 10),
        'input_per_point_p90': _percentile(input_per_point, 90),
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
    was crossed (constant `resets_at` across the range)."""
    start, end = _parse_iso(range_start), _parse_iso(range_end)
    in_range = sorted(
        (s for s in samples if s.get('quality') == 'ok' and s.get('raw_utilization') is not None
         and _in_range(s.get('source_observed_at'), start, end)),
        key=lambda s: s['source_observed_at'])
    if len(in_range) < 2:
        return {'status': 'insufficient_samples', 'delta_pp': None}
    if len({s.get('resets_at') for s in in_range}) > 1:
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

    fresh_plus_output = (totals['tokens'].get('input_fresh') or 0) + (totals['tokens'].get('output_tokens') or 0)
    processed_plus_output = ((totals['tokens'].get('input_processed_total') or 0)
                              + (totals['tokens'].get('output_tokens') or 0))
    estimated_pp = processed_plus_output / calibration['workload_per_point_median']
    low_pp = fresh_plus_output / calibration['workload_per_point_p90']
    high_pp = processed_plus_output / calibration['workload_per_point_p10']
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
        session_facts, checkpoints, provider=provider, range_start=range_start, range_end=range_end)
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
        empty_state = 'incomplete_coverage' if incomplete_count > 0 else 'no_runs'
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
            'median': calibration.get('input_per_point_median'),
            'p10': calibration.get('input_per_point_p10'),
            'p90': calibration.get('input_per_point_p90'),
            'sample_count': calibration.get('eligible_interval_count', 0),
            'note': 'Indicative: account-wide bar',
        },
        'bar_change': bar_change,
        'segmented_bar': segmented_bar,
    }
