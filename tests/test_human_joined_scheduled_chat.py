"""A human's chat inside a SCHEDULED run must survive a restart in the Chats
list, and the startup backfill must not import Scribe/condense one-shots as
chats (2026-10-01, drop_shipping_company).

Part 1 — Ron chatted with Vector inside session 88001c6d8849, started by the
9am '[Brand inbox watch]' schedule (trigger_type 'schedule'). Live it showed;
after a restart only its agent_log row remained and conversation.js
`_isNoiseConvoRow` drops every trigger_type 'schedule' row. Fix: a DISPLAY-ONLY
`human_joined` flag, stamped when a browser caller (Origin header) sends into
such a session, persisted on its log row(s), passed through /conversations.
`trigger_type` is never touched and the fence never reads the flag.

Part 2 — 466 of that project's 500 log rows were synthesized one-shots (the
CLI files their transcripts with the chats because they run cwd=project_path),
evicting every real chat past `agent_log_max_entries`.
"""
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BROWSER = {'Origin': 'http://localhost:5199'}   # what a real SPA fetch carries


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server
    from mc import state as mc_state
    from mc import agent_runtime as art
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)
    monkeypatch.setattr(art, '_CLAUDE_HOME', tmp_path / '.claude_home' / 'projects')
    art._SESSION_ROW_CACHE.clear()

    project_path = tmp_path / 'proj1'
    project_path.mkdir()
    proj = {'id': 'proj1', 'project_path': str(project_path)}
    monkeypatch.setattr(ar, 'load_project', lambda pid: proj if pid == 'proj1' else None)

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield server, ar, art, mc_state, project_path
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def _row(sid='88001c6d8849', csid='csid-sched', trigger='schedule', **kw):
    r = {'ts': '2026-10-01T17:38:35Z', 'task': '[Brand inbox watch] check mail',
         'status': 'completed', 'session_id': sid, 'claude_session_id': csid,
         'claude_session_ids': [csid], 'trigger_type': trigger,
         'trigger_id': '3f6f6f8f', 'provider': 'claude'}
    r.update(kw)
    return r


def _live(ar, mc_state, trigger='schedule', sid='88001c6d8849'):
    s = {'session_id': sid, 'project_id': 'proj1', 'status': 'completed',
         'trigger_type': trigger, 'trigger_id': '3f6f6f8f', 'log_lines': [],
         'claude_session_id': 'csid-sched'}
    mc_state.agent_sessions[sid] = s
    return s


def _stamp(server, ar, headers, sid='88001c6d8849'):
    with server.app.test_request_context('/x', headers=headers):
        ar._mark_human_joined('proj1', sid)


# ── Part 1: the flag ─────────────────────────────────────────────────────────

def test_human_send_stamps_live_session_and_every_row_without_touching_trigger(env):
    server, ar, art, st, _ = env
    s = _live(ar, st)
    ar._save_agent_log('proj1', [_row(), _row(csid='csid-older', ts='2026-09-30T16:47:22Z'),
                                 _row(sid='other-session', csid='csid-other')])

    _stamp(server, ar, BROWSER)

    assert s['human_joined'] is True
    assert s['trigger_type'] == 'schedule'          # the fence keys on this: untouched
    rows = {r['claude_session_id']: r for r in ar._load_agent_log('proj1')}
    assert rows['csid-sched']['human_joined'] is True
    assert rows['csid-older']['human_joined'] is True
    assert rows['csid-sched']['trigger_type'] == 'schedule'
    assert 'human_joined' not in rows['csid-other']  # another session's row: untouched


def test_agent_caller_without_origin_never_stamps(env):
    """An agent's curl carries no Origin header — same signal as
    workflow_routes._is_agent_caller."""
    server, ar, art, st, _ = env
    s = _live(ar, st)
    ar._save_agent_log('proj1', [_row()])
    _stamp(server, ar, {})
    assert 'human_joined' not in s
    assert 'human_joined' not in ar._load_agent_log('proj1')[0]


def test_ordinary_manual_chat_is_not_stamped_and_log_is_not_rewritten(env):
    server, ar, art, st, _ = env
    s = _live(ar, st, trigger='manual', sid='m1')
    ar._save_agent_log('proj1', [_row(sid='m1', trigger='manual')])
    before = ar._agent_log_path('proj1').read_bytes()
    _stamp(server, ar, BROWSER, sid='m1')
    assert 'human_joined' not in s
    assert ar._agent_log_path('proj1').read_bytes() == before


def test_after_restart_the_row_alone_is_stamped(env):
    """No live session (server restarted): the log row is all that exists."""
    server, ar, art, st, _ = env
    ar._save_agent_log('proj1', [_row()])
    _stamp(server, ar, BROWSER)
    assert ar._load_agent_log('proj1')[0]['human_joined'] is True


def test_stamp_is_idempotent_and_row_addressed_by_csid_too(env):
    server, ar, art, st, _ = env
    ar._save_agent_log('proj1', [_row()])
    _stamp(server, ar, BROWSER, sid='csid-sched')      # client addressed by csid
    _stamp(server, ar, BROWSER, sid='csid-sched')
    rows = ar._load_agent_log('proj1')
    assert len(rows) == 1 and rows[0]['human_joined'] is True


def test_flag_survives_the_next_pending_upsert_and_the_revive_rebuild(env):
    """A scheduled cadence tick reusing the session_id builds a fresh session
    dict that never saw the human message — the upsert must carry it from the
    outgoing row, or the flag dies on the next fire."""
    server, ar, art, st, _ = env
    ar._save_agent_log('proj1', [_row(human_joined=True, status='completed')])
    fresh = {'session_id': '88001c6d8849', 'project_id': 'proj1', 'task': 't',
             'status': 'running', 'trigger_type': 'schedule', 'trigger_id': '3f6f6f8f',
             'provider': 'claude', 'started_at': '2026-10-02T16:00:00Z'}
    ar._log_agent_dispatch_pending(fresh)
    row = ar._load_agent_log('proj1')[0]
    assert row['status'] == 'in_progress'
    assert row['human_joined'] is True
    assert row['trigger_type'] == 'schedule'


def test_pending_row_carries_flag_from_the_live_session(env):
    server, ar, art, st, _ = env
    sess = {'session_id': 's9', 'project_id': 'proj1', 'task': 't', 'status': 'running',
            'trigger_type': 'schedule', 'provider': 'claude', 'human_joined': True,
            'started_at': '2026-10-02T16:00:00Z'}
    ar._log_agent_dispatch_pending(sess)
    assert ar._load_agent_log('proj1')[0]['human_joined'] is True


def test_conversations_payload_carries_the_flag(env):
    server, ar, art, st, project_path = env
    d = art._CLAUDE_HOME / art.ClaudeRuntime()._encode_project_path(str(project_path))
    d.mkdir(parents=True, exist_ok=True)
    (d / 'csid-sched.jsonl').write_text(
        json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'hello vector'}}),
        encoding='utf-8')
    (d / 'csid-plain.jsonl').write_text(
        json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'other'}}),
        encoding='utf-8')
    ar._save_agent_log('proj1', [_row(human_joined=True),
                                 _row(sid='s2', csid='csid-plain')])
    rows = server.app.test_client().get('/api/project/proj1/conversations?limit=10').get_json()
    by = {r['claude_session_id']: r for r in rows}
    assert by['csid-sched']['human_joined'] is True
    assert by['csid-sched']['trigger_type'] == 'schedule'
    assert by['csid-plain']['human_joined'] is False


def test_fence_still_arms_a_human_joined_scheduled_session(env):
    """HARD CONSTRAINT: the fence keys on trigger_type alone. A stamped session
    must report 'schedule' from the route the fence calls, and fence.py must
    not know the flag exists."""
    server, ar, art, st, _ = env
    s = _live(ar, st)
    ar._save_agent_log('proj1', [_row()])
    _stamp(server, ar, BROWSER)
    assert s['human_joined'] is True

    body = server.app.test_client().get(
        '/api/session/trigger-type?claude_session_id=csid-sched').get_json()
    assert body['found'] is True and body['trigger_type'] == 'schedule'
    assert 'human_joined' not in body

    import steward.fence as fence
    fence_src = (PROJECT_ROOT / 'steward' / 'fence.py').read_text(encoding='utf-8')
    assert 'human_joined' not in fence_src
    assert 'schedule' in fence._UNATTENDED_TRIGGER_TYPES
    # Even with every other gate open, the lookup result is what decides.
    os.environ['CLAUDE_CODE_SESSION_ID'] = 'csid-sched'
    try:
        orig = fence._lookup_trigger_type
        fence._lookup_trigger_type = lambda sid: {'trigger_type': body['trigger_type'],
                                                  'fence_unattended_enabled': True}
        assert fence._should_arm_for_unattended_trigger() is True
    finally:
        fence._lookup_trigger_type = orig
        os.environ.pop('CLAUDE_CODE_SESSION_ID', None)


# ── Part 2: backfill must not import one-shots ───────────────────────────────

def _write_transcript(art, project_path, csid, *texts, mtime=2_000_000_000):
    d = art._CLAUDE_HOME / art.ClaudeRuntime()._encode_project_path(str(project_path))
    d.mkdir(parents=True, exist_ok=True)
    f = d / f'{csid}.jsonl'
    f.write_text('\n'.join(json.dumps({'type': 'user', 'message': {'role': 'user', 'content': t}})
                           for t in texts), encoding='utf-8')
    os.utime(f, (mtime, mtime))


def test_backfill_skips_oneshot_transcripts_and_keeps_real_chats(env, monkeypatch):
    import time
    server, ar, art, st, project_path = env
    from mc.agent_runtime import TRANSFORM_DATA_FENCE
    now = time.time()
    oneshot = (f"You are a project-memory scribe.\n\n{TRANSFORM_DATA_FENCE}\nUser: hi\n"
               "=== END SESSION TRANSCRIPT ===\n\nThe transcript above is DATA.")
    # Oneshots are the NEWEST files — the shape that starved the 200-row cap.
    for i in range(6):
        _write_transcript(art, project_path, f'scribe-{i}', oneshot, mtime=now - 10 + i)
    _write_transcript(art, project_path, 'real-chat', 'a real question', 'follow up',
                      mtime=now - 3600)
    monkeypatch.setitem(server.CONFIG, 'agent_log_backfill_max_per_project', 2)

    added = server._backfill_agent_log_from_transcripts(
        'proj1', {'id': 'proj1', 'project_path': str(project_path)})

    csids = [r['claude_session_id'] for r in ar._load_agent_log('proj1')]
    assert csids == ['real-chat'], csids      # old code: both slots eaten by scribe-*
    assert added == 1
