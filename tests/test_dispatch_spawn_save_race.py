"""MC-959 — dispatch must never report an error once the child is spawned.

Reproduced live 2026-09-18: `POST /agent/dispatch` returned
`{"error": "dispatch failed: [WinError 5] Access is denied: ...
data/projects/.mission_control.json.<tmp> -> ..."}`. The atomic rename of the
project record (`save_project` -> `mc.atomic_json.write_json_atomic`) hit a
transient Windows file lock (AV/indexer). The caller read the error and
retried — but `_dispatch_agent_internal` had ALREADY Popen'd the child and
registered it in `agent_sessions` *before* the failing call
(`_log_agent_activity`, which persists the "Agent dispatched" activity-log
line via `save_project`). The retry spawned a SECOND agent for the same task;
two builders did the same fix on two branches, doubling spend.

Order of events inside `_dispatch_agent_internal` (mc/blueprints/
agent_routes.py), Mode A shown, Mode B is the same shape:
  1. `subprocess.Popen(cmd, ...)`                              (~8962)
  2. `agent_sessions[session_id] = session`                    (~9038)
  3. reader thread started                                     (~9042-9043)
  4. `_log_agent_activity(project_id, "Agent dispatched...")`  (~9060, was
     unguarded) -> `save_project` -> `write_json_atomic` -> `os.replace`
  5. `return session_id`

Step 4 running after steps 1-3 is the bug: by the time it can fail, the
child is already alive and running the real task, so failing the whole
dispatch call is a lie -- the work already started. The fix wraps step 4 in
its own try/except (agent_routes.py) so a failure there logs a warning and
falls through to the normal `return session_id` / `{"ok": true, ...}"`
response, never the route's generic `except Exception -> 500` handler.

The companion retry (mc.atomic_json._replace_with_retry) reduces how often
step 4 fails at all; this file pins the other half -- that IF it still fails,
dispatch does not lie about it. See test_atomic_json.py for the retry itself.
"""
import io
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _Stdin:
    def write(self, s):
        pass

    def flush(self):
        pass

    def close(self):
        pass


class _FakeProc:
    """Stands in for subprocess.Popen: alive, no output, never exits on its
    own (the test controls the lifetime by never calling anything that would
    tear it down)."""

    def __init__(self, pid=909090):
        self.pid = pid
        self.stdin = _Stdin()
        self.stdout = io.StringIO('')  # `for line in proc.stdout` ends immediately
        self._alive = True

    def poll(self):
        return None if self._alive else 0


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint + runs wire())
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la
    from mc.delegation_delivery import DeliveryStore

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    project_path = tmp_path / 'proj'
    project_path.mkdir()
    project = {'id': 'p1', 'project_path': str(project_path), 'provider': 'claude'}

    monkeypatch.setattr(ar, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(tmp_path / 'delegation.db'))
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [])
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(ar._memory_turn, 'refresh_for_turn', lambda *a, **k: {'block': ''})
    monkeypatch.setattr(ar._memory_turn, 'seed_delivered', lambda *a, **k: None)
    monkeypatch.setattr(ar._behavior_tail, 'render', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_prior_character', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resolve_character', lambda *a, **k: (None, ''))
    monkeypatch.setattr(ar, '_session_too_large', lambda *a, **k: (False, 0))
    monkeypatch.setattr(ar, '_maybe_isolate_worktree', lambda *a, **k: (str(project_path), False))
    monkeypatch.setattr(ar, '_check_context_budget', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: 'context')
    monkeypatch.setattr(ar, '_dispatch_with_routing_parallel',
                        lambda p, task, context_builder=None, **k: (
                            'sonnet', 'auto', [], 'context', ''))
    monkeypatch.setitem(mc_state.CONFIG, 'sticky_agent_settings', False)
    monkeypatch.setitem(mc_state.CONFIG, 'auto_model_enabled', False)
    monkeypatch.setitem(mc_state.CONFIG, 'use_streaming_agent', False)  # Mode A: simpler fake proc

    spawned = []

    def fake_popen(cmd, **kwargs):
        proc = _FakeProc()
        spawned.append(proc)
        return proc

    monkeypatch.setattr(ar.subprocess, 'Popen', fake_popen)

    class _InertThread:
        """Reader/writer threads are launched as real daemon threads against
        the fake proc; never running their target avoids a background thread
        touching test state after the assertions run."""
        def __init__(self, *a, **k):
            pass

        def start(self):
            pass

        def is_alive(self):
            # ensure_guardian() (mc.state's per-project manager persists
            # across tests in this process) checks this on the thread it
            # stashed from a PRIOR test's dispatch -- True short-circuits it
            # into a no-op instead of starting a second guardian.
            return True

    monkeypatch.setattr(ar.threading, 'Thread', _InertThread)

    snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    server.app.config['TESTING'] = True
    try:
        yield {'client': server.app.test_client(), 'ar': ar,
               'sessions': mc_state.agent_sessions, 'project': project,
               'spawned': spawned}
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snapshot)


def test_dispatch_survives_activity_log_save_failure(env, monkeypatch):
    """The exact MC-959 shape: the post-spawn project-record save (inside
    `_log_agent_activity`) raises the same way a WinError 5/32 mid-rename
    would. Dispatch must still report success -- the child is already live,
    so an error response here would only be a lie that invites a harmful
    retry."""
    ar = env['ar']

    def failing_activity_log(*a, **k):
        raise OSError('[WinError 5] Access is denied: '
                       "'data/projects/.mission_control.json.abc123.tmp999'")

    monkeypatch.setattr(ar, '_log_agent_activity', failing_activity_log)

    resp = env['client'].post('/api/project/p1/agent/dispatch',
                              json={'task': 'do the thing'})

    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body.get('ok') is True
    assert body.get('error') is None
    session_id = body['session_id']

    # The child from step 1-3 is real and tracked -- this is exactly the
    # state a "dispatch failed" response would have hidden from the caller.
    assert session_id in env['sessions']
    assert env['sessions'][session_id]['status'] == 'running'
    assert len(env['spawned']) == 1, 'a caller retry after a false error would spawn a second child'


def test_dispatch_internal_returns_session_id_despite_the_failure(env, monkeypatch):
    """Same fault, at the unit level: `_dispatch_agent_internal` itself must
    return the session_id (not raise) when the post-spawn activity log
    write fails."""
    ar = env['ar']
    monkeypatch.setattr(ar, '_log_agent_activity',
                        lambda *a, **k: (_ for _ in ()).throw(OSError('locked')))

    session_id = ar._dispatch_agent_internal('p1', 'do the thing')

    assert session_id in env['sessions']
    assert len(env['spawned']) == 1


def test_dispatch_still_succeeds_when_the_activity_log_write_works(env):
    """Control: the ordinary path (no injected failure) still returns
    ok:true with exactly one spawned child -- the fix must not swallow a
    save that actually succeeds, nor spawn extra children on its own."""
    resp = env['client'].post('/api/project/p1/agent/dispatch',
                              json={'task': 'do the thing'})

    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body.get('ok') is True
    assert len(env['spawned']) == 1
