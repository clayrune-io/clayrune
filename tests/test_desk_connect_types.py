"""Desk connection types, MC-1062 ticket 01 (docs/desk_v1/connect_flow_tickets/01-type-projection.md):
the read-only Sign in / API / MCP projection of a service.

Pinned:

  * X offers a browser sign-in setup and an OAuth API setup in the new contract; LinkedIn offers a
    member/Page browser setup and a truthful unavailable API (restricted/missing adapter), never
    an available one;
  * a custom npm package and a remote server are offered for every address, a known service
    and an unknown one, without being in any catalogue;
  * setup support and runtime execution are separate answers: LinkedIn browser setup works
    and the Desk still does not run it; a PyPI detection and API details never execute;
  * Q1: only variants Clayrune can set up reach `picker`; the rest are under `details`
    with an explanation, and no unavailable route is promoted;
  * a browser route needs no `connect_method` and the projection leaves the version 1 view alone;
  * the projection opens no vault and no network, and the route is read-only and answers
    the same errors as `inspect`.
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

from mc.desk_connect import registry, type_view  # noqa: E402


def _types(picker):
    return [t['id'] for t in picker]


def _variants(out):
    return {v['id']: v for t in out['picker'] for v in t['variants']}


def _hidden(out):
    return {v['id']: v for v in out['details']['unavailable']}


# ── known services ───────────────────────────────────────────────────────────

def test_x_has_browser_oauth_and_custom_mcp_setup():
    out = type_view.project_service('x')
    assert _types(out['picker']) == ['signin', 'api', 'mcp']
    v = _variants(out)
    assert v['x-browser']['setup'] == {'mode': 'full', 'via': 'browser_signin',
                                       'signin': {'url': 'https://x.com/i/flow/login', 'hosts': ['x.com']}}
    assert v['x-oauth']['setup'] == {'mode': 'full', 'via': 'provider', 'method': 'oauth', 'provider': 'x', 'signs_in': True}
    assert v['x-oauth']['route_id'] == 'x-oauth' and v['x-oauth']['account_kinds'] == ['account']
    assert {'custom-npm', 'custom-remote'} <= set(v)


def test_x_hidden_variants_are_explained_not_offered():
    out = type_view.project_service('x')
    hidden = _hidden(out)
    assert {'x-mcp-action', 'x-docs-mcp', 'api-details', 'detected-pypi'} <= set(hidden)
    assert hidden['x-mcp-action']['explanation']['code'] == 'missing_adapter'
    assert hidden['x-mcp-action']['setup']['mode'] == 'none'
    assert all(v['explanation']['text'] for v in hidden.values())
    assert not set(hidden) & set(_variants(out))
    assert [d['route_id'] for d in out['details']['delivery']] == ['x-manual']      # a delivery route, never a connection


def test_linkedin_member_and_page_browser_setup_and_unavailable_api():
    out = type_view.project_service('linkedin')
    assert _types(out['picker']) == ['signin', 'mcp']                              # no API type in the primary picker
    browser = _variants(out)['linkedin-browser']
    assert browser['setup']['mode'] == 'full' and browser['setup']['signin']['hosts'] == ['www.linkedin.com']
    assert browser['account_kinds'] == ['member', 'organization']
    assert {k['id'] for k in out['service']['account_kinds']} == {'member', 'organization'}
    hidden = _hidden(out)
    assert hidden['linkedin-oauth']['explanation']['code'] == 'restricted'
    for rid in ('linkedin-oauth', 'linkedin-member-api', 'linkedin-member-community-api'):
        assert hidden[rid]['setup']['mode'] == 'none' and hidden[rid]['type'] == 'api'
    assert not any(v['type'] == 'api' for v in _variants(out).values())


def test_setup_support_is_separate_from_runtime_execution():
    x = _variants(type_view.project_service('x'))
    assert x['x-browser']['runtime']['purposes'] == {'read_own': 'pane'} and x['x-browser']['runtime']['desk_executes']
    assert x['x-oauth']['runtime']['purposes'] == {'publish': 'api', 'read_own': 'api'}
    li = _variants(type_view.project_service('linkedin'))['linkedin-browser']
    assert li['setup']['mode'] == 'full'
    assert li['runtime']['purposes'] == {} and li['runtime']['desk_executes'] is False
    assert 'does not run' in li['runtime']['note']


def test_custom_variants_never_claim_execution():
    for sid in ('x', 'linkedin', None):
        out = type_view.project_service(sid)
        everything = {**_variants(out), **_hidden(out)}
        for vid in ('custom-npm', 'custom-remote', 'detected-pypi', 'api-details'):
            assert everything[vid]['runtime']['desk_executes'] is False and everything[vid]['runtime']['purposes'] == {}
        assert everything['detected-pypi']['setup']['mode'] == 'none'
        assert everything['api-details']['setup']['mode'] == 'reference_only'
        assert everything['custom-npm']['setup']['endpoints']['review'] == '/api/desk/connect/custom/review'
        assert everything['custom-remote']['setup']['endpoints']['review'] == '/api/desk/connect/custom/remote/review'


def test_catalogue_and_engine_services_keep_their_provider_routes():
    notion = _variants(type_view.project_service('notion'))
    assert notion['notion-mcp']['setup']['via'] == 'provider' and notion['notion-mcp']['setup']['method'] == 'mcp'
    assert notion['notion-mcp']['runtime']['adapter'] == 'notion'
    hf = type_view.project_service('higgsfield')
    assert {'higgsfield-api-key', 'higgsfield-oauth'} <= set(_variants(hf))
    assert _variants(hf)['higgsfield-oauth']['type'] == 'mcp'                      # its sign-in IS its MCP


def test_service_with_no_provider_stays_unavailable():
    for sid, rid in (('youtube', 'youtube-oauth'), ('google_drive', 'google-drive-oauth')):
        out = type_view.project_service(sid)
        assert _types(out['picker']) == ['mcp']
        assert _hidden(out)[rid]['explanation']['code'] == 'missing_adapter'


# ── unknown addresses ────────────────────────────────────────────────────────

def test_unknown_address_gets_custom_mcp_and_the_reference_but_no_sign_in():
    out = type_view.project(None)
    assert out['service']['recognised'] is False
    assert _types(out['picker']) == ['mcp']
    assert set(_variants(out)) == {'custom-npm', 'custom-remote'}
    hidden = _hidden(out)
    assert hidden['signin-unreviewed']['explanation']['code'] == 'no_reviewed_signin'
    assert out['reference']['method'] == 'save_for_agents' and out['reference']['endpoint'] == '/api/desk/connect/commit'
    assert 'detected-pypi' in hidden and 'api-details' in hidden


def test_unknown_service_id_projects_as_unknown_address():
    assert type_view.project_service('no_such_service') == type_view.project(None)
    assert type_view.project_service(None)['service']['recognised'] is False


# ── fixtures: pure, no registry, no provider table ───────────────────────────

class _Prov:
    def __init__(self, methods, signs_in=()):
        self.methods, self.signs_in = set(methods), frozenset(signs_in)

    def supports(self, method):
        return method in self.methods


def _profile():
    p = copy.deepcopy(registry.profile('x'))
    p['service_id'] = 'fixture'
    return p


def test_browser_route_needs_no_connect_method():
    p = _profile()
    browser = next(r for r in p['routes'] if r['id'] == 'x-browser')
    assert 'connect_method' not in browser
    out = type_view.project(p, provider_for=lambda s: None, executor=lambda *a: None)
    assert _variants(out)['x-browser']['setup']['mode'] == 'full'
    assert not any(o['method'] == 'browser_signin' for o in registry.lookup('x.com')['options'])   # the v1 view omits it


def test_browser_route_without_a_declared_signin_page_is_not_set_up():
    p = _profile()
    next(r for r in p['routes'] if r['id'] == 'x-browser').pop('signin')
    out = type_view.project(p, provider_for=lambda s: None, executor=lambda *a: None)
    assert 'x-browser' not in _variants(out)
    assert _hidden(out)['x-browser']['explanation']['code'] == 'no_signin_declared'


def test_available_route_without_a_supporting_provider_is_not_promoted():
    p = _profile()
    out = type_view.project(p, provider_for=lambda s: None, executor=lambda *a: None)
    assert 'x-oauth' not in _variants(out) and _hidden(out)['x-oauth']['explanation']['code'] == 'missing_adapter'
    out = type_view.project(p, provider_for=lambda s: _Prov({'api_key'}), executor=lambda *a: None)
    assert 'x-oauth' not in _variants(out)                                          # supports a different method
    out = type_view.project(p, provider_for=lambda s: _Prov({'oauth'}, {'oauth'}), executor=lambda *a: None)
    assert _variants(out)['x-oauth']['setup']['signs_in'] is True


def test_profile_info_only_flag_is_read_not_promoted():
    p = _profile()
    for r in p['routes']:
        if r['id'] == 'x-oauth':
            r['support'] = 'info_only'
    out = type_view.project(p, provider_for=lambda s: _Prov({'oauth'}), executor=lambda *a: None)
    assert 'x-oauth' not in _variants(out)
    assert _hidden(out)['x-oauth']['setup']['mode'] == 'none'


def test_picker_never_lists_more_than_four_types_and_keeps_order():
    out = type_view.project(None)
    assert len(out['picker']) <= type_view.MAX_PICKER
    assert _types(type_view.project_service('x')['picker']) == list(type_view.TYPE_ORDER)


def test_projection_opens_no_vault_and_leaks_no_value(monkeypatch):
    from mc import secrets_store

    def boom(*a, **k):
        raise AssertionError('the projection must not touch the vault')
    monkeypatch.setattr(secrets_store, 'list_secrets', boom)
    monkeypatch.setattr(secrets_store, 'get_secret', boom, raising=False)
    for sid in ('x', 'linkedin', 'notion', 'higgsfield', None):
        text = json.dumps(type_view.project_service(sid))
        assert 'present' not in text and '"vault"' not in text


def test_every_profile_projects_with_unique_variant_ids():
    for p in registry.registry()['snapshot']['profiles']:
        out = type_view.project(p)
        ids = [v['id'] for t in out['picker'] for v in t['variants']] + [v['id'] for v in out['details']['unavailable']]
        assert len(ids) == len(set(ids)), p['service_id']
        assert all(v['setup']['mode'] != 'full' for v in out['details']['unavailable'])


# ── the route ────────────────────────────────────────────────────────────────

@pytest.fixture()
def client():
    from mc.blueprints import desk_connect_type_routes as routes
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(routes.bp)
    return app.test_client()


def test_route_answers_a_name_and_an_address(client):
    a = client.post('/api/desk/connect/types', json={'input': 'x.com'}).get_json()
    b = client.post('/api/desk/connect/types', json={'input': 'LinkedIn'}).get_json()
    assert a['service']['id'] == 'x' and a['host'] == 'x.com' and _types(a['picker']) == ['signin', 'api', 'mcp']
    assert b['service']['id'] == 'linkedin' and b['input_kind'] == 'name' and _types(b['picker']) == ['signin', 'mcp']
    u = client.post('/api/desk/connect/types', json={'url': 'https://plausible.io/mysite'}).get_json()
    assert u['service']['recognised'] is False and u['host'] == 'plausible.io' and _types(u['picker']) == ['mcp']


def test_route_errors_match_inspect(client):
    r = client.post('/api/desk/connect/types', json={'input': 'http://plausible.io'})
    assert r.status_code == 400 and 'error' in r.get_json()
    r = client.post('/api/desk/connect/types', json={'input': 'zzz unknown name'})
    assert r.status_code == 400 and r.get_json()['code'] and 'suggestions' in r.get_json()
    assert client.post('/api/desk/connect/types', json={}).status_code == 400
    assert client.post('/api/desk/connect/types', data='nope').status_code == 400


def test_route_is_post_only_and_inspect_is_unchanged(client):
    assert client.get('/api/desk/connect/types').status_code == 405
    from mc.desk_connect import methods
    out = methods.inspect('x.com')
    assert set(out) == {'url', 'host', 'path', 'input_kind', 'service', 'options'}
    assert 'picker' not in out
