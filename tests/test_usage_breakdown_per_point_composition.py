"""Backlog 4668eafc follow-up 7 (MC-998): the tokens-per-point figure also
reports what one point is MADE OF -- the per-point volume of each token type
(fresh input, cache reads, cache writes, output) and each type's share of the
workload. This is a description of the volume, not the vendor's weighting:
the components are collinear, so the data cannot separate their weights.

Live reference (5h, 2026-10-01): reads 5.13M/pt 97.1%, writes 126K 2.4%,
output 27K 0.5%, fresh ~100.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.usage_breakdown_aggregate import (  # noqa: E402
    _contributing, _eligible_intervals, build_breakdown, compute_calibration,
)
from tests.test_usage_breakdown_aggregate import (  # noqa: E402
    _checkpoint, _completion, _fact, _facts_by_session, _sample, _sample_tick,
)

T0 = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc)
N_PAIRS = 40
N_SESSIONS = 3
TICK_EVERY = 4
# per session per 2-minute pair
FRESH, WRITE, READ, OUT = 10, 100, 890, 100


def _split(row, i):
    """Overwrite a fixture row's cumulative counters with the 4-way split."""
    row['input_fresh'] = FRESH * i
    row['input_cache_write'] = WRITE * i
    row['input_cache_read'] = READ * i
    row['input_processed_total'] = (FRESH + WRITE + READ) * i
    row['output_tokens'] = OUT * i
    return row


def _series():
    times = [T0 + timedelta(minutes=2 * i) for i in range(N_PAIRS + 1)]
    samples = [_sample(raw_utilization=float(i // TICK_EVERY), source_observed_at=t.isoformat())
               for i, t in enumerate(times)]
    checkpoints, facts = {}, []
    for k in range(N_SESSIONS):
        sid = f'sess-{k}'
        ck = _checkpoint(sid, baseline_at=times[0].isoformat(), completion_at=None)
        ck['sample_ticks'] = [
            _split(_sample_tick(sid, observed_at=times[i].isoformat(),
                                input_processed_total=0, output_tokens=0), i)
            for i in range(1, N_PAIRS)]
        ck['completions'] = [_split(_completion(sid, observed_at=times[N_PAIRS].isoformat(),
                                                input_processed_total=0, output_tokens=0), N_PAIRS)]
        checkpoints[sid] = ck
        facts.append(_fact(sid, started_at=times[0].isoformat(), ended_at=times[-1].isoformat(),
                           input_processed_total=(FRESH + WRITE + READ) * N_PAIRS,
                           output_tokens=OUT * N_PAIRS))
    return samples, checkpoints, facts


# Totals over the whole span, and the 10pp the counter moves.
_TOT = {k: v * N_PAIRS * N_SESSIONS for k, v in
        {'input_fresh': FRESH, 'input_cache_write': WRITE,
         'input_cache_read': READ, 'output_tokens': OUT}.items()}
_PP = float(N_PAIRS // TICK_EVERY)
_WORKLOAD = sum(_TOT.values())


def test_eligible_intervals_carry_per_component_sums():
    samples, checkpoints, facts = _series()
    ivs = [iv for iv in _eligible_intervals(samples, checkpoints, _facts_by_session(facts),
                                             provider='claude', window_scope='all')
           if _contributing(iv)]
    for key, total in _TOT.items():
        assert sum(iv[key] for iv in ivs) == total, key
    # the new sums are a partition of the existing ones
    for iv in ivs:
        assert iv['input_fresh'] + iv['input_cache_write'] + iv['input_cache_read'] \
            == iv['input_processed_total']


def test_calibration_reports_per_point_value_and_share_for_each_component():
    samples, checkpoints, facts = _series()
    cal = compute_calibration(samples, checkpoints, _facts_by_session(facts),
                               provider='claude', window_scope='all')
    assert cal['status'] == 'ok'
    per_point = cal['component_per_point']
    share = cal['component_share']
    for key, total in _TOT.items():
        assert per_point[key] == total / _PP, key
        assert share[key] == total / _WORKLOAD, key
    assert abs(sum(share.values()) - 1.0) < 1e-12
    assert abs(sum(per_point.values()) - cal['workload_per_point']) < 1e-9


def test_breakdown_tokens_per_point_exposes_composition_and_pooled_rate():
    samples, checkpoints, facts = _series()
    out = build_breakdown(
        provider='claude', window_kind='5h', window_scope='all',
        range_start=samples[0]['source_observed_at'], range_end=samples[-1]['source_observed_at'],
        dimension='project', sort_by='input', range_samples=samples, calibration_samples=samples,
        session_facts=facts, checkpoints=checkpoints, code_deltas={}, coverage_begins='2026-09-30')
    tpp = out['tokens_per_point']
    assert tpp['status'] == 'ok'
    comp = tpp['composition']
    assert comp['cache_read']['per_point'] == _TOT['input_cache_read'] / _PP
    assert comp['cache_write']['per_point'] == _TOT['input_cache_write'] / _PP
    assert comp['output']['per_point'] == _TOT['output_tokens'] / _PP
    assert comp['fresh']['per_point'] == _TOT['input_fresh'] / _PP
    assert comp['cache_read']['share'] == _TOT['input_cache_read'] / _WORKLOAD
    # 'median' always held a pooled ratio-of-sums; the honest name is carried
    # alongside it and the legacy key is kept equal for old consumers.
    assert tpp['pooled_rate'] == tpp['median']
