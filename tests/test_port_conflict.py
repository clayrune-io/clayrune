"""Regression tests for the single-instance port guard (`_check_port_conflict`).

MC-908: with a live Clayrune on 5199, a second `python server.py` booted all the
way through and announced "Clayrune running at http://localhost:5199" — no
banner, no exit 2. The guard probed with a plain `bind('0.0.0.0', PORT)`, and on
Windows that bind SUCCEEDS against a listener holding SO_REUSEADDR (which
`_serve_dual_stack` sets). The guard was a silent no-op on the very platform its
comment described. Two MCs then split traffic across separate `agent_sessions`
dicts and the newer one's startup reaper killed the older one's live agents.

`64d0510` fixed it by adding a connect() probe to 127.0.0.1 and ::1 alongside
the bind test, but shipped no tests. **A test that only asserts the function
returns is worthless here — returning quietly was the bug.** So every test below
asserts the guard is FATAL (`SystemExit(2)`) or explicitly not, and the headline
test pins the exact shape of the hole: a port that a bind probe calls free while
something is genuinely serving on it.

Safety: every listener is a socket THIS test opened on an ephemeral port
(port 0) and closes itself. Nothing here touches 5199 and nothing is killed.
"""
from __future__ import annotations

import importlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────


@pytest.fixture
def srv(tmp_data_dir):
    """server, imported with MC_DATA_DIR pointed at a throwaway dir.

    tmp_data_dir also sets MC_PORT=0, so the module-level PORT never inherits
    the real 5199 — each test rebinds server.PORT to a port it owns.
    """
    import server
    importlib.reload(server)
    return server


@pytest.fixture
def logged(srv, monkeypatch):
    """Collect what the guard printed, so tests can assert on the banner."""
    lines: list[str] = []
    monkeypatch.setattr(srv, '_log', lambda *a, **k: lines.append(str(a[0]) if a else ''))
    return lines


class _Listener:
    """A real listening socket on an ephemeral port, closed on exit.

    It drains its accept queue in a background thread. That is not decoration:
    a listener that never accept()s stops answering once its backlog fills, so
    a test that probes it in a loop (the restart-window ones do, ~50 times)
    would see the port go "free" halfway through. A real server accepts.
    """

    def __init__(self, family=socket.AF_INET, host='127.0.0.1', reuseaddr=True):
        self.sock = socket.socket(family, socket.SOCK_STREAM)
        if reuseaddr:
            # Mimic werkzeug/_serve_dual_stack, which is what made the old
            # bind-only probe lie on Windows.
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            # V6ONLY=1: this listener owns [::1]:port and leaves the whole IPv4
            # space free, so a 0.0.0.0 bind probe succeeds on EVERY platform.
            self.sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        self.sock.bind((host, 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._closed = False
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self):
        while not self._closed:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return  # socket closed — we're done
            conn.close()   # accept and hang up: TCP answers, HTTP does not

    def close(self):
        self._closed = True
        try:
            self.sock.close()
        except OSError:
            pass
        self.thread.join(timeout=5)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class _FakeClock:
    """Stand-in for server._time so the 15s restart window costs no wall time."""

    def __init__(self, on_sleep=None):
        self.t = 1000.0
        self.slept: list[float] = []
        self._on_sleep = on_sleep

    def time(self):
        return self.t

    def sleep(self, secs):
        self.t += secs
        self.slept.append(secs)
        if self._on_sleep:
            self._on_sleep(len(self.slept))

    @property
    def elapsed(self):
        return self.t - 1000.0


def _free_port() -> int:
    """A port with nothing on it (bound to discover it, then released)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _ipv6_available() -> bool:
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s.bind(('::1', 0))
        s.close()
        return True
    except OSError:
        return False


class _StubHandler(BaseHTTPRequestHandler):
    """Answers /api/system/heartbeat like a real Clayrune iff HEARTBEAT is on."""

    HEARTBEAT = True

    def do_GET(self):  # noqa: N802 (stdlib naming)
        if self.HEARTBEAT and self.path == '/api/system/heartbeat':
            body = json.dumps({
                'started_at': '2026-09-01T00:00:00+00:00',
                'pid': 4242,
                'uptime_seconds': 7,
            }).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header('Content-Length', '0')
            self.end_headers()

    def log_message(self, *a):  # silence stderr noise
        pass


class _StubServer:
    """A tiny HTTP server on an ephemeral port; heartbeat answer is opt-in."""

    def __init__(self, heartbeat: bool):
        handler = type('H', (_StubHandler,), {'HEARTBEAT': heartbeat})
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


# ── the hole MC-908 reported ────────────────────────────────────────────────


@pytest.mark.skipif(not _ipv6_available(), reason='no IPv6 loopback on this host')
def test_serving_port_that_bind_probe_calls_free_is_still_fatal(srv, logged, monkeypatch):
    """THE regression test: bind says "free", something is serving anyway.

    An IPv6-only listener owns [::1]:port and leaves the entire IPv4 space
    free, so `bind('0.0.0.0', port)` succeeds — exactly what SO_REUSEADDR did
    to the old probe on Windows, but reproducible on every platform. Pre-64d0510
    this returned None and the second instance booted. It must now exit 2.
    """
    with _Listener(family=socket.AF_INET6, host='::1') as lis:
        monkeypatch.setattr(srv, 'PORT', lis.port)

        # Precondition — prove the bind probe alone is fooled, so this test is
        # actually exercising the connect() leg and not the bind leg.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(('0.0.0.0', lis.port))
            bind_says_free = True
        except OSError:
            bind_says_free = False
        finally:
            probe.close()
        assert bind_says_free, 'precondition: a bind probe must see this port as free'

        with pytest.raises(SystemExit) as exc:
            srv._check_port_conflict()
    assert exc.value.code == 2
    assert any('already in use' in ln for ln in logged)


def test_live_ipv4_listener_is_fatal(srv, logged, monkeypatch):
    with _Listener() as lis:
        monkeypatch.setattr(srv, 'PORT', lis.port)
        with pytest.raises(SystemExit) as exc:
            srv._check_port_conflict()
    assert exc.value.code == 2


def test_free_port_returns_cleanly(srv, logged, monkeypatch):
    """The other half: the guard must NOT be fatal when the port really is free
    (otherwise "it trips" is trivially satisfied by never starting at all)."""
    monkeypatch.setattr(srv, 'PORT', _free_port())
    assert srv._check_port_conflict() is None
    assert not any('already in use' in ln for ln in logged)


def test_fatal_path_writes_forensic_log(srv, logged, monkeypatch, tmp_data_dir):
    with _Listener() as lis:
        monkeypatch.setattr(srv, 'PORT', lis.port)
        with pytest.raises(SystemExit):
            srv._check_port_conflict()
    entries = (tmp_data_dir / 'port_conflict.log').read_text(encoding='utf-8')
    assert f'port {lis.port}' in entries and 'aborting' in entries


# ── the documented bypass ───────────────────────────────────────────────────


def test_allow_port_conflict_env_bypasses(srv, logged, monkeypatch):
    monkeypatch.setenv('MC_ALLOW_PORT_CONFLICT', '1')
    with _Listener() as lis:
        monkeypatch.setattr(srv, 'PORT', lis.port)
        assert srv._check_port_conflict() is None  # proceeds despite the conflict
    assert any('already in use' in ln for ln in logged)         # still warns
    assert any('proceeding ANYWAY' in ln for ln in logged)


def test_bypass_requires_exactly_1(srv, logged, monkeypatch):
    monkeypatch.setenv('MC_ALLOW_PORT_CONFLICT', 'true')
    with _Listener() as lis:
        monkeypatch.setattr(srv, 'PORT', lis.port)
        with pytest.raises(SystemExit) as exc:
            srv._check_port_conflict()
    assert exc.value.code == 2


# ── the restart re-exec window ──────────────────────────────────────────────


def test_restart_window_waits_for_parent_to_release(srv, logged, monkeypatch):
    """MC_RESTART_FROM_PID means the port-holder is the parent we just replaced
    and is on its way out. The guard must WAIT for it, not die."""
    lis = _Listener()
    monkeypatch.setattr(srv, 'PORT', lis.port)
    monkeypatch.setenv('MC_RESTART_FROM_PID', '4242')
    # The "parent" releases the port on the 3rd poll.
    clock = _FakeClock(on_sleep=lambda n: lis.close() if n == 3 else None)
    monkeypatch.setattr(srv, '_time', clock)
    try:
        assert srv._check_port_conflict() is None      # waited, then proceeded
    finally:
        lis.close()
    assert len(clock.slept) == 3                        # it really did poll
    assert clock.elapsed < 15.0                         # and did not burn the window
    # Marker cleared so the NEXT boot doesn't inherit a stale 15s grace period.
    assert 'MC_RESTART_FROM_PID' not in __import__('os').environ
    assert any('released port' in ln for ln in logged)


def test_restart_window_still_dies_if_parent_never_releases(srv, logged, monkeypatch):
    """The grace period is bounded — a parent that never leaves is still a
    conflict, not an excuse to boot beside a live server."""
    with _Listener() as lis:
        monkeypatch.setattr(srv, 'PORT', lis.port)
        monkeypatch.setenv('MC_RESTART_FROM_PID', '4242')
        clock = _FakeClock()
        monkeypatch.setattr(srv, '_time', clock)
        with pytest.raises(SystemExit) as exc:
            srv._check_port_conflict()
    assert exc.value.code == 2
    assert clock.elapsed >= 15.0                        # waited the full window
    assert any('waited 15s' in ln for ln in logged)


def test_no_restart_marker_dies_immediately(srv, logged, monkeypatch):
    with _Listener() as lis:
        monkeypatch.setattr(srv, 'PORT', lis.port)
        monkeypatch.delenv('MC_RESTART_FROM_PID', raising=False)
        clock = _FakeClock()
        monkeypatch.setattr(srv, '_time', clock)
        with pytest.raises(SystemExit):
            srv._check_port_conflict()
    assert clock.slept == []                            # no grace period at all


# ── naming the holder: another Clayrune vs a stranger ───────────────────────


def test_message_identifies_another_clayrune(srv, logged, monkeypatch):
    """A holder that answers /api/system/heartbeat is one of ours — say so, and
    point at the running instance."""
    with _StubServer(heartbeat=True) as stub:
        monkeypatch.setattr(srv, 'PORT', stub.port)
        with pytest.raises(SystemExit) as exc:
            srv._check_port_conflict()
    assert exc.value.code == 2
    banner = '\n'.join(logged)
    assert 'Another Clayrune is already running' in banner
    assert 'answered' in banner and '/api/system/heartbeat' in banner
    assert 'NOT Clayrune' not in banner


def test_message_identifies_a_stranger_and_suggests_another_port(srv, logged, monkeypatch):
    """A holder that answers TCP but not the heartbeat is somebody else's
    service. Telling the user to "stop the other MC" sends them hunting a
    process that does not exist — the advice must be to move Clayrune."""
    with _StubServer(heartbeat=False) as stub:
        monkeypatch.setattr(srv, 'PORT', stub.port)
        with pytest.raises(SystemExit) as exc:
            srv._check_port_conflict()
    assert exc.value.code == 2
    banner = '\n'.join(logged)
    assert 'NOT Clayrune' in banner
    assert 'MC_PORT' in banner                          # the actionable fix
    assert 'Another Clayrune is already running' not in banner
