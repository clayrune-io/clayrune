"""Real local HTTP reads and owned-Popen cleanup boundaries, no live server."""
import socket
import threading
import time
from types import SimpleNamespace

import pytest
import requests
import urllib3.connection

from mc import transform_http_deadline as http
from mc import transform_process_deadline as process


@pytest.mark.parametrize('mode', ['headers', 'keepalive_body', 'closing_body', 'success'])
def test_owned_http_socket_is_aborted_through_headers_and_body(monkeypatch, mode):
    client, peer = socket.socketpair()
    session = requests.Session()
    session.trust_env = False
    monkeypatch.setattr(requests, 'Session', lambda: session)

    def connect(connection):
        connection.sock = client
        client.settimeout(connection.timeout)

    monkeypatch.setattr(urllib3.connection.HTTPConnection, 'connect', connect)
    disconnected = threading.Event()

    def feed():
        try:
            peer.recv(65536)
            if mode == 'success':
                peer.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}')
                return
            if mode == 'headers':
                peer.sendall(b'HTTP/1.1 200 OK\r\nX-Trickle: ')
            else:
                close = b'Connection: close\r\n' if mode == 'closing_body' else b''
                peer.sendall(b'HTTP/1.1 200 OK\r\n' + close +
                             b'Transfer-Encoding: chunked\r\n\r\n')
            for _ in range(40):
                time.sleep(.025)
                peer.sendall(b'x' if mode == 'headers' else b'1\r\n \r\n')
        except (ConnectionError, OSError):
            disconnected.set()
        finally:
            peer.close()

    thread = threading.Thread(target=feed)
    thread.start()
    start = time.monotonic()
    try:
        if mode == 'success':
            assert http.post('http://synthetic.invalid', deadline=start + .25, timeout=1).json() == {}
        else:
            with pytest.raises(TimeoutError):
                http.post('http://synthetic.invalid', deadline=start + .25, timeout=1)
            assert time.monotonic() - start < .65
            assert disconnected.wait(.25)
    finally:
        client.close()
        peer.close()
        thread.join(timeout=1)
    assert not thread.is_alive()


def test_tree_cleanup_never_targets_an_exited_owned_root(monkeypatch):
    monkeypatch.setattr(process.subprocess, 'run', lambda *a, **kw: pytest.fail('stale PID kill'))
    monkeypatch.setattr(process.proc_kill, 'kill_tree', lambda *a: pytest.fail('stale PID kill'))
    process._stop_owned(SimpleNamespace(poll=lambda: 0))


def test_windows_tree_cleanup_uses_only_live_owned_pid_and_bounded_taskkill(monkeypatch):
    calls = []
    monkeypatch.setattr(process, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(process.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(process.subprocess, 'run', lambda *a, **kw: calls.append((a, kw)))
    alive = [True]
    proc = SimpleNamespace(pid=12345, poll=lambda: None if alive[0] else 1,
                           kill=lambda: alive.__setitem__(0, False))
    process._stop_owned(proc)
    assert calls[0][0] == (['taskkill', '/F', '/T', '/PID', '12345'],)
    assert calls[0][1]['timeout'] == process.CLEANUP_SECONDS
    assert not alive[0]


def test_pipe_and_tree_cleanup_cannot_extend_the_finalization_reserve(monkeypatch):
    release = threading.Event()
    entered = threading.Event()
    killed = []

    def communicate(data):
        entered.set()
        release.wait(3)
        return '', ''

    proc = SimpleNamespace(communicate=communicate, pid=12345,
                           poll=lambda: 1 if killed else None)
    monkeypatch.setattr(process.subprocess, 'Popen', lambda *a, **kw: proc)

    def slow_cleanup(owned):
        assert owned is proc
        release.wait(3)
        killed.append(True)

    monkeypatch.setattr(process, '_stop_owned', slow_cleanup)
    start = time.monotonic()
    try:
        with pytest.raises(process.subprocess.TimeoutExpired):
            process.run(['synthetic'], input='data', timeout=.1, deadline=start + .1)
        assert entered.is_set()
        assert time.monotonic() - start < .85
    finally:
        release.set()
