"""Catalogue contracts with stubbed vendors; no vault reads, uploads or spend."""
from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from flask import Flask

from mc import desk, desk_engines as eng, desk_storyboard
from mc.blueprints import desk_model_discovery_routes as routes
from mc.desk_higgsfield_mcp import HiggsfieldMcpAdapter
from mc.desk_model_discovery import catalogue, common, google, higgsfield, higgsfield_mcp, openai, quotes
from mc.desk_connect import higgsfield_mcp_snapshot


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(eng, '_creds', lambda *a: eng._Creds(secret='SENTINEL_TOKEN', user='KEY_ID'))
    monkeypatch.setattr(eng.secrets_store, 'redact', lambda text: str(text).replace('SENTINEL_TOKEN', '[redacted]'))
    # These are never permitted boundaries for either catalogue or quotes.
    def forbidden(*a, **kw):
        pytest.fail('Discovery attempted generation, preparation, upload or state writes')
    monkeypatch.setattr(HiggsfieldMcpAdapter, 'prepare', forbidden)
    monkeypatch.setattr(HiggsfieldMcpAdapter, 'submit', forbidden)
    monkeypatch.setattr(eng, '_read_asset', forbidden)
    monkeypatch.setattr(eng, '_write_store', forbidden)
    return tmp_path


def hf_row(mid='new_video', **fields):
    return {'id': mid, 'name': 'New Video', 'output_type': 'video', 'durations': [5, 10],
            'aspect_ratios': ['16:9'], **fields}


def test_hf_search_traverses_pages_and_never_follows_suggestions(setup, monkeypatch):
    calls = []
    pages = iter([{'items': [hf_row()], 'next_page_token': 'page2',
                   'recovery_tool': 'generate_video', 'recovery_tool_args': {'get_cost': False}},
                  {'items': [hf_row('second'), hf_row('wrong', output_type='image')]}])
    def call(self, creds, tool, args, **kw):
        calls.append((tool, args, kw))
        return {'structuredContent': next(pages)}
    monkeypatch.setattr(HiggsfieldMcpAdapter, '_call', call)
    out = higgsfield_mcp.discover_models(eng._Creds('fake'), kind='video', query='fast')
    assert [m['model_id'] for m in out] == ['new_video', 'second']
    assert calls == [('models_explore', {'action': 'search', 'type': 'video', 'limit': 100, 'query': 'fast'}, {'free': True}),
                     ('models_explore', {'action': 'search', 'type': 'video', 'limit': 100,
                                        'query': 'fast', 'after': 'page2'}, {'free': True})]
    assert all(m['price'] == {'state': 'unknown'} for m in out)


@pytest.mark.parametrize('out', [{}, {'items': {}}, {'items': [None]},
                                   {'items': [{}]}, {'items': [hf_row(id='bad id')]},
                                   {'items': [], 'has_more': True},
                                   {'items': [], 'next_page_token': 123}])
def test_hf_malformed_pages_refused(setup, monkeypatch, out):
    monkeypatch.setattr(HiggsfieldMcpAdapter, '_call', lambda *a, **kw: {'structuredContent': out})
    with pytest.raises(eng.EngineError):
        higgsfield_mcp.discover_models(eng._Creds('fake'), kind='video')


def test_repeating_cursor_and_late_page_failure_do_not_cache_subset(setup, monkeypatch):
    monkeypatch.setattr(HiggsfieldMcpAdapter, '_call', lambda *a, **kw: {
        'structuredContent': {'items': [hf_row()], 'next_page_token': 'same'}})
    with pytest.raises(eng.EngineError, match='repeated'):
        catalogue.discover('higgsfield_mcp', kind='video')
    assert not catalogue.cache_dir().exists()
    pages = iter([{'items': [hf_row()], 'next_page_token': 'p2'}, {}])
    monkeypatch.setattr(HiggsfieldMcpAdapter, '_call', lambda *a, **kw: {'structuredContent': next(pages)})
    with pytest.raises(eng.EngineError):
        catalogue.discover('higgsfield_mcp', kind='video')
    assert not catalogue.cache_dir().exists()


def test_google_pagination_filters_chat_models_and_keeps_credentials_in_headers(setup, monkeypatch):
    calls = []
    pages = iter([{'models': [{'name': 'models/veo-new', 'displayName': 'New Veo'},
                             {'name': 'models/gemini-chat', 'supportedGenerationMethods': ['generateContent']}],
                   'nextPageToken': 'p2 /?'},
                  {'models': [{'name': 'models/veo-other'}, {'name': 'models/gemini-flash-image'}]}])
    def call(method, url, headers, **kw):
        calls.append((method, url, headers, kw))
        return next(pages)
    monkeypatch.setattr(eng, '_json_call', call)
    out = google.discover_models(eng._Creds('SENTINEL_TOKEN'), kind='video')
    assert [m['model_id'] for m in out] == ['veo-new', 'veo-other']
    assert all(m == 'GET' and 'SENTINEL_TOKEN' not in url and h == {'x-goog-api-key': 'SENTINEL_TOKEN'}
               and kw['free'] for m, url, h, kw in calls)
    assert parse_qs(urlparse(calls[1][1]).query)['pageToken'] == ['p2 /?']


@pytest.mark.parametrize('kind,ids', [('image', ['gpt-image-new', 'dall-e-3']), ('video', ['sora-2'])])
def test_openai_kind_filter_no_synthetic_pricing(setup, monkeypatch, kind, ids):
    calls = []
    def call(method, url, headers, **kw):
        calls.append((method, url, kw))
        return {'data': [{'id': m} for m in ['gpt-chat', 'gpt-image-new', 'dall-e-3', 'sora-2']]}
    monkeypatch.setattr(eng, '_json_call', call)
    out = openai.discover_models(eng._Creds('fake'), kind=kind)
    assert [m['model_id'] for m in out] == ids
    assert all(m['price'] == {'state': 'unknown'} for m in out)
    assert calls[0][:2] == ('GET', 'https://api.openai.com/v1/models')
    assert calls[0][2]['free'] is True


def test_higgsfield_api_explicitly_unavailable_no_guessed_endpoint(setup, monkeypatch):
    monkeypatch.setattr(eng, '_json_call', lambda *a, **kw: pytest.fail('Guessed API endpoint'))
    with pytest.raises(eng.Refused) as e:
        higgsfield.discover_models(eng._Creds('fake'), kind='video')
    assert e.value.code == 'discovery_unavailable'


def test_cache_24h_identity_scope_refresh_and_redaction(setup, monkeypatch):
    calls = []
    def adapter(creds, **kw):
        calls.append(kw)
        return [common.project('kling3_0', 'video', 'Changed label'),
                common.project('new_video', 'video', 'Name SENTINEL_TOKEN')]
    monkeypatch.setitem(catalogue.ADAPTERS, 'higgsfield_mcp', adapter)
    first = catalogue.discover('higgsfield_mcp', kind='video', project_id='p1')
    assert first['cached'] is False
    assert first['models'][0]['label'] == 'Kling 3.0 (video)'
    assert first['models'][0]['render_supported'] is True
    assert first['models'][1]['render_supported'] is False
    assert catalogue.discover('higgsfield_mcp', kind='video', project_id='p1')['cached'] is True
    assert len(calls) == 1
    catalogue.discover('higgsfield_mcp', kind='video', project_id='p2')
    catalogue.discover('higgsfield_mcp', kind='video', project_id='p1', query='new')
    catalogue.discover('higgsfield_mcp', kind='video', project_id='p1', refresh=True)
    monkeypatch.setattr(eng, '_creds', lambda *a: eng._Creds('ANOTHER_TOKEN'))
    catalogue.discover('higgsfield_mcp', kind='video', project_id='p1')
    assert len(calls) == 5
    for path in catalogue.cache_dir().glob('*.json'):
        assert 'SENTINEL_TOKEN' not in path.read_text()
        assert 'ANOTHER_TOKEN' not in path.read_text()
        assert 'projects' not in path.relative_to(setup).parts
        doc = json.loads(path.read_text())
        doc['captured_at'] = time.time() - catalogue.TTL_SECONDS
        path.write_text(json.dumps(doc))
    assert catalogue.discover('higgsfield_mcp', kind='video', project_id='p1')['cached'] is False


def test_cached_reads_still_enforce_saved_connection_policy(setup, monkeypatch):
    monkeypatch.setitem(catalogue.ADAPTERS, 'higgsfield_mcp', lambda *a, **kw: [])
    catalogue.discover('higgsfield_mcp', kind='video')
    def denied(engine_id, pid, unattended):
        assert unattended is True
        raise eng.NotConnected(engine_id, 'Vault locked', 'oauth.higgsfield')
    monkeypatch.setattr(eng, '_creds', denied)
    with pytest.raises(eng.NotConnected):
        catalogue.discover('higgsfield_mcp', kind='video', unattended=True)


def test_corrupt_cache_observable_and_vendor_recovery(setup, monkeypatch):
    monkeypatch.setitem(catalogue.ADAPTERS, 'openai', lambda *a, **kw: [])
    catalogue.discover('openai', kind='image')
    path = next(catalogue.cache_dir().glob('*.json'))
    path.write_text('[]')
    logs = []
    monkeypatch.setattr(catalogue, '_log', lambda msg, **kw: logs.append(msg))
    assert not catalogue.discover('openai', kind='image')['cached']
    assert 'cache read failed' in logs[0]


def board_fixture(monkeypatch):
    monkeypatch.setattr(eng, '_owner_scope', lambda owner, body: ('studio', owner['id'], None, 'p1'))
    monkeypatch.setattr(desk_storyboard, 'get_storyboard', lambda *a: {'scenes': [
        {'id': str(i), 'label': f'Scene {i}', 'line': 'A scene', 'duration_sec': secs,
         'picture': {'path': '/must-not-be-read'}} for i, secs in enumerate([6, 4, 5, 5, 6, 6, 4])]})


def test_full_storyboard_quote_literal_flags_no_pictures_or_spend(setup, monkeypatch):
    board_fixture(monkeypatch)
    calls = []
    def call(self, creds, tool, args, **kw):
        calls.append((tool, args, kw))
        return {'structuredContent': {'items': [hf_row()]}} if tool == 'models_explore' else {
            'structuredContent': {'cost': {'credits': args['params']['duration'] * 2},
                                  'recovery_tool': 'media_upload', 'recovery_tool_args': {'get_cost': False}}}
    monkeypatch.setattr(HiggsfieldMcpAdapter, '_call', call)
    out = quotes.quote('higgsfield_mcp', {'owner': {'kind': 'studio', 'id': 'test'}, 'model_id': 'new_video'})
    assert out['price'] == {'state': 'quoted', 'currency': 'credits', 'credits': 100}
    assert out['pictures_omitted'] is True and out['text_only'] is True
    assert sum(s['requested_sec'] for s in out['scenes']) == 36
    assert [s['sent_duration_sec'] for s in out['scenes']] == [10, 5, 5, 5, 10, 10, 5]
    generation = [c for c in calls if c[0] != 'models_explore']
    assert len(generation) == 7
    for tool, args, kw in generation:
        assert tool == 'generate_video' and kw == {'free': True}
        assert args['params']['get_cost'] is True and args['params']['use_unlim'] is False
        assert set(args['params']) == {'model', 'prompt', 'count', 'aspect_ratio', 'get_cost', 'use_unlim', 'duration'}


@pytest.mark.parametrize('extra', ['params', 'medias', 'get_cost', 'use_unlim', 'engine_id'])
def test_quote_refuses_caller_tool_arguments_before_any_vendor_call(setup, monkeypatch, extra):
    monkeypatch.setattr(HiggsfieldMcpAdapter, '_call', lambda *a, **kw: pytest.fail('Quote reached vendor'))
    with pytest.raises(eng.Refused):
        quotes.quote('higgsfield_mcp', {extra: False})


@pytest.mark.parametrize('credits', [None, -1, float('nan'), float('inf'), True])
def test_invalid_quote_never_becomes_zero_price(setup, monkeypatch, credits):
    board_fixture(monkeypatch)
    monkeypatch.setattr(HiggsfieldMcpAdapter, '_call', lambda self, creds, tool, args, **kw: {
        'structuredContent': {'items': [hf_row()]} if tool == 'models_explore' else {'cost': {'credits': credits}}})
    with pytest.raises(eng.EngineError):
        quotes.quote('higgsfield_mcp', {'owner': {'id': 'test'}, 'model_id': 'new_video'})


def test_read_only_routes_work_unattended_and_errors_use_existing_contract(setup, monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(routes.bp)
    monkeypatch.setattr(routes, 'is_unattended_caller', lambda: True)
    monkeypatch.setitem(catalogue.ADAPTERS, 'openai', lambda *a, **kw: [])
    c = app.test_client()
    assert c.get('/api/desk/engines/openai/models?kind=image').json['models'] == []
    assert c.get('/api/desk/engines/nope/models').status_code == 400
    assert c.get('/api/desk/engines/openai/models?kind=audio').status_code == 400
    assert c.post('/api/desk/engines/openai/models/quote', json=[]).status_code == 400
    assert c.post('/api/desk/engines/openai/models', json={}).status_code == 405
    def denied(*a):
        raise eng.NotConnected('openai', 'Locked', 'openai-api')
    monkeypatch.setattr(eng, '_creds', denied)
    response = c.get('/api/desk/engines/openai/models?kind=image')
    assert response.status_code == 409 and response.json['code'] == 'not_connected'


def test_quote_prevalidates_all_scene_prompts_before_quoting(setup, monkeypatch):
    board_fixture(monkeypatch)
    monkeypatch.setattr(desk_storyboard, 'get_storyboard', lambda *a: {'scenes': [
        {'id': '1', 'line': 'A good scene', 'duration_sec': 5},
        {'id': '2', 'line': '', 'duration_sec': 5}]})
    monkeypatch.setitem(catalogue.ADAPTERS, 'higgsfield_mcp', lambda *a, **kw: [
        common.project('new_video', 'video', 'New Video')])
    monkeypatch.setattr(HiggsfieldMcpAdapter, '_call', lambda *a, **kw: pytest.fail('Invalid board reached quote'))
    with pytest.raises(eng.Refused):
        quotes.quote('higgsfield_mcp', {'owner': {'id': 'test'}, 'model_id': 'new_video'})


def test_no_quote_service_returns_unknown_with_catalogue_get_only(setup, monkeypatch):
    board_fixture(monkeypatch)
    calls = []
    def vendor(method, url, headers, **kw):
        calls.append((method, url, kw))
        return {'models': [{'name': 'models/veo-new'}]}
    monkeypatch.setattr(eng, '_json_call', vendor)
    out = quotes.quote('google', {'owner': {'id': 'test'}, 'model_id': 'veo-new'})
    assert out['price'] == {'state': 'unknown'} and len(calls) == 1 and calls[0][0] == 'GET'


def test_image_quote_has_no_duration_upload_or_generation(setup, monkeypatch):
    calls = []
    def call(self, creds, tool, args, **kw):
        calls.append((tool, args, kw))
        return {'structuredContent': {'items': [hf_row('new_image', output_type='image')]}} \
            if tool == 'models_explore' else {'structuredContent': {'cost': {'credits_exact': 0.5}}}
    monkeypatch.setattr(HiggsfieldMcpAdapter, '_call', call)
    out = quotes.quote('higgsfield_mcp', {'kind': 'image', 'prompt': 'A tree', 'model_id': 'new_image'})
    assert out['price']['credits'] == 0.5
    assert calls[-1] == ('generate_image', {'params': {'model': 'new_image', 'prompt': 'A tree', 'count': 1,
                                                    'aspect_ratio': '16:9', 'get_cost': True,
                                                    'use_unlim': False}}, {'free': True})


def test_projection_retains_explicit_capabilities_never_prices_or_secrets(setup):
    out = common.project('new_video', 'video', 'Name', raw={
        'medias': [{'type': 'image', 'required': True}],
        'parameters': [{'name': 'generate_audio'}, {'name': 'resolution', 'options': ['720p', '1080p']}],
        'credits_per_unit': 999, 'durations': [False, -1, float('inf'), 5]})
    assert out['catalogue_inputs'] == {'picture': True, 'required_picture': True, 'audio': True}
    assert out['durations_sec'] == [5] and out['price'] == {'state': 'unknown'}
    assert out['resolutions'] == ['720p', '1080p'] and 'credits_per_unit' not in out
    assert common.project('SENTINEL_TOKEN', 'video', 'Name') is None


def test_catalogue_quote_reuses_schema_checked_literal_preset_decline(setup, monkeypatch):
    # Reuse the production helper's existing schema fixture, not a new liberal
    # retry contract: one literal decline ID may change, no vendor prompt rewrite.
    from tests.test_higgsfield_picture_render import contract as contract_fixture
    from tests.test_higgsfield_preset_decline import notice
    doc = contract_fixture.__wrapped__()
    monkeypatch.setattr(higgsfield_mcp_snapshot, 'read', lambda: doc)
    monkeypatch.setitem(catalogue.ADAPTERS, 'higgsfield_mcp', lambda *a, **kw: [
        common.project('kling3_0', 'video', 'Kling')])
    calls = []
    def call(self, creds, tool, args, **kw):
        calls.append(args)
        return {'structuredContent': notice() if len(calls) == 1 else {'cost': {'credits': 7}}}
    monkeypatch.setattr(HiggsfieldMcpAdapter, '_call', call)
    board_fixture(monkeypatch)
    monkeypatch.setattr(desk_storyboard, 'get_storyboard', lambda *a: {'scenes': [
        {'id': '1', 'label': 'Desk', 'line': 'in the office', 'duration_sec': 5}]})
    out = quotes.quote('higgsfield_mcp', {'owner': {'id': 'test'}, 'model_id': 'kling3_0'})
    assert out['price']['credits'] == 7 and out['scenes'][0]['preset_declined'] is True
    assert calls[1] == {'params': {**calls[0]['params'], 'declined_preset_id': 'preset-123'}}


def test_finite_scene_quotes_cannot_overflow_into_infinite_total(setup, monkeypatch):
    board_fixture(monkeypatch)
    monkeypatch.setattr(HiggsfieldMcpAdapter, '_call', lambda self, creds, tool, args, **kw: {
        'structuredContent': {'items': [hf_row()]} if tool == 'models_explore' else {'cost': {'credits': 1e308}}})
    with pytest.raises(eng.EngineError, match='total quote'):
        quotes.quote('higgsfield_mcp', {'owner': {'id': 'test'}, 'model_id': 'new_video'})
