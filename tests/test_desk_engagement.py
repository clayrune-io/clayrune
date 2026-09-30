"""The Desk's inbound side — engagement feed, read costing, feed outcomes
(mc/desk_engagement.py, MC-977 R1-E, docs/THE_DESK_V1_IA_REVISION_2.md §8/§10.7).

Every platform response here is a recorded/fake payload behind the reader's
injectable transport: no test makes a live call, and the ones about a missing
credential assert the transport is NEVER reached.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_engagement as eng  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
PID = 'clayrune'


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(_desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(_desk, 'SIGNALS_PATH', tmp_path / 'sig.jsonl')
    return tmp_path


def _project(budget=10.0, platforms=('x',)):
    _desk.upsert_presence(PID, {
        'accounts': [{'platform': p, 'voice': 'personal'} for p in platforms],
        'budget': {'amount': budget, 'period': 'month', 'per_job': 0, 'kinds': []}})


def _post(url='https://x.com/ron/status/111', campaign_id='camp-1', cost=0.015,
          published='2026-09-28T10:00:00Z', platform='x'):
    return _desk.record_published(platform=platform, voice='personal', body='hello',
                                  project_id=PID, campaign_id=campaign_id, url=url,
                                  cost=cost, published_at=published)


class FakeX:
    """Recorded-shape X responses; counts every call."""

    def __init__(self, mentions=None, metrics=None, newest='900'):
        self.calls = []
        self.mentions = mentions if mentions is not None else []
        self.metrics = metrics if metrics is not None else []
        self.newest = newest

    def __call__(self, url, params, token):
        self.calls.append((url, dict(params)))
        if url.endswith('/users/me'):
            return {'data': {'id': '42', 'username': 'ron'}}
        if url.endswith('/mentions'):
            return {'data': self.mentions, 'includes': {'users': [
                {'id': 'u1', 'username': 'kat'}, {'id': 'u2', 'username': 'sam'}]},
                'meta': {'result_count': len(self.mentions), 'newest_id': self.newest}}
        if url.endswith('/tweets'):
            return {'data': self.metrics}
        raise AssertionError(url)


def _reader(fake):
    return {'x': eng.XReader(token='t', transport=fake), 'linkedin': eng.LinkedInReader()}


REPLY = {'id': '501', 'text': 'Does restore include memory?', 'author_id': 'u1',
         'created_at': '2026-09-30T09:00:00Z',
         'referenced_tweets': [{'type': 'replied_to', 'id': '111'}]}
MENTION = {'id': '502', 'text': 'quoted you', 'author_id': 'u2',
           'created_at': '2026-09-30T10:00:00Z'}


# -- coverage: a gap is never silence -------------------------------------------

def test_no_credential_reads_not_connected_and_never_touches_transport(store, monkeypatch):
    _project()
    # pin the vault to "no X read credential" so this holds on any machine
    monkeypatch.setattr(eng.secrets_store, 'list_secrets',
                        lambda *a, **k: [{'name': 'x.com'}])

    def boom(*a, **k):
        raise AssertionError('transport must not be reached without a credential')
    rd = {'x': eng.XReader(transport=boom), 'linkedin': eng.LinkedInReader()}
    assert rd['x'].capability()['connected'] is False
    rep = eng.poll_project(PID, readers=rd, now=NOW)
    assert rep['platforms']['x']['state'] == 'not_connected'
    b = eng.project_bundle(PID, readers=rd, now=NOW)
    assert b['status'] == 'not_connected'
    assert b['unread'] is None and b['awaiting'] is None and b['total'] is None
    assert b['message'] == "Not connected: replies on \U0001D54F aren't read yet"
    assert eng.overview(readers=rd, now=NOW)['unread_total'] is None


def test_linkedin_is_gap_only_with_its_own_reason(store):
    _project(platforms=('linkedin',))
    rd = {'linkedin': eng.LinkedInReader()}
    b = eng.project_bundle(PID, readers=rd, now=NOW)
    assert b['status'] == 'not_connected'
    assert b['message'] == "Not connected: replies on LinkedIn aren't read yet"
    assert 'Community Management' in b['coverage'][0]['reason']
    assert eng.poll_project(PID, readers=rd, now=NOW)['platforms']['linkedin']['spent'] == 0


def test_connected_but_never_read_is_not_a_zero(store):
    _project()
    b = eng.project_bundle(PID, readers=_reader(FakeX()), now=NOW)
    assert b['status'] == 'not_connected'          # no platform successfully read yet
    assert b['unread'] is None
    assert b['coverage'][0]['state'] == 'not_read_yet'


def test_partial_bundle_names_the_uncovered_platform(store):
    _project(platforms=('x', 'linkedin'))
    _post()
    rd = _reader(FakeX(mentions=[REPLY]))
    eng.poll_project(PID, readers=rd, now=NOW)
    b = eng.project_bundle(PID, readers=rd, now=NOW)
    assert b['status'] == 'partial'
    assert [g['platform'] for g in b['gaps']] == ['linkedin']
    assert b['unread'] == 1


# -- feed ingest ------------------------------------------------------------------

def test_poll_ingests_attributes_dedupes_and_advances_cursor(store):
    _project()
    post = _post()
    fake = FakeX(mentions=[REPLY, MENTION])
    rd = _reader(fake)
    rep = eng.poll_project(PID, readers=rd, now=NOW)
    assert rep['platforms']['x']['new_items'] == 2
    items = {i['external_id']: i for i in _desk.list_engagement_items(project_id=PID)}
    assert items['501']['source'] == 'our_posts'
    assert items['501']['post_id'] == post['id']
    assert items['501']['campaign_id'] == 'camp-1'       # from the ledger row
    assert items['501']['author'] == '@kat'
    assert items['502']['source'] == 'mentions' and items['502']['campaign_id'] is None
    assert items['501']['state'] == 'needs_you' and items['501']['read_at'] is None

    # second poll: same payload, nothing new, and the stored cursor is sent
    rep2 = eng.poll_project(PID, readers=rd, now=NOW)
    assert rep2['platforms']['x']['new_items'] == 0
    assert len(_desk.list_engagement_items(project_id=PID)) == 2
    mention_calls = [p for u, p in fake.calls if u.endswith('/mentions')]
    assert 'since_id' not in mention_calls[0] and mention_calls[1]['since_id'] == '900'


def test_reread_never_resurrects_a_read_item(store):
    _project()
    _post()
    rd = _reader(FakeX(mentions=[REPLY]))
    eng.poll_project(PID, readers=rd, now=NOW)
    item = _desk.list_engagement_items(project_id=PID)[0]
    assert _desk.mark_engagement_read(item['id'])['read_at']
    first = _desk.mark_engagement_read(item['id'])['read_at']
    eng.poll_project(PID, readers=rd, now=NOW)
    again = _desk.list_engagement_items(project_id=PID)[0]
    assert again['read_at'] == first                      # idempotent, kept


# -- aggregates match lane counts ---------------------------------------------------

def test_aggregates_match_lane_counts_and_read_at_drops_unread(store):
    _project()
    _post()
    rd = _reader(FakeX(mentions=[REPLY, MENTION]))
    eng.poll_project(PID, readers=rd, now=NOW)
    rows = _desk.list_engagement_items(project_id=PID)
    # put one row in each lane
    with _desk._store_lock:
        s = _desk._read_store()
        s['engagement']['items'][rows[0]['id']]['state'] = 'needs_reply'
        s['engagement']['items'][rows[1]['id']]['state'] = 'sent'
        _desk._write_store(s)
    b = eng.project_bundle(PID, readers=rd, now=NOW)
    lane = lambda st: len(_desk.list_engagement_items(project_id=PID, state=st))  # noqa: E731
    assert b['lanes'] == {'incoming': lane('needs_you'), 'suggested': lane('needs_reply'),
                          'sent': lane('sent')} == {'incoming': 0, 'suggested': 1, 'sent': 1}
    assert b['awaiting'] == b['lanes']['suggested'] == 1
    assert b['unread'] == 1                     # sent is never unread
    assert b['total'] == 2
    needs = next(r for r in rows if r['id'] == rows[0]['id'])
    _desk.mark_engagement_read(needs['id'])
    assert eng.project_bundle(PID, readers=rd, now=NOW)['unread'] == 0
    assert eng.overview(readers=rd, now=NOW)['unread_total'] == 0


# -- costing ---------------------------------------------------------------------

def test_reads_are_costed_and_counted_against_the_budget(store):
    _project(budget=10.0)
    _post(cost=0.015)
    fake = FakeX(mentions=[REPLY, MENTION],
                 metrics=[{'id': '111', 'public_metrics': {'impression_count': 900}}])
    eng.poll_project(PID, readers=_reader(fake), now=NOW)
    reads = _desk.list_reads(project_id=PID)
    assert [(r['kind'], r['resources'], r['ok']) for r in reads] == [
        ('replies', 3, True), ('metrics', 1, True)]      # 2 mentions + /users/me; 1 tweet
    assert sum(r['cost'] for r in reads) == pytest.approx(4 * eng.X_READ_UNIT_COST)
    sp = eng.project_spend(PID, now=NOW)
    assert sp['published'] == pytest.approx(0.015)
    assert sp['reads'] == pytest.approx(0.02)
    assert sp['room'] == pytest.approx(10.0 - 0.035)


def test_zero_budget_means_no_paid_read_not_unlimited(store):
    _project(budget=0)
    _post()
    fake = FakeX(mentions=[REPLY])
    rep = eng.poll_project(PID, readers=_reader(fake), now=NOW)
    assert fake.calls == []
    assert rep['platforms']['x']['budget_blocked'] is True
    assert _desk.list_reads(project_id=PID) == []
    assert _desk.get_read_coverage(PID)['x']['last_error'].startswith('read budget spent')


def test_budget_spent_by_publishing_blocks_reads(store):
    _project(budget=1.0)
    _post(cost=1.0)
    fake = FakeX(mentions=[REPLY])
    eng.poll_project(PID, readers=_reader(fake), now=NOW)
    assert fake.calls == []


def test_failed_read_is_recorded_and_reported_not_swallowed(store):
    _project()

    def down(url, params, token):
        raise eng.ReadError('X API HTTP 429: rate limited')
    rd = {'x': eng.XReader(token='t', transport=down)}
    rep = eng.poll_project(PID, readers=rd, now=NOW)
    assert 'HTTP 429' in rep['platforms']['x']['error']
    assert _desk.list_reads(project_id=PID)[0]['ok'] is False
    b = eng.project_bundle(PID, readers=rd, now=NOW)
    assert b['status'] == 'not_connected' and b['unread'] is None


# -- per-post outcomes, source:'feed' ---------------------------------------------

def test_feed_outcome_never_overwrites_a_typed_entry(store):
    _project()
    post = _post()
    _desk.record_outcome(post['id'], 'impressions', 700, source='manual', at='2026-09-30T08:00:00Z')
    fake = FakeX(metrics=[{'id': '111', 'public_metrics': {'impression_count': 900, 'like_count': 3}}])
    eng.poll_project(PID, readers=_reader(fake), now=NOW)
    outs = _desk.list_ledger(project_id=PID)[0]['outcomes']
    imp = [o for o in outs if o['metric'] == 'impressions']
    assert sorted((o['source'], o['value']) for o in imp) == [('feed', 900), ('manual', 700)]
    assert [(o['source'], o['value']) for o in outs if o['metric'] == 'likes'] == [('feed', 3)]


def test_same_day_reread_replaces_feed_entry_only(store):
    post = _post()
    _desk.record_outcome(post['id'], 'likes', 1, source='manual', at='2026-09-30T01:00:00Z')
    _desk.record_feed_outcome(post['id'], 'likes', 5, at='2026-09-30T02:00:00Z')
    _desk.record_feed_outcome(post['id'], 'likes', 8, at='2026-09-30T15:00:00Z')
    _desk.record_feed_outcome(post['id'], 'likes', 9, at='2026-10-01T02:00:00Z')
    outs = _desk.list_ledger()[0]['outcomes']
    assert sorted((o['source'], o['value'], o['at'][:10]) for o in outs) == [
        ('feed', 8, '2026-09-30'), ('feed', 9, '2026-10-01'), ('manual', 1, '2026-09-30')]


def test_metric_the_platform_did_not_return_is_absent_never_zero(store):
    _project()
    post = _post()
    fake = FakeX(metrics=[{'id': '111', 'public_metrics': {'like_count': 2}}])
    eng.poll_project(PID, readers=_reader(fake), now=NOW)
    metrics = {o['metric'] for o in _desk.list_ledger()[0]['outcomes']}
    assert metrics == {'likes'}                  # no impressions/clicks cell invented
    assert post['id']


def test_platform_with_no_metrics_read_writes_no_outcomes(store):
    _project(platforms=('linkedin',))
    post = _post(platform='linkedin', url='https://www.linkedin.com/feed/update/urn:li:share:1')
    eng.poll_project(PID, readers={'linkedin': eng.LinkedInReader()}, now=NOW)
    assert _desk.list_ledger()[0]['outcomes'] == []
    assert post['id']


def test_already_measured_today_is_not_paid_for_twice(store):
    _project()
    _post()
    fake = FakeX(metrics=[{'id': '111', 'public_metrics': {'like_count': 2}}])
    rd = _reader(fake)
    eng.poll_project(PID, readers=rd, now=NOW)
    eng.poll_project(PID, readers=rd, now=NOW)
    assert sum(1 for u, _ in fake.calls if u.endswith('/tweets')) == 1


def test_old_posts_are_not_metric_read(store):
    _project()
    _post(published='2026-07-01T00:00:00Z')
    fake = FakeX()
    eng.poll_project(PID, readers=_reader(fake), now=NOW)
    assert not any(u.endswith('/tweets') for u, _ in fake.calls)


# -- store ------------------------------------------------------------------------

def test_store_without_engagement_key_migrates(store):
    import json
    (store / 'desk.json').write_text(json.dumps({'version': 2, 'ledger': []}), encoding='utf-8')
    assert _desk.list_engagement_items() == []
    row, created = _desk.upsert_engagement_item(
        {'platform': 'x', 'external_id': '1', 'project_id': PID})
    assert created and row['state'] == 'needs_you'


# -- routes -------------------------------------------------------------------------

@pytest.fixture
def client(store):
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(load_projects_fn=lambda: [], load_project_fn=lambda _p: None,
                     store_path=store / 'desk.json', signals_path=store / 'sig.jsonl')
    app.register_blueprint(desk_routes.bp)
    return app.test_client()


def test_routes_overview_read_and_poll_validation(client, monkeypatch):
    _project()
    monkeypatch.setattr(eng, 'default_readers', lambda: _reader(FakeX(mentions=[REPLY])))
    _post()
    assert client.post('/api/desk/engagement/poll', json={}).status_code == 400
    assert client.get('/api/desk/engagement/overview?period=year').status_code == 400
    assert client.post('/api/desk/engagement/nope/read').status_code == 404

    r = client.post('/api/desk/engagement/poll', json={'project_id': PID})
    assert r.status_code == 200 and r.get_json()['platforms']['x']['new_items'] == 1
    ov = client.get('/api/desk/engagement/overview').get_json()
    assert ov['unread_total'] == 1 and ov['bundles'][0]['status'] == 'connected'
    item = client.get(f'/api/desk/engagement?project_id={PID}').get_json()[0]
    assert client.post(f"/api/desk/engagement/{item['id']}/read").get_json()['read_at']
    assert client.get('/api/desk/engagement/overview').get_json()['unread_total'] == 0


def test_poll_route_refuses_unattended_caller(client, monkeypatch):
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', lambda *a, **k: True)
    assert client.post('/api/desk/engagement/poll', json={'project_id': PID}).status_code == 403
