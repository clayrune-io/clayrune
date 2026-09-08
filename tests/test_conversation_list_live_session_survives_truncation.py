"""ws001/D6 regression: `/conversations` must never truncate a chat that is
running RIGHT NOW off its own list.

hm_d9c76579 finding D6 (docs/_ws002_session_state_truth_inventory.md):
`get_project_conversations` (mc/blueprints/agent_routes.py) builds its list
from transcript file mtimes, not from live session state — `agent_sessions`
is only a JOIN overlay applied AFTER the transcript scan already cut to
`limit`. Two independent truncation points:

  1. `_recent_claude_transcripts(project_path, limit=limit)` sorts transcript
     FILES by mtime and slices to `limit` before the live join ever runs — a
     live session whose transcript isn't in the freshest `limit` by mtime is
     never even a candidate row.
  2. The final union of Claude + Codex + agent-log rows is re-sorted and cut
     to `limit` AGAIN, with no live-session guarantee either.

CONSEQUENCE (measured live on localhost:5199): start a few other chats, or
let 'limit' other transcripts get touched, and a conversation you are
ACTIVELY IN drops off the list mid-flow, then reappears once its own
transcript is next written to. No purge, no race, no restart needed — this
is the deterministic half of Ron's "conversations disappearing" symptom.

This test drives the real route end-to-end (Flask test client, real
transcript files on disk under a faked ~/.claude/projects/<encoded> dir, a
real `agent_sessions` live entry) rather than asserting only that
`ClaudeRuntime.list_sessions(must_include_csids=...)` works in isolation —
that unit-level proof lives in test_claude_runtime.py; this is the
route-level proof that `get_project_conversations` actually wires it in.
Fails on the parent commit.
"""
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Same shape as test_agent_routes.client, plus a faked Claude transcript
    home so real .jsonl files can be scanned end to end."""
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state as mc_state
    from mc import agent_runtime as art
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)

    fake_claude_home = tmp_path / '.claude_home' / 'projects'
    monkeypatch.setattr(art, '_CLAUDE_HOME', fake_claude_home)

    project_path = tmp_path / 'proj1'
    project_path.mkdir()
    monkeypatch.setattr(ar, 'load_project', lambda pid: (
        {'id': pid, 'project_path': str(project_path)} if pid == 'proj1' else None))

    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client(), project_path
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)


def _write_transcript(fake_home, project_path, session_id, mtime, text):
    from mc.agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    encoded = rt._encode_project_path(str(project_path))
    d = fake_home / encoded
    d.mkdir(parents=True, exist_ok=True)
    f = d / f'{session_id}.jsonl'
    f.write_text(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': text}}),
                 encoding='utf-8')
    os.utime(f, (mtime, mtime))


def test_live_session_survives_the_conversations_list_cut(client, tmp_path, monkeypatch):
    from mc import state as mc_state
    from mc import agent_runtime as art

    test_client, project_path = client
    fake_home = art._CLAUDE_HOME

    live_csid = 'live-running-chat'
    _write_transcript(fake_home, project_path, live_csid, 1_000_000_000, 'still going')

    # 5 decoy transcripts, all touched more recently than the live chat.
    for i in range(5):
        _write_transcript(fake_home, project_path, f'decoy-{i}', 2_000_000_000 + i, f'msg {i}')

    # The live session itself — this is what SHOULD protect its transcript
    # from the mtime cut. No agent-log entry needed; agent_sessions alone
    # must be enough (matches production: a live session's log row is often
    # written only at completion).
    mc_state.agent_sessions['mcsid-live'] = {
        'project_id': 'proj1', 'session_id': 'mcsid-live',
        'claude_session_id': live_csid, 'status': 'running', 'task': 'still going',
    }

    resp = test_client.get('/api/project/proj1/conversations?limit=3')
    assert resp.status_code == 200, resp.get_data(as_text=True)
    rows = resp.get_json()

    sids = [r.get('claude_session_id') for r in rows]
    live_rows = [r for r in rows if r.get('claude_session_id') == live_csid]
    assert live_rows, (
        f"live session {live_csid!r} (status=running) was truncated off "
        f"/conversations despite being live right now — got claude_session_ids "
        f"{sids} (limit=3, 5 fresher decoys exist)")
    assert live_rows[0]['live'] is True
    assert live_rows[0]['status'] == 'running'
