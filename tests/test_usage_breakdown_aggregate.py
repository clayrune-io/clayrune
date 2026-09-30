"""MC-998 phase 4b: mc/usage_breakdown_aggregate.py -- pure totals, rankings,
tokens-per-point calibration, bar-change, and segmented-bar computation over
already-fetched UsageBreakdownStore rows. No sqlite, no Flask.

Covers the spec's acceptance checks that land in this module:
  1. A Codex row with nested positive usage and zero flat telemetry
     contributes once; absent data is Unavailable, not 0.
  2. Two samples separated by a reset/stale/missing interval produce no
     tokens-per-point ratio; a positive vendor delta with no Clayrune run
     lands entirely in Unattributed / uncertain.
  3. No row (ranking or otherwise) is ever given an exact percentage share
     -- the segmented bar is the only percentage estimate, and it is
     whole-bar, never per-project.

Plus the review fixes from docs/_journal/4668eafc-mc998-fenn-review.md:
  P1-2: a calibration interval only counts with correct provider/scope,
        complete coverage, and exactly one active session.
  P1-3: token deltas come from timestamped session_checkpoint pairs
        (baseline -> completion), not a completed session's lifetime total
        charged to every interval it overlaps.
  P2-4: window totals sum each TURN (checkpoint -> next checkpoint) fully
        contained in the range; a turn straddling the range, or any
        unmeasured span overlapping it, is surfaced as "incomplete
        coverage", never silently included or dropped (round 3 #4 moved
        this from whole-session to per-turn containment).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.usage_breakdown_aggregate import (  # noqa: E402
    build_breakdown, compute_bar_change, compute_calibration, compute_rankings,
    compute_segmented_bar, compute_totals, filter_facts_in_range,
)


def _fact(session_id, *, provider='claude', started_at, ended_at=None,
          project_id='proj-a', character='Tobin', trigger_type='manual',
          observed_model='claude-sonnet-5', input_processed_total=100,
          output_tokens=50, token_coverage='complete'):
    return {
        'session_id': session_id, 'provider': provider, 'project_id': project_id,
        'character': character, 'trigger_type': trigger_type, 'observed_model': observed_model,
        'started_at': started_at, 'ended_at': ended_at,
        'input_fresh': input_processed_total, 'input_cache_write': 0, 'input_cache_read': 0,
        'input_processed_total': input_processed_total if token_coverage != 'unavailable' else None,
        'output_tokens': output_tokens if token_coverage != 'unavailable' else None,
        'token_coverage': token_coverage,
    }


def _sample(*, raw_utilization, source_observed_at,
            resets_at: 'str | None' = '2026-10-05T00:00:00+00:00', quality='ok'):
    return {'raw_utilization': raw_utilization, 'source_observed_at': source_observed_at,
            'resets_at': resets_at, 'quality': quality}


def _checkpoint(session_id, *, provider='claude', baseline_at, completion_at=None,
                 input_processed_total=100, output_tokens=50, token_coverage='complete'):
    """One session's {'baseline': row|None, 'completions': [row, ...]} -- the
    production shape `UsageBreakdownStore.get_session_checkpoints`/grouped
    `list_session_checkpoints` returns (P1-3: a multi-turn session appends
    one completion row per turn, so `completions` is a list, not a single
    row). `completion_at=None` models a still-running session (baseline
    recorded at dispatch, no completion row yet). Cumulative totals given
    here ARE the checkpoint's absolute totals at that turn's end, matching
    the store row shape -- `_session_turns` derives per-turn deltas from
    them, not the other way around."""
    ck = {'baseline': {
        'session_id': session_id, 'provider': provider, 'checkpoint_type': 'baseline',
        'observed_at': baseline_at, 'input_fresh': 0, 'input_cache_write': 0,
        'input_cache_read': 0, 'input_processed_total': 0, 'output_tokens': 0,
        'output_reasoning': 0, 'token_coverage': 'unavailable',
    }, 'turn_starts': [], 'completions': []}
    if completion_at is not None:
        ck['completions'].append({
            'session_id': session_id, 'provider': provider, 'checkpoint_type': 'completion',
            'observed_at': completion_at, 'input_fresh': input_processed_total,
            'input_cache_write': 0, 'input_cache_read': 0,
            'input_processed_total': input_processed_total, 'output_tokens': output_tokens,
            'output_reasoning': 0, 'token_coverage': token_coverage,
        })
    return ck


def _completion(session_id, *, provider='claude', observed_at,
                 input_processed_total, output_tokens, token_coverage='complete'):
    """One additional 'completion' checkpoint row, for appending a second/
    third turn onto a `_checkpoint(...)` fixture's `completions` list."""
    return {
        'session_id': session_id, 'provider': provider, 'checkpoint_type': 'completion',
        'observed_at': observed_at, 'input_fresh': input_processed_total,
        'input_cache_write': 0, 'input_cache_read': 0,
        'input_processed_total': input_processed_total, 'output_tokens': output_tokens,
        'output_reasoning': 0, 'token_coverage': token_coverage,
    }


def _turn_start(session_id, *, provider='claude', observed_at,
                 input_processed_total, output_tokens, token_coverage='complete'):
    """One 'turn_start' checkpoint row (schema v5, MC-998 turn-start fix),
    for appending onto a `_checkpoint(...)` fixture's `turn_starts` list.
    Carries the same cumulative totals as the PRECEDING completion (the new
    turn hasn't produced a token yet) -- callers pass the previous turn's
    own cumulative totals here, not the upcoming turn's."""
    return {
        'session_id': session_id, 'provider': provider, 'checkpoint_type': 'turn_start',
        'observed_at': observed_at, 'input_fresh': input_processed_total,
        'input_cache_write': 0, 'input_cache_read': 0,
        'input_processed_total': input_processed_total, 'output_tokens': output_tokens,
        'output_reasoning': 0, 'token_coverage': token_coverage,
    }


def _sample_tick(session_id, *, provider='claude', observed_at,
                  input_processed_total, output_tokens, token_coverage='complete'):
    """One 'sample_tick' checkpoint row (schema v6, backlog 4668eafc
    follow-up), for appending onto a `_checkpoint(...)` fixture's
    `sample_ticks` list -- a RUNNING session's cumulative counters snapshot
    at the same `source_observed_at` an allowance sample used for its own
    reading. Same cumulative-totals convention as `_completion`/`_turn_start`:
    the ABSOLUTE total at that instant, not a delta."""
    return {
        'session_id': session_id, 'provider': provider, 'checkpoint_type': 'sample_tick',
        'observed_at': observed_at, 'input_fresh': input_processed_total,
        'input_cache_write': 0, 'input_cache_read': 0,
        'input_processed_total': input_processed_total, 'output_tokens': output_tokens,
        'output_reasoning': 0, 'token_coverage': token_coverage,
    }


# ── totals / AC1 ─────────────────────────────────────────────────────────

def test_unavailable_coverage_rows_do_not_contribute_a_fabricated_zero():
    facts = [_fact('s1', started_at='2026-09-28T10:00:00Z', token_coverage='unavailable')]
    totals = compute_totals(facts, {})
    assert totals['tokens']['input_processed_total'] is None
    assert totals['token_coverage_unavailable_count'] == 1


def test_codex_nested_only_row_contributes_once():
    facts = [_fact('c1', provider='codex', started_at='2026-09-28T10:00:00Z',
                    input_processed_total=40, output_tokens=10)]
    totals = compute_totals(facts, {})
    assert totals['tokens']['input_processed_total'] == 40
    assert totals['tokens']['output_tokens'] == 10
    assert totals['session_count'] == 1


def test_loc_unavailable_when_no_code_delta_row():
    facts = [_fact('s1', started_at='2026-09-28T10:00:00Z')]
    totals = compute_totals(facts, {})
    assert totals['loc']['added'] is None
    assert totals['loc_unavailable_count'] == 1


def test_loc_present_when_code_delta_ok():
    facts = [_fact('s1', started_at='2026-09-28T10:00:00Z')]
    code_deltas = {'s1': {'status': 'ok', 'added': 5, 'deleted': 2}}
    totals = compute_totals(facts, code_deltas)
    assert totals['loc'] == {'added': 5, 'deleted': 2}


def test_totals_carries_incomplete_coverage_count_through():
    facts = [_fact('s1', started_at='2026-09-28T10:00:00Z', ended_at='2026-09-28T10:05:00Z')]
    totals = compute_totals(facts, {}, incomplete_coverage_session_count=2)
    assert totals['incomplete_coverage_session_count'] == 2


# ── filter_facts_in_range (P2-4 / finding 4) ────────────────────────────

def test_filter_scopes_to_provider_and_requires_full_containment():
    facts = [
        _fact('s1', provider='claude', started_at='2026-09-28T10:00:00Z',
              ended_at='2026-09-28T10:05:00Z'),
        _fact('s2', provider='codex', started_at='2026-09-28T10:00:00Z',
              ended_at='2026-09-28T10:05:00Z'),
        _fact('s3', provider='claude', started_at='2026-09-27T10:00:00Z',
              ended_at='2026-09-27T15:00:00Z'),
    ]
    got, incomplete = filter_facts_in_range(
        facts, {}, provider='claude',
        range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z')
    assert [f['session_id'] for f in got] == ['s1']
    assert incomplete == 0  # s3 is fully outside the range -- no overlap, not "incomplete"


def test_filter_partial_overlap_is_incomplete_not_dropped_or_included():
    """A session that started the evening before and finished inside the
    range must not be silently included (its pre-range tokens aren't this
    window's) nor silently dropped (finding 4: the caller needs to know
    coverage is incomplete, not that nothing happened)."""
    facts = [_fact('s1', provider='claude', started_at='2026-09-27T23:00:00Z',
                    ended_at='2026-09-28T01:00:00Z')]
    got, incomplete = filter_facts_in_range(
        facts, {}, provider='claude',
        range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z')
    assert got == []
    assert incomplete == 1


def test_filter_still_running_session_in_range_is_incomplete():
    facts = [_fact('s1', provider='claude', started_at='2026-09-28T10:00:00Z', ended_at=None)]
    got, incomplete = filter_facts_in_range(
        facts, {}, provider='claude',
        range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z')
    assert got == []
    assert incomplete == 1


def test_filter_prefers_checkpoint_bounds_over_fact_started_ended():
    """P1-3: when a checkpoint pair exists it is the authority on the
    session's measured span, not the (possibly wider) session_fact
    started_at/ended_at."""
    checkpoints = {'s1': _checkpoint('s1', baseline_at='2026-09-28T10:00:00Z',
                                      completion_at='2026-09-28T10:05:00Z')}
    facts = [_fact('s1', provider='claude', started_at='2026-09-27T23:00:00Z',
                    ended_at='2026-09-28T10:05:00Z')]
    got, incomplete = filter_facts_in_range(
        facts, checkpoints, provider='claude',
        range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z')
    assert [f['session_id'] for f in got] == ['s1']
    assert incomplete == 0


# ── rankings / AC3 (no per-row percentage attribution exists at all) ──────

def test_rankings_group_by_project_and_sort_desc_by_input():
    facts = [
        _fact('s1', started_at='2026-09-28T10:00:00Z', project_id='proj-a',
              input_processed_total=200, output_tokens=0),
        _fact('s2', started_at='2026-09-28T10:00:00Z', project_id='proj-b',
              input_processed_total=50, output_tokens=0),
    ]
    r = compute_rankings(facts, {}, dimension='project', sort_by='input')
    assert [row['label'] for row in r['rows']] == ['proj-a', 'proj-b']
    assert 'percent' not in r['rows'][0] and 'share' not in r['rows'][0]


def test_rankings_unknown_group_and_missing_data_counted():
    facts = [
        _fact('s1', started_at='2026-09-28T10:00:00Z', project_id=None, token_coverage='unavailable'),
        _fact('s2', started_at='2026-09-28T10:00:00Z', project_id='proj-a'),
    ]
    r = compute_rankings(facts, {}, dimension='project', sort_by='input')
    assert r['unknown_count'] == 1
    assert r['missing_data_count'] == 1


def test_rankings_rejects_unknown_dimension():
    import pytest
    with pytest.raises(ValueError):
        compute_rankings([], {}, dimension='bogus')


# ── bar change / AC2 ────────────────────────────────────────────────────

def test_bar_change_positive_delta_within_one_window():
    samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z'),
               _sample(raw_utilization=15.0, source_observed_at='2026-09-28T10:05:00Z')]
    bc = compute_bar_change(samples, range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z')
    assert bc == {'status': 'ok', 'delta_pp': 5.0,
                  'earliest': '2026-09-28T10:00:00Z', 'latest': '2026-09-28T10:05:00Z'}


def test_bar_change_reset_crossed_yields_no_delta():
    samples = [_sample(raw_utilization=95.0, source_observed_at='2026-09-28T09:59:00Z',
                        resets_at='2026-09-28T10:00:00+00:00'),
               _sample(raw_utilization=2.0, source_observed_at='2026-09-28T10:01:00Z',
                       resets_at='2026-10-05T10:00:00+00:00')]
    bc = compute_bar_change(samples, range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z')
    assert bc['status'] == 'reset_crossed'
    assert bc['delta_pp'] is None


def test_bar_change_single_sample_is_insufficient():
    samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z')]
    bc = compute_bar_change(samples, range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z')
    assert bc['status'] == 'insufficient_samples'


def test_bar_change_jittered_resets_at_within_window_is_ok():
    """MC-998 follow-up: the vendor endpoint returns resets_at with
    sub-second jitter across polls of the SAME window (measured 2026-09-29:
    241/241 distinct raw values) -- string equality treated every sample
    pair as a reset, so the bar never became measurable. A handful of
    seconds of drift must not read as a reset crossing."""
    samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z',
                        resets_at='2026-09-28T19:00:00.444543+00:00'),
               _sample(raw_utilization=15.0, source_observed_at='2026-09-28T10:05:00Z',
                       resets_at='2026-09-28T18:59:59.567670+00:00'),
               _sample(raw_utilization=18.0, source_observed_at='2026-09-28T10:08:00Z',
                       resets_at='2026-09-28T19:00:00.005273+00:00')]
    bc = compute_bar_change(samples, range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z')
    assert bc == {'status': 'ok', 'delta_pp': 8.0,
                  'earliest': '2026-09-28T10:00:00Z', 'latest': '2026-09-28T10:08:00Z'}


def test_bar_change_genuine_reset_beyond_tolerance_still_crossed():
    samples = [_sample(raw_utilization=95.0, source_observed_at='2026-09-28T09:59:00Z',
                        resets_at='2026-09-28T10:00:00+00:00'),
               _sample(raw_utilization=2.0, source_observed_at='2026-09-28T10:01:00Z',
                       resets_at='2026-09-28T15:00:03+00:00')]  # +5h reset, not jitter
    bc = compute_bar_change(samples, range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z')
    assert bc['status'] == 'reset_crossed'
    assert bc['delta_pp'] is None


def test_bar_change_none_resets_at_fails_closed():
    samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z', resets_at=None),
               _sample(raw_utilization=15.0, source_observed_at='2026-09-28T10:05:00Z', resets_at=None)]
    bc = compute_bar_change(samples, range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z')
    assert bc['status'] == 'reset_crossed'
    assert bc['delta_pp'] is None


# ── calibration eligibility / AC2 + P1-2/P1-3 ───────────────────────────

def _calibration_fixture(n_pairs=5):
    """`n_pairs` distinct sessions, each with a checkpoint pair (baseline,
    completion) EXACTLY matching one sample pair's [t_a, t_b) -- the
    production shape a fully-measured, fully-contained session takes.
    Pairs are spaced 20 minutes apart so no cross-pair adjacency is ALSO
    eligible (the 10-minute max-gap rule excludes it)."""
    samples, checkpoints, facts = [], {}, []
    base = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(n_pairs):
        sid = f'sess-{i}'
        t0 = base + timedelta(minutes=20 * i)
        t1 = t0 + timedelta(minutes=2)
        samples.append(_sample(raw_utilization=float(i * 10), source_observed_at=t0.isoformat()))
        samples.append(_sample(raw_utilization=float(i * 10 + 5), source_observed_at=t1.isoformat()))
        checkpoints[sid] = _checkpoint(sid, baseline_at=t0.isoformat(), completion_at=t1.isoformat())
        facts.append(_fact(sid, started_at=t0.isoformat(), ended_at=t1.isoformat()))
    return samples, checkpoints, facts


def _facts_by_session(facts):
    return {f['session_id']: f for f in facts}


def test_calibration_ok_with_five_intervals_five_sessions():
    samples, checkpoints, facts = _calibration_fixture()
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'ok'
    assert cal['eligible_interval_count'] == 5
    assert cal['distinct_session_count'] == 5


def test_calibration_insufficient_with_only_four_intervals():
    samples, checkpoints, facts = _calibration_fixture(n_pairs=4)
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'insufficient_samples'


def test_calibration_reset_between_pair_excludes_that_interval():
    samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z',
                        resets_at='2026-10-01T00:00:00+00:00'),
               _sample(raw_utilization=20.0, source_observed_at='2026-09-28T10:05:00Z',
                       resets_at='2026-10-08T00:00:00+00:00')]  # different resets_at = reset crossed
    cal = compute_calibration(samples, {}, {}, provider='claude', window_scope='all')
    assert cal['status'] == 'insufficient_samples'
    assert cal['eligible_interval_count'] == 0


def test_calibration_none_resets_at_fails_closed():
    samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z', resets_at=None),
               _sample(raw_utilization=20.0, source_observed_at='2026-09-28T10:05:00Z', resets_at=None)]
    cal = compute_calibration(samples, {}, {}, provider='claude', window_scope='all')
    assert cal['status'] == 'insufficient_samples'
    assert cal['eligible_interval_count'] == 0


def test_calibration_jittered_resets_at_keeps_interval():
    """Same jitter as compute_bar_change, at the calibration pairing site
    (~line 426 pre-fix): a.resets_at != b.resets_at discarded every interval
    under vendor jitter, so tokens_per_point sample_count stayed 0 forever."""
    samples, checkpoints, facts = [], {}, []
    base = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
    jittered_resets = ['2026-10-05T00:00:00.444543+00:00', '2026-10-04T23:59:59.567670+00:00',
                        '2026-10-05T00:00:00.005273+00:00']
    for i in range(5):
        sid = f'sess-{i}'
        t0 = base + timedelta(minutes=20 * i)
        t1 = t0 + timedelta(minutes=2)
        samples.append(_sample(raw_utilization=float(i * 10), source_observed_at=t0.isoformat(),
                                resets_at=jittered_resets[i % len(jittered_resets)]))
        samples.append(_sample(raw_utilization=float(i * 10 + 5), source_observed_at=t1.isoformat(),
                                resets_at=jittered_resets[(i + 1) % len(jittered_resets)]))
        checkpoints[sid] = _checkpoint(sid, baseline_at=t0.isoformat(), completion_at=t1.isoformat())
        facts.append(_fact(sid, started_at=t0.isoformat(), ended_at=t1.isoformat()))
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'ok'
    assert cal['eligible_interval_count'] == 5


def test_calibration_gap_over_ten_minutes_excludes_interval():
    samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z'),
               _sample(raw_utilization=20.0, source_observed_at='2026-09-28T10:15:00Z')]
    cal = compute_calibration(samples, {}, {}, provider='claude', window_scope='all')
    assert cal['eligible_interval_count'] == 0


def test_calibration_no_session_activity_excluded_not_counted_as_eligible():
    samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z'),
               _sample(raw_utilization=20.0, source_observed_at='2026-09-28T10:05:00Z')]
    cal = compute_calibration(samples, {}, {}, provider='claude', window_scope='all')
    assert cal['status'] == 'insufficient_samples'
    assert cal['eligible_interval_count'] == 0


def test_calibration_two_active_sessions_in_one_interval_sums_them():
    """Ron, 2026-09-29 (reverses P1-2 finding 2's single-session rule): a
    second, fully measured concurrent session keeps the interval
    calibratable, and the interval charges BOTH sessions' deltas -- the
    tokens-per-point ratio does not depend on how many agents ran."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)  # sess-0's own interval
    checkpoints['sess-0-concurrent'] = _checkpoint(
        'sess-0-concurrent', baseline_at=t0.isoformat(),
        completion_at=(t0 + timedelta(minutes=2)).isoformat())
    facts.append(_fact('sess-0-concurrent', started_at=t0.isoformat(),
                        ended_at=(t0 + timedelta(minutes=2)).isoformat()))
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'ok'
    assert cal['eligible_interval_count'] == 5
    shared = [iv for iv in cal['all_intervals'] if len(iv['session_ids']) == 2]
    assert len(shared) == 1
    assert shared[0]['input_processed_total'] == 200 and shared[0]['output_tokens'] == 100


def test_calibration_partially_overlapping_session_marks_interval_incomplete():
    """P1-3 finding 3: a session whose lifetime spans BEYOND one interval
    (e.g. dispatched before the window opened) must not have its lifetime
    total charged to that interval -- the interval is withheld instead."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
    checkpoints['sess-0'] = _checkpoint(
        'sess-0', baseline_at=(t0 - timedelta(hours=1)).isoformat(),  # started well before t_a
        completion_at=(t0 + timedelta(minutes=2)).isoformat())
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'insufficient_samples'
    assert cal['eligible_interval_count'] == 4


def test_calibration_wrong_model_scope_excludes_session_entirely():
    samples, checkpoints, facts = _calibration_fixture()
    facts_by_session = _facts_by_session(facts)
    facts_by_session['sess-0']['observed_model'] = 'claude-opus-4'
    cal = compute_calibration(samples, checkpoints, facts_by_session,
                               provider='claude', window_scope='sonnet')
    # sess-0's opus session doesn't belong to the sonnet window at all --
    # its interval has zero sessions, so it's excluded (not "incomplete").
    assert cal['eligible_interval_count'] == 4


def test_calibration_unconfirmed_scope_marks_interval_incomplete():
    """A still-running session (no session_fact yet, so its model is
    unknown) must not silently pass as this window's scope."""
    samples, checkpoints, _facts = _calibration_fixture()
    cal = compute_calibration(samples, checkpoints, {},  # no facts at all -> scope unknown
                               provider='claude', window_scope='sonnet')
    assert cal['status'] == 'insufficient_samples'
    assert cal['eligible_interval_count'] == 0


def test_calibration_session_with_no_checkpoint_at_all_still_blocks_isolation():
    """P1-2 finding 2 (2026-09-28 re-review): the original fix only walked
    `checkpoints.items()`, so a session with NO checkpoint row -- a
    housekeeping dispatch that returns before checkpoint capture, or an old
    baseline dropped by 90-day retention -- was invisible to the isolation
    check even though its session_fact proves it overlapped the interval.
    Such a session must still disqualify that interval, exactly like a
    checkpoint-bearing overlapping session would."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)  # sess-0's own interval
    # 'ghost' has a session_fact overlapping sess-0's interval, but never
    # made it into `checkpoints` at all.
    facts.append(_fact('ghost', started_at=t0.isoformat(),
                        ended_at=(t0 + timedelta(minutes=2)).isoformat()))
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'insufficient_samples'
    assert cal['eligible_interval_count'] == 4


def test_calibration_multiturn_session_only_counts_the_contained_turn():
    """P1-3 finding 3 (2026-09-28 re-review): a session with more than one
    completion checkpoint (one per turn) must contribute only the turn that
    actually falls inside a given interval -- never the session's other
    turns' tokens, and never its lifetime cumulative total."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)  # sess-0's own interval
    t1 = t0 + timedelta(minutes=2)
    # sess-0 already has one turn ending in-interval at t1 with cumulative
    # total 100 (from _calibration_fixture). Add a SECOND turn entirely in
    # the 18-minute gap before sess-1's interval opens (t0+20min) -- it
    # can't itself disqualify a different fixture interval by crossing into
    # one -- that pushes the cumulative total much higher. If the old
    # lifetime-total bug were still present this second turn's tokens would
    # leak into sess-0's own [t0, t1) interval, which must see only its
    # first turn's delta.
    checkpoints['sess-0']['completions'].append(_completion(
        'sess-0', observed_at=(t1 + timedelta(minutes=5)).isoformat(),
        input_processed_total=100 + 5000, output_tokens=50 + 2000))
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'ok'
    assert cal['eligible_interval_count'] == 5
    # sess-0's interval must still show only its first turn's 100+50=150
    # workload, not 5100+2050.
    sess0_interval = next(iv for iv in cal['all_intervals'] if iv['start'] == t0)
    assert sess0_interval['input_processed_total'] == 100
    assert sess0_interval['output_tokens'] == 50


def test_calibration_turn_crossing_interval_boundary_marks_it_incomplete():
    """A turn that starts before an interval opens and ends inside it (the
    session was already mid-turn when the interval's first sample was
    taken) cannot be isolated to that interval -- its tokens might partly
    belong to an earlier interval too. Must withhold, not attribute."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=2)
    checkpoints['sess-0'] = _checkpoint(
        'sess-0', baseline_at=(t0 - timedelta(minutes=30)).isoformat(),
        completion_at=t1.isoformat())
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'insufficient_samples'
    assert cal['eligible_interval_count'] == 4


# ── turn-start fix (MC-998 follow-up 3, 2026-09-29) ─────────────────────

def test_idle_gap_between_completion_and_turn_start_does_not_straddle_an_interval():
    """docs/_journal/4668eafc-mc998-fenn-review.md, follow-up 3: a Mode B
    session's process stays alive between turns, so the checkpoint pair
    `_session_turns` used to build a turn was (last completion -> this
    completion) -- folding however long the session sat idle first into the
    turn's own span. Live evidence: 790 samples -> 111 candidate intervals,
    coverage_complete=0/111, every one blocked by a session whose turn
    'straddled' it. This reproduces the mechanism at unit scale: sess-idle
    sits idle for 2 hours between its two turns; an UNRELATED session's own
    interval falls entirely inside that idle window. Before the fix,
    sess-idle's (completion -> next completion) turn spanned the whole
    idle+turn2 stretch and crossed the unrelated interval's boundary,
    marking it incomplete even though sess-idle did nothing there. With
    turn_start recorded, sess-idle's idle span is simply absent from its
    evidence -- the interval sees only the session that actually ran in
    it."""
    t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
    t1a = t0 + timedelta(minutes=2)                 # sess-idle's turn 1 completes
    t_start2 = t0 + timedelta(hours=2, minutes=2)    # sess-idle's turn 2 begins
    t_end2 = t0 + timedelta(hours=2, minutes=4)      # sess-idle's turn 2 completes
    tx0 = t0 + timedelta(hours=1)                    # sess-x's interval -- deep inside the idle gap
    tx1 = tx0 + timedelta(minutes=2)

    samples = [
        _sample(raw_utilization=10.0, source_observed_at=tx0.isoformat()),
        _sample(raw_utilization=16.0, source_observed_at=tx1.isoformat()),
    ]
    checkpoints = {
        'sess-idle': _checkpoint('sess-idle', baseline_at=t0.isoformat(), completion_at=t1a.isoformat()),
        'sess-x': _checkpoint('sess-x', baseline_at=tx0.isoformat(), completion_at=tx1.isoformat()),
    }
    checkpoints['sess-idle']['turn_starts'].append(
        _turn_start('sess-idle', observed_at=t_start2.isoformat(),
                    input_processed_total=100, output_tokens=50))
    checkpoints['sess-idle']['completions'].append(
        _completion('sess-idle', observed_at=t_end2.isoformat(),
                    input_processed_total=200, output_tokens=100))
    facts = _facts_by_session([
        _fact('sess-idle', started_at=t0.isoformat(), ended_at=t_end2.isoformat(),
              input_processed_total=200, output_tokens=100),
        _fact('sess-x', started_at=tx0.isoformat(), ended_at=tx1.isoformat()),
    ])

    cal = compute_calibration(samples, checkpoints, facts, provider='claude', window_scope='all')
    iv = next(v for v in cal['all_intervals'] if v['start'] == tx0)
    assert iv['coverage_complete'] is True
    assert iv['session_ids'] == {'sess-x'}


def test_turn_crossing_interval_boundary_still_straddles_with_turn_start_present():
    """The idle-gap fix must never hide a REAL straddle: a turn whose OWN
    active span (turn_start -> completion) genuinely crosses an interval
    boundary -- the session was mid-turn, not idle, when the interval's
    sample was taken -- still marks that interval incomplete exactly as a
    baseline-anchored turn 1 does (mirrors
    test_calibration_turn_crossing_interval_boundary_marks_it_incomplete,
    but for a turn 2+ anchored by turn_start instead of baseline)."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)  # sess-0's own interval
    t1 = t0 + timedelta(minutes=2)
    checkpoints['sess-0'] = _checkpoint(
        'sess-0', baseline_at=(t0 - timedelta(hours=1)).isoformat(),
        completion_at=(t0 - timedelta(minutes=50)).isoformat())
    checkpoints['sess-0']['turn_starts'].append(
        _turn_start('sess-0', observed_at=(t0 - timedelta(minutes=30)).isoformat(),
                    input_processed_total=100, output_tokens=50))
    checkpoints['sess-0']['completions'].append(
        _completion('sess-0', observed_at=t1.isoformat(),
                    input_processed_total=200, output_tokens=100))
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'insufficient_samples'
    assert cal['eligible_interval_count'] == 4


def test_old_data_without_turn_start_behaves_exactly_as_before():
    """No turn_starts entries at all (rows written before this fix) must
    fall back to the prior (conservative) behaviour -- span from the
    previous checkpoint, folding idle time into the turn -- so this asserts
    the pre-fix result unchanged: same fixture and expectation as
    test_calibration_turn_crossing_interval_boundary_marks_it_incomplete,
    proving the turn_start-aware code path is a strict addition, not a
    replacement, when there is nothing to prefer."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=2)
    checkpoints['sess-0'] = _checkpoint(
        'sess-0', baseline_at=(t0 - timedelta(minutes=30)).isoformat(),
        completion_at=t1.isoformat())
    assert checkpoints['sess-0']['turn_starts'] == []
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'insufficient_samples'
    assert cal['eligible_interval_count'] == 4


def test_calibration_reaches_ok_with_idle_sessions_present():
    """Synthetic multi-session scenario proving the fix's actual point, at
    the shape of the live evidence (790 samples -> 111 intervals, 0
    coverage_complete, every one blocked by >=1 straddling turn): three
    ADDITIONAL sessions each go idle for ~1h25 BEFORE the fixture's own
    10:00-11:22 calibration span and don't complete their next turn until
    AFTER it -- i.e. their idle gap fully SPANS all five calibration
    intervals, exactly how a long-lived Mode B chat that last spoke an hour
    ago and won't speak again for another hour sits astride any short
    calibration window that falls in between. Before this fix, each idle
    session's single (completion -> next completion) turn would cross every
    one of the five fixture intervals -- proven below by asserting this
    exact scenario against the PRE-fix code path fails first (run with the
    fix's changes to mc/usage_breakdown_aggregate.py reverted: eligible_
    interval_count drops from 5 to 0 and status never reaches 'ok'). With
    turn_start recorded, the idle span is simply absent from each session's
    evidence, so it never touches the five fixture intervals at all -- only
    the five originally-measured sessions do, exactly matching
    _calibration_fixture()'s own expectation with no idle sessions."""
    samples, checkpoints, facts = _calibration_fixture()
    for i in range(3):
        sid = f'sess-idle-{i}'
        t0 = datetime(2026, 9, 28, 8, 30, 0, tzinfo=timezone.utc) + timedelta(minutes=5 * i)  # before 10:00
        t1a = t0 + timedelta(minutes=2)
        t_start2 = datetime(2026, 9, 28, 11, 30, 0, tzinfo=timezone.utc) + timedelta(minutes=5 * i)  # after 11:22
        t_end2 = t_start2 + timedelta(minutes=2)
        checkpoints[sid] = _checkpoint(sid, baseline_at=t0.isoformat(), completion_at=t1a.isoformat())
        checkpoints[sid]['turn_starts'].append(
            _turn_start(sid, observed_at=t_start2.isoformat(),
                        input_processed_total=100, output_tokens=50))
        checkpoints[sid]['completions'].append(
            _completion(sid, observed_at=t_end2.isoformat(),
                        input_processed_total=200, output_tokens=100))
        facts.append(_fact(sid, started_at=t0.isoformat(), ended_at=t_end2.isoformat(),
                            input_processed_total=200, output_tokens=100))
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'ok'
    assert cal['eligible_interval_count'] == 5
    assert cal['distinct_session_count'] == 5


# ── sample_tick fix (backlog 4668eafc follow-up, 2026-09-29) ────────────
#
# Measured 2026-09-29 (Dave, _scratch/calib_diag.py): even with turn_start
# shipped, 165/166 shape-eligible interval pairs against the live DB were
# STILL refused, because a Clayrune turn routinely outlives the sampler's
# own <=10-minute pairing window. These four tests build on top of
# _calibration_fixture()'s existing 5-interval/5-session base (already
# satisfying _MIN_ELIGIBLE_INTERVALS/_MIN_ELIGIBLE_SESSIONS on its own) with
# an ADDITIONAL block of contiguous samples far enough away in time
# (hours) that it can never accidentally pair with the base fixture's own
# samples -- isolating what the added session(s) contribute.

def test_calibration_long_turn_split_by_sample_ticks_becomes_eligible():
    """A single turn spanning four consecutive sample intervals, with a
    sample_tick at each intermediate boundary, is split into four segments
    that are each fully contained in their own interval -- all four become
    eligible instead of one long turn crossing every boundary it touches."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 15, 0, 0, tzinfo=timezone.utc)  # well past the base fixture's span
    boundaries = [t0 + timedelta(minutes=5 * i) for i in range(5)]  # 4 intervals
    for i, b in enumerate(boundaries):
        samples.append(_sample(raw_utilization=40.0 + 5 * i, source_observed_at=b.isoformat()))
    ck = _checkpoint('sess-long', baseline_at=boundaries[0].isoformat(), completion_at=None)
    ck['sample_ticks'] = [_sample_tick('sess-long', observed_at=boundaries[i].isoformat(),
                                        input_processed_total=100 * i, output_tokens=50 * i)
                           for i in range(1, 4)]
    ck['completions'] = [_completion('sess-long', observed_at=boundaries[4].isoformat(),
                                      input_processed_total=400, output_tokens=200)]
    checkpoints['sess-long'] = ck
    facts.append(_fact('sess-long', started_at=boundaries[0].isoformat(),
                        ended_at=boundaries[4].isoformat(), input_processed_total=400, output_tokens=200))
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'ok'
    assert cal['eligible_interval_count'] == 5 + 4
    assert cal['distinct_session_count'] == 5 + 1
    new_intervals = [iv for iv in cal['all_intervals'] if iv['start'] >= t0]
    assert len(new_intervals) == 4
    for iv in new_intervals:
        assert iv['coverage_complete'] is True
        assert iv['session_ids'] == {'sess-long'}
        assert iv['input_processed_total'] == 100
        assert iv['output_tokens'] == 50


def test_calibration_concurrent_ticked_sessions_sum_per_interval():
    """Two DIFFERENT sessions running concurrently, each ticked at the same
    boundaries, contribute their OWN measured delta to a shared interval --
    the interval sums both, exactly like the pre-existing (short-turn)
    concurrent-session rule, now proven for tick-segmented long turns."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 16, 0, 0, tzinfo=timezone.utc)
    b0, b1, b2 = t0, t0 + timedelta(minutes=5), t0 + timedelta(minutes=10)
    samples.append(_sample(raw_utilization=60.0, source_observed_at=b0.isoformat()))
    samples.append(_sample(raw_utilization=65.0, source_observed_at=b1.isoformat()))
    samples.append(_sample(raw_utilization=70.0, source_observed_at=b2.isoformat()))
    ck_a = _checkpoint('sess-long-a', baseline_at=b0.isoformat(), completion_at=None)
    ck_a['sample_ticks'] = [_sample_tick('sess-long-a', observed_at=b1.isoformat(),
                                          input_processed_total=100, output_tokens=50)]
    ck_a['completions'] = [_completion('sess-long-a', observed_at=b2.isoformat(),
                                        input_processed_total=200, output_tokens=100)]
    ck_b = _checkpoint('sess-long-b', baseline_at=b0.isoformat(), completion_at=None)
    ck_b['sample_ticks'] = [_sample_tick('sess-long-b', observed_at=b1.isoformat(),
                                          input_processed_total=40, output_tokens=20)]
    ck_b['completions'] = [_completion('sess-long-b', observed_at=b2.isoformat(),
                                        input_processed_total=80, output_tokens=40)]
    checkpoints['sess-long-a'] = ck_a
    checkpoints['sess-long-b'] = ck_b
    facts.append(_fact('sess-long-a', started_at=b0.isoformat(), ended_at=b2.isoformat(),
                        input_processed_total=200, output_tokens=100))
    facts.append(_fact('sess-long-b', started_at=b0.isoformat(), ended_at=b2.isoformat(),
                        input_processed_total=80, output_tokens=40))
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'ok'
    assert cal['eligible_interval_count'] == 5 + 2
    assert cal['distinct_session_count'] == 5 + 2
    new_intervals = [iv for iv in cal['all_intervals'] if iv['start'] >= t0]
    assert len(new_intervals) == 2
    for iv in new_intervals:
        assert iv['coverage_complete'] is True
        assert iv['session_ids'] == {'sess-long-a', 'sess-long-b'}
        assert iv['input_processed_total'] == 140
        assert iv['output_tokens'] == 70


def test_calibration_session_without_a_boundary_tick_stays_incomplete():
    """A session ticked at the FIRST boundary but not the second/third: its
    first segment (baseline -> tick) is fully contained and eligible, but
    its second segment (tick -> completion, with no tick at the boundaries
    in between) still crosses the later intervals -- those stay incomplete,
    exactly as an untracked long turn always has. Ticking some boundaries
    does not retroactively cover the ones with no tick."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 17, 0, 0, tzinfo=timezone.utc)
    b0 = t0
    b1 = t0 + timedelta(minutes=5)
    b2 = t0 + timedelta(minutes=10)
    b3 = t0 + timedelta(minutes=15)
    for b, util in ((b0, 80.0), (b1, 85.0), (b2, 90.0), (b3, 95.0)):
        samples.append(_sample(raw_utilization=util, source_observed_at=b.isoformat()))
    ck = _checkpoint('sess-partial', baseline_at=b0.isoformat(), completion_at=None)
    ck['sample_ticks'] = [_sample_tick('sess-partial', observed_at=b1.isoformat(),
                                        input_processed_total=100, output_tokens=50)]
    # No tick at b2 -- this segment (b1 -> completion at b3) crosses both
    # the [b1,b2) and [b2,b3) intervals without landing on either boundary.
    ck['completions'] = [_completion('sess-partial', observed_at=b3.isoformat(),
                                      input_processed_total=500, output_tokens=300)]
    checkpoints['sess-partial'] = ck
    facts.append(_fact('sess-partial', started_at=b0.isoformat(), ended_at=b3.isoformat(),
                        input_processed_total=500, output_tokens=300))
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    # 5 base intervals + only the one new interval sess-partial actually
    # ticked both ends of ([b0,b1)); [b1,b2) and [b2,b3) stay incomplete.
    assert cal['status'] == 'ok'
    assert cal['eligible_interval_count'] == 5 + 1
    new_intervals = {iv['start']: iv for iv in cal['all_intervals'] if iv['start'] >= t0}
    assert len(new_intervals) == 3
    assert new_intervals[b0]['coverage_complete'] is True
    assert new_intervals[b0]['input_processed_total'] == 100
    assert new_intervals[b1]['coverage_complete'] is False
    assert new_intervals[b2]['coverage_complete'] is False


def test_calibration_reset_crossed_pair_never_becomes_a_candidate_regardless_of_ticks():
    """A reset crossing the two allowance samples themselves (mismatched
    resets_at) must exclude that pair as an interval CANDIDATE before
    session evidence is ever consulted -- a fully-ticked session must not
    be able to rescue a reset-crossed pair into existence."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 18, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=5)
    samples.append(_sample(raw_utilization=10.0, source_observed_at=t0.isoformat(),
                            resets_at='2026-10-01T00:00:00+00:00'))
    samples.append(_sample(raw_utilization=20.0, source_observed_at=t1.isoformat(),
                            resets_at='2026-10-08T00:00:00+00:00'))  # different resets_at = reset crossed
    ck = _checkpoint('sess-reset', baseline_at=t0.isoformat(), completion_at=None)
    ck['sample_ticks'] = [_sample_tick('sess-reset', observed_at=t1.isoformat(),
                                        input_processed_total=100, output_tokens=50)]
    checkpoints['sess-reset'] = ck
    facts.append(_fact('sess-reset', started_at=t0.isoformat(), ended_at=t1.isoformat()))
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'ok'
    assert cal['eligible_interval_count'] == 5  # unchanged from the base fixture
    assert not any(iv['start'] == t0 for iv in cal['all_intervals'])


# ── segmented bar ────────────────────────────────────────────────────────

def test_segmented_bar_no_calibration_puts_whole_delta_in_unattributed():
    bar_change = {'status': 'ok', 'delta_pp': 8.0}
    totals = compute_totals([_fact('s1', started_at='2026-09-28T10:00:00Z')], {})
    calibration = {'status': 'insufficient_samples'}
    seg = compute_segmented_bar(bar_change, totals, calibration)
    assert seg['status'] == 'insufficient_calibration'
    assert seg['unattributed_pp'] == 8.0
    assert seg['estimated_pp'] is None


def test_segmented_bar_positive_delta_no_clayrune_activity_is_fully_unattributed():
    bar_change = {'status': 'ok', 'delta_pp': 5.0}
    totals = compute_totals([], {})  # zero Clayrune sessions this window
    calibration = {'status': 'insufficient_samples'}
    seg = compute_segmented_bar(bar_change, totals, calibration)
    assert seg['unattributed_pp'] == 5.0


def test_segmented_bar_estimate_exceeding_observed_is_flagged_not_clamped():
    bar_change = {'status': 'ok', 'delta_pp': 1.0}
    totals = {'tokens': {'input_fresh': 100, 'input_processed_total': 1000, 'output_tokens': 1000}}
    calibration = {'status': 'ok', 'workload_per_point_median': 1.0,
                   'workload_per_point_p10': 1.0, 'workload_per_point_p90': 1.0}
    seg = compute_segmented_bar(bar_change, totals, calibration)
    assert seg['status'] == 'estimate_exceeds_observed'
    assert seg['unattributed_pp'] < 0


# ── full assembly ────────────────────────────────────────────────────────

def test_build_breakdown_no_runs_empty_state():
    out = build_breakdown(
        provider='claude', window_kind='5h', window_scope='all',
        range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z',
        dimension='project', sort_by='input',
        range_samples=[], calibration_samples=[],
        session_facts=[], checkpoints={}, code_deltas={}, coverage_begins=None,
    )
    assert out['empty_state'] == 'no_runs'
    assert out['totals']['session_count'] == 0


def test_build_breakdown_incomplete_coverage_not_reported_as_no_runs():
    """P2-4 re-review (docs/_journal/4668eafc-mc998-fenn-review.md,
    "2026-09-28 re-review" finding 4): a session overlapping the window but
    excluded from the totals because its delta isn't isolated to this range
    must not render the same 'no_runs' empty state as a window with zero
    activity -- the work happened, it just isn't measurable in this window."""
    crossing = _fact('crossing', provider='claude',
                      started_at='2026-09-28T11:59:00Z', ended_at='2026-09-28T12:06:00Z')
    out = build_breakdown(
        provider='claude', window_kind='5h', window_scope='all',
        range_start='2026-09-28T12:00:00Z', range_end='2026-09-28T12:06:00Z',
        dimension='project', sort_by='input',
        range_samples=[], calibration_samples=[],
        session_facts=[crossing], checkpoints={}, code_deltas={}, coverage_begins=None,
    )
    assert out['totals']['session_count'] == 0
    assert out['totals']['incomplete_coverage_session_count'] == 1
    assert out['empty_state'] == 'incomplete_coverage'


def test_build_breakdown_end_to_end_ok_path():
    samples, checkpoints, cal_facts = _calibration_fixture()
    range_samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z'),
                      _sample(raw_utilization=25.0, source_observed_at='2026-09-28T10:05:00Z')]
    extra = _fact('extra', provider='claude', started_at='2026-09-28T09:30:00Z',
                   ended_at='2026-09-28T09:45:00Z')
    session_facts = cal_facts + [extra]
    out = build_breakdown(
        provider='claude', window_kind='5h', window_scope='all',
        range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z',
        dimension='character', sort_by='input',
        range_samples=range_samples, calibration_samples=samples,
        session_facts=session_facts, checkpoints=checkpoints, code_deltas={},
        coverage_begins='2026-09-01T00:00:00Z',
    )
    assert out['empty_state'] is None
    assert out['tokens_per_point']['status'] == 'ok'
    assert out['bar_change']['status'] == 'ok'
    assert out['segmented_bar']['status'] in ('ok', 'estimate_exceeds_observed')
    assert out['totals']['session_count'] == len(session_facts)


# ── round 3 (docs/_journal/4668eafc-mc998-fenn-review.md "Round 3") ─────

def test_calibration_resumed_session_running_after_its_last_completion_blocks_isolation():
    """Round 3 #2 (P1-2): a session that finished a turn BEFORE the five
    intervals and was then resumed has a fact re-marked 'running' by the
    dispatch writer, while its newest checkpoint is still the old
    completion. Its unfinished turn overlaps every interval, so none of
    them had exactly one active session. aa0f99f read the last completion
    as the session's end and unlocked calibration (status 'ok')."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
    prior_start = (t0 - timedelta(minutes=30)).isoformat()
    prior_end = (t0 - timedelta(minutes=10)).isoformat()
    checkpoints['resumed'] = _checkpoint('resumed', baseline_at=prior_start, completion_at=prior_end)
    running = _fact('resumed', started_at=prior_start, ended_at=prior_end)
    running['status'] = 'running'
    facts.append(running)
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'insufficient_samples'
    assert cal['eligible_interval_count'] == 0


def test_build_breakdown_live_first_turn_is_incomplete_not_no_runs():
    """Round 3 #3a (P1-3): a first-turn session has only its dispatch
    baseline checkpoint -- no completion, no fact yet. It is working inside
    the window, so the window must say incomplete coverage, never 'No
    runs'. aa0f99f only walked session_fact and returned 'no_runs'."""
    out = build_breakdown(
        provider='claude', window_kind='5h', window_scope='all',
        range_start='2026-09-28T12:00:00Z', range_end='2026-09-28T13:00:00Z',
        dimension='project', sort_by='input',
        range_samples=[], calibration_samples=[], session_facts=[],
        checkpoints={'live': _checkpoint('live', baseline_at='2026-09-28T12:10:00Z')},
        code_deltas={}, coverage_begins=None,
    )
    assert out['empty_state'] != 'no_runs'
    assert out['totals']['incomplete_coverage_session_count'] == 1
    assert out['totals']['session_count'] == 0


def test_filter_fact_ahead_of_checkpoint_history_marks_the_window_incomplete():
    """Round 3 #3b (P1-3): a v2 store kept only the FIRST completion (100
    at 12:05); the fact kept updating to 500 at 13:05. After migration the
    history says 100 while the fact says 500 -- the 400 in between has no
    checkpoint. The early window gets the measured 100 and is flagged
    incomplete; aa0f99f charged the fact's 500 to it with no flag."""
    ck = _checkpoint('mig', baseline_at='2026-09-28T12:00:00Z',
                      completion_at='2026-09-28T12:05:00Z',
                      input_processed_total=100, output_tokens=0)
    fact = _fact('mig', started_at='2026-09-28T12:00:00Z', ended_at='2026-09-28T13:05:00Z',
                  input_processed_total=500, output_tokens=0)
    rows, incomplete = filter_facts_in_range(
        [fact], {'mig': ck}, provider='claude',
        range_start='2026-09-28T12:00:00Z', range_end='2026-09-28T12:30:00Z')
    assert incomplete >= 1
    assert [r['input_processed_total'] for r in rows] == [100]


def test_filter_multiturn_later_window_gets_its_own_turn_delta():
    """Round 3 #4 (P2-4): two turns -- cumulative 100 at 12:05, 500 at
    13:05. A window holding only the second turn (12:05 -> 13:05) shows
    that turn's own 400; one holding only the first shows 100. aa0f99f
    required the whole session lifetime inside the window, so the later
    window showed nothing (and a window holding both got the fact's 500
    only because the lifetime happened to fit)."""
    ck = _checkpoint('two', baseline_at='2026-09-28T12:00:00Z',
                      completion_at='2026-09-28T12:05:00Z',
                      input_processed_total=100, output_tokens=0)
    ck['completions'].append(_completion('two', observed_at='2026-09-28T13:05:00Z',
                                          input_processed_total=500, output_tokens=0))
    fact = _fact('two', started_at='2026-09-28T12:00:00Z', ended_at='2026-09-28T13:05:00Z',
                  input_processed_total=500, output_tokens=0)

    def window(start, end):
        rows, incomplete = filter_facts_in_range([fact], {'two': ck}, provider='claude',
                                                 range_start=start, range_end=end)
        return compute_totals(rows, {}, incomplete_coverage_session_count=incomplete)

    later = window('2026-09-28T12:05:00Z', '2026-09-28T14:00:00Z')
    assert later['tokens']['input_processed_total'] == 400
    assert later['session_count'] == 1
    assert later['incomplete_coverage_session_count'] == 0
    earlier = window('2026-09-28T12:00:00Z', '2026-09-28T12:05:00Z')
    assert earlier['tokens']['input_processed_total'] == 100
    both = window('2026-09-28T12:00:00Z', '2026-09-28T14:00:00Z')
    assert both['tokens']['input_processed_total'] == 500
    # A window cutting the second turn in half can only show the first
    # turn and must say the rest was not measurable.
    cut = window('2026-09-28T12:00:00Z', '2026-09-28T12:30:00Z')
    assert cut['tokens']['input_processed_total'] == 100
    assert cut['incomplete_coverage_session_count'] == 1


def test_counter_reset_segment_is_unmeasured_not_a_confirmed_zero():
    """MC-1007 (backlog e91647ff): an MC session_id is deliberately kept
    across a provider-conversation rollover (auto-fresh / new Claude
    session), and the checkpoint counters are the CURRENT transcript's
    cumulative totals -- so they restart low. Measured 2026-09-29 on the live
    store: 18 such regressions over 8 sessions (e.g. 35bf35523b51 went
    19,372,004 -> 955,267 at 23:15Z). `_segment_delta` clamped each to
    `max(b - a, 0)`, reporting the reset turn as a 'complete' zero -- a
    fabricated zero hiding >=126M tokens. A regressed counter means the
    delta is unknown, so the segment must be 'unavailable'."""
    ck = _checkpoint('roll', baseline_at='2026-09-28T12:00:00Z',
                      completion_at='2026-09-28T12:05:00Z',
                      input_processed_total=500, output_tokens=50)
    ck['completions'].append(_completion('roll', observed_at='2026-09-28T13:05:00Z',
                                          input_processed_total=80, output_tokens=8))
    fact = _fact('roll', started_at='2026-09-28T12:00:00Z', ended_at='2026-09-28T13:05:00Z',
                  input_processed_total=80, output_tokens=8)
    rows, incomplete = filter_facts_in_range([fact], {'roll': ck}, provider='claude',
                                             range_start='2026-09-28T12:05:00Z',
                                             range_end='2026-09-28T14:00:00Z')
    totals = compute_totals(rows, {}, incomplete_coverage_session_count=incomplete)
    assert totals['tokens']['input_processed_total'] != 0
    assert totals['tokens']['output_tokens'] != 0
    assert totals['token_coverage_unavailable_count'] == 1
    # The turn BEFORE the reset is still a real, measured delta.
    rows, incomplete = filter_facts_in_range([fact], {'roll': ck}, provider='claude',
                                             range_start='2026-09-28T12:00:00Z',
                                             range_end='2026-09-28T12:05:00Z')
    assert compute_totals(rows, {}, incomplete_coverage_session_count=incomplete)[
        'tokens']['input_processed_total'] == 500


def test_calibration_interval_holding_a_counter_reset_is_not_eligible():
    """MC-1007: the same reset inside a calibration interval used to add a
    zero-token segment to a 'coverage_complete' interval, biasing
    tokens-per-point low. It must mark the interval incomplete instead."""
    samples, checkpoints, facts = _calibration_fixture()
    t0 = datetime(2026, 9, 28, 17, 0, 0, tzinfo=timezone.utc)
    b0, b1, b2 = t0, t0 + timedelta(minutes=5), t0 + timedelta(minutes=10)
    for i, b in enumerate((b0, b1, b2)):
        samples.append(_sample(raw_utilization=60.0 + 5 * i, source_observed_at=b.isoformat()))
    ck = _checkpoint('sess-roll', baseline_at=b0.isoformat(), completion_at=None)
    ck['sample_ticks'] = [_sample_tick('sess-roll', observed_at=b1.isoformat(),
                                        input_processed_total=900, output_tokens=90)]
    ck['completions'] = [_completion('sess-roll', observed_at=b2.isoformat(),
                                      input_processed_total=100, output_tokens=10)]
    checkpoints['sess-roll'] = ck
    facts.append(_fact('sess-roll', started_at=b0.isoformat(), ended_at=b2.isoformat(),
                        input_processed_total=100, output_tokens=10))
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    new_intervals = sorted((iv for iv in cal['all_intervals'] if iv['start'] >= t0),
                           key=lambda iv: iv['start'])
    assert len(new_intervals) == 2
    assert new_intervals[0]['coverage_complete'] is True
    assert new_intervals[0]['input_processed_total'] == 900
    assert new_intervals[1]['coverage_complete'] is False
