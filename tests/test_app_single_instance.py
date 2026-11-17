"""app.py (the desktop entry point) must bind its port BEFORE boot() (MC-1179 S2).

`server.py`'s `__main__` reserves its listening socket first (MC-1060) so the
loser of a boot race exits before any startup phase has touched shared state.
`app.py` used to call `boot(check_port=False)` and only then `app.run()`: with a
Clayrune already on the port, the second launch minted a new exec token over the
running instance's, flipped its live runs to interrupted, and rewrote hooks
before its own bind failed. These tests run `app._start_flask` against a port
that is really held and assert `boot()` never starts.

Safety: every port is ephemeral and every socket is one this test opened and
closes. Nothing touches 5199, nothing is killed. `boot` and `app.run` are
stubbed, so no startup phase runs.
"""
from __future__ import annotations

import socket

import pytest

from mc import listen_socket


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def desktop(monkeypatch):
    """`app._start_flask` with boot()/serve stubbed; returns (app_module, calls)."""
    import app as desktop_app
    import server

    calls: list[str] = []
    monkeypatch.setattr(server, 'boot', lambda check_port=True: calls.append('boot'))
    monkeypatch.setattr(server.app, 'run', lambda *a, **k: calls.append('app.run'))
    monkeypatch.setattr(server, '_serve_reserved',
                        lambda reserved: (calls.append('serve'), reserved.close()))
    # _start_flask exports these for the real server; keep the test hermetic.
    monkeypatch.setenv('MC_DATA_DIR', '')
    monkeypatch.setenv('MC_PORT', '')
    return desktop_app, server, calls


def test_second_desktop_launch_never_runs_boot(desktop, monkeypatch):
    """The S2 failure: a Clayrune already holds the port; the desktop launch
    must stop at the bind, before boot() can touch the running instance."""
    desktop_app, server, calls = desktop
    port = _free_port()
    monkeypatch.setattr(server, 'PORT', port)
    holder = listen_socket.reserve(port, loopback_only=True)   # the running instance
    try:
        with pytest.raises(SystemExit) as exc:
            desktop_app._start_flask(port)
    finally:
        holder.close()
    assert exc.value.code == 2
    assert calls == [], f"boot()/serve ran beside a running instance: {calls}"


def test_desktop_launch_binds_before_boot_then_serves_the_reserved_socket(desktop, monkeypatch):
    """Free port: the bind comes first, boot runs with the port already ours,
    and serving uses the reserved socket (not a fresh app.run bind)."""
    desktop_app, server, calls = desktop
    port = _free_port()
    monkeypatch.setattr(server, 'PORT', port)
    real_reserve = listen_socket.reserve

    def spy_reserve(*a, **k):
        calls.append('reserve')
        return real_reserve(*a, **k)

    monkeypatch.setattr(listen_socket, 'reserve', spy_reserve)
    desktop_app._start_flask(port)
    assert calls == ['reserve', 'boot', 'serve']


def test_desktop_listener_stays_loopback_only(desktop, monkeypatch):
    """app.py served on 127.0.0.1 only. Binding through the shared reserve path
    must not widen that to every interface."""
    desktop_app, server, calls = desktop
    port = _free_port()
    monkeypatch.setattr(server, 'PORT', port)
    seen = {}
    real_reserve = listen_socket.reserve

    def spy_reserve(p, **k):
        seen.update(k)
        return real_reserve(p, **k)

    monkeypatch.setattr(listen_socket, 'reserve', spy_reserve)
    monkeypatch.delenv('MC_BIND_LOOPBACK', raising=False)
    desktop_app._start_flask(port)
    assert seen.get('loopback_only') is True


def test_app_py_source_reserves_before_boot():
    """Static backstop for the behavioural tests: the same order in source.
    (test_entrypoint_boot's port-check-before-token test only orders
    `_check_port_conflict` inside boot(), which both entry points skip.)"""
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / 'app.py').read_text(encoding='utf-8')
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == '_start_flask')
    lines = {}
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            lines.setdefault(n.func.id, n.lineno)
    assert lines['_reserve_listeners'] < lines['boot'] < lines['_serve_reserved']
