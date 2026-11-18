"""P44 / OS48 (docs/_review/2026-11-16_07_platform.md, 2026-11-16_09_orchestration.md):
the Usage Breakdown took ~100 s per Claude request because
`_eligible_intervals` tested every allowance-sample pair against every
session's whole segment list (29,000 pairs x 1,400 sessions on the live
install), against a 25 s client timeout, behind a cache key that changed
every sampling minute.

Pins:
  * work scales with the sessions that can overlap a pair, not with all
    sessions: `_window_overlaps` call count on a 300-pair x 300-session
    fixture (the old loop made ~190,000 calls);
  * the fast path returns EXACTLY what the old per-pair loop returned.
    `_oracle_eligible_intervals` below is that loop, copied verbatim from
    master before the change -- it stays here as the reference, over
    randomised fixtures that include still-open spans, fact-only sessions,
    ended_unknown sessions, a scope the facts cannot confirm, and spans
    that touch an interval boundary exactly.
"""
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import usage_breakdown_aggregate as agg  # noqa: E402
from tests.test_usage_breakdown_aggregate import (  # noqa: E402
    _checkpoint, _completion, _fact, _sample, _sample_tick, _turn_start,
)

T0 = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
_COMPOSITION_KEYS = agg._COMPOSITION_KEYS


def _at(minute):
    return (T0 + timedelta(minutes=minute)).isoformat()


def _oracle_eligible_intervals(samples, checkpoints, facts_by_session, *, provider, window_scope):
    """`_eligible_intervals` as it stood on master before P44/OS48: every pair
    against every session. Reference only."""
    out = []
    ordered = sorted((s for s in samples if s.get('quality') == 'ok'
                       and s.get('raw_utilization') is not None
                       and s.get('source_observed_at')),
                      key=lambda s: s['source_observed_at'])
    all_sids = set(checkpoints.keys()) | set(facts_by_session.keys())
    evidence = []
    for sid in all_sids:
        ck = checkpoints.get(sid)
        fact = facts_by_session.get(sid)
        if agg._session_provider(ck, fact) != provider:
            continue
        scope_ok = agg._scope_matches(fact, window_scope)
        if scope_ok is False:
            continue
        turns, unmeasured = agg._session_evidence(ck, fact)
        evidence.append((sid, scope_ok, turns, unmeasured))
    for a, b in zip(ordered, ordered[1:]):
        if not agg._same_reset(a.get('resets_at'), b.get('resets_at')):
            continue
        if agg._reset_boundary_crossed(a['raw_utilization'], b['raw_utilization']):
            continue
        t_a, t_b = agg._parse_iso(a['source_observed_at']), agg._parse_iso(b['source_observed_at'])
        if t_a is None or t_b is None or t_b <= t_a:
            continue
        if (t_b - t_a) > timedelta(minutes=agg._MAX_INTERVAL_MINUTES):
            continue
        delta = b['raw_utilization'] - a['raw_utilization']
        if delta < 0:
            continue
        session_ids = set()
        coverage_complete = True
        input_processed_total = 0
        output_tokens = 0
        parts = dict.fromkeys(_COMPOSITION_KEYS, 0)
        for sid, scope_ok, turns, unmeasured in evidence:
            gap = any(agg._window_overlaps(s, e, t_a, t_b) for s, e in unmeasured)
            contained = [t for t in turns if t['start'] >= t_a and t['end'] <= t_b]
            crossing = [t for t in turns if t not in contained
                        and agg._window_overlaps(t['start'], t['end'], t_a, t_b)]
            if not (gap or contained or crossing):
                continue
            session_ids.add(sid)
            if scope_ok is None:
                coverage_complete = False
                continue
            if gap or crossing:
                coverage_complete = False
                continue
            if any(t['token_coverage'] != 'complete' or t['fact_only']
                   or t['input_processed_total'] is None or t['output_tokens'] is None
                   for t in contained):
                coverage_complete = False
                continue
            for t in contained:
                input_processed_total += t['input_processed_total']
                output_tokens += t['output_tokens']
                for k in _COMPOSITION_KEYS:
                    parts[k] += t.get(k) or 0
        out.append({
            'start': t_a, 'end': t_b, 'delta_pp': delta,
            'session_ids': session_ids,
            'coverage_complete': coverage_complete,
            'input_processed_total': input_processed_total,
            'output_tokens': output_tokens,
            **parts,
        })
    return out


def _fixture(seed, *, sessions, minutes, long_every=0, open_sessions=True):
    """Samples every minute (utilisation ticks up now and then, one reset
    drop mid-way) plus randomised sessions of every shape the evidence
    builder knows."""
    rng = random.Random(seed)
    samples, util = [], 10.0
    for m in range(minutes):
        if rng.random() < 0.25:
            util += 1.0
        if m == minutes // 2:
            util = 1.0  # a real reset: raw_utilization drops
        samples.append(_sample(raw_utilization=util, source_observed_at=_at(m),
                               resets_at=None if rng.random() < 0.03 else '2026-10-09T00:00:00+00:00'))
    checkpoints, facts = {}, []
    for i in range(sessions):
        sid = f's{i}'
        a = rng.randrange(0, minutes - 2)
        span = rng.choice([1, 2, 3, 5, 8]) if not (long_every and i % long_every == 0) else rng.randrange(20, minutes)
        b = min(minutes - 1, a + span)
        shapes = ['closed', 'closed', 'closed', 'running', 'fact_only', 'ended_unknown',
                  'no_baseline', 'ahead']
        if not open_sessions:  # a still-open session genuinely overlaps every later pair
            shapes.remove('running')
        shape = rng.choice(shapes)
        model = rng.choice(['claude-sonnet-5', 'claude-opus-5', ''])
        if shape == 'fact_only':
            facts.append(_fact(sid, started_at=_at(a), ended_at=_at(b), observed_model=model))
            continue
        ck = _checkpoint(sid, baseline_at=_at(a))
        total = 0
        for m in range(a, b + 1):
            if rng.random() < 0.7:  # a tick stamped at an allowance sample's own time
                total += rng.randrange(0, 500)
                ck.setdefault('sample_ticks', []).append(
                    _sample_tick(sid, observed_at=_at(m), input_processed_total=total, output_tokens=total // 10))
        ck.setdefault('sample_ticks', [])
        if rng.random() < 0.3 and b - a >= 2:
            ck['turn_starts'].append(_turn_start(sid, observed_at=_at(a + 1), input_processed_total=0,
                                                 output_tokens=0))
        status, ended = 'completed', _at(b)
        if shape in ('closed', 'ended_unknown', 'ahead', 'no_baseline'):
            ck['completions'].append(_completion(sid, observed_at=_at(b), input_processed_total=total + 100,
                                                 output_tokens=total // 10 + 10))
        if shape == 'running':
            status, ended = 'running', None
        if shape == 'ended_unknown':
            status = 'ended_unknown'
        if shape == 'ahead':
            ended = _at(min(minutes - 1, b + 3))
        if shape == 'no_baseline':
            ck['baseline'] = None
        facts.append(_fact(sid, started_at=_at(a), ended_at=ended, observed_model=model,
                           input_processed_total=total + 100, output_tokens=total // 10 + 10))
        facts[-1]['status'] = status
        checkpoints[sid] = ck
    return samples, checkpoints, {f['session_id']: f for f in facts}


def _canon(intervals):
    return [(iv['start'], iv['end'], iv['delta_pp'], frozenset(iv['session_ids']),
             iv['coverage_complete'], iv['input_processed_total'], iv['output_tokens'],
             *(iv[k] for k in _COMPOSITION_KEYS)) for iv in intervals]


@pytest.mark.parametrize('seed', range(8))
@pytest.mark.parametrize('scope', ['all', 'opus', 'sonnet'])
def test_fast_path_returns_exactly_what_the_pair_by_session_loop_returned(seed, scope):
    samples, checkpoints, facts = _fixture(seed, sessions=120, minutes=160,
                                           long_every=17 if seed % 2 else 0)
    new = agg._eligible_intervals(samples, checkpoints, facts, provider='claude', window_scope=scope)
    old = _oracle_eligible_intervals(samples, checkpoints, facts, provider='claude', window_scope=scope)
    assert new, 'fixture produced no intervals -- the comparison would prove nothing'
    assert _canon(new) == _canon(old)
    assert any(iv['session_ids'] for iv in new)
    assert any(not iv['coverage_complete'] for iv in new)


def test_fixture_exercises_the_measured_branch_too():
    samples, checkpoints, facts = _fixture(0, sessions=120, minutes=160)
    new = agg._eligible_intervals(samples, checkpoints, facts, provider='claude', window_scope='all')
    assert any(iv['coverage_complete'] and iv['session_ids'] and iv['input_processed_total'] for iv in new)


def test_samples_given_out_of_order_still_match():
    samples, checkpoints, facts = _fixture(3, sessions=60, minutes=120, long_every=11)
    shuffled = samples[:]
    random.Random(1).shuffle(shuffled)
    new = agg._eligible_intervals(shuffled, checkpoints, facts, provider='claude', window_scope='all')
    old = _oracle_eligible_intervals(shuffled, checkpoints, facts, provider='claude', window_scope='all')
    assert _canon(new) == _canon(old)


def test_work_scales_with_overlapping_sessions_not_with_all_sessions(monkeypatch):
    """300 sessions, 300 sampling minutes: the old loop made one
    `_window_overlaps` call per (pair, session-turn), ~190,000 here. A
    session far from the pair must not be looked at."""
    samples, checkpoints, facts = _fixture(5, sessions=300, minutes=300, open_sessions=False)
    calls = []
    real = agg._window_overlaps

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(agg, '_window_overlaps', counting)
    ivs = agg._eligible_intervals(samples, checkpoints, facts, provider='claude', window_scope='all')
    pairs = len(ivs)
    assert pairs > 250
    assert len(calls) < 20 * (pairs + 300), f'{len(calls)} overlap tests for {pairs} pairs x 300 sessions'


# ── the sweep module on its own ─────────────────────────────────────────────

from mc.usage_breakdown_sweep import TurnIndex, sessions_by_interval  # noqa: E402


def _turn(a, b):
    return {'start': T0 + timedelta(minutes=a), 'end': T0 + timedelta(minutes=b)}


def _w(a, b):
    return (T0 + timedelta(minutes=a), T0 + timedelta(minutes=b))


def _ev(sid, turns=(), unmeasured=()):
    return (sid, True, list(turns), list(unmeasured))


def test_sweep_returns_only_sessions_that_reach_each_window_in_original_order():
    evidence = [_ev('late', [_turn(100, 105)]), _ev('early', [_turn(0, 5)]),
                _ev('open', unmeasured=[(T0 + timedelta(minutes=50), None)]),
                _ev('back', unmeasured=[(None, T0 + timedelta(minutes=3))]),
                _ev('empty')]
    windows = [_w(101, 104), _w(1, 2), _w(60, 62)]
    got = [[e[0] for e in w] for w in sessions_by_interval(evidence, windows)]
    assert got == [['late', 'open'], ['early', 'back'], ['open']]


def test_sweep_keeps_a_span_that_touches_the_window_boundary_exactly():
    # `_window_overlaps` treats touching as no overlap, but a zero-length or
    # exactly-bounded turn IS contained -- the bounds test must be inclusive.
    evidence = [_ev('edge_in', [_turn(10, 10)]), _ev('edge_out', [_turn(5, 10)]),
                _ev('after', [_turn(11, 12)]), _ev('before', [_turn(1, 9)])]
    got = [e[0] for e in sessions_by_interval(evidence, [_w(10, 10)])[0]]
    assert got == ['edge_in', 'edge_out']


def test_sweep_does_not_depend_on_window_order():
    evidence = [_ev(f's{i}', [_turn(i * 3, i * 3 + 2)]) for i in range(40)]
    windows = [_w(i, i + 4) for i in range(0, 110, 3)]
    forward = sessions_by_interval(evidence, windows)
    shuffled = list(range(len(windows)))
    random.Random(2).shuffle(shuffled)
    back = sessions_by_interval(evidence, [windows[i] for i in shuffled])
    for pos, i in enumerate(shuffled):
        assert back[pos] == forward[i]


def test_turn_index_falls_back_to_the_full_list_when_turns_are_not_in_time_order():
    in_order = [_turn(0, 2), _turn(2, 4), _turn(4, 9)]
    assert TurnIndex(in_order).reaching(*_w(3, 5)) == [_turn(2, 4), _turn(4, 9)]
    out_of_order = [_turn(4, 9), _turn(0, 2), _turn(2, 4)]
    assert TurnIndex(out_of_order).reaching(*_w(3, 5)) == out_of_order
    backwards = [_turn(5, 3)]
    assert TurnIndex(backwards).reaching(*_w(0, 1)) == backwards
