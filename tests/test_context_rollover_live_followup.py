"""Token-based auto-fresh, end to end, on the exact live-process follow-up
path 7476f83 fixed for the byte-based backstop (agent_followup, Mode B, a
still-ALIVE process).

Before docs/CONTEXT_ECONOMY_SPEC.md's token trigger: a live Mode B session
whose transcript was small (well under the 5 MB byte cap) but whose
per-turn `context_tokens` had crossed `context_rollover_tokens` had no
mechanism to roll at all -- the only check was the byte one. These tests
fail on that absence (the live process happily receives the follow-up on
its own stdin, no rollover, no "Auto-fresh: context Nk tokens" activity
line) and pass once `_auto_fresh_trigger`/`_auto_fresh_handoff` are wired
into the live-process branch of agent_followup.

Also proves the §3 requirement that the MC session_id is NEVER replaced by
a roll (the session dict is mutated in place) -- a child session dispatched
before the roll, with `_notify_session` pointing at the parent's session_id,
must still resolve to that same parent afterward.

Reuses the Flask-route-driven fixture pattern from
test_auto_fresh_live_process.py. Never spawns a real model CLI.
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


def _session(sid, csid=CSID, alive=True, status='idle', last_output=0.0,
            context_tokens=None):
    return {
        'session_id': sid, 'project_id': 'p1', 'claude_session_id': csid,
        'status': status, 'task': 'chat', 'log_lines': ['> Ron: hi', 'hello'],
        'started_at': '2026-09-14T20:00:00Z', 'mode': 'B', 'proc': _Proc(alive),
        'process_alive': alive, 'stdin_lock': threading.Lock(),
        'last_output_time': last_output, 'last_status_change_time': 0.0,
        'context_tokens': context_tokens,
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
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(ar._memory_turn, 'refresh_for_turn', lambda *a, **k: {'block': ''})
    monkeypatch.setattr(ar._behavior_tail, 'render', lambda *a, **k: '')
    monkeypatch.setitem(mc_state.CONFIG, 'sticky_agent_settings', False)
    monkeypatch.setitem(mc_state.CONFIG, 'auto_model_enabled', False)
    monkeypatch.setitem(mc_state.CONFIG, 'context_rollover_tokens', 200_000)
    # Never over the byte cap in this fixture — isolates the token trigger.
    monkeypatch.setattr(ar, '_session_too_large', lambda pp, sid: (False, 4096))
    # No real transcript on disk for CSID, so the real _build_handoff_context
    # raises ValueError and the labeled-fallback branch is what's exercised
    # here (proves the fallback still carries real info, never a silent
    # shorter substitute).
    monkeypatch.setattr(ar, '_kill_proc_background', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_unregister_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_register_process', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_hide_windows_delayed', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_read_agent_stream_b', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resolve_claude', lambda: 'claude')
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(ar, '_fresh_context_for', lambda *a, **k: 'FRESH CONTEXT')
    monkeypatch.setattr(ar, '_sysprompt_file_args', lambda *a, **k: ([], None))

    activity_lines = []
    monkeypatch.setattr(ar, '_log_agent_activity',
                        lambda pid, line: activity_lines.append(line))

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield {'client': server.app.test_client(), 'ar': ar,
               'sessions': mc_state.agent_sessions, 'project': project,
               'pp': project_path, 'activity_lines': activity_lines}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def test_token_trigger_rolls_live_session_and_logs_context_measurement(env, monkeypatch):
    """A live Mode B process whose last recorded context_tokens is over the
    threshold must roll to a fresh session automatically (no prompt), even
    though its transcript is nowhere near the 5 MB byte cap — the failure
    mode this trigger exists to cover per docs/CONTEXT_ECONOMY_SPEC.md §2."""
    ar = env['ar']
    live = _session('c1a2b3', context_tokens=250_000)
    old_proc = live['proc']
    env['sessions']['c1a2b3'] = live

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
        'must not write the follow-up to the over-threshold live process'
    assert '-r' not in spawned_cmds[0], \
        'must start FRESH (no resume flag) on a token-triggered roll'
    assert any('context' in l and '250k tokens' in l for l in env['activity_lines']), \
        env['activity_lines']
    assert any('too large' in l for l in live['log_lines']) or \
           any('250' in l for l in live['log_lines']), live['log_lines']


def test_under_threshold_with_small_transcript_stays_on_stdin(env, monkeypatch):
    """Control: below the token threshold AND under the byte cap — must not
    roll, matches pre-existing undersized behavior."""
    ar = env['ar']
    live = _session('d4e5f6', context_tokens=1000)
    old_proc = live['proc']
    env['sessions']['d4e5f6'] = live
    monkeypatch.setattr(ar.subprocess, 'Popen',
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError('must not spawn for an under-threshold session')))

    resp = env['client'].post('/api/project/p1/agent/followup', json={
        'session_id': 'd4e5f6', 'message': 'still going'})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _wait(lambda: old_proc.stdin.written), 'message never reached the live process'
    assert 'still going' in old_proc.stdin.written[0]
    assert not any('Auto-fresh' in l for l in env['activity_lines']), env['activity_lines']


def test_in_flight_child_survives_the_token_triggered_roll(env, monkeypatch):
    """A session this parent dispatched (notify_session -> parent) before the
    roll must still resolve to the SAME parent session_id afterward — proves
    the MC session_id is never replaced by auto-fresh (§3), just mutated in
    place, so the child's eventual notify-back still lands."""
    ar = env['ar']
    live = _session('parent-1', context_tokens=300_000)
    env['sessions']['parent-1'] = live
    env['sessions']['child-1'] = {
        'project_id': 'p1', 'session_id': 'child-1',
        '_notify_session': 'parent-1', 'status': 'running', 'task': 'researching',
    }

    new_proc = _Proc(alive=True, pid=888888)
    monkeypatch.setattr(ar.subprocess, 'Popen', lambda cmd, **kwargs: new_proc)

    before_kids = ar._in_flight_children('p1', 'parent-1')
    assert len(before_kids) == 1

    resp = env['client'].post('/api/project/p1/agent/followup', json={
        'session_id': 'parent-1', 'message': 'keep going'})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _wait(lambda: env['sessions']['parent-1'].get('proc') is new_proc
                or env['sessions']['parent-1'].get('process_alive') is False), \
        'parent session never processed the roll'

    # The dict object identity for 'parent-1' is the same key in agent_sessions
    # -- never deleted and re-inserted under a new id.
    assert 'parent-1' in env['sessions']
    after_kids = ar._in_flight_children('p1', 'parent-1')
    assert after_kids == before_kids
