"""MC-1062/06c: the human connection check honours account Read consent.

The check's only probe is `PaneXReader.fetch_mentions` (`x-browser`, read_own/
mentions), built directly by `purpose_verification._pane_mentions`. Counting
constructions of the fake reader is the "no browser was opened" proof.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_desk_purposes import (FakePane, _b, _bind_pane_and_api, _draft, _rec, _save,  # noqa: F401
                                _store_bytes, _x, env, pane)

from mc import desk
from mc.desk_connect import permission_policy as policy
from mc.desk_connect import purpose_verification as pv

READ_MENTIONS = {'purpose': 'read_own', 'capability': 'mentions', 'route_id': 'x-browser'}
POST = {'purpose': 'publish', 'capability': 'post', 'route_id': 'x-oauth'}


@pytest.fixture(autouse=True)
def isolated_policy_cache():
    policy._forget_all_for_tests()
    yield
    policy._forget_all_for_tests()


@pytest.fixture
def built(pane, monkeypatch):
    """Record every reader the check constructs."""
    made = []
    real_init = FakePane.__init__

    def init(self, *a, **kw):
        made.append(self)
        real_init(self, *a, **kw)
    monkeypatch.setattr(FakePane, '__init__', init)
    return made


def consent(aid, *, read, post=False, scopes):
    policy.commit(f'policy-{aid}-{read}-{post}', {
        'account_id': aid, 'service': 'x', 'account_kind': 'account', 'read': read, 'post': post, 'scopes': scopes})


def verify(client, aid):
    r = client.post('/api/desk/connect/purpose/verify', json={'account_id': aid, 'purpose': 'read_own'})
    assert r.status_code == 200
    return r.get_json()


def browser_route(body):
    return next(r for r in body['routes'] if r['route_id'] == 'x-browser')


def bound(env):
    client, _, tmp = env
    acc = _x()
    assert _bind_pane_and_api(client, acc).status_code == 201
    return client, acc['id'], tmp


def test_explicit_read_deny_builds_no_reader_and_records_nothing(env, built):
    client, aid, tmp = bound(env)
    consent(aid, read=False, scopes=[])
    body = verify(client, aid)
    assert built == []
    assert browser_route(body)['result'] == 'failed' and 'permission denied' in browser_route(body)['message']
    assert body['state'] == 'not_checked' and body['verified'] == []
    assert not (tmp / 'desk_purpose_verification.json').exists()


def test_post_allow_does_not_imply_read(env, built):
    client, aid, _ = bound(env)
    consent(aid, read=False, post=True, scopes=[POST])
    body = verify(client, aid)
    assert built == [] and browser_route(body)['result'] == 'failed' and body['verified'] == []


def test_read_allow_on_the_exact_mentions_scope_runs_the_check(env, built):
    client, aid, _ = bound(env)
    consent(aid, read=True, scopes=[READ_MENTIONS])
    body = verify(client, aid)
    assert len(built) == 1
    assert browser_route(body)['result'] == 'passed' and body['verified'] == ['mentions']


def test_read_allow_for_another_route_does_not_cover_the_pane_probe(env, built):
    client, aid, _ = bound(env)
    consent(aid, read=True, scopes=[{'purpose': 'read_own', 'capability': 'own_posts', 'route_id': 'x-oauth'}])
    body = verify(client, aid)
    assert built == [] and browser_route(body)['result'] == 'failed' and body['verified'] == []


def test_revoking_read_after_a_passed_check_stops_the_next_probe(env, built):
    client, aid, _ = bound(env)
    consent(aid, read=True, scopes=[READ_MENTIONS])
    assert verify(client, aid)['verified'] == ['mentions'] and len(built) == 1
    consent(aid, read=False, scopes=[])
    body = verify(client, aid)
    assert len(built) == 1                       # no second reader
    assert browser_route(body)['result'] == 'failed'


def test_lost_policy_with_a_version_marker_fails_closed(env, built):
    client, aid, _ = bound(env)
    consent(aid, read=True, scopes=[READ_MENTIONS])
    with desk._store_lock:
        store = desk._read_store()
        del store['accounts'][aid][policy.POLICY_KEY]
        desk._write_store(store)
    body = verify(client, aid)
    assert built == [] and browser_route(body)['result'] == 'failed'


def test_legacy_account_without_policy_checks_exactly_as_before(env, built):
    client, aid, _ = bound(env)
    assert policy.POLICY_KEY not in _rec(aid) and policy.VERSION_KEY not in _rec(aid)
    body = verify(client, aid)
    assert len(built) == 1
    assert browser_route(body)['result'] == 'passed' and body['verified'] == ['mentions']


def test_denied_check_changes_no_stored_account(env, built):
    client, aid, _ = bound(env)
    consent(aid, read=False, scopes=[])
    before = _store_bytes()
    verify(client, aid)
    assert _store_bytes() == before


def test_consent_is_read_at_probe_time_not_from_the_checks_snapshot(env, built):
    """`check` loads the account first; a revoke landing before the probe must still stop it."""
    client, aid, _ = bound(env)
    consent(aid, read=True, scopes=[READ_MENTIONS])
    stale = _rec(aid)
    consent(aid, read=False, scopes=[])
    with pytest.raises(Exception, match='permission denied'):
        pv._pane_mentions(stale, {'refs': {'browser_profile': 'x-ron'}})
    assert built == []
