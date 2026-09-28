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


def _sample(*, raw_utilization, source_observed_at, resets_at='2026-10-05T00:00:00+00:00',
            quality='ok'):
    return {'raw_utilization': raw_utilization, 'source_observed_at': source_observed_at,
            'resets_at': resets_at, 'quality': quality}


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


# ── filter_facts_in_range ───────────────────────────────────────────────

def test_filter_scopes_to_provider_and_start_time_range():
    facts = [
        _fact('s1', provider='claude', started_at='2026-09-28T10:00:00Z'),
        _fact('s2', provider='codex', started_at='2026-09-28T10:00:00Z'),
        _fact('s3', provider='claude', started_at='2026-09-27T10:00:00Z'),
    ]
    got = filter_facts_in_range(facts, provider='claude',
                                 range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z')
    assert [f['session_id'] for f in got] == ['s1']


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


# ── calibration eligibility / AC2 ───────────────────────────────────────

def _calibration_fixture(n_pairs=5, distinct_sessions=3):
    """Each pair is 2 minutes wide (eligible on its own); pairs are spaced 20
    minutes apart so no cross-pair adjacency is ALSO eligible (the 10-minute
    max-gap rule excludes it) -- keeps the eligible-interval count exactly
    `n_pairs`, not `2*n_pairs - 1`."""
    samples, facts = [], []
    base = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(n_pairs):
        s_id = f'sess-{i % distinct_sessions}'
        t0 = base + timedelta(minutes=20 * i)
        t1 = t0 + timedelta(minutes=2)
        samples.append(_sample(raw_utilization=float(i * 10), source_observed_at=t0.isoformat()))
        samples.append(_sample(raw_utilization=float(i * 10 + 5), source_observed_at=t1.isoformat()))
        facts.append(_fact(s_id, started_at=(t0 - timedelta(hours=1)).isoformat(),
                            ended_at=(t1 + timedelta(hours=1)).isoformat()))
    return samples, facts


def test_calibration_ok_with_five_intervals_three_sessions():
    samples, facts = _calibration_fixture()
    cal = compute_calibration(samples, facts)
    assert cal['status'] == 'ok'
    assert cal['eligible_interval_count'] == 5
    assert cal['distinct_session_count'] == 3


def test_calibration_insufficient_with_only_four_intervals():
    samples, facts = _calibration_fixture(n_pairs=4)
    cal = compute_calibration(samples, facts)
    assert cal['status'] == 'insufficient_samples'


def test_calibration_reset_between_pair_excludes_that_interval():
    samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z',
                        resets_at='2026-10-01T00:00:00+00:00'),
               _sample(raw_utilization=20.0, source_observed_at='2026-09-28T10:05:00Z',
                       resets_at='2026-10-08T00:00:00+00:00')]  # different resets_at = reset crossed
    facts = [_fact('s1', started_at='2026-09-28T09:00:00Z', ended_at='2026-09-28T12:00:00Z')]
    cal = compute_calibration(samples, facts)
    assert cal['status'] == 'insufficient_samples'
    assert cal['eligible_interval_count'] == 0


def test_calibration_gap_over_ten_minutes_excludes_interval():
    samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z'),
               _sample(raw_utilization=20.0, source_observed_at='2026-09-28T10:15:00Z')]
    facts = [_fact('s1', started_at='2026-09-28T09:00:00Z', ended_at='2026-09-28T12:00:00Z')]
    cal = compute_calibration(samples, facts)
    assert cal['eligible_interval_count'] == 0


def test_calibration_no_session_activity_excluded_not_counted_as_eligible():
    samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z'),
               _sample(raw_utilization=20.0, source_observed_at='2026-09-28T10:05:00Z')]
    cal = compute_calibration(samples, [])  # no Clayrune activity at all
    assert cal['status'] == 'insufficient_samples'
    assert cal['eligible_interval_count'] == 0


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
        range_samples=[], calibration_samples=[], calibration_facts=[],
        session_facts=[], code_deltas={}, coverage_begins=None,
    )
    assert out['empty_state'] == 'no_runs'
    assert out['totals']['session_count'] == 0


def test_build_breakdown_end_to_end_ok_path():
    samples, cal_facts = _calibration_fixture()
    range_samples = [_sample(raw_utilization=10.0, source_observed_at='2026-09-28T10:00:00Z'),
                      _sample(raw_utilization=25.0, source_observed_at='2026-09-28T10:05:00Z')]
    session_facts = cal_facts + [_fact('extra', provider='claude', started_at='2026-09-28T09:30:00Z')]
    out = build_breakdown(
        provider='claude', window_kind='5h', window_scope='all',
        range_start='2026-09-28T00:00:00Z', range_end='2026-09-29T00:00:00Z',
        dimension='character', sort_by='input',
        range_samples=range_samples, calibration_samples=samples, calibration_facts=cal_facts,
        session_facts=session_facts, code_deltas={}, coverage_begins='2026-09-01T00:00:00Z',
    )
    assert out['empty_state'] is None
    assert out['tokens_per_point']['status'] == 'ok'
    assert out['bar_change']['status'] == 'ok'
    assert out['segmented_bar']['status'] in ('ok', 'estimate_exceeds_observed')
