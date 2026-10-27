"""MC-1114 part 1: missing Post consent is surfaced before Approve, never granted.

The refusal `permission_check.require_publish` raises at Approve used to be the
first the person heard of it. These pin the same question asked ahead of time:
the account row, the campaign Start gate, and the failure the version carries.
No network (the tick suite's fixtures stub every transport)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import desk as _desk
from mc import desk_accounts as _accounts
from mc import desk_tick as _tick
from mc.desk_connect import permission_policy as policy
from mc.desk_connect import post_consent
from test_desk_tick import CID, NOW, campaign, go, stored, version, world  # noqa: F401  (world is a fixture)

POST = {'purpose': 'publish', 'capability': 'post', 'route_id': 'x-oauth'}


@pytest.fixture(autouse=True)
def isolated_policy_cache():
    policy._forget_all_for_tests()
    yield
    policy._forget_all_for_tests()


def _consent(aid, *, post):
    policy.commit('p-' + aid + str(post), {'account_id': aid, 'service': 'x', 'account_kind': 'account',
                                           'read': False, 'post': post, 'scopes': [POST] if post else []})


def _acc(aid='ch-x'):
    with _desk._store_lock:
        return dict(_desk._read_store()['accounts'][aid])


def test_reason_for_is_none_for_legacy_allowed_manual_and_unsupported(world):
    assert post_consent.reason_for(_acc()) is None                       # no policy yet: the legacy path
    _consent('ch-x', post=True)
    assert post_consent.reason_for(_acc()) is None                       # explicit Allow
    assert post_consent.reason_for(_acc('ch-blog')) is None              # a platform the publisher has no API route for
    assert post_consent.reason_for(None) is None and post_consent.reason_for('x') is None
    assert post_consent.reason_for({**_acc(), 'capability': 'manual'}) is None      # a person presses Share


def test_reason_for_names_the_missing_tick_when_post_is_denied_or_read_only(world):
    _consent('ch-x', post=False)
    why = post_consent.reason_for(_acc())
    assert why and 'Permissions' in why and 'Post' in why
    assert post_consent.view(_acc()) == {'allowed': False, 'code': 'post_permission', 'reason': why}


def test_v1_account_carries_post_permission_only_while_it_is_missing(world):
    assert 'post_permission' not in _accounts.v1_account(_acc())
    _consent('ch-x', post=False)
    assert _accounts.v1_account(_acc())['post_permission']['code'] == 'post_permission'
    _consent('ch-x', post=True)
    assert 'post_permission' not in _accounts.v1_account(_acc())


def test_start_is_refused_with_the_missing_tick_named_and_works_once_ticked(world):
    _consent('ch-x', post=False)
    with pytest.raises(_desk.ApprovalRefused) as e:
        campaign(world)
    assert any('cannot post yet' in p and 'Permissions' in p for p in e.value.problems), e.value.problems
    with _desk._store_lock:
        assert _desk._read_store()['campaigns'][CID]['state'] == 'draft'    # nothing started
    _consent('ch-x', post=True)
    _desk.start_campaign(CID, today=NOW.date().isoformat())
    with _desk._store_lock:
        assert _desk._read_store()['campaigns'][CID]['state'] == 'running'


def test_start_is_unchanged_for_an_account_with_no_policy(world):
    campaign(world)
    with _desk._store_lock:
        assert _desk._read_store()['campaigns'][CID]['state'] == 'running'


def test_a_refused_publish_is_a_failed_version_that_carries_the_machine_code(world):
    campaign(world)                                                      # started while legacy
    piece, vid = version(world)
    _consent('ch-x', post=False)                                         # consent withdrawn before Approve
    v = go(world, piece, vid)
    assert v['state'] == 'failed' and v['failure']['code'] == 'post_permission'
    assert 'permission denied' in v['failure']['reason']                 # the publisher's own words, plus the code
    assert world.wire.posts == []                                        # nothing went out


def test_other_failures_carry_no_code(world):
    campaign(world)
    piece, vid = version(world)
    import urllib.error
    world.wire.fail_x = urllib.error.HTTPError('u', 403, 'forbidden', {}, None)
    world.wire.fail_x.read = lambda: b'{"detail":"dup"}'
    v = go(world, piece, vid)
    assert v['state'] == 'failed' and 'code' not in v['failure']
