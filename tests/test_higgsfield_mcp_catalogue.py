"""Catalogue-only discovery, hermetic transport; no upload or paid generation."""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from mc import desk, desk_engines as engines, desk_engine_schemas as registry
from mc.desk_connect import higgsfield_mcp_catalogue as catalogue
from mc.desk_connect import higgsfield_mcp_snapshot as snapshot
from tests.test_desk_engines import board, client, uploads, vault, vendor, _sc, _render_body  # noqa: F401


def tool():
    return {'name': 'models_explore', 'inputSchema': {
        'type': 'object', 'properties': {
            'action': {'type': 'string', 'enum': ['list', 'search', 'get', 'recommend']},
            'model_id': {'type': 'string'}}, 'required': ['action']}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'desk.json')
    monkeypatch.setattr(registry, '_snapshotters', {})
    monkeypatch.setattr(registry, '_connections', {})
    snapshot.capture([tool()])
    calls = []
    result = {'model': {'id': 'kling3_0', 'type': 'video', 'vendor_extension': {'opaque': True}}}

    def post(token, body, *, expect_id, timeout, **kw):
        assert token == 'fake-token'
        assert 0 < timeout <= 15
        calls.append(body)
        if body['method'] == 'tools/call':
            assert body['params']['name'] == 'models_explore'
            assert body['params']['arguments']['action'] == 'get'
            return {'structuredContent': copy.deepcopy(result)}
        assert body['method'] in ('initialize', 'notifications/initialized')
        return {}

    monkeypatch.setattr(engines, '_mcp_post', post)
    return calls, result


def read_model(model_id='kling3_0', kind='video'):
    registry.ensure('higgsfield_mcp', token='fake-token', model_id=model_id, kind=kind)


def test_selected_model_get_preserves_raw_untrusted_result_and_reuses_cache(env):
    calls, result = env
    before = snapshot.read()['captured_at']
    read_model()
    doc = snapshot.read()
    assert doc['captured_at'] == before
    assert set(doc['models']) == {'kling3_0'}
    record = doc['models']['kling3_0']
    assert record['result'] == result and record['untrusted_vendor_text'] is True
    assert record['kind'] == 'video' and catalogue.fresh(record)
    assert calls[-1]['params']['arguments'] == {'action': 'get', 'model_id': 'kling3_0'}
    read_model()
    assert len(calls) == 3
    registry.ensure('higgsfield_mcp', token='fake-token')
    assert len(calls) == 3  # save/verify does not browse the model catalogue


def test_only_another_used_model_adds_a_catalogue_call(env):
    calls, result = env
    read_model()
    result['model']['id'] = 'seedance_2_5'
    read_model('seedance_2_5')
    assert set(snapshot.read()['models']) == {'kling3_0', 'seedance_2_5'}
    assert [b['params']['arguments']['model_id'] for b in calls if b['method'] == 'tools/call'] \
        == ['kling3_0', 'seedance_2_5']


def test_expired_model_get_refreshes_without_relisting_fresh_tools(env):
    calls, _ = env
    read_model()
    doc = snapshot.read()
    doc['models']['kling3_0']['captured_at'] = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    snapshot.path().write_text(json.dumps(doc), encoding='utf-8')
    read_model()
    assert len(calls) == 6 and catalogue.cached(snapshot.read(), 'video', 'kling3_0')


@pytest.mark.parametrize('bad', [
    {'isError': True}, {'structuredContent': {}}, {'content': [{'type': 'text', 'text': 'not json'}]},
])
def test_failed_model_read_logs_type_and_preserves_cache_without_capability(env, monkeypatch, bad):
    before = snapshot.path().read_text(encoding='utf-8')
    monkeypatch.setattr(engines, '_mcp_post', lambda *a, **kw: bad)
    logs = []
    monkeypatch.setattr(registry, '_log', lambda line, **kw: logs.append(line))
    read_model()
    assert snapshot.path().read_text(encoding='utf-8') == before
    assert not catalogue.cached(snapshot.read(), 'video', 'kling3_0')
    assert 'EngineError' in logs[0]
    assert registry.model_inputs('higgsfield_mcp', 'video', 'kling3_0')['schema_state'] == 'unknown'


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'required', 'no_get', 'composition'])
def test_unestablished_get_contract_never_calls_any_tool(env, change):
    calls, _ = env
    t = tool()
    if change == 'missing':
        tools = []
    elif change == 'duplicate':
        tools = [t, t]
    else:
        if change == 'required':
            t['inputSchema']['required'].append('query')
        elif change == 'no_get':
            t['inputSchema']['properties']['action']['enum'] = ['list']
        else:
            t['inputSchema']['allOf'] = [{}]
        tools = [t]
    snapshot.capture(tools)
    read_model()
    assert calls == []


def test_catalogue_capture_shares_deadline_across_rpcs(env, monkeypatch):
    clock = [0.0]
    timeouts = []
    monkeypatch.setattr(catalogue.time, 'monotonic', lambda: clock[0])

    def post(*args, timeout, **kw):
        timeouts.append(timeout)
        clock[0] += 6
        return {}

    monkeypatch.setattr(engines, '_mcp_post', post)
    with pytest.raises(engines.EngineError, match='timed out'):
        catalogue.refresh(token='fake-token', kind='video', model_id='kling3_0')
    assert timeouts == [15, 9, 3]
    assert not catalogue.cached(snapshot.read(), 'video', 'kling3_0')


def test_oversized_model_result_preserves_previous_capture(env, monkeypatch):
    calls, result = env
    monkeypatch.setattr(catalogue, 'MAX_MODEL_BYTES', 20)
    before = snapshot.path().read_text(encoding='utf-8')
    read_model()
    assert snapshot.path().read_text(encoding='utf-8') == before
    assert len(calls) == 3


def test_oversized_merged_capture_is_never_written(env, monkeypatch):
    before = snapshot.path().read_text(encoding='utf-8')
    monkeypatch.setattr(snapshot, 'MAX_BYTES', len(before.encode('utf-8')) + 10)
    read_model()
    assert snapshot.path().read_text(encoding='utf-8') == before


def test_redaction_precedes_model_cache_write(env, monkeypatch):
    calls, result = env
    result['model']['note'] = 'do not persist FAKE-CREDENTIAL'
    monkeypatch.setattr(engines.secrets_store, 'redact', lambda text: text.replace('FAKE-CREDENTIAL', '[REDACTED]'))
    read_model()
    assert 'FAKE-CREDENTIAL' not in snapshot.path().read_text(encoding='utf-8')
    assert '[REDACTED]' in snapshot.path().read_text(encoding='utf-8')


@pytest.mark.parametrize('operation', ['estimate', 'estimate_render'])
def test_actual_price_paths_read_only_the_used_model_then_reuse_it(client, board, env, monkeypatch, operation):
    calls = []
    monkeypatch.setattr(engines, '_creds', lambda *a: engines._Creds(secret='fake-token'))

    def post(token, body, **kwargs):
        assert token == 'fake-token'
        calls.append(body)
        if body['method'] == 'tools/call':
            params = body['params']
            if params['name'] == 'models_explore':
                assert params['arguments'] == {'action': 'get', 'model_id': 'kling3_0'}
                return {'structuredContent': {'model': {'id': 'kling3_0', 'type': 'video'}}}
            assert params['name'] == 'generate_video'
            assert params['arguments']['params']['get_cost'] is True
            assert params['arguments']['params']['use_unlim'] is False
            return {'structuredContent': {'cost': {'credits': 2}}}
        assert body['method'] in ('initialize', 'notifications/initialized')
        return {}

    monkeypatch.setattr(engines, '_mcp_post', post)
    if operation == 'estimate_render':
        board([_sc('s1')])
        body = _render_body(engine_id='higgsfield_mcp', model_id='kling3_0')
    else:
        body = {'engine_id': 'higgsfield_mcp', 'model_id': 'kling3_0', 'kind': 'video',
                'prompt': 'Shot', 'aspect_ratio': '16:9', 'duration_sec': 5}
    for _ in range(2):
        assert getattr(engines, operation)(body)['estimate']['credits'] == 2
    names = [b['params']['name'] for b in calls if b['method'] == 'tools/call']
    assert names == ['models_explore', 'generate_video', 'generate_video']
    assert set(snapshot.read()['models']) == {'kling3_0'}
