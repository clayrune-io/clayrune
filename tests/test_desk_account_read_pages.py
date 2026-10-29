"""Any platform's Desk account can be read through the browser pane (44712cf4 follow-up).

Pinned:

  * a YouTube / Instagram / unknown-site account takes a browser profile and
    `read_pages` through the same PATCH X and LinkedIn use; both land on the workspace
    record AND the presence copy `PaneDigestReader` reads, and the reader parses them;
  * `read_via: 'api'` is X only: any other site is read through the pane;
  * a blog has nothing to read, so a read setting on it is still refused;
  * `read_pages` is saved strictly (https only, known roles, at most MAX_USER_PAGES):
    a bad entry is a 400 that changes nothing, `[]` clears;
  * an unattended agent cannot set `read_pages` (the pane would open what it names).
"""
import copy
import sys
from pathlib import Path

import pytest
from flask import Flask

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc import desk as _desk  # noqa: E402
from mc import desk_accounts as _accounts  # noqa: E402
from mc.blueprints import desk_routes  # noqa: E402
from mc.blueprints.secrets_routes import local_auth  # noqa: E402
from mc.desk_engagement_pane_digest import MAX_USER_PAGES, PaneDigestReader  # noqa: E402

PROJECTS = [{'id': 'alpha', 'name': 'Alpha'}]
YT = 'https://studio.youtube.com/channel/UC123/comments'


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(_accounts.secrets_store, 'list_secrets', lambda *a, **k: [])
    monkeypatch.setattr(_accounts.secrets_store, 'is_readable', lambda name: False)
    caller = {'unattended': False}
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', lambda: caller['unattended'])
    monkeypatch.setattr(local_auth, '_local_auth_is_configured', lambda: True)
    monkeypatch.setattr(local_auth, '_local_auth_try_passcode', lambda value: value == 'test-human-proof')
    app = Flask(__name__)
    app.config['TESTING'] = True
    desk_routes.wire(
        load_projects_fn=lambda: PROJECTS,
        load_project_fn=lambda pid: next((p for p in PROJECTS if p['id'] == pid), None),
        store_path=tmp_path / 'desk.json',
        signals_path=tmp_path / 'desk_signals.jsonl',
        uploads_root=tmp_path,
    )
    app.register_blueprint(desk_routes.bp)
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-yt', 'platform': 'youtube', 'identity': 'UC123'},
        {'channel_id': 'ch-ig', 'platform': 'instagram', 'identity': 'clayrune'},
        {'channel_id': 'ch-forum', 'platform': 'forum', 'identity': 'ron'},
        {'channel_id': 'ch-x', 'platform': 'x', 'identity': '@a'},
        {'channel_id': 'ch-b', 'platform': 'blog', 'identity': 'b'}]})
    return app.test_client(), caller


def _patch(client, cid, **body):
    if 'organization_id' in body:
        body['passcode'] = 'test-human-proof'
    return client.patch(f'/api/desk/accounts/{cid}', json=body)


def _by_id(client):
    return {a['id']: a for a in client.get('/api/desk/accounts').get_json()}


def _copy(cid):
    return next(a for a in (_desk.get_presence('alpha') or {})['accounts'] if a['channel_id'] == cid)


@pytest.mark.parametrize('record_type', ['binding', 'setup', 'untyped'])
@pytest.mark.parametrize('consent', ['legacy', 'deny', 'read', 'invalid', 'invalid_future'])
def test_saved_linkedin_company_address_overrides_member_kind_and_clear_restores_it(env, monkeypatch, record_type, consent):
    from mc import desk_engagement as engagement, desk_pane_activity_scope as scope
    from mc.desk_connect import browser_setup, browser_setup_record, permission_policy, purpose_bindings, purpose_view, registry, route_readiness

    if consent == 'read':
        # Exercise exact-consent preservation with a synthetic runtime. The
        # real LinkedIn executor registration is owned by the MC-1118 branch.
        monkeypatch.setitem(route_readiness.EXECUTORS, ('linkedin', 'linkedin-browser', 'read_own'), 'pane')

    client, caller = env
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-li', 'platform': 'linkedin', 'identity': 'Company', 'browser_profile': 'main'}]})
    client.get('/api/desk/accounts')
    binding = {'account_kind': 'member', 'route_id': 'linkedin-browser', 'refs': {'browser_profile': 'main'}, 'scopes': []}
    binding['fingerprint'] = purpose_bindings.fingerprint('linkedin', 'read_own', 'linkedin-browser', 'member', ['own_posts'], binding['refs'], [])
    member = {'read_own': {'own_posts': binding}}
    setup = browser_setup_record.build('linkedin', 'linkedin-browser', 'member', {'browser_profile': 'main'}, 'saved-before')
    policy_draft = {'account_id': 'ch-li', 'service': 'linkedin', 'account_kind': 'member',
                    'read': consent == 'read', 'post': False, 'scopes': [
                        {'purpose': 'read_own', 'capability': 'own_posts', 'route_id': 'linkedin-browser'}] if consent == 'read' else []}
    with _desk._store_lock:
        store = _desk._read_store()
        rec = store['accounts']['ch-li']
        if record_type == 'binding':
            rec['connections'] = copy.deepcopy(member)
        elif record_type == 'setup':
            rec['browser_setup'] = copy.deepcopy(setup)
        if consent != 'legacy':
            rec[permission_policy.POLICY_KEY] = permission_policy._record(policy_draft)
            rec[permission_policy.VERSION_KEY] = permission_policy.VERSION
            if consent == 'invalid':
                rec[permission_policy.POLICY_KEY]['fingerprint'] = 'tampered'
            elif consent == 'invalid_future':
                rec[permission_policy.POLICY_KEY]['fingerprint'] = permission_policy.fingerprint({**policy_draft, 'account_kind': 'organization'})
        _desk._write_store(store)
    url = 'https://www.linkedin.com/company/123456/admin/notifications/'
    patch = {'organization_id': '123456', 'read_pages': [{'role': 'activity', 'url': url}], 'project_id': 'alpha'}

    caller['unattended'] = True
    assert _patch(client, 'ch-li', **patch).status_code == 403
    assert _patch(client, 'ch-li', organization_id='123456').status_code == 403
    assert _patch(client, 'ch-li', organization_id='').status_code == 403
    assert not _by_id(client)['ch-li'].get('organization_id')
    caller['unattended'] = False
    saved = _patch(client, 'ch-li', **patch)
    assert saved.status_code == 200, saved.get_json()
    assert saved.json['account_kind'] == 'organization'
    acc = engagement.platform_account('alpha', 'linkedin')
    assert scope.needs_company_page('linkedin', acc)
    assert scope.is_company_admin(acc['read_pages'][0]['url'], acc['organization_id'])
    assert _copy('ch-li')['read_pages'] == patch['read_pages']
    def check_kind(acc, kind):
        assert purpose_view.account_kind(acc) == browser_setup_record.kind_of(acc) == kind
        _, clean = permission_policy.clean_submission({'request_id': 'company-kind-check', 'draft': {**policy_draft, 'account_kind': kind}})
        assert clean['account_kind'] == kind
        if record_type == 'binding':
            actual = acc['connections']['read_own']['own_posts']
            assert {k: v for k, v in actual.items() if k not in ('account_kind', 'fingerprint')} == {k: v for k, v in binding.items() if k not in ('account_kind', 'fingerprint')}
            assert actual['account_kind'] == kind
            assert actual['fingerprint'] == purpose_bindings.fingerprint('linkedin', 'read_own', 'linkedin-browser', kind, ['own_posts'], binding['refs'], [])
            browser_setup._clean_account({'id': 'ch-li'}, 'linkedin', kind)
        else:
            draft = {'service': 'linkedin', 'revision': registry.profile('linkedin')['revision'], 'route_id': 'linkedin-browser',
                     'account': {'id': 'ch-li'}, 'account_kind': kind, 'browser_profile': 'main'}
            setup_clean = browser_setup.clean_draft(draft)
            assert setup_clean['account_kind'] == kind
            with _desk._store_lock:
                assert browser_setup._apply(_desk._read_store(), 'ch-li', setup_clean, '')[1] is (record_type == 'setup')
        state = permission_policy.read_policy(acc)
        assert state['state'] == ('legacy' if consent == 'legacy' else 'invalid' if consent.startswith('invalid') else 'explicit')
        if state['state'] == 'explicit':
            assert state['read'] is (consent == 'read') and state['post'] is False
            assert state['scopes'] == policy_draft['scopes']
            assert state['account_kind'] == kind
        if record_type == 'setup':
            assert acc['browser_setup'] == {**setup, 'account_kind': kind}

    check_kind(acc, 'organization')
    with pytest.raises(permission_policy.PolicyError) as wrong:
        permission_policy._check_identity(acc, policy_draft)
    assert wrong.value.code == 'wrong_kind'
    with pytest.raises(browser_setup.SetupError) as wrong_setup:
        browser_setup._clean_account({'id': 'ch-li'}, 'linkedin', 'member')
    assert wrong_setup.value.code == 'kind_mismatch'

    cleared = _patch(client, 'ch-li', organization_id='', read_pages=[], project_id='alpha')
    assert cleared.status_code == 200, cleared.get_json()
    assert cleared.json['account_kind'] == 'member'
    acc = engagement.platform_account('alpha', 'linkedin')
    assert not acc.get('organization_id') and 'read_pages' not in acc
    assert 'read_pages' not in _copy('ch-li')
    assert not scope.needs_company_page('linkedin', acc)
    check_kind(acc, 'member')


def test_company_destination_validation_and_unrelated_edits_are_atomic(env):
    client, _ = env
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-li', 'platform': 'linkedin', 'identity': 'Company', 'browser_profile': 'main'}]})
    client.get('/api/desk/accounts')
    before = _desk._read_store()['accounts']['ch-li']
    for patch in ({'organization_id': 'bad'}, {'organization_id': '123', 'read_pages': 'bad'},
                  {'organization_id': '123', 'read_via': 'api'}):
        assert _patch(client, 'ch-li', **patch).status_code == 400
        assert _desk._read_store()['accounts']['ch-li'] == before
    assert _patch(client, 'ch-li', label='Renamed').status_code == 200
    assert 'account_kind' not in _desk._read_store()['accounts']['ch-li']


def test_human_resaving_a_stale_company_destination_repairs_its_member_binding(env):
    from mc.desk_connect import permission_policy, purpose_view

    client, _ = env
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-li', 'platform': 'linkedin', 'identity': 'Company', 'browser_profile': 'main'}]})
    client.get('/api/desk/accounts')
    page = {'role': 'activity', 'url': 'https://www.linkedin.com/company/123/admin/notifications/'}
    with _desk._store_lock:
        store = _desk._read_store()
        rec = store['accounts']['ch-li']
        rec.update(organization_id='123', read_pages=[page], connections={
            'read_own': {'own_posts': {'route_id': 'linkedin-browser', 'account_kind': 'member'}}})
        _desk._write_store(store)
    assert _by_id(client)['ch-li']['account_kind'] == 'member'
    draft = {'account_id': 'ch-li', 'service': 'linkedin', 'account_kind': 'organization', 'read': False, 'post': False, 'scopes': []}
    with pytest.raises(permission_policy.PolicyError) as refused:
        permission_policy.clean_submission({'request_id': 'stale-company-repair', 'draft': draft})
    assert refused.value.code == 'wrong_kind'
    assert _patch(client, 'ch-li', organization_id='123', read_pages=[page]).json['account_kind'] == 'organization'
    _, clean = permission_policy.clean_submission({'request_id': 'stale-company-repair', 'draft': draft})
    assert clean == draft
    assert purpose_view.account_kind(_desk._read_store()['accounts']['ch-li']) == 'organization'


@pytest.mark.parametrize('trigger', ['manual', 'dispatch'])
@pytest.mark.parametrize('origin', [False, True])
@pytest.mark.parametrize('org', ['', '123'])
def test_company_kind_change_requires_human_proof_even_with_origin(env, monkeypatch, trigger, origin, org):
    from mc import unattended
    client, _ = env
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', unattended.is_unattended_caller)
    monkeypatch.setattr(unattended, 'agent_sessions', {
        'synthetic': {'status': 'running', 'trigger_type': trigger}})
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-li', 'platform': 'linkedin', 'identity': 'Company'}]})
    client.get('/api/desk/accounts')
    before = copy.deepcopy(_desk._read_store())
    body = {'organization_id': org, 'read_pages': []}
    headers = {'Origin': 'http://localhost:5199'} if origin else {}
    refused = client.patch('/api/desk/accounts/ch-li', json=body, headers=headers)
    assert refused.status_code == 403
    assert _desk._read_store() == before


@pytest.mark.parametrize('org', ['', '123'])
def test_company_kind_change_human_ui_path_checks_proof_and_does_not_store_it(env, monkeypatch, org):
    from mc import unattended
    client, _ = env
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', unattended.is_unattended_caller)
    monkeypatch.setattr(unattended, 'agent_sessions', {
        'synthetic': {'status': 'running', 'trigger_type': 'dispatch'}})
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-li', 'platform': 'linkedin', 'identity': 'Company'}]})
    client.get('/api/desk/accounts')
    with _desk._store_lock:
        store = _desk._read_store()
        store['accounts']['ch-li']['account_kind'] = 'member' if org else 'organization'
        _desk._write_store(store)
    body = {'organization_id': org, 'read_pages': [], 'passcode': 'wrong'}
    headers = {'Origin': 'http://localhost:5199'}
    assert client.patch('/api/desk/accounts/ch-li', json=body, headers=headers).status_code == 403
    body['passcode'] = 'test-human-proof'
    result = client.patch('/api/desk/accounts/ch-li', json=body, headers=headers)
    assert result.status_code == 200, result.json
    assert result.json['account_kind'] == ('organization' if org else 'member')
    assert 'passcode' not in str(_desk._read_store())
    assert 'passcode' not in result.json


@pytest.mark.parametrize('include_id', [True, False])
@pytest.mark.parametrize('path', [
    '/company/123/admin/../../../notifications/',
    '/company/123/admin/%2e%2e/%2E%2E/%2e%2E/notifications/',
    '/company/123/admin/.%2e/%2E./../notifications/',
    '/company/123/admin/./notifications/',
    '/company/123/admin/%2E/notifications/',
    '/company/123/admin/child/../notifications/',
    '/company/123/admin/child/%2e./notifications/',
    '/company/123/admin/../../456/admin/notifications/',
    '/company/123/%2E/admin/notifications/',
    '/company/456/admin/notifications/',
    '/company/123/admin-other/notifications/',
    '/notifications/',
])
def test_company_admin_patch_rejects_escaped_or_mismatched_destination_atomically(env, include_id, path):
    client, _ = env
    _desk.upsert_presence('alpha', {'accounts': [
        {'channel_id': 'ch-li', 'platform': 'linkedin', 'identity': 'Company', 'organization_id': '123'}]})
    client.get('/api/desk/accounts')
    assert _patch(client, 'ch-li', organization_id='123').status_code == 200
    before = copy.deepcopy(_desk._read_store())
    body = {'label': 'Must not save', 'read_pages': [{'role': 'activity', 'url': 'https://www.linkedin.com' + path}]}
    if include_id:
        body['organization_id'] = '123'
    response = _patch(client, 'ch-li', **body)
    assert response.status_code == 400, response.json
    assert _desk._read_store() == before


@pytest.mark.parametrize('cid', ['ch-yt', 'ch-ig', 'ch-forum'])
def test_a_non_x_account_takes_a_profile_and_pages(env, cid):
    client, _ = env
    pages = [{'role': 'activity', 'url': YT}]
    r = _patch(client, cid, read_via='pane', browser_profile=' Main ', read_pages=pages)
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert (body['read_via'], body['browser_profile'], body['read_pages']) == ('pane', 'main', pages)
    assert _by_id(client)[cid]['read_pages'] == pages
    copy = _copy(cid)
    assert copy['browser_profile'] == 'main' and copy['read_pages'] == pages


def test_the_reader_parses_what_the_account_saved(env):
    client, _ = env
    _patch(client, 'ch-yt', browser_profile='main', read_pages=[{'role': 'activity', 'url': YT}])
    rd = PaneDigestReader('alpha', 'youtube', _copy('ch-yt'))
    assert rd._user_pages == [{'role': 'activity', 'url': YT}] and rd._profile == 'main'


def test_pages_alone_and_clearing(env):
    client, _ = env
    assert _patch(client, 'ch-yt', read_pages=[{'role': 'activity', 'url': YT}]).status_code == 200
    assert 'browser_profile' not in _by_id(client)['ch-yt']
    assert _patch(client, 'ch-yt', read_pages=[]).status_code == 200
    assert 'read_pages' not in _by_id(client)['ch-yt'] and 'read_pages' not in _copy('ch-yt')


def test_an_unrelated_edit_leaves_the_pages_alone(env):
    client, _ = env
    _patch(client, 'ch-yt', read_pages=[{'role': 'activity', 'url': YT}])
    assert _patch(client, 'ch-yt', browser_profile='main').status_code == 200
    assert _by_id(client)['ch-yt']['read_pages'] == [{'role': 'activity', 'url': YT}]


def test_api_read_is_x_only(env):
    client, _ = env
    for cid in ('ch-yt', 'ch-ig', 'ch-forum'):
        r = _patch(client, cid, read_via='api')
        assert r.status_code == 400 and 'browser pane' in r.get_json()['error']
        assert 'read_via' not in _by_id(client)[cid]
    assert _patch(client, 'ch-x', read_via='api').status_code == 200


def test_a_blog_still_has_no_read_setting(env):
    client, _ = env
    for body in ({'read_via': 'pane'}, {'browser_profile': 'main'}, {'read_pages': [{'role': 'activity', 'url': YT}]}):
        assert _patch(client, 'ch-b', **body).status_code == 400
    assert _patch(client, 'ch-b', label='My blog').status_code == 200   # a plain edit still works


@pytest.mark.parametrize('bad', [
    'https://studio.youtube.com/',                                  # not a list
    [{'role': 'activity', 'url': 'http://studio.youtube.com/'}],     # not https
    [{'role': 'activity', 'url': 'javascript:alert(1)'}],
    [{'role': 'activity', 'url': 'https://youtube.com@evil.example/'}],   # userinfo
    [{'role': 'activity', 'url': 'https://192.168.0.1/'}],           # IP literal
    [{'role': 'activity', 'url': 7}],
    [{'role': 'dm', 'url': YT}],                                     # unknown role
    [{'role': 'activity', 'url': YT, 'extra': 1}],
    ['https://studio.youtube.com/'],
    [{'role': 'activity', 'url': f'https://example.org/{i}'} for i in range(MAX_USER_PAGES + 1)],
])
def test_a_bad_address_is_refused_and_changes_nothing(env, bad):
    client, _ = env
    r = _patch(client, 'ch-yt', label='Renamed', browser_profile='main', read_pages=bad)
    assert r.status_code == 400
    acc = _by_id(client)['ch-yt']
    assert acc['label'] != 'Renamed' and 'browser_profile' not in acc and 'read_pages' not in acc


def test_duplicate_addresses_collapse_and_whitespace_is_trimmed(env):
    client, _ = env
    r = _patch(client, 'ch-yt', read_pages=[{'role': 'activity', 'url': f' {YT} '}, {'role': 'activity', 'url': YT}])
    assert r.get_json()['read_pages'] == [{'role': 'activity', 'url': YT}]


def test_an_unattended_agent_cannot_set_pages(env):
    client, caller = env
    caller['unattended'] = True
    r = _patch(client, 'ch-yt', read_pages=[{'role': 'activity', 'url': YT}])
    assert r.status_code == 403
    assert 'read_pages' not in _by_id(client)['ch-yt']
    assert _patch(client, 'ch-yt', label='Renamed').status_code == 200    # a label is not a read setting


def test_project_id_files_a_copy_that_carries_the_pages(env):
    client, _ = env
    _desk.upsert_presence('alpha', {'accounts': []})
    r = _patch(client, 'ch-ig', browser_profile='main', read_pages=[{'role': 'activity', 'url': YT}], project_id='alpha')
    assert r.status_code == 200
    assert _copy('ch-ig')['read_pages'] == [{'role': 'activity', 'url': YT}]
