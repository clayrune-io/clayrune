"""MC-1062/06a: actual publisher, reply-route and tick consumers; no network."""
from __future__ import annotations

import copy
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import desk, desk_accounts, desk_oauth, desk_publish, desk_tick, secrets_store
from mc.blueprints import desk_routes
from mc.desk_connect import permission_check as check
from mc.desk_connect import permission_policy as policy
from mc.desk_connect import route_readiness
from test_desk_tick import NOW, campaign, edit_store, stored, version, world

POST = {'purpose': 'publish', 'capability': 'post', 'route_id': 'x-oauth'}
READ = {'purpose': 'read_own', 'capability': 'mentions', 'route_id': 'x-browser'}


@pytest.fixture(autouse=True)
def isolated_policy_cache():
    policy._forget_all_for_tests()
    yield
    policy._forget_all_for_tests()


def consent(aid, mode='allow', *, service='x', kind='account', scopes=None):
    if mode == 'legacy':
        return
    draft = {'account_id': aid, 'service': service, 'account_kind': kind,
             'read': mode == 'read', 'post': mode == 'allow',
             'scopes': scopes if scopes is not None else [POST] if mode == 'allow' else [READ] if mode == 'read' else []}
    policy.commit('policy-' + aid + '-' + mode, draft)


@pytest.fixture
def publisher(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(desk_publish, 'RECEIPTS_PATH', tmp_path / 'receipts.json')
    desk._write_store({'oauth_legacy_bound': 'first', 'accounts': {
        'first': {'id': 'first', 'platform': 'x', 'identity': 'first', 'capability': 'direct',
                  'credentials': {'oauth_vault': 'oauth.x', 'oauth_profile': 'desk-x'}},
        'second': {'id': 'second', 'platform': 'x', 'identity': 'second', 'capability': 'direct',
                   'credentials': {'oauth_vault': 'oauth.x.second', 'oauth_profile': 'desk-x-second'}},
        'page': {'id': 'page', 'platform': 'linkedin', 'identity': 'Page', 'capability': 'direct',
                 'organization_id': '777'},
    }})
    monkeypatch.setattr(secrets_store, 'list_secrets', lambda: [
        {'name': 'x.oauth-token', 'allow_unattended': True},
        {'name': 'linkedin.oauth-token', 'allow_unattended': True}])
    monkeypatch.setattr(secrets_store, 'is_readable', lambda name: True)
    monkeypatch.setattr(desk_oauth, 'status', lambda *a, **k: {'state': 'not_connected'})
    token = Mock(return_value='valid-token')
    linkedin_token = Mock(return_value='valid-linkedin-token')
    post = Mock(side_effect=lambda token, body, in_reply_to=None: {'data': {'id': 'post-1'}})
    username = Mock(return_value='first')
    linkedin_post = Mock(return_value={'id': 'urn:li:share:1'})
    monkeypatch.setattr(desk_oauth, 'x_token', token)
    monkeypatch.setattr(secrets_store, 'get_secret_value', linkedin_token)
    monkeypatch.setattr(desk_publish, '_post_tweet', post)
    monkeypatch.setattr(desk_publish, '_get_username', username)
    monkeypatch.setattr(desk_publish, '_post_linkedin', linkedin_post)
    wire = Mock(side_effect=AssertionError('unexpected outbound HTTP'))
    monkeypatch.setattr('urllib.request.urlopen', wire)
    return SimpleNamespace(token=token, li_token=linkedin_token, post=post, username=username,
                           li_post=linkedin_post, wire=wire, tmp=tmp_path)


def item(**over):
    return {'id': 'version-1', 'platform': 'x', 'body': 'A plain post', 'account_id': 'first', **over}


def no_outbound(publisher):
    for fn in (publisher.token, publisher.li_token, publisher.post, publisher.username,
               publisher.li_post, publisher.wire):
        fn.assert_not_called()


@pytest.mark.parametrize('mode', ['deny', 'read'])
def test_explicit_deny_or_read_only_blocks_real_publisher_before_oauth_mapping(publisher, monkeypatch, mode):
    consent('first', mode)
    approved = Mock(return_value=[])
    monkeypatch.setattr(desk, 'publish_blockers', approved)
    mapped = Mock(return_value=None)  # first account's real legacy credential mapping
    monkeypatch.setattr(desk_publish._refs, 'oauth_arg_for', mapped)
    with pytest.raises(desk_publish.PublishError, match='permission denied'):
        desk_publish.publish(item(campaign_id='approved-campaign'))
    approved.assert_called_once_with('approved-campaign')
    mapped.assert_not_called()
    no_outbound(publisher)
    assert desk_publish.get_receipt('version-1') is None


@pytest.mark.parametrize('aid,arg', [('first', None), ('second', 'second')])
def test_post_allow_uses_original_account_then_its_own_token(publisher, monkeypatch, aid, arg):
    # second's own sign-in is ready; this is metadata only.
    monkeypatch.setattr(desk_oauth, 'status', lambda *a, **k: {'state': 'connected'})
    consent(aid)
    out = desk_publish.publish(item(account_id=aid))
    assert out['post_id'] == 'post-1'
    assert publisher.token.call_args.kwargs['account_id'] == arg
    publisher.post.assert_called_once_with('valid-token', 'A plain post')
    publisher.username.assert_called_once_with('valid-token')


@pytest.mark.parametrize('aid', ['first', None])
def test_legacy_account_and_pre_account_caller_keep_reply_behavior(publisher, aid):
    desk_publish.publish(item(account_id=aid, in_reply_to='parent-1'))
    publisher.token.assert_called_once()
    publisher.post.assert_called_once_with('valid-token', 'A plain post', 'parent-1')


@pytest.mark.parametrize('mode', ['deny', 'read', 'allow'])
def test_omitting_account_id_cannot_bypass_singleton_owners_consent(publisher, mode):
    consent('first', mode)
    if mode == 'allow':
        desk_publish.publish(item(account_id=None))
        publisher.post.assert_called_once()
    else:
        with pytest.raises(desk_publish.PublishError, match='permission denied'):
            desk_publish.publish(item(account_id=None))
        no_outbound(publisher)


def test_pre_account_caller_with_no_workspace_owner_still_uses_legacy_token(publisher):
    desk._write_store({})
    desk_publish.publish(item(account_id=None, in_reply_to='parent-1'))
    publisher.post.assert_called_once_with('valid-token', 'A plain post', 'parent-1')


def test_singleton_tombstone_never_borrows_another_accounts_permission(publisher):
    with desk._store_lock:
        store = desk._read_store()
        del store['accounts']['first']  # do not free its legacy-token ownership marker
        desk._write_store(store)
    with pytest.raises(desk_publish.PublishError, match='not found'):
        desk_publish.publish(item(account_id=None))
    no_outbound(publisher)


def test_singleton_reference_enforces_consent_even_without_the_ownership_marker(publisher):
    consent('first', 'deny')
    with desk._store_lock:
        store = desk._read_store()
        store.pop('oauth_legacy_bound')
        desk._write_store(store)
    with pytest.raises(desk_publish.PublishError, match='permission denied'):
        desk_publish.publish(item(account_id=None))
    no_outbound(publisher)


@pytest.mark.parametrize('damage', ['ambiguous', 'bad_record_id'])
def test_missing_marker_with_invalid_singleton_ownership_fails_closed(publisher, damage):
    with desk._store_lock:
        store = desk._read_store()
        store.pop('oauth_legacy_bound')
        if damage == 'ambiguous':
            store['accounts']['second']['credentials']['oauth_vault'] = 'oauth.x'
        else:
            store['accounts']['first']['id'] = None
        desk._write_store(store)
    with pytest.raises(desk_publish.PublishError, match='account|ambiguous'):
        desk_publish.publish(item(account_id=None))
    no_outbound(publisher)


@pytest.mark.parametrize('aid', ['', 'missing', 'page', [], 17])
def test_invalid_or_wrong_service_account_never_uses_singleton(publisher, aid):
    with pytest.raises(desk_publish.PublishError, match='account'):
        desk_publish.publish(item(account_id=aid))
    no_outbound(publisher)


@pytest.mark.parametrize('parent', ['parent-1', None, ''])
def test_explicit_allow_never_covers_reply_or_caller_claims(publisher, parent):
    consent('first')
    with pytest.raises(desk_publish.PublishError, match='publish/reply'):
        desk_publish.publish(item(in_reply_to=parent, capability='post', route_id='x-oauth', approved=True))
    no_outbound(publisher)


@pytest.mark.parametrize('damage', ['lost_record', 'wrong_route', 'wrong_identity', 'wrong_kind'])
def test_corrupt_explicit_policy_cannot_become_legacy(publisher, damage):
    consent('first')
    with desk._store_lock:
        store = desk._read_store()
        rec = store['accounts']['first']
        if damage == 'lost_record':
            del rec[policy.POLICY_KEY]
        else:
            draft = {k: copy.deepcopy(rec[policy.POLICY_KEY][k]) for k in policy._FIELDS}
            if damage == 'wrong_route':
                draft['scopes'][0]['route_id'] = 'x-browser'
            elif damage == 'wrong_identity':
                draft['account_id'] = 'second'
            else:
                draft['account_kind'] = 'member'
            rec[policy.POLICY_KEY] = policy._record(draft)
        desk._write_store(store)
    with pytest.raises(desk_publish.PublishError, match='permission denied'):
        desk_publish.publish(item())
    no_outbound(publisher)


def test_shared_helper_exact_scope_and_legacy_tristate(publisher):
    assert check.require_permission('first', 'x', 'post', **POST, account_kind='account') is None
    consent('first')
    assert check.require_permission('first', 'x', 'post', **POST, account_kind='account') is True
    for scope in ({**POST, 'route_id': 'x-browser'}, {**POST, 'capability': 'media'},
                  {**POST, 'capability': 'reply'}):
        with pytest.raises(check.PermissionDenied):
            check.require_permission('first', 'x', 'post', **scope, account_kind='account')
    no_outbound(publisher)


def test_shared_read_seam_is_exact_and_invalid_operations_never_use_legacy(publisher):
    consent('first', 'read')
    assert check.require_permission('first', 'x', 'read', **READ, account_kind='account') is True
    with pytest.raises(check.PermissionDenied):
        check.require_permission('first', 'x', 'read', **{**READ, 'route_id': 'x-oauth'})
    with pytest.raises(check.PermissionDenied):
        check.require_permission(None, 'x', 'install', **POST)
    no_outbound(publisher)


@pytest.mark.parametrize('patch', [{'preview': True}, {'capability': 'manual'}])
def test_allow_cannot_turn_preview_or_manual_account_into_api_publisher(publisher, patch):
    consent('first')
    with desk._store_lock:
        store = desk._read_store()
        store['accounts']['first'].update(patch)
        desk._write_store(store)
    with pytest.raises(desk_publish.PublishError, match='publish|preview'):
        desk_publish.publish(item())
    no_outbound(publisher)


def test_post_allow_still_needs_ready_provider(publisher, monkeypatch):
    consent('second')
    with pytest.raises(desk_publish.PublishError, match='not signed in'):
        desk_publish.publish(item(account_id='second'))
    no_outbound(publisher)


def test_allow_cannot_open_linkedin_gate_even_with_an_executor(publisher, monkeypatch):
    # Simulate only the future executor registration; retain the real closed provider gate.
    monkeypatch.setitem(route_readiness.EXECUTORS, ('linkedin', 'linkedin-oauth', 'publish'), 'api')
    consent('page', service='linkedin', kind='organization',
            scopes=[{'purpose': 'publish', 'capability': 'post', 'route_id': 'linkedin-oauth'}])
    assert desk_accounts.LINKEDIN_ORG_POSTING_APPROVED is False
    with pytest.raises(desk_publish.PublishError, match='LinkedIn'):
        desk_publish.publish(item(platform='linkedin', account_id='page', organization_id='777'))
    no_outbound(publisher)


def test_idempotent_receipt_is_a_fact_after_post_revocation(publisher):
    consent('first')
    first = desk_publish.publish(item())
    consent('first', 'deny')
    assert desk_publish.publish(item()) == first
    assert publisher.token.call_count == publisher.post.call_count == 1
    with pytest.raises(desk_publish.PublishError, match='permission denied'):
        desk_publish.publish(item(id='next-version'))
    assert publisher.token.call_count == publisher.post.call_count == 1


@pytest.mark.parametrize('mode', ['deny', 'allow', 'legacy'])
@pytest.mark.parametrize('scheduled', [False, True])
def test_actual_manual_approval_and_scheduled_tick_consume_permission(world, monkeypatch, mode, scheduled):
    campaign(world)
    piece, vid = version(world)
    consent('ch-x', mode)
    token = Mock(wraps=desk_oauth.x_token)
    monkeypatch.setattr(desk_oauth, 'x_token', token)
    if scheduled:
        when = NOW + timedelta(hours=1)
        r = world.client.post(f'/api/desk/pieces/{piece}/versions/{vid}/approve',
                             json={'scheduled_at': when.isoformat(), 'passcode': 'test-passcode'})
        assert r.status_code == 200
        assert stored(piece, vid)['state'] == 'scheduled' and world.wire.posts == []
        desk_tick.run_once(when + timedelta(seconds=1))
    else:
        r = world.client.post(f'/api/desk/pieces/{piece}/versions/{vid}/approve',
                             json={'passcode': 'test-passcode'})
        assert r.status_code == 200
    if mode == 'deny':
        assert stored(piece, vid)['state'] == 'failed'
        assert 'permission denied' in stored(piece, vid)['failure']['reason']
        assert world.wire.posts == [] and world.wire.verifies == 0 and world.vault.reads == []
        token.assert_not_called()
    else:
        assert stored(piece, vid)['state'] == 'verified_published'
        assert len(world.wire.posts) == 1 and world.wire.verifies == 1
        assert world.vault.reads[0][2] is scheduled


@pytest.mark.parametrize('gate', ['paused', 'unapproved', 'budget_widened', 'provider', 'attended_only'])
def test_allow_does_not_bypass_tick_campaign_provider_or_budget_bounds(world, monkeypatch, gate):
    campaign(world)
    piece, vid = version(world)
    consent('ch-x')
    desk_tick._pieces.approve_version(piece, vid, scheduled_at=(NOW + timedelta(hours=1)).isoformat(), now=NOW)
    if gate == 'paused':
        edit_store(lambda s: s['campaigns']['camp-t'].update(state='paused'))
    elif gate == 'unapproved':
        edit_store(lambda s: s['campaigns']['camp-t'].pop('approved'))
    elif gate == 'budget_widened':
        edit_store(lambda s: s['campaigns']['camp-t'].setdefault('how', {}).update(
            budget={'source': 'own', 'amount': 10}))
    elif gate == 'provider':
        world.vault.entries.clear()
    else:
        world.vault.entries['x.oauth-token']['allow_unattended'] = False
    token = Mock(wraps=desk_oauth.x_token)
    monkeypatch.setattr(desk_oauth, 'x_token', token)
    desk_tick.run_once(NOW + timedelta(hours=2))
    assert stored(piece, vid)['state'] == 'held'
    assert world.wire.posts == [] and world.vault.reads == []
    token.assert_not_called()


@pytest.mark.parametrize('gate', ['paused', 'unapproved', 'budget_widened'])
def test_direct_publisher_allow_retains_current_campaign_and_budget_approval(world, monkeypatch, gate):
    campaign(world)
    consent('ch-x')
    if gate == 'paused':
        edit_store(lambda s: s['campaigns']['camp-t'].update(state='paused'))
    elif gate == 'unapproved':
        edit_store(lambda s: s['campaigns']['camp-t'].pop('approved'))
    else:
        edit_store(lambda s: s['campaigns']['camp-t'].setdefault('how', {}).update(
            budget={'source': 'own', 'amount': 10}))
    token = Mock(wraps=desk_oauth.x_token)
    monkeypatch.setattr(desk_oauth, 'x_token', token)
    with pytest.raises(desk_publish.PublishError, match='not approved'):
        desk_publish.publish(item(account_id='ch-x', campaign_id='camp-t'))
    assert world.wire.posts == [] and world.vault.reads == []
    token.assert_not_called()


@pytest.mark.parametrize('mode,status', [('deny', 502), ('allow', 502), ('legacy', 200)])
def test_human_reply_route_with_real_publisher_cannot_borrow_post_permission(publisher, monkeypatch, mode, status):
    consent('first', mode)
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', lambda: False)
    monkeypatch.setattr(desk_routes, '_require_human_passcode', lambda data: None)
    desk.upsert_presence('alpha', {'accounts': [{'channel_id': 'first', 'platform': 'x'}]})
    row, _ = desk.upsert_engagement_item({'project_id': 'alpha', 'platform': 'x',
                                         'external_id': 'parent-1', 'source': 'mentions', 'excerpt': 'Hi'})
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(desk_routes.bp)
    r = app.test_client().post(f'/api/desk/engagement/{row["id"]}/reply', json={'text': 'Hello'})
    assert r.status_code == status, r.get_json()
    if mode == 'legacy':
        publisher.post.assert_called_once_with('valid-token', 'Hello', 'parent-1')
        assert desk.get_engagement_item(row['id'])['state'] == 'sent'
    else:
        assert 'publish/reply' in r.get_json()['error']
        assert desk.get_engagement_item(row['id'])['state'] != 'sent'
        no_outbound(publisher)


@pytest.mark.parametrize('gate', ['unattended', 'passcode'])
def test_real_reply_consumer_retains_human_gate_before_publisher(publisher, monkeypatch, gate):
    consent('first')
    monkeypatch.setattr(desk_routes, 'is_unattended_caller', lambda: gate == 'unattended')
    monkeypatch.setattr(desk_routes, '_require_human_passcode',
                        lambda data: (jsonify({'error': 'passcode_required'}), 403))
    row, _ = desk.upsert_engagement_item({'project_id': 'alpha', 'platform': 'x',
                                         'external_id': 'parent-1', 'source': 'mentions', 'excerpt': 'Hi'})
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(desk_routes.bp)
    r = app.test_client().post(f'/api/desk/engagement/{row["id"]}/reply', json={'text': 'Hello'})
    assert r.status_code == 403
    no_outbound(publisher)
