"""MC-1062/06b: actual API, pane and digest poll dispatch; no live calls."""
from __future__ import annotations

import json
import copy
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import browser_agent_read, browser_digest, desk, desk_engagement as eng, desk_oauth, secrets_store
from mc import desk_engagement_pane_digest as digest
from mc.blueprints import desk_routes
from mc.desk_connect import permission_policy as policy, registry
from test_desk_engagement import FakeX, REPLY
from test_desk_engagement_pane import MENTIONS_TEXT, STATUS_TEXT, FakePages, _body

PID = 'consent-project'
NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAYRUNE_HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(desk, 'SIGNALS_PATH', tmp_path / 'signals.jsonl')
    policy._forget_all_for_tests()
    desk._write_store({'oauth_legacy_bound': 'first', 'accounts': {
        'first': {'id': 'first', 'platform': 'x', 'capability': 'direct',
                  'credentials': {'oauth_vault': 'oauth.x'}},
        'second': {'id': 'second', 'platform': 'x', 'capability': 'direct',
                   'credentials': {'oauth_vault': 'oauth.x.second'}},
        'page': {'id': 'page', 'platform': 'linkedin', 'account_kind': 'organization',
                 'organization_id': '777', 'capability': 'direct'},
        'other': {'id': 'other', 'platform': 'unknown-site', 'capability': 'manual'},
    }})
    monkeypatch.setattr(secrets_store, 'list_secrets', lambda: [
        {'name': 'oauth.x'}, {'name': 'oauth.x.second'}, {'name': 'x.oauth-token'}])
    monkeypatch.setattr(desk_oauth, 'status', lambda *a, **k: {'state': 'connected'})
    token = Mock(return_value='valid-token')
    refresh = Mock(side_effect=AssertionError('unexpected real token refresh'))
    secret = Mock(side_effect=AssertionError('unexpected secret read'))
    monkeypatch.setattr(desk_oauth, 'x_token', token)
    monkeypatch.setattr(desk_oauth, '_refresh', refresh)
    monkeypatch.setattr(secrets_store, 'get_secret_value', secret)
    transport = FakeX(mentions=[REPLY], metrics=[{'id': '111', 'public_metrics': {'like_count': 7}}])
    monkeypatch.setattr(eng, '_urllib_transport', transport)
    pages = FakePages({eng.X_MENTIONS_URL: _body(MENTIONS_TEXT),
                       eng.X_STATUS_URL.format(id='111'): _body(STATUS_TEXT, 'https://x.com/ron/status/111')})
    factory = Mock(return_value=pages)
    monkeypatch.setattr(eng, '_default_page_reader', factory)
    monkeypatch.setattr(eng, '_default_profile_exists', lambda name: True)
    generic_pages = FakePages(lambda url: _body('Visible activity or post', url))
    generic_factory = Mock(return_value=generic_pages)
    monkeypatch.setattr(digest, '_default_pages', generic_factory)
    def model(instruction, stdin_text, **kwargs):
        result = ({'page': 'post', 'suspicious': False, 'counts': {'likes': 7}}
                  if instruction == digest.POST_INSTRUCTION else
                  {'page': 'activity', 'suspicious': False, 'rows': [{
                      'kind': 'mention', 'author': 'Kat', 'snippet': 'Hello',
                      'post': '', 'when': '2h', 'id': 'mention-1'}]})
        return json.dumps(result), None
    model_call = Mock(side_effect=model)
    monkeypatch.setattr(browser_digest, 'run_laundering_call', model_call)
    wire = Mock(side_effect=AssertionError('unexpected outbound HTTP'))
    monkeypatch.setattr('urllib.request.urlopen', wire)
    browser_agent_read.set_policy('main', True, ['x.com', 'linkedin.com', 'example.com'])
    yield SimpleNamespace(token=token, refresh=refresh, secret=secret, transport=transport,
                          pages=pages, factory=factory, generic_pages=generic_pages,
                          generic_factory=generic_factory, model=model_call, wire=wire)
    policy._forget_all_for_tests()


@pytest.fixture
def supported_api_scope(monkeypatch):
    """Simulate future reviewed API read coverage; production remains unknown.

    These tests prove dispatch behavior if supported consent exists. The real
    registry refusal has its own tests below, without this fixture.
    """
    profile = registry.profile
    reviewed = copy.deepcopy(profile('x'))
    for route in reviewed['routes']:
        if route['id'] == 'x-oauth':
            for cap in route['coverage']:
                if cap['purpose'] == 'read_own' and cap['capability'] in ('mentions', 'post_metrics'):
                    cap['status'] = 'documented'
    monkeypatch.setattr(registry, 'profile', lambda service: reviewed if service == 'x' else profile(service))


def presence(via='api', aid='first', platform='x', budget=10, *, no_id=False):
    acc = {'platform': platform, 'read_via': via, 'browser_profile': 'main'}
    if not no_id:
        acc['channel_id'] = aid
    if platform != 'x':
        root = 'https://www.linkedin.com/' if platform == 'linkedin' else 'https://example.com/'
        acc['url'] = root
        acc['read_pages'] = [{'role': 'activity', 'url': root + 'notifications/'},
                             {'role': 'post', 'url': root + 'posts/'}]
    desk.upsert_presence(PID, {'accounts': [acc], 'budget': {'amount': budget, 'period': 'month'}})


def consent(caps=(), *, aid='first', route='x-oauth', service='x', kind='account'):
    draft = {'account_id': aid, 'service': service, 'account_kind': kind,
             'read': bool(caps), 'post': False,
             'scopes': [{'purpose': 'read_own', 'capability': c, 'route_id': route} for c in caps]}
    policy.commit('policy-' + aid + '-' + route + '-' + '-'.join(caps or ['deny']), draft)


def post(platform='x', ext='111'):
    url = (f'https://x.com/ron/status/{ext}' if platform == 'x'
           else f'https://www.linkedin.com/posts/{ext}')
    return desk.record_published(platform=platform, voice='personal', body='Our post',
                                 project_id=PID, url=url, cost=0, published_at='2026-10-05T12:00:00Z')


def poll(readers=None):
    return eng.poll_project(PID, readers=readers, now=NOW)['platforms']


def no_calls(w):
    for fn in (w.token, w.refresh, w.secret, w.factory, w.generic_factory, w.model, w.wire):
        fn.assert_not_called()
    assert w.transport.calls == [] and w.pages.reads == [] and w.generic_pages.reads == []


@pytest.mark.parametrize('via', ['api', 'pane'])
def test_read_deny_stops_both_real_x_dispatches_before_refresh_or_pages(world, via):
    presence(via)
    row = post()
    consent()
    out = poll()['x']
    assert set(out['permission_denied']) == {'mentions', 'post_metrics'}
    assert out['spent'] == out['new_items'] == out['metrics_written'] == 0
    no_calls(world)
    reads = desk.list_reads(project_id=PID)
    assert {r['kind'] for r in reads} == {'replies', 'metrics'}
    assert all(r['cost'] == r['resources'] == 0 and not r['ok'] for r in reads)
    assert next(r for r in desk.list_ledger(project_id=PID) if r['id'] == row['id'])['outcomes'] == []
    assert desk.list_engagement_items(project_id=PID) == []


@pytest.mark.parametrize('via', ['api', 'pane'])
@pytest.mark.parametrize('caps', [('mentions',), ('post_metrics',), ('mentions', 'post_metrics')])
def test_mentions_and_metrics_have_independent_exact_decisions(world, supported_api_scope, via, caps):
    presence(via)
    post()
    consent(caps, route='x-oauth' if via == 'api' else 'x-browser')
    out = poll()['x']
    assert (out['new_items'] > 0) is ('mentions' in caps)
    assert (out['metrics_written'] > 0) is ('post_metrics' in caps)
    assert set(out.get('permission_denied', {})) == {'mentions', 'post_metrics'} - set(caps)
    if via == 'api':
        calls = [url for url, _ in world.transport.calls]
        assert sum(url.endswith('/mentions') for url in calls) == int('mentions' in caps)
        assert sum(url.endswith('/tweets') for url in calls) == int('post_metrics' in caps)
        assert world.token.call_count == len(caps)
        assert out['spent'] == pytest.approx((2 * int('mentions' in caps) + int('post_metrics' in caps)) * eng.X_READ_UNIT_COST)
    else:
        assert (eng.X_MENTIONS_URL in world.pages.reads) is ('mentions' in caps)
        assert (eng.X_STATUS_URL.format(id='111') in world.pages.reads) is ('post_metrics' in caps)
        assert world.pages.closed == 1
        world.token.assert_not_called()


@pytest.mark.parametrize('via,policy_route', [('api', 'x-browser'), ('pane', 'x-oauth')])
def test_allow_for_other_route_never_switches_or_falls_back(world, supported_api_scope, via, policy_route):
    presence(via)
    post()
    consent(('mentions', 'post_metrics'), route=policy_route)
    assert set(poll()['x']['permission_denied']) == {'mentions', 'post_metrics'}
    no_calls(world)


@pytest.mark.parametrize('via', ['api', 'pane'])
def test_revocation_stops_selected_reader_without_any_binding(world, supported_api_scope, via):
    presence(via)
    consent(('mentions',), route='x-oauth' if via == 'api' else 'x-browser')
    assert 'connections' not in desk._read_store()['accounts']['first']
    assert poll()['x']['new_items'] > 0
    consent()
    before = (world.token.call_count, len(world.transport.calls), len(world.pages.reads))
    assert 'mentions' in poll()['x']['permission_denied']
    assert (world.token.call_count, len(world.transport.calls), len(world.pages.reads)) == before


@pytest.mark.parametrize('aid,token_arg', [('first', None), ('second', 'second')])
def test_original_workspace_id_precedes_its_own_oauth_mapping(world, supported_api_scope, aid, token_arg):
    presence(aid=aid)
    consent((), aid='first')
    consent(('mentions',), aid='second')
    out = poll()['x']
    if aid == 'first':
        no_calls(world)
    else:
        assert out['new_items'] == 1
        assert world.token.call_args.kwargs['account_id'] == token_arg


@pytest.mark.parametrize('damage', ['lost_policy', 'wrong_identity', 'wrong_service', 'wrong_record_id'])
def test_invalid_explicit_policy_or_identity_never_uses_another_account(world, supported_api_scope, damage):
    presence()
    consent(('mentions',))
    with desk._store_lock:
        store = desk._read_store()
        rec = store['accounts']['first']
        if damage == 'lost_policy':
            del rec[policy.POLICY_KEY]
        elif damage == 'wrong_identity':
            rec[policy.POLICY_KEY]['account_id'] = 'second'
        elif damage == 'wrong_service':
            rec['platform'] = 'linkedin'
        else:
            rec['id'] = 'second'
        desk._write_store(store)
    assert 'mentions' in poll()['x']['permission_denied']
    no_calls(world)


@pytest.mark.parametrize('via', ['api', 'pane'])
@pytest.mark.parametrize('no_id', [False, True])
def test_legacy_unset_and_inline_accounts_keep_reads_and_costs(world, via, no_id):
    presence(via, no_id=no_id)
    if no_id:
        consent()  # singleton policy must not be inferred for an inline caller
    post()
    out = poll()['x']
    assert out['new_items'] > 0 and out['metrics_written'] > 0
    assert 'permission_denied' not in out
    assert out['spent'] == pytest.approx(3 * eng.X_READ_UNIT_COST if via == 'api' else 0)


def test_post_allow_does_not_grant_read(world):
    presence()
    policy.commit('publish-only-policy', {'account_id': 'first', 'service': 'x', 'account_kind': 'account',
        'read': False, 'post': True, 'scopes': [{'purpose': 'publish', 'capability': 'post', 'route_id': 'x-oauth'}]})
    assert 'mentions' in poll()['x']['permission_denied']
    no_calls(world)


def test_metrics_only_budget_does_not_reserve_denied_mentions(world, supported_api_scope):
    presence(budget=eng.X_READ_UNIT_COST)
    post()
    consent(('post_metrics',))
    out = poll()['x']
    assert out['metrics_written'] == 1 and out['spent'] == eng.X_READ_UNIT_COST
    assert [url for url, _ in world.transport.calls] == [eng.X_API_BASE + '/tweets']


def test_allow_cannot_bypass_existing_paid_read_budget(world, supported_api_scope):
    presence(budget=0)
    post()
    consent(('mentions', 'post_metrics'))
    assert poll()['x']['budget_blocked'] is True
    no_calls(world)


@pytest.mark.parametrize('via', ['api', 'pane'])
def test_allow_cannot_bypass_missing_credential_or_profile(world, supported_api_scope, monkeypatch, via):
    presence(via)
    consent(('mentions',), route='x-oauth' if via == 'api' else 'x-browser')
    if via == 'api':
        monkeypatch.setattr(secrets_store, 'list_secrets', lambda: [])
    else:
        monkeypatch.setattr(eng, '_default_profile_exists', lambda name: False)
    assert poll()['x']['state'] == 'not_connected'
    no_calls(world)


def test_revocation_between_capabilities_stops_metrics_refresh_and_spend(world, supported_api_scope, monkeypatch):
    presence()
    post()
    consent(('mentions', 'post_metrics'))
    original = world.transport
    def transport(url, params, token):
        result = original(url, params, token)
        if url.endswith('/mentions'):
            consent()
        return result
    monkeypatch.setattr(eng, '_urllib_transport', transport)
    out = poll()['x']
    assert 'post_metrics' in out['permission_denied']
    assert world.token.call_count == 1
    assert not any(url.endswith('/tweets') for url, _ in original.calls)
    assert out['spent'] == 2 * eng.X_READ_UNIT_COST


def test_each_metrics_batch_rechecks_current_policy(world, supported_api_scope, monkeypatch):
    presence()
    post(ext='111')
    post(ext='222')
    consent(('post_metrics',))
    monkeypatch.setattr(eng, 'METRICS_BATCH', 1)
    def transport(url, params, token):
        assert url.endswith('/tweets')
        world.transport.calls.append((url, params))
        consent()
        return {'data': [{'id': params['ids'], 'public_metrics': {'like_count': 7}}]}
    monkeypatch.setattr(eng, '_urllib_transport', transport)
    out = poll()['x']
    assert len(world.transport.calls) == world.token.call_count == 1
    assert 'post_metrics' in out['permission_denied'] and out['metrics_written'] == 1


@pytest.mark.parametrize('caps', [('mentions',), ('post_metrics',), ('mentions', 'post_metrics')])
def test_real_registry_refuses_unknown_x_api_read_allow_and_forged_consent(world, caps):
    presence()
    post()
    with pytest.raises(policy.PolicyError, match='exact capability'):
        consent(caps)
    with desk._store_lock:
        store = desk._read_store()
        rec = store['accounts']['first']
        rec[policy.VERSION_KEY] = policy.VERSION
        rec[policy.POLICY_KEY] = policy._record({'account_id': 'first', 'service': 'x',
            'account_kind': 'account', 'read': True, 'post': False, 'scopes': [
                {'purpose': 'read_own', 'capability': cap, 'route_id': 'x-oauth'} for cap in caps]})
        desk._write_store(store)
    assert set(poll()['x']['permission_denied']) == {'mentions', 'post_metrics'}
    no_calls(world)


@pytest.mark.parametrize('mode', ['deny', 'unsupported_allow', 'legacy'])
def test_generic_linkedin_digest_obeys_explicit_policy_without_inventing_allow(world, mode):
    presence('pane', aid='page', platform='linkedin')
    post(platform='linkedin')
    if mode == 'deny':
        consent((), aid='page', service='linkedin', kind='organization')
    elif mode == 'unsupported_allow':
        with pytest.raises(policy.PolicyError, match='executor'):
            consent(('mentions',), aid='page', service='linkedin', kind='organization', route='linkedin-browser')
        # A forged/stale record cannot override the absent executor registration.
        with desk._store_lock:
            store = desk._read_store()
            rec = store['accounts']['page']
            rec[policy.VERSION_KEY] = policy.VERSION
            rec[policy.POLICY_KEY] = policy._record({'account_id': 'page', 'service': 'linkedin',
                'account_kind': 'organization', 'read': True, 'post': False, 'scopes': [
                    {'purpose': 'read_own', 'capability': 'mentions', 'route_id': 'linkedin-browser'}]})
            desk._write_store(store)
    out = poll()['linkedin']
    if mode == 'legacy':
        assert out['new_items'] == out['metrics_written'] == 1
        assert world.model.call_count == 2 and len(world.generic_pages.reads) == 2
        assert out['spent'] == 2 * digest.PAGE_READ_UNIT_COST
    else:
        assert set(out['permission_denied']) == {'mentions', 'post_metrics'}
        assert out['spent'] == 0
        no_calls(world)


def test_generic_legacy_selected_reader_stops_after_explicit_revocation(world):
    presence('pane', aid='page', platform='linkedin')
    readers = eng.readers_for_project(PID)
    assert poll(readers)['linkedin']['new_items'] == 1
    consent((), aid='page', service='linkedin', kind='organization')
    assert 'connections' not in desk._read_store()['accounts']['page']
    before = (world.model.call_count, len(world.generic_pages.reads))
    assert 'mentions' in poll(readers)['linkedin']['permission_denied']
    assert (world.model.call_count, len(world.generic_pages.reads)) == before


@pytest.mark.parametrize('gate', ['budget', 'agent_read_off', 'domain_off_list'])
def test_generic_supported_x_allow_retains_budget_and_profile_policy(world, gate):
    presence('pane', budget=0 if gate == 'budget' else 10)
    consent(('mentions',), route='x-browser')
    if gate != 'budget':
        browser_agent_read.set_policy('main', gate != 'agent_read_off',
                                      ['linkedin.com'] if gate == 'domain_off_list' else ['x.com'])
    reader = digest.PaneDigestReader(PID, 'x', {'browser_profile': 'main', 'read_pages': [
        {'role': 'activity', 'url': eng.X_MENTIONS_URL}]})
    out = poll({'x': reader})['x']
    assert out.get('budget_blocked') is True if gate == 'budget' else out['state'] == 'not_connected'
    no_calls(world)


@pytest.mark.parametrize('mode', ['legacy', 'invalid_explicit'])
def test_unknown_site_digest_preserves_legacy_but_invalid_explicit_fails_closed(world, mode):
    presence('pane', aid='other', platform='unknown-site')
    if mode == 'invalid_explicit':
        with desk._store_lock:
            store = desk._read_store()
            store['accounts']['other'][policy.VERSION_KEY] = policy.VERSION
            desk._write_store(store)
    out = poll()['unknown-site']
    if mode == 'legacy':
        assert out['new_items'] == 1 and world.model.call_count == 1
    else:
        assert 'mentions' in out['permission_denied']
        no_calls(world)


@pytest.mark.parametrize('caps', [(), ('mentions',), ('post_metrics',)])
def test_generic_x_digest_checks_both_scopes_before_page_and_model_calls(world, caps):
    presence('pane')
    post()
    consent(caps, route='x-browser')
    acc = {'browser_profile': 'main', 'read_pages': [
        {'role': 'activity', 'url': eng.X_MENTIONS_URL}, {'role': 'post', 'url': 'https://x.com/'}]}
    reader = digest.PaneDigestReader(PID, 'x', acc)
    out = poll({'x': reader})['x']
    assert world.model.call_count == len(caps)
    assert (out['new_items'] > 0) is ('mentions' in caps)
    assert (out['metrics_written'] > 0) is ('post_metrics' in caps)
    if not caps:
        no_calls(world)


@pytest.mark.parametrize('unattended', [False, True])
def test_real_human_poll_route_reaches_consent_and_retains_human_gate(world, monkeypatch, unattended):
    presence()
    consent()
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', lambda: unattended)
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(desk_routes.bp)
    response = app.test_client().post('/api/desk/engagement/poll', json={'project_id': PID})
    assert response.status_code == (403 if unattended else 200)
    if not unattended:
        assert 'mentions' in response.get_json()['platforms']['x']['permission_denied']
    no_calls(world)
