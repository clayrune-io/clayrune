"""P5 transport boundaries without a production service or credentials."""
from __future__ import annotations

import importlib
import io
from pathlib import Path
import sys
import threading

from flask import Flask, jsonify, request
import pytest
from werkzeug.serving import make_server, WSGIRequestHandler

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
transport = importlib.import_module('test_run_transport')


def test_loopback_bypasses_proxies_and_never_follows_bearer_redirect(monkeypatch):
    app = Flask(__name__)
    destinations = []

    @app.get('/api/config')
    def config():
        return jsonify(test_run_progress_enabled=True)

    @app.post('/redirect')
    def redirect():
        return '', 307, {'Location': '/destination'}

    @app.post('/destination')
    def destination():
        destinations.append(request.headers.get('Authorization'))
        return jsonify(ok=True)

    class Quiet(WSGIRequestHandler):
        def log_request(self, *args, **kwargs):
            pass

    server = make_server('127.0.0.1', 0, app, request_handler=Quiet)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')
        monkeypatch.setenv('http_proxy', 'http://127.0.0.1:1')
        monkeypatch.setenv('NO_PROXY', '')
        monkeypatch.setenv('no_proxy', '')
        client = transport.LocalClient(server.server_port, 'project')
        assert client.request('GET', '/api/config')['test_run_progress_enabled'] is True
        with pytest.raises(transport.TransportError, match='http_307'):
            client.request('POST', '/redirect', {}, 'synthetic-test-receipt')
        assert len(destinations) == 0
    finally:
        server.shutdown()
        thread.join(2)
        server.server_close()


@pytest.mark.parametrize('raw,code', [
    (b'[]', 'invalid_response'), (b'null', 'invalid_response'),
    (b'not JSON', 'transport_unavailable'), (b'\xff', 'transport_unavailable'),
    (b'x' * (1024 * 1024 + 1), 'response_too_large'),
], ids=['array', 'null', 'malformed', 'invalid-utf8', 'oversized'])
def test_responses_are_bounded_and_errors_do_not_echo_content(raw, code):
    class Opener:
        def open(self, *args, **kwargs):
            return io.BytesIO(raw)
    client = transport.LocalClient(5199, 'project')
    client.opener = Opener()
    with pytest.raises(transport.TransportError) as caught:
        client.request('GET', '/fixture')
    assert str(caught.value) == code
