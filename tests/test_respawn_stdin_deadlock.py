"""Regression tests for the 2026-09-23 respawn-stdin deadlock
(mc/blueprints/agent_routes.py).

py-spy on the live server showed: a respawned claude.exe hung at startup and
never read stdin, so a large stdin write (an image reference pushed it over
the pipe buffer) blocked FOREVER inside _do_respawn_b while holding
stdin_lock. The user's next message routed to agent_interrupt, which called
old_proc.stdin.close() while holding get_manager(project_id).lock — that
close() blocked too (it needs the same internal BufferedWriter lock the
stuck write already held), so every subsequent /agent/send for the project
piled up behind the held project lock. Separately, agent_send read
resp.status_code off agent_interrupt's return value, which is a plain
(Response, status) TUPLE (not a Response) on every non-200 path — an
AttributeError observed in the log during the incident.

These tests cover the three fixes:
  - _bounded_stdin_write: a stdin write that never drains is bounded and the
    hung process gets killed instead of hanging the writer thread forever.
  - _bounded_stdin_close: same bound for stdin.close().
  - agent_send normalizes agent_interrupt's/agent_followup's return value
    with flask.make_response() before reading .status_code — a stuck
    project (post-race project-not-found) now degrades to its real error
    instead of a 500.
"""
import sys
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client; agent_routes global-scope deps patched on the MODULE.
    Mirrors tests/test_agent_routes.py's `client` fixture."""
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state as mc_state
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)


# ── _bounded_stdin_write ────────────────────────────────────────────────────

class _HangingStdin:
    """Mimics a Windows pipe write that never returns because the child
    process never reads it — the exact shape of the diagnosed hang."""

    def __init__(self, write_started, release):
        self._write_started = write_started
        self._release = release

    def write(self, data):
        self._write_started.set()
        # Blocks until the test lets it go (or the process "dies" and we
        # simulate the OS unblocking the syscall — see the kill test below).
        self._release.wait(10)

    def flush(self):
        pass

    def close(self):
        pass


class _FakeProc:
    def __init__(self, stdin):
        self.pid = 999001
        self.stdin = stdin


def test_bounded_stdin_write_never_blocks_the_caller(monkeypatch):
    """The calling thread must get control back immediately even though the
    write itself never finishes — this is what keeps a stuck respawn from
    pinning get_manager(...).lock (point A of the fix)."""
    from mc.blueprints import agent_routes as ar

    write_started = threading.Event()
    release = threading.Event()
    proc = _FakeProc(_HangingStdin(write_started, release))
    monkeypatch.setattr(ar, '_kill_proc_background', lambda p: None)

    import time
    t0 = time.monotonic()
    result = ar._bounded_stdin_write(proc, 'hello\n', timeout=5.0)
    elapsed = time.monotonic() - t0

    assert result is None  # fire-and-forget path (no wait_for_ack)
    assert elapsed < 1.0, f'_bounded_stdin_write blocked the caller for {elapsed}s'
    assert write_started.wait(2), 'writer thread never started the write'
    release.set()  # let the background writer thread exit; avoid leaking it


def test_bounded_stdin_write_kills_hung_proc_and_logs(monkeypatch):
    """When the child never drains stdin, the watchdog kills it (breaking
    the pipe so the writer thread unblocks instead of leaking forever) and
    leaves a visible line in the session log — this is the mechanism that
    stops a future stdin.close() on the same proc from also hanging."""
    from mc.blueprints import agent_routes as ar

    write_started = threading.Event()
    release = threading.Event()
    proc = _FakeProc(_HangingStdin(write_started, release))

    killed = threading.Event()
    killed_proc = {}

    def _fake_kill_bg(p):
        killed_proc['proc'] = p
        killed.set()

    monkeypatch.setattr(ar, '_kill_proc_background', _fake_kill_bg)

    session = {'log_lines': []}
    ar._bounded_stdin_write(proc, 'hello\n', session=session, timeout=0.2)

    assert write_started.wait(2), 'writer thread never started the write'
    assert killed.wait(2), 'watchdog did not kill the hung process within its bound'
    assert killed_proc['proc'] is proc
    assert any('stopped responding' in line for line in session['log_lines']), (
        f"expected a visible timeout line, got: {session['log_lines']}")
    assert session.get('_stdin_write_uncertain') is True
    release.set()  # let the writer thread's blocked write() return; cleanup


def test_bounded_stdin_write_durable_ack_reports_unknown_on_timeout(monkeypatch):
    """The durable-delivery caller (agent_followup's _write_mode_b_stdin)
    must get an 'unknown' ack back within its own wait window, same as
    before this change — the watchdog-kill is an ADDITION, not a change to
    this contract."""
    from mc.blueprints import agent_routes as ar

    write_started = threading.Event()
    release = threading.Event()
    proc = _FakeProc(_HangingStdin(write_started, release))
    monkeypatch.setattr(ar, '_kill_proc_background', lambda p: None)

    session = {'log_lines': []}
    ack = ar._bounded_stdin_write(proc, 'hello\n', session=session,
                                   timeout=5.0, wait_for_ack=0.2)
    assert ack == {'ack': 'unknown',
                    'error': 'stdin write did not complete before timeout'}
    assert session.get('_stdin_write_uncertain') is True
    release.set()


def test_bounded_stdin_write_reports_success_via_ack():
    from mc.blueprints import agent_routes as ar

    class _OkStdin:
        def __init__(self):
            self.written = []

        def write(self, data):
            self.written.append(data)

        def flush(self):
            pass

    proc = _FakeProc(_OkStdin())
    session = {'log_lines': []}
    ack = ar._bounded_stdin_write(proc, 'hello\n', session=session, wait_for_ack=2.0)
    assert ack == {'ack': 'written'}
    assert proc.stdin.written == ['hello\n']


def test_bounded_stdin_write_close_after_runs_even_when_no_lock():
    """Mode A's midturn write-then-close (EOF-terminated prompt) must still
    close stdin after the write, on the same worker thread, preserving
    ordering even though the caller doesn't wait for it."""
    from mc.blueprints import agent_routes as ar

    events = []

    class _Stdin:
        def write(self, data):
            events.append(('write', data))

        def flush(self):
            pass

        def close(self):
            events.append(('close', None))

    proc = _FakeProc(_Stdin())
    done = threading.Event()

    # Piggyback on wait_for_ack purely to block the test until the worker
    # thread (which also performs close_after) has finished.
    ar._bounded_stdin_write(proc, 'hi', close_after=True, wait_for_ack=2.0)
    assert events == [('write', 'hi'), ('close', None)]


# ── _bounded_stdin_close ────────────────────────────────────────────────────

class _HangingCloseStdin:
    def __init__(self, close_started, release):
        self._close_started = close_started
        self._release = release

    def close(self):
        self._close_started.set()
        self._release.wait(10)


def test_bounded_stdin_close_never_blocks_the_caller(monkeypatch):
    """This is the exact call agent_interrupt makes while holding
    get_manager(project_id).lock (point B) — it must return immediately."""
    from mc.blueprints import agent_routes as ar

    close_started = threading.Event()
    release = threading.Event()
    proc = _FakeProc(None)
    proc.stdin = _HangingCloseStdin(close_started, release)
    monkeypatch.setattr(ar, '_kill_proc_background', lambda p: None)

    import time
    t0 = time.monotonic()
    ar._bounded_stdin_close(proc, timeout=5.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, f'_bounded_stdin_close blocked the caller for {elapsed}s'
    assert close_started.wait(2), 'closer thread never started the close'
    release.set()


def test_bounded_stdin_close_kills_hung_proc(monkeypatch):
    from mc.blueprints import agent_routes as ar

    close_started = threading.Event()
    release = threading.Event()
    proc = _FakeProc(None)
    proc.stdin = _HangingCloseStdin(close_started, release)

    killed = threading.Event()
    monkeypatch.setattr(ar, '_kill_proc_background',
                        lambda p: killed.set())

    ar._bounded_stdin_close(proc, timeout=0.2)
    assert close_started.wait(2)
    assert killed.wait(2), 'watchdog did not kill the process whose close() hung'
    release.set()


# ── agent_send: normalize agent_interrupt's tuple return (point C) ─────────

def test_agent_send_interrupt_path_normalizes_tuple_response(client, monkeypatch):
    """Reproduces the observed 500: agent_send picks decision='interrupt'
    (session status=='running'), then agent_interrupt's OWN reread of
    load_project comes back empty (the project having gone away between the
    two checks is the same race class the code comments describe for
    session status — here forced deterministically via a call-counting
    stub). agent_interrupt's `_ret()` returns (jsonify(...), 404) — a plain
    tuple — whenever `_internal` is None and status != 200.

    Before the fix, agent_send did `resp = agent_interrupt(project_id)` and
    then `resp.status_code`, which raised AttributeError('tuple' object has
    no attribute 'status_code') -> Flask 500. The fix wraps the call in
    flask.make_response(), which normalizes both the Response and the tuple
    shape, so the route now returns agent_interrupt's real error.
    """
    from mc.blueprints import agent_routes as ar
    from mc import state as mc_state

    project_id = 'stdin-deadlock-proj'
    real_load_project = ar.load_project
    calls = {'n': 0}

    def _load_project(pid):
        calls['n'] += 1
        if calls['n'] == 1:
            # agent_send's own top-of-function check — let it through so we
            # reach the interrupt decision.
            return {'project_id': project_id, 'project_path': str(PROJECT_ROOT)}
        # agent_interrupt's independent re-check — simulate the project
        # having disappeared in the window between the two lock sections.
        return None

    monkeypatch.setattr(ar, 'load_project', _load_project)

    class _AliveProc:
        def poll(self):
            return None  # still running -> _session_proc_alive() == True

    session_id = 'sess-interrupt-race'
    mc_state.agent_sessions[session_id] = {
        'project_id': project_id,
        'status': 'running',
        'log_lines': [],
        'mode': 'B',
        'proc': _AliveProc(),
    }

    resp = client.post(f'/api/project/{project_id}/agent/send',
                       json={'message': 'hi', 'session_id': session_id})

    assert resp.status_code != 500, (
        f'agent_send 500d instead of surfacing agent_interrupt\'s real error: '
        f'{resp.get_data(as_text=True)[:500]}')
    assert resp.status_code == 404
    body = resp.get_json()
    assert body.get('error') == 'project not found'
    assert body.get('route') == 'interrupt'
