"""MC-1152 P4: the dark comparison routes and their service, against the REAL Desk store.

Spec: docs/desk_v1/CHANNEL_IMPACT_SPEC.md sections 10 and 12 row P4 (D1 = A).
Pinned here: 404 while `desk_impact_compare_enabled` is off; the GET is local
(no transport, no vault, no platform reader, no write); every channel conversion
is `Not attributable by this source`; repository context is one separate block;
the two writes are human-gated; with no connection nothing can be saved or read;
the manual Goal is never touched. No network; every store lives in tmp_path.
"""
from __future__ import annotations

import copy
import json
import socket
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_impact_view as view  # noqa: E402
from mc import state  # noqa: E402
from mc.blueprints import desk_impact_compare_routes as routes  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402
from mc.desk_impact_store import ImpactStore  # noqa: E402
from tests.desk_impact_github_fakes import FakeTransport, TARGET, response, star  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
CID = 'camp-p4'
NOT_ATTR = 'Not attributable by this source'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError('P4 tests may not use the network')
    monkeypatch.setattr(socket.socket, 'connect', refuse)
    monkeypatch.setattr(socket, 'create_connection', refuse)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(desk_routes, '_require_human_passcode', lambda data: None)
    monkeypatch.setattr(routes, '_require_human_passcode', lambda data: None)
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: False)
    monkeypatch.setattr(state, 'CONFIG', {view.CONFIG_KEY: True}, raising=False)
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'data' / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=uploads,
    )
    app.register_blueprint(desk_routes.bp)
    app.register_blueprint(routes.bp)
    client = app.test_client()
    r = client.post('/api/desk/campaigns?shape=v1', json={
        'id': CID, 'state': 'draft', 'projectId': 'alpha', 'rules': {},
        'map': {'stop': 'what', 'done': []},
        'plan': {'brief': 'b', 'title': 'P4', 'cadence': {}, 'end': {},
                 'accounts': [{'id': 'ch-x', 'platform': 'x'}, {'id': 'ch-li', 'platform': 'linkedin'}]}})
    assert r.status_code == 201, r.get_json()
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-x', 'platform': 'x', 'identity': '@a'},
        {'channel_id': 'ch-li', 'platform': 'linkedin', 'identity': 'Clay'}]})
    base = datetime.fromisoformat(_camp()['created_at'].replace('Z', '+00:00'))
    now = base.replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(days=3)
    connections = {'conn-1': {'id': 'conn-1', 'project_id': 'alpha', 'target': TARGET,
                               'vault_ref': 'chosen-by-human', 'read_enabled': True}}
    resolver = lambda ident: copy.deepcopy(connections.get(ident))
    root = tmp_path / 'data' / 'desk_impact'
    clock = lambda: now
    store = ImpactStore(root, data_dir=tmp_path / 'data' / 'projects', connection_resolver=resolver,
                        clock=lambda: now.isoformat())
    view.wire(store=store, connection_resolver=resolver, clock=clock)
    yield {'client': client, 'tmp': tmp_path, 'root': root, 'now': now, 'connections': connections,
           'resolver': resolver}
    view.wire()


def _camp():
    with _desk._store_lock:
        return copy.deepcopy(_desk._read_store()['campaigns'][CID])


def _define(client, expected=0, **over):
    spec = {'label': 'Stars', 'source_id': 'github', 'connection_id': 'conn-1', 'metric': 'stars',
            'unit': 'stars', 'target': TARGET}
    spec.update(over)
    return client.put(f'/api/desk/impact/definition/{CID}',
                      json={'definition': spec, 'expected_revision': expected})


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()} \
        if root.exists() else {}


def _publish(env, account, published_at, outcomes=()):
    row = _desk.record_published(platform='x' if account == 'ch-x' else 'linkedin', voice='', body='b',
                                 campaign_id=CID, project_id='alpha', account=account,
                                 published_at=published_at, piece_id=None)
    for metric, value, at in outcomes:
        _desk.record_feed_outcome(row['id'], metric, value, at=at)
    return row


def _inside(env, days_after_start=1):
    start = datetime.fromisoformat(
        _camp()['created_at'].replace('Z', '+00:00')).replace(hour=0, minute=0, second=0, microsecond=0)
    return (start + timedelta(days=days_after_start, hours=9)).isoformat()


# -- darkness ---------------------------------------------------------------------------

def test_every_route_but_status_is_404_while_the_flag_is_off(env, monkeypatch):
    c = env['client']
    monkeypatch.setattr(state, 'CONFIG', {}, raising=False)
    before = _tree(env['tmp'])
    assert c.get('/api/desk/impact/compare/status').get_json() == {'enabled': False}
    assert c.get(f'/api/desk/impact/compare/{CID}').status_code == 404
    assert c.get(f'/api/desk/impact/definition/{CID}').status_code == 404
    assert _define(c).status_code == 404
    assert c.post(f'/api/desk/impact/refresh/{CID}', json={}).status_code == 404
    assert _tree(env['tmp']) == before
    monkeypatch.setattr(state, 'CONFIG', {view.CONFIG_KEY: 'true'}, raising=False)
    assert view.enabled() is False           # only a real boolean True turns it on
    assert c.get(f'/api/desk/impact/compare/{CID}').status_code == 404


def test_default_is_off_with_no_config_at_all(monkeypatch):
    monkeypatch.setattr(state, 'CONFIG', None, raising=False)
    assert view.enabled() is False
    monkeypatch.setattr(state, 'CONFIG', {}, raising=False)
    assert view.enabled() is False


# -- the cached comparison ----------------------------------------------------------------

def test_no_definition_shows_every_plan_account_with_reasons_not_zeros(env):
    got = env['client'].get(f'/api/desk/impact/compare/{CID}').get_json()
    assert [r['account_id'] for r in got['channels']] == ['ch-x', 'ch-li']
    for row in got['channels']:
        assert row['published_posts'] == 0
        assert row['conversions'] == {'value': None, 'reason': 'No conversion definition'}
        assert row['platform_clicks']['value'] is None and row['platform_clicks']['reason']
        assert row['post_metrics'] == {} and row['post_metrics_coverage']['reason']
    assert got['definition'] is None and got['repository_context'] is None
    assert got['connection']['state'] == 'no_definition'
    assert got['auto_progress']['state'] == 'dark' and got['auto_progress']['value'] is None


def test_post_metrics_are_latest_totals_and_clicks_keep_their_own_column(env):
    ts = _inside(env)
    _publish(env, 'ch-x', ts, [('impressions', 100, ts), ('impressions', 140, ts), ('clicks', 6, ts)])
    _publish(env, 'ch-x', ts, [('impressions', 10, ts)])
    got = env['client'].get(f'/api/desk/impact/compare/{CID}').get_json()
    row = next(r for r in got['channels'] if r['account_id'] == 'ch-x')
    assert row['published_posts'] == 2
    # 140 is the latest of the first post's two reads; the second post has no 'clicks'.
    assert row['post_metrics']['impressions']['value'] == 150
    assert row['post_metrics']['impressions']['label'] == 'Latest totals for posts published in this period'
    assert 'clicks' not in row['post_metrics']
    assert row['platform_clicks']['value'] is None            # one of two posts unread: unknown, not 6
    assert 'Some posts' in row['platform_clicks'].get('reason', '')
    other = next(r for r in got['channels'] if r['account_id'] == 'ch-li')
    assert other['published_posts'] == 0 and other['post_metrics'] == {}


def test_a_removed_account_keeps_its_history_row_and_cannot_be_added(env):
    ts = _inside(env)
    _publish(env, 'ch-gone', ts, [('impressions', 5, ts)])
    got = env['client'].get(f'/api/desk/impact/compare/{CID}').get_json()
    ids = [r['account_id'] for r in got['channels']]
    assert ids == ['ch-x', 'ch-li', 'ch-gone']
    gone = got['channels'][-1]
    assert gone['removed'] is True and gone['in_plan'] is False and gone['published_posts'] == 1


def test_github_is_one_separate_block_and_every_channel_cell_is_not_attributable(env):
    c = env['client']
    assert _define(c).status_code == 200
    store = view.get_store()
    from tests.desk_impact_github_fakes import normalize
    from mc.desk_impact_sources import base as B
    window = B.Window(*_window_of(c))
    for row in normalize('stars', [star(1, '2026-11-03T12:00:00Z')], window=window, now=window.end):
        store.append_snapshot('alpha', row)
    got = c.get(f'/api/desk/impact/compare/{CID}').get_json()
    assert got['definition']['metric'] == 'stars'
    for row in got['channels']:
        assert row['conversions'] == {'value': None, 'reason': NOT_ATTR}
        assert 'repository' not in json.dumps(row).lower()      # never repeated per channel
    ctx = got['repository_context']
    assert ctx['label'] == 'Repository context — not attributed to this campaign'
    assert ctx['target'] == TARGET
    assert got['not_attributable'] == NOT_ATTR


def _window_of(client):
    w = client.get(f'/api/desk/impact/compare/{CID}').get_json()['requested_window']
    return w['start'], w['end'], w['granularity']


def test_window_is_whole_utc_days_before_today_and_open_when_the_campaign_began_today(env):
    w = env['client'].get(f'/api/desk/impact/compare/{CID}').get_json()
    assert w['requested_window']['end'] == env['now'].replace(hour=0).isoformat()
    assert w['historical'] is True and 'not including, today' in w['window_note']
    view.wire(store=view.get_store(), connection_resolver=env['resolver'],
              clock=lambda: env['now'] - timedelta(days=3))
    same_day = env['client'].get(f'/api/desk/impact/compare/{CID}').get_json()
    assert same_day['historical'] is False and 'began today' in same_day['window_note']


def test_caller_window_must_be_valid_and_paired(env):
    c = env['client']
    assert c.get(f'/api/desk/impact/compare/{CID}?start=2026-01-01T00:00:00Z').status_code == 400
    assert c.get(f'/api/desk/impact/compare/{CID}?start=nonsense&end=2026-01-02T00:00:00Z').status_code == 400
    ok = c.get(f'/api/desk/impact/compare/{CID}?start=2026-01-01T00:00:00Z&end=2026-01-02T00:00:00Z')
    assert ok.status_code == 200 and ok.get_json()['requested_window']['start'].startswith('2026-01-01')


def test_get_is_local_and_writes_nothing(env, monkeypatch):
    c = env['client']
    assert _define(c).status_code == 200
    def boom(*a, **k):
        raise AssertionError('a GET must not collect, read the vault or call a platform reader')
    import mc.desk_impact_collect as collect
    monkeypatch.setattr(collect, 'collect_campaigns', boom)
    monkeypatch.setattr(view, 'collect_campaigns', boom)
    from mc import secrets_store
    monkeypatch.setattr(secrets_store, 'get_secret_value', boom)
    import mc.desk_engagement_poll as poll
    monkeypatch.setattr(poll, 'poll_once', boom, raising=False)
    view.wire(store=view.get_store(), connection_resolver=env['resolver'], transport_factory=boom,
              clock=lambda: env['now'])
    ts = _inside(env)
    _publish(env, 'ch-x', ts, [('impressions', 3, ts)])
    snapshot = (_tree(env['root']), (env['tmp'] / 'data' / 'desk.json').read_bytes())
    for _ in range(3):
        assert c.get(f'/api/desk/impact/compare/{CID}').status_code == 200
        assert c.get(f'/api/desk/impact/definition/{CID}').status_code == 200
    assert (_tree(env['root']), (env['tmp'] / 'data' / 'desk.json').read_bytes()) == snapshot


def test_unknown_and_malformed_campaign_ids(env):
    c = env['client']
    assert c.get('/api/desk/impact/compare/camp-nope').status_code == 404
    assert c.get('/api/desk/impact/compare/a.b').status_code == 400
    assert c.get('/api/desk/impact/definition/camp-nope').status_code == 404


# -- the definition (human-only write) -----------------------------------------------------

def test_default_build_has_no_connection_so_no_definition_can_be_saved(env):
    view.wire()                                   # production defaults: no connection source
    r = _define(env['client'])
    assert r.status_code == 409 and r.get_json()['code'] == 'no_connection'
    assert not (env['tmp'] / 'data' / 'desk_impact').exists() or \
        not (env['tmp'] / 'data' / 'desk_impact' / 'alpha' / 'definitions.json').exists()


def test_definition_round_trip_revision_and_manual_goal_untouched(env):
    c = env['client']
    goal_before = _camp().get('goal')
    first = _define(c)
    assert first.status_code == 200 and first.get_json()['definition']['revision'] == 1
    again = _define(c, expected=1)
    assert again.status_code == 200                       # identical content: idempotent no-op
    changed = _define(c, expected=1, label='Stars v2')
    assert changed.get_json()['definition']['revision'] == 2
    stale = _define(c, expected=1, label='Stars v3')
    assert stale.status_code == 409 and stale.get_json()['code'] == 'revision_conflict'
    got = c.get(f'/api/desk/impact/definition/{CID}').get_json()
    assert [r['revision'] for r in got['revisions']] == [1, 2]
    assert _camp().get('goal') == goal_before     # the Goal is never reinterpreted


def test_definition_refuses_unsupported_metric_and_foreign_connection(env):
    c = env['client']
    assert _define(c, metric='revenue').status_code == 400
    env['connections']['conn-2'] = {'id': 'conn-2', 'project_id': 'other', 'target': TARGET,
                                    'vault_ref': 'x', 'read_enabled': True}
    r = _define(c, connection_id='conn-2')
    assert r.status_code in (404, 409)
    assert c.get(f'/api/desk/impact/definition/{CID}').get_json()['definition'] is None


def test_both_writes_are_human_only(env, monkeypatch):
    c = env['client']
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: True)
    before = _tree(env['root'])
    r = _define(c)
    assert r.status_code == 403 and r.get_json()['code'] == 'human_only'
    assert c.post(f'/api/desk/impact/refresh/{CID}', json={}).status_code == 403
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: False)
    from flask import jsonify
    monkeypatch.setattr(routes, '_require_human_passcode',
                        lambda data: (jsonify({'error': 'passcode_required'}), 403))
    assert _define(c).status_code == 403
    assert c.post(f'/api/desk/impact/refresh/{CID}', json={}).status_code == 403
    assert _tree(env['root']) == before


# -- refresh ----------------------------------------------------------------------------------

def _transport(env):
    day = (env['now'] - timedelta(days=1)).replace(hour=0).strftime('%Y-%m-%dT00:00:00Z')
    return FakeTransport(
        stars=[response([star(1, day)])],
        traffic=response({'count': 4, 'uniques': 2, 'views': [{'timestamp': day, 'count': 4, 'uniques': 2}]}),
        referrers=response([{'referrer': 'example.org', 'count': 4, 'uniques': 2}]))


def test_refresh_reads_once_then_serves_the_cache(env):
    c = env['client']
    assert _define(c).status_code == 200
    transport = _transport(env)
    view.wire(store=view.get_store(), connection_resolver=env['resolver'],
              transport_factory=lambda pid, resolver: transport, clock=lambda: env['now'])
    first = c.post(f'/api/desk/impact/refresh/{CID}', json={})
    assert first.status_code == 200, first.get_json()
    body = first.get_json()
    assert body['collection']['request_count'] == len(transport.calls) > 0
    ctx = body['comparison']['repository_context']
    assert ctx['observations'] and all(o['scope'] for o in ctx['observations'])
    n = len(transport.calls)
    second = c.post(f'/api/desk/impact/refresh/{CID}', json={})
    assert second.status_code == 200 and len(transport.calls) == n        # no bypass, no second read
    assert second.get_json()['collection']['request_count'] == 0


def test_refresh_refuses_an_open_window_and_reads_nothing(env):
    c = env['client']
    assert _define(c).status_code == 200
    calls = []
    view.wire(store=view.get_store(), connection_resolver=env['resolver'],
              transport_factory=lambda pid, resolver: calls.append(1), clock=lambda: env['now'] - timedelta(days=3))
    r = c.post(f'/api/desk/impact/refresh/{CID}', json={})
    assert r.status_code == 400 and 'still open' in r.get_json()['error'] and calls == []


def test_refresh_with_no_definition_reads_nothing(env):
    calls = []
    view.wire(store=view.get_store(), connection_resolver=env['resolver'],
              transport_factory=lambda pid, resolver: calls.append(1) or FakeTransport(), clock=lambda: env['now'])
    r = env['client'].post(f'/api/desk/impact/refresh/{CID}', json={})
    assert r.status_code == 200 and r.get_json()['collection']['request_count'] == 0
    assert r.get_json()['collection']['campaigns'][CID]['reason'] == 'No conversion definition'


def test_refresh_when_the_connection_has_gone_says_so_and_reads_nothing(env):
    c = env['client']
    assert _define(c).status_code == 200
    env['connections'].clear()
    calls = []
    view.wire(store=view.get_store(), connection_resolver=env['resolver'],
              transport_factory=lambda pid, resolver: calls.append(1), clock=lambda: env['now'])
    r = c.post(f'/api/desk/impact/refresh/{CID}', json={})
    assert r.status_code == 409 and r.get_json()['code'] == 'no_connection' and calls == []
    got = c.get(f'/api/desk/impact/compare/{CID}').get_json()
    assert got['connection']['state'] == 'unavailable'
    assert all(row['conversions']['reason'] == NOT_ATTR for row in got['channels'])


def test_impact_store_cannot_become_a_project_record(env):
    root = env['root']
    assert _define(env['client']).status_code == 200
    projects = env['tmp'] / 'data' / 'projects'
    assert not any(p for p in root.rglob('*') if projects in p.parents)
    with pytest.raises(ValueError):
        ImpactStore(projects / 'desk_impact', data_dir=projects)
