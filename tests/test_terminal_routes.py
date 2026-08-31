"""Request-level tests for the terminal family (mc/blueprints/terminal_routes.py).

Added with blueprint step 1.8 (MODERNIZATION_PLAN.md Phase 5 — terminal is the
RCE-adjacent priority): happy path, auth-rejected path, malformed-input path.

Auth contract pinned here (plan 1.8 acceptance "re-verify the 403 behavior"):
/api/terminal/launch has NO route-private gate — the protection is the
app-wide local_auth_gate (mc/blueprints/local_auth.py). Loopback is exempt;
a non-loopback peer with no passcode cookie is rejected BEFORE the handler
runs with **401 auth_required** (the plan note's "403" is imprecise: 403 only
exists in the passcode-login flow itself). The reject test additionally
proves no subprocess spawn and no session creation happened.

Determinism: no real child processes. subprocess is replaced ON THE BLUEPRINT
MODULE (the Phase-0 test-port rule: patch mc.blueprints.terminal_routes.*,
never server.*) with a recorder whose FakeProc carries a real OS pipe, so the
verbatim-moved _read_terminal_stream reader thread is exercised end-to-end
(EOF → wait() → unregister → status flip). Shared mc.state dicts are
snapshot/restored around every test.
"""
import io
import os
import sys
import time
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}


class FakeProc:
    """Popen stand-in: real pipe for the reader thread, BytesIO stdin."""
    _next_pid = 994000

    def __init__(self):
        r, w = os.pipe()
        self.stdout = os.fdopen(r, 'rb')
        self._w = w
        self.stdin = io.BytesIO()
        FakeProc._next_pid += 1
        self.pid = FakeProc._next_pid
        self._rc = None

    # — test controls —
    def feed(self, data: bytes):
        os.write(self._w, data)

    def eof(self, rc: int = 0):
        self._exit(rc)

    def _exit(self, rc: int):
        if self._rc is None:
            self._rc = rc
            try:
                os.close(self._w)
            except OSError:
                pass

    # — Popen API used by the blueprint + system_routes —
    def poll(self):
        return self._rc

    def wait(self, timeout=None):
        # The reader thread calls wait() after EOF; rc is set by then.
        return self._rc if self._rc is not None else 0

    def kill(self):
        self._exit(-9)


class FakePtyUnavailable(RuntimeError):
    pass


class FakePty:
    """pty_backend session stand-in: real OS pipe for the reader thread
    (same trick as FakeProc), plus the narrow interface terminal_routes.py
    actually drives — no real ConPTY/pty.openpty() involved."""
    _next_pid = 995000

    def __init__(self):
        r, w = os.pipe()
        self._r_fd = r
        self._w = w
        self.written = []
        self.resized = None
        self.closed_with = None
        FakePty._next_pid += 1
        self.pid = FakePty._next_pid
        self._rc = None

    def read(self, size=4096):
        try:
            data = os.read(self._r_fd, size)
        except OSError:
            return ''
        return data.decode('utf-8', errors='replace')

    def write(self, text):
        self.written.append(text)

    def resize(self, cols, rows):
        self.resized = (cols, rows)

    def isalive(self):
        return self._rc is None

    def poll(self):
        return self._rc

    def kill(self):
        self.close(force=True)

    def wait(self, timeout=None):
        return self._rc if self._rc is not None else 0

    def close(self, force=True):
        self.closed_with = force
        self._exit(-9 if force else 0)

    # — test controls —
    def feed(self, text: str):
        os.write(self._w, text.encode('utf-8'))

    def eof(self, rc: int = 0):
        self._exit(rc)

    def _exit(self, rc):
        if self._rc is None:
            self._rc = rc
            try:
                os.close(self._w)
            except OSError:
                pass


def _fake_pty_backend(available=True):
    """A pty_backend-shaped namespace (pty_available/spawn/PtyUnavailable)
    for monkeypatching tr.pty_backend — no real ConPTY/stdlib pty involved,
    so these tests run identically on Windows and POSIX CI."""
    spawned = []

    def _spawn(command, cwd=None, env=None, cols=120, rows=30):
        if not available:
            raise FakePtyUnavailable('no pty backend installed')
        p = FakePty()
        spawned.append((command, cwd, env, cols, rows, p))
        return p

    ns = types.SimpleNamespace(
        pty_available=lambda: available,
        spawn=_spawn,
        PtyUnavailable=FakePtyUnavailable,
    )
    ns.spawned = spawned
    return ns


@pytest.fixture()
def state():
    """Run against EMPTY shared mc.state dicts; restore prior contents after.

    The clear-at-entry matters in the full suite: test_pid_reaper leaks a
    tracked_processes entry whose proc is a bare object() (no .poll), which
    500s /api/processes for every later caller. Snapshot/restore leaves other
    suites exactly the state they had.
    """
    from mc import state as st
    before_terms = dict(st.terminal_sessions)
    before_procs = dict(st.tracked_processes)
    st.terminal_sessions.clear()
    st.tracked_processes.clear()
    yield st
    st.terminal_sessions.clear()
    st.terminal_sessions.update(before_terms)
    st.tracked_processes.clear()
    st.tracked_processes.update(before_procs)


@pytest.fixture()
def client(tmp_path, monkeypatch, state):
    """Flask test client; terminal blueprint deps patched on the MODULE."""
    import server
    from mc.blueprints import local_auth as la
    from mc.blueprints import terminal_routes as tr

    # Deterministic gate state: no LAN passcode configured on this run.
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    # Project registry stub (projects family stays in server.py until 1.11).
    proj = {'id': 'tterm', 'name': 'Terminal Test', 'project_path': str(tmp_path)}
    monkeypatch.setattr(tr, 'load_project',
                        lambda pid: proj if pid == 'tterm' else None)

    # Recorder subprocess namespace — launch must not spawn real children.
    calls = []

    def _popen(*a, **kw):
        calls.append((a, kw))
        return FakeProc()

    monkeypatch.setattr(tr, 'subprocess', types.SimpleNamespace(
        Popen=_popen, PIPE=-1, STDOUT=-2))

    # Default: a real-PTY backend IS available, spawning FakePty instances —
    # individual pty tests override with _fake_pty_backend(available=False).
    fake_pty = _fake_pty_backend(available=True)
    monkeypatch.setattr(tr, 'pty_backend', fake_pty)

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c._popen_calls = calls  # type: ignore[attr-defined]
    c._fake_pty = fake_pty  # type: ignore[attr-defined]
    return c


def _wait_until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def _seed_session(state, sid='seeded123456', status='running', proc=None):
    s = {
        'proc': proc if proc is not None else FakeProc(),
        'status': status,
        'command': 'echo seeded',
        'output_lines': [],
        'started_at': '2026-06-10T00:00:00Z',
        'session_id': sid,
        'project_id': 'tterm',
        'exit_code': None,
    }
    state.terminal_sessions[sid] = s
    return s


class TestLaunchHappyPath:
    def test_launch_streams_and_tracks_process(self, client, state):
        r = client.post('/api/terminal/launch',
                        json={'project_id': 'tterm', 'command': 'echo hi'})
        assert r.status_code == 200
        j = r.get_json()
        assert j['ok'] is True
        sid = j['session_id']
        assert len(sid) == 12

        session = state.terminal_sessions[sid]
        assert session['status'] == 'running'
        proc = session['proc']

        # TTY-shim env wiring reached Popen (mc_tty_shim on PYTHONPATH).
        (args, kw) = client._popen_calls[0]
        assert args[0] == 'echo hi'
        assert kw['shell'] is True
        assert 'mc_tty_shim' in kw['env']['PYTHONPATH']
        assert kw['env']['MC_FORCE_TTY'] == '1'

        # Cross-blueprint integration: the launch shows up in /api/processes
        # (system_routes reads the shared mc.state tracker).
        procs = client.get('/api/processes').get_json()
        mine = [p for p in procs if p['session_id'] == sid]
        assert mine and mine[0]['type'] == 'terminal'
        assert mine[0]['alive'] is True
        # project_name resolves via server.py's _register_process → its own
        # load_project (dispatch family, NOT the patched blueprint stub) →
        # unknown id falls back to the raw project_id.
        assert mine[0]['project_name'] == 'tterm'

        # Reader thread captures output, then EOF completes the session and
        # unregisters the pid (the verbatim-moved _read_terminal_stream).
        proc.feed(b'hello-from-child')
        assert _wait_until(lambda: any('hello-from-child' in l
                                       for l in session['output_lines']))
        proc.eof(rc=0)
        assert _wait_until(lambda: session['status'] == 'completed')
        assert session['exit_code'] == 0
        assert _wait_until(lambda: proc.pid not in state.tracked_processes)

    def test_launch_unknown_project_404(self, client, state):
        r = client.post('/api/terminal/launch',
                        json={'project_id': 'nope', 'command': 'echo hi'})
        assert r.status_code == 404
        assert r.get_json()['error'] == 'project not found'
        assert client._popen_calls == []


class TestLaunchAuthReject:
    def test_non_loopback_rejected_before_handler(self, client, state):
        r = client.post('/api/terminal/launch',
                        json={'project_id': 'tterm', 'command': 'echo pwned'},
                        environ_base=LAN)
        # The local_auth gate fires first: 401 auth_required (locked — no
        # passcode configured). NOT 200, and no side effects at all.
        assert r.status_code == 401
        assert r.get_json() == {'error': 'auth_required', 'auth_state': 'locked'}
        assert client._popen_calls == []
        assert not any(s.get('command') == 'echo pwned'
                       for s in state.terminal_sessions.values())

    def test_loopback_is_exempt_same_payload(self, client, state):
        # Identical request from loopback (test-client default) passes the
        # gate — proving the reject above was the gate, not the handler.
        r = client.post('/api/terminal/launch',
                        json={'project_id': 'tterm', 'command': 'echo fine'})
        assert r.status_code == 200
        state.terminal_sessions[r.get_json()['session_id']]['proc'].eof()


class TestLaunchMalformed:
    @pytest.mark.parametrize('payload', [
        {},
        {'project_id': 'tterm'},
        {'command': 'echo hi'},
        {'project_id': '  ', 'command': 'echo hi'},
        {'project_id': 'tterm', 'command': '   '},
    ])
    def test_missing_fields_400(self, client, payload):
        r = client.post('/api/terminal/launch', json=payload)
        assert r.status_code == 400
        assert r.get_json()['error'] == 'project_id and command required'
        assert client._popen_calls == []


class TestStdin:
    def test_stdin_roundtrip(self, client, state):
        s = _seed_session(state)
        r = client.post('/api/terminal/stdin',
                        json={'session_id': s['session_id'], 'text': 'ls\n'})
        assert r.status_code == 200 and r.get_json() == {'ok': True}
        assert s['proc'].stdin.getvalue() == b'ls\n'

    def test_stdin_malformed_and_not_running(self, client, state):
        r = client.post('/api/terminal/stdin', json={'text': 'x'})
        assert r.status_code == 400
        assert r.get_json()['error'] == 'session_id required'
        s = _seed_session(state, sid='stoppedsess1', status='stopped')
        r = client.post('/api/terminal/stdin',
                        json={'session_id': s['session_id'], 'text': 'x'})
        assert r.status_code == 400
        assert r.get_json()['error'] == 'session not running'


class TestStopDeleteStatus:
    def test_stop_kills_and_marks(self, client, state):
        s = _seed_session(state)
        r = client.post('/api/terminal/stop', json={'session_id': s['session_id']})
        assert r.status_code == 200 and r.get_json() == {'ok': True}
        assert s['status'] == 'stopped'
        assert s['proc'].poll() == -9  # FakeProc.kill() was reached
        assert any('stopped by user' in l for l in s['output_lines'])

    def test_stop_missing_404_not_running_400_malformed_400(self, client, state):
        r = client.post('/api/terminal/stop', json={'session_id': 'ghost'})
        assert r.status_code == 404
        s = _seed_session(state, sid='donesession1', status='completed')
        r = client.post('/api/terminal/stop', json={'session_id': s['session_id']})
        assert r.status_code == 400 and r.get_json()['error'] == 'not running'
        r = client.post('/api/terminal/stop', json={})
        assert r.status_code == 400 and r.get_json()['error'] == 'session_id required'

    def test_delete_running_and_idempotent(self, client, state):
        s = _seed_session(state)
        sid = s['session_id']
        r = client.post('/api/terminal/delete', json={'session_id': sid})
        assert r.status_code == 200 and r.get_json() == {'ok': True}
        assert sid not in state.terminal_sessions
        assert s['proc'].poll() == -9
        # Already gone → still ok (idempotent by contract).
        r = client.post('/api/terminal/delete', json={'session_id': sid})
        assert r.status_code == 200 and r.get_json() == {'ok': True}
        r = client.post('/api/terminal/delete', json={})
        assert r.status_code == 400

    def test_project_status_lists_running_purges_rest(self, client, state):
        run = _seed_session(state, sid='runningsess1')
        _seed_session(state, sid='deadsession1', status='completed')
        r = client.get('/api/project/tterm/terminal/status')
        assert r.status_code == 200
        sessions = r.get_json()['sessions']
        assert [s['session_id'] for s in sessions] == [run['session_id']]
        assert 'deadsession1' not in state.terminal_sessions  # purged


class TestLaunchPty:
    """MC-928: {"pty": true} routes through pty_backend instead of
    subprocess+shim. Same reader-thread/EOF/teardown contract as the pipe
    path (TestLaunchHappyPath above), proven against a fake backend so these
    run on any CI platform without a real ConPTY/pty.openpty()."""

    def test_launch_pty_streams_and_tracks_process(self, client, state):
        r = client.post('/api/terminal/launch',
                        json={'project_id': 'tterm', 'command': 'gemini', 'pty': True})
        assert r.status_code == 200
        j = r.get_json()
        assert j == {'ok': True, 'session_id': j['session_id'], 'is_pty': True}
        sid = j['session_id']
        assert client._popen_calls == []  # never touched the pipe/shim path

        session = state.terminal_sessions[sid]
        assert session['status'] == 'running'
        assert session['is_pty'] is True
        assert session['proc'] is None
        pty = session['pty']

        (command, cwd, env, cols, rows, spawned) = client._fake_pty.spawned[0]
        assert command == 'gemini' and spawned is pty
        # No TTY-shim PYTHONPATH injection for a real PTY — it isn't needed.
        assert 'PYTHONPATH' not in env or 'mc_tty_shim' not in env.get('PYTHONPATH', '')

        procs = client.get('/api/processes').get_json()
        mine = [p for p in procs if p['session_id'] == sid]
        assert mine and mine[0]['type'] == 'terminal' and mine[0]['alive'] is True

        pty.feed('drawing an Ink TUI\x1b[2J')
        assert _wait_until(lambda: any('Ink TUI' in l for l in session['output_lines']))
        pty.eof(rc=0)
        assert _wait_until(lambda: session['status'] == 'completed')
        assert session['exit_code'] == 0
        assert _wait_until(lambda: pty.pid not in state.tracked_processes)

    def test_launch_pty_unavailable_400(self, client, state, monkeypatch):
        from mc.blueprints import terminal_routes as tr
        monkeypatch.setattr(tr, 'pty_backend', _fake_pty_backend(available=False))
        r = client.post('/api/terminal/launch',
                        json={'project_id': 'tterm', 'command': 'gemini', 'pty': True})
        assert r.status_code == 400
        assert 'pywinpty' in r.get_json()['error']
        assert state.terminal_sessions == {}

    def test_launch_pty_spawn_failure_400(self, client, state, monkeypatch):
        from mc.blueprints import terminal_routes as tr

        def _boom(*a, **kw):
            raise OSError('gemini.CMD not found')
        monkeypatch.setattr(tr.pty_backend, 'spawn', _boom)
        r = client.post('/api/terminal/launch',
                        json={'project_id': 'tterm', 'command': 'gemini', 'pty': True})
        assert r.status_code == 400
        assert 'gemini.CMD not found' in r.get_json()['error']
        assert state.terminal_sessions == {}


def _seed_pty_session(state, client, sid='ptyseeded1234', status='running'):
    pty = FakePty()
    client._fake_pty.spawned.append(('seed', None, {}, 120, 30, pty))
    s = {
        'pty': pty,
        'proc': None,
        'status': status,
        'command': 'gemini',
        'output_lines': [],
        'started_at': '2026-08-31T00:00:00Z',
        'session_id': sid,
        'project_id': 'tterm',
        'exit_code': None,
        'is_pty': True,
    }
    state.terminal_sessions[sid] = s
    return s


class TestPtyStdinResizeStop:
    def test_stdin_writes_raw_to_pty_not_proc(self, client, state):
        s = _seed_pty_session(state, client)
        r = client.post('/api/terminal/stdin',
                        json={'session_id': s['session_id'], 'text': '\x1b[B'})
        assert r.status_code == 200 and r.get_json() == {'ok': True}
        assert s['pty'].written == ['\x1b[B']

    def test_resize_happy_path(self, client, state):
        s = _seed_pty_session(state, client)
        r = client.post('/api/terminal/resize',
                        json={'session_id': s['session_id'], 'cols': 100, 'rows': 40})
        assert r.status_code == 200 and r.get_json() == {'ok': True}
        assert s['pty'].resized == (100, 40)

    def test_resize_rejects_pipe_session(self, client, state):
        s = _seed_session(state)  # pipe-backed FakeProc, no 'pty' key
        r = client.post('/api/terminal/resize',
                        json={'session_id': s['session_id'], 'cols': 100, 'rows': 40})
        assert r.status_code == 400
        assert r.get_json()['error'] == 'not a pty session'

    def test_resize_missing_fields_400(self, client, state):
        s = _seed_pty_session(state, client)
        r = client.post('/api/terminal/resize', json={'session_id': s['session_id']})
        assert r.status_code == 400

    def test_stop_closes_pty_and_unregisters(self, client, state):
        s = _seed_pty_session(state, client)
        pid = s['pty'].pid
        state.tracked_processes[pid] = {'pid': pid}
        r = client.post('/api/terminal/stop', json={'session_id': s['session_id']})
        assert r.status_code == 200 and r.get_json() == {'ok': True}
        assert s['status'] == 'stopped'
        assert s['pty'].closed_with is True  # force=True
        assert pid not in state.tracked_processes


class TestStream:
    def test_stream_unknown_session_yields_error_event(self, client):
        r = client.get('/api/terminal/stream?session=ghost')
        assert r.status_code == 200
        assert r.mimetype == 'text/event-stream'
        assert b'no active session' in r.data
