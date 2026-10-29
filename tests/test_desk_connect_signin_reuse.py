"""MC-1114 part 2: reopening a saved, signed-in X account to tick Post keeps its sign-in.

Same real temp vault / Desk store / scripted vendor as the 03b attach suite. Before this,
the Save of the developer-app connection claimed a freshly made sign-in and was refused
`already_signed_in` (the account was never signed out), so 'Permissions were not changed'.
"""
from __future__ import annotations

import copy
import json

import pytest
from flask import Flask

from mc import desk, desk_account_refs, desk_accounts, desk_oauth, secret_field_refs, secrets_store
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
    """A further start-held on the account (the X port is ephemeral in tests, conftest)."""
    return start(client, p, aid).get_json()


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


# ── an entry made before MC-1113 put `app_id_sha256` in the hint (Ron's real oauth.x.acct-0362a76b) ──

def make_legacy(aid, *, drop_client_id=False):
    """Rewrite the account's token entry as an older Clayrune stored it: hint without the app hash."""
    arg = desk_account_refs.require_oauth_arg(aid)
    rec = desk_oauth._read_record('x', consumer='test', account_id=arg)
    name = desk_oauth.vault_name('x', arg)
    old = next(s for s in secrets_store.list_secrets() if s['name'] == name)
    if drop_client_id:
        rec.pop('client_id', None)
    hint = ' '.join(p for p in old['hint'].split() if not p.startswith('app_id_sha256='))
    secrets_store.set_secret(name, json.dumps(rec, separators=(',', ':')), description=old.get('description', ''), hint=hint,
                             scope=old.get('scope') or 'global', allow_unattended=bool(old.get('allow_unattended', True)),
                             entry_type=secrets_store.ENTRY_TOKEN)
    assert 'app_id_sha256' not in token_hint(aid)
    return name


def meta_of(name):
    m = next(s for s in secrets_store.list_secrets() if s['name'] == name)
    return {k: v for k, v in m.items() if k not in ('last_used_at', 'use_count')}


@pytest.mark.parametrize('bound', [False, True], ids=['plain', 'field-bound'])
def test_legacy_save_connects_real_status_and_publish_readiness(api, monkeypatch, bound, capsys):
    """Save must repair the metadata that real status reads, not just say it reused a sign-in."""
    client, p = api
    aid, _ = signed_in(client, p)
    name = make_legacy(aid)
    arg = desk_account_refs.require_oauth_arg(aid)
    if bound:
        from mc.desk_connect.undo import UndoStack
        secrets_store.set_secret('test.x-app', CLIENT_ID)
        undo = UndoStack()
        secret_field_refs.create('x.client-id', {'name': 'test.x-app', 'part': 'value'},
                                 undo=undo, confirm_replace=True)
        undo.commit()
    # Retain unknown hint fields and every encrypted/policy field, not just today's public projection.
    with secrets_store._lock:
        store = secrets_store._load_store()
        store['secrets'][name]['hint'] += ' legacy_note=keep-this'
        secrets_store._save_store(store)
        before = copy.deepcopy(store['secrets'][name])
    policy.commit('allow-legacy-' + aid, {'account_id': aid, 'service': 'x', 'account_kind': 'account',
                   'read': False, 'post': True,
                   'scopes': [{'purpose': 'publish', 'capability': 'post', 'route_id': 'x-oauth'}]})
    account = copy.deepcopy(desk._read_store()['accounts'][aid])
    assert post_consent.reason_for(account) is None
    if bound:
        assert desk_oauth.status('x', arg) == {'state': 'needs_signin', 'reason': desk_oauth.APP_UNCONFIRMED}
        assert desk_accounts.publish_state(account)['ready'] is False
    fresh_hint = token_hint(aid) + ' app_id_sha256=' + secret_field_refs.fingerprint('x.client-id')
    response = save(client, aid, fields={}, request_id='reuse-legacy-readiness')
    assert response.status_code == 201, response.get_json()
    assert response.get_json()['reused_signin'] is True
    assert response.get_json()['status']['state'] == 'signed_in'
    # Status/readiness may not open the OAuth record themselves to hide missing hint metadata.
    monkeypatch.setattr(desk_oauth, '_read_record', lambda *a, **k: pytest.fail('status read the OAuth record'))
    assert desk_oauth.status('x', arg) == {'state': 'connected', 'reason': None}
    assert desk_accounts.publish_state(account)['ready'] is True
    again = save(client, aid, fields={}, request_id='reuse-legacy-readiness-again')
    assert again.status_code == 201 and again.get_json()['reused_signin'] is True
    assert post_consent.reason_for(account) is None
    assert desk._read_store()['accounts'][aid] == account
    with secrets_store._lock:
        after = copy.deepcopy(secrets_store._load_store()['secrets'][name])
    assert after.pop('hint') == fresh_hint
    before.pop('hint')
    for usage_key in ('last_used_at', 'use_count'):
        before.pop(usage_key, None)
        after.pop(usage_key, None)
    assert after == before
    assert CLIENT_ID not in repr(response.get_json()) + ''.join(capsys.readouterr())


@pytest.mark.parametrize('refusal', ['locked', 'no-client-id', 'different-app'])
def test_save_reuse_refusals_never_backfill_a_legacy_hint(api, refusal):
    client, p = api
    aid, _ = signed_in(client, p)
    name = make_legacy(aid, drop_client_id=refusal == 'no-client-id')
    before = meta_of(name)
    fields = {'client_id': 'different-app'} if refusal == 'different-app' else {}
    if refusal == 'locked':
        secrets_store.set_passphrase('a long passphrase for this test')
        secrets_store.lock_now()
    assert signin_reuse.applies({'service': 'x', 'account_id': aid, 'fields': fields}, True) is False
    assert meta_of(name) == before


def test_wrong_passcode_does_not_backfill_a_legacy_hint(api):
    client, p = api
    aid, _ = signed_in(client, p)
    name = make_legacy(aid)
    before = meta_of(name)
    response = save(client, aid, fields={}, request_id='reuse-no-passcode', passcode='wrong')
    assert response.status_code == 403
    assert meta_of(name) == before


def test_backfill_io_failure_refuses_save_without_leaking_the_error(api, monkeypatch, capsys):
    client, p = api
    aid, _ = signed_in(client, p)
    name = make_legacy(aid)
    hint = token_hint(aid)
    real_save = secrets_store._save_store

    def fail_backfill(store):
        if 'app_id_sha256=' in store['secrets'][name]['hint']:
            raise OSError(CLIENT_ID)  # exception text may contain a credential; only log the class
        return real_save(store)

    monkeypatch.setattr(secrets_store, '_save_store', fail_backfill)
    response = save(client, aid, fields={}, request_id='reuse-backfill-fails')
    assert response.status_code == 500 and response.get_json()['code'] == 'record_failed'
    assert token_hint(aid) == hint
    logs = ''.join(capsys.readouterr())
    assert 'hint backfill failed (OSError)' in logs
    assert CLIENT_ID not in repr(response.get_json()) + logs


def test_a_pre_hash_entry_is_reusable_when_its_record_names_the_same_app(api):
    client, p = api
    aid, _ = signed_in(client, p)
    make_legacy(aid)
    assert signin_reuse.check('x', aid) == {'reusable': True, 'reason': None}
    other = signin_reuse.check('x', aid, {'client_id': 'someone-elses-app'})
    assert other['reusable'] is False and 'different app' in other['reason']


def test_a_pre_hash_entry_is_reusable_even_when_status_asks_to_confirm_the_app(api, monkeypatch):
    """With the saved Client ID bound by field reference, `status` reports needs_signin for a hash-less entry."""
    client, p = api
    aid, _ = signed_in(client, p)
    make_legacy(aid)
    monkeypatch.setattr(desk_oauth, 'status', lambda *a, **k: {'state': 'needs_signin', 'reason': desk_oauth.APP_UNCONFIRMED})
    assert signin_reuse.check('x', aid)['reusable'] is True


def test_the_pre_hash_check_returns_and_logs_no_value_and_writes_nothing(api, capsys):
    client, p = api
    aid, _ = signed_in(client, p)
    name = make_legacy(aid)
    before = (token_hint(aid), _vault_names(), meta_of(name))
    out = signin_reuse.check('x', aid)
    assert CLIENT_ID not in repr(out) and CLIENT_ID not in ''.join(capsys.readouterr())
    assert (token_hint(aid), _vault_names(), meta_of(name)) == before      # only the read's own usage counters may differ


def test_a_pre_hash_entry_with_no_client_id_is_not_reusable_and_the_refusal_names_the_way_out(api):
    client, p = api
    aid, _ = signed_in(client, p)
    name = make_legacy(aid, drop_client_id=True)
    got = signin_reuse.check('x', aid)
    assert got['reusable'] is False and name in got['reason'] and 'Secrets' in got['reason']
    out = second_sign_in(client, p, aid)
    assert _sign_in(out)[0] == 200
    r = save(client, aid, _held(out), fields={}, request_id='reuse-req-0005')
    assert r.status_code == 409 and r.get_json()['code'] == 'already_signed_in'
    assert name in r.get_json()['error'] and 'Secrets' in r.get_json()['error'] and 'Connections' not in r.get_json()['error']


def test_a_locked_vault_is_not_reusable_and_says_to_unlock_it(api):
    client, p = api
    aid, _ = signed_in(client, p)
    make_legacy(aid)
    secrets_store.set_passphrase('a long passphrase for this test')
    secrets_store.lock_now()
    got = signin_reuse.check('x', aid)
    assert got['reusable'] is False and 'Unlock the Vault' in got['reason']


def test_a_vault_that_locks_mid_check_is_not_reusable_and_says_to_unlock_it(api, monkeypatch):
    client, p = api
    aid, _ = signed_in(client, p)
    make_legacy(aid)
    monkeypatch.setattr(desk_oauth, 'status', lambda *a, **k: {'state': 'needs_signin', 'reason': desk_oauth.APP_UNCONFIRMED})
    def locked(*a, **k):
        raise desk_oauth.OAuthError('vault_locked', 'locked', 409)
    monkeypatch.setattr(desk_oauth, '_read_record', locked)
    got = signin_reuse.check('x', aid)
    assert got['reusable'] is False and 'Unlock the Vault' in got['reason']


def test_the_route_gives_the_browser_the_same_answer_for_a_pre_hash_entry(api):
    client, p = api
    aid, _ = signed_in(client, p)
    make_legacy(aid)
    from mc.blueprints import desk_connect_signin_reuse_routes as routes
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    assert app.test_client().get(f'/api/desk/connect/signin-reuse/{aid}').get_json() == signin_reuse.check('x', aid) == {'reusable': True, 'reason': None}


def test_a_pre_hash_save_keeps_the_sign_in_and_a_held_one_never_replaces_the_entry(api):
    client, p = api
    aid, before = signed_in(client, p)
    name = make_legacy(aid)
    hint = token_hint(aid) + ' app_id_sha256=' + secret_field_refs.fingerprint('x.client-id')
    out = second_sign_in(client, p, aid)
    assert _sign_in(out)[0] == 200
    r = save(client, aid, _held(out), fields={}, request_id='reuse-req-0006')
    assert r.status_code == 201 and r.get_json()['reused_signin'] is True
    assert token_hint(aid) == hint and desk._read_store()['accounts'][aid] == before
