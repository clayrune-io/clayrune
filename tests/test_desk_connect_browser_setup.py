"""Browser connection setup without a developer app (`mc/desk_connect/browser_setup.py`,
`desk_connect_browser_setup_routes.py`; MC-1062 ticket 03).

Pinned:

  * a fresh X account with no Client ID reaches a saved browser setup: account, named profile and,
    optionally, a login NAME; nothing else is granted (no binding, no read setting, no legacy profile,
    no presence, no pane, no vault value read) and no OAuth token is minted;
  * two X identities stay separate (own account, own profile, own login); a LinkedIn member and a
    Company Page are separate accounts; LinkedIn permits profile/login sharing regardless of kind;
  * a replayed save answers once, another draft under the same id is 409, an identical re-save of an
    account is `unchanged`, a different one is `already_set_up`, an account bound through purposes is
    `already_bound`, a taken handle is `account_exists`;
  * a wrong or missing passcode, an unattended caller and a malformed draft write nothing (and the
    last two never cost a passcode guess);
  * a typed login is created once (create-only), is not in any response, store or log, and is removed
    again, with a new account, when the account write fails;
  * `signin_fill.bound_refs` finds the login and profile a setup saved, and a purpose binding wins;
  * `account_attach.target` names the account's own OAuth references, writes nothing, and a setup
    leaves those references alone.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
from flask import Flask

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

PASSCODE = 'dash-passcode-1'
USER = 'ron.levy@example.test'
PW = 'S3cretPW-QQTOP-24680'


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import desk as _desk
    from mc import desk_engagement
    from mc import secrets_store
    from mc.blueprints import desk_connect_browser_setup_routes as routes
    from mc.blueprints import local_auth
    from mc.desk_connect import browser_setup, permission_policy, signin_fill, signin_login_store
    from mc.state import agent_sessions
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    local_auth._LOCAL_AUTH_FAILS.clear()
    secrets_store._dispensed.clear()
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    monkeypatch.setattr(_desk, 'STORE_PATH', tmp_path / 'desk.json')
    agent_sessions.clear()
    browser_setup._forget_all_for_tests()
    permission_policy._forget_all_for_tests()
    local_auth._local_auth_set_passcode(PASSCODE)
    logs: list = []
    monkeypatch.setattr(browser_setup, '_log', lambda msg, *a, **k: logs.append(str(msg)))
    monkeypatch.setattr(signin_login_store, '_log', lambda msg, *a, **k: logs.append(str(msg)))

    def boom(*_a, **_k):
        raise AssertionError('a browser setup must not do this')
    monkeypatch.setattr(desk_engagement, 'PaneXReader', boom)         # no agent read starts
    monkeypatch.setattr(signin_fill.Pane, 'find', boom)               # no pane is looked at
    monkeypatch.setattr(signin_fill.Pane, 'open', boom)
    monkeypatch.setattr(secrets_store, 'get_secret_value', boom)      # no vault value is ever read
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    calls = {'n': 0}
    real = routes._require_human_passcode

    def counting(data):
        calls['n'] += 1
        return real(data)
    monkeypatch.setattr(routes, '_require_human_passcode', counting)
    yield app.test_client(), calls, logs, tmp_path
    agent_sessions.clear()
    browser_setup._forget_all_for_tests()
    permission_policy._forget_all_for_tests()


# -- helpers ----------------------------------------------------------------------

def _rev(service):
    from mc.desk_connect import registry
    return registry.profile(service)['revision']


ROUTE = {'x': 'x-browser', 'linkedin': 'linkedin-browser'}


def _draft(service, account, kind=None, **over):
    d = {'service': service, 'revision': _rev(service), 'route_id': ROUTE[service], 'account': account,
         'account_kind': kind or ('account' if service == 'x' else 'member')}
    d.update(over)
    return d


def _new(identity, **extra):
    return {'new': {'identity': identity, **extra}}


def _save(client, draft, rid='req-browser-0001', passcode=PASSCODE, **extra):
    body = {'request_id': rid, 'draft': draft, **extra}
    if passcode is not None:
        body['passcode'] = passcode
    return client.post('/api/desk/connect/browser-setup/commit', json=body)


def _store():
    from mc import desk as _desk
    with _desk._store_lock:
        return copy.deepcopy(_desk._read_store())


def _store_bytes():
    from mc import desk as _desk
    p = _desk.STORE_PATH
    return p.read_bytes() if p.exists() else b''


def _rec(account_id):
    return _store()['accounts'][account_id]


def _mk(platform, identity, org=None):
    from mc import desk_accounts
    acc = desk_accounts.create_account(platform, identity)
    if org:
        desk_accounts.update_account(acc['id'], {'organization_id': org})
    return acc


def _login(name, user=USER):
    from mc import secrets_store
    secrets_store.set_secret(name, PW, username=user, entry_type=secrets_store.ENTRY_LOGIN)
    return name


def _vault_names():
    from mc import secrets_store
    return {s['name'] for s in secrets_store.list_secrets()}


# -- the fresh X path --------------------------------------------------------------

def test_a_fresh_x_account_with_no_app_saves_a_browser_setup(env):
    client, _, _, _ = env
    assert not any(n.startswith('x.client') for n in _vault_names())            # no developer app anywhere
    r = _save(client, _draft('x', _new('@ron')))
    assert r.status_code == 201, r.get_json()
    out = r.get_json()
    assert out['ok'] and out['account_created'] and not out['duplicate'] and not out['unchanged']
    assert out['browser_profile'] == 'x-ron' and out['profile_state'] in ('new', 'exists', 'unknown') and out['login'] is None
    rec = _rec(out['account_id'])
    assert rec['platform'] == 'x' and rec['identity'] == 'ron'
    assert rec['browser_setup']['route_id'] == 'x-browser' and rec['browser_setup']['refs'] == {'browser_profile': 'x-ron'}
    assert not any(n.startswith('x.client') for n in _vault_names())            # still none: no app was needed or made


def test_a_setup_grants_nothing_and_leaves_the_read_route_alone(env):
    from mc import desk
    client, _, _, _ = env
    before = _store()
    out = _save(client, _draft('x', _new('ron'))).get_json()
    after = _store()
    rec = after['accounts'][out['account_id']]
    assert 'connections' not in rec and 'read_via' not in rec and 'browser_profile' not in rec     # no permission, no read setting
    assert desk.account_read_via(rec) == 'pane'                                                       # the default, untouched
    assert after['presences'] == before['presences'] and after['campaigns'] == before['campaigns']   # on no board, in no campaign
    assert after['engagement'] == before['engagement']
    assert set(rec) - {'id', 'platform', 'identity', 'label', 'capability', 'voice', 'created_at', 'credentials',
                       'browser_setup', 'permission_policy', 'permission_policy_version'} == set()


@pytest.mark.parametrize('service,kind,identity,extra', [
    ('x', 'account', 'ron', {}),
    ('linkedin', 'member', 'Ron Levy', {}),
    ('linkedin', 'organization', 'Clayrune', {}),
    ('linkedin', 'organization', 'Clayrune', {'organization_id': '42'}),
])
def test_new_browser_accounts_persist_explicit_default_deny(env, service, kind, identity, extra):
    from mc.desk_connect import permission_policy
    client, _, _, _ = env
    r = _save(client, _draft(service, _new(identity, **extra), kind))
    assert r.status_code == 201, r.get_json()
    aid = r.get_json()['account_id']
    state = permission_policy.read_policy(_rec(aid), account_id=aid, account_kind=kind)
    assert state['state'] == 'explicit' and state['account_kind'] == kind
    assert state['read'] is False and state['post'] is False and state['scopes'] == []


@pytest.mark.parametrize('explicit', [False, True])
def test_browser_setup_preserves_reused_account_policy(env, explicit):
    from mc.desk_connect import permission_policy
    client, _, _, _ = env
    acc = _mk('x', 'ron')
    if explicit:
        draft = {'account_id': acc['id'], 'service': 'x', 'account_kind': 'account',
                 'read': True, 'post': False,
                 'scopes': [{'purpose': 'read_own', 'capability': 'mentions', 'route_id': 'x-browser'}]}
        permission_policy.commit('req-policy-0001', draft)
    before = permission_policy.read_policy(_rec(acc['id']))
    r = _save(client, _draft('x', {'id': acc['id']}))
    assert r.status_code == 201, r.get_json()
    assert permission_policy.read_policy(_rec(acc['id'])) == before
    r = _save(client, _draft('x', {'id': acc['id']}), rid='req-browser-0002')
    assert r.status_code == 200 and r.get_json()['unchanged'] is True
    assert permission_policy.read_policy(_rec(acc['id'])) == before


def test_default_deny_initialization_failure_rolls_back_new_account_and_login(env, monkeypatch):
    from mc.desk_connect import permission_policy
    client, _, _, _ = env

    def fail(*_a, **_k):
        raise RuntimeError('policy initialization failed')

    monkeypatch.setattr(permission_policy, 'initialize_new_account', fail)
    r = _save(client, _draft('x', _new('ron'), new_login=_typed()))
    assert r.status_code == 500 and r.get_json()['code'] == 'record_failed'
    assert _store()['accounts'] == {} and 'x.ron' not in _vault_names()


def test_an_existing_account_is_reused_not_duplicated(env):
    client, _, _, _ = env
    acc = _mk('x', 'ron')
    out = _save(client, _draft('x', {'id': acc['id']})).get_json()
    assert out['account_id'] == acc['id'] and not out['account_created']
    assert len(_store()['accounts']) == 1


def test_an_existing_login_is_bound_by_name_and_never_read(env):
    client, _, _, _ = env
    _login('x.ron')
    out = _save(client, _draft('x', _new('ron'), login='x.ron')).get_json()
    assert out['login'] == 'x.ron' and not out['login_created']
    assert _rec(out['account_id'])['browser_setup']['refs'] == {'browser_profile': 'x-ron', 'login': 'x.ron'}


@pytest.mark.parametrize('login,code,status', [
    ('x.missing', 'login_not_found', 404),
    ('oauth.x', 'bad_login', 400),             # Clayrune's own sign-in entry
])
def test_a_login_that_cannot_be_used_is_refused_before_the_passcode(env, login, code, status):
    client, calls, _, _ = env
    before = _store_bytes()
    r = _save(client, _draft('x', _new('ron'), login=login))
    assert r.status_code == status and r.get_json()['code'] == code
    assert calls['n'] == 0 and _store_bytes() == before


def test_an_api_key_is_not_a_login(env):
    from mc import secrets_store
    client, calls, _, _ = env
    secrets_store.set_secret('x.api-key', PW, entry_type='api_key')
    r = _save(client, _draft('x', _new('ron'), login='x.api-key'))
    assert r.status_code == 400 and r.get_json()['code'] == 'not_a_login' and calls['n'] == 0


# -- typed login ------------------------------------------------------------------

def _typed(name='x.ron', **over):
    return {'name': name, 'username': USER, 'value': PW, **over}


def test_a_typed_login_is_created_once_and_never_echoed(env):
    client, _, logs, _ = env
    r = _save(client, _draft('x', _new('ron'), new_login=_typed()))
    assert r.status_code == 201
    out = r.get_json()
    assert out['login'] == 'x.ron' and out['login_created'] is True
    assert 'x.ron' in _vault_names()
    blob = r.get_data(as_text=True) + _store_bytes().decode('utf-8') + '\n'.join(logs)
    assert PW not in blob and USER not in blob


def test_a_typed_login_name_that_exists_is_refused_before_the_passcode_and_kept(env):
    from mc import secrets_store
    client, calls, _, _ = env
    _login('x.ron')
    before = _store_bytes()
    r = _save(client, _draft('x', _new('ron'), new_login=_typed()))
    assert r.status_code == 409 and r.get_json()['code'] == 'secret_exists' and calls['n'] == 0
    assert _store_bytes() == before
    assert secrets_store.list_secrets()[0]['username'] == USER


def test_a_login_cannot_be_both_stored_and_typed(env):
    client, calls, _, _ = env
    _login('x.one')
    r = _save(client, _draft('x', _new('ron'), login='x.one', new_login=_typed('x.two')))
    assert r.status_code == 400 and calls['n'] == 0 and 'x.two' not in _vault_names()


def test_a_failed_account_write_removes_the_new_account_and_the_typed_login(env, monkeypatch):
    from mc.desk_connect import browser_setup
    client, _, logs, _ = env

    real_apply = browser_setup._apply

    def fail(*_a, **_k):
        raise RuntimeError('disk full')
    monkeypatch.setattr(browser_setup, '_apply', fail)
    r = _save(client, _draft('x', _new('ron'), new_login=_typed()))
    assert r.status_code == 500 and r.get_json()['code'] == 'record_failed'
    assert _store()['accounts'] == {} and 'x.ron' not in _vault_names()
    assert PW not in r.get_data(as_text=True) + '\n'.join(logs)
    monkeypatch.setattr(browser_setup, '_apply', real_apply)
    # the same request is not remembered as done: it can be retried
    assert _save(client, _draft('x', _new('ron'), new_login=_typed())).status_code == 201


def test_a_failed_account_write_keeps_an_existing_account_and_login_untouched(env, monkeypatch):
    from mc.desk_connect import browser_setup
    client, _, _, _ = env
    acc = _mk('x', 'ron')
    _login('x.pre')
    monkeypatch.setattr(browser_setup, '_apply', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    assert _save(client, _draft('x', {'id': acc['id']}, login='x.pre')).status_code == 500
    assert acc['id'] in _store()['accounts'] and 'browser_setup' not in _rec(acc['id']) and 'x.pre' in _vault_names()


# -- two identities ---------------------------------------------------------------

def test_two_x_identities_stay_separate(env):
    client, _, _, _ = env
    a = _save(client, _draft('x', _new('ron')), rid='req-a-000001').get_json()
    b = _save(client, _draft('x', _new('ronco')), rid='req-b-000001').get_json()
    assert a['account_id'] != b['account_id'] and a['browser_profile'] == 'x-ron' and b['browser_profile'] == 'x-ronco'
    r = _save(client, _draft('x', _new('third'), browser_profile='x-ron'), rid='req-c-000001')
    assert r.status_code == 409 and r.get_json()['code'] == 'profile_in_use'
    assert len(_store()['accounts']) == 2


def test_an_x_login_belongs_to_one_account(env):
    client, _, _, _ = env
    _login('x.shared')
    assert _save(client, _draft('x', _new('ron'), login='x.shared'), rid='req-a-000001').status_code == 201
    r = _save(client, _draft('x', _new('ronco'), login='x.shared'), rid='req-b-000001')
    assert r.status_code == 409 and r.get_json()['code'] == 'login_in_use'
    assert len(_store()['accounts']) == 1


def test_a_taken_handle_is_an_explicit_conflict(env):
    client, _, _, _ = env
    _mk('x', 'Ron')
    r = _save(client, _draft('x', _new('@ron')))
    assert r.status_code == 409 and r.get_json()['code'] == 'account_exists'


def test_an_x_account_may_not_take_another_accounts_oauth_profile(env):
    client, _, _, _ = env
    first = _mk('x', 'first')
    legacy = _rec(first['id'])['credentials']['oauth_profile']          # `desk-x`: the first account's sign-in profile
    r = _save(client, _draft('x', _new('second'), browser_profile=legacy))
    assert r.status_code == 409 and r.get_json()['code'] == 'profile_in_use'


def test_a_profile_may_be_shared_across_sites(env):
    client, _, _, _ = env
    assert _save(client, _draft('x', _new('ron'), browser_profile='main'), rid='req-a-000001').status_code == 201
    r = _save(client, _draft('linkedin', _new('Ron Levy'), browser_profile='main'), rid='req-b-000001')
    assert r.status_code == 201 and r.get_json()['shared_with'] == []        # a different site's cookies: not an identity clash


@pytest.mark.parametrize('identity', ['', 'has space', 'x' * 16, '@@'])
def test_a_bad_x_handle_is_refused_before_the_passcode(env, identity):
    client, calls, _, _ = env
    r = _save(client, _draft('x', _new(identity)))
    assert r.status_code == 400 and calls['n'] == 0 and _store()['accounts'] == {}


# -- LinkedIn ---------------------------------------------------------------------

def test_a_linkedin_member_and_a_page_are_separate_accounts(env):
    client, _, _, _ = env
    _login('linkedin.ron')
    m = _save(client, _draft('linkedin', _new('Ron Levy'), 'member', login='linkedin.ron'), rid='req-m-000001').get_json()
    p = _save(client, _draft('linkedin', _new('Clayrune', organization_id='1234567'), 'organization', login='linkedin.ron'),
              rid='req-p-000001')
    assert p.status_code == 201
    p = p.get_json()
    assert m['account_id'] != p['account_id'] and m['account_kind'] == 'member' and p['account_kind'] == 'organization'
    assert p['shared_with'] == [m['account_id']]                          # the Page is reached through the member's login: disclosed
    assert _rec(m['account_id']).get('organization_id') is None
    assert _rec(p['account_id'])['organization_id'] == '1234567'
    assert _rec(p['account_id'])['browser_setup']['account_kind'] == 'organization'


def test_two_linkedin_members_may_share_a_profile_or_login(env):
    client, _, _, _ = env
    _login('linkedin.ron')
    first = _save(client, _draft('linkedin', _new('Ron Levy'), login='linkedin.ron'), rid='req-a-000001').get_json()
    r = _save(client, _draft('linkedin', _new('Someone Else'), login='linkedin.ron'), rid='req-b-000001')
    assert r.status_code == 201 and r.get_json()['shared_with'] == [first['account_id']]
    r = _save(client, _draft('linkedin', _new('Third Member'), browser_profile='linkedin-ron-levy'), rid='req-c-000001')
    assert r.status_code == 201 and r.get_json()['shared_with'] == [first['account_id']]


@pytest.mark.parametrize('kind', ['member', 'organization', None])
@pytest.mark.parametrize('other_kind', ['member', 'organization', None])
@pytest.mark.parametrize('refs', [
    {'browser_profile': 'own', 'login': 'linkedin.shared'},
    {'browser_profile': 'shared'},
    {'browser_profile': 'shared', 'login': 'linkedin.shared'},
])
def test_linkedin_sharing_never_depends_on_kind(kind, other_kind, refs):
    from mc.desk_connect import browser_setup
    account = {'platform': 'linkedin', 'identity': 'Existing', 'browser_setup': {
        'route_id': 'linkedin-browser', 'account_kind': other_kind,
        'refs': {'browser_profile': 'shared', 'login': 'linkedin.shared'}}}
    before = copy.deepcopy(account)
    assert browser_setup._check_conflicts({'acct-existing': account}, None, 'linkedin', kind, refs) == ['acct-existing']
    assert account == before


def test_linkedin_legacy_kind_shares_login_without_backfilling(env):
    from mc import desk
    client, _, _, _ = env
    page = _mk('linkedin', 'Clayrune')
    _login('linkedin')
    with desk._store_lock:
        store = desk._read_store()
        store['accounts'][page['id']]['connections'] = {'read_own': {'mentions': {
            'route_id': 'linkedin-browser', 'refs': {'browser_profile': 'linked-company', 'login': 'linkedin'}}}}
        desk._write_store(store)
    before = _rec(page['id'])
    r = _save(client, _draft('linkedin', _new(USER), login='linkedin', browser_profile='linkedin-personal'))
    assert r.status_code == 201, r.get_json()
    assert r.get_json()['shared_with'] == [page['id']]
    assert _rec(page['id']) == before                  # no kind inferred, no legacy data changed


def test_unknown_linkedin_kind_allows_profile_sharing(env):
    from mc import desk
    client, _, _, _ = env
    page = _mk('linkedin', 'Clayrune')
    legacy_profile = 'linked-company'
    with desk._store_lock:
        store = desk._read_store()
        store['accounts'][page['id']]['browser_profile'] = legacy_profile
        desk._write_store(store)
    before = _rec(page['id'])
    r = _save(client, _draft('linkedin', _new(USER), browser_profile=legacy_profile))
    assert r.status_code == 201 and r.get_json()['shared_with'] == [page['id']]
    assert _rec(page['id']) == before


@pytest.mark.parametrize('reference,code,what', [('login', 'login_in_use', 'login'),
                                              ('browser_profile', 'profile_in_use', 'browser profile')])
def test_conflict_names_service_identity_and_setup(env, reference, code, what):
    service, identity = 'x', 'ron'
    client, _, _, _ = env
    _login(service + '.shared')
    a = _save(client, _draft(service, _new(identity), login=service + '.shared')).get_json()
    value = service + '.shared' if reference == 'login' else a['browser_profile']
    r = _save(client, _draft(service, _new('SomeoneElse'), **{reference: value}), rid='req-conflict-0001')
    assert r.status_code == 409 and r.get_json()['code'] == code
    message = r.get_json()['error']
    assert f'already belongs to {identity}.' in message
    assert f'Each X account needs its own {what}.' in message
    assert 'Back to Setup' in message
    assert _rec(a['account_id'])['label'] not in message


def test_a_member_cannot_be_saved_on_an_account_with_a_page_id(env):
    client, calls, _, _ = env
    page = _mk('linkedin', 'Clayrune', org='42')
    r = _save(client, _draft('linkedin', {'id': page['id']}, 'member'))
    assert r.status_code == 400 and r.get_json()['code'] == 'kind_mismatch' and calls['n'] == 0


def test_an_organization_id_only_comes_with_a_new_page(env):
    client, calls, _, _ = env
    r = _save(client, _draft('linkedin', _new('Ron Levy', organization_id='42'), 'member'))
    assert r.status_code == 400 and calls['n'] == 0
    r = _save(client, _draft('linkedin', _new('Clayrune', organization_id='abc'), 'organization'))
    assert r.status_code == 400 and calls['n'] == 0
    assert _store()['accounts'] == {}


def test_a_page_without_an_organization_id_is_saved_with_none(env):
    client, _, _, _ = env
    out = _save(client, _draft('linkedin', _new('Clayrune'), 'organization')).get_json()
    assert 'organization_id' not in _rec(out['account_id'])                # never inferred from the member's login


def test_the_kind_once_saved_is_never_swapped(env):
    client, _, _, _ = env
    out = _save(client, _draft('linkedin', _new('Clayrune'), 'organization'), rid='req-a-000001').get_json()
    r = _save(client, _draft('linkedin', {'id': out['account_id']}, 'member'), rid='req-b-000001')
    assert r.status_code == 409 and r.get_json()['code'] == 'already_set_up'
    assert _rec(out['account_id'])['browser_setup']['account_kind'] == 'organization'


def test_linkedin_has_no_publishing_gate_change(env):
    from mc import desk_accounts
    client, _, _, _ = env
    out = _save(client, _draft('linkedin', _new('Clayrune'), 'organization')).get_json()
    assert desk_accounts.LINKEDIN_ORG_POSTING_APPROVED is False
    assert desk_accounts.get_account(out['account_id'])['publish']['ready'] is False


# -- replay and conflicts ---------------------------------------------------------

def test_a_replay_answers_the_first_result_without_writing(env):
    client, _, _, _ = env
    d = _draft('x', _new('ron'))
    first = _save(client, d)
    snap = _store_bytes()
    again = _save(client, d)
    assert first.status_code == 201 and again.status_code == 200
    a, b = first.get_json(), again.get_json()
    assert b['duplicate'] is True and b['account_id'] == a['account_id'] and _store_bytes() == snap
    assert len(_store()['accounts']) == 1


def test_the_same_request_id_with_another_draft_is_409(env):
    client, _, _, _ = env
    _save(client, _draft('x', _new('ron')))
    r = _save(client, _draft('x', _new('ronco')))
    assert r.status_code == 409 and r.get_json()['code'] == 'request_id_reused'
    assert len(_store()['accounts']) == 1


def test_a_replayed_typed_login_is_not_stored_twice(env):
    client, _, _, _ = env
    d = _draft('x', _new('ron'), new_login=_typed())
    assert _save(client, d).status_code == 201
    r = _save(client, d)
    assert r.status_code == 200 and r.get_json()['duplicate'] is True


def test_a_new_request_for_an_account_with_the_same_setup_is_unchanged(env):
    client, _, _, _ = env
    out = _save(client, _draft('x', _new('ron')), rid='req-a-000001').get_json()
    snap = _store_bytes()
    r = _save(client, _draft('x', {'id': out['account_id']}), rid='req-b-000001')
    assert r.status_code == 200 and r.get_json()['unchanged'] is True and _store_bytes() == snap


def test_a_different_setup_for_a_saved_account_is_refused(env):
    client, _, _, _ = env
    out = _save(client, _draft('x', _new('ron')), rid='req-a-000001').get_json()
    snap = _store_bytes()
    r = _save(client, _draft('x', {'id': out['account_id']}, browser_profile='another'), rid='req-b-000001')
    assert r.status_code == 409 and r.get_json()['code'] == 'already_set_up' and _store_bytes() == snap


def test_an_account_bound_through_purposes_is_not_set_up_twice(env):
    from mc import desk
    client, _, _, _ = env
    acc = _mk('x', 'ron')
    with desk._store_lock:
        st = desk._read_store()
        st['accounts'][acc['id']]['connections'] = {'read_own': {'mentions': {
            'service': 'x', 'route_id': 'x-browser', 'account_kind': 'account', 'refs': {'browser_profile': 'pane1'}}}}
        desk._write_store(st)
    r = _save(client, _draft('x', {'id': acc['id']}))
    assert r.status_code == 409 and r.get_json()['code'] == 'already_bound'


def test_an_account_on_another_service_is_refused(env):
    client, _, _, _ = env
    li = _mk('linkedin', 'Ron Levy')
    r = _save(client, _draft('x', {'id': li['id']}))
    assert r.status_code == 400 and r.get_json()['code'] == 'wrong_platform'
    assert _save(client, _draft('x', {'id': 'acct_nope'})).status_code == 404


# -- gates and shape --------------------------------------------------------------

@pytest.mark.parametrize('passcode', ['wrong-passcode', None, ''])
def test_a_wrong_or_missing_passcode_writes_nothing(env, passcode):
    client, calls, _, _ = env
    before = _store_bytes()
    r = _save(client, _draft('x', _new('ron'), new_login=_typed()), passcode=passcode)
    assert r.status_code in (401, 403) and calls['n'] == 1
    assert _store_bytes() == before and 'x.ron' not in _vault_names()
    assert not (_store_bytes() and _store()['accounts'])


def test_an_unattended_agent_is_refused_before_anything(env, monkeypatch):
    from mc.blueprints import desk_connect_browser_setup_routes as routes
    client, calls, _, _ = env
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: True)
    before = _store_bytes()
    r = _save(client, _draft('x', _new('ron'), new_login=_typed()))
    assert r.status_code == 403 and r.get_json()['code'] == 'human_required'
    assert calls['n'] == 0 and _store_bytes() == before and 'x.ron' not in _vault_names()


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(extra=1),
    lambda d: d.update(service='blog'),
    lambda d: d.update(service='reddit'),
    lambda d: d.update(route_id='x-manual'),                       # declares no sign-in page
    lambda d: d.update(route_id='x-oauth'),                        # an API route, not a sign-in
    lambda d: d.update(route_id='nope'),
    lambda d: d.update(account_kind='member'),                     # not an X kind
    lambda d: d.update(account={'id': 'a', 'new': {'identity': 'x'}}),
    lambda d: d.update(account={'new': {'identity': 'ron', 'oops': 1}}),
    lambda d: d.update(account=None),
    lambda d: d.update(browser_profile='Bad Name!'),
    lambda d: d.update(browser_profile='../x'),
    lambda d: d.update(login=7),
    lambda d: d.update(new_login={'name': 'x.ron'}),               # no password
    lambda d: d.update(new_login={'name': 'oauth.x', 'username': 'u', 'value': 'v'}),
    lambda d: d.update(revision=999),
])
def test_malformed_drafts_are_refused_and_cost_no_passcode_guess(env, mutate):
    client, calls, _, _ = env
    d = _draft('x', _new('ron'))
    mutate(d)
    before = _store_bytes()
    r = _save(client, d)
    assert r.status_code in (400, 404, 409) and calls['n'] == 0 and _store_bytes() == before


def test_a_bad_request_id_and_a_non_object_body_are_refused(env):
    client, calls, _, _ = env
    assert _save(client, _draft('x', _new('ron')), rid='x').status_code == 400
    assert client.post('/api/desk/connect/browser-setup/commit', data='nope').status_code == 400
    assert client.post('/api/desk/connect/browser-setup/commit', json={'request_id': 'req-0000001', 'draft': []}).status_code == 400
    assert client.get('/api/desk/connect/browser-setup/commit').status_code == 405 and calls['n'] == 0


def test_a_save_starts_no_agent_read_or_post(env):
    """The fixture makes every reader, pane and vault-value read raise; a save that touched one would 500."""
    client, _, _, _ = env
    _login('x.ron')
    assert _save(client, _draft('x', _new('ron'), login='x.ron')).status_code == 201


# -- what consumes the setup ------------------------------------------------------

def test_the_fill_engine_finds_what_a_setup_saved(env):
    from mc.desk_connect import signin_fill
    client, _, _, _ = env
    _login('x.ron')
    out = _save(client, _draft('x', _new('ron'), login='x.ron', browser_profile='x-main')).get_json()
    assert signin_fill.bound_refs(out['account_id'], 'x', 'x-browser') == {'browser_profile': 'x-main', 'login': 'x.ron'}
    assert signin_fill.pick_login('x', 'x-browser', out['account_id'], None, None) == ('x.ron', 'x-main')
    with pytest.raises(signin_fill.FillError) as e:                                     # another login name in the request is still refused
        signin_fill.pick_login('x', 'x-browser', out['account_id'], 'x.other', None)
    assert e.value.code == 'login_not_bound'


def test_a_setup_without_a_login_cannot_be_filled_and_says_so(env):
    from mc.desk_connect import signin_fill
    client, _, _, _ = env
    out = _save(client, _draft('x', _new('ron'))).get_json()
    with pytest.raises(signin_fill.FillError) as e:
        signin_fill.bound_refs(out['account_id'], 'x', 'x-browser')
    assert e.value.code == 'login_not_bound'


def test_a_purpose_binding_wins_over_a_setup_in_the_fill_engine(env):
    from mc import desk
    from mc.desk_connect import signin_fill
    client, _, _, _ = env
    out = _save(client, _draft('linkedin', _new('Ron Levy'), browser_profile='li-setup')).get_json()
    with desk._store_lock:
        st = desk._read_store()
        st['accounts'][out['account_id']]['connections'] = {'read_own': {'mentions': {
            'route_id': 'linkedin-browser', 'account_kind': 'member', 'refs': {'browser_profile': 'li-bound', 'login': 'linkedin.bound'}}}}
        desk._write_store(st)
    refs = signin_fill.bound_refs(out['account_id'], 'linkedin', 'linkedin-browser')
    assert refs == {'browser_profile': 'li-bound', 'login': 'linkedin.bound'}


def test_the_record_reads_a_malformed_setup_as_absent():
    from mc.desk_connect import browser_setup_record as rec
    assert rec.of({'browser_setup': 'nope'}) is None and rec.of({'browser_setup': {'route_id': 1, 'refs': {}}}) is None
    assert rec.saved_refs({'browser_setup': {'route_id': 'r', 'refs': {'login': 'a', 'password': 'x', 'browser_profile': 3}}}, 'r') == {'login': 'a'}
    assert rec.saved_refs({'browser_setup': {'route_id': 'r', 'refs': {'login': 'a'}}}, 'other') == {}
    assert rec.held_refs({'connections': 'nope', 'credentials': None}) == {'profiles': set(), 'logins': set()}


# -- attaching a later connection -------------------------------------------------

def test_attach_names_the_accounts_own_oauth_references_and_writes_nothing(env):
    from mc.desk_connect import account_attach
    client, _, _, _ = env
    first = _mk('x', 'first')
    acc = _mk('x', 'ron')
    out = _save(client, _draft('x', {'id': acc['id']}, browser_profile='x-ron')).get_json()
    snap = _store_bytes()
    t = account_attach.target('x', acc['id'], identity='@Ron')
    assert _store_bytes() == snap
    assert t['account_id'] == acc['id'] == out['account_id'] and t['identity'] == 'ron' and t['account_kind'] == 'account'
    assert t['oauth'] == {'oauth_vault': f'oauth.x.{acc["id"]}', 'oauth_profile': f'desk-x-{acc["id"]}'}
    assert t['oauth'] != account_attach.target('x', first['id'])['oauth']
    assert t['browser'] == {'route_id': 'x-browser', 'account_kind': 'account', 'refs': {'browser_profile': 'x-ron'}}


def test_a_setup_leaves_the_accounts_oauth_references_alone(env):
    from mc.desk_connect import account_attach
    client, _, _, _ = env
    acc = _mk('x', 'ron')
    before = account_attach.target('x', acc['id'])['oauth']
    _save(client, _draft('x', {'id': acc['id']}))
    assert account_attach.target('x', acc['id'])['oauth'] == before and before is not None


@pytest.mark.parametrize('service,account_id,identity,code,status', [
    ('x', 'acct_missing', None, 'account_not_found', 404),
    ('linkedin', 'LIVE', None, 'wrong_platform', 400),
    ('x', 'LIVE', 'someoneelse', 'identity_mismatch', 409),
    ('x', None, None, 'bad_account', 400),
    (None, 'LIVE', None, 'bad_account', 400),
])
def test_attach_refuses_the_wrong_account(env, service, account_id, identity, code, status):
    from mc.desk_connect import account_attach
    acc = _mk('x', 'ron')
    with pytest.raises(account_attach.AttachError) as e:
        account_attach.target(service, acc['id'] if account_id == 'LIVE' else account_id, identity=identity)
    assert e.value.code == code and e.value.status == status


def test_the_blueprint_is_registered_in_the_server():
    src = (REPO / 'server.py').read_text(encoding='utf-8')
    assert 'desk_connect_browser_setup_routes' in src and 'register_blueprint(_bp_desk_connect_browser_setup.bp)' in src
    json.dumps({})        # (keeps the import used when this file is trimmed)
