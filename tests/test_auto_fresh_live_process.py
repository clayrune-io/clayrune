"""Mode B follow-up to a LIVE process must auto-fresh when oversized too.

Bug (found 2026-09-18): `_session_too_large()` only ran on RESPAWN paths —
a dead process on followup (agent_followup ~8881), the Mode A respawn
(~9250), dispatch (~7511), and revive (~4917). A follow-up sent to a process
that was still ALIVE skipped straight to a stdin write with no size check at
all, so a long-running Mode B chat could sail past `_SESSION_SIZE_LIMIT` and
auto-fresh would never fire for it — the exact silent-failure this repo's
activity log already showed (0 "Auto-fresh" lines ever logged for
mission_control, despite a 20 MB transcript sitting well over the 5 MB cap).

Fix: agent_followup() now checks `_session_too_large()` for a live process
too, right after the liveness poll and before the process-alive/dead branch
split. When it trips, the process is treated as needing a respawn — the
existing dead-process auto-fresh handoff (kill the old process, drop `-r`,
start fresh, no prompt) takes over unchanged.

Reuses the Flask-route-driven fixture pattern from
test_one_process_per_conversation.py (subprocess.Popen patched so the test
controls exactly what "spawning a process" does).
"""
import sys
import threading
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CSID = '3f6c790f-9ec2-4ded-9793-970a6a2f340b'


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


def _session(sid, csid=CSID, alive=True, status='idle', last_output=0.0):
    return {
        'session_id': sid, 'project_id': 'p1', 'claude_session_id': csid,
        'status': status, 'task': 'chat', 'log_lines': ['> Ron: hi', 'hello'],
        'started_at': '2026-09-14T20:00:00Z', 'mode': 'B', 'proc': _Proc(alive),
        'process_alive': alive, 'stdin_lock': threading.Lock(),
        'last_output_time': last_output, 'last_status_change_time': 0.0,
    }


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
    # Respawn plumbing: keep the real command-building path (pure, no I/O),
    # but stub the actually-spawns-a-process / OS-touching pieces so the test
    # controls what "a fresh process" looks like.
    monkeypatch.setattr(ar, '_kill_proc_background', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_unregister_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_hide_windows_delayed', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_read_agent_stream_b', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(ar, '_fresh_context_for', lambda *a, **k: 'FRESH CONTEXT')
    monkeypatch.setattr(ar, '_sysprompt_file_args', lambda *a, **k: ([], None))

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


def test_live_oversized_session_ends_process_and_starts_fresh(env, monkeypatch):
    """A follow-up to a still-ALIVE Mode B process whose transcript already
    exceeds the size limit must not reach that process's stdin — it must end
    the process and spawn a brand-new, non-resumed one automatically."""
    ar = env['ar']
    live = _session('c1a2b3')
    old_proc = live['proc']
    env['sessions']['c1a2b3'] = live

    monkeypatch.setattr(ar, '_session_too_large',
                         lambda pp, sid: (True, 6 * 1024 * 1024))

    new_proc = _Proc(alive=True, pid=999999)
    spawned_cmds = []

    def _popen(cmd, **kwargs):
        spawned_cmds.append(cmd)
        return new_proc

    monkeypatch.setattr(ar.subprocess, 'Popen', _popen)

    resp = env['client'].post('/api/project/p1/agent/followup', json={
        'session_id': 'c1a2b3', 'message': 'keep going'})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _wait(lambda: bool(spawned_cmds)), 'no fresh process was ever spawned'
    assert old_proc.stdin.written == [], \
        'must not write the follow-up to the oversized live process'
    assert '-r' not in spawned_cmds[0], \
        'must start FRESH (no resume flag), not -r the oversized transcript'
    assert _wait(lambda: bool(new_proc.stdin.written)), \
        'the fresh process never received the follow-up message'
    assert any('too large' in l for l in live['log_lines']), live['log_lines']


def test_live_undersized_session_writes_straight_to_stdin(env, monkeypatch):
    """Control: a live process under the size limit is unaffected — the
    message still goes straight to its own stdin, no respawn."""
    ar = env['ar']
    live = _session('d4e5f6')
    old_proc = live['proc']
    env['sessions']['d4e5f6'] = live

    monkeypatch.setattr(ar, '_session_too_large', lambda pp, sid: (False, 1024))
    monkeypatch.setattr(ar.subprocess, 'Popen',
                         lambda *a, **k: (_ for _ in ()).throw(
                             AssertionError('must not spawn a process for an undersized session')))

    resp = env['client'].post('/api/project/p1/agent/followup', json={
        'session_id': 'd4e5f6', 'message': 'still going'})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _wait(lambda: old_proc.stdin.written), 'message never reached the live process'
    assert 'still going' in old_proc.stdin.written[0]
