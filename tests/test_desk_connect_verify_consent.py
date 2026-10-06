"""MC-1062/06d: account Read consent on post readback (`desk_publish.verify_post`),
reached from the tick's immediate verify, crash recovery and the later
verification loop. Real publisher/tick with a recorded transport; no network."""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import desk, desk_oauth, desk_pieces as _pieces, desk_publish, desk_tick as _tick
from mc.desk_connect import permission_check as check
from mc.desk_connect import permission_policy as policy
from test_desk_tick import CID, NOW, campaign, go, stored, version, world  # noqa: F401  # pyright: ignore[reportUnusedImport]

POST = {'purpose': 'publish', 'capability': 'post', 'route_id': 'x-oauth'}
OWN_POSTS = {'purpose': 'read_own', 'capability': 'own_posts', 'route_id': 'x-oauth'}
MENTIONS_PANE = {'purpose': 'read_own', 'capability': 'mentions', 'route_id': 'x-browser'}
OWN_POSTS_PANE = {'purpose': 'read_own', 'capability': 'own_posts', 'route_id': 'x-browser'}


@pytest.fixture(autouse=True)
def isolated_policy_cache():
    policy._forget_all_for_tests()
    yield
    policy._forget_all_for_tests()


def consent(aid, *, read=False, post=False, scopes=(), tag='p'):
    policy.commit(f'policy-{aid}-{tag}', {'account_id': aid, 'service': 'x', 'account_kind': 'account',
                                          'read': read, 'post': post, 'scopes': list(scopes)})


@pytest.fixture
def readback(tmp_path, monkeypatch):
    """verify_post alone, two X accounts, every outbound seam recorded."""
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    desk._write_store({'oauth_legacy_bound': 'first', 'accounts': {
        'first': {'id': 'first', 'platform': 'x', 'identity': 'first', 'capability': 'direct',
                  'credentials': {'oauth_vault': 'oauth.x', 'oauth_profile': 'desk-x'}},
        'second': {'id': 'second', 'platform': 'x', 'identity': 'second', 'capability': 'direct',
                   'credentials': {'oauth_vault': 'oauth.x.second', 'oauth_profile': 'desk-x-second'}},
        'page': {'id': 'page', 'platform': 'linkedin', 'identity': 'Page', 'capability': 'direct',
                 'organization_id': '777'}}})
    seams = SimpleNamespace(token=Mock(return_value='valid-token'),
                            get=Mock(side_effect=lambda _t, post_id: {'data': {'id': post_id}}),
                            wire=Mock(side_effect=AssertionError('unexpected outbound HTTP')))
    monkeypatch.setattr(desk_oauth, 'x_token', seams.token)
    monkeypatch.setattr(desk_publish, '_get_tweet', seams.get)
    monkeypatch.setattr('urllib.request.urlopen', seams.wire)
    return seams


def untouched(r):
    r.token.assert_not_called()
    r.get.assert_not_called()
    r.wire.assert_not_called()


# -- verify_post, the consumer itself ------------------------------------------------------

@pytest.mark.parametrize('kw', [
    dict(),                                                                  # explicit Deny
    dict(post=True, scopes=[POST]),                                          # Post Allow only
    dict(read=True, scopes=[MENTIONS_PANE]),                                 # Read, wrong capability
    dict(read=True, scopes=[OWN_POSTS_PANE]),                                # Read, wrong route
], ids=['deny', 'post-only', 'mentions-only', 'pane-only'])
def test_denied_readback_makes_zero_token_and_zero_get_calls(readback, monkeypatch, kw):
    consent('first', **kw)
    mapped = Mock(return_value=None)
    monkeypatch.setattr(desk_publish._refs, 'oauth_arg_for', mapped)
    with pytest.raises(desk_publish.ReadConsentDenied, match='permission denied'):
        desk_publish.verify_post('x', 'p1', account_id='first')
    mapped.assert_not_called()
    untouched(readback)


@pytest.mark.parametrize('aid,arg', [('first', None), ('second', 'second')])
def test_exact_read_allow_verifies_with_the_accounts_own_token(readback, aid, arg):
    consent(aid, read=True, scopes=[OWN_POSTS])
    assert desk_publish.verify_post('x', 'p1', account_id=aid) is True
    assert readback.token.call_args.kwargs['account_id'] == arg
    readback.get.assert_called_once_with('valid-token', 'p1')


def test_read_allow_does_not_cover_posting(readback):
    consent('first', read=True, scopes=[OWN_POSTS])
    with pytest.raises(check.PermissionDenied):
        check.require_permission('first', 'x', 'post', **POST, account_kind='account')


@pytest.mark.parametrize('aid', ['missing', 'page', '', 7])
def test_bad_or_wrong_service_account_never_falls_back_to_another_token(readback, aid):
    with pytest.raises(desk_publish.PublishError):
        desk_publish.verify_post('x', 'p1', account_id=aid)
    untouched(readback)


def test_legacy_unset_and_pre_account_callers_read_back_exactly_as_before(readback):
    assert desk_publish.verify_post('x', 'p1', account_id='first') is True      # account, no policy
    assert desk_publish.verify_post('x', 'p1') is True                          # no account id
    assert desk_publish.verify_post('x', 'p1', account_id=None) is True
    assert readback.get.call_count == 3
    assert [c.kwargs['account_id'] for c in readback.token.call_args_list] == [None, None, None]


def test_pre_account_caller_is_not_blocked_by_an_explicit_deny_on_some_account(readback):
    consent('first')
    assert desk_publish.verify_post('x', 'p1') is True


def test_linkedin_stays_unsupported_with_no_call_whatever_the_account(readback):
    assert desk_publish.verify_post('linkedin', 'urn:li:share:1', account_id='page') is None
    assert desk_publish.verify_post('linkedin', 'urn:li:share:1') is None
    untouched(readback)


def test_revoking_read_takes_effect_on_the_next_call(readback):
    consent('first', read=True, scopes=[OWN_POSTS], tag='on')
    assert desk_publish.verify_post('x', 'p1', account_id='first') is True
    consent('first', tag='off')
    with pytest.raises(desk_publish.ReadConsentDenied, match='permission denied'):
        desk_publish.verify_post('x', 'p1', account_id='first')
    assert readback.get.call_count == 1 and readback.token.call_count == 1


# -- the tick: immediate verify, recovery, later loop ----------------------------------------------

def test_immediate_verify_denied_leaves_the_post_submitted_and_unverified(world, monkeypatch):
    campaign(world)
    piece, vid = version(world)
    consent('ch-x', post=True, scopes=[POST])                                   # Post Allow, no Read
    token = Mock(wraps=desk_oauth.x_token)
    monkeypatch.setattr(desk_oauth, 'x_token', token)
    v = go(world, piece, vid)
    assert v['state'] == 'submitted'                                            # the post is live and says so
    assert len(world.wire.posts) == 1 and world.wire.verifies == 0
    assert token.call_count == 1                                                # the post's token only
    assert not v['receipt'].get('verify_attempts') and 'permission denied' in v['receipt']['verify_error']
    assert 'verified_at' not in v['receipt'] and v['receipt']['post_id'] == 'x1'


def test_denied_readback_never_counts_an_attempt_or_gives_up(world):
    campaign(world)
    piece, vid = version(world)
    consent('ch-x', post=True, scopes=[POST])
    go(world, piece, vid)
    reads = len(world.vault.reads)
    for _ in range(_tick.VERIFY_MAX_ATTEMPTS + 3):
        _tick.run_once(NOW + timedelta(minutes=2))
    v = stored(piece, vid)
    assert v['state'] == 'submitted' and 'verify' not in v['receipt']
    assert not v['receipt'].get('verify_attempts') and 'permission denied' in v['receipt']['verify_error']
    assert world.wire.verifies == 0 and len(world.wire.posts) == 1
    assert len(world.vault.reads) == reads                                      # no token ever fetched for a read


def test_read_granted_after_many_denied_ticks_verifies_on_the_next_tick(world):
    campaign(world)
    piece, vid = version(world)
    consent('ch-x', post=True, scopes=[POST], tag='post')
    go(world, piece, vid)
    for _ in range(_tick.VERIFY_MAX_ATTEMPTS + 3):
        _tick.run_once(NOW + timedelta(minutes=2))
    assert world.wire.verifies == 0
    consent('ch-x', read=True, post=True, scopes=[POST, OWN_POSTS], tag='both')
    assert _tick.run_once(NOW + timedelta(minutes=3))['verified'] == [vid]
    v = stored(piece, vid)
    assert v['state'] == 'verified_published' and world.wire.verifies == 1 and v['receipt']['verified_at']


def test_a_generic_publish_error_still_counts_and_gives_up(world, monkeypatch):
    campaign(world)
    piece, vid = version(world)
    monkeypatch.setattr(desk_publish, '_get_tweet', Mock(side_effect=OSError('down')))
    v = go(world, piece, vid)
    assert v['state'] == 'submitted' and v['receipt']['verify_attempts'] == 1
    for _ in range(_tick.VERIFY_MAX_ATTEMPTS):
        _tick.run_once(NOW + timedelta(minutes=2))
    v = stored(piece, vid)
    assert v['receipt']['verify'] == 'unconfirmed' and v['receipt']['verify_attempts'] == _tick.VERIFY_MAX_ATTEMPTS


def test_the_denial_is_a_publish_error_subclass_so_other_callers_still_catch_it():
    assert issubclass(desk_publish.ReadConsentDenied, desk_publish.PublishError)


def test_later_verification_loop_zero_get_while_denied_then_verifies_once_read_is_granted(world):
    campaign(world)
    piece, vid = version(world)
    consent('ch-x', post=True, scopes=[POST], tag='post')
    go(world, piece, vid)
    out = _tick.run_once(NOW + timedelta(minutes=1))
    assert out['verified'] == [] and world.wire.verifies == 0
    assert not stored(piece, vid)['receipt'].get('verify_attempts')
    consent('ch-x', read=True, post=True, scopes=[POST, OWN_POSTS], tag='both')
    out = _tick.run_once(NOW + timedelta(minutes=2))
    assert out['verified'] == [vid] and world.wire.verifies == 1
    assert stored(piece, vid)['state'] == 'verified_published'


def test_recovery_of_a_sending_version_does_not_read_back_when_read_is_denied(world):
    campaign(world)
    piece, vid = version(world)
    _pieces.approve_version(piece, vid, scheduled_at=None)
    consent('ch-x', post=True, scopes=[POST])
    desk_publish.publish({'id': vid, 'platform': 'x', 'body': 'We shipped restore points.',
                          'campaign_id': CID, 'account_id': 'ch-x'}, project_id='alpha')
    _pieces.transition_version(piece, vid, to='sending', expect='approved')
    out = _tick.run_once(NOW)
    assert out['recovered'] == [(vid, 'submitted')]
    assert len(world.wire.posts) == 1 and world.wire.verifies == 0
    v = stored(piece, vid)
    assert v['state'] == 'submitted' and 'permission denied' in v['receipt']['verify_error']


def test_explicit_read_and_post_allow_still_verifies_immediately(world):
    campaign(world)
    piece, vid = version(world)
    consent('ch-x', read=True, post=True, scopes=[POST, OWN_POSTS])
    v = go(world, piece, vid)
    assert v['state'] == 'verified_published' and world.wire.verifies == 1


def test_legacy_account_verifies_immediately_as_before(world):
    campaign(world)
    piece, vid = version(world)
    v = go(world, piece, vid)
    assert v['state'] == 'verified_published' and world.wire.verifies == 1
