"""MC-946 nesting (Floor conversations rail): a dispatched worker must carry
its spawner's session id all the way to `/conversations`.

`_log_agent_completion` now writes `spawned_by_session_id` onto the durable
agent-log row (see `test_agent_spawn_notify.py`), but that alone is not
enough — the row is dead weight until `/api/project/<id>/conversations`
actually emits it, and a worker still mid-run has no agent-log row at all
until it finishes. Mirrors the fallback shape `test_conversation_character_
fallback.py` already proved out for `character`: log row first, live session
(`_notify_session`) second.
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


def _wire(monkeypatch, tmp_path, *, session_notify=None, log_spawner=None):
    from mc.blueprints import agent_routes as ar
    from mc import state as mc_state

    monkeypatch.setattr(ar, 'load_project', lambda pid: (
        {'id': 'p1', 'name': 'P1', 'project_path': str(tmp_path)} if pid == 'p1' else None))
    monkeypatch.setattr(ar, '_recent_claude_transcripts', lambda path, limit=20, must_include_csids=None: [
        {'session_id': CSID, 'first_user': 'hi', 'last_user': 'hi',
         'turns': 2, 'size': 10, 'mtime': 1_760_000_000.0}])
    log_row = {'claude_session_id': CSID, 'session_id': 'mc1', 'status': 'running'}
    if log_spawner is not None:
        log_row['spawned_by_session_id'] = log_spawner
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [log_row])
    monkeypatch.setattr(ar, '_non_claude_conversation_rows', lambda *a, **k: [])
    sess = {'project_id': 'p1', 'claude_session_id': CSID, 'session_id': 'mc1',
            'status': 'running'}
    if session_notify is not None:
        sess['_notify_session'] = session_notify
    mc_state.agent_sessions['mc1'] = sess


def test_spawner_from_the_agent_log_row_reaches_the_conversation(client, monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, log_spawner='parent-1')
    row = client.get('/api/project/p1/conversations').get_json()[0]
    assert row['spawned_by_session_id'] == 'parent-1'


def test_spawner_falls_back_to_the_live_session_before_the_log_row_has_one(
        client, monkeypatch, tmp_path):
    """A worker still running has no agent-log row yet — only the live
    session (`_notify_session`, set at dispatch) knows who spawned it."""
    _wire(monkeypatch, tmp_path, session_notify='parent-2', log_spawner=None)
    row = client.get('/api/project/p1/conversations').get_json()[0]
    assert row['spawned_by_session_id'] == 'parent-2'


def test_the_log_row_wins_once_it_has_a_spawner(client, monkeypatch, tmp_path):
    """Once completion has written the durable row, that value is authoritative
    even if the (now-stale) live session dict still carries the old field."""
    _wire(monkeypatch, tmp_path, session_notify='stale-parent', log_spawner='parent-3')
    row = client.get('/api/project/p1/conversations').get_json()[0]
    assert row['spawned_by_session_id'] == 'parent-3'


def test_ordinary_conversation_has_no_spawner(client, monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    row = client.get('/api/project/p1/conversations').get_json()[0]
    assert row['spawned_by_session_id'] == ''
