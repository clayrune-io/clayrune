"""One reader and one coverage row per ACCOUNT, not per platform (backlog 0ddcd44b).

A project that holds both a personal LinkedIn and the Clayrune Company Page used to read only
the first (`readers_for_project` keyed by platform). SYNTHETIC ONLY: fake page reader, fake
toolless model, no browser, no network (same fakes as test_desk_engagement_pane_digest).
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_engagement as eng  # noqa: E402
from mc import desk_engagement_pane_digest as pd  # noqa: E402
from mc import desk_engagement_targets as targets  # noqa: E402
from tests.test_desk_engagement_pane_digest import (  # noqa: E402
    LI_NOTES, FakePages, FakeRuntime, _activity, _allow, _body, _row, env)  # noqa: F401

PID = 'clayrune'
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
ADMIN = 'https://www.linkedin.com/company/777/admin/notifications/all/'
NOTES_TEXT = 'Notifications\nKat Lee commented on your post\nDoes restore include memory?'

MEMBER = {'channel_id': 'li-ron', 'platform': 'linkedin', 'browser_profile': 'main'}
COMPANY = {'channel_id': 'li-clayrune', 'platform': 'linkedin', 'browser_profile': 'main'}


def _project(accounts, account_fields=None):
    """Presence with `accounts`; `account_fields[channel_id]` lands on the workspace account record."""
    _desk.upsert_presence(PID, {
        'accounts': accounts, 'budget': {'amount': 10.0, 'period': 'month', 'per_job': 0, 'kinds': []}})
    with _desk._store_lock:
        data = _desk._read_store()
        for cid, fields in (account_fields or {}).items():
            data['accounts'][cid].update(fields)
        _desk._write_store(data)


COMPANY_FIELDS = {'li-clayrune': {'account_kind': 'organization', 'organization_id': '777', 'label': 'Clayrune',
                                  'read_pages': [{'role': 'activity', 'url': ADMIN}]},
                  'li-ron': {'account_kind': 'member', 'label': 'Ron'}}


# -- keys ----------------------------------------------------------------------------

def test_first_account_keeps_the_platform_key_and_later_ones_are_qualified(env):
    _project([COMPANY, MEMBER], COMPANY_FIELDS)
    got = [(t['key'], t['platform'], t['account_id']) for t in targets.read_targets(PID)]
    assert got == [('linkedin', 'linkedin', 'li-clayrune'), ('linkedin:li-ron', 'linkedin', 'li-ron')]


def test_single_account_and_published_only_platforms_keep_the_platform_key(env):
    _project([{'channel_id': 'x-ron', 'platform': 'x'}])
    _desk.record_published(platform='linkedin', voice='personal', body='hi', project_id=PID)
    got = [(t['key'], t['account_id']) for t in targets.read_targets(PID)]
    assert got == [('linkedin', None), ('x', 'x-ron')]        # published-only platform: one target, no account


def test_a_channel_listed_twice_is_one_target(env):
    _project([MEMBER, MEMBER])
    assert [t['key'] for t in targets.read_targets(PID)] == ['linkedin']


# -- two LinkedIn accounts: two readers, two coverage rows ----------------------------------

def test_two_linkedin_accounts_yield_two_readers_and_two_coverage_rows(env):
    _project([COMPANY, MEMBER], COMPANY_FIELDS)
    readers = eng.readers_for_project(PID)
    assert sorted(readers) == ['linkedin', 'linkedin:li-ron']
    assert all(isinstance(r, pd.PaneDigestReader) for r in readers.values())
    assert readers['linkedin']._company_page is True and readers['linkedin:li-ron']._company_page is False
    assert readers['linkedin']._account['channel_id'] == 'li-clayrune'
    assert readers['linkedin:li-ron']._account['channel_id'] == 'li-ron'

    bundle = eng.project_bundle(PID, readers=readers, now=NOW)
    rows = bundle['coverage']
    assert [(r['key'], r['account_id'], r['account_label']) for r in rows] == [
        ('linkedin', 'li-clayrune', 'Clayrune'), ('linkedin:li-ron', 'li-ron', 'Ron')]
    assert [g['account_id'] for g in bundle['gaps']] == ['li-clayrune', 'li-ron']


def test_each_account_is_polled_and_its_coverage_stored_separately(env, monkeypatch):
    _allow(['linkedin.com'])
    _project([COMPANY, MEMBER], COMPANY_FIELDS)
    readers = eng.readers_for_project(PID)
    reads = {}
    for key, url, who in (('linkedin', ADMIN, 'Company fan'), ('linkedin:li-ron', LI_NOTES, 'Member fan')):
        rd = readers[key]
        pages = FakePages({url: _body(NOTES_TEXT, url)})
        reads[key] = pages
        rd._factory = lambda _p, _n, _d, pages=pages: pages
        rd._profile_exists = lambda _n: True
        rd._runtime = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(_row(author=who))})

    rep = eng.poll_project(PID, readers=readers, now=NOW)

    assert sorted(rep['platforms']) == ['linkedin', 'linkedin:li-ron']
    assert rep['platforms']['linkedin']['new_items'] == 1 and rep['platforms']['linkedin:li-ron']['new_items'] == 1
    assert reads['linkedin'].reads == [ADMIN] and reads['linkedin:li-ron'].reads == [LI_NOTES]
    items = {i['author']: i['account_id'] for i in _desk.list_engagement_items(project_id=PID)}
    assert items == {'Company fan': 'li-clayrune', 'Member fan': 'li-ron'}
    cov = _desk.get_read_coverage(PID)
    assert cov['linkedin']['account_id'] == 'li-clayrune' and cov['linkedin']['last_ok_at']
    assert cov['linkedin:li-ron']['account_id'] == 'li-ron' and cov['linkedin:li-ron']['last_ok_at']
    assert [r['state'] for r in eng.project_bundle(PID, readers=readers, now=NOW)['coverage']] == ['ok', 'ok']


def test_one_failing_account_does_not_hide_the_other(env):
    _allow(['linkedin.com'])
    _project([COMPANY, MEMBER], COMPANY_FIELDS)
    readers = eng.readers_for_project(PID)
    member = readers['linkedin:li-ron']
    member._factory = lambda _p, _n, _d: FakePages({LI_NOTES: _body(NOTES_TEXT, LI_NOTES)})
    member._profile_exists = lambda _n: True
    member._runtime = FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(_row())})
    readers['linkedin']._profile_exists = lambda _n: False           # the Company Page is not signed in
    rep = eng.poll_project(PID, readers=readers, now=NOW)
    assert rep['platforms']['linkedin']['state'] == 'not_connected'
    assert rep['platforms']['linkedin:li-ron']['state'] == 'ok'


# -- a record that belongs to another account is never shown as this one's -------------------

def test_coverage_stamped_for_another_account_reads_as_not_read_yet(env):
    _allow(['linkedin.com'])
    _project([MEMBER])
    _desk.set_read_coverage(PID, 'linkedin', ok=True, via='pane', account_id='li-someone-else')
    rd = eng.readers_for_project(PID)['linkedin']
    rd._profile_exists = lambda _n: True
    row = eng.project_bundle(PID, readers={'linkedin': rd}, now=NOW)['coverage'][0]
    assert row['state'] == 'not_read_yet' and row['last_ok_at'] is None


def test_writing_over_another_accounts_record_starts_it_afresh(env):
    _desk.set_read_coverage(PID, 'linkedin', ok=True, cursor='old', via='pane', account_id='li-a')
    rec = _desk.set_read_coverage(PID, 'linkedin', ok=False, error='boom', via='pane', account_id='li-b')
    assert rec['account_id'] == 'li-b' and rec['last_ok_at'] is None and rec['cursor'] is None


def test_an_unstamped_legacy_record_still_counts(env):
    _allow(['linkedin.com'])
    _project([MEMBER], {'li-ron': {'account_kind': 'member'}})
    _desk.set_read_coverage(PID, 'linkedin', ok=True, via='pane')               # written before targets
    rd = eng.readers_for_project(PID)['linkedin']
    rd._profile_exists = lambda _n: True
    assert eng.project_bundle(PID, readers={'linkedin': rd}, now=NOW)['coverage'][0]['state'] == 'ok'


# -- callers that pick "the account" -------------------------------------------------------

def test_platform_account_prefers_the_named_channel(env):
    _project([COMPANY, MEMBER], COMPANY_FIELDS)
    assert eng.platform_account(PID, 'linkedin')['channel_id'] == 'li-clayrune'
    assert eng.platform_account(PID, 'linkedin', 'li-ron')['channel_id'] == 'li-ron'
    assert eng.platform_account(PID, 'linkedin', 'nope')['channel_id'] == 'li-clayrune'   # unknown id: the first


# -- legacy kindless LinkedIn accounts ------------------------------------------------------

def _reader_for(acc):
    return pd.PaneDigestReader(PID, 'linkedin', {'platform': 'linkedin', 'browser_profile': 'main', **acc},
                               pages_factory=lambda *_: FakePages({LI_NOTES: _body(NOTES_TEXT, LI_NOTES)}),
                               profile_exists=lambda _n: True,
                               runtime=FakeRuntime({pd.ACTIVITY_INSTRUCTION: _activity(_row())}))


MEMBER_PAGES = {'read_pages': [{'role': 'activity', 'url': LI_NOTES}]}


def test_kindless_legacy_account_with_its_member_notifications_address_reads_as_a_member(env):
    _allow(['linkedin.com'])
    rd = _reader_for(MEMBER_PAGES)                        # no kind, no organization id, no setup
    assert rd._company_page is False
    assert rd.pages_state()['status'] != 'pages_needed'
    assert len(rd.fetch_mentions(since_id=None, known_posts={})['items']) == 1


def test_bare_kindless_account_stays_fail_closed(env):
    """Legacy accounts predate member support, so a kindless one with no address could be a
    Company Page: it is not read from the member's feed (pinned in test_desk_engagement_pane_digest)."""
    assert _reader_for({})._company_page is True


@pytest.mark.parametrize('acc', [
    {'organization_id': '777'},
    {'account_kind': 'organization'},
    {'read_pages': [{'role': 'activity', 'url': 'https://www.linkedin.com/company/777/admin'}]},
    {'read_pages': [{'role': 'activity', 'url': LI_NOTES},
                    {'role': 'activity', 'url': 'https://www.linkedin.com/company/777/admin'}]},
])
def test_kindless_account_that_shows_it_is_a_company_page_stays_one(env, acc):
    assert _reader_for(acc)._company_page is True


def test_a_recorded_kind_beats_the_legacy_default(env):
    assert _reader_for({'account_kind': 'member'})._company_page is False
    assert _reader_for({'account_kind': 'organization'})._company_page is True
