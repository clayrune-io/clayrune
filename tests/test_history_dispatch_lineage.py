"""History reconstruction returns the same durable spawner as conversation rows."""
import pytest
from types import SimpleNamespace
from flask import Flask
from mc.blueprints import agent_routes as ar


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.register_blueprint(ar.bp)
    monkeypatch.setattr(ar, 'load_project', lambda pid: {'id': pid, 'project_path': '/fixture'})
    monkeypatch.setattr(ar, 'agent_sessions', {})
    monkeypatch.setattr(ar, '_conversation_cutover', None)
    monkeypatch.setattr(ar, '_transcript_buffer_lines_and_ts', lambda *a, **k: (['answer'], [None]))
    monkeypatch.setattr(ar, '_transcript_buffer_lines', lambda *a, **k: ['answer'])
    return app.test_client()


@pytest.mark.parametrize('metadata,expected', [
    ({'dispatched_by_session_id': 'recorded-parent'}, 'recorded-parent'),
    ({'trigger_type': 'dispatch', 'spawned_by_session_id': 'legacy-parent'}, 'legacy-parent'),
    ({'spawned_by_session_id': 'callback-only', 'trigger_type': 'manual'}, ''),
    ({}, ''),
    ({'dispatched_by_session_id': 'child'}, ''),
])
@pytest.mark.parametrize('path', ['session/child', 'transcript/transcript1'])
def test_reconstruct_preserves_only_recorded_lineage(client, monkeypatch, metadata, expected, path):
    rows = [{'session_id': 'child', 'claude_session_id': 'transcript1', 'task': 'build', 'ts': '1', **metadata}]
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: rows)
    result = client.get(f'/api/project/fixture/{path}/reconstruct')
    assert result.status_code == 200
    assert result.json['spawned_by_session_id'] == expected


def test_legacy_cycle_is_not_reintroduced_by_reconstruction(client, monkeypatch):
    rows = [{'session_id': 'child', 'claude_session_id': 'csid', 'ts': '1',
             'trigger_type': 'dispatch', 'spawned_by_session_id': 'parent'},
            {'session_id': 'parent', 'ts': '1', 'trigger_type': 'dispatch', 'spawned_by_session_id': 'child'}]
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: rows)
    assert client.get('/api/project/fixture/session/child/reconstruct').json['spawned_by_session_id'] == ''


def test_non_claude_log_only_history_also_preserves_parent(client, monkeypatch):
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [
        {'session_id': 'child', 'task': 'build', 'summary': 'done', 'provider': 'gemini',
         'dispatched_by_session_id': 'recorded-parent'}])
    result = client.get('/api/project/fixture/session/child/reconstruct')
    assert result.status_code == 200
    assert result.json['spawned_by_session_id'] == 'recorded-parent'


def test_codex_transcript_history_preserves_parent(client, monkeypatch):
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [
        {'session_id': 'child', 'task': 'build', 'provider': 'codex', 'provider_session_id': 'rollout1',
         'dispatched_by_session_id': 'recorded-parent'}])
    runtime = SimpleNamespace(transcript_path=lambda *a: '/fixture/rollout',
                              extract_chat_turns_with_ts=lambda *a, **k: [('assistant', 'done', None)])
    monkeypatch.setattr(ar._agent_runtime, 'get_runtime', lambda *a: runtime)
    result = client.get('/api/project/fixture/session/child/reconstruct')
    assert result.status_code == 200
    assert result.json['resumable'] is True
    assert result.json['spawned_by_session_id'] == 'recorded-parent'


def test_canonical_history_preserves_parent(client, monkeypatch):
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [
        {'session_id': 'child', 'dispatched_by_session_id': 'recorded-parent'}])
    monkeypatch.setattr(ar, '_conversation_cutover', SimpleNamespace(display_lines=lambda *a: ['answer']))
    result = client.get('/api/project/fixture/session/child/reconstruct')
    assert result.status_code == 200
    assert result.json['canonical'] is True
    assert result.json['spawned_by_session_id'] == 'recorded-parent'
