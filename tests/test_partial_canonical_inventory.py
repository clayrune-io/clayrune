from types import SimpleNamespace

from tests.test_agent_routes import client  # noqa: F401


def test_agent_log_keeps_legacy_inventory_with_one_canonical_row(client, monkeypatch):
    from mc.blueprints import agent_routes as ar
    legacy = [{'session_id': f'legacy-{n}', 'task': 'retained'} for n in range(100)]
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: legacy)
    monkeypatch.setattr(ar, '_conversation_cutover', SimpleNamespace(
        agent_log=lambda pid: [SimpleNamespace(as_dict=lambda: {'session_id': 'canonical-new'})]))
    response = client.get('/api/project/p/agent/log')
    assert response.status_code == 200
    assert {r['session_id'] for r in response.json} == {
        *(f'legacy-{n}' for n in range(100)), 'canonical-new'}


def test_rail_keeps_legacy_and_live_metadata_during_partial_adoption(client, monkeypatch):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, 'load_project', lambda pid: {'id': pid, 'project_path': '/unused'})
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [])
    monkeypatch.setattr(ar, '_recent_claude_transcripts', lambda *a, **kw: [])
    monkeypatch.setattr(ar, '_recent_codex_conversation_rows', lambda *a, **kw: ([], set()))
    legacy = [{'mc_session_id': 'old', 'mtime': 5, 'live': True, 'provider': 'codex', 'resumable': True}]
    monkeypatch.setattr(ar, '_non_claude_conversation_rows', lambda *a, **kw: legacy)
    monkeypatch.setattr(ar, '_conversation_cutover', SimpleNamespace(conversation_rows=lambda *a, **kw: [
        {'mc_session_id': 'old', 'mtime': 0, 'live': False, 'resumable': False},
        {'mc_session_id': 'new', 'mtime': 1, 'live': False}]))
    response = client.get('/api/project/p/conversations')
    assert response.status_code == 200
    rows = {r['mc_session_id']: r for r in response.json}
    assert set(rows) == {'old', 'new'}
    assert rows['old']['resumable'] and rows['old']['live']
