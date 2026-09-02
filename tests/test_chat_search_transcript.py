"""GET /api/project/<id>/transcript/<csid>/full-buffer — in-chat search's
server-side source of truth (mc/blueprints/agent_routes.py).

The chat pane the client renders from is doubly capped: conversation.js only
draws the last 500 lines unless the user clicks "load all" (client-side, easy
to solve), and the client's OWN in-memory buffer is trimmed to the last 1500
lines once a live session passes 2000 (resume-preview.js) — no client-side
"expand" can undo that one, the older lines are simply gone from the tab's
memory. This endpoint is what in-chat search reads instead: the complete,
uncapped transcript straight off disk, in the same log_lines shape the client
buffer already uses, so it can be adopted wholesale and rendered identically.

It deliberately does NOT share /transcript/<csid>/reconstruct's 409-on-live
guard: reconstruct refuses a live session because it's offering to *resume*
one (which a live session doesn't need), but a search must work on a
conversation that is still running, not just a finished one.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Same shape as test_agent_routes.client: module-scope deps patched, not server.*."""
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)
    monkeypatch.setattr(ar, 'load_project', lambda pid: (
        {'id': pid, 'project_path': str(tmp_path)} if pid == 'proj1' else None))

    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)


def test_unknown_project_404s(client):
    resp = client.get('/api/project/nope/transcript/csid123/full-buffer')
    assert resp.status_code == 404


def test_no_transcript_on_disk_404s(client, monkeypatch):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, '_transcript_buffer_lines', lambda pp, cs, ul, max_messages=None: [])
    resp = client.get('/api/project/proj1/transcript/csid123/full-buffer')
    assert resp.status_code == 404


def test_happy_path_returns_full_log_lines(client, monkeypatch):
    from mc.blueprints import agent_routes as ar
    lines = ['\n> Ron: what did we decide about the cap?\n', 'We capped it at 1500 lines.']
    monkeypatch.setattr(ar, '_transcript_buffer_lines', lambda pp, cs, ul, max_messages=None: lines)
    resp = client.get('/api/project/proj1/transcript/csid123/full-buffer')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['claude_session_id'] == 'csid123'
    assert body['log_lines'] == lines


def test_uses_the_no_meaningful_cap_convention(client, monkeypatch):
    """Search wants the WHOLE transcript, not the 300-message default the
    revive/preview paths use — this must not silently inherit that cap."""
    from mc.blueprints import agent_routes as ar
    seen = {}

    def _fake(pp, cs, ul, max_messages=None):
        seen['max_messages'] = max_messages
        return ['one line']
    monkeypatch.setattr(ar, '_transcript_buffer_lines', _fake)
    resp = client.get('/api/project/proj1/transcript/csid123/full-buffer')
    assert resp.status_code == 200
    assert seen['max_messages'] == 100000


def test_does_not_409_on_a_live_session(client, monkeypatch):
    """The one behavioral difference from .../reconstruct: search must work on
    a session that is still running, so this route must NOT refuse it."""
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, '_transcript_buffer_lines',
                        lambda pp, cs, ul, max_messages=None: ['still typing...'])
    mc_state.agent_sessions['mcsid1'] = {
        'project_id': 'proj1', 'status': 'running', 'claude_session_id': 'csid123',
    }
    resp = client.get('/api/project/proj1/transcript/csid123/full-buffer')
    assert resp.status_code == 200
    assert resp.get_json()['log_lines'] == ['still typing...']


def test_registered_under_agent_routes_blueprint(client):
    import server
    owned = {rule.rule for rule in server.app.url_map.iter_rules()
             if rule.endpoint.startswith('agent_routes.')}
    assert '/api/project/<project_id>/transcript/<claude_session_id>/full-buffer' in owned
