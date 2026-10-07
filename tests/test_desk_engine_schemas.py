"""Automatic discovery and runtime evidence: fake MCP only, no generation/upload."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from mc import desk, desk_engines as eng, desk_engine_schemas as registry
from mc.desk_connect import higgsfield_mcp_capture as capture, higgsfield_mcp_snapshot as snapshot
from mc.desk_mcp_picture_schema import derive, MISSING, PENDING
from tests.test_desk_engines import board, client, uploads, vault, vendor, _sc, _render_body  # noqa: F401


def document(schema, *, age=0, kind='video'):
    return {'untrusted_vendor_text': True,
            'capture_version': snapshot.CAPTURE_VERSION,
            'captured_at': (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(),
            'tools': [{'name': 'generate_' + kind, 'inputSchema': schema, 'description': ''}]}


def contract(model='kling3_0', **fields):
    return {'type': 'object', 'additionalProperties': False, 'properties': {
        'params': {'type': 'object', 'additionalProperties': False, 'properties': {
            'model': {'const': model}, 'prompt': {'type': 'string'}, **fields}}}}


@pytest.fixture(autouse=True)
def isolate_registry(monkeypatch):
    monkeypatch.setattr(registry, '_snapshotters', {})
    monkeypatch.setattr(registry, '_connections', {})


@pytest.fixture
def mcp(monkeypatch, tmp_path):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    calls = []
    tools = document(contract(start_image={'type': 'string'}))['tools']

    def post(token, body, *, expect_id, timeout=None):
        assert token == 'fake-token'
        calls.append((body, timeout))
        assert body['method'] in {'initialize', 'notifications/initialized', 'tools/list'}
        return {'tools': tools} if body['method'] == 'tools/list' else {}

    monkeypatch.setattr(eng, '_mcp_post', post)
    return calls, tools


def test_connection_saved_captures_without_verify_and_price_skips_fresh(mcp):
    calls, _ = mcp
    registry.connection_saved('higgsfield', 'oauth', token='fake-token')
    assert snapshot.path().exists()
    assert eng.get_model('higgsfield_mcp', 'kling3_0').inputs['first_frame'] is True
    registry.ensure('higgsfield_mcp', token='fake-token')
    assert len(calls) == 3
    assert all(0 < timeout <= 15 for _, timeout in calls)
    assert 'fake-token' not in snapshot.path().read_text(encoding='utf-8')


@pytest.mark.parametrize('age', [None, 86401])
def test_missing_and_expired_snapshot_refresh_before_price(mcp, age):
    calls, _ = mcp
    if age is not None:
        snapshot.path().parent.mkdir()
        snapshot.path().write_text(json.dumps(document(contract(), age=age)), encoding='utf-8')
    registry.ensure('higgsfield_mcp', token='fake-token')
    assert len(calls) == 3 and registry.fresh(snapshot.read())


def test_fresh_old_capture_is_upgraded_once_on_price_path(mcp):
    calls, _ = mcp
    old = document(contract())
    old.pop('capture_version')
    snapshot.path().parent.mkdir()
    snapshot.path().write_text(json.dumps(old), encoding='utf-8')
    registry.ensure('higgsfield_mcp', token='fake-token')
    assert snapshot.read()['capture_version'] == snapshot.CAPTURE_VERSION
    registry.ensure('higgsfield_mcp', token='fake-token')
    assert len(calls) == 3


def test_registry_reuses_shape_for_another_remote_engine():
    state = {'doc': None, 'calls': 0}

    def refresh(**kwargs):
        state.update(doc=document(contract()), calls=state['calls'] + 1)

    registry.register('other_mcp', service='other', method='oauth',
                      read=lambda: state['doc'], refresh=refresh)
    registry.connection_saved('other', 'oauth')
    registry.ensure('other_mcp')
    assert state['calls'] == 1


def test_non_oauth_provider_save_uses_registered_snapshotter(monkeypatch):
    from types import SimpleNamespace
    from mc.desk_connect import provider_commit
    calls = []
    registry.register('other_mcp', service='other', method='api_key', read=lambda: None,
                      refresh=lambda **kw: calls.append('capture'))
    provider = SimpleNamespace(signs_in=(), after_commit=lambda *a: {})
    monkeypatch.setattr(provider_commit.providers, 'for_service', lambda service: provider)
    assert provider_commit.follow({'service': 'other', 'method': 'api_key', 'fields': {}}, None) == {}
    assert calls == ['capture']


def test_one_inflight_capture_other_price_does_not_wait():
    entered, release = threading.Event(), threading.Event()
    calls = []

    def refresh(**kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(2)

    registry.register('other_mcp', service='other', method='oauth', read=lambda: None, refresh=refresh)
    thread = threading.Thread(target=lambda: registry.ensure('other_mcp'))
    thread.start()
    try:
        assert entered.wait(2)
        assert registry.ensure('other_mcp') is None
        with pytest.raises(RuntimeError, match='already in progress'):
            registry.ensure('other_mcp', strict=True)
        assert len(calls) == 1
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()


def test_failed_discovery_is_logged_redacted_and_next_price_retries(mcp, monkeypatch):
    logs = []
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(1)
        raise OSError('secret signed URL must not be logged')

    monkeypatch.setattr(eng, '_mcp_post', fail)
    monkeypatch.setattr(registry, '_log', lambda message, **kw: logs.append(message))
    registry.connection_saved('higgsfield', 'oauth', token='fake-token')
    assert registry.ensure('higgsfield_mcp', token='fake-token') is None
    assert len(attempts) == 2 and len(logs) == 2
    assert all('OSError' in line and 'secret' not in line for line in logs)


def test_discovery_has_one_deadline_across_rpc_pages(monkeypatch):
    clock = [0.0]
    timeouts = []
    monkeypatch.setattr(capture.time, 'monotonic', lambda: clock[0])

    def post(*args, timeout, **kw):
        timeouts.append(timeout)
        clock[0] += 6
        return {'tools': [], 'nextCursor': 'more'}

    monkeypatch.setattr(eng, '_mcp_post', post)
    with pytest.raises(eng.EngineError, match='timed out'):
        capture.collect('fake', timeout=15)
    assert timeouts == [15, 9, 3]


@pytest.mark.parametrize('model', ['kling3_0', 'seedance_2_5'])
def test_model_bound_picture_evidence_and_adapter_pending_guard(model):
    doc = document(contract(model, start_image={'type': 'string'},
                            reference_images={'type': 'array', 'maxItems': 3, 'items': {'type': 'string'}}))
    registry.register('higgsfield_mcp', service='higgsfield', method='oauth', read=lambda: doc, refresh=lambda **kw: None,
                      capabilities=derive)
    descriptor = eng.get_model('higgsfield_mcp', model)
    assert descriptor.inputs['first_frame'] and descriptor.inputs['reference_images_max'] == 3
    assert descriptor.inputs['picture_inputs'][0]['path'] == ['params', 'start_image']
    req = eng.GenerationRequest(engine_id='higgsfield_mcp', model_id=model, kind='video',
                                prompt='Shot', aspect_ratio='16:9', duration_sec=5, first_frame={'path': 'fake.png'})
    assert PENDING in eng.validate_request(descriptor, req)
    with pytest.raises(eng.EngineError, match='upload wiring'):
        eng.HiggsfieldMcpAdapter._params(descriptor, req, get_cost=True)
    assert not eng.get_model('higgsfield_mcp', 'gpt_image_2_5').inputs['first_frame']
    assert not next(m for m in eng.ENGINES['higgsfield_mcp'].models if m.model_id == model).inputs['first_frame']


def test_medias_role_enum_and_local_reference_are_structured_evidence():
    schema = contract(medias={'type': 'array', 'maxItems': 4, 'items': {'$ref': '#/$defs/media'}})
    schema['$defs'] = {'media': {'type': 'object', 'properties': {'role': {'enum': ['start_image', 'image_references']}}}}
    inputs = derive(document(schema), 'video', 'kling3_0')
    assert inputs['first_frame'] and inputs['reference_images_max'] == 4
    assert {e['role'] for e in inputs['picture_inputs']} == {'start_image', 'image_references'}


@pytest.mark.parametrize('schema', [
    {'type': 'object', 'description': 'All models accept start_image', 'properties': {'model': {'type': 'string'}, 'start_image': {'type': 'string'}}},
    contract('different_model', start_image={'type': 'string'}),
    {'allOf': [contract(start_image={'type': 'string'})]},
    contract(start_image={'$ref': 'https://vendor.invalid/schema'}),
    contract(image_url={'type': 'string'}),
    contract(model={'bad': 'enum'}, medias={'type': 'array', 'items': {'properties': None}}),
])
def test_opaque_conflicting_or_other_model_contract_does_not_grant_pictures(schema):
    inputs = derive(document(schema), 'video', 'kling3_0')
    assert not inputs['first_frame'] and inputs['reference_images_max'] == 0


def test_absent_and_stale_evidence_explain_refusal():
    assert derive(None, 'video', 'kling3_0')['picture_refusal'] == MISSING
    doc = document(contract(start_image={'type': 'string'}), age=86401)
    registry.register('higgsfield_mcp', service='higgsfield', method='oauth', read=lambda: doc, refresh=lambda **kw: None,
                      capabilities=derive)
    assert eng.get_model('higgsfield_mcp', 'kling3_0').inputs['schema_state'] == 'stale'


def test_closed_model_with_no_picture_still_refuses():
    inputs = derive(document(contract()), 'video', 'kling3_0')
    assert inputs['schema_state'] == 'none' and inputs['first_frame'] is False


def test_conflicting_model_branches_remain_unknown():
    schema = {'oneOf': [contract(start_image={'type': 'string'}), contract()]}
    inputs = derive(document(schema), 'video', 'kling3_0')
    assert inputs['schema_state'] == 'unknown' and not inputs['first_frame']


def test_unrecognized_picture_field_is_unknown_not_prompt_only():
    assert derive(document(contract(image_url={'type': 'string'})), 'video', 'kling3_0')['schema_state'] == 'unknown'


@pytest.mark.parametrize('model', ['gpt_image_2_5', 'soul_2', 'nano_banana', 'z_image'])
def test_image_models_use_generate_image_schema_only(model):
    doc = document(contract(model, input_images={'type': 'array', 'maxItems': 2}), kind='image')
    inputs = derive(doc, 'image', model)
    assert inputs['reference_images_max'] == 2
    assert inputs['picture_inputs'][0]['tool'] == 'generate_image'
    assert derive(doc, 'video', model)['schema_state'] == 'unknown'


@pytest.mark.parametrize('operation', ['estimate', 'estimate_render', 'render', 'submit'])
def test_user_price_and_render_discover_before_picture_validation(client, board, uploads, monkeypatch, operation):
    from mc.desk_mcp_picture_schema import PENDING
    picture = uploads / 'desk' / 'library' / 'image' / 'shot.png'
    picture.parent.mkdir(parents=True, exist_ok=True)
    picture.write_bytes(b'fake image')
    board([_sc('s1', picture={'path': 'desk/library/image/shot.png'})])
    calls = []
    monkeypatch.setattr(eng, '_creds', lambda *a: eng._Creds(secret='fake-token'))

    def post(token, body, **kwargs):
        calls.append(body['method'])
        assert body['method'] in {'initialize', 'notifications/initialized', 'tools/list'}
        return {'tools': document(contract(start_image={'type': 'string'}))['tools']} if body['method'] == 'tools/list' else {}

    monkeypatch.setattr(eng, '_mcp_post', post)
    if operation in ('estimate', 'submit'):
        body = {'engine_id': 'higgsfield_mcp', 'model_id': 'kling3_0', 'kind': 'video',
                'prompt': 'Shot', 'aspect_ratio': '16:9', 'duration_sec': 5,
                'first_frame': {'path': 'shot.png'}, 'desk': {'idempotency_key': 'schema-test'}}
    else:
        body = _render_body(engine_id='higgsfield_mcp', model_id='kling3_0')
    with pytest.raises(eng.Refused, match='upload wiring'):
        getattr(eng, operation)(body)
    assert calls == ['initialize', 'notifications/initialized', 'tools/list']


def test_capture_failure_keeps_text_price_working_and_picture_reason(client, board, uploads, monkeypatch):
    monkeypatch.setattr(eng, '_creds', lambda *a: eng._Creds(secret='fake-token'))
    calls = []

    def post(token, body, **kwargs):
        calls.append(body['method'])
        if body['method'] == 'tools/list':
            raise eng.EngineError('engine', 'fake timeout')
        if body['method'] == 'tools/call':
            assert body['params']['arguments']['params']['get_cost'] is True
            assert body['params']['arguments']['params']['use_unlim'] is False
            return {'structuredContent': {'cost': {'credits': 2}}}
        return {}

    monkeypatch.setattr(eng, '_mcp_post', post)
    result = eng.estimate({'engine_id': 'higgsfield_mcp', 'model_id': 'kling3_0', 'kind': 'video',
                           'prompt': 'Shot', 'aspect_ratio': '16:9', 'duration_sec': 5})
    assert result['estimate']['credits'] == 2 and calls.count('tools/call') == 1
    picture = uploads / 'desk' / 'library' / 'image' / 'shot.png'
    picture.parent.mkdir(parents=True, exist_ok=True)
    picture.write_bytes(b'fake image')
    board([_sc('s1', picture={'path': 'desk/library/image/shot.png'})])
    with pytest.raises(eng.Refused, match='has not read Higgsfield'):
        eng.estimate_render(_render_body(engine_id='higgsfield_mcp', model_id='kling3_0'))


def test_corrupt_snapshot_reader_fails_closed(mcp):
    snapshot.path().parent.mkdir()
    snapshot.path().write_text('{broken', encoding='utf-8')
    assert snapshot.read() is None
