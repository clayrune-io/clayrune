"""A LIVE session's persona must reach the conversation row.

`/conversations` derived `character` only from the agent-log row, which is
written from the dispatch payload. A session that inherited a project default
(or had its persona resolved after spawn) has no character there — so the row
came back faceless even while that agent was visibly mid-turn.

Observed 2026-09-02 on `find_ron_a_job`: `/agent/status` reported the live
session as Dave (global:dave) while `/conversations` returned `character: null`
for that same csid. The Channel roster derives membership from those rows, so
it said "No conversations with Dave yet" about the chat open on the right of
the same screen.
"""
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


CSID = 'b89b6a8e-a620-4cc7-be57-9168bbd89a58'


def _wire(monkeypatch, tmp_path, *, session_char, log_char):
    """One live transcript, a live session, and an agent-log row for it."""
    from mc.blueprints import agent_routes as ar
    from mc import state as mc_state

    monkeypatch.setattr(ar, 'load_project', lambda pid: (
        {'id': 'p1', 'name': 'P1', 'project_path': str(tmp_path)} if pid == 'p1' else None))
    monkeypatch.setattr(ar, '_recent_claude_transcripts', lambda path, limit=20: [
        {'session_id': CSID, 'first_user': 'hi', 'last_user': 'hi',
         'turns': 2, 'size': 10, 'mtime': 1_760_000_000.0}])
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: (
        [{'claude_session_id': CSID, 'session_id': 'mc1', 'status': 'running',
          'character': log_char}] if log_char is not None else []))
    monkeypatch.setattr(ar, '_non_claude_conversation_rows', lambda *a, **k: [])
    mc_state.agent_sessions['mc1'] = {
        'project_id': 'p1', 'claude_session_id': CSID, 'session_id': 'mc1',
        'status': 'running', 'character': session_char,
    }


DAVE = {'name': 'dave', 'scope': 'global', 'agent_name': 'Dave'}


def test_live_session_persona_reaches_the_row_when_the_log_row_has_none(
        client, monkeypatch, tmp_path):
    """The defect: log row faceless, live session knows it's Dave."""
    _wire(monkeypatch, tmp_path, session_char=DAVE, log_char=None)
    row = client.get('/api/project/p1/conversations').get_json()[0]
    assert row['claude_session_id'] == CSID
    assert row['character'], 'row came back faceless while the session knew its persona'
    assert row['character']['name'] == 'dave'
    assert row['character']['scope'] == 'global'


def test_the_log_row_still_wins_when_it_has_a_persona(client, monkeypatch, tmp_path):
    """The fallback must not override a persona the dispatch actually recorded."""
    _wire(monkeypatch, tmp_path,
          session_char=DAVE,
          log_char={'name': 'code-reviewer', 'scope': 'global', 'agent_name': 'Fenn'})
    row = client.get('/api/project/p1/conversations').get_json()[0]
    assert row['character']['name'] == 'code-reviewer'


def test_no_persona_anywhere_stays_null(client, monkeypatch, tmp_path):
    """An ordinary personaless chat must not grow a face."""
    _wire(monkeypatch, tmp_path, session_char=None, log_char=None)
    row = client.get('/api/project/p1/conversations').get_json()[0]
    assert row['character'] is None
