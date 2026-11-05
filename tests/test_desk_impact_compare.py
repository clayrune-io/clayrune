"""MC-1152 P3 local projection evidence. No HTTP or engagement reads."""
import copy

import pytest

from mc.desk_impact_compare import comparison, counter_delta, repository_context
from mc.desk_impact_sources import base as B
from tests.desk_impact_github_fakes import NOW, TARGET, WINDOW, normalize, offline, world


def sample(w, value, at=NOW, asset=42):
    row = normalize('asset', {'id': asset, 'download_count': value},
                     {**TARGET, 'asset_id': asset}, now=at)[0]
    return w['store'].append_snapshot('proj1', row)[0]


def test_cumulative_first_sample_is_baseline_never_campaign_downloads(world):
    first = sample(world, 100)
    delta = counter_delta([first], WINDOW.start, NOW)
    assert delta['value'] is None and 'baseline' in delta['reason']


@pytest.mark.parametrize('values,expected', [([100, 105], 5), ([100, 100], 0), ([100, 90], None),
                                          ([100, 90, 110], None)])
def test_download_deltas_require_continuity_even_after_reset_rebounds(world, values, expected):
    stamps = ['2026-11-04T00:00:00+00:00', '2026-11-05T00:00:00+00:00', '2026-11-06T00:00:00+00:00']
    rows = [sample(world, value, at) for value, at in zip(values, stamps)]
    got = counter_delta(rows, stamps[0], stamps[len(values) - 1])
    assert got['value'] == expected
    if expected is None:
        assert 'decreased or reset' in got['reason']


def test_replaced_asset_identity_cannot_join_counters(world):
    before, after = sample(world, 100), sample(world, 120, '2026-11-05T00:00:00+00:00', asset=43)
    got = counter_delta([before, after], NOW, after['window']['end'])
    assert got['value'] is None and 'identity' in got['reason']


def test_download_deleted_endpoint_gap_breaks_continuity(world):
    end = '2026-11-05T00:00:00+00:00'
    rows = [sample(world, 100), sample(world, 120, end)]
    got = counter_delta(rows, NOW, end, [{'start': NOW, 'end': end, 'reason': '404 deleted'}])
    assert got['value'] is None and 'continuity' in got['reason']


def test_github_is_context_once_and_every_channel_conversion_is_unknown(world):
    definition = world['define']()
    for row in normalize('stars', []):
        world['store'].append_snapshot('proj1', row)
    accounts = {'acc-1': {'id': 'acc-1', 'platform': 'x', 'label': 'X one'},
                'acc-2': {'id': 'acc-2', 'platform': 'x', 'label': 'X two'}}
    before = copy.deepcopy(world['campaigns'])
    got = comparison(world['store'], 'proj1', 'camp-a', WINDOW, ledger=[], accounts=accounts)
    assert len(got['channels']) == 2 and all(r['published_posts'] == 0 for r in got['channels'])
    assert all(r['conversions'] == {'value': None, 'reason': 'Not attributable by this source'} for r in got['channels'])
    assert got['repository_context']['label'] == 'Repository context — not attributed to this campaign'
    assert got['goal'] == before['camp-a']['goal'] and got['auto_progress']['state'] == 'dark'
    assert got['definition']['id'] == definition['id'] and world['campaigns'] == before


def post(ident='post-1', account='acc-1', outcomes=None, **patch):
    return {'id': ident, 'account': account, 'platform': 'x', 'project_id': 'proj1',
            'campaign_id': 'camp-a', 'published_at': '2026-11-03T10:00:00Z',
            'outcomes': outcomes or [], **patch}


def outcome(value, metric='impressions', at='2026-11-04T00:00:00Z', source='feed'):
    return {'metric': metric, 'value': value, 'at': at, 'source': source}


def test_latest_post_totals_never_add_snapshots_or_manual_values(world):
    ledger = [post(outcomes=[outcome(5, at='2026-11-03T12:00:00Z'), outcome(10),
                              outcome(99, source='manual'), outcome(0, metric='clicks')])]
    got = comparison(world['store'], 'proj1', 'camp-a', WINDOW, ledger=ledger + ledger, accounts={})
    row = got['channels'][0]
    assert row['published_posts'] == 1 and row['post_metrics']['impressions']['value'] == 10
    assert row['platform_clicks']['value'] == 0
    assert got['post_metrics_label'] == 'Latest totals for posts published in this period'
    assert got['repository_context'] is None and got['auto_progress']['value'] is None


def test_missing_account_metric_is_unknown_not_zero_or_partial_sum(world):
    got = comparison(world['store'], 'proj1', 'camp-a', WINDOW,
        ledger=[post(outcomes=[outcome(10)]), post('post-2')], accounts={})
    row = got['channels'][0]
    assert row['post_metrics']['impressions']['value'] is None
    assert row['post_metrics']['impressions']['observed_posts'] == 1
    assert row['platform_clicks']['value'] is None


def test_same_platform_accounts_and_removed_history_stay_distinct(world):
    ledger = [post(outcomes=[outcome(10)]), post('post-2', 'acc-2', [outcome(20)]),
              post('post-3', 'removed-id', [outcome(30)]), post('old', 'acc-1', [outcome(999)],
                 published_at='2026-10-01T00:00:00Z'), post('foreign', outcomes=[outcome(999)], project_id='other')]
    got = comparison(world['store'], 'proj1', 'camp-a', WINDOW, ledger=ledger,
                      accounts={'acc-1': {'id': 'acc-1', 'platform': 'x'}})
    assert len(got['channels']) == 3
    assert [r['post_metrics']['impressions']['value'] for r in got['channels']] == [10, 20, 30]
    assert got['channels'][2]['removed'] and not got['channels'][2]['in_plan']
    assert got['channels'][2]['platform'] == 'x'


def test_persisted_click_name_matches_real_existing_reader_contract(world):
    from mc.desk_engagement import XReader
    reader = XReader(token='offline-fixture', include_nonpublic=True,
                      transport=lambda *_: {'data': [{'id': '123', 'non_public_metrics': {'url_link_clicks': 5}}]})
    cached = reader.fetch_metrics(['123'])['metrics']['123']
    assert cached == {'clicks': 5}
    got = comparison(world['store'], 'proj1', 'camp-a', WINDOW,
                      ledger=[post(outcomes=[outcome(v, metric=k) for k, v in cached.items()])], accounts={})
    assert got['channels'][0]['platform_clicks']['value'] == 5
    assert 'clicks' not in got['channels'][0]['post_metrics']


def test_legacy_missing_identity_is_not_assigned_to_first_platform_account(world):
    got = comparison(world['store'], 'proj1', 'camp-a', WINDOW,
                      ledger=[post(account=None, outcomes=[outcome(5)])], accounts={})
    assert got['channels'][0]['published_posts'] == 0
    assert got['channels'][-1]['account_id'] is None
    assert got['channels'][-1]['published_posts'] == 1


def test_bare_platform_identity_is_unknown_not_an_account(world):
    got = comparison(world['store'], 'proj1', 'camp-a', WINDOW,
                      ledger=[post(account='x', outcomes=[outcome(5)])], accounts={})
    assert got['channels'][-1]['account_id'] is None


def test_default_comparison_reads_local_store_without_reader_vault_or_writes(world, monkeypatch):
    from mc import desk, desk_engagement, secrets_store
    state = {'accounts': {'acc-1': {'id': 'acc-1', 'platform': 'x'}},
             'ledger': [post(outcomes=[outcome(10)])]}
    before = copy.deepcopy(state)
    def refuse(*args, **kwargs):
        raise AssertionError('Cached comparison may not read platforms, dispense secrets or write Desk state')
    monkeypatch.setattr(desk, '_read_store', lambda: state)
    monkeypatch.setattr(desk, '_write_store', refuse)
    monkeypatch.setattr(desk, 'record_feed_outcome', refuse)
    monkeypatch.setattr(desk_engagement, 'poll_project', refuse)
    monkeypatch.setattr(desk_engagement, 'readers_for_project', refuse)
    monkeypatch.setattr(desk_engagement.XReader, 'fetch_metrics', refuse)
    monkeypatch.setattr(secrets_store, 'get_secret_value', refuse)
    got = comparison(world['store'], 'proj1', 'camp-a', WINDOW)
    assert got['channels'][0]['post_metrics']['impressions']['value'] == 10
    assert got['channels'][0]['platform_clicks']['value'] is None
    assert state == before


def test_referrers_use_latest_native_list_not_sum_or_omitted_host_carry(world):
    definition = world['define']()
    for row in normalize('referrers', [{'referrer': 'old.org', 'count': 50, 'uniques': 20}]):
        world['store'].append_snapshot('proj1', row)
    for row in normalize('referrers', [{'referrer': 'new.org', 'count': 3, 'uniques': 2}],
                         now='2026-11-05T00:00:00+00:00'):
        world['store'].append_snapshot('proj1', row)
    got = repository_context(world['store'], 'proj1', definition, WINDOW)
    referrers = [r for r in got['observations'] if r['metric'] == 'referrer_views']
    assert len(referrers) == 1 and referrers[0]['value'] == 3
    assert referrers[0]['target']['referrer'] == 'new.org'
    assert referrers[0]['window']['granularity'] == 'rolling_window'
    assert 'not an exact requested-range' in referrers[0]['label']


def test_traffic_daily_counts_are_not_rolling_sums_and_uniques_are_not_additive(world):
    definition = world['define']()
    body = {'count': 8, 'uniques': 4, 'views': [
        {'timestamp': '2026-11-02T00:00:00Z', 'count': 5, 'uniques': 3},
        {'timestamp': '2026-11-03T00:00:00Z', 'count': 3, 'uniques': 2}]}
    for row in normalize('traffic', body):
        world['store'].append_snapshot('proj1', row)
    window = B.Window('2026-11-02T00:00:00+00:00', NOW, 'day')
    got = repository_context(world['store'], 'proj1', definition, window)
    metrics = {r['metric']: r for r in got['observations']}
    assert metrics['traffic_views']['value'] == 8
    assert metrics['traffic_unique_visitors']['value'] is None
    assert 'cannot be summed' in metrics['traffic_unique_visitors']['reason']
    missing = repository_context(world['store'], 'proj1', definition,
                                  B.Window('2026-11-01T00:00:00+00:00', NOW, 'day'))
    assert all(r['value'] is None for r in missing['observations'])


def test_revision_history_and_manual_goal_survive_new_definition(world):
    first = world['define']()
    second = world['define'](metric='downloads', target={**TARGET, 'asset_id': 42})
    got = comparison(world['store'], 'proj1', 'camp-a', WINDOW, ledger=[], accounts={})
    assert got['definition']['revision'] == 2
    assert [d['revision'] for d in got['revisions']] == [1, 2]
    assert got['goal']['entries'][0]['value'] == 7
    assert got['repository_context']['observations'] == []
