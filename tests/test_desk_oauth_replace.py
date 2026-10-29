"""A held X sign-in replaces a dead entry, with compensating restore, never revoke.

Real temporary vault/Desk store and scripted provider; no operator credentials.
"""
from copy import deepcopy
import json
import time

import pytest

from mc import desk, desk_account_refs, desk_oauth as oauth, desk_oauth_hold as hold, secrets_store as vault
from mc.desk_connect.undo import UndoStack
from tests.test_desk_connect_signin_reuse import second_sign_in, signed_in
from tests.test_desk_connect_x_account_attach import save
from tests.test_desk_oauth_hold import (CLIENT_ID, X_REVOKE, ACCESS_1, _held, _sign_in,
                                      api, profiles, env, _clean_flows)  # noqa: F401


@pytest.fixture(autouse=True)
def isolated_callback_port(monkeypatch):
    # These tests exercise replacement, not X's configured fixed callback port.
    # An ephemeral real loopback listener avoids Windows TIME_WAIT between cases.
    monkeypatch.setattr(oauth, 'X_CALLBACK_PORT', 0)


def entry(name):
    with vault._lock:
        return deepcopy(vault._load_store()['secrets'][name])


def dead_saved(client, p, kind='rejected'):
    aid, account = signed_in(client, p)
    arg = desk_account_refs.require_oauth_arg(aid)
    name = oauth.vault_name('x', arg)
    rec = {'access_token': 'old-access-for-replacement-test', 'client_id': CLIENT_ID,
           'expires_at': int(time.time()) - 10 if kind == 'expired' else int(time.time()) + 7200}
    if kind == 'rejected':
        rec.update(needs_signin=True, refresh_token='old-refresh-for-replacement-test')
    oauth._store_record('x', rec, arg)
    # Non-default metadata must survive Undo too, not just the token JSON.
    with vault._lock:
        store = vault._load_store()
        store['secrets'][name].update(description='original sign-in description', allow_unattended=False,
                                      scope='mission_control', use_count=7, last_used_at='2026-10-01T00:00:00Z')
        vault._save_store(store)
    assert oauth.status('x', arg)['state'] == 'needs_signin'
    return aid, account, arg, name


@pytest.mark.parametrize('kind', ['rejected', 'expired'])
def test_save_replaces_dead_signin_under_same_name_without_revoke(api, kind):
    client, p = api
    aid, account, arg, name = dead_saved(client, p, kind)
    names = {s['name'] for s in vault.list_secrets()}
    out = second_sign_in(client, p, aid)
    assert _sign_in(out)[0] == 200
    r = save(client, aid, _held(out), fields={}, request_id='replace-dead-001')
    assert r.status_code == 201, r.get_json()
    assert r.get_json()['account_id'] == aid and not r.get_json().get('reused_signin')
    assert {s['name'] for s in vault.list_secrets()} == names
    assert desk._read_store()['accounts'][aid] == account
    rec = oauth._read_record('x', consumer='replacement-test', project_id='mission_control', account_id=arg)
    assert rec['access_token'] == ACCESS_1 and not rec.get('needs_signin')
    assert oauth.status('x', arg)['state'] == 'connected'
    assert entry(name)['scope'] == 'mission_control' and entry(name)['allow_unattended'] is False
    assert hold.alive(out['flow_id']) == 0 and not p.to(X_REVOKE)
    assert ACCESS_1 not in json.dumps(r.get_json())
    replay = save(client, aid, _held(out), fields={}, request_id='replace-dead-001')
    assert replay.status_code == 200 and not p.to(X_REVOKE)


@pytest.mark.parametrize('hint', [
    'state=ok exp=2147483647 refresh=yes',
    'state=ok exp=1 refresh=yes',  # expired but refreshable is still usable
    'state=ok exp=2147483647 refresh=no',
    'state=ok exp=invalid refresh=no',
    '',  # absence of proof of a dead sign-in does not permit overwriting it
])
def test_commit_held_still_refuses_usable_or_unconfirmed_entries(api, hint):
    client, p = api
    aid, _ = signed_in(client, p)
    arg = desk_account_refs.require_oauth_arg(aid)
    name = oauth.vault_name('x', arg)
    with vault._lock:
        store = vault._load_store()
        store['secrets'][name]['hint'] = hint
        vault._save_store(store)
    before = entry(name)
    out = second_sign_in(client, p, aid)
    assert _sign_in(out)[0] == 200
    undo = UndoStack()
    with pytest.raises(oauth.OAuthError) as ex:
        oauth.commit_held('x', out['flow_id'], out['claim'], arg, undo, client_id=CLIENT_ID)
    assert ex.value.code == 'already_signed_in'
    assert entry(name) == before and len(undo) == 0
    assert hold.alive(out['flow_id']) and not p.to(X_REVOKE)


@pytest.mark.parametrize('kind', ['rejected', 'expired'])
def test_later_save_failure_restores_exact_previous_record(api, monkeypatch, kind):
    from mc import desk_engine_connections
    client, p = api
    aid, account, arg, name = dead_saved(client, p, kind)
    out = second_sign_in(client, p, aid)
    assert _sign_in(out)[0] == 200
    before = entry(name)

    def fail_after_token_write(*a, **kw):
        assert entry(name) != before  # prove this failure follows the replacement
        raise RuntimeError('scripted failure after token write')

    monkeypatch.setattr(desk_engine_connections, 'reconnect_saved', fail_after_token_write)
    r = save(client, aid, _held(out), fields={}, request_id='replace-undo-001')
    assert r.status_code == 500, r.get_json()
    assert entry(name) == before
    assert desk._read_store()['accounts'][aid] == account
    assert oauth.status('x', arg)['state'] == 'needs_signin'
    assert not p.to(X_REVOKE)


def test_failed_replacement_keeps_old_entry_and_retryable_hold(api, monkeypatch):
    client, p = api
    aid, _, arg, name = dead_saved(client, p)
    out = second_sign_in(client, p, aid)
    assert _sign_in(out)[0] == 200
    before = entry(name)

    def refuse(*a, **kw):
        raise vault.SecretsError('scripted vault failure')

    monkeypatch.setattr(oauth, '_store_record', refuse)
    r = save(client, aid, _held(out), fields={}, request_id='replace-fail-001')
    assert r.status_code == 400 and r.get_json()['code'] == 'vault_refused'
    assert entry(name) == before and hold.alive(out['flow_id'])
    hold.discard(out['flow_id'])
    assert entry(name) == before and not p.to(X_REVOKE)  # MC-1117 also protects dead saved grants


def test_undo_does_not_overwrite_an_intervening_signin_change(api):
    client, p = api
    aid, _, arg, name = dead_saved(client, p)
    out = second_sign_in(client, p, aid)
    assert _sign_in(out)[0] == 200
    undo = UndoStack()
    oauth.commit_held('x', out['flow_id'], out['claim'], arg, undo, client_id=CLIENT_ID)
    oauth._store_record('x', {'access_token': 'intervening-test-token', 'client_id': CLIENT_ID}, arg)
    intervening = entry(name)
    assert undo.unwind() == [f'vault entry {name}']
    assert entry(name) == intervening and not p.to(X_REVOKE)
