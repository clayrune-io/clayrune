"""The Desk publish tick and the version approval gate (MC-1021 R1-W S7).

Plan: docs/desk_v1/R1W_WIRING_PLAN.md §4. Pinned, in the order it would cost to
lose them:

  * NOTHING REACHES A NETWORK. An autouse fixture makes `urllib.request.urlopen`
    raise, and every transport function (`_post_tweet`, `_get_tweet`,
    `_post_linkedin`) is replaced by a recording fake. A test that posted for
    real would fail on that raise, not on an assertion.
  * M19 is a human act: the route refuses an unattended caller (403) and goes
    through the retyped-passcode gate; `approve_version` refuses a version whose
    campaign is not running, whose limits are not covered, whose account cannot
    publish, whose piece carries media, or that has no text; every reason is listed.
  * the tick sends only what is approved/scheduled AND still carries the human
    stamp for the exact text and account it has now; each ordered check holds the
    version WITH A REASON (nothing silently skips) and only a human approving
    again releases it;
  * exactly once: two passes, or a pass racing a click, post once; a `sending`
    left behind by a crash is completed from its receipt or becomes
    `unknown_outcome`, and is never sent again by the tick;
  * receipt -> submitted -> verified_published (X), `submitted` for LinkedIn
    (cannot be read back), one ledger row per post with its cost;
  * an unattended pass cannot use an attended-only vault token; Approve now can;
  * a manual account gets a publishing task (and an X share-intent link), no API
    call, and the person's "I posted it" is the only thing that completes it;
  * `start()` refuses without a wired store, importing starts nothing.
"""
import sys
import threading
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_accounts as _accounts  # noqa: E402
from mc import desk_pieces as _pieces  # noqa: E402
from mc import desk_publish as _publish  # noqa: E402
from mc import desk_tick as _tick  # noqa: E402
from mc import secrets_store  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
CID = 'camp-t'
NOW = datetime.now(timezone.utc)


class _Vault:
    """secrets_store stand-in: metadata for `publish_state`, values for `publish`."""

    def __init__(self):
        self.entries = {}
        self.reads = []

    def add(self, name, allow_unattended=True):
        self.entries[name] = {'name': name, 'allow_unattended': allow_unattended}

    def list_secrets(self, *a, **k):
        return list(self.entries.values())

    def is_readable(self, name):
        return name in self.entries

    def get_secret_value(self, name, *, consumer=None, project_id=None, unattended=False):
        self.reads.append((name, consumer, unattended))
        meta = self.entries.get(name)
        if meta is None:
            raise secrets_store.SecretsError(f'no secret {name}')
        if unattended and not meta['allow_unattended']:
            raise secrets_store.SecretsError(f'{name} is attended-only')
        return 'tok-' + name


class _Wire:
    """Records every transport call; a test sets what it should answer."""

    def __init__(self):
        self.posts = []        # (platform, body)
        self.fail_x = None     # an exception to raise from the X post
        self.fail_li = None
        self.x_found = True
        self.verifies = 0

    def post_tweet(self, token, body, in_reply_to=None):
        self.posts.append(('x', body))
        if self.fail_x:
            raise self.fail_x
        return {'data': {'id': f'x{len(self.posts)}', 'text': body}}

    def post_linkedin(self, token, org, body):
        self.posts.append(('linkedin', body))
        assert org == '777'
        if self.fail_li:
            raise self.fail_li
        return {'id': f'urn:li:share:{len(self.posts)}'}

    def get_tweet(self, token, post_id):
        self.verifies += 1
        return {'data': {'id': post_id}} if self.x_found else {'errors': [{'title': 'Not Found'}]}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError('a test reached the network')
    monkeypatch.setattr('urllib.request.urlopen', boom)


@pytest.fixture
def world(tmp_path, monkeypatch):
    vault = _Vault()
    wire = _Wire()
    vault.add('x.oauth-token')
    monkeypatch.setattr(secrets_store, 'list_secrets', vault.list_secrets)
    monkeypatch.setattr(secrets_store, 'is_readable', vault.is_readable)
    monkeypatch.setattr(secrets_store, 'get_secret_value', vault.get_secret_value)
    monkeypatch.setattr(_publish, 'RECEIPTS_PATH', tmp_path / 'desk_receipts.json')
    monkeypatch.setattr(_publish, '_post_tweet', wire.post_tweet)
    monkeypatch.setattr(_publish, '_get_username', lambda t: 'clayrune')
    monkeypatch.setattr(_publish, '_post_linkedin', wire.post_linkedin)
    monkeypatch.setattr(_publish, '_get_tweet', wire.get_tweet)
    caller = {'unattended': False}
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', lambda: caller['unattended'])
    passcodes = []
    monkeypatch.setattr(desk_routes, '_require_human_passcode',
                        lambda data: passcodes.append(data.get('passcode')) or None)
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=uploads,
    )
    app.register_blueprint(desk_routes.bp)
    w = SimpleNamespace(client=app.test_client(), vault=vault, wire=wire, caller=caller,
                        passcodes=passcodes, tmp=tmp_path)
    _accounts.create_account('x', '@clayrune', account_id='ch-x')
    _accounts.create_account('blog', 'clayrune.io/blog', account_id='ch-blog')
    _accounts.create_account('linkedin', 'Clayrune', account_id='ch-li')
    return w


def campaign(w, cid=CID, *, per_week=5, post_cap=10, end_date=None, accounts=('ch-x',), start=True, **extra):
    end = {'post_cap': post_cap} if end_date is None else {'date': end_date}
    plan = {'brief': 'b', 'title': 'T', 'accounts': list(accounts), 'cadence': {'per_week': per_week}, 'end': end}
    plan.update(extra.pop('plan', {}))
    r = w.client.post('/api/desk/campaigns?shape=v1', json=dict(
        {'id': cid, 'state': 'draft', 'projectId': 'alpha', 'rules': {},
         'map': {'stop': 'what', 'done': []}, 'plan': plan}, **extra))
    assert r.status_code == 201, r.get_json()
    if start:
        _desk.start_campaign(cid, today=NOW.date().isoformat())
    return cid


def version(w, *, account='ch-x', body='We shipped restore points.', piece='p1', vid='v1',
            cid=CID, review=True, **piece_kw):
    _pieces.create_piece(cid, piece_kw.pop('kind', 'post'), 'A post', piece_id=piece, **piece_kw)
    _pieces.add_version(piece, account, body=body, version_id=vid)
    if review:
        _pieces.update_version(piece, vid, {'state': 'needs_review'})
    return piece, vid


def stored(piece, vid):
    with _desk._store_lock:
        p = _desk._read_store()['pieces'][piece]
    return next(v for v in p['versions'] if v['id'] == vid)


def edit_store(fn):
    with _desk._store_lock:
        store = _desk._read_store()
        fn(store)
        _desk._write_store(store)


def ledger():
    return _desk.list_ledger(limit=1000)


def go(w, *a, now=NOW, **k):
    """Approve now (attended) and return the resulting version."""
    piece, vid = a[:2]
    _tick.approve_and_send(piece, vid, now=now, **k)
    return stored(piece, vid)


# -- M19: approve_version -----------------------------------------------------------

def test_approve_refuses_from_wrong_states(world):
    campaign(world)
    piece, vid = version(world, review=False)                       # drafting
    with pytest.raises(_pieces.PieceError) as e:
        _pieces.approve_version(piece, vid)
    assert e.value.status == 409 and 'drafting' in str(e.value)
    _pieces.update_version(piece, vid, {'state': 'needs_review'})
    _pieces.approve_version(piece, vid, scheduled_at=None)
    with pytest.raises(_pieces.PieceError, match='approved'):
        _pieces.approve_version(piece, vid)


def test_approve_lists_every_reason(world):
    campaign(world, start=False)                                      # a draft: not running
    world.vault.entries.clear()                                       # X cannot publish
    piece, vid = version(world, body='   ')
    with pytest.raises(_pieces.PieceError) as e:
        _pieces.approve_version(piece, vid)
    probs = e.value.problems
    assert e.value.status == 409
    assert any('not active' in p for p in probs)
    assert any('cannot publish' in p and 'no X API token' in p for p in probs)
    assert any('no text' in p for p in probs)


def test_approve_refuses_media_and_video_on_a_direct_account(world):
    campaign(world, accounts=('ch-x', 'ch-blog'))
    piece, vid = version(world, kind='video')
    with pytest.raises(_pieces.PieceError, match='text only'):
        _pieces.approve_version(piece, vid)
    p2, v2 = version(world, kind='video', account='ch-blog', piece='p2', vid='v2')
    _pieces.approve_version(p2, v2, scheduled_at=None)                  # by hand: allowed
    assert stored(p2, v2)['state'] == 'approved'


def test_approve_refuses_unsourced_claim_and_widened_campaign(world):
    campaign(world)
    piece, vid = version(world, claims=[{'id': 'c1', 'text': '40% faster'}])
    with pytest.raises(_pieces.PieceError, match='claim'):
        _pieces.approve_version(piece, vid)
    # widening a bound after the human approval voids it
    p2, v2 = version(world, piece='p2', vid='v2')
    edit_store(lambda s: s['campaigns'][CID]['plan']['cadence'].update(per_week=99))
    with pytest.raises(_pieces.PieceError, match='limits changed'):
        _pieces.approve_version(p2, v2)


def test_approve_stamps_the_exact_text_and_account(world):
    campaign(world)
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    v = stored(piece, vid)
    assert v['state'] == 'approved' and v['approved']['by'] == 'human'
    assert v['approved']['account_id'] == 'ch-x'
    assert v['approved']['body_sha'] == _pieces._body_sha('We shipped restore points.')


def test_future_time_is_scheduled_and_a_past_time_is_refused(world):
    campaign(world)
    piece, vid = version(world)
    when = (NOW + timedelta(hours=3)).isoformat()
    _pieces.approve_version(piece, vid, scheduled_at=when, now=NOW)
    assert stored(piece, vid)['state'] == 'scheduled' and stored(piece, vid)['scheduled_at'] == when
    p2, v2 = version(world, piece='p2', vid='v2')
    with pytest.raises(_pieces.PieceError, match='already passed'):
        _pieces.approve_version(p2, v2, scheduled_at=(NOW - timedelta(hours=2)).isoformat(), now=NOW)
    assert stored(p2, v2)['state'] == 'needs_review'


def test_the_plain_patch_still_cannot_write_a_send_state(world):
    campaign(world)
    piece, vid = version(world)
    for st in ('approved', 'scheduled', 'sending', 'submitted', 'verified_published', 'held'):
        with pytest.raises(_pieces.PieceError):
            _pieces.update_version(piece, vid, {'state': st})
    assert stored(piece, vid)['state'] == 'needs_review'


def test_transition_version_is_compare_and_set(world):
    campaign(world)
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    assert _pieces.transition_version(piece, vid, to='sending', expect=('approved', 'scheduled')) is not None
    assert _pieces.transition_version(piece, vid, to='sending', expect=('approved', 'scheduled')) is None
    with pytest.raises(_pieces.PieceError):
        _pieces.transition_version(piece, vid, to='needs_review', expect='sending')
    with pytest.raises(_pieces.PieceError):
        _pieces.transition_version(piece, vid, to='failed', expect='sending', body='x')
    # an illegal edge reads as "not in the expected state", never a write
    assert _pieces.transition_version(piece, vid, to='verified_published', expect='sending') is None
    assert stored(piece, vid)['state'] == 'sending'


# -- M19: the route ----------------------------------------------------------------------

def test_route_is_human_only_and_passcode_gated(world):
    campaign(world)
    piece, vid = version(world)
    url = f'/api/desk/pieces/{piece}/versions/{vid}/approve'
    world.caller['unattended'] = True
    r = world.client.post(url, json={'passcode': '1234'})
    assert r.status_code == 403 and 'needs a human' in r.get_json()['error']
    assert world.passcodes == [] and stored(piece, vid)['state'] == 'needs_review'
    assert world.wire.posts == []
    world.caller['unattended'] = False
    r = world.client.post(url, json={'passcode': '1234'})
    assert r.status_code == 200, r.get_json()
    assert world.passcodes == ['1234']                              # retyped per call
    assert world.wire.posts == [('x', 'We shipped restore points.')]
    ver = next(v for v in r.get_json()['versions'] if v['id'] == vid)
    assert ver['state'] == 'verified_published'


def test_route_refusal_carries_the_problem_list(world):
    campaign(world, start=False)
    piece, vid = version(world)
    r = world.client.post(f'/api/desk/pieces/{piece}/versions/{vid}/approve', json={})
    assert r.status_code == 409
    assert any('not active' in p for p in r.get_json()['problems'])
    assert world.wire.posts == []


def test_passcode_refusal_blocks_the_approval(world, monkeypatch):
    campaign(world)
    piece, vid = version(world)
    from flask import jsonify
    monkeypatch.setattr(desk_routes, '_require_human_passcode',
                        lambda data: (jsonify({'error': 'passcode_required'}), 403))
    r = world.client.post(f'/api/desk/pieces/{piece}/versions/{vid}/approve', json={})
    assert r.status_code == 403
    assert stored(piece, vid)['state'] == 'needs_review' and world.wire.posts == []


def test_route_future_time_schedules_and_does_not_send(world):
    campaign(world)
    piece, vid = version(world)
    when = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    r = world.client.post(f'/api/desk/pieces/{piece}/versions/{vid}/approve', json={'scheduled_at': when})
    assert r.status_code == 200
    assert stored(piece, vid)['state'] == 'scheduled' and world.wire.posts == []


def test_posted_route_is_human_only(world):
    campaign(world, accounts=('ch-blog',))
    piece, vid = version(world, account='ch-blog')
    go(world, piece, vid)
    url = f'/api/desk/pieces/{piece}/versions/{vid}/posted'
    world.caller['unattended'] = True
    assert world.client.post(url, json={}).status_code == 403
    assert stored(piece, vid)['state'] == 'approved'
    world.caller['unattended'] = False
    assert world.client.post(url, json={'url': 'https://clayrune.io/blog/a'}).status_code == 200
    assert stored(piece, vid)['state'] == 'you_reported'


# -- the send: X ---------------------------------------------------------------------------

def test_approve_now_posts_once_verifies_and_writes_the_ledger(world):
    campaign(world)
    piece, vid = version(world, body='Link post https://clayrune.io today')
    v = go(world, piece, vid)
    assert v['state'] == 'verified_published'
    assert world.wire.posts == [('x', 'Link post https://clayrune.io today')]
    assert v['receipt']['post_id'] == 'x1' and v['receipt']['permalink'] == 'https://x.com/clayrune/status/x1'
    assert v['receipt']['verified_at'] and world.wire.verifies == 1
    assert v['failure'] is None
    rows = ledger()
    assert len(rows) == 1
    r = rows[0]
    assert (r['platform'], r['campaign_id'], r['piece_id'], r['account'], r['term']) == ('x', CID, piece, 'ch-x', '1')
    assert r['url'] == v['receipt']['permalink'] and r['cost'] == _tick.X_LINK_POST_COST
    # a plain text post costs the plain rate
    p2, v2 = version(world, body='No link here', piece='p2', vid='v2')
    go(world, p2, v2)
    assert [x['cost'] for x in ledger() if x['piece_id'] == p2] == [_tick.X_POST_COST]


def test_a_second_pass_never_posts_again(world):
    campaign(world)
    piece, vid = version(world)
    go(world, piece, vid)
    for _ in range(3):
        out = _tick.run_once(NOW + timedelta(minutes=5))
        assert out['sent'] == [] and out['errors'] == []
    assert len(world.wire.posts) == 1 and len(ledger()) == 1


def test_scheduled_version_waits_for_its_time_then_sends_unattended(world):
    campaign(world)
    piece, vid = version(world)
    when = NOW + timedelta(hours=2)
    _pieces.approve_version(piece, vid, scheduled_at=when.isoformat(), now=NOW)
    assert _tick.run_once(NOW + timedelta(hours=1))['sent'] == []
    assert world.wire.posts == []
    out = _tick.run_once(when + timedelta(seconds=1))
    assert out['sent'] == [vid] and len(world.wire.posts) == 1
    assert stored(piece, vid)['state'] == 'verified_published'
    assert world.vault.reads[0] == ('x.oauth-token', 'desk_tick', True)       # an unattended caller


def test_two_passes_racing_one_version_post_once(world):
    campaign(world)
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    barrier = threading.Barrier(4)
    results = []

    def worker():
        barrier.wait()
        results.append(_tick.run_once(NOW))

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(world.wire.posts) == 1
    assert sum(len(r['sent']) for r in results) == 1 and len(ledger()) == 1


def test_attended_only_token_is_held_unattended_and_usable_by_approve_now(world):
    campaign(world)
    world.vault.entries['x.oauth-token']['allow_unattended'] = False
    piece, vid = version(world)
    when = NOW + timedelta(hours=1)
    _pieces.approve_version(piece, vid, scheduled_at=when.isoformat(), now=NOW)
    out = _tick.run_once(when + timedelta(minutes=1))
    assert out['held'] == [vid] and world.wire.posts == []
    v = stored(piece, vid)
    assert v['state'] == 'held' and 'attended-only' in v['failure']['reason'] and v['approved'] is None
    # Approve now is a person clicking: the same entry is usable
    # (the held version still carries its old time, so the click names `now` explicitly, as the button does)
    with pytest.raises(_pieces.PieceError, match='already passed'):
        go(world, piece, vid, now=when + timedelta(minutes=2))
    v = go(world, piece, vid, now=when + timedelta(minutes=2), scheduled_at=None)
    assert v['state'] == 'verified_published' and world.wire.posts == [('x', 'We shipped restore points.')]
    assert world.vault.reads[-1][2] is False


# -- the ordered checks: each one holds, with a reason ----------------------------------------

def _held(world, piece, vid, now=NOW, expect_in=''):
    out = _tick.run_once(now)
    assert out['held'] == [vid], out
    v = stored(piece, vid)
    assert v['state'] == 'held' and v['approved'] is None
    assert expect_in in v['failure']['reason'], v['failure']
    assert world.wire.posts == []
    return v


def test_text_edited_behind_the_approval_is_held(world):
    campaign(world)
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    edit_store(lambda s: s['pieces'][piece]['versions'][0].update(body='something else entirely'))
    _held(world, piece, vid, expect_in='text was changed')


def test_account_switched_behind_the_approval_is_held(world):
    campaign(world, accounts=('ch-x', 'ch-blog'))
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    edit_store(lambda s: s['pieces'][piece]['versions'][0].update(account_id='ch-blog'))
    _held(world, piece, vid, expect_in='account was changed')


def test_forged_or_missing_approval_stamp_is_held(world):
    campaign(world)
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    edit_store(lambda s: s['pieces'][piece]['versions'][0].update(approved=None))
    _held(world, piece, vid, expect_in='no human approval')
    p2, v2 = version(world, piece='p2', vid='v2')
    _pieces.approve_version(p2, v2, scheduled_at=None)
    edit_store(lambda s: s['pieces'][p2]['versions'][0]['approved'].update(by='agent'))
    out = _tick.run_once(NOW)
    assert out['held'] == [v2] and world.wire.posts == []


def test_a_paused_campaign_holds(world):
    campaign(world)
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    edit_store(lambda s: s['campaigns'][CID].update(state='paused'))
    _held(world, piece, vid, expect_in='not active')


def test_bounds_widened_after_approval_hold(world):
    campaign(world)
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    edit_store(lambda s: s['campaigns'][CID]['plan']['cadence'].update(per_week=50))
    _held(world, piece, vid, expect_in='limits are not covered')


def test_weekly_ceiling_holds_the_next_post(world):
    campaign(world, per_week=1)
    p1, v1 = version(world)
    assert go(world, p1, v1)['state'] == 'verified_published'
    p2, v2 = version(world, piece='p2', vid='v2')
    v = go(world, p2, v2, now=NOW + timedelta(days=2))
    assert v['state'] == 'held' and 'weekly ceiling' in v['failure']['reason']
    assert len(world.wire.posts) == 1
    # eight days on the first post has left the rolling window: a human approves again
    v = go(world, p2, v2, now=NOW + timedelta(days=8))
    assert v['state'] == 'verified_published' and len(world.wire.posts) == 2


def test_post_cap_holds(world):
    campaign(world, per_week=9, post_cap=1)
    p1, v1 = version(world)
    go(world, p1, v1)
    p2, v2 = version(world, piece='p2', vid='v2')
    v = go(world, p2, v2)
    assert v['state'] == 'held' and 'post cap' in v['failure']['reason'] and len(world.wire.posts) == 1


def test_minimum_gap_holds(world):
    campaign(world, per_week=9, plan={'cadence': {'per_week': 9, 'min_gap_h': 6}})
    p1, v1 = version(world)
    go(world, p1, v1)
    p2, v2 = version(world, piece='p2', vid='v2')
    v = go(world, p2, v2, now=NOW + timedelta(hours=1))
    assert v['state'] == 'held' and 'minimum gap' in v['failure']['reason']


def test_end_date_and_term_end_hold(world):
    campaign(world, end_date=(NOW + timedelta(days=30)).date().isoformat())
    piece, vid = version(world)
    v = go(world, piece, vid, now=NOW + timedelta(days=60))
    assert v['state'] == 'held'
    assert 'ended' in v['failure']['reason'] or 'end date' in v['failure']['reason']
    assert world.wire.posts == []


def test_account_that_stopped_being_ready_holds(world):
    campaign(world)
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    world.vault.entries.clear()
    _held(world, piece, vid, expect_in='cannot publish')


def test_held_is_released_only_by_a_human_approving_again(world):
    campaign(world)
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    edit_store(lambda s: s['campaigns'][CID].update(state='paused'))
    _held(world, piece, vid, expect_in='not active')
    edit_store(lambda s: s['campaigns'][CID].update(state='running'))
    for _ in range(3):                                              # fixing the cause is not enough
        assert _tick.run_once(NOW)['sent'] == []
    assert stored(piece, vid)['state'] == 'held' and world.wire.posts == []
    v = go(world, piece, vid)
    assert v['state'] == 'verified_published' and v['failure'] is None


# -- failure, unknown outcome, recovery ----------------------------------------------------------------

def test_http_failure_is_failed_with_the_reason_and_a_human_may_retry(world):
    campaign(world)
    piece, vid = version(world)
    err = urllib.error.HTTPError('u', 403, 'forbidden', {}, None)
    err.read = lambda: b'{"detail":"duplicate content"}'
    world.wire.fail_x = err
    v = go(world, piece, vid)
    assert v['state'] == 'failed' and 'HTTP 403' in v['failure']['reason'] and 'duplicate content' in v['failure']['reason']
    assert ledger() == [] and _publish.get_receipt(vid) is None
    assert _tick.run_once(NOW)['sent'] == [] and len(world.wire.posts) == 1      # the tick never retries
    world.wire.fail_x = None
    v = go(world, piece, vid)                                                  # the human's click is the retry
    assert v['state'] == 'verified_published' and len(world.wire.posts) == 2


def test_no_response_is_an_unknown_outcome_and_is_not_approvable(world):
    campaign(world)
    piece, vid = version(world)
    world.wire.fail_x = urllib.error.URLError('reset')
    v = go(world, piece, vid)
    assert v['state'] == 'unknown_outcome' and 'MAY' in v['failure']['reason']
    assert _tick.run_once(NOW)['sent'] == [] and len(world.wire.posts) == 1
    with pytest.raises(_pieces.PieceError, match='unknown_outcome'):
        _pieces.approve_version(piece, vid)
    # the person looks, finds it live, and says so
    _tick.report_posted(piece, vid, url='https://x.com/clayrune/status/9')
    v = stored(piece, vid)
    assert v['state'] == 'you_reported' and v['receipt']['reported'] is True
    assert [r['url'] for r in ledger()] == ['https://x.com/clayrune/status/9']


def test_unexpected_exception_after_sending_is_an_unknown_outcome(world, monkeypatch):
    campaign(world)
    piece, vid = version(world)

    def boom(*a, **k):
        raise RuntimeError('disk full')
    monkeypatch.setattr(_publish, 'publish', boom)
    v = go(world, piece, vid)
    assert v['state'] == 'unknown_outcome' and 'disk full' in v['failure']['reason']


def test_crash_leaving_sending_completes_from_the_receipt(world):
    campaign(world)
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    # the publisher wrote its receipt, the process died before `submitted` was written
    _publish.publish({'id': vid, 'platform': 'x', 'body': 'We shipped restore points.', 'campaign_id': CID},
                     project_id='alpha')
    _pieces.transition_version(piece, vid, to='sending', expect='approved')
    assert len(world.wire.posts) == 1
    out = _tick.run_once(NOW)
    assert out['recovered'] == [(vid, 'verified_published')]
    assert len(world.wire.posts) == 1 and len(ledger()) == 1
    assert _tick.run_once(NOW)['recovered'] == [] and len(ledger()) == 1


def test_crash_leaving_sending_without_a_receipt_is_never_sent_again(world):
    campaign(world)
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    _pieces.transition_version(piece, vid, to='sending', expect='approved')
    out = _tick.run_once(NOW)
    assert out['recovered'] == [(vid, 'unknown_outcome')]
    v = stored(piece, vid)
    assert v['state'] == 'unknown_outcome' and 'MAY have gone out' in v['failure']['reason']
    for _ in range(2):
        _tick.run_once(NOW)
    assert world.wire.posts == []


def test_one_bad_version_does_not_stop_the_pass(world, monkeypatch):
    campaign(world)
    p1, v1 = version(world)
    p2, v2 = version(world, piece='p2', vid='v2', body='second')
    _pieces.approve_version(p1, v1, scheduled_at=None)
    _pieces.approve_version(p2, v2, scheduled_at=None)
    real = _tick._process

    def flaky(pid, vid, **kw):
        if vid == v1:
            raise RuntimeError('boom')
        return real(pid, vid, **kw)
    monkeypatch.setattr(_tick, '_process', flaky)
    out = _tick.run_once(NOW)
    assert out['errors'] and out['sent'] == [v2]


# -- verification ----------------------------------------------------------------------------------------

def test_unconfirmed_post_stays_submitted_and_is_retried_then_given_up(world):
    campaign(world)
    piece, vid = version(world)
    world.wire.x_found = False
    v = go(world, piece, vid)
    assert v['state'] == 'submitted' and v['receipt']['verify_attempts'] == 1
    assert len(ledger()) == 1                                         # the post happened; only the proof is missing
    for _ in range(_tick.VERIFY_MAX_ATTEMPTS):
        _tick.run_once(NOW + timedelta(minutes=2))
    v = stored(piece, vid)
    assert v['state'] == 'submitted' and v['receipt']['verify'] == 'unconfirmed'
    n = world.wire.verifies
    _tick.run_once(NOW + timedelta(minutes=3))
    assert world.wire.verifies == n                                   # stopped paying for reads
    world.wire.x_found = True
    # a later pass cannot verify once given up; the post is not re-sent either
    assert len(world.wire.posts) == 1


def test_late_verification_promotes_to_verified_published(world):
    campaign(world)
    piece, vid = version(world)
    world.wire.x_found = False
    go(world, piece, vid)
    world.wire.x_found = True
    out = _tick.run_once(NOW + timedelta(minutes=1))
    assert out['verified'] == [vid] and stored(piece, vid)['state'] == 'verified_published'
    assert len(world.wire.posts) == 1


def test_verify_error_is_recorded_not_a_verdict(world, monkeypatch):
    campaign(world)
    piece, vid = version(world)

    def down(t, i):
        raise urllib.error.URLError('down')
    monkeypatch.setattr(_publish, '_get_tweet', down)
    v = go(world, piece, vid)
    assert v['state'] == 'submitted' and 'could not reach X' in v['receipt']['verify_error']


# -- LinkedIn --------------------------------------------------------------------------------------------------

def _li_ready(world, monkeypatch):
    monkeypatch.setattr(_accounts, 'LINKEDIN_ORG_POSTING_APPROVED', True)
    world.vault.add('linkedin.oauth-token')
    _accounts.update_account('ch-li', {'organization_id': '777'})


def test_linkedin_is_not_approvable_until_the_scope_is_approved(world):
    campaign(world, accounts=('ch-li',))
    world.vault.add('linkedin.oauth-token')
    piece, vid = version(world, account='ch-li')
    with pytest.raises(_pieces.PieceError, match='app review pending'):
        _pieces.approve_version(piece, vid)
    assert world.wire.posts == []


def test_linkedin_post_is_submitted_and_never_claims_verification(world, monkeypatch):
    _li_ready(world, monkeypatch)
    campaign(world, accounts=('ch-li',))
    piece, vid = version(world, account='ch-li', body='Company update')
    v = go(world, piece, vid)
    assert world.wire.posts == [('linkedin', 'Company update')]
    assert v['state'] == 'submitted'                                  # LinkedIn cannot be read back
    assert v['receipt']['permalink'].startswith('https://www.linkedin.com/feed/update/urn:li:share:')
    assert v['receipt']['verify'] == 'unsupported'
    assert [(r['platform'], r['cost']) for r in ledger()] == [('linkedin', 0.0)]
    assert _tick.run_once(NOW + timedelta(minutes=1))['sent'] == [] and len(world.wire.posts) == 1


def test_linkedin_without_an_organization_id_cannot_be_approved(world, monkeypatch):
    monkeypatch.setattr(_accounts, 'LINKEDIN_ORG_POSTING_APPROVED', True)
    world.vault.add('linkedin.oauth-token')
    campaign(world, accounts=('ch-li',))
    piece, vid = version(world, account='ch-li')
    with pytest.raises(_pieces.PieceError, match='organization id'):
        _pieces.approve_version(piece, vid)


# -- manual accounts ---------------------------------------------------------------------------------------------

def test_blog_gets_a_publishing_task_and_no_api_call(world):
    campaign(world, accounts=('ch-blog',))
    piece, vid = version(world, account='ch-blog', body='Long article text')
    v = go(world, piece, vid)
    assert v['state'] == 'approved' and v['manual']['copy_text'] == 'Long article text'
    assert v['manual']['share_url'] is None and v['manual']['account_id'] == 'ch-blog'
    for _ in range(2):
        _tick.run_once(NOW + timedelta(minutes=1))
    assert world.wire.posts == [] and world.vault.reads == []
    assert ledger() == []                                                # nothing is on the ledger until the person says
    _tick.report_posted(piece, vid, url='https://clayrune.io/blog/a')
    assert stored(piece, vid)['state'] == 'you_reported'
    assert [r['url'] for r in ledger()] == ['https://clayrune.io/blog/a']


def test_manual_x_account_gets_a_share_intent_link(world):
    _accounts.create_account('x', '@ron', account_id='ch-xm', capability='manual')
    campaign(world, accounts=('ch-xm',))
    piece, vid = version(world, account='ch-xm', body='Hello & welcome')
    v = go(world, piece, vid)
    assert v['manual']['share_url'] == 'https://x.com/intent/post?text=Hello%20%26%20welcome'
    assert world.wire.posts == []


def test_report_posted_refusals(world):
    campaign(world)
    piece, vid = version(world)
    with pytest.raises(_pieces.PieceError, match='needs_review'):
        _tick.report_posted(piece, vid)
    go(world, piece, vid)
    with pytest.raises(_pieces.PieceError, match='verified_published'):
        _tick.report_posted(piece, vid)
    c2 = campaign(world, 'camp-b', accounts=('ch-blog',))
    p2, v2 = version(world, account='ch-blog', piece='p2', vid='v2', cid=c2)
    go(world, p2, v2)
    with pytest.raises(_pieces.PieceError, match='http'):
        _tick.report_posted(p2, v2, url='javascript:alert(1)')
    assert stored(p2, v2)['state'] == 'approved'
    with pytest.raises(_pieces.PieceError, match='not found'):
        _tick.report_posted(p2, 'nope')


# -- the thread --------------------------------------------------------------------------------------------------------

def test_importing_the_tick_starts_nothing():
    assert _tick._thread is None


def test_start_refuses_without_a_wired_store(monkeypatch):
    monkeypatch.setattr(_desk, 'STORE_PATH', None)
    assert _tick.start() is None and _tick._thread is None
    monkeypatch.setattr(_desk, 'STORE_PATH', Path('x.json'))
    monkeypatch.setattr(_publish, 'RECEIPTS_PATH', None)
    assert _tick.start() is None and _tick._thread is None


def test_loop_honours_the_enabled_switch_and_survives_a_crash(world, monkeypatch):
    calls = []
    state = {'on': False}

    def fake_run_once():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('boom')
        return {k: [] for k in ('sent', 'held', 'failed', 'unknown', 'recovered', 'manual', 'errors')}
    monkeypatch.setattr(_tick, 'run_once', fake_run_once)
    th = _tick.start(interval_s=0.01, enabled=lambda: state['on'])
    try:
        assert th is not None and th.is_alive()
        assert _tick.start(interval_s=0.01) is th                      # one thread, not two
        threading.Event().wait(0.1)
        assert calls == []                                              # switched off: no pass
        state['on'] = True
        deadline = datetime.now() + timedelta(seconds=3)
        while len(calls) < 3 and datetime.now() < deadline:
            threading.Event().wait(0.02)
        assert len(calls) >= 3 and th.is_alive()                        # the crash did not end it
    finally:
        _tick.stop()
    assert _tick._thread is None
