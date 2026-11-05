"""MC-1152 P3 GitHub evidence: no real HTTP, vault or clock."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from mc import desk_impact_schema as S
from mc.desk_impact_sources import base as B
from mc.desk_impact_sources import github as G
from tests.desk_impact_github_fakes import (
    NOW, TARGET, WINDOW, FakeTransport, normalize, offline, request, response, star,
)


def test_capabilities_are_metadata_and_never_attribute_github():
    caps = G.GitHubSource().capabilities({})
    assert not any((caps.attributes_campaign, caps.attributes_channel, caps.attributes_post))
    assert caps.history_limit_days == 14
    assert caps.attribution_dimensions == ()
    assert 'traffic_administration_read' in caps.requires


@pytest.mark.parametrize('target', [
    {'owner': '../x', 'repo': 'repo'}, {'owner': 'example', 'repo': '..'},
    {**TARGET, 'url': 'https://elsewhere.invalid'}, {**TARGET, 'asset_id': True},
    {**TARGET, 'asset_id': 0}, {**TARGET, 'referrer': 'host/?token=hidden'},
    {**TARGET, 'referrer': 'host', 'asset_id': 42},
])
def test_target_refused_before_transport(target):
    transport = FakeTransport()
    with pytest.raises(B.DefinitionRefused):
        G.GitHubSource().collect(request(target), transport, lambda: NOW)
    assert transport.calls == []


def test_successful_zero_and_half_open_star_boundaries_no_raw_users():
    t = FakeTransport(stars=[response([star(1, WINDOW.start), star(2, WINDOW.end)])])
    got = G.GitHubSource().collect(request(), t, lambda: NOW)
    assert got.ok and got.coverage.complete
    by_metric = {o['metric']: o for o in got.observations}
    assert by_metric['stars']['value'] == 1
    assert by_metric['star_total']['value'] == 2
    assert by_metric['star_total']['window']['start'] is None
    assert all(o['scope'] in ('repository', 'asset') and o['attribution'] == {
        'supported': False, 'dimensions': {}} for o in got.observations)
    assert 'private-person' not in json.dumps(got.observations)
    empty = G.GitHubSource().collect(request(), FakeTransport(stars=[response([])]), lambda: NOW)
    assert next(o['value'] for o in empty.observations if o['metric'] == 'stars') == 0


def test_paginated_stars_are_complete_before_counting():
    link = '<https://api.github.com/repos/example/repo/stargazers?page=2&per_page=100>; rel="next"'
    t = FakeTransport(stars=[response([star(1)], headers={'Link': link}), response([star(2)])])
    got = G.GitHubSource().collect(request(), t, lambda: NOW)
    assert got.ok and got.request_count == 4
    assert next(o['value'] for o in got.observations if o['metric'] == 'stars') == 2
    assert t.calls[0]['headers']['Accept'] == 'application/vnd.github.star+json'
    assert t.calls[1]['params'] == {'per_page': '100', 'page': '2'}


@pytest.mark.parametrize('second', [TimeoutError('raw sensitive error'), response({}, 403),
                                     response({}, 404), response({}, 429, {'Retry-After': '60'}),
                                     response([star(1)]), response([{'user': {'id': 2}}])])
def test_partial_pages_are_unknown_and_never_partial_star_counts(second):
    link = '<https://api.github.com/repos/example/repo/stargazers?page=2&per_page=100>; rel="next"'
    t = FakeTransport(stars=[response([star(1)], headers={'Link': link}), second])
    got = G.GitHubSource().collect(request(), t, lambda: NOW)
    assert not got.ok and got.coverage.incomplete_pages
    assert not any(o['metric'] in ('stars', 'star_total') for o in got.observations)
    assert 'raw sensitive' not in str(got)


@pytest.mark.parametrize('link', [
    '<https://attacker.invalid/steal?page=2&per_page=100>; rel="next"',
    '<https://api.github.com/repos/other/repo/stargazers?page=2&per_page=100>; rel="next"',
    '<https://api.github.com/repos/example/repo/stargazers?page=1&per_page=100>; rel="next"',
    '<https://api.github.com/repos/example/repo/stargazers?page=2&per_page=100&token=bad>; rel="next"',
])
def test_next_page_is_never_followed_outside_exact_safe_sequence(link):
    t = FakeTransport(stars=[response([star(1)], headers={'Link': link})])
    got = G.GitHubSource().collect(request(), t, lambda: NOW)
    assert got.errors[0].code == 'malformed'
    assert len([c for c in t.calls if c['url'].endswith('stargazers')]) == 1


def test_pagination_is_bounded(monkeypatch):
    monkeypatch.setattr(G, 'MAX_PAGES', 1)
    t = FakeTransport(stars=[response([star(1)], headers={'Link':
        '<https://api.github.com/repos/example/repo/stargazers?page=2&per_page=100>; rel="next"'})])
    got = G.GitHubSource().collect(request(), t, lambda: NOW)
    assert got.errors[0].code == 'partial'
    assert got.coverage.incomplete_pages
    assert not any(o['metric'] == 'stars' for o in got.observations)


@pytest.mark.parametrize('status,code', [(401, 'auth'), (403, 'permission'), (404, 'not_found'),
                                       (429, 'rate_limited'), (500, 'unavailable'), (302, 'unavailable')])
def test_status_failures_never_mean_zero_or_reveal_body(status, code):
    t = FakeTransport(asset=response({'message': 'secret raw response'}, status))
    got = G.GitHubSource().collect(request({**TARGET, 'asset_id': 42}), t, lambda: NOW)
    assert got.errors[0].code == code and not got.coverage.complete
    assert got.observations == () and got.request_count == 1
    assert 'secret raw response' not in str(got)


@pytest.mark.parametrize('headers', [{'Retry-After': '60'},
    {'Retry-After': 'Wed, 04 Nov 2026 00:01:00 GMT'},
    {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1793750460'}])
def test_retry_after_normalizes_seconds_http_date_and_reset(headers):
    t = FakeTransport(asset=response({}, 403, headers))
    got = G.GitHubSource().collect(request({**TARGET, 'asset_id': 42}), t, lambda: NOW)
    assert got.errors[0].code == 'rate_limited'
    assert got.retry_after == '2026-11-04T00:01:00+00:00'


def test_rate_limit_stops_remaining_endpoints():
    t = FakeTransport(stars=[response({}, 429, {'Retry-After': '60'})])
    got = G.GitHubSource().collect(request(), t, lambda: NOW)
    assert got.request_count == 1


def test_traffic_native_days_are_preserved_current_day_is_unknown():
    body = {'count': 8, 'uniques': 4, 'views': [
        {'timestamp': '2026-11-03T00:00:00Z', 'count': 3, 'uniques': 2},
        {'timestamp': NOW, 'count': 5, 'uniques': 3}]}
    rows = normalize('traffic', body)
    assert [r['value'] for r in rows] == [3, 2, None, None]
    assert all(r['window']['granularity'] == 'day' for r in rows)
    assert all('incomplete' in r['null_reason'] for r in rows[-2:])


def test_old_and_missing_traffic_days_are_partial_not_zero():
    old = B.Window('2026-10-01T00:00:00Z', NOW, 'day')
    got = G.GitHubSource().collect(request(window=old), FakeTransport(), lambda: NOW)
    assert not got.coverage.complete and got.errors[0].code == 'partial'
    assert any('older' in g for g in got.coverage.gaps)
    assert any('Missing traffic' in g for g in got.coverage.gaps)
    assert len([r for r in got.observations if r['metric'] == 'traffic_views']) == 1


@pytest.mark.parametrize('body', [
    {'count': 1, 'uniques': 1}, {'count': 1, 'uniques': 1, 'views': [{'timestamp': NOW, 'count': -1, 'uniques': 0}]},
    {'count': 0, 'uniques': 0, 'views': [{'timestamp': '2025-01-01T00:00:00Z', 'count': 0, 'uniques': 0}]},
])
def test_malformed_traffic_endpoint_does_not_discard_good_other_endpoints(body):
    got = G.GitHubSource().collect(request(), FakeTransport(traffic=response(body)), lambda: NOW)
    assert got.errors[0].code == 'malformed'
    assert any(r['metric'] == 'stars' for r in got.observations)
    assert not any(r['metric'].startswith('traffic') for r in got.observations)


@pytest.mark.parametrize('body', [
    {'count': 9, 'uniques': 1, 'views': [{'timestamp': WINDOW.start, 'count': 3, 'uniques': 2}]},
    {'count': 3, 'uniques': 4, 'views': [{'timestamp': WINDOW.start, 'count': 3, 'uniques': 2}]},
    {'count': 3, 'uniques': 1, 'views': [{'timestamp': WINDOW.start, 'count': 3, 'uniques': 4}]},
])
def test_internally_partial_or_inconsistent_traffic_is_unknown(body):
    got = G.GitHubSource().collect(request(), FakeTransport(traffic=response(body)), lambda: NOW)
    assert got.errors[0].code == 'malformed'
    assert not any(r['metric'].startswith('traffic') for r in got.observations)


def test_top_ten_referrers_are_window_context_and_empty_list_is_observed_zero():
    rows = normalize('referrers', [{'referrer': f'site{i}.org', 'count': i, 'uniques': 0} for i in range(10)])
    assert len(rows) == 21 and rows[0]['value'] == 10
    assert all(r['counter_kind'] == 'window' for r in rows)
    assert all(not r['attribution']['supported'] for r in rows)
    assert normalize('referrers', [])[0]['value'] == 0
    with pytest.raises(S.SchemaError):
        normalize('referrers', [{'referrer': 'same.org', 'count': 1, 'uniques': 1}] * 11)


@pytest.mark.parametrize('value', [-1, True, float('nan'), float('inf'), '12', None])
def test_asset_counter_malformed_is_not_stored(value):
    with pytest.raises(S.SchemaError):
        normalize('asset', {'id': 42, 'download_count': value}, {**TARGET, 'asset_id': 42})


def test_asset_stable_identity_must_match_selected_asset():
    with pytest.raises(S.SchemaError):
        normalize('asset', {'id': 43, 'download_count': 50}, {**TARGET, 'asset_id': 42})


def test_bound_campaign_requests_are_refused_no_scope_upgrade():
    with pytest.raises(B.DefinitionRefused):
        G.GitHubSource().collect(replace(request(), campaign_id='camp-a'), FakeTransport(), lambda: NOW)


def test_definition_refusals_and_canonical_target():
    adapter = G.GitHubSource()
    definition = {'source_id': 'github', 'connection_id': 'conn-1', 'metric': 'stars',
                  'unit': 'stars', 'target': TARGET}
    connection = {'id': 'conn-1', 'project_id': 'proj1', 'target': TARGET}
    adapter.validate_definition(definition, connection)
    for patch in ({'unit': 'downloads'}, {'metric': 'stars_plus_downloads'},
                  {'project_id': 'other'}, {'connection_id': 'other'},
                  {'target': {**TARGET, 'repo': 'Repo'}}):
        with pytest.raises(B.DefinitionRefused):
            adapter.validate_definition({**definition, **patch}, connection)


@pytest.mark.parametrize('raw', [{}, {'kind': 'traffic'},
    {'kind': 'traffic', 'body': {'views': [None], 'count': 0, 'uniques': 0},
     'observed_at': NOW, 'request_count': 1}])
def test_normalize_malformed_envelopes_raise_schema_errors(raw):
    with pytest.raises(S.SchemaError):
        G.GitHubSource().normalize(raw, request())
