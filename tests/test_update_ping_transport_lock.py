"""The update-check ping's network I/O must never sit inside the shared config lock
(Fenn third review, 2026-11-05).

Before this change `_gate_and_send` held `provider_preferences._write_lock` through
DNS, connect, TLS and the response body, so a stalled resolver or a slow server froze
OFF, /api/update-ping/seen and every PUT /api/config (Fenn measured 11 s and 21 s,
no finite bound). Now: connect with no lock, gate + request write under the lock,
read with no lock.

These tests use the real Flask routes and REAL loopback sockets (nothing from the
transport is mocked; only the resolver is substituted in the first test). Nothing
leaves 127.0.0.1.
"""
import json
import socket
import threading
import time

import pytest
from flask import Flask

from mc import state, update_ping
from mc.blueprints import settings_routes, update_ping_routes

# Captured at import, before tests/test_update_ping.py's autouse socket guard exists.
_REAL_CONNECT = socket.socket.connect
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_GETADDRINFO = socket.getaddrinfo

FAST = 1.0          # a waiter must return in under this many seconds


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in update_ping.ENV_OFF_VARS:
        monkeypatch.delenv(k, raising=False)
    cfg: dict = {'update_ping_enabled': True, 'update_ping_disclosed': True,
                 'sticky_agent_settings': False}
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(cfg), encoding='utf-8')
    monkeypatch.setattr(state, 'CONFIG', cfg)
    monkeypatch.setattr(update_ping_routes, 'is_unattended_caller', lambda: False)
    monkeypatch.setattr(settings_routes, 'is_unattended_caller', lambda: False)
    monkeypatch.setattr(settings_routes, '_require_human_passcode', lambda data: None)
    monkeypatch.setattr(settings_routes, 'CONFIG_PATH', path)
    update_ping_routes.wire(config_path=path)

    def point_at(port):
        cfg['update_ping_url'] = f'http://127.0.0.1:{port}/ping'
    update_ping.wire(tmp_path / 'state.json', lambda: cfg, lambda: 'v2.3.0', tmp_path / 'ping_id')
    app = Flask(__name__)
    app.register_blueprint(update_ping_routes.bp)
    app.register_blueprint(settings_routes.bp)
    # the real socket functions for the loopback server, whatever an earlier test left patched
    monkeypatch.setattr(socket.socket, 'connect', _REAL_CONNECT)
    monkeypatch.setattr(socket, 'create_connection', _REAL_CREATE_CONNECTION)
    return type('E', (), {'app': app, 'cfg': cfg, 'path': path, 'point_at': staticmethod(point_at)})


class Listener:
    """One-connection loopback server. Records every request byte it receives.

    `mode`: 'silent' reads until the client closes and answers nothing;
    'drip' reads the request, sends headers, then one body byte per 50 ms until
    `release` is set, then flushes the rest.
    """

    def __init__(self, mode='silent'):
        self.mode = mode
        self.location = ''
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(1)
        self.sock.settimeout(15)
        self.port = self.sock.getsockname()[1]
        self.received = b''
        self.accepted = threading.Event()
        self.body_started = threading.Event()
        self.release = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True, name='loopback')
        self.thread.start()

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        self.accepted.set()
        with conn:
            conn.settimeout(15)
            try:
                if self.mode == 'silent':
                    while True:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        self.received += chunk
                    return
                while b'\r\n\r\n' not in self.received:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    self.received += chunk
                head, _, rest = self.received.partition(b'\r\n\r\n')
                need = int([ln.split(b':')[1] for ln in head.split(b'\r\n')
                            if ln.lower().startswith(b'content-length')][0])
                while len(rest) < need:
                    rest += conn.recv(4096)
                self.received = head + b'\r\n\r\n' + rest
                if self.mode == 'redirect':
                    conn.sendall(f'HTTP/1.1 302 Found\r\nLocation: {self.location}\r\n'
                                 'Content-Length: 0\r\nConnection: close\r\n\r\n'.encode())
                    return
                conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 1024\r\nConnection: close\r\n\r\n')
                body = b'{}' + b' ' * 1022
                conn.sendall(body[:1])
                self.body_started.set()
                for i in range(1, len(body)):
                    if self.release.wait(.05):
                        conn.sendall(body[i:])
                        break
                    conn.sendall(body[i:i + 1])
            except OSError:
                pass

    def close(self):
        self.release.set()
        self.sock.close()
        self.thread.join(5)


def _start(fn):
    out = {}

    def run():
        try:
            out['r'] = fn()
        except BaseException as exc:      # surfaced by the asserting test, not swallowed
            out['err'] = repr(exc)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, out


def _timed(app, method, path, data=None):
    t0 = time.monotonic()
    r = app.test_client().open(path, method=method, json=data)
    return r.status_code, round(time.monotonic() - t0, 3)


def _three_writers(app):
    """OFF, /seen and an unrelated PUT /api/config, each timed, run one after another."""
    return {
        'OFF': _timed(app, 'POST', '/api/update-ping/choice', {'enabled': False}),
        'seen': _timed(app, 'POST', '/api/update-ping/seen'),
        'settings': _timed(app, 'PUT', '/api/config', {'sticky_agent_settings': False}),
    }


def _assert_fast(label, times):
    print(f'{label}: ' + json.dumps(times))
    for name, (status, elapsed) in times.items():
        assert status == 200, (label, name, status)
        assert elapsed < FAST, (label, name, elapsed)


def test_stalled_resolver_does_not_freeze_config_writers(env, monkeypatch):
    """Regression 1. The resolver hangs inside connect(). OFF, /seen and an unrelated
    PUT /api/config return 200 in <1 s meanwhile; when the resolver is released the
    connection opens, the gate sees OFF, and ZERO bytes reach the server."""
    srv = Listener('silent')
    env.point_at(srv.port)
    reached, release = threading.Event(), threading.Event()

    def stalled(*a, **k):
        reached.set()
        assert release.wait(30)
        return _REAL_GETADDRINFO(*a, **k)
    monkeypatch.setattr(socket, 'getaddrinfo', stalled)
    try:
        t, out = _start(lambda: update_ping.maybe_ping(now=1_000_000, environ={}))
        assert reached.wait(5), 'sender never reached the resolver'
        times = _three_writers(env.app)
        _assert_fast('stalled resolver', times)
        assert t.is_alive(), 'sender must still be stuck in the resolver'
        release.set()
        t.join(10)
        assert not t.is_alive()
        assert out.get('r') == {'sent': False, 'reason': 'disabled'}, out
        assert srv.accepted.wait(5), 'connect() should have completed after release'
        time.sleep(0.2)
        assert srv.received == b'', srv.received
        assert 'last_attempt_ts' not in update_ping._STATE   # unsent ping does not eat the day
    finally:
        release.set()
        srv.close()


def test_dripping_response_body_does_not_freeze_config_writers(env, monkeypatch):
    """Regression 2. A real loopback server sends 200 headers and then one body byte per
    50 ms (the case Fenn measured at 21 s). While the sender sits in the response read,
    OFF, /seen and PUT /api/config all return 200 in <1 s."""
    monkeypatch.setattr(update_ping, 'TIMEOUT_S', 5)
    srv = Listener('drip')
    env.point_at(srv.port)
    try:
        t, out = _start(lambda: update_ping.maybe_ping(now=1_000_000, environ={}))
        assert srv.body_started.wait(5), 'sender never got to the response'
        times = _three_writers(env.app)
        _assert_fast('dripping body', times)
        assert t.is_alive(), 'sender must still be reading the dripping body'
        srv.release.set()
        t.join(10)
        assert out.get('r', {}).get('ok') is True, out
        assert b'"install_id"' in srv.received         # the request was written before OFF
    finally:
        srv.close()


def test_opt_out_between_connect_and_the_locked_write_sends_zero_bytes(env, monkeypatch):
    """Regression 3. OFF commits after connect() returns and before the locked write.
    The connection is open (the server accepted it) and the server receives nothing."""
    srv = Listener('silent')
    env.point_at(srv.port)
    real_connect = update_ping._HttpPost.connect
    off = {}

    def connect_then_off(self):
        real_connect(self)
        t, o = _start(lambda: _timed(env.app, 'POST', '/api/update-ping/choice', {'enabled': False}))
        t.join(5)              # would time out if connect() were still inside the config lock
        off['alive'], off['out'] = t.is_alive(), o
    monkeypatch.setattr(update_ping._HttpPost, 'connect', connect_then_off)
    try:
        r = update_ping.maybe_ping(now=1_000_000, environ={})
        assert off['alive'] is False and off['out']['r'][0] == 200, off
        assert env.cfg['update_ping_enabled'] is False
        assert json.loads(env.path.read_text())['update_ping_enabled'] is False
        assert r == {'sent': False, 'reason': 'disabled'}, r
        assert srv.accepted.wait(5), 'the connection should have been opened'
        srv.thread.join(5)     # the client closed it; the server loop ends on EOF
        assert srv.received == b'', srv.received
        assert 'last_attempt_ts' not in update_ping._STATE
    finally:
        srv.close()


def test_redirect_is_refused_and_not_followed(env):
    """Replaces the old `_NoRedirect` handler test: the hand-built http.client transport
    never follows a 3xx, and raises instead of treating it as success."""
    target = Listener('silent')
    srv = Listener('redirect')
    srv.location = f'http://127.0.0.1:{target.port}/x'
    try:
        post = update_ping._HttpPost(f'http://127.0.0.1:{srv.port}/ping', {'a': 1})
        post.connect()
        post.send()
        with pytest.raises(ValueError, match='HTTP 302'):
            post.read()
        post.close()
        assert not target.accepted.wait(0.5), 'the redirect target was contacted'
    finally:
        srv.close()
        target.close()


def test_dripping_body_hits_the_read_deadline(monkeypatch):
    """The read (no lock held) still stops a server that drips its body; read1() makes
    the deadline check run between arriving bytes, which read(1024) would not."""
    monkeypatch.setattr(socket.socket, 'connect', _REAL_CONNECT)
    monkeypatch.setattr(socket, 'create_connection', _REAL_CREATE_CONNECTION)
    monkeypatch.setattr(update_ping, 'TIMEOUT_S', 0.3)
    srv = Listener('drip')
    try:
        post = update_ping._HttpPost(f'http://127.0.0.1:{srv.port}/ping', {})
        post.connect()
        post.send()
        t0 = time.monotonic()
        with pytest.raises(TimeoutError):
            post.read()
        assert time.monotonic() - t0 < 2
        post.close()
    finally:
        srv.close()
