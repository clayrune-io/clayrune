"""MC-1126: real passcode/OAuth refresh/store paths; every platform endpoint fake."""
import io
import json
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor

import pytest
from flask import Flask

from mc import desk, desk_oauth, desk_pieces, desk_post_delete, secrets_store
from mc.blueprints import desk_routes, desk_post_delete_routes, local_auth

URL = '/api/desk/pieces/p1/versions/v1/delete-post'
PROOF = {'passcode': 'test-passcode'}
ORIGIN = {'Origin': 'http://localhost:5199'}
RECEIPT = {'platform': 'x', 'post_id': '123456789', 'item_id': 'v1',
           'permalink': 'https://x.com/test/status/123456789', 'posted_at': '2026-10-01T01:00:00Z'}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    local_auth._local_auth_set_passcode(PROOF['passcode'])
    local_auth._LOCAL_AUTH_FAILS.clear()
    monkeypatch.setattr(desk_post_delete_routes, 'is_unattended_caller', lambda: False)
    # Every account has its own OAuth name, so using the singleton would fail.
    account = {'id': 'acct-x', 'platform': 'x', 'capability': 'direct',
               'credentials': {'oauth_vault': 'oauth.x.acct-x'}}
    version = {'id': 'v1', 'account_id': 'acct-x', 'state': 'verified_published',
               'receipt': dict(RECEIPT), 'approved': {'by': 'human'}, 'body': 'Hello'}
    store = desk._empty_store()
    store.update(accounts={'acct-x': account}, pieces={'p1': {
        'id': 'p1', 'campaign_id': 'c1', 'project_id': 'alpha', 'title': 'Hello',
        'kind': 'post', 'versions': [version], 'body': 'Hello'}})
    desk._write_store(store)
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(desk_routes.bp)
    calls, reads, writes = [], [], []
    records = {'oauth.x.acct-x': json.dumps({
        'access_token': 'fake-old-access', 'refresh_token': 'fake-old-refresh',
        'expires_at': int(time.time()) - 1, 'token_endpoint': 'https://fake.test/token',
        'client_id': 'fake-app'})}

    def read(name, **kwargs):
        reads.append((name, kwargs))
        if name not in records:
            raise secrets_store.SecretNotFound(name)
        return records[name]

    def write(name, value, **kwargs):
        writes.append((name, kwargs))
        records[name] = value

    monkeypatch.setattr(secrets_store, 'get_secret_value', read)
    monkeypatch.setattr(secrets_store, 'set_secret', write)
    monkeypatch.setattr(secrets_store, 'list_secrets', lambda: [
        {'name': n, 'scope': 'alpha', 'allow_unattended': False} for n in records])
    monkeypatch.setattr(desk_oauth, '_meta', lambda service, account_id=None: next(
        (s for s in secrets_store.list_secrets() if s['name'] == desk_oauth.vault_name(service, account_id)), None))
    monkeypatch.setattr(desk_oauth, '_client_auth', lambda *a, **kw: None)
    from mc import desk_oauth_app_refs
    monkeypatch.setattr(desk_oauth_app_refs, 'bound', lambda name: False)

    def refresh(method, url, **kwargs):
        assert method == 'POST' and url == 'https://fake.test/token'
        calls.append(('refresh', kwargs))
        assert b'fake-old-refresh' in kwargs['body']
        return 200, {}, json.dumps({'access_token': 'fake-new-access',
                                    'refresh_token': 'fake-new-refresh', 'expires_in': 7200}).encode()

    monkeypatch.setattr(desk_oauth, '_http', refresh)

    def delete(req, **kwargs):
        calls.append(('delete', req))
        assert req.method == 'DELETE' and req.full_url == 'https://api.x.com/2/tweets/123456789'
        assert req.headers['Authorization'] == 'Bearer fake-new-access'
        assert json.loads(records['oauth.x.acct-x'])['refresh_token'] == 'fake-new-refresh'
        return io.BytesIO(b'{"data":{"deleted":true}}')

    monkeypatch.setattr(desk_post_delete.urllib.request, 'urlopen', delete)
    return app.test_client(), calls, records, reads, writes


def mutate(fn):
    with desk._store_lock:
        store = desk._read_store()
        fn(store, store['pieces']['p1']['versions'][0])
        desk._write_store(store)


def test_delete_refreshes_and_saves_rotation_before_delete_and_keeps_history(env):
    client, calls, records, reads, writes = env
    response = client.post(URL, json=PROOF, headers=ORIGIN)
    assert response.status_code == 200, response.json
    ver = response.json['versions'][0]
    assert ver['deleted']['by'] == 'human' and ver['deleted']['at'].endswith('Z')
    assert ver['receipt'] == RECEIPT and ver['state'] == 'verified_published'
    assert [c[0] for c in calls] == ['refresh', 'delete']
    assert writes[0][0] == 'oauth.x.acct-x' and writes[0][1]['scope'] == 'alpha'
    assert writes[0][1]['allow_unattended'] is False
    assert all(n == 'oauth.x.acct-x' and kw['consumer'] == 'desk_post_delete' and
               kw['project_id'] == 'alpha' and not kw['unattended'] and kw['internal'] for n, kw in reads)
    assert 'fake-' not in json.dumps(response.json)
    assert PROOF['passcode'] not in desk.STORE_PATH.read_text()
    assert client.get('/api/desk/pieces').json[0]['versions'][0]['deleted'] == ver['deleted']
    assert client.post(URL, json=PROOF, headers=ORIGIN).json == response.json
    assert len(calls) == 2  # idempotency returns the existing record without tokens/network
    assert client.post(URL, json={}, headers=ORIGIN).status_code == 403  # proof still required


@pytest.mark.parametrize('body', [{}, {'passcode': 'wrong'}, {'passcode': 5}, [], 'bad'])
def test_forged_origin_cannot_skip_passcode(env, body):
    client, calls, _, reads, _ = env
    before = desk.STORE_PATH.read_bytes()
    response = client.post(URL, json=body, headers=ORIGIN)
    assert response.status_code == 403
    assert not calls and not reads and desk.STORE_PATH.read_bytes() == before


def test_no_configured_passcode_fails_closed(env, monkeypatch, tmp_path):
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'absent.json')
    response = env[0].post(URL, json=PROOF, headers=ORIGIN)
    assert response.status_code == 403 and response.json['error'] == 'passcode_required'
    assert not env[1]


def test_unattended_refused_even_with_passcode(env, monkeypatch):
    monkeypatch.setattr(desk_post_delete_routes, 'is_unattended_caller', lambda: True)
    response = env[0].post(URL, json=PROOF)
    assert response.status_code == 403 and 'unattended' in response.json['error']
    assert not env[1] and not env[3]


@pytest.mark.parametrize('trigger', ['dispatch', 'scheduled', None])
def test_real_agent_classification_refuses_without_origin_even_with_proof(env, monkeypatch, trigger):
    from mc import unattended
    monkeypatch.setattr(unattended, 'agent_sessions', {
        'delete-test': {'status': 'running', 'trigger_type': trigger, 'project_id': 'alpha'}})
    monkeypatch.setattr(desk_post_delete_routes, 'is_unattended_caller', unattended.is_unattended_caller)
    before = desk.STORE_PATH.read_bytes()
    response = env[0].post(URL, json=PROOF)
    assert response.status_code == 403 and 'unattended' in response.json['error']
    assert not env[1] and not env[3] and desk.STORE_PATH.read_bytes() == before


@pytest.mark.parametrize('trigger', ['dispatch', 'manual'])
@pytest.mark.parametrize('headers', [{}, ORIGIN])
def test_real_agent_classification_never_replaces_retyped_passcode(env, monkeypatch, trigger, headers):
    from mc import unattended
    monkeypatch.setattr(unattended, 'agent_sessions', {
        'delete-test': {'status': 'running', 'trigger_type': trigger, 'project_id': 'alpha'}})
    monkeypatch.setattr(desk_post_delete_routes, 'is_unattended_caller', unattended.is_unattended_caller)
    before = desk.STORE_PATH.read_bytes()
    # Origin is forgeable and manual agent chats are exempt from the session
    # heuristic. Neither grants proof or permits any token/platform access.
    response = env[0].post(URL, json={}, headers=headers)
    assert response.status_code == 403
    assert not env[1] and not env[3] and desk.STORE_PATH.read_bytes() == before


def test_passcode_throttle_is_shared(env, monkeypatch):
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda p: None)
    assert env[0].post(URL, json=PROOF, headers=ORIGIN).status_code == 429
    assert not env[1]


@pytest.mark.parametrize('state', ['drafting', 'needs_review', 'approved', 'scheduled', 'sending',
                                  'submitted', 'you_reported', 'unknown_outcome', 'archived', 'failed'])
def test_only_verified_published_can_delete(env, state):
    mutate(lambda s, v: v.update(state=state))
    assert env[0].post(URL, json=PROOF, headers=ORIGIN).status_code == 409
    assert not env[1] and not env[3]


@pytest.mark.parametrize('receipt', [None, {}, {'platform': 'linkedin', 'post_id': '123'},
                                     {'platform': 'x', 'post_id': '../users'},
                                     {'platform': 'x', 'post_id': 123}])
def test_target_is_only_a_valid_stored_receipt(env, receipt):
    mutate(lambda s, v: v.update(receipt=receipt))
    assert env[0].post(URL, json=PROOF, headers=ORIGIN).status_code == 409
    assert not env[1] and not env[3]


@pytest.mark.parametrize('platform', ['linkedin', 'blog', None])
def test_unsupported_is_explicit_and_uses_no_token(env, platform):
    mutate(lambda s, v: s['accounts']['acct-x'].update(platform=platform))
    response = env[0].post(URL, json=PROOF, headers=ORIGIN)
    assert response.status_code == 422 and response.json['code'] == 'unsupported'
    assert not env[1] and not env[3]


def test_missing_piece_version_or_account_does_not_fall_back_to_another_login(env):
    client = env[0]
    assert client.post(URL.replace('p1', 'absent'), json=PROOF, headers=ORIGIN).status_code == 404
    assert client.post(URL.replace('v1', 'absent'), json=PROOF, headers=ORIGIN).status_code == 404
    mutate(lambda s, v: s['accounts'].clear())
    assert client.post(URL, json=PROOF, headers=ORIGIN).status_code == 422
    assert not env[1] and not env[3]


def test_platform_refusal_shows_reason_and_never_records_deleted(env, monkeypatch):
    def refused(req, **kw):
        raise urllib.error.HTTPError(req.full_url, 403, 'Forbidden', {},
                                     io.BytesIO(b'{"detail":"You cannot delete this post; fake-new-access"}'))
    monkeypatch.setattr(desk_post_delete.urllib.request, 'urlopen', refused)
    response = env[0].post(URL, json=PROOF, headers=ORIGIN)
    assert response.status_code == 502 and response.json['code'] == 'platform_refused'
    assert 'You cannot delete this post' in response.json['error']
    assert 'fake-new-access' not in response.json['error']
    assert 'deleted' not in desk_pieces.get_stored_piece('p1')['versions'][0]
    assert json.loads(env[2]['oauth.x.acct-x'])['refresh_token'] == 'fake-new-refresh'


@pytest.mark.parametrize('body', [b'{"data":{"deleted":false},"errors":[{"detail":"denied"}]}',
                                  b'{"data":{"deleted":"true"}}', b'null', b'[]'])
def test_no_confirmed_deletion_is_not_success(env, monkeypatch, body):
    monkeypatch.setattr(desk_post_delete.urllib.request, 'urlopen', lambda *a, **kw: io.BytesIO(body))
    response = env[0].post(URL, json=PROOF, headers=ORIGIN)
    assert response.status_code == 502
    assert 'deleted' not in desk_pieces.get_stored_piece('p1')['versions'][0]


@pytest.mark.parametrize('failure', [TimeoutError(), urllib.error.URLError('offline'), ValueError('bad json')])
def test_uncertain_outcome_is_reported_honestly(env, monkeypatch, failure):
    def fail(*a, **kw):
        raise failure
    monkeypatch.setattr(desk_post_delete.urllib.request, 'urlopen', fail)
    response = env[0].post(URL, json=PROOF, headers=ORIGIN)
    assert response.status_code == 502 and response.json['code'] == 'unknown_outcome'
    assert 'may already be deleted' in response.json['error']
    assert 'deleted' not in desk_pieces.get_stored_piece('p1')['versions'][0]


def test_refresh_rejected_stores_needs_signin_and_does_not_delete(env, monkeypatch):
    monkeypatch.setattr(desk_oauth, '_http', lambda *a, **kw: (401, {}, b'{"error":"invalid_grant"}'))
    response = env[0].post(URL, json=PROOF, headers=ORIGIN)
    assert response.status_code == 409 and 'sign in again' in response.json['error']
    assert json.loads(env[2]['oauth.x.acct-x'])['needs_signin']
    assert not env[1]


def test_concurrent_deletes_issue_one_platform_call_and_preserve_other_edits(env, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = desk_post_delete.urllib.request.urlopen

    def delayed(*a, **kw):
        entered.set()
        assert release.wait(5)
        return original(*a, **kw)

    monkeypatch.setattr(desk_post_delete.urllib.request, 'urlopen', delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(desk_post_delete.delete_post, 'p1', 'v1')
        assert entered.wait(5)
        b = pool.submit(desk_post_delete.delete_post, 'p1', 'v1')
        mutate(lambda s, v: s['pieces']['p1'].update(title='Concurrent title edit'))
        release.set()
        assert a.result(5) == b.result(5)
    assert [x[0] for x in env[1]] == ['refresh', 'delete']
    assert desk_pieces.get_piece('p1')['title'] == 'Concurrent title edit'


def test_disk_failure_after_platform_success_says_post_is_deleted(env, monkeypatch):
    monkeypatch.setattr(desk, '_write_store', lambda s: (_ for _ in ()).throw(OSError('disk full')))
    response = env[0].post(URL, json=PROOF, headers=ORIGIN)
    assert response.status_code == 500 and response.json['code'] == 'record_failed'
    assert 'X confirmed deletion' in response.json['error']
    assert 'deleted' not in desk_pieces.get_stored_piece('p1')['versions'][0]


def test_generic_patch_cannot_forge_or_clear_deleted_record(env):
    assert env[0].patch(URL.removesuffix('/delete-post'), json={'deleted': {'by': 'human'}}).status_code == 400
    assert env[0].post(URL, json=PROOF, headers=ORIGIN).status_code == 200
    assert env[0].patch(URL.removesuffix('/delete-post'), json={'deleted': None}).status_code == 400


def test_legacy_bound_account_refreshes_its_own_singleton(env, monkeypatch):
    records = env[2]
    records['oauth.x'] = records.pop('oauth.x.acct-x')
    mutate(lambda s, v: s['accounts']['acct-x']['credentials'].update(oauth_vault='oauth.x'))
    monkeypatch.setattr(desk_oauth, 'status', lambda service: {'state': 'connected'})

    def delete(req, **kw):
        assert req.headers['Authorization'] == 'Bearer fake-new-access'
        assert json.loads(records['oauth.x'])['refresh_token'] == 'fake-new-refresh'
        return io.BytesIO(b'{"data":{"deleted":true}}')

    monkeypatch.setattr(desk_post_delete.urllib.request, 'urlopen', delete)
    assert env[0].post(URL, json=PROOF, headers=ORIGIN).status_code == 200
    assert all(name == 'oauth.x' for name, kw in env[3])
    assert env[4][0][0] == 'oauth.x'


def test_missing_per_account_signin_never_uses_a_singleton(env):
    env[2]['oauth.x'] = env[2].pop('oauth.x.acct-x')
    response = env[0].post(URL, json=PROOF, headers=ORIGIN)
    assert response.status_code == 409 and 'not signed in' in response.json['error']
    assert not env[1]
    assert all(name == 'oauth.x.acct-x' for name, kw in env[3])
