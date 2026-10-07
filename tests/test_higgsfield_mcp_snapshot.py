"""Server-side schema capture: fake MCP only, no credential or provider access."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mc import desk, desk_engines, desk_oauth  # noqa: E402
from mc.desk_connect.providers.higgsfield import HiggsfieldProvider  # noqa: E402

TOKEN = 'FAKE-OAUTH-DO-NOT-STORE'


@pytest.fixture
def capture_env(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'STORE_PATH', tmp_path / 'data' / 'desk.json')
    monkeypatch.setattr(desk_oauth, 'access_token', lambda *a, **kw: TOKEN)
    calls = []

    def fake(pages):
        def post(token, body, *, expect_id, **kw):
            assert token == TOKEN
            calls.append(body)
            if body['method'] == 'tools/list':
                page = pages.pop(0)
                if isinstance(page, Exception):
                    raise page
                return page
            assert body['method'] in ('initialize', 'notifications/initialized')
            return {}
        monkeypatch.setattr(desk_engines, '_mcp_post', post)
    return tmp_path / 'data' / 'desk' / 'higgsfield_mcp_tools.json', calls, fake


def test_verify_captures_filtered_schemas_across_cursor_pages(capture_env):
    path, calls, fake = capture_env
    schema = {'type': 'object', 'properties': {'params': {'type': 'object'}}}
    tools = [
        {'name': 'generate_video', 'inputSchema': schema},
        {'name': 'generate_image', 'inputSchema': schema},
        {'name': 'job_status', 'inputSchema': schema},
        {'name': 'media_upload', 'inputSchema': schema, 'description': 'allocate'},
        {'name': 'confirm', 'inputSchema': schema, 'description': 'Confirm uploaded FILE'},
        {'name': 'balance', 'inputSchema': schema, 'description': 'Credits remaining'},
        {'name': 'later', 'inputSchema': schema, 'description': 'x' * 2100 + ' IMAGE reference'},
    ]
    fake([{'tools': tools[:4], 'nextCursor': 'page-two', 'token': TOKEN},
          {'tools': tools[4:], 'headers': {'Authorization': TOKEN}}])
    probe = HiggsfieldProvider().verify('oauth')
    assert probe.ok is True
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    assert snapshot['untrusted_vendor_text'] is True
    assert datetime.fromisoformat(snapshot['captured_at']).tzinfo is not None
    assert [t['name'] for t in snapshot['tools']] == [t['name'] for t in tools if t['name'] != 'balance']
    assert all(set(t) == {'name', 'inputSchema', 'description'} for t in snapshot['tools'])
    assert all(t['inputSchema'] == schema for t in snapshot['tools'])
    assert snapshot['tools'][-1]['description'] == 'x' * 2000
    assert TOKEN not in path.read_text(encoding='utf-8')
    assert 'Authorization' not in path.read_text(encoding='utf-8')
    lists = [c for c in calls if c['method'] == 'tools/list']
    assert len(lists) == 2 and lists[1]['params'] == {'cursor': 'page-two'}
    assert len({c['id'] for c in lists}) == 2
    assert [c['method'] for c in calls[:2]] == ['initialize', 'notifications/initialized']


@pytest.mark.parametrize('word', ['upload', 'media', 'image', 'file', 'reference'])
def test_filter_matches_each_keyword_in_names_and_descriptions(capture_env, word):
    path, _, fake = capture_env
    fake([{'tools': [{'name': word.upper() + '_tool', 'inputSchema': {}},
                     {'name': 'described', 'description': word.upper(), 'inputSchema': {}}]}])
    assert HiggsfieldProvider().verify('oauth').ok is True
    assert len(json.loads(path.read_text(encoding='utf-8'))['tools']) == 2


@pytest.mark.parametrize('second_page', [
    {'tools': [], 'nextCursor': 'repeat'},
    {'tools': [], 'nextCursor': 42},
    {'tools': 'malformed'},
    desk_engines.EngineError('engine', 'fake network failure'),
])
def test_incomplete_listing_fails_probe_and_preserves_previous_snapshot(capture_env, second_page):
    path, _, fake = capture_env
    path.parent.mkdir(parents=True)
    path.write_text('previous complete snapshot', encoding='utf-8')
    fake([{'tools': [], 'nextCursor': 'repeat'}, second_page])
    assert HiggsfieldProvider().verify('oauth').ok is False
    assert path.read_text(encoding='utf-8') == 'previous complete snapshot'


def test_write_failure_is_logged_without_failing_probe_or_leaking_exception(capture_env, monkeypatch):
    from mc.desk_connect import higgsfield_mcp_snapshot as snapshot
    path, _, fake = capture_env
    fake([{'tools': [{'name': 'generate_video', 'inputSchema': {}}]}])
    logs = []

    def fail(*args, **kw):
        raise OSError('Authorization: ' + TOKEN)

    monkeypatch.setattr(snapshot, 'write_json_atomic', fail)
    monkeypatch.setattr(snapshot, '_log', lambda message, **kw: logs.append(message))
    assert HiggsfieldProvider().verify('oauth').ok is True
    assert not path.exists()
    assert len(logs) == 1 and 'snapshot write failed' in logs[0] and 'OSError' in logs[0]
    assert TOKEN not in logs[0]


def test_empty_list_replaces_previous_snapshot(capture_env):
    path, _, fake = capture_env
    path.parent.mkdir(parents=True)
    path.write_text('old snapshot', encoding='utf-8')
    fake([{'tools': []}])
    assert HiggsfieldProvider().verify('oauth').ok is True
    assert json.loads(path.read_text(encoding='utf-8'))['tools'] == []


def test_catalogue_and_declared_output_schemas_survive_capture_without_calls(capture_env):
    from mc.desk_connect import higgsfield_mcp_snapshot as snapshot
    path, calls, fake = capture_env
    output = {'type': 'object', 'properties': {'media_id': {'type': 'string'},
                                             'upload_url': {'type': 'string'}}}
    fake([{'tools': [
        {'name': 'models_explore', 'inputSchema': {'type': 'object'}, 'description': 'catalogue'},
        {'name': 'media_upload', 'inputSchema': {}, 'outputSchema': output,
         'headers': {'Authorization': TOKEN}},
        {'name': 'media_confirm', 'inputSchema': {}, 'outputSchema': None},
    ]}])
    assert HiggsfieldProvider().verify('oauth').ok is True
    doc = json.loads(path.read_text(encoding='utf-8'))
    assert doc['capture_version'] == snapshot.CAPTURE_VERSION
    assert [t['name'] for t in doc['tools']] == ['models_explore', 'media_upload', 'media_confirm']
    assert doc['tools'][1]['outputSchema'] == output
    assert 'outputSchema' not in doc['tools'][2]
    assert TOKEN not in path.read_text(encoding='utf-8')
    assert [c['method'] for c in calls] == ['initialize', 'notifications/initialized', 'tools/list']


def test_endless_unique_cursors_are_bounded_without_writing_partial_schemas(capture_env):
    path, calls, fake = capture_env
    fake([{'tools': [], 'nextCursor': f'page-{n}'} for n in range(50)])
    assert HiggsfieldProvider().verify('oauth').ok is False
    assert len([c for c in calls if c['method'] == 'tools/list']) == 50
    assert not path.exists()
