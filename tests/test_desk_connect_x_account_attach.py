"""MC-1062/03b: real held callback and passcode Save attach to the same saved account.

Real temporary store/vault, scripted provider, isolated profile fixture: no live account calls.
"""
from __future__ import annotations

import copy

import pytest

from mc import desk, desk_account_refs, desk_accounts, desk_oauth, secrets_store
from mc.desk_connect import browser_setup, commit, registry
from tests.test_desk_oauth_hold import (CLIENT_ID, PASSCODE, X_TOKEN, ACCESS_1, REFRESH_1,
                                      _held, _sign_in, _vault_names, api, profiles, env,
                                      _clean_flows)


def saved(identity='ronbuilds'):
    """Use ticket 03's real browser-account Save, then retain a real pane-read setting."""
    browser_setup._forget_all_for_tests()
    raw = {
        'service': 'x', 'revision': registry.profile('x')['revision'], 'route_id': 'x-browser',
        'account_kind': 'account', 'account': {'new': {'identity': identity}},
        'browser_profile': 'browser-' + identity,
    }
    draft = browser_setup.clean_draft(raw)
    out, _ = browser_setup.commit('browser-' + identity, raw, draft)
    aid = out['account_id']
    with desk._store_lock:
        data = desk._read_store()
        data['accounts'][aid]['read_via'] = 'pane'
        data['accounts'][aid]['browser_profile'] = 'browser-' + identity
        desk._write_store(data)
    return aid, copy.deepcopy(desk._read_store()['accounts'][aid])


def start(client, provider, aid, **extra):
    provider.on('POST', X_TOKEN, (200, {'access_token': ACCESS_1, 'refresh_token': REFRESH_1, 'expires_in': 7200}))
    return client.post('/api/desk/connect/x/start-held', json={
        'passcode': PASSCODE, 'account_id': aid, 'hold': {'client_id': CLIENT_ID}, **extra})


def save(client, aid, held=None, fields=None, **extra):
    draft = {'url': 'https://x.com', 'method': 'oauth', 'account_id': aid,
             'fields': {'client_id': CLIENT_ID} if fields is None else fields}
    if held:
        draft['held'] = held
    return client.post('/api/desk/connect/commit', json={
        'passcode': PASSCODE, 'request_id': 'attach-req-001', 'draft': draft, **extra})


@pytest.mark.parametrize('second', [False, True])
def test_held_attach_preserves_same_id_and_browser_route(api, monkeypatch, second):
    client, p = api
    if second:
        saved('first')
    aid, before = saved()
    count = len(desk._read_store()['accounts'])
    def forbidden(*a, **kw):
        raise AssertionError('attachment must never create an account')
    monkeypatch.setattr(desk_accounts, 'create_account', forbidden)
    r = start(client, p, aid)
    assert r.status_code == 201, r.get_json()
    out = r.get_json()
    assert out['account_id'] == aid and out['profile'] == before['credentials']['oauth_profile']
    assert _sign_in(out)[0] == 200
    assert _vault_names() == []
    r = save(client, aid, _held(out))
    assert r.status_code == 201, r.get_json()
    assert r.get_json()['account_id'] == aid
    assert _vault_names() == sorted(['x.client-id', before['credentials']['oauth_vault']])
    data = desk._read_store()
    assert len(data['accounts']) == count
    assert data['accounts'][aid] == before
    assert desk_oauth.status('x', desk_account_refs.require_oauth_arg(aid))['state'] == 'connected'
    assert save(client, aid, _held(out)).status_code == 200  # idempotent replay
    assert desk._read_store()['accounts'][aid] == before


def test_save_without_held_starts_same_accounts_own_oauth(api, monkeypatch):
    client, _ = api
    saved('first')
    aid, before = saved()
    calls = []
    monkeypatch.setattr(desk_oauth, 'start', lambda service, arg=None: calls.append((service, arg)) or {'profile': before['credentials']['oauth_profile']})
    r = save(client, aid)
    assert r.status_code == 201, r.get_json()
    assert calls == [('x', aid)]
    assert desk._read_store()['accounts'][aid] == before
    assert len(desk._read_store()['accounts']) == 2


@pytest.mark.parametrize('which,status,code', [('missing', 404, 'account_not_found'), ('other', 400, 'wrong_platform'),
                                             ('invalid', 400, 'bad_account')])
def test_invalid_target_refused_before_any_oauth_or_write(api, monkeypatch, which, status, code):
    client, p = api
    aid = 'acct-absent' if which == 'missing' else [] if which == 'invalid' else desk_accounts.create_account('linkedin', 'member')['id']
    monkeypatch.setattr(desk_oauth, 'start', lambda *a, **kw: pytest.fail('refused target starts no sign-in'))
    r = start(client, p, aid)
    assert r.status_code == status and r.get_json()['code'] == code
    r = save(client, aid)
    assert r.status_code == status and r.get_json()['code'] == code
    assert _vault_names() == []


def test_identity_mismatch_refused_and_saved_label_untouched(api, monkeypatch):
    client, _ = api
    aid, before = saved()
    r = save(client, aid, fields={'identity': 'stranger', 'client_id': CLIENT_ID})
    assert r.status_code == 409 and r.get_json()['code'] == 'identity_mismatch'
    assert _vault_names() == [] and desk._read_store()['accounts'][aid] == before
    monkeypatch.setattr(desk_oauth, 'start', lambda *a, **kw: {})
    r = save(client, aid, fields={'identity': '@RONBUILDS', 'label': 'Overwrite', 'client_id': CLIENT_ID})
    assert r.status_code == 201, r.get_json()
    assert desk._read_store()['accounts'][aid] == before


def test_held_cannot_be_claimed_for_another_saved_account(api):
    client, p = api
    aid, _ = saved()
    other, before = saved('other')
    out = start(client, p, aid).get_json()
    assert _sign_in(out)[0] == 200
    r = save(client, other, _held(out))
    assert r.status_code == 409 and r.get_json()['code'] == 'account_mismatch'
    assert _vault_names() == [] and desk._read_store()['accounts'][other] == before


def test_changed_app_still_refuses_held_save(api):
    client, p = api
    aid, before = saved()
    out = start(client, p, aid).get_json()
    assert _sign_in(out)[0] == 200
    r = save(client, aid, _held(out), fields={'client_id': 'changed-app'})
    assert r.status_code == 409 and r.get_json()['code'] == 'app_mismatch'
    assert _vault_names() == [] and desk._read_store()['accounts'][aid] == before


def test_failed_token_save_never_deletes_existing_account(api, monkeypatch):
    client, p = api
    aid, before = saved()
    out = start(client, p, aid).get_json()
    assert _sign_in(out)[0] == 200
    real = secrets_store.set_secret
    def fail_token(name, *a, **kw):
        if name == before['credentials']['oauth_vault']:
            raise secrets_store.SecretsError('scripted failure')
        return real(name, *a, **kw)
    monkeypatch.setattr(secrets_store, 'set_secret', fail_token)
    r = save(client, aid, _held(out))
    assert r.status_code == 400, r.get_json()
    assert _vault_names() == [] and desk._read_store()['accounts'][aid] == before
    monkeypatch.setattr(secrets_store, 'set_secret', real)
    assert save(client, aid, _held(out)).status_code == 201


@pytest.mark.parametrize('passcode', [None, 'wrong-pass'])
def test_both_boundaries_still_need_their_own_passcode(api, passcode):
    client, p = api
    aid, before = saved()
    assert start(client, p, aid, passcode=passcode).status_code == 403
    out = start(client, p, aid).get_json()
    assert _sign_in(out)[0] == 200
    assert save(client, aid, _held(out), passcode=passcode).status_code == 403
    assert _vault_names() == [] and desk._read_store()['accounts'][aid] == before


def test_unattended_start_and_save_still_refused(api, monkeypatch):
    from mc.blueprints import desk_held_signin_routes, desk_connect_routes
    client, p = api
    aid, before = saved()
    monkeypatch.setattr(desk_held_signin_routes, 'is_unattended_caller', lambda: True)
    monkeypatch.setattr(desk_connect_routes, 'is_unattended_caller', lambda: True)
    assert start(client, p, aid).status_code == 403
    assert save(client, aid).status_code == 403
    assert _vault_names() == [] and desk._read_store()['accounts'][aid] == before


def test_target_deleted_between_validation_and_apply_is_refused(api):
    _, _p = api
    aid, _ = saved()
    clean = commit.clean_draft({'url': 'https://x.com', 'method': 'oauth', 'account_id': aid, 'fields': {'client_id': CLIENT_ID}})
    desk_accounts.delete_account(aid)
    with pytest.raises(commit.CommitError) as caught:
        commit.commit('attach-delete-001', clean)
    assert caught.value.code == 'account_not_found'
    assert _vault_names() == []


@pytest.mark.parametrize('bad', ['preview', 'refs'])
def test_target_without_usable_own_oauth_refs_never_falls_back(api, bad):
    client, p = api
    aid, _ = saved()
    with desk._store_lock:
        data = desk._read_store()
        if bad == 'preview':
            data['accounts'][aid]['preview'] = True
        else:
            data['accounts'][aid]['credentials']['oauth_profile'] = 'someone-elses-profile'
        desk._write_store(data)
    r = start(client, p, aid)
    assert r.status_code in (400, 409) and r.get_json()['code'] == 'account_refused'
    r = save(client, aid)
    assert r.status_code in (400, 409) and r.get_json()['code'] == 'account_refused'
    assert _vault_names() == []


def test_no_account_id_keeps_legacy_create_path(api, monkeypatch):
    client, p = api
    starts = []
    monkeypatch.setattr(desk_oauth, 'start', lambda service, arg=None, **kw: starts.append((service, arg, kw)) or {})
    r = client.post('/api/desk/connect/x/start-held', json={'passcode': PASSCODE, 'hold': {'client_id': CLIENT_ID}})
    assert r.status_code == 201
    assert r.get_json()['account_id'].startswith('acct-')
    assert starts[0][1] is None and desk._read_store()['accounts'] == {}
    r = client.post('/api/desk/connect/commit', json={'passcode': PASSCODE, 'request_id': 'legacy-new-001',
        'draft': {'url': 'https://x.com', 'method': 'oauth', 'fields': {'identity': 'legacy', 'client_id': CLIENT_ID}}})
    assert r.status_code == 201, r.get_json()
    aid = r.get_json()['account_id']
    assert len(desk._read_store()['accounts']) == 1 and desk._read_store()['accounts'][aid]['identity'] == 'legacy'
    assert starts[-1] == ('x', None, {})
