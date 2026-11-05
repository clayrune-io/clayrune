"""MC-1152 P3 shared collection, downtime, idempotency and safe failures."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json

import pytest

from mc.desk_impact_collect import collect_campaigns
from mc.desk_impact_compare import comparison
from mc.desk_impact_sources import base as B
from mc.desk_impact_store import OwnershipError
from tests.desk_impact_github_fakes import (
    NOW, TARGET, WINDOW, FakeTransport, offline, response, star, world,
)


def collect(w, transport, campaigns=('camp-a',), window=WINDOW):
    return collect_campaigns(w['store'], 'proj1', campaigns, window,
        transport=transport, clock=w['clock'], connection_resolver=w['resolver'])


def test_overlapping_campaigns_join_one_shared_source_and_retry_is_read_free(world):
    world['define']('camp-a')
    world['define']('camp-b')
    t = FakeTransport()
    result = collect(world, t, ('camp-a', 'camp-b', 'camp-a'))
    assert result['request_count'] == 3 and len(result['sources']) == 1
    a, b = result['campaigns']['camp-a'], result['campaigns']['camp-b']
    assert a['snapshot_ids'] == b['snapshot_ids']
    assert a['definition']['id'] != b['definition']['id']
    assert all(r['definition_id'] is None for r in world['store'].read_snapshots('proj1').records)
    count = len(world['store'].read_snapshots('proj1').records)
    world['now'][0] = '2026-11-04T01:00:00+00:00'
    retry = collect(world, t, ('camp-b', 'camp-a'))
    assert retry['request_count'] == 0 and len(t.calls) == 3
    assert len(world['store'].read_snapshots('proj1').records) == count


def test_concurrent_manual_and_daily_calls_dedup_across_store_callers(world):
    world['define']()
    t = FakeTransport()
    with ThreadPoolExecutor(max_workers=2) as pool:
        out = list(pool.map(lambda _: collect(world, t), range(2)))
    assert sum(r['request_count'] for r in out) == 3
    assert len(t.calls) == 3


def test_no_definition_means_no_transport_and_manual_goal_unchanged(world):
    before = copy.deepcopy(world['campaigns'])
    result = collect(world, FakeTransport())
    assert result['request_count'] == 0
    assert result['campaigns']['camp-a']['reason'] == 'No conversion definition'
    assert world['campaigns'] == before


def test_batch_project_validation_happens_before_any_read(world):
    world['define']()
    world['connections']['conn-1']['project_id'] = 'other'
    t = FakeTransport()
    with pytest.raises(OwnershipError):
        collect(world, t)
    assert not t.calls


def test_failures_preserve_last_good_values_and_original_success_time(world):
    world['define'](metric='downloads', target={**TARGET, 'asset_id': 42})
    collect(world, FakeTransport())
    old = world['store'].read_snapshots('proj1').records[0]
    world['now'][0] = '2026-11-05T00:00:00+00:00'
    failure = collect(world, FakeTransport(asset=response({'secret': 'raw-private'}, 404)))
    status = next(iter(failure['sources'].values()))['status']
    assert status['status'] == 'error' and status['error_code'] == 'not_found'
    assert status['last_success_at'] == NOW
    assert world['store'].read_snapshots('proj1').records == (old,)
    projection = comparison(world['store'], 'proj1', 'camp-a', B.Window(NOW, world['clock'](), 'day'),
                            ledger=[], accounts={})
    observation = projection['repository_context']['observations'][0]
    assert observation['stale'] and observation['observed_at'] == NOW
    assert observation['latest_total'] == 100 and observation['value'] is None
    assert 'raw-private' not in json.dumps(projection)


def test_downtime_gap_survives_restart_and_later_success(world):
    world['define'](metric='downloads', target={**TARGET, 'asset_id': 42})
    collect(world, FakeTransport())
    world['now'][0] = '2026-11-08T00:00:00+00:00'
    collect(world, FakeTransport(asset=response({'id': 42, 'download_count': 120})))
    status = world['store'].get_coverage('proj1', 'github', 'conn-1', {**TARGET, 'asset_id': 42})
    gap = next(g for g in status['gaps'] if 'downtime' in g['reason'])
    assert gap['start'] == NOW and gap['end'] == world['clock']()
    from mc.desk_impact_store import ImpactStore
    reopened = ImpactStore(world['store'].root, campaign_resolver=lambda c: world['campaigns'][c],
                           connection_resolver=world['resolver'], clock=world['clock'])
    world['store'] = reopened
    world['now'][0] = '2026-11-09T00:00:00+00:00'
    collect(world, FakeTransport(asset=response({'id': 42, 'download_count': 130})))
    status2 = reopened.get_coverage('proj1', 'github', 'conn-1', {**TARGET, 'asset_id': 42})
    assert gap in status2['gaps']
    projection = comparison(reopened, 'proj1', 'camp-a', B.Window(NOW, world['clock'](), 'day'), ledger=[], accounts={})
    assert projection['repository_context']['observations'][0]['value'] is None


def test_rate_limit_is_connection_wide_and_survives_new_batch(world):
    world['define'](metric='downloads', target={**TARGET, 'asset_id': 42})
    world['define']('camp-b', metric='downloads', target={**TARGET, 'asset_id': 43})
    t = FakeTransport(asset=response({}, 429, {'Retry-After': '3600'}))
    first = collect(world, t)
    assert first['request_count'] == 1
    second = collect(world, t, ('camp-b',))
    assert second['request_count'] == 0 and len(t.calls) == 1
    assert next(iter(second['sources'].values()))['connection_status']['error_code'] == 'rate_limited'
    world['now'][0] = '2026-11-04T01:00:00+00:00'
    third = collect(world, FakeTransport(asset=response({'id': 43, 'download_count': 0})), ('camp-b',))
    assert third['request_count'] == 1


def test_retry_backoff_without_rate_headers_and_success_after_timeout(world):
    world['define'](metric='downloads', target={**TARGET, 'asset_id': 42})
    t = FakeTransport(asset=TimeoutError('raw-error'))
    assert collect(world, t)['request_count'] == 1
    world['now'][0] = '2026-11-04T00:05:00+00:00'
    assert collect(world, t)['request_count'] == 0
    world['now'][0] = '2026-11-04T00:15:00+00:00'
    assert collect(world, FakeTransport())['request_count'] == 1
    rows = world['store'].read_snapshots('proj1').records
    assert len(rows) == 1 and rows[0]['value'] == 100
    assert 'raw-error' not in (world['store'].root / 'proj1' / 'coverage.json').read_text()


def test_correction_uses_latest_value_for_same_window_and_does_not_sum_history(world):
    world['define']()
    collect(world, FakeTransport(stars=[response([star(1), star(2)])]))
    world['now'][0] = '2026-11-05T00:00:00+00:00'
    collect(world, FakeTransport(stars=[response([star(1)])]))
    series = [r for r in world['store'].effective_snapshots('proj1') if r['metric'] == 'stars']
    assert len(series) == 1 and series[0]['value'] == 1
    assert len([r for r in world['store'].read_snapshots('proj1').records if r['metric'] == 'stars']) == 2


def test_raw_users_and_credentials_never_persist_in_collection(world):
    world['define']()
    collect(world, FakeTransport())
    stored = ''.join(p.read_text() for p in (world['store'].root / 'proj1').glob('*.json*'))
    assert 'private-person' not in stored
    assert 'Authorization' not in stored and 'chosen-by-human' not in stored


def test_crash_during_append_cannot_mark_another_window_complete(world, monkeypatch):
    world['define']()
    collect(world, FakeTransport())
    original = world['store'].append_snapshot
    calls = []
    def interrupted(pid, observation):
        calls.append(observation)
        if len(calls) == 2:
            raise OSError('simulated collection crash')
        return original(pid, observation)
    world['now'][0] = '2026-11-04T01:00:00+00:00'
    other = B.Window('2026-11-02T00:00:00+00:00', NOW, 'day')
    monkeypatch.setattr(world['store'], 'append_snapshot', interrupted)
    with pytest.raises(OSError, match='simulated collection crash'):
        collect(world, FakeTransport(), window=other)
    monkeypatch.setattr(world['store'], 'append_snapshot', original)
    retry = collect(world, FakeTransport(), window=other)
    assert retry['request_count'] == 3
    effective = [s for s in world['store'].effective_snapshots('proj1')
                 if s['metric'] == 'stars' and s['window'] == other.as_dict()]
    assert len(effective) == 1


def test_equivalent_utc_spellings_share_cache_and_exact_star_projection(world):
    world['define']()
    zulu = B.Window('2026-11-03T00:00:00Z', '2026-11-04T00:00:00Z', 'day')
    t = FakeTransport()
    assert collect(world, t, window=zulu)['request_count'] == 3
    assert collect(world, t, window=WINDOW)['request_count'] == 0
    got = comparison(world['store'], 'proj1', 'camp-a', zulu, ledger=[], accounts={})
    stars = next(o for o in got['repository_context']['observations'] if o['metric'] == 'stars')
    assert stars['value'] == 1


def test_future_or_unbounded_request_cannot_bypass_validation_via_asset_cache(world):
    world['define'](metric='downloads', target={**TARGET, 'asset_id': 42})
    t = FakeTransport()
    collect(world, t)
    for invalid in (B.Window(None, NOW, 'instant'),
                    B.Window(NOW, '2026-11-05T00:00:00Z', 'day')):
        with pytest.raises(B.DefinitionRefused):
            collect(world, t, window=invalid)
    assert len(t.calls) == 1
