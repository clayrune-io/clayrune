"""SF8 (MC-1179 pass 11): the stdio server must speak UTF-8 whatever the locale.

On Windows `sys.stdin`/`sys.stdout` default to the locale code page (cp1252),
so a reply holding a character outside it killed the server and non-ASCII
arguments arrived as mojibake. The in-memory stream tests in
test_clayrune_mcp_protocol.py have no encoding and cannot see that, so this
starts the real server the way mc/session_mcp_config.py does (interpreter +
script path + args) with PYTHONUTF8 / PYTHONIOENCODING removed.

On a POSIX box with a UTF-8 locale the defect does not exist and these tests
pass before and after; the defect reproduces on Windows (cp1252 locale), and
on POSIX if the test forces a C locale below.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mc import session_mcp_config

ARROW_EMOJI = 'next → step \U0001F680 done'
ARGUMENT = 'dash — arrow → “hello”'


class _Stub(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):  # keep pytest output clean
        pass

    def _send(self, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self._send({'events': [{'summary': ARROW_EMOJI}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)).decode('utf-8'))
        if self.path.endswith('/api/clayrune-mcp/identity'):
            self._send({'ok': True, 'session_id': 'sess1', 'project_id': body['project_id'], 'label': 'me'})
            return
        _Stub.seen.append(body)
        self._send({'ok': True})


@pytest.fixture()
def stub_server():
    _Stub.seen = []
    srv = ThreadingHTTPServer(('127.0.0.1', 0), _Stub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def _launch(port):
    os.environ['MC_PORT'] = str(port)  # _base_url prefers it over config
    try:
        spec = session_mcp_config.server_spec({'id': 'proj1'}, {})
    finally:
        os.environ.pop('MC_PORT', None)
    assert spec is not None, 'server_spec returned None'
    argv = [spec['command'], *spec['args']]
    env = {k: v for k, v in os.environ.items()
           if k not in ('PYTHONUTF8', 'PYTHONIOENCODING', 'MC_PORT')}
    if os.name != 'nt':  # force the non-UTF-8 default a Windows box has
        env['LC_ALL'] = 'C'
        env['LANG'] = 'C'
        env['PYTHONCOERCECLOCALE'] = '0'
    # bytes in, bytes out: the test, not the child's locale, decides the encoding
    return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)


def _rpc(proc, msg):
    proc.stdin.write((json.dumps(msg, ensure_ascii=False) + '\n').encode('utf-8'))
    proc.stdin.flush()
    line = proc.stdout.readline()
    assert line, 'server closed stdout: ' + proc.stderr.read().decode('utf-8', 'replace')
    return json.loads(line.decode('utf-8'))


def _close(proc):
    try:
        proc.stdin.close()
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
        for s in (proc.stdout, proc.stderr):
            s.close()


def _init(proc):
    _rpc(proc, {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                           'clientInfo': {'name': 't', 'version': '0'}}})
    proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    proc.stdin.flush()


def test_reply_with_arrow_and_emoji_survives_and_server_keeps_answering(stub_server):
    proc = _launch(stub_server)
    try:
        _init(proc)
        reply = _rpc(proc, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                            'params': {'name': 'clayrune_coord_events', 'arguments': {}}})
        text = reply['result']['content'][0]['text']
        assert ARROW_EMOJI in text
        pong = _rpc(proc, {'jsonrpc': '2.0', 'id': 3, 'method': 'ping'})
        assert pong['id'] == 3 and 'result' in pong
    finally:
        _close(proc)


def test_argument_with_em_dash_and_curly_quotes_reaches_the_handler_unchanged(stub_server):
    proc = _launch(stub_server)
    try:
        _init(proc)
        reply = _rpc(proc, {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                            'params': {'name': 'clayrune_coord_publish',
                                       'arguments': {'type': 'intention', 'summary': ARGUMENT}}})
        assert reply['result']['isError'] is False, reply
        assert [b['summary'] for b in _Stub.seen] == [ARGUMENT]
    finally:
        _close(proc)
