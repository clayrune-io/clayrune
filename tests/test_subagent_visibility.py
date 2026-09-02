"""Live subagent visibility (MC-937 Phase 4): agent_runtime.py's
ClaudeRuntime.list_running_subagents() + mc/blueprints/agent_routes.py's
_active_subagents_for_session() / GET /api/project/<id>/agent/status.

Ron, twice: "no way of telling something is in works right now" / "I see no
one on the panes" — a Task-tool subagent runs entirely inside its parent's
CLI process and writes only to its own nested transcript
(`<encoded>/<parent_csid>/subagents/agent-<id>.jsonl`), so nothing on the
Floor or in the rail ever showed one existed. This is the backend half:
a source of truth for currently-running subagents on the same endpoint
the rail/Floor already poll.

Four properties under test:
  1. a subagent whose transcript is still being written, under a parent
     session that is itself 'running', is reported with running=True.
  2. a subagent that finished — either long enough ago, or because its
     parent session itself is no longer 'running' — is never reported as
     running (and drops out of the list entirely once stale enough).
  3. ClaudeRuntime.list_running_subagents() caches per-file by (mtime,
     size): an unchanged transcript is never re-parsed.
  4. a project with no subagents gets active_subagents: [] without
     disturbing any pre-existing /agent/status field.
"""
import json
import sys
import threading
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def _write_subagent_transcript(path, lines):
    """lines: [(timestamp, 'user'|'assistant', message_dict), ...] — mirrors
    the real on-disk shape (one JSON object per line, a top-level
    'timestamp' + 'type' + 'message')."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        for ts, typ, message in lines:
            fh.write(json.dumps({'type': typ, 'timestamp': ts, 'message': message}) + '\n')


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    import mc.agent_runtime as art
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la
    from mc.blueprints import project_routes as pr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(pr, 'PROJECTS_BASE', tmp_path)
    monkeypatch.setattr(pr, 'get_manager',
                        lambda pid: types.SimpleNamespace(lock=threading.Lock()))

    project_path = tmp_path / 'repo'
    project_path.mkdir()
    (data_dir / 'p1.json').write_text(
        json.dumps({'id': 'p1', 'name': 'P1', 'backlog': [],
                    'project_path': str(project_path)}), encoding='utf-8')

    claude_home = tmp_path / 'claude_projects'
    claude_home.mkdir()
    monkeypatch.setattr(art, '_CLAUDE_HOME', claude_home)
    # Per-transcript scan cache is process-global — a stale entry from an
    # earlier test (same tmp path shape reused across a run) must not hide a
    # real result. Each test gets a clean cache (mirrors test_documents_tab's
    # _DOC_WRITE_CACHE reset).
    monkeypatch.setattr(art, '_SUBAGENT_SCAN_CACHE', {})

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    server.app.config['TESTING'] = True
    c = types.SimpleNamespace(
        ar=ar, art=art, project_path=project_path, claude_home=claude_home,
        sessions=mc_state.agent_sessions, client=server.app.test_client())
    yield c

    mc_state.agent_sessions.clear()
    mc_state.agent_sessions.update(snapshot)


def _encoded_dir(env):
    encoded = env.art.ClaudeRuntime._encode_project_path(str(env.project_path))
    d = env.claude_home / encoded
    d.mkdir(parents=True, exist_ok=True)
    return d


def _seed_running_session(env, csid='parent-csid', session_id='s1'):
    """A 'running' MC session already attached to a claude_session_id, with
    that parent's own (otherwise-irrelevant) top-level transcript on disk —
    transcript_path() requires it to exist to resolve the encoded dir."""
    d = _encoded_dir(env)
    (d / f'{csid}.jsonl').write_text(
        json.dumps({'type': 'user', 'timestamp': _iso(datetime.now(timezone.utc)),
                    'message': {'role': 'user', 'content': 'go'}}) + '\n',
        encoding='utf-8')
    env.sessions[session_id] = {
        'session_id': session_id, 'project_id': 'p1', 'status': 'running',
        'task': 'do the thing', 'log_lines': [],
        'started_at': '2026-09-02T00:00:00Z', 'claude_session_id': csid,
    }
    return d


# ── liveness ──────────────────────────────────────────────────────────────

def test_a_live_subagent_is_reported_as_running(env):
    d = _seed_running_session(env)
    now = datetime.now(timezone.utc)
    sub = d / 'parent-csid' / 'subagents' / 'agent-abc123.jsonl'
    _write_subagent_transcript(sub, [
        (_iso(now - timedelta(seconds=10)), 'user',
         {'role': 'user', 'content': 'MANDATE: root-cause the flaky test'}),
        (_iso(now - timedelta(seconds=8)), 'assistant',
         {'content': [{'type': 'tool_use', 'name': 'Bash', 'id': 't1',
                       'input': {'command': 'ls'}}]}),
        (_iso(now - timedelta(seconds=1)), 'assistant',
         {'content': [{'type': 'tool_use', 'name': 'Read', 'id': 't2',
                       'input': {'file_path': 'x'}}]}),
    ])
    body = env.client.get('/api/project/p1/agent/status').get_json()
    subs = body['sessions'][0]['active_subagents']
    assert len(subs) == 1
    row = subs[0]
    assert row['agent_id'] == 'abc123'
    assert row['running'] is True
    assert row['tool_calls'] == 2
    assert row['parent_claude_session_id'] == 'parent-csid'
    assert 'root-cause the flaky test' in row['label']
    assert row['elapsed_seconds'] >= 9


def test_a_recently_finished_subagent_is_not_reported_as_running(env):
    """Between the running window (120s) and the visibility window (600s):
    still shown — so a frontend CAN render a brief 'done' state — but
    running is False. Never running past the window."""
    d = _seed_running_session(env)
    now = datetime.now(timezone.utc)
    sub = d / 'parent-csid' / 'subagents' / 'agent-def456.jsonl'
    _write_subagent_transcript(sub, [
        (_iso(now - timedelta(seconds=310)), 'user', {'role': 'user', 'content': 'go do it'}),
        (_iso(now - timedelta(seconds=305)), 'assistant',
         {'content': [{'type': 'tool_use', 'name': 'Bash', 'id': 't1', 'input': {}}]}),
    ])
    body = env.client.get('/api/project/p1/agent/status').get_json()
    subs = body['sessions'][0]['active_subagents']
    assert len(subs) == 1
    assert subs[0]['running'] is False


def test_a_subagent_finished_days_ago_is_never_reported_at_all(env):
    d = _seed_running_session(env)
    now = datetime.now(timezone.utc)
    sub = d / 'parent-csid' / 'subagents' / 'agent-old999.jsonl'
    _write_subagent_transcript(sub, [
        (_iso(now - timedelta(days=3)), 'user', {'role': 'user', 'content': 'ancient task'}),
        (_iso(now - timedelta(days=3) + timedelta(seconds=5)), 'assistant',
         {'content': [{'type': 'tool_use', 'name': 'Bash', 'id': 't1', 'input': {}}]}),
    ])
    body = env.client.get('/api/project/p1/agent/status').get_json()
    assert body['sessions'][0]['active_subagents'] == []


def test_a_subagent_under_a_no_longer_running_parent_is_not_reported(env):
    """Gate #1: even a millisecond-fresh transcript file cannot render as
    running once the parent session itself is no longer 'running' — a
    Task-tool subagent cannot outlive the process it runs inside."""
    d = _seed_running_session(env)
    env.sessions['s1']['status'] = 'completed'
    now = datetime.now(timezone.utc)
    sub = d / 'parent-csid' / 'subagents' / 'agent-ghost1.jsonl'
    _write_subagent_transcript(sub, [
        (_iso(now), 'user', {'role': 'user', 'content': 'fresh but orphaned'}),
    ])
    body = env.client.get('/api/project/p1/agent/status').get_json()
    assert body['sessions'][0]['active_subagents'] == []


# ── caching ───────────────────────────────────────────────────────────────

def test_an_unchanged_subagent_transcript_is_not_reparsed(env, monkeypatch):
    d = _seed_running_session(env)
    now = datetime.now(timezone.utc)
    sub = d / 'parent-csid' / 'subagents' / 'agent-cache1.jsonl'
    _write_subagent_transcript(sub, [
        (_iso(now), 'user', {'role': 'user', 'content': 'once'}),
    ])
    rt = env.art.get_runtime('claude')
    first = rt.list_running_subagents(str(env.project_path), 'parent-csid')
    assert len(first) == 1

    # If the cache didn't hold, a second call would call parse_event() again;
    # break parse_event and prove the cached path is what actually ran.
    def _boom(*a, **k):
        raise AssertionError('parse_event called on an unchanged subagent transcript')
    monkeypatch.setattr(rt, 'parse_event', _boom)
    second = rt.list_running_subagents(str(env.project_path), 'parent-csid')
    assert second == first


# ── shape stability ─────────────────────────────────────────────────────────

def test_a_project_with_no_subagents_gets_an_empty_list_and_shape_is_unchanged(env):
    _seed_running_session(env)
    body = env.client.get('/api/project/p1/agent/status').get_json()
    row = body['sessions'][0]
    assert row['active_subagents'] == []
    # Pre-existing fields untouched by this change.
    assert row['session_id'] == 's1'
    assert row['status'] == 'running'
    assert row['character'] is None
    assert 'condense' in body
