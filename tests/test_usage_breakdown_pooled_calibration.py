"""Backlog 4668eafc follow-up 2 (MC-998): the tokens-per-point calibration is
a POOLED ratio (sum tokens / sum delta_pp over every coverage-complete,
session-bearing interval, delta-0 pairs included), not the median of
per-interval ratios over the delta>=1 pairs.

Why: `raw_utilization` is whole points. A pair's delta is 0 for most pairs
and 1 on the minute the counter ticks; the consumption behind that tick
accrued over the delta-0 pairs before it. The old median paired a bucket's
worth of vendor movement with ONE pair's tokens. Live symptom, 2026-09-30:
5h bar change 47pp, estimate 171pp ("estimate_exceeds_observed").
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.usage_breakdown_aggregate import (  # noqa: E402
    _contributing, build_breakdown, compute_calibration,
)
from tests.test_usage_breakdown_aggregate import (  # noqa: E402
    _checkpoint, _completion, _fact, _facts_by_session, _sample, _sample_tick,
)

T0 = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc)
PER_PAIR_INPUT = 1000  # per session per 2-minute pair
N_PAIRS = 40
N_SESSIONS = 3


def _even_series(*, tick_every, n_sessions=N_SESSIONS, input_per_pair=PER_PAIR_INPUT,
                 output_per_pair=0):
    """`n_sessions` Clayrune sessions alive for the whole span, each adding
    exactly `input_per_pair` input tokens per 2-minute pair, sampled every 2
    minutes. The counter ticks +1pp every `tick_every`-th pair, so the TRUE
    rate is tick_every * n_sessions * input_per_pair input tokens per point."""
    times = [T0 + timedelta(minutes=2 * i) for i in range(N_PAIRS + 1)]
    samples = [_sample(raw_utilization=float(i // tick_every), source_observed_at=t.isoformat())
               for i, t in enumerate(times)]
    checkpoints, facts = {}, []
    for k in range(n_sessions):
        sid = f'sess-{k}'
        ck = _checkpoint(sid, baseline_at=times[0].isoformat(), completion_at=None)
        ck['sample_ticks'] = [
            _sample_tick(sid, observed_at=times[i].isoformat(),
                         input_processed_total=input_per_pair * i, output_tokens=output_per_pair * i)
            for i in range(1, N_PAIRS)]
        ck['completions'] = [_completion(sid, observed_at=times[N_PAIRS].isoformat(),
                                          input_processed_total=input_per_pair * N_PAIRS,
                                          output_tokens=output_per_pair * N_PAIRS)]
        checkpoints[sid] = ck
        facts.append(_fact(sid, started_at=times[0].isoformat(), ended_at=times[-1].isoformat(),
                           input_processed_total=input_per_pair * N_PAIRS,
                           output_tokens=output_per_pair * N_PAIRS))
    return samples, checkpoints, facts


def test_pooled_calibration_recovers_true_rate_when_counter_ticks_every_4th_pair():
    """The regression. Tokens accrue evenly, the counter ticks every 4th
    pair -> true input/pt = 4 * 3 * 1000 = 12000. The old median over the
    delta>=1 pairs saw 3000 tokens against 1pp (4x low)."""
    samples, checkpoints, facts = _even_series(tick_every=4, output_per_pair=100)
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'ok'
    assert cal['total_delta_pp'] == 10.0
    assert cal['eligible_interval_count'] == N_PAIRS  # the 30 delta-0 pairs count too
    assert cal['input_per_point'] == 12000.0
    assert cal['workload_per_point'] == 13200.0


def test_breakdown_tokens_per_point_median_is_the_true_rate_not_the_tick_minute():
    """Same series through the wire contract (`tokens_per_point.median`,
    the key the UI and smokes read) so the old estimator's 4x-low reading
    fails here on its own terms."""
    samples, checkpoints, facts = _even_series(tick_every=4)
    out = build_breakdown(
        provider='claude', window_kind='5h', window_scope='all',
        range_start=samples[0]['source_observed_at'], range_end=samples[-1]['source_observed_at'],
        dimension='project', sort_by='input', range_samples=samples, calibration_samples=samples,
        session_facts=facts, checkpoints=checkpoints, code_deltas={}, coverage_begins='2026-09-30')
    tpp = out['tokens_per_point']
    assert tpp['status'] == 'ok'
    assert tpp['median'] == 12000.0
    assert tpp['estimator'] == 'pooled_ratio'
    assert tpp['range_method'] == 'block_bootstrap_p10_p90'
    # 3 sessions x 40 pairs x 1000 tokens = 120k over 10pp observed -> the
    # bar's own estimate must land on the observed 10pp, not 4x it.
    seg = out['segmented_bar']
    assert seg['status'] == 'ok'
    assert abs(seg['estimated_pp'] - 10.0) < 1e-9


def test_total_delta_below_five_points_is_insufficient():
    """Plenty of intervals and sessions, but only 4pp of total vendor
    movement: one quantisation bucket more or less moves the ratio 25%."""
    samples, checkpoints, facts = _even_series(tick_every=10)
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'insufficient_samples'
    assert cal['total_delta_pp'] == 4.0
    assert cal['eligible_interval_count'] == N_PAIRS
    assert cal['distinct_session_count'] == N_SESSIONS


def test_zero_total_tokens_is_insufficient_not_a_zero_divisor():
    samples, checkpoints, facts = _even_series(tick_every=4, input_per_pair=0)
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'insufficient_samples'


def test_zero_token_zero_delta_pair_does_not_count_toward_the_gate():
    base = {'coverage_complete': True, 'session_ids': {'s'}, 'delta_pp': 0.0,
            'input_processed_total': 0, 'output_tokens': 0}
    assert _contributing(base) is False
    assert _contributing({**base, 'input_processed_total': 1}) is True   # delta-0 pair with tokens
    assert _contributing({**base, 'delta_pp': 1.0}) is True               # tick with no tokens
    assert _contributing({**base, 'input_processed_total': 1, 'coverage_complete': False}) is False
    assert _contributing({**base, 'input_processed_total': 1, 'session_ids': set()}) is False


def test_bootstrap_range_brackets_the_pooled_ratio_and_is_deterministic():
    """Six separate one-pair runs (20 min apart, so never adjacent), each 5pp
    but with different token loads -> per-run rates 200..1200 input/pt,
    pooled 700. The range must bracket the pooled value strictly, stay
    inside the per-run extremes, and be identical on a second call."""
    samples, checkpoints, facts = [], {}, []
    base = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(6):
        sid = f'sess-{i}'
        t0 = base + timedelta(minutes=20 * i)
        t1 = t0 + timedelta(minutes=2)
        samples.append(_sample(raw_utilization=float(i * 10), source_observed_at=t0.isoformat()))
        samples.append(_sample(raw_utilization=float(i * 10 + 5), source_observed_at=t1.isoformat()))
        checkpoints[sid] = _checkpoint(sid, baseline_at=t0.isoformat(), completion_at=t1.isoformat(),
                                        input_processed_total=1000 * (i + 1), output_tokens=0)
        facts.append(_fact(sid, started_at=t0.isoformat(), ended_at=t1.isoformat(),
                           input_processed_total=1000 * (i + 1), output_tokens=0))
    kw = dict(provider='claude', window_scope='all')
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts), **kw)
    assert cal['status'] == 'ok'
    assert cal['run_count'] == 6
    assert cal['input_per_point'] == 700.0
    assert 200.0 <= cal['input_per_point_lo'] < 700.0 < cal['input_per_point_hi'] <= 1200.0
    again = compute_calibration(samples, checkpoints, _facts_by_session(facts), **kw)
    assert (again['input_per_point_lo'], again['input_per_point_hi']) == \
        (cal['input_per_point_lo'], cal['input_per_point_hi'])


def test_eligible_intervals_derives_each_sessions_evidence_once(monkeypatch):
    """Keeping delta-0 pairs took the live 90-day history to ~2000 pairs;
    re-deriving every session's evidence inside the per-pair loop made the
    breakdown route take 16s+ per call and the dashboard's 7d request time
    out ("Breakdown failed to load", 2026-09-30). Evidence is per-session,
    not per-interval, so it must be derived once per session."""
    import mc.usage_breakdown_aggregate as agg
    samples, checkpoints, facts = _even_series(tick_every=4)
    calls = []
    real = agg._session_evidence
    monkeypatch.setattr(agg, '_session_evidence',
                        lambda ck, fact: calls.append(1) or real(ck, fact))
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['input_per_point'] == 12000.0
    assert len(calls) == N_SESSIONS
