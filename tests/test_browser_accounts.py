"""Desk metadata projects to Vault; only explicit check evidence raises requests."""
import json

import pytest
from flask import Flask

from mc import desk, desk_browser_accounts as accounts
from mc.blueprints import browser_account_routes, browser_routes
from mc.desk_connect import browser_check, browser_connections, registry, signin_method
from mc.desk_connect.browser_setup import SetupError
from mc.desk_connect.providers.base import Probe


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(browser_routes, 'named_profile_exists', lambda _: True)
    browser_connections._done.clear()
    data = desk._empty_store()
    data['services'] = {'svc-example': {'id':'svc-example', 'name':'Example', 'link':'https://example.com',
        'credential':'PRIVATE-REFERENCE', 'value':'NEVER-RETURN', 'browser_connection': {
            'service':None, 'browser_profile':'main', 'signin_method':'google', 'cookie':'NEVER-RETURN'}}}
    data['accounts'] = {'acc-tiktok': {'id':'acc-tiktok', 'platform':'tiktok', 'identity':'person',
        'browser_profile':'main', 'signin_method':'phone_code', 'credentials':{'token':'NEVER-RETURN'}}}
    desk._write_store(data)
    return data


def target(url='https://example.com', profile='main', **over):
    return {'url':url, 'browser_profile':profile, 'check':True, 'confirmed':False, **over}


def expire(t=None):
    accounts.observe(t or target(), {'state':'check_failed','failure':'signed_out','verifiable':True})


def test_projection_is_allowlisted_metadata_without_vault_access(store, monkeypatch):
    from mc import secrets_store
    monkeypatch.setattr(secrets_store, 'list_secrets', lambda *a, **k: pytest.fail('vault read'))
    rows = accounts.snapshot()['browser_accounts']
    assert len(rows) == 2 and all(r['read_only'] for r in rows)
    assert {r['signin_method'] for r in rows} == {'google','phone_code'}
    assert 'NEVER-RETURN' not in json.dumps(rows)
    assert 'PRIVATE-REFERENCE' not in json.dumps(rows)


def test_account_setup_wins_over_legacy_read_profile(store):
    store['accounts']['acc-tiktok']['browser_setup'] = {'service':'tiktok','route_id':'browser',
        'refs':{'browser_profile':'signin-profile','login':'private-name'},'signin_method':'google'}
    row = accounts.project(store)[0]
    assert row['browser_profile'] == 'signin-profile' and row['signin_method'] == 'google'
    assert 'private-name' not in json.dumps(row)


def test_legacy_missing_method_is_other_without_backfill(store):
    del store['services']['svc-example']['browser_connection']['signin_method']
    desk._write_store(store)
    before = desk.STORE_PATH.read_bytes()
    row = accounts.snapshot()['browser_accounts'][1]
    assert row['signin_method'] == 'other'
    assert desk.STORE_PATH.read_bytes() == before


@pytest.mark.parametrize('setup', [None, 'broken', {'browser_profile':None}, {'browser_profile':'main'}])
def test_bad_or_absent_service_references_are_skipped(store, setup):
    store['services']['svc-example']['browser_connection'] = setup
    if isinstance(setup, dict):
        store['services']['svc-example']['link'] = 'http://example.com'
    assert len(accounts.project(store)) == 1


@pytest.mark.parametrize('url', ['https://[', 'not a URL', 'https://'])
def test_bad_saved_address_cannot_hide_other_rows(store, url):
    store['services']['svc-example']['link'] = url
    assert len(accounts.project(store)) == 1


def test_malformed_label_cannot_expose_nested_data(store):
    store['accounts']['acc-tiktok']['label'] = {'token':'NEVER-RETURN'}
    assert 'NEVER-RETURN' not in json.dumps(accounts.project(store))


def test_dedupe_and_clear_persist_across_snapshots(store):
    expire()
    before = desk.STORE_PATH.read_bytes()
    expire()
    assert desk.STORE_PATH.read_bytes() == before
    assert len(accounts.snapshot()['signin_requests']) == 1
    accounts.observe(target(), {'state':'verified'})
    assert accounts.snapshot()['signin_requests'] == []
    assert not desk._read_store()[accounts.REQUESTS]


def test_unknown_manual_confirmation_clears_only_confirmed_evidence(store):
    expire()
    accounts.observe(target(confirmed=True), {'state':'not_checked'})
    assert accounts.snapshot()['signin_requests']
    accounts.observe(target(confirmed=True), {'state':'not_checked','at':'2026-10-23'})
    assert accounts.snapshot()['signin_requests'] == []


@pytest.mark.parametrize('verdict', [
    {'state':'check_failed','failure':'unavailable'}, {'state':'unknown'},
    {'state':'not_checked'}, {'state':'sign_in_required','verifiable':False},
])
def test_no_request_from_inconclusive_or_transport_results(store, verdict):
    accounts.observe(target(), verdict)
    assert accounts.snapshot()['signin_requests'] == []


def test_unchecked_status_cannot_raise_or_clear(store):
    accounts.observe(target(check=False), {'state':'check_failed','failure':'signed_out'})
    assert not accounts.snapshot()['signin_requests']
    expire()
    accounts.observe(target(check=False), {'state':'verified'})
    assert accounts.snapshot()['signin_requests']


def test_missing_profile_raises_from_explicit_check_only(store, monkeypatch):
    monkeypatch.setattr(browser_routes, 'named_profile_exists', lambda _: False)
    accounts.observe(target(), {'state':'sign_in_required','verifiable':True})
    assert accounts.snapshot()['signin_requests'][0]['message'] == 'Sign in again'


def test_one_service_request_covers_multiple_accounts_profiles(store):
    store['accounts']['second'] = {**store['accounts']['acc-tiktok'], 'id':'second', 'browser_profile':'second'}
    desk._write_store(store)
    url = registry.profile('tiktok')['home_url']
    expire(target(url))
    expire(target(url,'second'))
    rows = accounts.snapshot()['signin_requests']
    assert len(rows) == 1 and set(rows[0]['profiles']) == {'main','second'}
    accounts.observe(target(url), {'state':'verified'})
    assert accounts.snapshot()['signin_requests'][0]['profiles'] == ['second']
    accounts.observe(target(url,'second'), {'state':'verified'})
    assert accounts.snapshot()['signin_requests'] == []


def test_removed_connections_and_unsaved_checks_do_not_raise(store):
    expire(target('https://unsaved.example'))
    assert not accounts.snapshot()['signin_requests']


def test_replacement_record_does_not_inherit_old_request(store):
    expire()
    service = store['services'].pop('svc-example')
    service['id'] = 'svc-replacement'
    store[accounts.REQUESTS] = desk._read_store()[accounts.REQUESTS]
    store['services']['svc-replacement'] = service
    desk._write_store(store)
    assert not accounts.snapshot()['signin_requests']
    expire()
    store['services'].clear()
    desk._write_store(store)
    assert not accounts.snapshot()['signin_requests']


def test_actual_check_hook_observes_verdict_but_status_does_not(store, monkeypatch):
    monkeypatch.setattr(browser_check, 'profile_stamp', lambda _: ('stamp',))
    monkeypatch.setattr(browser_check.BrowserProvider, 'verify', lambda *a: Probe(False,'signed out',kind='signed_out'))
    clean = browser_check.clean({'url':'https://example.com','browser_profile':'main','check':False})
    browser_check.run(clean)
    assert not accounts.snapshot()['signin_requests']
    browser_check.run({**clean,'check':True})
    assert accounts.snapshot()['signin_requests']
    monkeypatch.setattr(browser_check.BrowserProvider, 'verify', lambda *a: Probe(True,'passed'))
    browser_check.run({**clean,'check':True})
    assert not accounts.snapshot()['signin_requests']


def test_read_only_route_works_locked_and_has_no_write_path(store, monkeypatch):
    from mc import secrets_store
    monkeypatch.setattr(secrets_store, 'is_locked', lambda: True)
    app = Flask(__name__)
    app.register_blueprint(browser_account_routes.bp)
    client = app.test_client()
    r = client.get('/api/secrets/browser-accounts')
    assert r.status_code == 200 and len(r.json['browser_accounts']) == 2
    assert b'NEVER-RETURN' not in r.data
    for method in ('post','patch','delete'):
        assert getattr(client, method)('/api/secrets/browser-accounts').status_code == 405


@pytest.mark.parametrize('method', signin_method.METHODS)
@pytest.mark.parametrize('url', ['https://example.org','https://tiktok.com'])
def test_generic_and_pane_accounts_save_method(store, method, url):
    clean = browser_connections.clean_draft({'name':'new-person','url':url,'browser_profile':'new-profile','signin_method':method})
    result, replay = browser_connections.commit('request-method-1', clean)
    assert not replay and result['signin_method'] == method
    saved = next(r for r in accounts.snapshot()['browser_accounts'] if r['source_id'] == result.get('account_id', result['connection_id']))
    assert saved['signin_method'] == method
    if result.get('account_id'):
        from mc import desk_accounts
        listed = next(r for r in desk_accounts.list_accounts() if r['id'] == result['account_id'])
        assert listed['signin_method'] == method
    assert browser_connections.commit('request-method-1', clean)[1]
    with pytest.raises(SetupError):
        browser_connections.commit('request-method-1', {**clean,'signin_method':'invalid'})


@pytest.mark.parametrize('method', ['sms', '', None, [], {}, 1])
def test_bad_methods_refused_before_save(store, method):
    before = desk.STORE_PATH.read_bytes()
    with pytest.raises(SetupError):
        browser_connections.clean_draft({'name':'test','url':'https://example.org','browser_profile':'main','signin_method':method})
    assert desk.STORE_PATH.read_bytes() == before
