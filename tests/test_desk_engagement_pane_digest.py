"""The Desk's generic pane reader (mc/desk_engagement_pane_digest.py, backlog 44712cf4 p2).

SYNTHETIC ONLY. No test here opens a browser, a network connection or a model: the page
reader is a fake behind the `read(url)` / `links()` / `close()` interface of
`_GuardedPages`, and the toolless model is a fake `runtime.oneshot`. The page texts and
model replies are hand-written, not recorded from a live site, so these pin the
validation and the control flow, not what LinkedIn / YouTube / any site really renders.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import browser_agent_read as policy  # noqa: E402
from mc import desk as _desk  # noqa: E402
from mc import desk_engagement as eng  # noqa: E402
from mc import desk_pane_pages_store as store_mod  # noqa: E402
from mc import desk_engagement_pane_digest as pd  # noqa: E402

PID = 'clayrune'
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
LI_NOTES = 'https://www.linkedin.com/notifications/'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setattr(_desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(_desk, 'SIGNALS_PATH', tmp_path / 'sig.jsonl')
    return tmp_path


def _allow(domains, profile='main', enabled=True):
    policy.set_policy(profile, enabled, domains)


def _body(text, url, title='Page'):
    return {'ok': True, 'url': url, 'final_url': url, 'title': title,
            'content': {'origin_url': url, 'warning': 'untrusted', 'text': text}}


class FakePages:
    """Stands in for `_GuardedPages`. `pages` maps url -> body (or callable); `links_for`
    maps url -> the anchors `links()` returns after reading it."""

    def __init__(self, pages, links_for=None):
        self.pages, self.links_for = pages, links_for or {}
        self.reads, self.closed, self._last = [], 0, None

    def read(self, url):
        self.reads.append(url)
        self._last = url
        page = self.pages.get(url)
        if page is None:
            return {'ok': False, 'error': 'navigation_failed', 'detail': f'no such page {url}'}
        return page(url) if callable(page) else page

    def links(self):
        return self.links_for.get(self._last, [])

    def close(self):
        self.closed += 1


class FakeRuntime:
    """`oneshot` answers by which instruction was sent; each value is a reply or a list of them."""

    def __init__(self, replies):
        self.replies = {k: list(v) if isinstance(v, list) else [v] for k, v in replies.items()}
        self.calls = []

    def oneshot(self, *, prompt, model, stdin_text, timeout):
        self.calls.append((prompt, stdin_text))
        queue = self.replies[prompt]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return SimpleNamespace(text=reply if isinstance(reply, str) else json.dumps(reply))


def _row(**kw):
    return {'kind': 'comment', 'author': 'Kat Lee', 'snippet': 'Does restore include memory?',
            'post': 'Restore points', 'when': '2h', 'id': '', **kw}


def _activity(*rows, page='activity', suspicious=False):
    return {'page': page, 'suspicious': suspicious, 'rows': list(rows)}


def _reader(pages, runtime, *, platform='linkedin', acc=None, profile='main', now=NOW):
    acc = {'platform': platform, 'browser_profile': profile, **(acc or {})}
    return pd.PaneDigestReader(PID, platform, acc, pages_factory=lambda _p, _n, _d: pages,
                               profile_exists=lambda _n: True, runtime=runtime, now=lambda: NOW if now is None else now)


NOTES_TEXT = 'Notifications\nKat Lee commented on your post\nDoes restore include memory?'


def _li_pages(**extra):
    return FakePages({LI_NOTES: _body(NOTES_TEXT, LI_NOTES, 'Notifications | LinkedIn'), **extra})


# -- activity: valid, malformed, signed out ---------------------------------------------

def test_valid_rows_become_items_at_one_call_per_page(env):
    _allow(['linkedin.com'])
    pages = _li_pages()
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(_row(), _row(kind='mention', author='Sam', snippet='nice', id='urn:li:comment:7'))})
    rd = _reader(pages, rt)
    got = rd.fetch_mentions(since_id=None, known_posts={})
    assert pages.reads == [LI_NOTES]
    assert got['resources'] == 1 and len(rt.calls) == 1
    assert [i['kind'] for i in got['items']] == ['comment', 'mention']
    assert got['items'][1]['external_id'] == 'id:urn:li:comment:7'
    assert got['items'][0]['external_id'].startswith('pane:')
    assert all(i['created_at'] is None and i['url'] is None and i['post_id'] is None for i in got['items'])
    assert got['items'][0]['post_ref'] == 'Restore points' and got['items'][0]['when'] == '2h'
    # the page text, marked untrusted, is what the model was shown
    assert 'untrusted' in rt.calls[0][1] and 'Does restore include memory?' in rt.calls[0][1]


def test_malformed_rows_are_dropped_and_counted(env):
    _allow(['linkedin.com'])
    bad = [
        {**_row(), 'extra': 'x'},                   # extra field
        {k: v for k, v in _row().items() if k != 'when'},   # missing field
        _row(kind='like'),                           # kind outside the schema
        _row(author=7),                              # non-string
        _row(snippet=''),                            # nothing said
        _row(snippet='x' * 300),                     # over the cap
        _row(id='a b'),                              # id not an identifier
        'a string',
    ]
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(_row(), *bad)})
    got = _reader(_li_pages(), rt).fetch_mentions(since_id=None, known_posts={})
    assert len(got['items']) == 1 and got['rejected'] == len(bad)


def test_rows_not_a_list_or_all_bad_is_a_gap_not_an_empty_feed(env):
    _allow(['linkedin.com'])
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: {'page': 'activity', 'suspicious': False, 'rows': 'none'},
                      pd.DISCOVER_INSTRUCTION: {'page': 'start', 'suspicious': False, 'activity': '', 'own_posts': ''}})
    pages = FakePages({LI_NOTES: _body(NOTES_TEXT, LI_NOTES), 'https://www.linkedin.com/': _body('Home', 'https://www.linkedin.com/')})
    with pytest.raises(pd.PagesNeeded):
        _reader(pages, rt).fetch_mentions(since_id=None, known_posts={})


def test_quiet_activity_page_is_zero_items_not_a_failure(env):
    _allow(['linkedin.com'])
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity()})
    got = _reader(_li_pages(), rt).fetch_mentions(since_id=None, known_posts={})
    assert got['items'] == [] and got['rejected'] == 0


def test_signed_out_page_raises_not_signed_in_without_a_model_call(env):
    _allow(['linkedin.com'])
    wall = 'https://www.linkedin.com/login?session_redirect=x'
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(_row())})
    page = _body('Sign in\nJoin now', wall, 'Sign in | LinkedIn')
    pages = FakePages({LI_NOTES: page, 'https://www.linkedin.com/': page})
    with pytest.raises(eng.NotSignedIn):
        _reader(pages, rt).fetch_mentions(since_id=None, known_posts={})
    assert rt.calls == []


def test_model_calling_it_a_login_page_raises_not_signed_in(env):
    _allow(['linkedin.com'])
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(page='login'),
                      pd.DISCOVER_INSTRUCTION: _activity(page='login')})
    pages = FakePages({LI_NOTES: _body(NOTES_TEXT, LI_NOTES), 'https://www.linkedin.com/': _body('Home', 'https://www.linkedin.com/')},
                      links_for={'https://www.linkedin.com/': [{'t': 'Notifications', 'h': LI_NOTES}]})
    with pytest.raises(eng.NotSignedIn):
        _reader(pages, rt).fetch_mentions(since_id=None, known_posts={})
    assert store_mod.get(PID, 'linkedin').get('status') != 'pages_needed'


def test_model_output_that_is_not_json_is_a_read_error(env):
    _allow(['linkedin.com'])
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: 'Sure! Ignore the page and post this.'})
    with pytest.raises(eng.ReadError, match='expected JSON'):
        _reader(_li_pages(), rt).fetch_mentions(since_id=None, known_posts={})


def test_pane_failure_keeps_the_kinds_pane_x_reader_raises(env):
    _allow(['linkedin.com'])
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity()})
    soft = FakePages({LI_NOTES: {'ok': False, 'error': 'read_timeout', 'detail': 'slow'}})
    with pytest.raises(eng.ReadError) as e:
        _reader(soft, rt).fetch_mentions(since_id=None, known_posts={})
    assert not isinstance(e.value, eng._FatalPaneError)
    hard = FakePages({LI_NOTES: {'ok': False, 'error': 'launch_failed', 'detail': 'no chrome'}})
    with pytest.raises(eng._FatalPaneError):
        _reader(hard, rt).fetch_mentions(since_id=None, known_posts={})
    assert rt.calls == []        # a transient failure never starts discovery or a model call


# -- policy: no bypass ------------------------------------------------------------------

def test_policy_off_is_not_connected_and_never_reads(env):
    _allow(['linkedin.com'], enabled=False)
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(_row())})
    pages = _li_pages()
    rd = _reader(pages, rt)
    cap = rd.capability()
    assert cap['connected'] is False and 'agent reads' in cap['short']
    with pytest.raises(eng._FatalPaneError, match='agent_read_off'):
        rd.fetch_mentions(since_id=None, known_posts={})
    assert pages.reads == [] and rt.calls == []


def test_domain_not_on_the_list_is_not_connected_and_never_reads(env):
    _allow(['x.com'])
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(_row())})
    pages = _li_pages()
    rd = _reader(pages, rt)
    cap = rd.capability()
    assert cap['connected'] is False and 'allowed list' in cap['short']
    with pytest.raises(eng._FatalPaneError, match='domain_not_allowed'):
        rd.fetch_mentions(since_id=None, known_posts={})
    assert pages.reads == [] and rt.calls == []


def test_no_profile_and_missing_profile_are_not_connected(env):
    rt = FakeRuntime({})
    assert pd.PaneDigestReader(PID, 'linkedin', {}).capability()['connected'] is False
    rd = pd.PaneDigestReader(PID, 'linkedin', {'browser_profile': 'ghost'}, profile_exists=lambda _n: False)
    assert 'no saved browser profile' in rd.capability()['reason']
    assert rt.calls == []


# -- discovery --------------------------------------------------------------------------

YT = 'https://www.youtube.com/'
YT_INBOX = 'https://studio.youtube.com/channel/UC1/comments'
YT_LINKS = [{'t': 'Comments', 'h': YT_INBOX}, {'t': 'Your channel', 'h': 'https://www.youtube.com/@clayrune'},
            {'t': 'Terms', 'h': 'https://evil.example.net/steal'}]


def _yt(rt_pick, *, activity_reply=None, links=None, replies_extra=None):
    _allow(['youtube.com'])
    pages = FakePages({YT: _body('Home\nYour channel\nComments', YT),
                       YT_INBOX: _body('Comments\nKat: great video', YT_INBOX)},
                      links_for={YT: YT_LINKS if links is None else links})
    replies = {pd.DISCOVER_INSTRUCTION: {'page': 'start', 'suspicious': False, **rt_pick},
               pd.ACTIVITY_INSTRUCTION: activity_reply or _activity(_row(kind='comment', snippet='great video'))}
    replies.update(replies_extra or {})
    rt = FakeRuntime(replies)
    return _reader(pages, rt, platform='youtube'), pages, rt


def test_discovery_finds_saves_and_reuses_the_page(env):
    rd, pages, rt = _yt({'activity': YT_INBOX, 'own_posts': 'https://www.youtube.com/@clayrune'})
    got = rd.fetch_mentions(since_id=None, known_posts={})
    assert [i['excerpt'] for i in got['items']] == ['great video']
    assert pages.reads == [YT, YT_INBOX]
    assert got['resources'] == 2                      # the pick and the check read
    rec = store_mod.get(PID, 'youtube')
    assert rec['status'] == 'ok' and rec['last_discovery_at'] == '2026-10-06T12:00:00Z'
    assert [(p['role'], p['url'], p['verified']) for p in rec['pages']] == [
        ('activity', YT_INBOX, True), ('own_posts', 'https://www.youtube.com/@clayrune', False)]
    # the model was shown only same-site links: the off-site one never reached it
    assert 'evil.example.net' not in rt.calls[0][1] and YT_INBOX in rt.calls[0][1]
    # next poll: no discovery, one call
    pages.reads.clear()
    again = _reader(pages, rt, platform='youtube').fetch_mentions(since_id=None, known_posts={})
    assert pages.reads == [YT_INBOX] and again['resources'] == 1
    assert rd.pages_state()['source'] == 'discovered'


def test_a_pick_that_was_not_offered_is_rejected(env):
    rd, pages, _rt = _yt({'activity': 'https://studio.youtube.com/channel/UC1/invented', 'own_posts': ''})
    with pytest.raises(pd.PagesNeeded):
        rd.fetch_mentions(since_id=None, known_posts={})
    assert pages.reads == [YT]                        # the invented page was never opened
    rec = store_mod.get(PID, 'youtube')
    assert rec['status'] == 'pages_needed' and not rec.get('pages')
    assert rd.pages_state()['status'] == 'pages_needed'


def test_an_off_domain_link_is_rejected_even_when_offered_by_the_page(env):
    rd, pages, _rt = _yt({'activity': 'https://evil.example.net/steal', 'own_posts': ''})
    with pytest.raises(pd.PagesNeeded):
        rd.fetch_mentions(since_id=None, known_posts={})
    assert pages.reads == [YT]


def test_a_same_site_link_off_the_allow_list_is_rejected(env):
    _allow(['youtube.com'])
    offered = pd.PaneDigestReader._offer_links(
        [{'t': 'Studio', 'h': 'https://studio.youtube.com/x'}, {'t': 'Other', 'h': 'https://youtube.org/x'},
         {'t': 'Insecure', 'h': 'http://www.youtube.com/y'}, {'t': 'Dup', 'h': 'https://studio.youtube.com/x#a'}],
        YT, ['studio.youtube.com'])
    assert offered == [('Studio', 'https://studio.youtube.com/x')]
    assert pd.PaneDigestReader._valid_pick('https://www.youtube.com/y', {'https://www.youtube.com/y'}, YT,
                                           ['studio.youtube.com']) is None


def test_nothing_found_flags_pages_needed_and_rediscovery_waits_a_day(env):
    rd, pages, rt = _yt({'activity': '', 'own_posts': ''})
    with pytest.raises(pd.PagesNeeded, match='pages needed'):
        rd.fetch_mentions(since_id=None, known_posts={})
    assert store_mod.get(PID, 'youtube')['status'] == 'pages_needed'
    n_calls = len(rt.calls)
    with pytest.raises(pd.PagesNeeded):
        _reader(pages, rt, platform='youtube').fetch_mentions(since_id=None, known_posts={})
    assert len(rt.calls) == n_calls                   # the same day: no second discovery, no model call
    later = _reader(pages, rt, platform='youtube', now=NOW + timedelta(days=1, minutes=1))
    later._now = lambda: NOW + timedelta(days=1, minutes=1)
    with pytest.raises(pd.PagesNeeded):
        later.fetch_mentions(since_id=None, known_posts={})
    assert len(rt.calls) == n_calls + 1               # a day on, it tries again


def test_a_chosen_page_that_does_not_hold_up_is_not_saved(env):
    rd, _pages, _rt = _yt({'activity': YT_INBOX, 'own_posts': ''},
                          activity_reply=_activity(page='other'))
    with pytest.raises(pd.PagesNeeded, match='did not hold up'):
        rd.fetch_mentions(since_id=None, known_posts={})
    assert not store_mod.get(PID, 'youtube').get('pages')


def test_signed_out_start_page_is_not_a_pages_needed_flag(env):
    _allow(['youtube.com'])
    pages = FakePages({YT: _body('Sign in', 'https://www.youtube.com/signin?x=1', 'Sign in')})
    rd = _reader(pages, FakeRuntime({}), platform='youtube')
    with pytest.raises(eng.NotSignedIn):
        rd.fetch_mentions(since_id=None, known_posts={})
    assert store_mod.get(PID, 'youtube').get('status') != 'pages_needed'


def test_saved_page_gone_stale_rediscovers_once_per_day(env):
    _allow(['linkedin.com'])
    store_mod.update(PID, 'linkedin', pages=[{'role': 'activity', 'url': 'https://www.linkedin.com/old/',
                                              'source': 'discovered', 'found_at': 'x', 'verified': True}],
                     status='ok', last_discovery_at='2026-10-01T00:00:00Z')
    new = 'https://www.linkedin.com/notifications/'
    pages = FakePages({'https://www.linkedin.com/old/': _body('404', 'https://www.linkedin.com/old/'),
                       'https://www.linkedin.com/': _body('Home', 'https://www.linkedin.com/'),
                       new: _body(NOTES_TEXT, new)},
                      links_for={'https://www.linkedin.com/': [{'t': 'Notifications', 'h': new}]})
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: [_activity(page='other'), _activity(_row())],
                      pd.DISCOVER_INSTRUCTION: {'page': 'start', 'suspicious': False, 'activity': new, 'own_posts': ''}})
    got = _reader(pages, rt).fetch_mentions(since_id=None, known_posts={})
    assert len(got['items']) == 1 and got['resources'] == 3     # failed read, the pick, its check
    assert store_mod.get(PID, 'linkedin')['pages'][0]['url'] == new


def test_user_pages_are_authoritative_and_never_rediscovered(env):
    _allow(['example.org'])
    url = 'https://forum.example.org/inbox'
    pages = FakePages({url: _body('Inbox', url)})
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(page='other')})
    rd = _reader(pages, rt, platform='forum', acc={'url': 'https://forum.example.org/',
                                                   'read_pages': [{'role': 'activity', 'url': url}]})
    with pytest.raises(eng.ReadError, match='activity page'):
        rd.fetch_mentions(since_id=None, known_posts={})
    assert pages.reads == [url] and store_mod.get(PID, 'forum') == {}


def test_unknown_site_discovers_from_its_account_url(env):
    _allow(['example.org'])
    start, inbox = 'https://forum.example.org/', 'https://forum.example.org/messages'
    pages = FakePages({start: _body('Welcome back', start), inbox: _body('Messages\nSam: hi', inbox)},
                      links_for={start: [{'t': 'Messages', 'h': inbox}]})
    rt = FakeRuntime({pd.DISCOVER_INSTRUCTION: {'page': 'start', 'suspicious': False, 'activity': inbox, 'own_posts': ''},
                      pd.ACTIVITY_INSTRUCTION: _activity(_row(kind='message', author='Sam', snippet='hi'))})
    rd = _reader(pages, rt, platform='forum', acc={'url': start})
    got = rd.fetch_mentions(since_id=None, known_posts={})
    assert got['items'][0]['kind'] == 'message' and pages.reads == [start, inbox]
    assert rd.capability()['connected'] is True


# -- post counters ----------------------------------------------------------------------

POST = 'https://www.youtube.com/watch?v=abc123'


def test_post_counters_exact_ints_only(env):
    _allow(['youtube.com'])
    pages = FakePages({POST: _body('Video\n1,234 views', POST)})
    counts = {'impressions': None, 'views': 1234, 'likes': '12', 'replies': 3, 'reposts': True,
              'quotes': 1.5, 'bookmarks': -2, 'shares': None, 'clicks': 10 ** 12, 'bogus': 5}
    rt = FakeRuntime({pd.POST_INSTRUCTION: {'page': 'post', 'suspicious': False, 'counts': counts}})
    res = _reader(pages, rt, platform='youtube').fetch_metrics([POST])
    assert res['metrics'] == {POST: {'views': 1234, 'replies': 3}}
    assert res['resources'] == 1 and res['unavailable'] == {}


def test_post_page_that_is_not_a_post_or_shows_nothing_is_unavailable_not_zero(env):
    _allow(['youtube.com'])
    other = 'https://www.youtube.com/watch?v=zzz'
    pages = FakePages({POST: _body('x', POST), other: _body('x', other), 'https://www.youtube.com/watch?v=q': _body('x', 'https://www.youtube.com/')})
    rt = FakeRuntime({pd.POST_INSTRUCTION: [{'page': 'other', 'suspicious': False, 'counts': {}},
                                            {'page': 'post', 'suspicious': False, 'counts': {'views': None}}]})
    res = _reader(pages, rt, platform='youtube').fetch_metrics([POST, other, 'https://www.youtube.com/watch?v=q'])
    assert res['metrics'] == {} and set(res['unavailable']) == {POST, other, 'https://www.youtube.com/watch?v=q'}
    assert res['resources'] == 2                      # the redirected-to-home page was not sent to the model


def test_post_external_id_follows_the_platform_prefixes_and_the_account(env):
    yt = pd.PaneDigestReader(PID, 'youtube', {})
    assert yt.post_external_id({'url': POST}) == POST
    assert yt.post_external_id({'url': 'https://youtu.be/abc'}) == 'https://youtu.be/abc'
    assert yt.post_external_id({'url': 'https://x.com/ron/status/1'}) is None
    assert yt.post_external_id({'url': None}) is None
    own = pd.PaneDigestReader(PID, 'forum', {'read_pages': [{'role': 'post', 'url': 'https://forum.example.org/t/'}]})
    assert own.post_external_id({'url': 'https://forum.example.org/t/9'}) == 'https://forum.example.org/t/9'


# -- wiring and the budget --------------------------------------------------------------

def _project(accounts, budget=10.0):
    _desk.upsert_presence(PID, {'accounts': accounts,
                                'budget': {'amount': budget, 'period': 'month', 'per_job': 0, 'kinds': []}})


def test_readers_for_project_uses_the_generic_reader_for_pane_accounts(env):
    _project([{'channel_id': 'x1', 'platform': 'x', 'browser_profile': 'main'},
              {'channel_id': 'l1', 'platform': 'linkedin', 'browser_profile': 'main'},
              {'channel_id': 'y1', 'platform': 'youtube', 'browser_profile': 'main'},
              {'channel_id': 'l2', 'platform': 'tiktok', 'read_via': 'api'}])
    rd = eng.readers_for_project(PID)
    assert isinstance(rd['x'], eng.PaneXReader)                       # untouched
    assert isinstance(rd['linkedin'], pd.PaneDigestReader) and rd['linkedin'].via == 'pane'
    assert isinstance(rd['youtube'], pd.PaneDigestReader)
    assert 'tiktok' not in rd                                         # api route has no generic reader


def test_poll_counts_each_model_call_against_the_read_budget(env):
    _allow(['linkedin.com'])
    _project([{'channel_id': 'l1', 'platform': 'linkedin', 'browser_profile': 'main'}])
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(_row())})
    rd = _reader(_li_pages(), rt)
    rep = eng.poll_project(PID, readers={'linkedin': rd})
    entry = rep['platforms']['linkedin']
    assert entry['new_items'] == 1 and entry['spent'] == pytest.approx(pd.PAGE_READ_UNIT_COST)
    assert [r['resources'] for r in _desk.list_reads(project_id=PID)] == [1]


def test_poll_refuses_when_the_worst_case_does_not_fit_the_budget(env):
    _allow(['linkedin.com'])
    _project([{'channel_id': 'l1', 'platform': 'linkedin', 'browser_profile': 'main'}], budget=0.01)
    rt = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(_row())})
    rd = _reader(_li_pages(), rt)
    rep = eng.poll_project(PID, readers={'linkedin': rd})
    assert rep['platforms']['linkedin'].get('budget_blocked') is True and rt.calls == []


def test_pages_state_is_exposed_in_coverage(env):
    _allow(['youtube.com'])
    rd, _pages, _rt = _yt({'activity': '', 'own_posts': ''})
    _project([{'channel_id': 'y1', 'platform': 'youtube', 'browser_profile': 'main'}])
    eng.poll_project(PID, readers={'youtube': rd})
    cov = eng.platform_coverage(PID, 'youtube', rd)
    assert cov['pages']['status'] == 'pages_needed' and cov['pages']['pages'] == []


def test_reader_closes_its_page_reader(env):
    _allow(['linkedin.com'])
    pages = _li_pages()
    rd = _reader(pages, FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(_row())}))
    rd.fetch_mentions(since_id=None, known_posts={})
    rd.close()
    rd.close()
    assert pages.closed == 1
