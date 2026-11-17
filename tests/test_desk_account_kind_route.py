"""MC-1187 follow-up F1: the generic POST /api/desk/accounts takes an account kind.

A LinkedIn account made through the generic route with no kind got no consent policy
and so read as a legacy account (no consent needed). For a platform with more than
one account kind the route now refuses a request that names none, with a plain 400;
the kind is part of the policy and is never defaulted. A platform with one kind
passes that kind through without the caller having to say it.
"""
from __future__ import annotations

import pytest

from mc import desk as _desk
from mc.desk_connect import permission_policy

from tests.test_desk_accounts import env, _create  # noqa: F401


def _rec(account_id):
    with _desk._store_lock:
        return dict(_desk._read_store()['accounts'][account_id])


def test_linkedin_without_a_kind_is_refused_naming_the_kinds(env):
    client, _, _ = env
    r = _create(client, platform='linkedin', identity='Clayrune', id='ch-li')
    assert r.status_code == 400, r.get_json()
    msg = r.get_json()['error']
    assert 'member' in msg and 'organization' in msg
    assert client.get('/api/desk/accounts').get_json() == []        # nothing was written


@pytest.mark.parametrize('kind', ['member', 'organization'])
def test_linkedin_with_a_kind_starts_denied_under_that_kind(env, kind):
    client, _, _ = env
    r = _create(client, platform='linkedin', identity='Clayrune', id='ch-li', account_kind=kind)
    assert r.status_code == 201, r.get_json()
    state = permission_policy.read_policy(_rec('ch-li'), account_id='ch-li', account_kind=kind)
    assert state['state'] == 'explicit' and state['read'] is False and state['post'] is False


def test_an_unknown_kind_is_refused(env):
    client, _, _ = env
    r = _create(client, platform='linkedin', identity='Clayrune', id='ch-li', account_kind='page')
    assert r.status_code == 400 and 'organization' in r.get_json()['error']
    r = _create(client, platform='x', identity='@ron', account_kind='organization')
    assert r.status_code == 400
    r = _create(client, platform='linkedin', identity='Clayrune', id='ch-li2', account_kind=['member'])
    assert r.status_code == 400
    assert client.get('/api/desk/accounts').get_json() == []


def test_a_single_kind_platform_needs_no_kind_and_still_starts_denied(env):
    client, _, _ = env
    r = _create(client, platform='x', identity='@ron', id='ch-x')
    assert r.status_code == 201, r.get_json()
    state = permission_policy.read_policy(_rec('ch-x'), account_id='ch-x', account_kind='account')
    assert state['state'] == 'explicit' and state['read'] is False and state['post'] is False


def test_a_blog_has_no_kinds_and_is_created_as_before(env):
    client, _, _ = env
    assert _create(client, platform='blog', identity='my-blog').status_code == 201
    assert _create(client, platform='blog', identity='other-blog', account_kind='member').status_code == 400
