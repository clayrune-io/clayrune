"""Regression for the 2026-09-27 double-roll incident (diagnosed by Dave,
data/logs/clayrune.log ~line 2328009-2328069).

Session e01660c51470 hit the 200k auto-fresh trigger on a send at 12:41:26
against native id OLD_SID. The fresh CLI process emitted init session id
NEW_SID a moment later, but the user hit Stop 1s after that — before the new
process produced a single turn, so NEW_SID never got its own transcript file.

The next send (12:42:00) rolled AGAIN: the old code left `context_tokens` at
OLD_SID's stale 200297 figure, so the token trigger fired a second time
against NEW_SID, and `_build_handoff_context(NEW_SID)` raised (no transcript)
— the agent woke up with no prior conversation at all, despite OLD_SID's real
2 MB transcript sitting right there.

Fix: `_mark_context_rolled` resets `context_tokens` to None on every roll and
remembers `_rolled_from` (the last native id that DOES have a transcript);
`_fragile_resume_target` catches the "plain -r on a transcript-less id" case
even when the token trigger stays quiet; `_auto_fresh_handoff` retries the
real handoff against `_rolled_from` before giving up.

Reuses the Flask-route-driven fixture from test_auto_fresh_live_process.py.
"""
import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

OLD_SID = '2ae02f0a-d457-4b69-bd58-57b46ad404fe'
NEW_SID = '6b3ab9a2-2a9c-4942-baf7-530073c2702a'


class _Stdin:
    def __init__(self):
        self.written = []

    def write(self, s):
        self.written.append(s)

    def flush(self):
        pass

    def close(self):
        pass


class _Proc:
    def __init__(self, alive=True, pid=424242):
        self.alive = alive
        self.pid = pid
        self.stdin = _Stdin()

    def poll(self):
        return None if self.alive else 0


def _wait(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return pred()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.delegation_delivery import DeliveryStore
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    project_path = tmp_path / 'proj'
    project_path.mkdir()
    project = {'id': 'p1', 'project_path': str(project_path), 'provider': 'claude'}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(tmp_path / 'delegation.db'))
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [])
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(ar._memory_turn, 'refresh_for_turn', lambda *a, **k: {'block': ''})
    monkeypatch.setattr(ar._behavior_tail, 'render', lambda *a, **k: '')
    monkeypatch.setitem(mc_state.CONFIG, 'sticky_agent_settings', False)
    monkeypatch.setitem(mc_state.CONFIG, 'auto_model_enabled', False)
    monkeypatch.setattr(ar, '_kill_proc_background', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_unregister_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_hide_windows_delayed', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_read_agent_stream_b', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(ar, '_fresh_context_for', lambda *a, **k: 'FRESH CONTEXT')
    monkeypatch.setattr(ar, '_sysprompt_file_args', lambda *a, **k: ([], None))
    # Byte backstop stays quiet throughout — isolates the token/interrupted
    # trigger paths this test is actually exercising.
    monkeypatch.setattr(ar, '_session_too_large', lambda pp, sid: (False, 0))

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield {'client': server.app.test_client(), 'ar': ar,
               'sessions': mc_state.agent_sessions, 'project': project,
               'pp': project_path}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def _session(sid, csid, alive, context_tokens):
    return {
        'session_id': sid, 'project_id': 'p1', 'claude_session_id': csid,
        'status': 'idle', 'task': 'chat', 'log_lines': ['> Ron: hi', 'hello'],
        'started_at': '2026-09-27T12:00:00Z', 'mode': 'B',
        'proc': _Proc(alive), 'process_alive': alive,
        'stdin_lock': threading.Lock(), 'last_output_time': 0.0,
        'last_status_change_time': 0.0, 'context_tokens': context_tokens,
    }


def _handoff_stub(pp, provider, native_id, project_id=''):
    """Only OLD_SID has a real transcript; NEW_SID (stopped before its first
    turn) does not."""
    if native_id == OLD_SID:
        return 'REAL HANDOFF FROM OLD_SID', {
            'owning_provider': 'claude', 'total_turns': 5,
            'included_turns': 5, 'omitted_turns': 0}
    raise ValueError(
        "cannot hand off from 'claude': no transcript is available for this "
        "conversation")


def test_interrupted_roll_carries_the_original_handoff_not_a_second_roll(env, monkeypatch):
    """The exact incident, replayed as two sequential /followup sends."""
    ar = env['ar']
    monkeypatch.setattr(ar, '_build_handoff_context', _handoff_stub)

    # ── Send 1: session is over the token threshold against OLD_SID ────────
    live = _session('e01660c51470', OLD_SID, alive=False, context_tokens=200297)
    env['sessions']['e01660c51470'] = live

    new_proc = _Proc(alive=False, pid=999991)  # already "stopped" — no turn ran
    spawned_cmds = []

    def _popen(cmd, **kwargs):
        spawned_cmds.append(cmd)
        return new_proc

    monkeypatch.setattr(ar.subprocess, 'Popen', _popen)

    resp = env['client'].post('/api/project/p1/agent/followup', json={
        'session_id': 'e01660c51470', 'message': 'first message'})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _wait(lambda: bool(spawned_cmds)), 'no fresh process spawned on send 1'
    assert '-r' not in spawned_cmds[0], 'first roll must not resume the oversized session'
    assert _wait(lambda: bool(new_proc.stdin.written)), 'fresh process never got the message'
    assert 'REAL HANDOFF FROM OLD_SID' in new_proc.stdin.written[0]

    # `_mark_context_rolled` must have reset the stale figure and remembered
    # where the real transcript lives — this is fix (1).
    assert live['context_tokens'] is None, \
        'stale context_tokens must be reset on roll, or a later send re-triggers on it'
    assert live['_rolled_from'] == OLD_SID

    # ── Simulate the interrupted roll: the new CLI emits its init event
    # (session_id known) then is Stopped before writing any transcript, so
    # context_tokens is never set and the process ends up dead again. ───────
    live['claude_session_id'] = NEW_SID
    live['process_alive'] = False
    live['proc'] = _Proc(alive=False, pid=999992)

    # ── Send 2: must NOT roll a second time against NEW_SID's stale figure —
    # there is none now — but a bare `-r NEW_SID` would still lose everything,
    # since NEW_SID has no transcript of its own. ───────────────────────────
    spawned_cmds.clear()
    newer_proc = _Proc(alive=True, pid=999993)
    monkeypatch.setattr(ar.subprocess, 'Popen', lambda cmd, **kw: (
        spawned_cmds.append(cmd), newer_proc)[1])

    resp2 = env['client'].post('/api/project/p1/agent/followup', json={
        'session_id': 'e01660c51470', 'message': 'second message'})
    assert resp2.status_code == 200, resp2.get_data(as_text=True)
    assert _wait(lambda: bool(spawned_cmds)), 'no fresh process spawned on send 2'
    assert '-r' not in spawned_cmds[0], \
        'must not blindly resume NEW_SID — it has no transcript to resume from'
    assert _wait(lambda: bool(newer_proc.stdin.written)), 'fresh process never got the message'
    sent = newer_proc.stdin.written[0]
    assert 'REAL HANDOFF FROM OLD_SID' in sent, (
        'the carried handoff must still be the ORIGINAL transcript (OLD_SID), '
        f'not a blank fresh start: {sent!r}')
    assert any('recovering the real handoff' in l for l in live['log_lines']), live['log_lines']


def test_a_healthy_resume_is_unaffected(env, monkeypatch):
    """Control: a normal dead-process respawn with a real transcript and no
    prior roll still takes the plain -r path, unchanged."""
    ar = env['ar']
    monkeypatch.setattr(ar, '_build_handoff_context', _handoff_stub)
    monkeypatch.setattr(ar, '_fragile_resume_target', lambda *a, **k: False)

    live = _session('c9d8e7', OLD_SID, alive=False, context_tokens=50000)
    live['_resume_id'] = OLD_SID
    live['_resume_confirmed'] = True
    env['sessions']['c9d8e7'] = live

    new_proc = _Proc(alive=True, pid=888881)
    spawned_cmds = []
    monkeypatch.setattr(ar.subprocess, 'Popen', lambda cmd, **kw: (
        spawned_cmds.append(cmd), new_proc)[1])

    resp = env['client'].post('/api/project/p1/agent/followup', json={
        'session_id': 'c9d8e7', 'message': 'keep going'})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _wait(lambda: bool(spawned_cmds))
    assert '-r' in spawned_cmds[0] and OLD_SID in spawned_cmds[0]
    assert live.get('_rolled_from') is None
