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
    dt = _parse_iso(ts)
    if dt is None:
        return False
    if start is not None and dt < start:
        return False
    if end is not None and dt >= end:
        return False
    return True


# ── totals + rankings (session_fact / code_delta, no allowance samples) ────

def filter_facts_in_range(session_facts: list[dict], *, provider: str,
                           range_start: Optional[str], range_end: Optional[str]) -> list[dict]:
    """A session "belongs to" the window it started in -- matches the
    dashboard's per-window story (a session that starts in one window and
    finishes in the next still attributes its whole cost to where the work
    began)."""
    start = _parse_iso(range_start)
    end = _parse_iso(range_end)
    return [f for f in session_facts
            if f.get('provider') == provider and _in_range(f.get('started_at'), start, end)]


def compute_totals(facts: list[dict], code_deltas: dict[str, dict]) -> dict:
    """Sum token/LOC categories across `facts`. A field stays `None` (not 0)
    when every contributing session had `token_coverage='unavailable'` for
    it -- a reported 0 must mean a confirmed zero, per the spec's "no
    fabricated zero" rule."""
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
        cd = code_deltas.get(f.get('session_id') or '')
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
        cd = code_deltas.get(f.get('session_id') or '')
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


# ── tokens-per-point calibration (allowance_sample pairs + session_fact) ───

def _eligible_intervals(samples: list[dict], facts: list[dict]) -> list[dict]:
    """Consecutive fresh-sample pairs meeting the spec's eligibility rule:
    same provider/window/scope identity (guaranteed -- `samples` is already
    scoped to one), <=10 minutes apart, positive delta >=1pp, quality 'ok'
    at both endpoints, no reset crossed (same `resets_at`). Each eligible
    interval also gets the set of session_ids whose activity overlaps it and
    whether ALL of them have complete-or-partial (non-unavailable) token
    coverage -- "complete Clayrune session-token coverage" per the spec.
    """
    out = []
    ordered = sorted((s for s in samples if s.get('quality') == 'ok'
                       and s.get('raw_utilization') is not None
                       and s.get('source_observed_at')),
                      key=lambda s: s['source_observed_at'])
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
        overlapping = [f for f in facts if _fact_overlaps(f, t_a, t_b)]
        coverage_complete = all(f.get('token_coverage') != 'unavailable' for f in overlapping)
        out.append({
            'start': t_a, 'end': t_b, 'delta_pp': delta,
            'session_ids': {f.get('session_id') for f in overlapping if f.get('session_id')},
            'coverage_complete': coverage_complete,
            'input_processed_total': sum(f.get('input_processed_total') or 0 for f in overlapping),
            'output_tokens': sum(f.get('output_tokens') or 0 for f in overlapping),
        })
    return out


def _fact_overlaps(fact: dict, start: datetime, end: datetime) -> bool:
    s = _parse_iso(fact.get('started_at'))
    e = _parse_iso(fact.get('ended_at')) or end  # a still-running session extends to "now" (= end)
    if s is None:
        return False
    return s < end and e >= start


def compute_calibration(samples: list[dict], facts: list[dict]) -> dict:
    """Median + 10th/90th percentile workload-per-point and input-per-point
    over ALL eligible intervals in the given (already 90-day-scoped)
    history, per "A calibration value requires at least five eligible
    intervals from at least three distinct sessions in the same
    provider/window class." An interval with zero overlapping sessions
    contributes nothing to calibration (its delta belongs to
    `Unattributed activity` instead, per spec) but is not itself an error.
    """
    intervals = _eligible_intervals(samples, facts)
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
                     calibration_facts: list[dict], session_facts: list[dict],
                     code_deltas: dict[str, dict], coverage_begins: Optional[str]) -> dict:
    """Assemble one provider/window/range Breakdown payload. Caller (the
    Flask route) is responsible for fetching `range_samples` (this
    provider/window/scope's allowance_sample rows for the display range),
    `calibration_samples`/`calibration_facts` (the full 90-day history for
    calibration, NOT range-limited), `session_facts` (all facts, any
    provider -- filtered here), and `code_deltas` (session_id -> row)."""
    facts_in_range = filter_facts_in_range(session_facts, provider=provider,
                                            range_start=range_start, range_end=range_end)
    totals = compute_totals(facts_in_range, code_deltas)
    rankings = compute_rankings(facts_in_range, code_deltas, dimension=dimension, sort_by=sort_by)
    calibration = compute_calibration(calibration_samples, calibration_facts)
    bar_change = compute_bar_change(range_samples, range_start=range_start, range_end=range_end)
    segmented_bar = compute_segmented_bar(bar_change, totals, calibration)

    empty_state = None
    if totals['session_count'] == 0:
        empty_state = 'no_runs'
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
