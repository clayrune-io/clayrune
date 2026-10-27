"""MC-1114 part 2: reopening a saved, signed-in X account to tick Post keeps its sign-in.

Same real temp vault / Desk store / scripted vendor as the 03b attach suite. Before this,
the Save of the developer-app connection claimed a freshly made sign-in and was refused
`already_signed_in` (the account was never signed out), so 'Permissions were not changed'.
"""
from __future__ import annotations

import copy
import time

import pytest
from flask import Flask

from mc import desk, desk_account_refs, desk_oauth, secrets_store
from mc.desk_connect import permission_policy as policy
from mc.desk_connect import post_consent, provider_commit, signin_reuse
from tests.test_desk_connect_x_account_attach import saved, save, start
from tests.test_desk_oauth_hold import (CLIENT_ID, PASSCODE, _held, _sign_in, _vault_names, api, profiles, env,  # noqa: F401
                                      _clean_flows)


def signed_in(client, p):
    """A saved X account that already holds its own working sign-in for CLIENT_ID."""
    aid, before = saved()
    out = start(client, p, aid).get_json()
    assert _sign_in(out)[0] == 200
    r = save(client, aid, _held(out))
    assert r.status_code == 201, r.get_json()
    return aid, copy.deepcopy(desk._read_store()['accounts'][aid])


def second_sign_in(client, p, aid):
    """A further start-held on the account: the first flow's return-address listener closes a moment after its Save."""
    for _ in range(40):
        out = start(client, p, aid).get_json()
        if out.get('code') != 'port_busy':
            return out
        time.sleep(0.25)
    return out


def token_hint(aid):
    name = desk_oauth.vault_name('x', desk_account_refs.require_oauth_arg(aid))
    return next(s for s in secrets_store.list_secrets() if s['name'] == name)['hint']


def test_a_signed_in_account_is_reusable_for_the_saved_app_only(api):
    client, p = api
    aid, _ = signed_in(client, p)
    assert signin_reuse.check('x', aid) == {'reusable': True, 'reason': None}              # the app saved in the Vault
    other = signin_reuse.check('x', aid, {'client_id': 'someone-elses-app'})
    assert other['reusable'] is False and 'different app' in other['reason']
    assert signin_reuse.check('linkedin', aid)['reusable'] is False
    assert signin_reuse.check('x', 'acct-absent')['reusable'] is False
    assert signin_reuse.check('x', None)['reusable'] is False


def test_an_account_that_never_signed_in_is_not_reusable(api):
    client, _ = api
    aid, _ = saved()
    assert signin_reuse.check('x', aid)['reusable'] is False


def test_a_lapsed_sign_in_is_not_reusable(api, monkeypatch):
    client, p = api
    aid, _ = signed_in(client, p)
    monkeypatch.setattr(desk_oauth, 'status', lambda *a, **k: {'state': 'needs_signin', 'reason': 'ran out'})
    assert signin_reuse.check('x', aid)['reusable'] is False


def test_the_route_answers_without_reading_a_token(api, monkeypatch):
    client, p = api
    aid, _ = signed_in(client, p)
    from mc.blueprints import desk_connect_signin_reuse_routes as routes
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    monkeypatch.setattr(desk_oauth, '_read_record', lambda *a, **k: pytest.fail('a token was read'))
    r = app.test_client().get(f'/api/desk/connect/signin-reuse/{aid}')
    assert r.status_code == 200 and r.get_json() == {'reusable': True, 'reason': None}
    assert app.test_client().get('/api/desk/connect/signin-reuse/acct-absent').get_json()['reusable'] is False


def test_save_without_a_new_sign_in_keeps_the_saved_one_and_starts_none(api, monkeypatch):
    client, p = api
    aid, before = signed_in(client, p)
    names, hint = _vault_names(), token_hint(aid)
    monkeypatch.setattr(desk_oauth, 'start', lambda *a, **k: pytest.fail('a reused sign-in starts no new one'))
    r = save(client, aid, fields={}, request_id='reuse-req-0002')                  # the app is the saved one: not typed again
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body['reused_signin'] is True and body['account_id'] == aid and 'signin' not in body
    assert _vault_names() == names and token_hint(aid) == hint
    assert desk._read_store()['accounts'][aid] == before
    assert desk_oauth.status('x', desk_account_refs.require_oauth_arg(aid))['state'] == 'connected'


def test_a_sign_in_made_anyway_is_not_claimed_and_no_longer_refused(api):
    """The reported failure: a fresh held sign-in on an account that already holds one."""
    client, p = api
    aid, before = signed_in(client, p)
    hint = token_hint(aid)
    out = second_sign_in(client, p, aid)
    assert _sign_in(out)[0] == 200
    r = save(client, aid, _held(out), fields={}, request_id='reuse-req-0003')
    assert r.status_code == 201, r.get_json()
    assert r.get_json()['reused_signin'] is True
    assert token_hint(aid) == hint and desk._read_store()['accounts'][aid] == before


def test_without_the_reuse_the_same_save_is_refused_already_signed_in(api, monkeypatch):
    client, p = api
    aid, _ = signed_in(client, p)
    monkeypatch.setattr(signin_reuse, 'applies', lambda *a, **k: False)
    out = second_sign_in(client, p, aid)
    assert _sign_in(out)[0] == 200
    r = save(client, aid, _held(out), fields={}, request_id='reuse-req-0003')
    assert r.status_code == 409 and r.get_json()['code'] == 'already_signed_in'


def test_another_app_is_not_reused_and_keeps_its_own_refusal(api):
    client, p = api
    aid, before = signed_in(client, p)
    out = second_sign_in(client, p, aid)
    assert _sign_in(out)[0] == 200
    r = save(client, aid, _held(out), fields={'client_id': 'changed-app'}, request_id='reuse-req-0004')
    assert r.status_code == 409 and r.get_json()['code'] in ('secret_exists', 'app_mismatch')     # refused as before, never reused
    assert desk._read_store()['accounts'][aid] == before


def test_reuse_never_grants_post_the_permission_step_still_does(api):
    client, p = api
    aid, _ = signed_in(client, p)
    policy.commit('deny-' + aid, {'account_id': aid, 'service': 'x', 'account_kind': 'account',
                                  'read': False, 'post': False, 'scopes': []})
    assert save(client, aid, fields={}, request_id='reuse-req-0002').status_code == 201
    with desk._store_lock:
        rec = dict(desk._read_store()['accounts'][aid])
    assert post_consent.reason_for(rec)                     # Save reused the sign-in and granted nothing
    policy.commit('allow-' + aid, {'account_id': aid, 'service': 'x', 'account_kind': 'account', 'read': False, 'post': True,
                                   'scopes': [{'purpose': 'publish', 'capability': 'post', 'route_id': 'x-oauth'}]})
    with desk._store_lock:
        rec = dict(desk._read_store()['accounts'][aid])
    assert post_consent.reason_for(rec) is None
