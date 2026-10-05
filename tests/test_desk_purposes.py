"""Desk routes by purpose, slice P2 of docs/DESK_SERVICE_PROFILES_SPEC.md: every route of a known
service grouped by purpose, and one account bound to different routes per purpose.

Pinned:

  * the executor table names real routes, and a route Clayrune cannot run is described but
    never bound as if it could (manual, restricted, MCP, a purpose that is not per account);
  * API publish plus pane own reads bind on one account; two routes inside `read_own` bind
    capability by capability and never infer the missing half; split own reads leave the
    legacy single `read_via` alone and say so, agreeing ones sync it;
  * a LinkedIn member and a Company Page, and two X identities, hold separate bindings;
  * credentials, voice, organization id, read settings and profile names that the draft does
    not change are untouched; a vault entry is bound by NAME and must exist; no value is in
    any response;
  * nothing is written by the view, a bad draft, a wrong passcode or an unattended caller;
    the same request id answers once, another draft under it is 409;
  * verification is derived per capability and partial: one proved capability of four reads
    `partial`; a changed credential or binding clears exactly what rested on it;
  * `LINKEDIN_ORG_POSTING_APPROVED` stays False and no LinkedIn route becomes bindable for publish;
  * a service whose sign-in IS its MCP (Higgsfield) shows no "not available" MCP row.
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
SECRET = 'PLAINTEXT-VALUE-SHOULD-NEVER-APPEAR'


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / '.clayrune'))
    monkeypatch.setenv('CLAYRUNE_SECRETS_KEY_BACKEND', 'file')
    monkeypatch.delenv('CLAUDE_CODE_SESSION_ID', raising=False)
    from mc import desk as _desk
    from mc import secrets_store
    from mc.blueprints import desk_connect_purpose_routes as routes
    from mc.blueprints import local_auth
    from mc.desk_connect import purpose_bindings, purpose_verification
    from mc.state import agent_sessions
    monkeypatch.setattr(local_auth, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    local_auth._LOCAL_AUTH_FAILS.clear()
    secrets_store._dispensed.clear()
    secrets_store._unlocked_key = None
    secrets_store._lock_notified = False
    secrets_store._key_mismatch = False
    monkeypatch.setattr(_desk, 'STORE_PATH', tmp_path / 'desk.json')
    agent_sessions.clear()
    purpose_bindings._forget_all_for_tests()
    purpose_verification._forget_all_for_tests()
    local_auth._local_auth_set_passcode(PASSCODE)
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    calls = {'n': 0}
    real = routes._require_human_passcode

    def counting(data):
        calls['n'] += 1
        return real(data)
    monkeypatch.setattr(routes, '_require_human_passcode', counting)
    yield app.test_client(), calls, tmp_path
    agent_sessions.clear()
    purpose_bindings._forget_all_for_tests()
    purpose_verification._forget_all_for_tests()


class FakePane:
    """Stands in for `PaneXReader`: no browser. `ok` decides whether the mentions page reads."""
    ok = True
    profiles_present = True

    def __init__(self, project_id, profile, **_kw):
        self.profile = profile

    def capability(self):
        if self.profiles_present:
            return {'connected': True, 'reason': None, 'short': None}
        return {'connected': False, 'reason': 'no saved browser profile', 'short': 'sign in'}

    def fetch_mentions(self, *, since_id, known_posts):
        if not FakePane.ok:
            raise RuntimeError('the mentions page did not look like X notifications')
        return {'items': [], 'resources': 0, 'cursor': None, 'account': None}

    def close(self):
        pass


@pytest.fixture()
def pane(monkeypatch):
    from mc import desk_engagement
    FakePane.ok = True
    FakePane.profiles_present = True
    monkeypatch.setattr(desk_engagement, 'PaneXReader', FakePane)
    return FakePane


def _x(identity='@ron'):
    from mc import desk_accounts
    return desk_accounts.create_account('x', identity)


def _li(identity, org=None):
    from mc import desk_accounts
    acc = desk_accounts.create_account('linkedin', identity)
    if org:
        desk_accounts.update_account(acc['id'], {'organization_id': org})
    return acc


def _rev(service):
    from mc.desk_connect import registry
    return registry.profile(service)['revision']


def _draft(service, account, kind, bindings, **over):
    d = {'service': service, 'revision': _rev(service), 'account': account, 'account_kind': kind, 'bindings': bindings}
    d.update(over)
    return d


def _b(purpose, route, caps, **extra):
    return {'purpose': purpose, 'route_id': route, 'capabilities': caps, **extra}


def _save(client, draft, rid='req-purpose-0001', passcode=PASSCODE):
    return client.post('/api/desk/connect/purpose/commit', json={'request_id': rid, 'draft': draft, 'passcode': passcode})


def _store_bytes():
    from mc import desk as _desk
    p = _desk.STORE_PATH
    return p.read_bytes() if p.exists() else b''


def _rec(account_id):
    from mc import desk as _desk
    with _desk._store_lock:
        return copy.deepcopy(_desk._read_store()['accounts'][account_id])


def _presence_copies(account_id):
    from mc import desk as _desk
    with _desk._store_lock:
        st = _desk._read_store()
    return [a for p in st['presences'].values() for a in (p.get('accounts') or [])
            if isinstance(a, dict) and a.get('channel_id') == account_id]


# -- the executor table and the screen's data --------------------------------------------

def test_executor_table_names_real_routes():
    from mc.desk_connect import registry, route_readiness
    assert route_readiness.EXECUTORS
    for (service, route_id, purpose), via in route_readiness.EXECUTORS.items():
        prof = registry.profile(service)
        route = next(r for r in prof['routes'] if r['id'] == route_id)
        assert any(p['id'] == purpose and route_id in p['routes'] for p in prof['purposes']), (service, route_id, purpose)
        assert via in route_readiness.READ_VIA_OF
        assert (route['transport'] == 'browser') == (via == 'pane')


def test_every_route_of_x_and_linkedin_is_shown_grouped_by_purpose(env):
    client, _, _ = env
    for sid in ('x', 'linkedin'):
        out = client.post('/api/desk/connect/purposes', json={'service': sid}).get_json()
        from mc.desk_connect import registry
        want = {r['id'] for r in registry.profile(sid)['routes']}
        shown = {r['id'] for p in out['purposes'] for r in p['routes']}
        assert shown == want - {'x-docs-mcp'} if sid == 'x' else shown == want
        for p in out['purposes']:
            for r in p['routes']:
                assert {'cost', 'requirements', 'evidence', 'status', 'coverage', 'auth', 'limits'} <= set(r)
        assert {p['id'] for p in out['purposes']} == {'publish', 'read_own', 'listen_broad'}


def test_x_publish_and_read_own_say_what_clayrune_can_run(env):
    client, _, _ = env
    out = client.post('/api/desk/connect/purposes', json={'service': 'x'}).get_json()
    pur = {p['id']: {r['id']: r['status'] for r in p['routes']} for p in out['purposes']}
    assert pur['publish']['x-oauth']['executes'] and pur['publish']['x-oauth']['bindable']
    assert not pur['publish']['x-manual']['bindable'] and 'own browser' in pur['publish']['x-manual']['why']
    assert not pur['publish']['x-mcp-action']['bindable']
    assert pur['read_own']['x-browser']['executes'] and pur['read_own']['x-oauth']['executes']
    assert not pur['listen_broad']['x-browser']['bindable']          # broad listening is the Desk's, not an account's
    order = [r['id'] for p in out['purposes'] if p['id'] == 'read_own' for r in p['routes']]
    assert order.index('x-browser') < order.index('x-mcp-action')   # runnable first, then the rest


def test_linkedin_has_no_bindable_publish_route_and_the_org_switch_is_off(env):
    from mc import desk_accounts
    client, _, _ = env
    assert desk_accounts.LINKEDIN_ORG_POSTING_APPROVED is False
    out = client.post('/api/desk/connect/purposes', json={'service': 'linkedin'}).get_json()
    publish = next(p for p in out['purposes'] if p['id'] == 'publish')
    assert publish['routes'] and not any(r['status']['bindable'] for r in publish['routes'])
    li = next(r for r in publish['routes'] if r['id'] == 'linkedin-oauth')
    assert li['support'] == 'restricted' and 'limits' in li['status']['why'].lower() or not li['status']['bindable']
    read = next(p for p in out['purposes'] if p['id'] == 'read_own')
    browser = next(r for r in read['routes'] if r['id'] == 'linkedin-browser')
    assert browser['status']['bindable'] and not browser['status']['executes'] and 'cannot read' in browser['status']['why']


def test_unknown_service_is_404_and_view_writes_nothing(env):
    client, _, _ = env
    _x()
    before = _store_bytes()
    assert client.post('/api/desk/connect/purposes', json={'service': 'nope'}).status_code == 404
    assert client.post('/api/desk/connect/purposes', json={'service': 'x'}).status_code == 200
    assert _store_bytes() == before


def test_view_has_vault_metadata_only_and_marks_the_entry_named_for_the_service(env):
    from mc import secrets_store
    client, _, _ = env
    secrets_store.set_secret('linkedin', SECRET, username='ron@example.com', entry_type='login')
    secrets_store.set_secret('plausible.api-key', SECRET, entry_type='api_key')
    raw = client.post('/api/desk/connect/purposes', json={'service': 'linkedin'}).get_data(as_text=True)
    assert SECRET not in raw
    vault = {v['name']: v for v in json.loads(raw)['vault']}
    assert vault['linkedin']['matches'] and vault['linkedin']['has_username'] and not vault['plausible.api-key']['matches']
    assert set(vault['linkedin']) == {'name', 'entry_type', 'has_username', 'scope', 'matches'}


# -- binding: API publish plus pane own reads --------------------------------------------

def test_api_publish_and_pane_own_reads_on_one_account(env, pane):
    client, calls, _ = env
    acc = _x()
    d = _draft('x', {'id': acc['id']}, 'account', [
        _b('publish', 'x-oauth', ['post']),
        _b('read_own', 'x-browser', ['mentions', 'replies'], browser_profile='x-ron')])
    r = _save(client, d)
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert body['account_id'] == acc['id'] and body['legacy_read']['read_via'] == 'pane' and not body['legacy_read']['split']
    rec = _rec(acc['id'])
    assert rec['connections']['publish']['post']['route_id'] == 'x-oauth'
    assert rec['connections']['publish']['post']['refs'] == {'oauth_vault': rec['credentials']['oauth_vault'],
                                                             'oauth_profile': rec['credentials']['oauth_profile']}
    assert rec['connections']['read_own']['mentions']['route_id'] == 'x-browser'
    assert rec['connections']['read_own']['mentions']['refs'] == {'browser_profile': 'x-ron'}
    assert rec['read_via'] == 'pane' and rec['browser_profile'] == 'x-ron'
    assert all(c['read_via'] == 'pane' for c in _presence_copies(acc['id'])) or not _presence_copies(acc['id'])
    assert calls['n'] == 1


def test_two_routes_inside_read_own_split_the_capabilities_and_leave_read_via_alone(env, pane):
    client, _, _ = env
    acc = _x()
    r = _save(client, _draft('x', {'id': acc['id']}, 'account', [
        _b('read_own', 'x-browser', ['mentions'], browser_profile='x-ron'),
        _b('read_own', 'x-oauth', ['own_posts'])]))
    assert r.status_code == 201, r.get_json()
    assert r.get_json()['legacy_read']['split'] is True
    rec = _rec(acc['id'])
    assert rec['connections']['read_own']['mentions']['route_id'] == 'x-browser'
    assert rec['connections']['read_own']['own_posts']['route_id'] == 'x-oauth'
    assert 'post_metrics' not in rec['connections']['read_own']        # the missing half is never inferred
    assert 'read_via' not in rec or rec['read_via'] != 'api'            # engagement still reads one way
    from mc.desk_connect import purpose_bindings
    groups = {(g['purpose'], g['route_id']): g['capabilities'] for g in purpose_bindings.groups(rec)}
    assert groups == {('read_own', 'x-browser'): ['mentions'], ('read_own', 'x-oauth'): ['own_posts']}


def test_api_read_own_syncs_legacy_read_via_to_api(env, pane):
    client, _, _ = env
    acc = _x()
    r = _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('read_own', 'x-oauth', ['own_posts'])]))
    assert r.status_code == 201 and r.get_json()['legacy_read']['read_via'] == 'api'
    assert _rec(acc['id'])['read_via'] == 'api'


def test_a_capability_the_route_is_not_documented_to_cover_is_refused(env):
    client, _, _ = env
    acc = _x()
    before = _store_bytes()
    r = _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('read_own', 'x-oauth', ['mentions'])]))
    assert r.status_code == 400 and r.get_json()['code'] == 'capability_not_covered'
    assert _store_bytes() == before


@pytest.mark.parametrize('purpose,route,caps,code', [
    ('publish', 'x-manual', ['post'], 'route_not_bindable'),
    ('publish', 'x-mcp-action', ['post'], 'route_not_bindable'),
    ('listen_broad', 'x-browser', ['search'], 'invalid'),
    ('publish', 'x-browser', ['post'], 'route_not_offered'),
])
def test_routes_clayrune_cannot_run_are_not_bound(env, purpose, route, caps, code):
    client, _, _ = env
    acc = _x()
    before = _store_bytes()
    r = _save(client, _draft('x', {'id': acc['id']}, 'account', [_b(purpose, route, caps)]))
    assert r.status_code == 400 and r.get_json()['code'] == code, r.get_json()
    assert _store_bytes() == before


def test_a_browser_route_with_no_reader_is_saved_and_says_so(env, pane):
    client, _, _ = env
    acc = _li('Ron Levy')
    r = _save(client, _draft('linkedin', {'id': acc['id']}, 'member', [_b('read_own', 'linkedin-browser', ['own_posts'], browser_profile='li-ron')]))
    assert r.status_code == 201, r.get_json()
    from mc.desk_connect import purpose_view
    view = purpose_view.view('linkedin')
    bound = view['accounts'][0]['bound'][0]
    assert bound['setup'] == 'pending_runtime' and 'cannot run this route' in bound['reason']


def test_linkedin_company_page_publishing_is_not_enabled_by_a_binding(env):
    from mc import desk_accounts
    client, _, _ = env
    acc = _li('Clayrune Page', org='123456')
    r = _save(client, _draft('linkedin', {'id': acc['id']}, 'organization', [_b('publish', 'linkedin-oauth', ['post'])]))
    assert r.status_code == 400 and r.get_json()['code'] == 'route_not_bindable'
    assert desk_accounts.LINKEDIN_ORG_POSTING_APPROVED is False
    assert desk_accounts.get_account(acc['id'])['publish']['ready'] is False


# -- separate identities ------------------------------------------------------------------

def test_linkedin_member_and_company_page_are_separate_accounts_with_separate_bindings(env, pane):
    client, _, _ = env
    member = _li('Ron Levy')
    page = _li('Clayrune', org='998877')
    r1 = _save(client, _draft('linkedin', {'id': member['id']}, 'member',
                              [_b('read_own', 'linkedin-browser', ['own_posts'], browser_profile='li-ron')]), rid='req-member-0001')
    r2 = _save(client, _draft('linkedin', {'id': page['id']}, 'organization',
                              [_b('read_own', 'linkedin-browser', ['own_posts', 'post_metrics'], browser_profile='li-clayrune')]), rid='req-page-00001')
    assert r1.status_code == 201 and r2.status_code == 201, (r1.get_json(), r2.get_json())
    m, p = _rec(member['id']), _rec(page['id'])
    assert m['connections']['read_own']['own_posts']['account_kind'] == 'member'
    assert p['connections']['read_own']['post_metrics']['account_kind'] == 'organization'
    assert m['connections']['read_own']['own_posts']['refs'] == {'browser_profile': 'li-ron'}
    assert p['connections']['read_own']['own_posts']['refs'] == {'browser_profile': 'li-clayrune'}
    assert 'post_metrics' not in m['connections']['read_own'] and p['organization_id'] == '998877'


def test_a_company_page_account_cannot_be_bound_as_a_member_and_a_member_cannot_gain_page_coverage(env, pane):
    client, _, _ = env
    page = _li('Clayrune', org='998877')
    r = _save(client, _draft('linkedin', {'id': page['id']}, 'member', [_b('read_own', 'linkedin-browser', ['own_posts'])]))
    assert r.status_code == 400 and r.get_json()['code'] == 'kind_mismatch'
    member = _li('Ron Levy')
    r = _save(client, _draft('linkedin', {'id': member['id']}, 'member', [_b('read_own', 'linkedin-browser', ['post_metrics'])]), rid='req-member-0002')
    assert r.status_code == 400 and r.get_json()['code'] == 'capability_not_covered'      # only the Page has documented metrics here
    ok = _save(client, _draft('linkedin', {'id': member['id']}, 'member', [_b('read_own', 'linkedin-browser', ['own_posts'])]), rid='req-member-0003')
    assert ok.status_code == 201
    r = _save(client, _draft('linkedin', {'id': member['id']}, 'organization', [_b('read_own', 'linkedin-browser', ['own_posts'])]), rid='req-member-0004')
    assert r.status_code == 409 and r.get_json()['code'] == 'account_kind_changed'


def test_a_new_linkedin_account_is_made_by_the_save_and_only_by_the_save(env, pane):
    from mc import desk_accounts
    client, _, _ = env
    before = len(desk_accounts.list_accounts())
    d = _draft('linkedin', {'new': {'identity': 'Ron Levy', 'label': 'Ron on LinkedIn'}}, 'member',
               [_b('read_own', 'linkedin-browser', ['own_posts'], browser_profile='li-ron')])
    assert _save(client, d, passcode='wrong').status_code == 403
    assert len(desk_accounts.list_accounts()) == before                       # nothing before a good passcode
    r = _save(client, d)
    assert r.status_code == 201 and r.get_json()['account_created'] is True
    accounts = desk_accounts.list_accounts()
    assert len(accounts) == before + 1
    assert _rec(r.get_json()['account_id'])['connections']['read_own']['own_posts']['account_kind'] == 'member'


def test_a_failed_binding_removes_the_account_it_made(env, monkeypatch):
    from mc import desk_accounts
    from mc.desk_connect import purpose_bindings
    client, _, _ = env

    def boom(*a, **k):
        raise purpose_bindings.BindError('boom', 500, 'x')
    monkeypatch.setattr(purpose_bindings, '_apply', boom)
    before = len(desk_accounts.list_accounts())
    d = _draft('linkedin', {'new': {'identity': 'Ron Levy'}}, 'member', [_b('read_own', 'linkedin-browser', ['own_posts'])])
    r = _save(client, d)
    assert r.status_code == 500
    assert len(desk_accounts.list_accounts()) == before


def test_x_accounts_are_made_by_their_sign_in_not_here(env):
    client, _, _ = env
    r = _save(client, _draft('x', {'new': {'identity': '@new'}}, 'account', [_b('read_own', 'x-oauth', ['own_posts'])]))
    assert r.status_code == 400 and 'its own sign-in' in r.get_json()['error']


def test_two_x_identities_keep_their_own_sign_in_and_profile(env, pane):
    client, _, _ = env
    a, b = _x('@ron'), _x('@clayrune')
    ra = _save(client, _draft('x', {'id': a['id']}, 'account', [_b('publish', 'x-oauth', ['post']),
               _b('read_own', 'x-browser', ['mentions'], browser_profile='x-ron')]), rid='req-x-a-00001')
    rb = _save(client, _draft('x', {'id': b['id']}, 'account', [_b('publish', 'x-oauth', ['post']),
               _b('read_own', 'x-browser', ['mentions'], browser_profile='x-clayrune')]), rid='req-x-b-00001')
    assert ra.status_code == 201 and rb.status_code == 201
    ra_rec, rb_rec = _rec(a['id']), _rec(b['id'])
    assert ra_rec['credentials']['oauth_vault'] != rb_rec['credentials']['oauth_vault']
    assert ra_rec['connections']['publish']['post']['refs']['oauth_vault'] == ra_rec['credentials']['oauth_vault']
    assert rb_rec['connections']['publish']['post']['refs']['oauth_vault'] == rb_rec['credentials']['oauth_vault']
    assert ra_rec['browser_profile'] == 'x-ron' and rb_rec['browser_profile'] == 'x-clayrune'
    assert ra_rec['connections']['publish']['post']['fingerprint'] != rb_rec['connections']['publish']['post']['fingerprint']


# -- preservation -------------------------------------------------------------------------

def test_what_the_draft_does_not_change_is_preserved(env, pane):
    from mc import desk_accounts
    client, _, _ = env
    acc = _x()
    desk_accounts.update_account(acc['id'], {'voice': 'builder voice', 'read_via': 'api'})
    before = _rec(acc['id'])
    r = _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('publish', 'x-oauth', ['post'])]))
    assert r.status_code == 201
    after = _rec(acc['id'])
    for k in ('credentials', 'voice', 'read_via', 'label', 'identity', 'capability', 'created_at'):
        assert after.get(k) == before.get(k), k
    assert r.get_json()['legacy_read']['read_via'] == 'api'               # a publish-only draft never touches the read setting


def test_a_binding_never_changes_the_legacy_read_when_no_read_binding_is_touched_and_unbinding_works(env, pane):
    client, _, _ = env
    acc = _x()
    assert _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('read_own', 'x-browser', ['mentions', 'replies'], browser_profile='x-ron')])).status_code == 201
    r = _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('read_own', None, ['replies'])]), rid='req-unbind-0001')
    assert r.status_code == 201
    rec = _rec(acc['id'])
    assert set(rec['connections']['read_own']) == {'mentions'}
    assert rec['browser_profile'] == 'x-ron' and rec['read_via'] == 'pane'


# -- credentials by reference -------------------------------------------------------------

def test_an_existing_vault_entry_is_bound_by_name_and_no_value_is_returned(env, pane):
    from mc import secrets_store
    client, _, _ = env
    secrets_store.set_secret('linkedin', SECRET, username='ron@example.com', entry_type='login')
    acc = _li('Ron Levy')
    d = _draft('linkedin', {'id': acc['id']}, 'member',
               [_b('read_own', 'linkedin-browser', ['own_posts'], browser_profile='li-ron', credentials={'login': 'linkedin'})])
    r = _save(client, d)
    assert r.status_code == 201
    assert SECRET not in r.get_data(as_text=True) and SECRET not in json.dumps(_rec(acc['id']))
    assert _rec(acc['id'])['connections']['read_own']['own_posts']['refs'] == {'browser_profile': 'li-ron', 'login': 'linkedin'}
    assert [s['name'] for s in secrets_store.list_secrets()] == ['linkedin']      # nothing was created


def test_a_missing_internal_or_wrong_role_vault_name_is_refused_before_the_passcode(env):
    client, calls, _ = env
    acc = _li('Ron Levy')
    for creds, code in (({'login': 'nothing.here'}, 'vault_entry_missing'), ({'login': 'clayrune.secret'}, 'invalid'),
                        ({'api_key': 'x'}, 'invalid')):
        before = calls['n']
        r = _save(client, _draft('linkedin', {'id': acc['id']}, 'member',
                  [_b('read_own', 'linkedin-browser', ['own_posts'], credentials=creds)]))
        assert r.status_code == 400, (creds, r.get_json())
        assert calls['n'] == before                                            # a bad draft never costs a passcode guess


def test_a_profile_name_must_be_a_plain_name(env):
    client, _, _ = env
    acc = _x()
    for bad in ('../x', 'A B', '', 'x' * 60, 5):
        r = _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('read_own', 'x-browser', ['mentions'], browser_profile=bad)]))
        assert r.status_code == 400, bad


# -- gates: passcode, idempotency, agents, stale profile ----------------------------------

def test_wrong_passcode_writes_nothing_and_the_retry_works(env, pane):
    client, calls, _ = env
    acc = _x()
    d = _draft('x', {'id': acc['id']}, 'account', [_b('publish', 'x-oauth', ['post'])])
    before = _store_bytes()
    r = _save(client, d, passcode='not-it')
    assert r.status_code == 403 and r.get_json()['error'] == 'bad_passcode'
    assert _store_bytes() == before
    assert _save(client, d).status_code == 201


def test_the_same_request_answers_once_and_another_draft_under_it_is_409(env, pane):
    client, calls, _ = env
    acc = _x()
    d = _draft('x', {'id': acc['id']}, 'account', [_b('publish', 'x-oauth', ['post'])])
    first = _save(client, d)
    assert first.status_code == 201 and first.get_json()['duplicate'] is False
    stamp = _rec(acc['id'])['connections']['publish']['post']['approved_at']
    second = _save(client, d)
    assert second.status_code == 200 and second.get_json()['duplicate'] is True
    assert _rec(acc['id'])['connections']['publish']['post']['approved_at'] == stamp       # not written again
    other = _draft('x', {'id': acc['id']}, 'account', [_b('read_own', 'x-oauth', ['own_posts'])])
    r = _save(client, other)
    assert r.status_code == 409 and r.get_json()['code'] == 'request_id_reused'


def test_an_unattended_agent_is_refused_before_anything(env, monkeypatch):
    from mc.blueprints import desk_connect_purpose_routes as routes
    client, calls, _ = env
    acc = _x()
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: True)
    before = _store_bytes()
    r = _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('publish', 'x-oauth', ['post'])]))
    assert r.status_code == 403 and calls['n'] == 0 and _store_bytes() == before
    assert client.post('/api/desk/connect/purpose/verify', json={'account_id': acc['id'], 'purpose': 'read_own'}).status_code == 403


def test_a_stale_profile_revision_asks_for_a_fresh_review(env):
    client, _, _ = env
    acc = _x()
    r = _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('publish', 'x-oauth', ['post'])], revision=999))
    assert r.status_code == 409 and r.get_json()['code'] == 'profile_changed'


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(extra=1),
    lambda d: d.update(bindings=[]),
    lambda d: d.update(account={'id': 'a', 'new': {}}),
    lambda d: d['bindings'][0].update(capabilities=[]),
    lambda d: d['bindings'][0].update(capabilities=['post', 'post']),
    lambda d: d['bindings'][0].update(purpose='listen_broad'),
    lambda d: d['bindings'][0].update(oops=1),
    lambda d: d['bindings'].append(copy.deepcopy(d['bindings'][0])),
])
def test_malformed_drafts_are_refused_and_write_nothing(env, mutate):
    client, calls, _ = env
    acc = _x()
    d = _draft('x', {'id': acc['id']}, 'account', [_b('publish', 'x-oauth', ['post'])])
    mutate(d)
    before = _store_bytes()
    r = _save(client, d)
    assert r.status_code in (400, 404) and calls['n'] == 0 and _store_bytes() == before


def test_an_account_of_another_service_is_refused(env):
    client, _, _ = env
    li = _li('Ron Levy')
    r = _save(client, _draft('x', {'id': li['id']}, 'account', [_b('publish', 'x-oauth', ['post'])]))
    assert r.status_code == 400 and r.get_json()['code'] == 'wrong_platform'


# -- verification: derived, per capability, partial ---------------------------------------

def _bind_pane_and_api(client, acc):
    return _save(client, _draft('x', {'id': acc['id']}, 'account', [
        _b('read_own', 'x-browser', ['mentions', 'replies', 'post_metrics'], browser_profile='x-ron'),
        _b('read_own', 'x-oauth', ['own_posts'])]))


def test_a_check_that_proves_one_capability_of_four_is_partial(env, pane):
    client, _, _ = env
    acc = _x()
    assert _bind_pane_and_api(client, acc).status_code == 201
    from mc.desk_connect import purpose_view
    assert purpose_view.view('x')['accounts'][0]['verification']['read_own']['state'] == 'not_checked'
    r = client.post('/api/desk/connect/purpose/verify', json={'account_id': acc['id'], 'purpose': 'read_own'}).get_json()
    assert r['state'] == 'partial' and r['verified'] == ['mentions']
    routes = {x['route_id']: x for x in r['routes']}
    assert routes['x-browser']['result'] == 'passed' and routes['x-browser']['proved'] == ['mentions']
    assert routes['x-browser']['not_proved'] == ['post_metrics', 'replies']
    assert routes['x-oauth']['result'] == 'no_check' and 'billed' in routes['x-oauth']['message']
    v = purpose_view.view('x')['accounts'][0]['verification']['read_own']
    assert v['state'] == 'partial' and v['capabilities']['replies']['state'] == 'not_checked'
    assert v['capabilities']['own_posts']['state'] == 'not_checked'


def test_a_failed_check_is_a_result_and_verifies_nothing(env, pane):
    client, _, _ = env
    acc = _x()
    assert _bind_pane_and_api(client, acc).status_code == 201
    pane.ok = False
    r = client.post('/api/desk/connect/purpose/verify', json={'account_id': acc['id'], 'purpose': 'read_own'})
    assert r.status_code == 200
    body = r.get_json()
    assert body['state'] == 'not_checked' and any(x['result'] == 'failed' for x in body['routes'])


def test_verify_refuses_an_unbound_purpose_a_missing_account_and_bad_input(env):
    client, _, _ = env
    acc = _x()
    assert client.post('/api/desk/connect/purpose/verify', json={'account_id': acc['id'], 'purpose': 'read_own'}).status_code == 409
    assert client.post('/api/desk/connect/purpose/verify', json={'account_id': 'nope', 'purpose': 'read_own'}).status_code == 404
    assert client.post('/api/desk/connect/purpose/verify', json={'account_id': '../x', 'purpose': 'read_own'}).status_code == 400
    assert client.post('/api/desk/connect/purpose/verify', json={'account_id': acc['id'], 'purpose': 'listen_broad'}).status_code == 400


def test_changing_one_credential_clears_only_what_rested_on_it(env, pane, monkeypatch):
    from mc import secrets_store
    from mc.desk_connect import purpose_verification as pv, purpose_view
    client, _, _ = env
    secrets_store.set_secret('oauth.x.test', 'v1', entry_type='token')
    acc = _x()
    # a second route that rests on a vault entry, with a checker, so two records rest on different things
    monkeypatch.setitem(pv.CHECKERS, ('x', 'x-oauth', 'read_own'), lambda rec, g: {'own_posts': '@ron'})
    assert _save(client, _draft('x', {'id': acc['id']}, 'account', [
        _b('read_own', 'x-browser', ['mentions'], browser_profile='x-ron'),
        _b('read_own', 'x-oauth', ['own_posts'])])).status_code == 201
    from mc import desk
    with desk._store_lock:
        st = desk._read_store()
        st['accounts'][acc['id']]['credentials']['oauth_vault'] = 'oauth.x.test'
        st['accounts'][acc['id']]['connections']['read_own']['own_posts']['refs']['oauth_vault'] = 'oauth.x.test'
        desk._write_store(st)
    # the fingerprint was computed at Save, so re-save to bind the edited reference honestly
    assert _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('read_own', 'x-oauth', ['own_posts'])]), rid='req-rebind-0001').status_code == 201
    out = client.post('/api/desk/connect/purpose/verify', json={'account_id': acc['id'], 'purpose': 'read_own'}).get_json()
    assert out['state'] == 'verified' and out['verified'] == ['mentions', 'own_posts']
    secrets_store.set_secret('oauth.x.test', 'v2-rotated', entry_type='token')       # the credential changes
    v = purpose_view.view('x')['accounts'][0]['verification']['read_own']
    assert v['capabilities']['own_posts']['state'] == 'not_checked'                 # rested on it: cleared
    assert v['capabilities']['mentions']['state'] == 'verified'                      # rested on a browser profile: kept
    assert v['state'] == 'partial'


def test_rebinding_a_route_to_another_profile_clears_its_verification_and_nothing_else(env, pane, monkeypatch):
    from mc.desk_connect import purpose_verification as pv
    client, _, _ = env
    acc = _x()
    monkeypatch.setitem(pv.CHECKERS, ('x', 'x-oauth', 'read_own'), lambda rec, g: {'own_posts': '@ron'})
    _save(client, _draft('x', {'id': acc['id']}, 'account', [
        _b('read_own', 'x-browser', ['mentions', 'replies'], browser_profile='x-ron'),
        _b('read_own', 'x-oauth', ['own_posts'])]))
    client.post('/api/desk/connect/purpose/verify', json={'account_id': acc['id'], 'purpose': 'read_own'})
    assert pv.purpose_state(_rec(acc['id']), 'read_own')['verified'] == ['mentions', 'own_posts']
    _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('read_own', 'x-browser', ['mentions', 'replies'], browser_profile='x-other')]), rid='req-rebind-0002')
    st = pv.purpose_state(_rec(acc['id']), 'read_own')
    assert st['verified'] == ['own_posts']                                          # the untouched API binding keeps its record
    assert st['capabilities']['mentions']['state'] == 'not_checked'


def test_adding_a_capability_to_a_route_leaves_the_others_verified(env, pane):
    from mc.desk_connect import purpose_verification as pv
    client, _, _ = env
    acc = _x()
    _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('read_own', 'x-browser', ['mentions'], browser_profile='x-ron')]))
    client.post('/api/desk/connect/purpose/verify', json={'account_id': acc['id'], 'purpose': 'read_own'})
    _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('read_own', 'x-browser', ['replies'], browser_profile='x-ron')]), rid='req-addcap-0001')
    st = pv.purpose_state(_rec(acc['id']), 'read_own')
    assert st['verified'] == ['mentions'] and st['state'] == 'partial'


def test_verification_is_not_persisted_and_never_survives_a_forgotten_process(env, pane):
    from mc.desk_connect import purpose_verification as pv
    client, _, _ = env
    acc = _x()
    _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('read_own', 'x-browser', ['mentions'], browser_profile='x-ron')]))
    client.post('/api/desk/connect/purpose/verify', json={'account_id': acc['id'], 'purpose': 'read_own'})
    assert pv.purpose_state(_rec(acc['id']), 'read_own')['state'] == 'verified'
    assert 'verified' not in json.dumps(_rec(acc['id']))
    pv._forget_all_for_tests()
    assert pv.purpose_state(_rec(acc['id']), 'read_own')['state'] == 'not_checked'


def test_setup_state_and_verification_are_separate_dimensions(env, pane):
    from mc.desk_connect import purpose_view
    client, _, _ = env
    acc = _x()
    _save(client, _draft('x', {'id': acc['id']}, 'account', [_b('read_own', 'x-browser', ['mentions'], browser_profile='x-ron')]))
    client.post('/api/desk/connect/purpose/verify', json={'account_id': acc['id'], 'purpose': 'read_own'})
    pane.profiles_present = False
    a = purpose_view.view('x')['accounts'][0]
    assert a['bound'][0]['setup'] == 'needs_signin'
    assert a['verification']['read_own']['state'] == 'verified'      # a record is kept until what it rested on changes


# -- the MCP row ---------------------------------------------------------------------------

def test_higgsfield_shows_no_not_available_mcp_row_and_its_sign_in_says_it_is_its_mcp():
    from mc.desk_connect import methods
    out = methods.inspect('Higgsfield')
    rows = {o['method']: o for o in out['options']}
    assert 'mcp' not in rows, list(rows)
    assert not any('not available' in (o.get('guidance') or '').lower() and 'mcp' in o['title'].lower() for o in out['options'])
    assert 'MCP' in rows['oauth']['title']


def test_services_without_their_own_mcp_keep_one_honestly_worded_row():
    from mc.desk_connect import methods
    for name in ('X', 'LinkedIn'):
        rows = [o for o in methods.inspect(name)['options'] if o['method'] == 'mcp']
        assert len(rows) == 1 and rows[0]['support'] == 'info_only'
        assert rows[0]['title'] == 'Adding your own MCP server' and 'Coming' in rows[0]['guidance']
    notion = [o for o in methods.inspect('Notion')['options'] if o['method'] == 'mcp']
    assert len(notion) == 1 and notion[0]['support'] == 'available'
