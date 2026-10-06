"""The main server's listening socket must be exclusive (mc/listen_socket.py).

2026-10-06: two Clayrune servers both LISTENING on 5199 after a reboot. Every
single-instance guard in front of the bind is check-then-bind and can be raced;
the bind was the only thing that could have refused the second server, and it
could not because the socket set SO_REUSEADDR, which on Windows means "share
this port with any other socket that also set it".

Safety: every port here is ephemeral and every socket is one this test opened
and closes. Nothing touches 5199, nothing is killed.
"""
from __future__ import annotations

import importlib
import socket
import sys
import threading
import time

import pytest

from mc import listen_socket

WIN = sys.platform == 'win32'


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _legacy_bind(port):
    """The pre-fix recipe: SO_REUSEADDR on a dual-stack socket."""
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    s.bind(('::', port))
    s.listen(8)
    return s


@pytest.mark.skipif(not WIN, reason='SO_REUSEADDR lets two live listeners share a port only on Windows')
def test_premise_two_reuseaddr_listeners_share_a_port_on_windows():
    """Pins the bug itself, so the tests below cannot pass by accident: the old
    recipe really does let a second listener bind the same port."""
    port = _free_port()
    a = _legacy_bind(port)
    try:
        b = _legacy_bind(port)   # must not raise -- this IS the bug
        b.close()
    finally:
        a.close()


@pytest.mark.skipif(not WIN, reason='exclusive-bind semantics under test are Windows-specific')
def test_second_exclusive_bind_on_one_port_fails_on_windows():
    port = _free_port()
    a = listen_socket.bind_listener(socket.AF_INET6, '::', port, v6only=False, retry_window_s=0)
    try:
        with pytest.raises(listen_socket.PortInUse):
            listen_socket.bind_listener(socket.AF_INET6, '::', port, v6only=False, retry_window_s=0)
    finally:
        a.close()


@pytest.mark.skipif(not WIN, reason='exclusive-bind semantics under test are Windows-specific')
def test_exclusive_listener_refuses_a_legacy_reuseaddr_second_server():
    """A server still running the OLD code (reuse) must not slip in beside a new
    one either -- the rolling-upgrade case."""
    port = _free_port()
    a = listen_socket.bind_listener(socket.AF_INET6, '::', port, v6only=False, retry_window_s=0)
    try:
        with pytest.raises(OSError):
            _legacy_bind(port)
    finally:
        a.close()


@pytest.mark.skipif(not WIN, reason='shared bind is a Windows-only concept')
def test_shared_true_restores_the_old_shared_bind():
    """MC_ALLOW_PORT_CONFLICT=1 must keep working: two opted-in instances share."""
    port = _free_port()
    a = listen_socket.bind_listener(socket.AF_INET6, '::', port, v6only=False, shared=True, retry_window_s=0)
    try:
        b = listen_socket.bind_listener(socket.AF_INET6, '::', port, v6only=False, shared=True, retry_window_s=0)
        b.close()
    finally:
        a.close()


def test_live_holder_fails_fast_not_after_the_retry_window():
    """The loser of a boot race must exit promptly: a holder that ANSWERS is a
    live server, not a socket still closing, so no waiting."""
    port = _free_port()
    a = listen_socket.bind_listener(socket.AF_INET, '127.0.0.1', port)
    try:
        t0 = time.time()
        with pytest.raises(listen_socket.PortInUse):
            listen_socket.bind_listener(socket.AF_INET, '127.0.0.1', port, retry_window_s=15.0)
        assert time.time() - t0 < 3.0
    finally:
        a.close()


def test_retries_while_old_owner_is_still_releasing():
    """Restart path: the old process still holds the port but is not accepting
    (bound, no listen -- the same 'answers nothing' state a closing socket is
    in). The bind must wait it out and then succeed."""
    port = _free_port()
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if WIN:
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    holder.bind(('127.0.0.1', port))     # bound, never listen()s

    threading.Timer(0.8, holder.close).start()
    t0 = time.time()
    s = listen_socket.bind_listener(socket.AF_INET, '127.0.0.1', port, retry_window_s=10.0)
    try:
        elapsed = time.time() - t0
        assert 0.5 < elapsed < 5.0, elapsed   # it did wait, and did not burn the window
    finally:
        s.close()


def test_retry_is_bounded_when_the_holder_never_leaves():
    port = _free_port()
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if WIN:
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    holder.bind(('127.0.0.1', port))
    try:
        t0 = time.time()
        with pytest.raises(listen_socket.PortInUse):
            listen_socket.bind_listener(socket.AF_INET, '127.0.0.1', port, retry_window_s=1.0)
        assert 0.9 < time.time() - t0 < 4.0
    finally:
        holder.close()


def test_rebind_right_after_a_listener_with_a_live_connection_closes():
    """Restart without reuse: connection history on the old listener (TIME_WAIT)
    must not stop the next exclusive bind. Measured fine on Windows 11."""
    port = _free_port()
    a = listen_socket.bind_listener(socket.AF_INET, '127.0.0.1', port)
    c = socket.create_connection(('127.0.0.1', port))
    conn, _ = a.accept()
    conn.close()
    time.sleep(0.2)
    c.close()
    a.close()
    b = listen_socket.bind_listener(socket.AF_INET, '127.0.0.1', port, retry_window_s=2.0)
    b.close()


# ── through the server's own entry points ───────────────────────────────────


@pytest.fixture
def srv(tmp_data_dir):
    import server
    importlib.reload(server)
    return server


def test_serve_dual_stack_exits_2_with_the_banner_when_port_is_held(srv, monkeypatch):
    """The end-to-end shape of the 2026-10-06 bug: a live server owns the port,
    a second server reaches the bind. It must die through the 'already in use'
    banner (exit 2), not silently share the port and split traffic."""
    lines: list[str] = []
    monkeypatch.setattr(srv, '_log', lambda *a, **k: lines.append(str(a[0]) if a else ''))
    monkeypatch.delenv('MC_ALLOW_PORT_CONFLICT', raising=False)
    monkeypatch.delenv('MC_BIND_LOOPBACK', raising=False)
    port = _free_port()
    monkeypatch.setattr(srv, 'PORT', port)
    holder = _legacy_bind(port)   # what the pre-fix server held: the strictest case to refuse on Windows
    served = []
    import werkzeug.serving
    monkeypatch.setattr(werkzeug.serving, 'make_server', lambda *a, **k: served.append(a) or None)
    try:
        with pytest.raises(SystemExit) as exc:
            srv._serve_dual_stack(port)
    finally:
        holder.close()
    assert exc.value.code == 2
    assert served == []                                   # never got as far as serving
    assert any('already in use' in ln for ln in lines)
