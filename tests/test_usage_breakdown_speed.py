"""MC-998 / backlog 4668eafc follow-up 9: the Usage report's first open
showed "Breakdown failed to load" because GET /api/system/usage/breakdown took
1.4-2.7s warm (measured 2026-10-01) and ~4s with three requests in flight --
past the client's 8s abort once a busy box and the tunnel were added.

Two costs, two pins:
  * the per-session `get_code_delta` N+1 (one sqlite connection each) is now
    ONE batched read -- connection count must not grow with session count;
  * calibration + turn sizes are pure functions of (samples, checkpoints,
    facts), so `build_breakdown` memoizes them on a CONTENT digest of those
    inputs: repeat requests and dimension/sort changes reuse them, and any new
    sample/checkpoint/fact changes the digest and recomputes.
"""
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import usage_breakdown_aggregate as agg  # noqa: E402
from mc.usage_breakdown_store import UsageBreakdownStore  # noqa: E402
from tests.test_usage_breakdown_aggregate import _fact, _sample_tick  # noqa: E402
from tests.test_usage_breakdown_turns_per_point import _series_with_turns  # noqa: E402


# ── batched code_delta read ─────────────────────────────────────────────────

def _count_connections(monkeypatch):
    calls = []
    real = UsageBreakdownStore._connection

    def counting(self, *, write=False):
        calls.append(write)
        return real(self, write=write)

    monkeypatch.setattr(UsageBreakdownStore, '_connection', counting)
    return calls


def _seed_sessions(store, n):
    now = datetime.now(timezone.utc)
    for i in range(n):
        sid = f'sess-{i}'
        store.upsert_session_fact(sid, {
            'provider': 'claude', 'project_id': 'p', 'character': 'c', 'trigger_type': 'manual',
            'observed_model': 'claude-sonnet-5', 'status': 'completed',
            'started_at': (now - timedelta(hours=1)).isoformat(),
            'ended_at': (now - timedelta(minutes=30)).isoformat(),
            'input_processed_total': 10, 'output_tokens': 5, 'token_coverage': 'complete'})
        store.upsert_code_delta(sid, {'added': 3 + i, 'deleted': 1, 'status': 'ok'})


def test_get_code_deltas_is_one_connection_and_omits_missing(tmp_path, monkeypatch):
    store = UsageBreakdownStore(tmp_path / 'ub.sqlite')
    _seed_sessions(store, 5)
    calls = _count_connections(monkeypatch)
    out = store.get_code_deltas([f'sess-{i}' for i in range(5)] + ['no-such-session'])
    assert len(calls) == 1
    assert sorted(out) == [f'sess-{i}' for i in range(5)]
    assert out['sess-2']['added'] == 5 and out['sess-2']['status'] == 'ok'
    assert store.get_code_deltas([]) == {}


def test_get_code_deltas_handles_more_ids_than_one_sqlite_variable_batch(tmp_path):
    store = UsageBreakdownStore(tmp_path / 'ub.sqlite')
    _seed_sessions(store, 3)
    ids = [f'ghost-{i}' for i in range(2500)] + ['sess-1']
    assert list(store.get_code_deltas(ids)) == ['sess-1']


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server
    from mc.blueprints import system_routes as sr_mod
    monkeypatch.setattr(sr_mod, '_DATA_ROOT', tmp_path)
    yield server.app.test_client(), sr_mod._usage_breakdown_store()


def test_route_connection_count_does_not_grow_with_session_count(client, monkeypatch):
    c, store = client
    _seed_sessions(store, 3)
    calls = _count_connections(monkeypatch)
    assert c.get('/api/system/usage/breakdown').status_code == 200
    few = len(calls)
    _seed_sessions(store, 12)
    calls.clear()
    assert c.get('/api/system/usage/breakdown').status_code == 200
    assert len(calls) == few, f'{few} connections for 3 sessions, {len(calls)} for 12'


def test_route_code_delta_totals_unchanged_by_batching(client):
    c, store = client
    _seed_sessions(store, 4)  # added = 3,4,5,6 -> 18
    body = c.get('/api/system/usage/breakdown').get_json()
    assert body['totals']['loc']['added'] == 18


# ── calibration / turn-size memoization ─────────────────────────────────────

@pytest.fixture()
def counters(monkeypatch):
    agg._clear_memo()
    n = {'cal': 0, 'turns': 0}
    real_cal, real_turns = agg.compute_calibration, agg.compute_turn_sizes

    def cal(*a, **k):
        n['cal'] += 1
        return real_cal(*a, **k)

    def turns(*a, **k):
        n['turns'] += 1
        return real_turns(*a, **k)

    monkeypatch.setattr(agg, 'compute_calibration', cal)
    monkeypatch.setattr(agg, 'compute_turn_sizes', turns)
    yield n
    agg._clear_memo()


def _build(samples, checkpoints, facts, *, provider='claude', window_kind='5h', scope='all',
           dimension='project', sort_by='input'):
    return agg.build_breakdown(
        provider=provider, window_kind=window_kind, window_scope=scope,
        range_start=samples[0]['source_observed_at'], range_end=samples[-1]['source_observed_at'],
        dimension=dimension, sort_by=sort_by, range_samples=samples, calibration_samples=samples,
        session_facts=facts, checkpoints=checkpoints, code_deltas={}, coverage_begins='2026-09-30')


def test_repeat_request_reuses_calibration_and_turn_sizes(counters):
    samples, checkpoints, facts = _series_with_turns(10)
    first = _build(samples, checkpoints, facts)
    second = _build(samples, checkpoints, facts)
    assert counters == {'cal': 1, 'turns': 1}
    assert first['tokens_per_point']['status'] == 'ok'
    assert second['tokens_per_point'] == first['tokens_per_point']


def test_dimension_and_sort_change_do_not_recompute(counters):
    samples, checkpoints, facts = _series_with_turns(10)
    _build(samples, checkpoints, facts, dimension='project', sort_by='input')
    _build(samples, checkpoints, facts, dimension='character', sort_by='output')
    _build(samples, checkpoints, facts, dimension='model', sort_by='added')
    assert counters == {'cal': 1, 'turns': 1}


def test_cached_result_equals_uncached_result(counters):
    samples, checkpoints, facts = _series_with_turns(10)
    cold = _build(samples, checkpoints, facts)
    warm = _build(samples, checkpoints, facts)
    agg._clear_memo()
    recomputed = _build(samples, checkpoints, facts)
    assert warm == cold == recomputed


def test_new_allowance_sample_invalidates(counters):
    samples, checkpoints, facts = _series_with_turns(10)
    _build(samples, checkpoints, facts)
    last = datetime.fromisoformat(samples[-1]['source_observed_at'])
    samples = samples + [dict(samples[-1], source_observed_at=(last + timedelta(minutes=2)).isoformat())]
    _build(samples, checkpoints, facts)
    assert counters['cal'] == 2


def test_new_checkpoint_invalidates_calibration_and_turn_sizes(counters):
    samples, checkpoints, facts = _series_with_turns(10)
    _build(samples, checkpoints, facts)
    sid = next(iter(checkpoints))
    checkpoints[sid]['sample_ticks'].append(
        _sample_tick(sid, observed_at=samples[-1]['source_observed_at'],
                     input_processed_total=1, output_tokens=1))
    _build(samples, checkpoints, facts)
    assert counters == {'cal': 2, 'turns': 2}


def test_changed_fact_invalidates(counters):
    samples, checkpoints, facts = _series_with_turns(10)
    _build(samples, checkpoints, facts)
    facts = [dict(f, observed_model='claude-opus-5') if f['session_id'] == 'sess-0' else f
             for f in facts]
    _build(samples, checkpoints, facts)
    assert counters == {'cal': 2, 'turns': 2}


def test_added_fact_invalidates(counters):
    samples, checkpoints, facts = _series_with_turns(10)
    _build(samples, checkpoints, facts)
    _build(samples, checkpoints, facts + [_fact('late', started_at=samples[0]['source_observed_at'])])
    assert counters['cal'] == 2


def test_provider_and_scope_are_keyed_separately(counters):
    samples, checkpoints, facts = _series_with_turns(10)
    _build(samples, checkpoints, facts)
    _build(samples, checkpoints, facts, scope='sonnet')
    _build(samples, checkpoints, facts, provider='codex')
    assert counters['cal'] == 3
    _build(samples, checkpoints, facts, scope='sonnet')
    assert counters['cal'] == 3


def test_memo_stays_small(counters):
    samples, checkpoints, facts = _series_with_turns(2)
    for i in range(agg._MEMO_MAX + 10):
        _build(samples + [dict(samples[-1], raw_utilization=float(i))], checkpoints, facts)
    assert len(agg._memo) <= agg._MEMO_MAX


def test_caller_mutating_a_returned_payload_cannot_poison_the_cache(counters):
    samples, checkpoints, facts = _series_with_turns(10)
    first = _build(samples, checkpoints, facts)
    first['tokens_per_point']['status'] = 'tampered'
    first['tokens_per_point']['turn_size']['mean'] = -1
    assert _build(samples, checkpoints, facts)['tokens_per_point']['status'] == 'ok'
    assert _build(samples, checkpoints, facts)['tokens_per_point']['turn_size']['mean'] > 0


def test_concurrent_identical_requests_compute_once(counters, monkeypatch):
    samples, checkpoints, facts = _series_with_turns(10)
    gate = threading.Event()
    real = agg._eligible_intervals

    def slow(*a, **k):
        gate.wait(5)
        return real(*a, **k)

    monkeypatch.setattr(agg, '_eligible_intervals', slow)
    out = []
    threads = [threading.Thread(target=lambda: out.append(_build(samples, checkpoints, facts)))
               for _ in range(3)]
    for t in threads:
        t.start()
    gate.set()
    for t in threads:
        t.join(10)
    assert len(out) == 3 and counters['cal'] == 1
    assert out[0]['tokens_per_point'] == out[1]['tokens_per_point'] == out[2]['tokens_per_point']
