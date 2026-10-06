"""MC-1062/05: consent truth table and real Save authorization, no network."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import desk
from mc.blueprints import desk_connect_permission_routes as routes
from mc.blueprints import local_auth
from mc.desk_connect import permission_policy as policy
from mc.state import agent_sessions

PASSCODE = 'permission-test-passcode'
RID = 'permission-request-001'
READ = {'purpose': 'read_own', 'capability': 'mentions', 'route_id': 'x-browser'}
API_READ = {'purpose': 'read_own', 'capability': 'own_posts', 'route_id': 'x-oauth'}
POST = {'purpose': 'publish', 'capability': 'post', 'route_id': 'x-oauth'}


def account(**over):
    return {'id': 'x-one', 'platform': 'x', 'identity': '@one', 'read_via': 'pane',
            'browser_profile': 'shared-profile', 'credentials': {'oauth_vault': 'one.oauth'}, **over}


def draft(**over):
    return {'account_id': 'x-one', 'service': 'x', 'account_kind': 'account',
            'read': False, 'post': False, 'scopes': [], **over}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    local_auth._LOCAL_AUTH_FAILS.clear()
    local_auth._local_auth_set_passcode(PASSCODE)
    agent_sessions.clear()
    policy._forget_all_for_tests()
    desk._write_store({'accounts': {'x-one': account(), 'x-two': account(id='x-two', identity='@two')},
                       'presences': {'other': {'accounts': [{'id': 'x-one', 'read_via': 'pane'}]}}})
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    calls = []
    real = routes._require_human_passcode

    def counting(data):
        calls.append(True)
        return real(data)
    monkeypatch.setattr(routes, '_require_human_passcode', counting)
    yield app.test_client(), calls
    agent_sessions.clear()
    policy._forget_all_for_tests()
    local_auth._LOCAL_AUTH_FAILS.clear()


def save(client, data=None, *, request_id=RID, passcode=PASSCODE, **extra):
    return client.post('/api/desk/connect/permissions/commit', json={
        'request_id': request_id, 'draft': draft() if data is None else data, 'passcode': passcode, **extra})


def stored():
    return desk._read_store()['accounts']['x-one']


def permitted(rec, operation='read', scope=None, **identity):
    return policy.decision(rec, operation, **(scope or (READ if operation == 'read' else POST)), **identity)


def test_legacy_route_and_token_do_not_become_explicit_consent():
    rec = account(connections={'read_own': {'mentions': {'route_id': 'x-browser'}}})
    assert policy.read_policy(rec)['state'] == 'legacy'
    assert permitted(rec) is None
    assert permitted(rec, 'post') is None
    assert policy.POLICY_KEY not in rec


def test_new_account_default_deny_preserves_connection_details():
    rec = account()
    old = copy.deepcopy(rec)
    policy.initialize_new_account(rec, 'account')
    assert policy.read_policy(rec)['state'] == 'explicit'
    assert permitted(rec) is False and permitted(rec, 'post') is False
    assert {k: v for k, v in rec.items() if k not in (policy.POLICY_KEY, policy.VERSION_KEY)} == old
    with pytest.raises(policy.PolicyError):
        policy.initialize_new_account(rec, 'account')


@pytest.mark.parametrize('read,post,scopes', [(False, False, []), (True, False, [READ]),
                                           (False, True, [POST]), (True, True, [READ, POST])])
def test_explicit_consent_truth_table(env, read, post, scopes):
    client, calls = env
    response = save(client, draft(read=read, post=post, scopes=scopes))
    assert response.status_code == 201
    rec = stored()
    assert permitted(rec) is read and permitted(rec, 'post') is post
    assert len(calls) == 1


def test_exact_scope_does_not_expand_to_paid_api_or_reply(env):
    client, _ = env
    assert save(client, draft(read=True, scopes=[READ])).status_code == 201
    assert permitted(stored(), scope=API_READ) is False
    assert permitted(stored(), scope={**READ, 'capability': 'own_posts'}) is False
    assert permitted(stored(), 'post', {**POST, 'capability': 'reply'}) is False
    assert permitted(stored(), 'read', POST) is False
    assert permitted(stored(), 'unknown') is False


def test_revocation_does_not_need_unbinding_and_route_removal_is_not_new_consent(env):
    client, _ = env
    store = desk._read_store()
    store['accounts']['x-one']['connections'] = {'read_own': {'mentions': {'route_id': 'x-browser'}}}
    desk._write_store(store)
    assert save(client, draft(read=True, scopes=[READ])).status_code == 201
    assert save(client, request_id='revocation-request-001').status_code == 201
    assert permitted(stored()) is False
    assert stored()['connections']['read_own']['mentions']['route_id'] == 'x-browser'
    store = desk._read_store()
    store['accounts']['x-one'].pop('connections')
    desk._write_store(store)
    assert permitted(stored()) is False


@pytest.mark.parametrize('change', [
    lambda r: r.pop(policy.POLICY_KEY),
    lambda r: r.pop(policy.VERSION_KEY),
    lambda r: r.update(permission_policy=None),
    lambda r: r.update(permission_policy=[]),
    lambda r: r.update(permission_policy_version=True),
    lambda r: r[policy.POLICY_KEY].update(version=99),
    lambda r: r[policy.POLICY_KEY].update(fingerprint='wrong'),
    lambda r: r[policy.POLICY_KEY].pop('scopes'),
    lambda r: r[policy.POLICY_KEY].update(read=1),
    lambda r: r[policy.POLICY_KEY].update(account_id='x-two'),
    lambda r: r[policy.POLICY_KEY].update(service='linkedin'),
    lambda r: r[policy.POLICY_KEY].update(account_kind='member'),
    lambda r: r[policy.POLICY_KEY].update(scopes=[{'purpose': []}]),
    lambda r: r[policy.POLICY_KEY].update(approved=True),
])
def test_corrupt_explicit_policy_fails_closed(env, change):
    client, _ = env
    assert save(client, draft(read=True, scopes=[READ])).status_code == 201
    rec = stored()
    change(rec)
    assert policy.read_policy(rec)['state'] == 'invalid'
    assert permitted(rec) is False and permitted(rec, 'post') is False


@pytest.mark.parametrize('explicit', [False, True])
def test_wrong_account_and_kind_never_inherit_consent(env, explicit):
    client, _ = env
    if explicit:
        save(client, draft(read=True, scopes=[READ]))
    assert permitted(stored(), account_id='x-two') is False
    assert permitted(stored(), account_kind='member') is False
    assert permitted(None) is False
    assert permitted({}) is False


@pytest.mark.parametrize('over', [
    {'account_id': 'missing'}, {'service': 'linkedin'}, {'account_kind': 'member'},
    {'read': 1}, {'post': 'false'}, {'read': True},
    {'read': True, 'scopes': [READ, READ]}, {'read': False, 'scopes': [READ]},
    {'read': True, 'scopes': [{**READ, 'capability': 'made-up'}]},
    {'read': True, 'scopes': [{**API_READ, 'capability': 'mentions'}]},
    {'read': True, 'scopes': [{**READ, 'purpose': 'listen_broad', 'capability': 'search'}]},
    {'read': True, 'scopes': [{**READ, 'route_id': 'x-mcp-action'}]},
    {'post': True, 'scopes': [{**POST, 'route_id': 'x-browser'}]},
    {'post': True, 'scopes': [{**POST, 'capability': 'reply'}]},
    {'post': True, 'scopes': [{**POST, 'capability': 'media'}]},
    {'post': True, 'scopes': [{**POST, 'capability': 'article'}]},
    {'scopes': [{}]}, {'scopes': None}, {'scopes': [READ] * 33},
    {'approved': True}, {'authorization': 'reusable-token'}, {'version': 1},
])
def test_invalid_drafts_cost_no_passcode_guess_or_write(env, over):
    client, calls = env
    before = desk.STORE_PATH.read_bytes()
    assert save(client, draft(**over)).status_code in (400, 404, 409)
    assert calls == []
    assert desk.STORE_PATH.read_bytes() == before


def test_kind_from_existing_bindings_is_respected(env):
    client, calls = env
    store = desk._read_store()
    rec = account(id='li-one', platform='linkedin', connections={'read_own': {
        'own_posts': {'account_kind': 'organization', 'route_id': 'linkedin-browser'}}})
    store['accounts']['li-one'] = rec
    desk._write_store(store)
    response = save(client, draft(account_id='li-one', service='linkedin', account_kind='member'))
    assert response.status_code == 409
    assert response.json['code'] == 'wrong_kind'
    assert calls == []


def test_linkedin_deny_valid_but_browser_digest_is_a_separate_policy(env):
    client, _ = env
    store = desk._read_store()
    store['accounts']['li-one'] = account(id='li-one', platform='linkedin', organization_id='123')
    desk._write_store(store)
    data = draft(account_id='li-one', service='linkedin', account_kind='organization')
    assert save(client, data).status_code == 201
    response = save(client, {**data, 'read': True, 'scopes': [{**READ, 'route_id': 'linkedin-browser'}]},
                    request_id='another-permission-request')
    assert response.status_code == 400
    assert response.json['code'] == 'unsupported_scope'


def test_read_only_view_and_preservation_of_unrelated_grants(env):
    client, _ = env
    store = desk._read_store()
    store['accounts']['x-one']['connections'] = {'read_own': {
        'mentions': {'route_id': 'x-browser', 'account_kind': 'account', 'refs': {'browser_profile': 'shared-profile'}},
        'own_posts': {'route_id': 'x-oauth', 'account_kind': 'account', 'scopes': ['old-scope']}}}
    store['accounts']['x-one']['voice'] = 'Keep this voice'
    store['accounts']['x-one']['vault_policy'] = {'allow_unattended': False}
    store['browser_grants'] = {'shared-profile': ['x.com', 'other.example']}
    store['mcp_approvals'] = {'server': {'scope': 'global', 'fingerprint': 'keep'}}
    desk._write_store(store)
    before = desk._read_store()
    assert client.get('/api/desk/connect/permissions/x-one').json['policy']['state'] == 'legacy'
    assert desk._read_store() == before
    assert save(client, draft(read=True, scopes=[READ, API_READ])).status_code == 201
    after = desk._read_store()
    after['accounts']['x-one'].pop(policy.POLICY_KEY)
    after['accounts']['x-one'].pop(policy.VERSION_KEY)
    assert after == before


def test_replay_has_fresh_passcode_no_second_write_and_conflicts(env, monkeypatch):
    client, calls = env
    data = draft(read=True, scopes=[READ])
    first = save(client, data)
    assert first.status_code == 201
    before = desk.STORE_PATH.read_bytes()
    writes = []
    real = desk._write_store
    monkeypatch.setattr(desk, '_write_store', lambda store: (writes.append(True), real(store))[-1])
    assert save(client, data, passcode='wrong').status_code == 403
    duplicate = save(client, data)
    assert duplicate.status_code == 200 and duplicate.json['duplicate'] is True
    assert duplicate.json['policy'] == first.json['policy']
    assert save(client).status_code == 409
    assert len(calls) == 4 and writes == [] and desk.STORE_PATH.read_bytes() == before


def test_failed_write_can_retry_same_request(env, monkeypatch):
    client, _ = env
    real = desk._write_store

    def fail(store):
        raise OSError('test disk failure')
    monkeypatch.setattr(desk, '_write_store', fail)
    assert save(client).status_code == 500
    assert policy.read_policy(stored())['state'] == 'legacy'
    monkeypatch.setattr(desk, '_write_store', real)
    assert save(client).status_code == 201


@pytest.mark.parametrize('trigger', ['dispatch', 'scheduled', 'steward', None])
def test_agents_rejected_before_passcode(env, trigger):
    client, calls = env
    agent_sessions['agent'] = {'project_id': 'mission_control', 'status': 'running', 'trigger_type': trigger}
    before = desk.STORE_PATH.read_bytes()
    assert save(client).status_code == 403
    assert calls == [] and desk.STORE_PATH.read_bytes() == before


def test_forged_origin_is_still_passcode_protected(env):
    client, calls = env
    agent_sessions['agent'] = {'status': 'running', 'trigger_type': 'dispatch'}
    response = client.post('/api/desk/connect/permissions/commit', headers={'Origin': 'http://localhost:5199'},
                           json={'request_id': RID, 'draft': draft(), 'approved': True})
    assert response.status_code == 400 and calls == []
    response = client.post('/api/desk/connect/permissions/commit', headers={'Origin': 'http://localhost:5199'},
                           json={'request_id': RID, 'draft': draft()})
    assert response.status_code == 403 and calls == [True]
    assert policy.read_policy(stored())['state'] == 'legacy'


def test_unconfigured_passcode_and_wrong_passcode_write_nothing(env):
    client, _ = env
    before = desk.STORE_PATH.read_bytes()
    assert save(client, passcode='wrong').status_code == 403
    local_auth.LOCAL_AUTH_PATH.unlink()
    assert save(client).status_code == 403
    assert desk.STORE_PATH.read_bytes() == before


def test_shared_passcode_guess_budget_is_preserved(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda value: None)
    response = save(client)
    assert response.status_code == 429
    assert response.json['error'] == 'too_many_attempts'
    assert policy.read_policy(stored())['state'] == 'legacy'


def test_policy_kind_cannot_change_on_a_previously_edited_linkedin_account(env):
    client, calls = env
    store = desk._read_store()
    store['accounts']['li-one'] = account(id='li-one', platform='linkedin')
    desk._write_store(store)
    data = draft(account_id='li-one', service='linkedin', account_kind='member')
    assert save(client, data).status_code == 201
    response = save(client, {**data, 'account_kind': 'organization'}, request_id='change-kind-request-001')
    assert response.status_code == 409 and response.json['code'] == 'wrong_kind'
    assert len(calls) == 1


@pytest.mark.parametrize('body', [None, [], {'request_id': []}, {'request_id': 'short'},
                                 {'request_id': RID, 'draft': []}])
def test_malformed_submissions_fail_before_passcode(env, body):
    client, calls = env
    assert client.post('/api/desk/connect/permissions/commit', json=body).status_code == 400
    assert calls == []


@pytest.mark.parametrize('extra', [{'approved': True}, {'authorization_token': 'token'}, {'fingerprint': 'fake'}])
def test_caller_approval_fields_rejected(env, extra):
    client, calls = env
    assert save(client, **extra).status_code == 400
    assert calls == []


def test_fingerprint_tracks_identity_exact_scopes_and_version():
    a = draft(read=True, scopes=[READ])
    assert policy.fingerprint(a) != policy.fingerprint({**a, 'account_id': 'x-two'})
    assert policy.fingerprint(a) != policy.fingerprint({**a, 'scopes': [API_READ]})
    assert policy.fingerprint(a) != policy.fingerprint({**a, 'read': False})


def test_identity_rechecked_under_write_lock(env):
    client, _ = env
    rid, clean = policy.clean_submission({'request_id': RID, 'draft': draft(), 'passcode': PASSCODE})
    store = desk._read_store()
    store['accounts']['x-one']['platform'] = 'linkedin'
    desk._write_store(store)
    with pytest.raises(policy.PolicyError, match='different service'):
        policy.commit(rid, clean)
    assert policy.POLICY_KEY not in stored()
