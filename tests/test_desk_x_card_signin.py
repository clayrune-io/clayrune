"""Card-only X auto-save: real callback/temp vault, scripted provider, no live login."""
import json

import pytest

from mc import desk, desk_account_refs, desk_oauth as oauth, desk_oauth_hold as hold, secrets_store as vault
from mc.desk_connect import x_card_signin as card
from tests.test_desk_connect_x_account_attach import saved, start, save
from tests.test_desk_oauth_hold import (CLIENT_ID, PASSCODE, ACCESS_1, X_REVOKE, _held, _sign_in,
                                      _start_x, api, profiles, env, _clean_flows)  # noqa: F401
from tests.test_desk_oauth_replace import dead_saved, entry


@pytest.fixture(autouse=True)
def callback_port(monkeypatch):
    monkeypatch.setattr(oauth, 'X_CALLBACK_PORT', 0)


def begin(client, p, aid):
    vault.set_secret('x.client-id', CLIENT_ID)
    out = start(client, p, aid, auto_save=True)
    assert out.status_code == 201, out.get_json()
    return out.get_json()


def poll(client, out):
    return client.get('/api/desk/connect/flows/' + out['flow_id']).get_json()


@pytest.mark.parametrize('second', [False, True])
def test_match_saves_once_without_second_passcode_and_keeps_account(api, second):
    client, p = api
    if second:
        saved('first')
    aid, before = saved()
    p.on('GET', card.ME_URL, (200, {'data': {'id': '123', 'username': 'RONBUILDS'}}))
    out = begin(client, p, aid)
    code, page = _sign_in(out)
    assert code == 200 and 'The X sign-in is saved.' in page and 'press Save' not in page
    assert poll(client, out)['status'] == 'done'
    assert hold.alive(out['flow_id']) == 0
    assert desk._read_store()['accounts'][aid] == before
    arg = desk_account_refs.require_oauth_arg(aid)
    assert oauth.status('x', arg)['state'] == 'connected'
    calls = p.to(card.ME_URL)
    assert len(calls) == 1 and calls[0]['headers']['Authorization'] == 'Bearer ' + ACCESS_1
    assert ACCESS_1 not in page + json.dumps(poll(client, out))
    assert out['claim'] not in page + json.dumps(poll(client, out))
    # Duplicate callback cannot repeat the read or write.
    from tests.test_desk_oauth import _state_of
    assert oauth.complete({'state': _state_of(out['auth_url'])['state'], 'code': 'duplicate'})[0] is False
    assert len(p.to(card.ME_URL)) == 1
    assert not p.to(X_REVOKE)


def test_mismatch_is_discard_only_and_refuses_a_direct_manual_commit(api):
    client, p = api
    aid, before = saved()
    p.on('GET', card.ME_URL, (200, {'data': {'username': 'someone_else'}}))
    out = begin(client, p, aid)
    code, page = _sign_in(out)
    status = poll(client, out)
    assert code == 200 and '@someone_else' in page and '@ronbuilds' in page and 'press Save' not in page
    assert status['status'] == 'held' and status['save_allowed'] is False
    assert hold.alive(out['flow_id']) and oauth.status('x')['state'] == 'not_connected'
    r = save(client, aid, _held(out), fields={})
    assert r.status_code == 409 and r.get_json()['code'] == 'identity_mismatch'
    assert desk._read_store()['accounts'][aid] == before
    assert client.post('/api/desk/connect/flows/' + out['flow_id'] + '/cancel', json={'claim': out['claim']}).get_json()['ok']
    assert not hold.alive(out['flow_id'])


@pytest.mark.parametrize('response', [
    (401, {'error': ACCESS_1}), (403, {}), (429, {}), (500, {}),
    (200, {}), (200, {'data': {'username': '<script>'}}), OSError('network failure'),
])
def test_lookup_failure_keeps_manual_save_and_does_not_echo_provider(api, response):
    client, p = api
    aid, before = saved()
    p.on('GET', card.ME_URL, response)
    out = begin(client, p, aid)
    code, page = _sign_in(out)
    status = poll(client, out)
    assert code == 200 and 'identity could not be confirmed' in page
    assert status['status'] == 'held' and status['save_allowed'] is True
    assert len(p.to(card.ME_URL)) == 1 and ACCESS_1 not in page + json.dumps(status)
    assert save(client, aid, _held(out), fields={}, request_id='card-retry-001').status_code == 201
    assert desk._read_store()['accounts'][aid] == before


@pytest.mark.parametrize('kind', ['rejected', 'expired'])
def test_auto_save_replaces_dead_entry_and_keeps_policy(api, kind):
    client, p = api
    aid, before, arg, name = dead_saved(client, p, kind)
    previous = entry(name)
    p.on('GET', card.ME_URL, (200, {'data': {'username': 'ronbuilds'}}))
    out = begin(client, p, aid)
    assert _sign_in(out)[0] == 200 and poll(client, out)['status'] == 'done'
    after = entry(name)
    assert after['scope'] == previous['scope'] and after['allow_unattended'] is False
    assert oauth.status('x', arg)['state'] == 'connected' and not p.to(X_REVOKE)
    assert desk._read_store()['accounts'][aid] == before


def test_live_entry_save_failure_falls_back_without_losing_either_signin(api):
    client, p = api
    aid, _, arg, name = dead_saved(client, p)
    with vault._lock:
        store = vault._load_store()
        store['secrets'][name]['hint'] = 'state=ok exp=2147483647 refresh=yes'
        vault._save_store(store)
    previous = entry(name)
    p.on('GET', card.ME_URL, (200, {'data': {'username': 'ronbuilds'}}))
    out = begin(client, p, aid)
    assert _sign_in(out)[0] == 200
    status = poll(client, out)
    assert status['status'] == 'held' and status['save_allowed'] is True
    assert 'could not be saved automatically' in status['message']
    assert hold.alive(out['flow_id']) and entry(name) == previous and not p.to(X_REVOKE)


def test_write_failure_restores_previous_entry_and_leaves_manual_retry(api, monkeypatch):
    client, p = api
    aid, _, _, name = dead_saved(client, p)
    previous = entry(name)
    p.on('GET', card.ME_URL, (200, {'data': {'username': 'ronbuilds'}}))
    out = begin(client, p, aid)
    real = oauth._replace.store_with_undo
    def fail_after_write(*a, **kw):
        real(*a, **kw)
        raise OSError('write failed after registering undo')
    monkeypatch.setattr(oauth._replace, 'store_with_undo', fail_after_write)
    assert _sign_in(out)[0] == 200
    assert entry(name) == previous and hold.alive(out['flow_id'])
    assert poll(client, out)['status'] == 'held'
    monkeypatch.setattr(oauth._replace, 'store_with_undo', real)
    assert save(client, aid, _held(out), fields={}, request_id='card-undo-retry').status_code == 201


def test_app_change_during_identity_lookup_falls_back(api):
    client, p = api
    aid, _ = saved()
    out = begin(client, p, aid)
    def changed(*a, **kw):
        vault.set_secret('x.client-id', 'different-app')
        return 200, {'data': {'username': 'ronbuilds'}}
    original = oauth._call
    # Keep the scripted exchange; alter only the identity response.
    from unittest.mock import patch
    with patch.object(oauth, '_call', side_effect=lambda method, url, **kw: changed() if url == card.ME_URL else original(method, url, **kw)):
        assert _sign_in(out)[0] == 200
    assert poll(client, out)['status'] == 'held' and 'Client ID has changed' in poll(client, out)['message']


def test_cancel_during_lookup_prevents_any_save(api, monkeypatch):
    client, p = api
    aid, _ = saved()
    out = begin(client, p, aid)
    original = oauth._call
    def cancel(method, url, **kw):
        if url == card.ME_URL:
            assert oauth.cancel_flow(out['flow_id'], out['claim'])['ok']
            return 200, {'data': {'username': 'ronbuilds'}}
        return original(method, url, **kw)
    monkeypatch.setattr(oauth, '_call', cancel)
    assert _sign_in(out)[0] == 400
    assert poll(client, out)['status'] == 'unknown' and not hold.alive(out['flow_id'])
    assert len(vault.list_secrets()) == 1  # only app id


def test_account_handle_change_during_lookup_is_discard_only(api, monkeypatch):
    client, p = api
    aid, _ = saved()
    out = begin(client, p, aid)
    original = oauth._call
    def changed(method, url, **kw):
        if url == card.ME_URL:
            with desk._store_lock:
                store = desk._read_store()
                store['accounts'][aid]['identity'] = 'changed_user'
                desk._write_store(store)
            return 200, {'data': {'username': 'ronbuilds'}}
        return original(method, url, **kw)
    monkeypatch.setattr(oauth, '_call', changed)
    assert _sign_in(out)[0] == 200
    assert poll(client, out)['save_allowed'] is False
    assert 'identity changed' in poll(client, out)['message']
    assert hold.alive(out['flow_id']) and len(vault.list_secrets()) == 1


def test_wizard_has_no_lookup_and_still_holds(api):
    client, p = api
    out = _start_x(client, p)
    assert _sign_in(out)[0] == 200
    assert poll(client, out)['status'] == 'held' and not p.to(card.ME_URL)
    assert hold.alive(out['flow_id']) and vault.list_secrets() == []


def test_auto_save_cannot_be_requested_without_existing_account_or_human_gate(api, monkeypatch):
    from mc.blueprints import desk_held_signin_routes as routes
    client, p = api
    assert client.post('/api/desk/connect/x/start-held', json={'passcode': PASSCODE, 'auto_save': True}).status_code == 400
    aid, _ = saved()
    assert start(client, p, aid, auto_save=True, passcode='wrong').status_code == 403
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: True)
    assert start(client, p, aid, auto_save=True).status_code == 403
    assert not p.to(card.ME_URL) and not oauth._flows
