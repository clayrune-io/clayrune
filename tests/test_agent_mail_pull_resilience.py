"""MC-1186 / MB8: a transient network failure must not halt the pull worker for good.

Every case drives the real PullClient; the three ticket cases go through the
production HttpsTransport with a fake opener (no network), the way the review
measured them. HALT reasons must still halt.
"""
import socket
import urllib.error

import pytest

from mc.agent_mail_enrollment_keys import EnrollmentRefused
from mc.agent_mail_pull_client import PullClient
from mc.agent_mail_service_client import HttpsTransport
from test_agent_mail_pull_client import World, Peer, ORIGIN, MANIFEST, connect, eventually


class _Reply:
    def __init__(self, status, headers, body):
        self.status, self.headers, self._body = status, headers, body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def geturl(self):
        return ORIGIN + '/v1/pull/ticket'

    def read(self, n=-1):
        return self._body


class _FailingOpener:
    """Fails the first request in the chosen way, then delegates to the signed fake."""

    def __init__(self, world, mode):
        self.world, self.mode, self.calls = world, mode, 0

    def open(self, request, timeout=None):
        self.calls += 1
        if self.calls == 1:
            if self.mode == 'urlerror':
                raise urllib.error.URLError('dns down')
            if self.mode == 'timeout':
                raise socket.timeout('timed out')
            if self.mode == 'bad_gateway':
                body = b'<html>502 Bad Gateway</html>'
                return _Reply(502, _Headers({'content-type': 'text/html'}), body)
        headers = {k.lower(): v for k, v in request.header_items()}
        status, h, raw = self.world.http('POST', request.selector, headers, request.data)
        return _Reply(status, _Headers(h), raw)


class _Headers:
    def __init__(self, pairs):
        self.pairs = dict(pairs)

    def items(self):
        return list(self.pairs.items())


def _client(w, transport, peer, reviewed=None, **kw):
    return PullClient(w.store, w.seal, transport, install_id='%s' % w.client().install_id,
                      reviewed_service=reviewed or (lambda: (w.pin, MANIFEST)),
                      socket_factory=peer.factory, jitter=lambda low, high: 0, **kw)


@pytest.fixture
def w(tmp_path):
    return World(tmp_path)


@pytest.mark.parametrize('mode', ['urlerror', 'timeout', 'bad_gateway'])
def test_one_failed_ticket_request_is_retried_not_halted(w, mode):
    peer = Peer()
    opener = _FailingOpener(w, mode)
    client = _client(w, HttpsTransport(origin=ORIGIN, opener=opener), peer)
    try:
        client.start()
        eventually(lambda: peer.connections == 1, timeout=8)
        assert client.running and opener.calls == 2
    finally:
        peer.stop.set()  # the client closes its socket abruptly; that reset is not a peer error
        assert client.stop()
        peer.close()


def test_one_failed_manifest_refresh_is_retried_not_halted(w):
    peer = Peer()
    state = {'fail': False, 'calls': 0}

    def reviewed():
        state['calls'] += 1
        if state['fail']:
            state['fail'] = False
            raise EnrollmentRefused('service_unavailable')
        return w.pin, MANIFEST

    client = _client(w, w.http, peer, reviewed)
    try:
        client.start()
        eventually(lambda: peer.connections == 1)
        connect(w, peer)
        state['fail'] = True                # the ten-minute refresh fails once while connected
        eventually(lambda: peer.connections == 2, timeout=8)
        assert client.running
    finally:
        peer.stop.set()  # the client closes its socket abruptly; that reset is not a peer error
        assert client.stop()
        peer.close()


@pytest.mark.parametrize('status', [200, 400, 404, 429])
def test_unverifiable_non_5xx_reply_still_halts(w, status):
    def update(_s, h, raw):
        return status, {'content-type': 'application/json'}, b'{"v":1}'
    w.http_update = update
    client = w.client(socket_factory=lambda: pytest.fail('unverified ticket used'))
    client.start()
    eventually(lambda: not client.running)
    assert client.reason == 'response_unverified'


@pytest.mark.parametrize('reason', ['service_key_changed', 'revoked', 'vault_locked', 'stale_generation'])
def test_halt_reasons_still_halt_the_worker(w, reason):
    def reviewed():
        raise EnrollmentRefused(reason)
    peer = Peer()
    client = _client(w, w.http, peer, reviewed)
    try:
        client.start()
        eventually(lambda: not client.running)
        assert client.reason == reason and peer.connections == 0
    finally:
        peer.stop.set()
        client.stop()
        peer.close()


def test_unexpected_exception_type_is_logged_and_still_retried(w, monkeypatch):
    """MB11: a code defect in the session loop is retried like a dropped
    socket, but its type must be in the log or it reads as a network fault."""
    from mc import agent_mail_pull_client as pull
    lines = []
    monkeypatch.setattr(pull, '_log', lambda text, **kw: lines.append(text))
    client = w.client(socket_factory=lambda: pytest.fail('no socket expected'))

    def defect():
        raise KeyError('row text that must not be logged')
    monkeypatch.setattr(client, '_session', defect)
    client.start()
    try:
        eventually(lambda: any('connection_lost' in line for line in lines))
        assert client.running  # retried under backoff, not halted
        assert any('KeyError' in line for line in lines)
        assert not any('row text' in line for line in lines)
    finally:
        assert client.stop()
