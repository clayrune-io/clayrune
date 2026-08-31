"""MC-929: the conversation rail must not be Claude-only.

`_recent_claude_transcripts()` only ever sees Claude Code .jsonl transcripts —
non-Claude runtimes (Gemini, ...) leave none (`transcript_path()` returns None
by design) and never populate `claude_session_id` (MC-922). Before this fix
`get_project_conversations` built the ENTIRE list from that one source, so a
non-Claude chat could never appear, let alone be reopened.

These tests exercise the union at the HTTP layer: a non-Claude agent-log
session appears in `/conversations`; a Claude conversation still appears
exactly once and keeps its live-status join; an auth-probe leftover is still
filtered. Plus the two `reconstruct_dead_session` branches this fix adds:
read-only history for a dead non-Claude session, and that replying to it
genuinely starts a fresh session rather than 404ing (the thing that makes the
honest banner text true instead of a lie).
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
    # No Claude transcripts anywhere unless a test overrides it — isolates the
    # non-Claude path from whatever real transcript scanning would do.
    monkeypatch.setattr(ar, '_recent_claude_transcripts', lambda project_path, limit=10: [])

    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)


def _write_log(tmp_path, project_id, entries):
    (tmp_path / 'projects' / f'{project_id}_agent_log.json').write_text(
        __import__('json').dumps(entries), encoding='utf-8')


def _gemini_entry(session_id, ts, task, summary, character=None):
    return {
        'ts': ts, 'task': task, 'status': 'completed', 'summary': summary,
        'session_id': session_id, 'claude_session_id': '', 'started_at': ts,
        'trigger_type': 'manual', 'source': 'ui', 'provider': 'gemini',
        'character': character, 'num_turns': 1,
    }


# ── a non-Claude session appears in /conversations ────────────────────────────

def test_non_claude_session_appears_in_conversations(client, tmp_path):
    _write_log(tmp_path, 'proj1', [
        _gemini_entry('geminisid1', '2026-08-31T10:00:00Z', 'first turn', 'the reply',
                       character={'name': 'market-scout', 'scope': 'project',
                                   'agent_name': 'Halloway'}),
    ])
    resp = client.get('/api/project/proj1/conversations')
    assert resp.status_code == 200
    rows = resp.get_json()
    assert len(rows) == 1
    row = rows[0]
    assert row['mc_session_id'] == 'geminisid1'
    assert row['claude_session_id'] == ''
    assert row['provider'] == 'gemini'
    assert row['resumable'] is False
    assert row['resume_mode'] == 'readonly'
    # Character survives the union — the row shows WHO the chat was with, even
    # though the persona lookup itself fails here (no real character file) and
    # falls back to the log's own snapshot.
    assert row['character'] is not None
    assert row['character']['agent_name'] == 'Halloway'


def test_non_claude_session_groups_multiple_turns(client, tmp_path):
    """Mode A logs one agent_log row per turn — the union must group them into
    ONE conversation, not one row per turn."""
    _write_log(tmp_path, 'proj1', [
        _gemini_entry('geminisid1', '2026-08-31T10:00:00Z', 'first turn', 'reply one'),
        _gemini_entry('geminisid1', '2026-08-31T10:05:00Z', 'first turn', 'reply two'),
    ])
    rows = client.get('/api/project/proj1/conversations').get_json()
    assert len(rows) == 1
    assert rows[0]['turns'] == 2
    assert rows[0]['last_user'] == 'reply two'


def test_hivemind_worker_turn_excluded_from_rail(client, tmp_path):
    entry = _gemini_entry('workersid', '2026-08-31T10:00:00Z', 'work', 'done')
    entry['hivemind_ws_id'] = 'ws1'
    _write_log(tmp_path, 'proj1', [entry])
    rows = client.get('/api/project/proj1/conversations').get_json()
    assert rows == []


# ── a Claude conversation still appears exactly once, live-status join intact ─

def test_claude_conversation_still_appears_once_with_live_join(client, tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as ar
    from mc import state as mc_state

    csid = 'claude-csid-1'
    monkeypatch.setattr(ar, '_recent_claude_transcripts', lambda project_path, limit=10: [
        {'session_id': csid, 'mtime': 1000.0, 'first_user': 'hello',
         'last_user': 'hello', 'turns': 1, 'size': 10},
    ])
    mc_state.agent_sessions['mcsid1'] = {
        'project_id': 'proj1', 'session_id': 'mcsid1', 'claude_session_id': csid,
        'status': 'running', 'task': 'hello',
    }
    # An agent-log row for the SAME session (provider claude, has the csid) —
    # must not ALSO be picked up by the non-claude union path.
    _write_log(tmp_path, 'proj1', [
        {'ts': '2026-08-31T10:00:00Z', 'task': 'hello', 'status': 'running',
         'summary': '', 'session_id': 'mcsid1', 'claude_session_id': csid,
         'started_at': '2026-08-31T10:00:00Z', 'trigger_type': 'manual',
         'source': 'ui', 'provider': 'claude', 'character': None},
    ])
    rows = client.get('/api/project/proj1/conversations').get_json()
    matches = [r for r in rows if r['claude_session_id'] == csid]
    assert len(matches) == 1
    row = matches[0]
    assert row['live'] is True
    assert row['status'] == 'running'
    assert row['mc_session_id'] == 'mcsid1'
    assert row['provider'] == 'claude'
    assert row['resumable'] is True


# ── auth-probe leftover is still filtered, alongside a real non-claude row ────

def test_auth_probe_filtered_non_claude_row_kept(client, tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as ar

    monkeypatch.setattr(ar, '_recent_claude_transcripts', lambda project_path, limit=10: [
        {'session_id': 'ghost-probe', 'mtime': 2000.0, 'first_user': 'ok',
         'last_user': 'ok', 'turns': 1, 'size': 5},
    ])
    _write_log(tmp_path, 'proj1', [
        _gemini_entry('geminisid1', '2026-08-31T10:00:00Z', 'first turn', 'the reply'),
    ])
    rows = client.get('/api/project/proj1/conversations').get_json()
    sids = {r['claude_session_id'] or r['mc_session_id'] for r in rows}
    assert 'ghost-probe' not in sids
    assert 'geminisid1' in sids


# ── reconstruct_dead_session: honest read-only history for a dead non-Claude session ─

def test_reconstruct_dead_non_claude_session_is_readonly(client, tmp_path):
    _write_log(tmp_path, 'proj1', [
        _gemini_entry('geminisid1', '2026-08-31T10:00:00Z', 'first turn', 'the reply'),
    ])
    resp = client.get('/api/project/proj1/session/geminisid1/reconstruct')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['read_only'] is True
    assert body['resumable'] is False
    assert body['claude_session_id'] == ''
    assert body['provider'] == 'gemini'
    joined = '\n'.join(body['log_lines'])
    assert 'the reply' in joined
    assert 'read-only' in joined.lower()
    assert 'brand-new session' in joined


def test_reconstruct_dead_claude_session_without_csid_still_404s(client, tmp_path):
    """A stale/legacy Claude row with no csid at all is not silently rendered as
    a fake non-Claude read-only view — same 404 contract as before this fix."""
    _write_log(tmp_path, 'proj1', [
        {'ts': '2026-08-31T10:00:00Z', 'task': 'hi', 'status': 'interrupted',
         'summary': '', 'session_id': 'sid-no-csid', 'claude_session_id': '',
         'started_at': '2026-08-31T10:00:00Z', 'trigger_type': 'manual',
         'source': 'ui', 'provider': 'claude', 'character': None},
    ])
    resp = client.get('/api/project/proj1/session/sid-no-csid/reconstruct')
    assert resp.status_code == 404


# ── a reply to a reconstructed non-Claude session actually starts a session ───
# (the thing that makes the "sending a message starts a brand-new session"
# banner text true, instead of the 404-shaped lie it would otherwise be)

def test_revive_non_claude_dispatches_fresh_session(client, tmp_path, monkeypatch):
    from mc.blueprints import agent_routes as ar

    _write_log(tmp_path, 'proj1', [
        _gemini_entry('geminisid1', '2026-08-31T10:00:00Z', 'first turn', 'the reply',
                       character={'name': 'market-scout', 'scope': 'project'}),
    ])
    calls = {}

    def _fake_dispatch(project_id, task, **kw):
        calls['project_id'] = project_id
        calls['task'] = task
        calls['kw'] = kw
        return kw.get('reuse_session_id')

    monkeypatch.setattr(ar, '_dispatch_agent_internal', _fake_dispatch)
    p = ar.load_project('proj1')
    result = ar._revive_non_claude_from_agent_log('proj1', 'geminisid1', 'continue please', p)
    assert result == 'geminisid1'
    assert calls['task'] == 'continue please'
    assert calls['kw']['reuse_session_id'] == 'geminisid1'
    assert calls['kw']['provider_override'] == 'gemini'
    assert calls['kw']['character'] == 'market-scout'


def test_revive_non_claude_returns_none_for_claude_session(client, tmp_path):
    """Guards the fallback from swallowing a genuine Claude row that merely
    hasn't been assigned a csid yet — that's not this function's job."""
    from mc.blueprints import agent_routes as ar

    _write_log(tmp_path, 'proj1', [
        {'ts': '2026-08-31T10:00:00Z', 'task': 'hi', 'status': 'in_progress',
         'summary': '', 'session_id': 'sid-claude', 'claude_session_id': '',
         'started_at': '2026-08-31T10:00:00Z', 'trigger_type': 'manual',
         'source': 'ui', 'provider': 'claude', 'character': None},
    ])
    p = ar.load_project('proj1')
    assert ar._revive_non_claude_from_agent_log('proj1', 'sid-claude', 'hi', p) is None
