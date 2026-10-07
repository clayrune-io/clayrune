"""bb65f3bb: the Desk spend guard (`mc/desk_spend_guard.py`).

Remaining budget = campaign `how.budget` - spent (engine jobs + ledger post
costs) - tagged read rows - live holds. Covered: exhausted, exactly at the limit,
no budget, free calls, the race for the last of a budget, a failed call giving
its hold back, and each of the three boundaries (publish, verify_post, the paid
metrics read). Nothing here reaches a network; the transport is always a fake.
"""
from __future__ import annotations

import sys
import threading
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import desk as _desk  # noqa: E402
from mc import desk_engagement as eng  # noqa: E402
from mc import desk_publish as _publish  # noqa: E402
from mc import desk_spend_guard as guard  # noqa: E402
from test_desk_engagement import FakeX  # noqa: E402

PID = 'clayrune'
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
POST = guard.X_POST_COST          # 0.015
READ = eng.X_READ_UNIT_COST       # 0.005


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(_desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(_desk, 'SIGNALS_PATH', tmp_path / 'sig.jsonl')
    monkeypatch.setattr(_publish, 'RECEIPTS_PATH', tmp_path / 'receipts.json')
    monkeypatch.setattr(_publish._oauth, 'x_token', lambda **k: 'tok')
    monkeypatch.setattr(_publish, '_get_username', lambda token: 'ron')
    monkeypatch.setattr(_desk, 'publish_blockers', lambda cid: [])
    guard._reservations.clear()
    yield tmp_path
    guard._reservations.clear()


def _camp(cid='camp-1', amount=1.0, source='own', title='Launch week'):
    how = {'budget': {'source': source, 'amount': amount}}
    c = _desk.create_campaign(title, 'thesis', voiceless_ok=True, project_id=PID, how=how,
                              campaign_id=cid)
    return c['id']


def _spend(cid, cost, url='https://x.com/ron/status/111'):
    return _desk.record_published(platform='x', voice='personal', body='b', project_id=PID,
                                  campaign_id=cid, url=url, cost=cost)


# -- the budget arithmetic ----------------------------------------------------------

def test_a_post_that_fits_is_held_and_released(store):
    cid = _camp(amount=0.05)
    res = guard.reserve(cid, POST, what='posting to x')
    assert res is not None
    assert guard.campaign_budget(cid)['reserved'] == pytest.approx(POST)
    assert guard.campaign_budget(cid)['remaining'] == pytest.approx(0.05 - POST)
    guard.release(res)
    guard.release(res)                          # twice is harmless
    assert guard.campaign_budget(cid)['remaining'] == pytest.approx(0.05)


def test_exhausted_budget_refuses_and_names_the_campaign(store):
    cid = _camp(amount=0.02, title='Launch week')
    _spend(cid, 0.02)
    with pytest.raises(guard.SpendRefused) as e:
        guard.reserve(cid, POST, what='posting to x')
    msg = str(e.value)
    assert '"Launch week"' in msg and '$0.000 left' in msg and 'raise it' in msg


def test_exactly_at_the_limit_passes_and_one_cent_over_does_not(store):
    cid = _camp(amount=POST)
    held = guard.reserve(cid, POST, what='posting to x')      # cost == remaining
    assert held is not None
    with pytest.raises(guard.SpendRefused):
        guard.reserve(cid, 0.001, what='anything else')       # nothing left
    guard.release(held)
    _spend(cid, 0.01)
    with pytest.raises(guard.SpendRefused):
        guard.reserve(cid, 0.006, what='posting to x')        # remaining 0.005
    assert guard.reserve(cid, 0.005, what='a read') is not None


def test_no_budget_means_zero_and_says_to_set_one_in_the_brief(store):
    cid = _camp(source='none', title='Quiet one')
    with pytest.raises(guard.SpendRefused) as e:
        guard.reserve(cid, POST, what='posting to x')
    assert '"Quiet one"' in str(e.value) and 'set a budget in its brief' in str(e.value)


def test_free_calls_are_never_blocked(store):
    cid = _camp(source='none')
    assert guard.reserve(cid, 0.0, what='a pane read') is None
    assert guard.reserve(None, POST, what='no campaign') is None
    assert guard.post_cost('linkedin', 'We shipped https://x.test') == 0.0
    assert guard.read_cost('linkedin', 5) == 0.0


def test_read_rows_tagged_with_the_campaign_count_untagged_do_not(store):
    cid = _camp(amount=0.01)
    _desk.record_read(platform='x', project_id=PID, kind='replies', resources=9, cost=0.045, ok=True)
    assert guard.campaign_budget(cid)['remaining'] == pytest.approx(0.01)       # a mention read
    _desk.record_read(platform='x', project_id=PID, kind='metrics', resources=1, cost=READ,
                      ok=True, campaign_id=cid)
    assert guard.campaign_budget(cid)['reads'] == pytest.approx(READ)
    assert guard.campaign_budget(cid)['remaining'] == pytest.approx(0.01 - READ)


def test_unreadable_spend_records_refuse_rather_than_read_as_zero(store, monkeypatch):
    cid = _camp(amount=5)
    from mc import desk_engines
    monkeypatch.setattr(desk_engines, '_read_store', lambda: (_ for _ in ()).throw(RuntimeError('torn file')))
    with pytest.raises(guard.SpendRefused, match='could not be read'):
        guard.reserve(cid, POST, what='posting to x')


def test_a_campaign_that_is_gone_is_refused(store):
    with pytest.raises(guard.SpendRefused, match='no longer exists'):
        guard.reserve('camp-gone', POST, what='posting to x')


# -- the race ----------------------------------------------------------------------

def test_two_calls_racing_for_the_last_of_a_budget_do_not_both_pass(store):
    cid = _camp(amount=POST * 2 + 0.001)          # room for exactly two posts
    start, passed, refused = threading.Barrier(8), [], []

    def go():
        start.wait()
        try:
            passed.append(guard.reserve(cid, POST, what='posting to x'))
        except guard.SpendRefused:
            refused.append(1)
    ts = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert (len(passed), len(refused)) == (2, 6)
    # and the holds are real: with both held there is no room for a third
    with pytest.raises(guard.SpendRefused):
        guard.reserve(cid, POST, what='posting to x')


# -- boundary 1: publish -----------------------------------------------------------

def _item(cid, body='hello', item_id='i1', platform='x'):
    return {'id': item_id, 'platform': platform, 'body': body, 'campaign_id': cid,
            'organization_id': '123'}


def test_publish_over_budget_is_refused_before_any_token_or_network(store, monkeypatch):
    cid = _camp(amount=0.01)
    monkeypatch.setattr(_publish._oauth, 'x_token', lambda **k: pytest.fail('a token was fetched'))
    monkeypatch.setattr(_publish, '_post_tweet', lambda *a, **k: pytest.fail('the post was sent'))
    with pytest.raises(_publish.PublishError, match='has \\$0.010 left'):
        _publish.publish(_item(cid))
    assert _publish.get_receipt('i1') is None


def test_publish_with_room_posts_and_a_failed_post_gives_its_hold_back(store, monkeypatch):
    cid = _camp(amount=POST)
    sent = []

    def post(token, body, reply=None):
        sent.append(body)
        raise urllib.error.URLError('down')
    monkeypatch.setattr(_publish, '_post_tweet', post)
    with pytest.raises(_publish.PublishError, match='MAY'):
        _publish.publish(_item(cid))
    assert sent == ['hello'] and guard.campaign_budget(cid)['reserved'] == 0
    # the hold came back, so the same last post can be tried again
    monkeypatch.setattr(_publish, '_post_tweet', lambda t, b, r=None: {'data': {'id': '9'}})
    assert _publish.publish(_item(cid, item_id='i2'))['post_id'] == '9'
    assert guard.campaign_budget(cid)['reserved'] == 0


def test_a_link_post_costs_the_link_rate(store):
    cid = _camp(amount=0.1)                        # fits 0.015, not 0.20
    with pytest.raises(_publish.PublishError, match=r'costs \$0.200'):
        _publish.publish(_item(cid, body='see https://clayrune.io'))


def test_publish_without_a_budget_or_on_linkedin(store, monkeypatch):
    cid = _camp(source='none', title='Quiet one')
    with pytest.raises(_publish.PublishError, match='set a budget in its brief'):
        _publish.publish(_item(cid))
    # LinkedIn is free: a campaign with no budget still posts there
    monkeypatch.setattr(_publish, '_post_linkedin', lambda *a: {'id': 'urn:li:share:1'})
    monkeypatch.setattr(_publish.secrets_store, 'get_secret_value', lambda *a, **k: 'tok')
    assert _publish.publish(_item(cid, platform='linkedin', item_id='li1'))['post_id'] == 'urn:li:share:1'


def test_an_item_with_no_campaign_is_not_gated(store, monkeypatch):
    monkeypatch.setattr(_publish, '_post_tweet', lambda t, b, r=None: {'data': {'id': '7'}})
    assert _publish.publish({'id': 'r1', 'platform': 'x', 'body': 'a reply'})['post_id'] == '7'


def test_the_handle_is_looked_up_once_per_account(store, monkeypatch):
    cid = _camp(amount=5)
    calls = []
    monkeypatch.setattr(_publish, '_get_username', lambda token: calls.append(token) or 'ron')
    monkeypatch.setattr(_publish, '_post_tweet', lambda t, b, r=None: {'data': {'id': '5'}})
    a = _publish.publish(_item(cid, item_id='a'))
    b = _publish.publish(_item(cid, item_id='b'))
    assert len(calls) == 1
    assert a['permalink'] == 'https://x.com/ron/status/5' and b['permalink'] == a['permalink']


def test_a_failed_handle_lookup_is_not_cached(store, monkeypatch):
    cid = _camp(amount=5)
    names = iter(['', 'ron'])
    monkeypatch.setattr(_publish, '_get_username', lambda token: next(names))
    monkeypatch.setattr(_publish, '_post_tweet', lambda t, b, r=None: {'data': {'id': '5'}})
    assert 'i/web' in _publish.publish(_item(cid, item_id='a'))['permalink']
    assert 'ron' in _publish.publish(_item(cid, item_id='b'))['permalink']


# -- boundary 2: verify_post -------------------------------------------------------

def test_verify_over_budget_is_refused_as_a_read_consent_style_denial(store, monkeypatch):
    cid = _camp(amount=0.004)                       # a read is 0.005
    monkeypatch.setattr(_publish, '_get_tweet', lambda *a: pytest.fail('X was asked'))
    with pytest.raises(_publish.ReadConsentDenied, match='checking that the post is live'):
        _publish.verify_post('x', '1', project_id=PID, campaign_id=cid)
    assert isinstance(_publish.ReadSpendRefused('x'), _publish.ReadConsentDenied)


def test_verify_records_the_read_on_the_campaign_and_it_counts(store, monkeypatch):
    cid = _camp(amount=0.01)
    monkeypatch.setattr(_publish, '_get_tweet', lambda t, p: {'data': {'id': p}})
    assert _publish.verify_post('x', '1', project_id=PID, campaign_id=cid) is True
    row = _desk.list_reads(project_id=PID)[0]
    assert (row['kind'], row['cost'], row['campaign_id']) == ('verify', READ, cid)
    assert guard.campaign_budget(cid)['remaining'] == pytest.approx(0.01 - READ)
    assert _publish.verify_post('x', '1', project_id=PID, campaign_id=cid) is True
    with pytest.raises(_publish.ReadConsentDenied):                # 0.01 is now spent
        _publish.verify_post('x', '1', project_id=PID, campaign_id=cid)


def test_a_failed_verify_gives_its_hold_back_and_bills_nothing(store, monkeypatch):
    cid = _camp(amount=READ)

    def boom(t, p):
        raise urllib.error.HTTPError('u', 503, 'down', {}, None)  # type: ignore[arg-type]
    monkeypatch.setattr(_publish, '_get_tweet', boom)
    with pytest.raises(_publish.PublishError):
        _publish.verify_post('x', '1', project_id=PID, campaign_id=cid)
    assert guard.campaign_budget(cid)['reserved'] == 0 and _desk.list_reads() == []

    def gone(t, p):
        raise urllib.error.HTTPError('u', 404, 'nf', {}, None)    # type: ignore[arg-type]
    monkeypatch.setattr(_publish, '_get_tweet', gone)
    assert _publish.verify_post('x', '1', project_id=PID, campaign_id=cid) is False
    assert guard.campaign_budget(cid)['reserved'] == 0 and _desk.list_reads() == []


def test_verify_with_no_campaign_or_a_free_platform_is_unaffected(store, monkeypatch):
    monkeypatch.setattr(_publish, '_get_tweet', lambda t, p: {'data': {'id': p}})
    assert _publish.verify_post('x', '1', project_id=PID) is True
    assert _publish.verify_post('linkedin', 'urn:x', project_id=PID, campaign_id='camp-none') is None
    assert _desk.list_reads() == []


# -- boundary 3: the paid engagement read ---------------------------------------------

def _presence(budget=10.0):
    _desk.upsert_presence(PID, {'accounts': [{'platform': 'x', 'voice': 'personal'}],
                                'budget': {'amount': budget, 'period': 'month', 'per_job': 0, 'kinds': []}})


def _owned_post(cid, ext, published='2026-09-28T10:00:00Z'):
    return _desk.record_published(platform='x', voice='personal', body='hello', project_id=PID,
                                  campaign_id=cid, url=f'https://x.com/ron/status/{ext}',
                                  cost=0, published_at=published)


def _metrics(*ids):
    return [{'id': i, 'public_metrics': {'impression_count': 5}} for i in ids]


def test_a_campaign_with_room_pays_for_its_metrics_batch_and_the_row_is_tagged(store):
    _presence(budget=0)             # the project cap would refuse: the campaign pays instead
    cid = _camp(amount=0.05)
    _owned_post(cid, '111')
    fake = FakeX(metrics=_metrics('111'))
    rep = eng.poll_project(PID, readers={'x': eng.XReader(token='t', transport=fake)}, now=NOW)
    reads = [r for r in _desk.list_reads(project_id=PID) if r['kind'] == 'metrics']
    assert [(r['cost'], r['campaign_id'], r['ok']) for r in reads] == [(READ, cid, True)]
    assert rep['platforms']['x']['metrics_written'] == 1
    assert guard.campaign_budget(cid)['remaining'] == pytest.approx(0.05 - READ)


def test_a_campaign_out_of_budget_refuses_the_batch_with_a_reason(store):
    _presence(budget=10)
    cid = _camp(amount=0.004, title='Launch week')
    _owned_post(cid, '111')
    fake = FakeX(metrics=_metrics('111'))
    rep = eng.poll_project(PID, readers={'x': eng.XReader(token='t', transport=fake)}, now=NOW)
    x = rep['platforms']['x']
    assert not [c for c in fake.calls if c[0].endswith('/tweets')]            # nothing paid was sent
    assert x['budget_blocked'] is True and '"Launch week"' in x['error']
    assert '"Launch week"' in _desk.get_read_coverage(PID)['x']['last_error']
    failed = [r for r in _desk.list_reads(project_id=PID) if not r['ok']]
    assert failed and failed[0]['campaign_id'] == cid and failed[0]['cost'] == 0


def test_batches_are_split_per_campaign_and_one_refusal_does_not_stop_the_other(store):
    _presence(budget=10)
    poor = _camp('camp-poor', amount=0.0, source='none', title='Poor')
    rich = _camp('camp-rich', amount=1.0, title='Rich')
    _owned_post(poor, '111')
    _owned_post(rich, '222')
    fake = FakeX(metrics=_metrics('222'))
    rep = eng.poll_project(PID, readers={'x': eng.XReader(token='t', transport=fake)}, now=NOW)
    asked = [c[1].get('ids') for c in fake.calls if c[0].endswith('/tweets')]
    assert asked == ['222']
    assert rep['platforms']['x']['metrics_written'] == 1
    assert guard.campaign_budget(rich)['reads'] == pytest.approx(READ)
    assert guard.campaign_budget(poor)['reads'] == 0


def test_a_failed_paid_read_gives_its_hold_back(store):
    _presence(budget=10)
    cid = _camp(amount=READ)
    _owned_post(cid, '111')

    def down(url, params, token):
        if url.endswith('/tweets'):
            raise eng.ReadError('X API HTTP 503')
        return FakeX()(url, params, token)
    eng.poll_project(PID, readers={'x': eng.XReader(token='t', transport=down)}, now=NOW)
    assert guard.campaign_budget(cid)['reserved'] == 0
    assert guard.campaign_budget(cid)['remaining'] == pytest.approx(READ)


def test_a_post_of_a_deleted_campaign_stays_on_the_project_cap(store):
    _presence(budget=10)
    _owned_post('camp-deleted', '111')
    fake = FakeX(metrics=_metrics('111'))
    eng.poll_project(PID, readers={'x': eng.XReader(token='t', transport=fake)}, now=NOW)
    reads = [r for r in _desk.list_reads(project_id=PID) if r['kind'] == 'metrics']
    assert [(r['ok'], 'campaign_id' in r) for r in reads] == [(True, False)]


def test_a_pane_read_is_free_and_never_reaches_the_guard(store, monkeypatch):
    _presence(budget=0)
    cid = _camp(source='none')
    _owned_post(cid, '111')

    class Pane(eng.Reader):
        platform, via, unit_cost = 'x', 'pane', 0.0
        mentions_units = 1

        def capability(self):
            return {'connected': True, 'reason': None, 'short': None}

        def fetch_mentions(self, *, since_id, known_posts):
            return {'items': [], 'resources': 0, 'cursor': None, 'account': None}

        def fetch_metrics(self, ids):
            return {'resources': 0, 'metrics': {i: {'impressions': 3} for i in ids}, 'unavailable': {}}
    monkeypatch.setattr(guard, 'reserve', lambda *a, **k: pytest.fail('a free read reached the guard'))
    rep = eng.poll_project(PID, readers={'x': Pane()}, now=NOW)
    assert rep['platforms']['x']['metrics_written'] == 1
    assert not rep['platforms']['x'].get('budget_blocked')
