"""Backlog 4668eafc follow-up 8 (MC-998): the tokens-per-point figure is also
translated into agent TURNS so "how much work do I get per 1%" has a unit.

A turn is a session's 'completion' checkpoint paired with the nearest
preceding 'turn_start' (or 'baseline') after the previous completion; its
size is the delta of input_processed_total + output_tokens. turns_per_point
divides the workload per point by the MEAN turn size (consistent with the
totals); the median-basis figure is the typical-turn reading.

Live reference (5h, 2026-10-01, claude only): 612 turns, mean 2.47M, median
0.78M, p10 0.15M, p90 6.33M -> 2.1 turns/pt mean-based, 6.8 median-based.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import usage_breakdown_aggregate as agg  # noqa: E402
from tests.test_usage_breakdown_aggregate import (  # noqa: E402
    _checkpoint, _completion, _fact, _facts_by_session, _sample_tick, _turn_start,
)
from tests.test_usage_breakdown_per_point_composition import (  # noqa: E402
    _PP, _WORKLOAD, N_PAIRS, _series,
)

T0 = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc)


def _at(minutes):
    return (T0 + timedelta(minutes=minutes)).isoformat()


def _sizes(ck_by_sid, facts=None, *, provider='claude', scope='all'):
    facts = facts if facts is not None else []
    return agg.compute_turn_sizes(ck_by_sid, _facts_by_session(facts),
                                  provider=provider, window_scope=scope)


def _two_turn_session(sid='s1', **kw):
    """baseline(0,0) -> completion@10 (1000+200) -> turn_start@60 -> completion@70 (+3000+500)."""
    ck = _checkpoint(sid, baseline_at=_at(0), completion_at=_at(10),
                     input_processed_total=1000, output_tokens=200, **kw)
    ck['turn_starts'].append(_turn_start(sid, observed_at=_at(60), input_processed_total=1000,
                                         output_tokens=200, **kw))
    ck['completions'].append(_completion(sid, observed_at=_at(70), input_processed_total=4000,
                                         output_tokens=700, **kw))
    return ck


def test_turn_is_completion_paired_with_nearest_preceding_start():
    out = _sizes({'s1': _two_turn_session()})
    assert out['turn_count'] == 2
    # turn 1 = baseline -> completion = 1200; turn 2 = turn_start -> completion = 3500
    assert out['mean'] == (1200 + 3500) / 2
    assert out['median'] == (1200 + 3500) / 2


def test_sample_ticks_do_not_split_a_turn():
    ck = _two_turn_session()
    ck['sample_ticks'] = [_sample_tick('s1', observed_at=_at(5), input_processed_total=500,
                                       output_tokens=100),
                          _sample_tick('s1', observed_at=_at(65), input_processed_total=2000,
                                       output_tokens=300)]
    out = _sizes({'s1': ck})
    assert out['turn_count'] == 2 and out['mean'] == 2350


def test_turn_without_a_start_after_the_previous_completion_is_skipped():
    ck = _two_turn_session()
    ck['turn_starts'] = []  # aged out: turn 2 must NOT pair across completion 1
    out = _sizes({'s1': ck})
    assert out['turn_count'] == 1 and out['mean'] == 1200


def test_none_counter_and_reset_are_skipped_not_counted_as_sizes():
    none_ck = _two_turn_session()
    none_ck['completions'][1]['output_tokens'] = None
    reset_ck = _two_turn_session('s2')
    reset_ck['completions'][1]['input_processed_total'] = 10  # below turn_start's 1000: reset (MC-1007)
    out = _sizes({'s1': none_ck, 's2': reset_ck})
    assert out['turn_count'] == 2  # only each session's first turn
    assert out['mean'] == 1200


def test_filters_by_provider_and_scope():
    claude = _two_turn_session('c', provider='claude')
    codex = _two_turn_session('x', provider='codex')
    cks = {'c': claude, 'x': codex}
    facts = [_fact('c', started_at=_at(0), ended_at=_at(70), observed_model='claude-opus-5-5'),
             _fact('x', provider='codex', started_at=_at(0), ended_at=_at(70))]
    assert _sizes(cks, facts, provider='claude')['turn_count'] == 2
    assert _sizes(cks, facts, provider='codex')['turn_count'] == 2
    assert _sizes(cks, facts, provider='claude', scope='opus')['turn_count'] == 2
    assert _sizes(cks, facts, provider='claude', scope='sonnet')['turn_count'] == 0
    # scope unconfirmed (no fact, so no model) is excluded from a scoped window
    assert _sizes({'c': claude}, [], provider='claude', scope='opus')['turn_count'] == 0


def test_stats_over_a_known_distribution():
    cks = {}
    for i, size in enumerate([100, 200, 300, 400, 1000]):
        sid = f'd{i}'
        cks[sid] = _checkpoint(sid, baseline_at=_at(0), completion_at=_at(5),
                               input_processed_total=size, output_tokens=0)
    out = _sizes(cks)
    assert out == {'turn_count': 5, 'mean': 400.0, 'median': 300.0,
                   'p10': 100 + 0.4 * 100, 'p90': 400 + 0.6 * 600}


def test_no_turns_gives_none_stats():
    assert _sizes({}) == {'turn_count': 0, 'mean': None, 'median': None, 'p10': None, 'p90': None}


def _series_with_turns(extra_per_session):
    """The composition fixture (3 sessions, one 44,000-token turn each, 10pp)
    plus `extra_per_session` 1,000-token turns after the measured span."""
    samples, checkpoints, facts = _series()
    for sid, ck in checkpoints.items():
        ins, out = 40 * 1000, 40 * 100
        for j in range(extra_per_session):
            t0 = N_PAIRS * 2 + 10 + 20 * j
            ck['turn_starts'].append(_turn_start(sid, observed_at=_at(t0), input_processed_total=ins,
                                                 output_tokens=out))
            ins, out = ins + 700, out + 300
            ck['completions'].append(_completion(sid, observed_at=_at(t0 + 5),
                                                 input_processed_total=ins, output_tokens=out))
    return samples, checkpoints, facts


def _breakdown(samples, checkpoints, facts, *, calibration_samples=None):
    return agg.build_breakdown(
        provider='claude', window_kind='5h', window_scope='all',
        range_start=samples[0]['source_observed_at'], range_end=samples[-1]['source_observed_at'],
        dimension='project', sort_by='input', range_samples=samples,
        calibration_samples=samples if calibration_samples is None else calibration_samples,
        session_facts=facts, checkpoints=checkpoints, code_deltas={}, coverage_begins='2026-09-30')


def test_payload_carries_turns_per_point_mean_and_median_basis():
    samples, checkpoints, facts = _series_with_turns(10)  # 3 x 44,000 + 30 x 1,000 = 33 turns
    tpp = _breakdown(samples, checkpoints, facts)['tokens_per_point']
    assert tpp['status'] == 'ok'
    ts = tpp['turn_size']
    assert ts['turn_count'] == 33
    assert ts['mean'] == (3 * 44000 + 30 * 1000) / 33
    assert ts['median'] == 1000
    workload_per_point = _WORKLOAD / _PP
    assert tpp['turns_per_point'] == workload_per_point / ts['mean']
    assert tpp['turns_per_point_median_basis'] == workload_per_point / 1000


def test_turns_per_point_is_none_below_twenty_turns():
    samples, checkpoints, facts = _series_with_turns(5)  # 3 + 15 = 18 turns
    tpp = _breakdown(samples, checkpoints, facts)['tokens_per_point']
    assert tpp['status'] == 'ok' and tpp['turn_size']['turn_count'] == 18
    assert tpp['turns_per_point'] is None
    assert tpp['turns_per_point_median_basis'] is None


def test_turns_per_point_is_none_when_calibration_is_not_ok():
    samples, checkpoints, facts = _series_with_turns(10)
    tpp = _breakdown(samples, checkpoints, facts, calibration_samples=[])['tokens_per_point']
    assert tpp['status'] != 'ok'
    assert tpp['turns_per_point'] is None and tpp['turns_per_point_median_basis'] is None
