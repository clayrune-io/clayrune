"""Desk connect-by-URL, slice 2 / M3: known-host adapters and verification
(docs/DESK_CONNECT_BY_URL_SPEC.md "Result" and slice 2 acceptance).

Pinned:

  * a registry `available` row is selectable only when the service has a provider
    that supports the method; LinkedIn / YouTube / Drive stay information only and
    cannot be committed even by a hand-built request;
  * a stored key never reads "Verified": only a passed read-only probe does, and a
    replaced, re-saved or deleted credential un-verifies itself;
  * a failed probe is "Check failed", an X sign-in has no free check and so is never
    Verified, and a provider that raises reads as a failed check;
  * the Save writes the provider's entries newest-undone-first: a failure after the
    first write leaves nothing behind, an undo that fails is named, and an entry that
    already exists is refused (never replaced) before anything is written;
  * a failed sign-in start AFTER the commit keeps the credential and the account and
    says "setup failed";
  * two X accounts get separate sign-ins; the second reuses the stored app Client ID;
  * verify is human-only, takes no passcode and writes nothing; no response, log or
    Desk store ever holds the key value.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests.test_desk_connect import (PASSCODE, RID, SECRET, _commit_req, _services, _vault_names,  # noqa: E402,F401
                                     env)


def _key_draft(url='https://aistudio.google.com/app/apikey', secret=SECRET, **fields):
    f = {'secret': secret}
    f.update(fields)
    return {'url': url, 'method': 'api_key', 'fields': f}


def _post(client, draft, rid=RID, passcode=PASSCODE):
    return _commit_req(client, draft, rid=rid, passcode=passcode)


def _verify(client, service, method, **extra):
    return client.post('/api/desk/connect/verify', json={'service': service, 'method': method, **extra})


@pytest.fixture()
def net(monkeypatch):
    """Every outbound call the providers can make, replaced by a recorder."""
    from mc import desk_oauth
    state = {'key_ok': True, 'starts': [], 'start_error': None, 'tests': []}

    def test_key(service, **kw):
        state['tests'].append(service)
        return {'ok': state['key_ok'], 'message': 'accepted' if state['key_ok'] else 'rejected by the service'}

    def start(service, account_id=None):
        state['starts'].append((service, account_id))
        if state['start_error']:
            raise desk_oauth.OAuthError('unreachable', state['start_error'], 502)
        return {'state': 'started', 'service': service, 'account_id': account_id}

    monkeypatch.setattr(desk_oauth, 'test_key', test_key)
    monkeypatch.setattr(desk_oauth, 'start', start)
    return state


# -- what the Method step offers ---------------------------------------------------

def test_known_services_with_a_provider_are_selectable_and_carry_their_fields(env):
    client, _, _ = env
    got = client.post('/api/desk/connect/inspect', json={'input': 'https://aistudio.google.com'}).get_json()
    row = next(o for o in got['options'] if o['method'] == 'api_key')
    assert row['selectable'] is True
    assert [f['key'] for f in row['connector']['fields']] == ['secret', 'allow_unattended']
    assert row['connector']['fields'][0]['kind'] == 'secret' and 'value' not in row['connector']['fields'][0]
    assert row['connector']['signs_in'] is False


def test_recognised_without_a_provider_stays_information_only(env):
    client, _, _ = env
    for host in ('https://www.linkedin.com/in/x', 'https://youtube.com', 'https://drive.google.com'):
        got = client.post('/api/desk/connect/inspect', json={'input': host}).get_json()
        assert all(not o['selectable'] or o['method'] == 'save_for_agents' for o in got['options']), host
        assert all('connector' not in o for o in got['options']), host


def test_a_hand_built_request_cannot_commit_an_information_only_method(env):
    client, calls, _ = env
    r = _post(client, {'url': 'https://www.linkedin.com/in/x', 'method': 'oauth', 'fields': {}})
    assert r.status_code == 400 and r.get_json()['code'] == 'method_not_available'
    assert calls['n'] == 0 and _vault_names() == []


def test_the_service_comes_from_the_host_not_from_the_browser(env):
    client, _, _ = env
    d = _key_draft(url='https://platform.openai.com/api-keys')
    d['service'] = 'google_ai'
    assert _post(client, d).status_code == 400
    assert _vault_names() == []


def test_unknown_field_is_refused_before_the_passcode(env):
    client, calls, _ = env
    r = _post(client, _key_draft(extra='x'))
    assert r.status_code == 400 and calls['n'] == 0 and _vault_names() == []


# -- key paste: store, then check --------------------------------------------------

def test_a_stored_key_is_not_verified_until_checked(env, net):
    client, calls, _ = env
    r = _post(client, _key_draft())
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body['stored'] == ['gemini-api'] and body['status']['state'] == 'key_stored'
    assert body['status']['label'] == 'Key stored, not verified'
    assert 'verified' not in json.dumps(body).replace('not verified', '').lower()
    assert SECRET not in json.dumps(body) and _vault_names() == ['gemini-api']
    assert calls['n'] == 1 and net['tests'] == []          # saving makes no outside call
    v = _verify(client, 'google_ai', 'api_key').get_json()
    assert v['state'] == 'verified' and v['capability'] == 'list models (read-only)' and v['at']
    assert net['tests'] == ['gemini'] and SECRET not in json.dumps(v)


def test_a_failed_check_is_check_failed_never_verified(env, net):
    client, _, _ = env
    _post(client, _key_draft(url='https://platform.openai.com/api-keys'))
    net['key_ok'] = False
    v = _verify(client, 'openai', 'api_key').get_json()
    assert v['state'] == 'check_failed' and v['failure'] == 'rejected' and v['message'] == 'rejected by the service'


def test_a_replaced_key_un_verifies(env, net):
    from mc import secrets_store
    client, _, _ = env
    _post(client, _key_draft())
    assert _verify(client, 'google_ai', 'api_key').get_json()['state'] == 'verified'
    secrets_store.set_secret('gemini-api', 'a-different-key', scope='global', entry_type='api_key')
    from mc.desk_connect import verification
    assert verification.status('google_ai', 'api_key')['state'] == 'key_stored'


def test_a_deleted_key_reads_not_connected(env, net):
    from mc import secrets_store
    from mc.desk_connect import verification
    client, _, _ = env
    _post(client, _key_draft())
    _verify(client, 'google_ai', 'api_key')
    secrets_store.delete_secret('gemini-api')
    assert verification.status('google_ai', 'api_key')['state'] == 'not_connected'


def test_nothing_stored_means_nothing_to_check(env, net):
    client, _, _ = env
    v = _verify(client, 'google_ai', 'api_key').get_json()
    assert v['state'] == 'not_connected' and net['tests'] == []


def test_a_provider_that_raises_reads_as_a_failed_check(env, net, monkeypatch):
    from mc import desk_oauth
    client, _, _ = env
    _post(client, _key_draft())
    monkeypatch.setattr(desk_oauth, 'test_key', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom ' + SECRET)))
    v = _verify(client, 'google_ai', 'api_key').get_json()
    assert v['state'] == 'check_failed' and SECRET not in json.dumps(v)


def test_verify_is_human_only_and_needs_no_passcode(env, net):
    from mc.state import agent_sessions
    client, calls, _ = env
    _post(client, _key_draft())
    calls['n'] = 0
    assert _verify(client, 'google_ai', 'api_key').status_code == 200 and calls['n'] == 0
    agent_sessions['scheduled-1'] = {'status': 'running', 'trigger_type': 'scheduled', 'project_id': 'p'}
    assert _verify(client, 'google_ai', 'api_key').status_code == 403


def test_check_false_reads_the_status_without_calling_out(env, net):
    client, _, _ = env
    _post(client, _key_draft())
    v = _verify(client, 'google_ai', 'api_key', check=False).get_json()
    assert v['state'] == 'key_stored' and net['tests'] == []


def test_verify_refuses_an_unknown_service_and_a_bad_account(env):
    client, _, _ = env
    assert _verify(client, 'linkedin', 'oauth').status_code == 404
    assert _verify(client, 'x', 'oauth', account_id='../x').status_code == 400


# -- never replace, undo on failure ------------------------------------------------

def test_an_existing_entry_is_refused_not_replaced(env, net):
    from mc import secrets_store
    client, _, _ = env
    secrets_store.set_secret('gemini-api', 'already-here', scope='global', entry_type='api_key')
    r = _post(client, _key_draft())
    assert r.status_code == 409 and r.get_json()['code'] == 'secret_exists'
    assert secrets_store.get_secret_value('gemini-api', consumer='test') == 'already-here'


def test_a_failure_after_the_first_write_leaves_nothing_behind(env, net, monkeypatch):
    from mc import desk_accounts
    client, _, _ = env

    def boom(*a, **k):
        raise RuntimeError('disk full')
    monkeypatch.setattr(desk_accounts, 'create_account', boom)
    r = _post(client, {'url': 'https://x.com/someone', 'method': 'oauth',
                       'fields': {'identity': '@someone', 'client_id': 'cid', 'client_secret': 'csec'}})
    assert r.status_code == 500 and r.get_json()['code'] == 'record_failed'
    assert _vault_names() == [] and net['starts'] == []
    assert 'cid' not in json.dumps(r.get_json())


def test_an_undo_that_fails_is_named(env, net, monkeypatch):
    from mc import desk_accounts, secrets_store
    client, _, _ = env
    monkeypatch.setattr(desk_accounts, 'create_account', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('x')))
    monkeypatch.setattr(secrets_store, 'delete_secret', lambda name: False)
    r = _post(client, {'url': 'https://x.com/someone', 'method': 'oauth',
                       'fields': {'identity': '@someone', 'client_id': 'cid'}})
    assert r.status_code == 500 and 'x.client-id' in r.get_json()['error']


# -- X: an app, an account, a sign-in ----------------------------------------------

def _x_draft(handle='@first', **fields):
    f = {'identity': handle, 'client_id': 'the-client-id'}
    f.update(fields)
    return {'url': 'https://x.com/first', 'method': 'oauth', 'fields': f}


def test_x_saves_the_app_and_the_account_then_starts_its_own_sign_in(env, net):
    client, _, _ = env
    r = _post(client, _x_draft(client_secret='the-client-secret'))
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body['stored'] == ['x.client-id', 'x.client-secret'] and body['account']['identity'] == 'first'
    assert body['status']['state'] == 'not_connected'          # no sign-in finished yet
    # the FIRST X account keeps the legacy singleton sign-in (`oauth.x`): no account arg
    assert body['signin']['state'] == 'started' and net['starts'] == [('x', None)]
    assert 'the-client-secret' not in json.dumps(body) and 'the-client-id' not in json.dumps(body)


def test_a_second_x_account_reuses_the_app_and_signs_in_separately(env, net):
    client, _, _ = env
    a = _post(client, _x_draft('@first'), rid='req-aaaa-0001').get_json()
    b = _post(client, {'url': 'https://x.com/second', 'method': 'oauth',
                       'fields': {'identity': '@second'}}, rid='req-bbbb-0002')
    assert b.status_code == 201, b.get_json()
    b = b.get_json()
    assert b['stored'] == [] and b['account_id'] != a['account_id']
    assert net['starts'] == [('x', None), ('x', b['account_id'])]   # the first keeps the legacy names, the second its own
    assert _vault_names() == ['x.client-id']


def test_x_needs_a_client_id_when_none_is_stored(env, net):
    client, _, _ = env
    r = _post(client, {'url': 'https://x.com/first', 'method': 'oauth', 'fields': {'identity': '@first'}})
    assert r.status_code == 400 and 'Client ID' in r.get_json()['error'] and _services() == []


def test_x_handle_is_validated_before_the_passcode(env, net):
    client, calls, _ = env
    r = _post(client, _x_draft('not a handle'))
    assert r.status_code == 400 and calls['n'] == 0


def test_a_failed_sign_in_start_keeps_the_commit_and_says_setup_failed(env, net):
    client, _, _ = env
    net['start_error'] = 'could not reach x.com'
    r = _post(client, _x_draft())
    assert r.status_code == 201
    body = r.get_json()
    assert body['setup']['state'] == 'failed' and 'x.com' in body['setup']['message']
    assert _vault_names() == ['x.client-id'] and body['account']['identity'] == 'first'
    assert body['status']['state'] != 'verified'


def test_x_has_no_free_check_so_it_is_never_verified(env, net):
    client, _, _ = env
    acc = _post(client, _x_draft()).get_json()['account_id']
    v = _verify(client, 'x', 'oauth', account_id=acc).get_json()
    assert v['state'] == 'not_connected'                  # the sign-in is not finished: nothing to check
    from mc.desk_connect import providers
    x = providers.for_service('x')
    assert x is not None and x.verify('oauth', acc).ok is None  # a finished one has no free check either


def test_replaying_a_request_does_not_start_a_second_sign_in(env, net):
    client, _, _ = env
    first = _post(client, _x_draft())
    second = _post(client, _x_draft())
    assert first.status_code == 201 and second.status_code == 200 and second.get_json()['duplicate'] is True
    assert len(net['starts']) == 1 and 'signin' not in second.get_json()


# -- Higgsfield ---------------------------------------------------------------------

def test_higgsfield_sign_in_takes_no_value_and_starts_the_flow(env, net):
    client, _, _ = env
    r = _post(client, {'url': 'https://higgsfield.ai', 'method': 'oauth', 'fields': {}})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()['signin']['state'] == 'started' and net['starts'] == [('higgsfield', None)]
    assert _vault_names() == []


def test_higgsfield_key_is_stored_as_a_pair_and_not_verified(env, net):
    from mc import secrets_store
    client, _, _ = env
    r = _post(client, {'url': 'https://higgsfield.ai', 'method': 'api_key',
                       'fields': {'key_id': 'KEYID', 'secret': SECRET}})
    assert r.status_code == 201, r.get_json()
    assert r.get_json()['status']['state'] == 'key_stored'
    assert secrets_store.get_username('higgsfield') == 'KEYID'
    assert SECRET not in json.dumps(r.get_json())


def test_higgsfield_key_check_uses_the_free_quote_and_binds_to_the_entry(env, net, monkeypatch):
    from mc import desk_engines
    from mc.desk_connect import verification
    client, _, _ = env
    _post(client, {'url': 'https://higgsfield.ai', 'method': 'api_key', 'fields': {'key_id': 'KEYID', 'secret': SECRET}})
    seen = {}

    def fake(method, url, headers, payload=None, **kw):
        seen.update(method=method, url=url, auth=headers.get('Authorization'))
        return {}
    monkeypatch.setattr(desk_engines, '_json_call', fake)
    v = _verify(client, 'higgsfield', 'api_key').get_json()
    assert v['state'] == 'verified' and '/estimate/' in seen['url'] and seen['method'] == 'POST'
    assert SECRET not in json.dumps(v)
    verification._forget_all_for_tests()
    assert verification.status('higgsfield', 'api_key')['state'] == 'key_stored'


# -- the verification module on its own --------------------------------------------

def test_status_of_an_unknown_method_is_a_404_error():
    from mc.desk_connect import verification
    with pytest.raises(verification.VerifyError) as e:
        verification.status('google_ai', 'oauth')
    assert e.value.status == 404


def test_a_status_that_cannot_be_read_does_not_turn_a_landed_save_into_a_failure(env, net, monkeypatch):
    """The status is read after the writes are committed (review 2026-10-03): if it raised the
    route answered 500 'could not save' for a save that had in fact landed."""
    from mc.desk_connect import provider_commit, verification
    client, _, _ = env

    def boom(*a, **k):
        raise RuntimeError('vault index unreadable')
    monkeypatch.setattr(verification, 'status', boom)
    r = _post(client, _key_draft())
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body['status']['state'] == 'unknown' and 'could not be read' in body['status']['label']
    assert _vault_names() != []                    # the write stayed
    assert SECRET not in json.dumps(body)
