"""The listening socket is bound BEFORE boot() runs (MC-1060).

`server.py`'s `__main__` used to run boot() and only then bind. The exclusive
bind (mc/listen_socket.py) therefore refused the loser of a boot race only after
it had run every boot phase: reconcile pending agent_log rows, delegation
delivery, workflow-run adoption, hivemind stale reconcile, worktree gc, builtin
installs, guardrail-hook writes. Those can adopt or mark stale the WINNER's live
runs and double-deliver callbacks.

The tests run the real `__main__` block (extracted from server.py by AST, so the
order under test is the shipped order) against a real held port, with boot() and
serving stubbed. Safety: every port is ephemeral, every socket is opened and
closed here; nothing touches 5199, no server starts, nothing is killed.
"""
from __future__ import annotations

import ast
import importlib
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from mc import listen_socket

REPO = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def srv(tmp_data_dir, monkeypatch):
    import server
    importlib.reload(server)
    monkeypatch.delenv('MC_ALLOW_PORT_CONFLICT', raising=False)
    monkeypatch.delenv('MC_BIND_LOOPBACK', raising=False)
    monkeypatch.delenv('MC_RESTART_FROM_PID', raising=False)
    return server


def _main_body():
    tree = ast.parse((REPO / 'server.py').read_text(encoding='utf-8'))
    main = [n for n in tree.body
            if isinstance(n, ast.If) and ast.unparse(n.test) == "__name__ == '__main__'"]
    assert len(main) == 1
    return compile(ast.Module(body=main[0].body, type_ignores=[]), 'server.py:__main__', 'exec')


def _run_main_block(srv, port, *, boot, serve, reserve=None):
    """Execute server.py's `if __name__ == '__main__':` body with boot and the
    serving step replaced; the port reservation stays real."""
    ns = dict(vars(srv))
    ns['PORT'] = port
    ns['boot'] = boot
    ns['_serve_reserved'] = serve
    if reserve is not None:
        ns['_reserve_listeners'] = reserve
    exec(_main_body(), ns)


def test_a_failed_bind_runs_no_boot_phase(srv, monkeypatch):
    """Headline: the loser of a boot race exits before boot() is even called.
    Fails on the old order (boot() first), where the stub is called once."""
    lines: list[str] = []
    monkeypatch.setattr(srv, '_log', lambda *a, **k: lines.append(str(a[0]) if a else ''))
    port = _free_port()
    holder = listen_socket.bind_listener(socket.AF_INET6, '::', port, v6only=False, retry_window_s=0)
    events: list = []
    try:
        with pytest.raises(SystemExit) as exc:
            _run_main_block(srv, port,
                            boot=lambda *a, **k: events.append('boot'),
                            serve=lambda r: events.append('serve'))
    finally:
        holder.close()
    assert exc.value.code == 2
    assert events == []                                   # no boot, no serve
    assert any('before any startup phase has run' in ln for ln in lines)   # said plainly
    assert any('already in use' in ln for ln in lines)    # banner still printed


def test_the_port_is_already_held_by_the_time_boot_runs(srv):
    """Winner path: reserve -> boot -> serve, in that order, and while boot()
    runs a rival cannot take the port (it is listening and answers)."""
    port = _free_port()
    seen: dict = {}
    events: list = []

    def fake_boot(*a, **k):
        events.append('boot')
        seen['kwargs'] = k
        seen['answers'] = listen_socket.port_answers(port)
        try:
            listen_socket.bind_listener(socket.AF_INET6, '::', port, v6only=False, retry_window_s=0)
            seen['rival'] = 'bound'
        except listen_socket.PortInUse:
            seen['rival'] = 'refused'

    real = srv._reserve_listeners
    reserved: list = []

    def reserve(p):
        events.append('reserve')
        reserved.append(real(p))
        return reserved[-1]

    try:
        _run_main_block(srv, port, boot=fake_boot, reserve=reserve,
                        serve=lambda r: events.append('serve'))
    finally:
        for r in reserved:
            r.close()
    assert events == ['reserve', 'boot', 'serve']
    assert seen['answers'] is True
    assert seen['rival'] == 'refused'
    # boot()'s connect-probe check would see our own listening socket as a
    # rival and exit 2: the bind IS the check, so main skips it.
    assert seen['kwargs'] == {'check_port': False}


# ── reserve(): modes and the restart wait ───────────────────────────────────


def test_default_mode_is_one_dual_stack_socket_that_accepts_ipv4():
    port = _free_port()
    r = listen_socket.reserve(port)
    try:
        assert [h for h, _ in r.listeners] == ['::']
        c = socket.create_connection(('127.0.0.1', port), timeout=2)   # IPv4 into the v6 socket
        c.close()
    finally:
        r.close()


def test_loopback_mode_binds_both_loopback_addresses():
    port = _free_port()
    r = listen_socket.reserve(port, loopback_only=True)
    try:
        hosts = [h for h, _ in r.listeners]
        assert hosts[-1] == '127.0.0.1'                 # served on the calling thread
        assert hosts in (['::1', '127.0.0.1'], ['127.0.0.1'])   # ::1 absent only without IPv6
    finally:
        r.close()


def test_loopback_mode_fails_on_a_held_port_and_releases_what_it_bound():
    port = _free_port()
    holder = listen_socket.bind_listener(socket.AF_INET, '127.0.0.1', port)
    try:
        with pytest.raises(listen_socket.PortInUse):
            listen_socket.reserve(port, loopback_only=True, retry_window_s=0)
    finally:
        holder.close()
    # whatever reserve() bound before failing must be gone, or the port stays stuck
    again = listen_socket.reserve(port, loopback_only=True, retry_window_s=2.0)
    again.close()


def test_dual_stack_falls_back_to_ipv4_only_when_ipv6_is_unavailable(monkeypatch):
    port = _free_port()
    real = listen_socket.bind_listener

    def fake(family, host, p, **kw):
        if family == socket.AF_INET6:
            raise OSError(97, 'Address family not supported')
        return real(family, host, p, **kw)

    monkeypatch.setattr(listen_socket, 'bind_listener', fake)
    lines: list[str] = []
    r = listen_socket.reserve(port, log=lines.append)
    try:
        assert [h for h, _ in r.listeners] == ['0.0.0.0']
        assert any('falling back to IPv4-only' in ln for ln in lines)
    finally:
        r.close()


def test_a_taken_port_is_not_mistaken_for_missing_ipv6():
    """PortInUse is an OSError; the IPv4 fallback must not swallow it."""
    port = _free_port()
    holder = listen_socket.bind_listener(socket.AF_INET6, '::', port, v6only=False)
    try:
        with pytest.raises(listen_socket.PortInUse):
            listen_socket.reserve(port, retry_window_s=0)
    finally:
        holder.close()


def test_restart_wait_rides_out_a_holder_that_still_answers():
    """Restart: the parent we replaced still ACCEPTS connections while it shuts
    down. Without wait_for_holder the bind fails on the first answer."""
    port = _free_port()
    holder = listen_socket.bind_listener(socket.AF_INET6, '::', port, v6only=False)
    try:
        t0 = time.time()
        with pytest.raises(listen_socket.PortInUse):
            listen_socket.reserve(port, retry_window_s=5.0)        # fails fast: it answers
        assert time.time() - t0 < 3.0
        threading.Timer(0.8, holder.close).start()
        t0 = time.time()
        r = listen_socket.reserve(port, wait_for_holder=True, retry_window_s=10.0)
        try:
            assert 0.5 < time.time() - t0 < 5.0
        finally:
            r.close()
    finally:
        holder.close()


def test_restart_wait_is_still_bounded():
    port = _free_port()
    holder = listen_socket.bind_listener(socket.AF_INET6, '::', port, v6only=False)
    try:
        t0 = time.time()
        with pytest.raises(listen_socket.PortInUse):
            listen_socket.reserve(port, wait_for_holder=True, retry_window_s=1.0)
        assert 0.9 < time.time() - t0 < 4.0
    finally:
        holder.close()


def test_reserved_sockets_are_not_inherited_by_child_processes():
    """The sockets now live through boot(), which spawns children."""
    port = _free_port()
    r = listen_socket.reserve(port)
    try:
        assert all(not s.get_inheritable() for _, s in r.listeners)
    finally:
        r.close()


def test_reserve_listeners_consumes_the_restart_marker(srv, monkeypatch):
    """boot() no longer runs _check_port_conflict (which used to pop it), so the
    reserve step must, or every agent and test run inherits the 15s wait."""
    port = _free_port()
    monkeypatch.setenv('MC_RESTART_FROM_PID', '4242')
    r = srv._reserve_listeners(port)
    try:
        assert 'MC_RESTART_FROM_PID' not in os.environ
    finally:
        r.close()


def test_reserved_serve_answers_http_on_an_ephemeral_port():
    """Reserved.serve() hands the already-bound sockets to werkzeug and a
    request is answered: the end of the reserve -> boot -> serve path."""
    from flask import Flask
    app = Flask('t')

    @app.route('/ping')
    def ping():
        return 'pong'

    port = _free_port()
    r = listen_socket.reserve(port, loopback_only=True)
    threading.Thread(target=r.serve, args=(app,), daemon=True).start()
    deadline = time.time() + 5
    body = None
    while time.time() < deadline:
        try:
            body = urllib.request.urlopen(f'http://127.0.0.1:{port}/ping', timeout=1).read()
            break
        except OSError:
            time.sleep(0.1)
    assert body == b'pong'
