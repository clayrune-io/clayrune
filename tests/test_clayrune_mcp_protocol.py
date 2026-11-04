"""Protocol conformance for the first-party `clayrune` MCP server (BUILTINS-7a).

Covers the stdio JSON-RPC layer only (mc/clayrune_mcp/protocol.py): handshake,
tools/list, tools/call, and the error paths. Tool behaviour and identity are in
test_clayrune_mcp_tools.py.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

from mc.clayrune_mcp import protocol, tools

SERVER = Path(__file__).resolve().parents[1] / 'mc' / 'clayrune_mcp' / 'server.py'
INFO = {'name': 'clayrune', 'version': 'test'}

EXPECTED_TOOLS = [
    'clayrune_list_agents', 'clayrune_send_message', 'clayrune_dispatch_agent',
    'clayrune_coord_events', 'clayrune_coord_publish', 'clayrune_delivery_status',
    'clayrune_start_job', 'clayrune_job_status',
]


class _Host:
    def __init__(self):
        self.calls = []

    def list_tools(self):
        return [{'name': 'echo', 'description': 'd', 'inputSchema': {'type': 'object'}}]

    def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == 'boom':
            raise RuntimeError('secret detail')
        if name == 'nope':
            raise protocol.RpcError(protocol.INVALID_PARAMS, 'unknown tool: nope')
        return {'content': [{'type': 'text', 'text': 'ok'}], 'isError': False}


def _run(frames, host=None):
    host = host or _Host()
    out = io.StringIO()
    protocol.serve(host, INFO, io.StringIO('\n'.join(frames) + '\n'), out)
    return [json.loads(line) for line in out.getvalue().splitlines()], host


def _req(rid, method, params=None):
    msg = {'jsonrpc': '2.0', 'id': rid, 'method': method}
    if params is not None:
        msg['params'] = params
    return json.dumps(msg)


def test_initialize_negotiates_known_version_and_declares_tools():
    out, _ = _run([_req(1, 'initialize', {'protocolVersion': '2025-03-26', 'capabilities': {}})])
    res = out[0]['result']
    assert res['protocolVersion'] == '2025-03-26'
    assert 'tools' in res['capabilities']
    assert res['serverInfo'] == INFO


def test_initialize_unknown_version_falls_back_to_newest_supported():
    out, _ = _run([_req(1, 'initialize', {'protocolVersion': '1999-01-01'})])
    assert out[0]['result']['protocolVersion'] == protocol.SUPPORTED_PROTOCOLS[0]


def test_notifications_get_no_response():
    out, _ = _run([json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}),
                   _req(2, 'ping')])
    assert [m['id'] for m in out] == [2]


def test_tools_list_and_call_roundtrip():
    out, host = _run([_req(1, 'tools/list'),
                      _req(2, 'tools/call', {'name': 'echo', 'arguments': {'a': 1}})])
    assert out[0]['result']['tools'][0]['name'] == 'echo'
    assert out[1]['result']['content'][0]['text'] == 'ok'
    assert host.calls == [('echo', {'a': 1})]


def test_unknown_method_is_method_not_found():
    out, _ = _run([_req(1, 'resources/list')])
    assert out[0]['error']['code'] == protocol.METHOD_NOT_FOUND


def test_unknown_tool_is_invalid_params_protocol_error():
    out, _ = _run([_req(1, 'tools/call', {'name': 'nope'})])
    assert out[0]['error']['code'] == protocol.INVALID_PARAMS


def test_tool_crash_is_internal_error_without_leaking_detail():
    out, _ = _run([_req(1, 'tools/call', {'name': 'boom'}), _req(2, 'ping')])
    assert out[0]['error']['code'] == protocol.INTERNAL_ERROR
    assert 'secret detail' not in json.dumps(out[0])
    assert out[1]['result'] == {}, 'loop must survive a crashing tool'


def test_bad_frames_do_not_kill_the_loop():
    out, _ = _run(['{not json', '"str"',
                   _req(3, 'tools/call', {'arguments': {}}),
                   _req(4, 'tools/call', {'name': 'echo', 'arguments': [1]}),
                   _req(5, 'ping')])
    codes = [m.get('error', {}).get('code') for m in out]
    assert codes[0] == protocol.PARSE_ERROR
    assert codes[1] == protocol.INVALID_REQUEST
    assert codes[2] == protocol.INVALID_PARAMS and codes[3] == protocol.INVALID_PARAMS
    assert out[4]['result'] == {}


def test_batch_returns_array_without_notification_entries():
    batch = json.dumps([json.loads(_req(1, 'ping')),
                        {'jsonrpc': '2.0', 'method': 'notifications/initialized'}])
    out, _ = _run([batch])
    assert out == [[{'jsonrpc': '2.0', 'id': 1, 'result': {}}]]


def test_real_tool_table_lists_the_eight_tools_with_closed_schemas():
    gw = tools.Gateway('http://127.0.0.1:1', 'p1')
    listing = tools.ClayruneToolHost(gw).list_tools()
    assert [t['name'] for t in listing] == EXPECTED_TOOLS
    for t in listing:
        assert t['description']
        assert t['inputSchema']['type'] == 'object'
        assert t['inputSchema']['additionalProperties'] is False


def test_server_script_speaks_stdio_when_launched_by_path():
    """The exact launch shape the CLI uses: python <path> --project ... --base-url ..."""
    frames = '\n'.join([
        _req(1, 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}}),
        json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}),
        _req(2, 'tools/list'),
    ]) + '\n'
    cp = subprocess.run([sys.executable, str(SERVER), '--project', 'p1',
                         '--base-url', 'http://127.0.0.1:1'],
                        input=frames, capture_output=True, text=True, timeout=30)
    assert cp.returncode == 0, cp.stderr
    msgs = [json.loads(line) for line in cp.stdout.splitlines()]
    assert [m['id'] for m in msgs] == [1, 2], 'stdout must carry protocol frames only'
    assert [t['name'] for t in msgs[1]['result']['tools']] == EXPECTED_TOOLS


def test_server_refuses_a_non_loopback_base_url():
    cp = subprocess.run([sys.executable, str(SERVER), '--project', 'p1',
                         '--base-url', 'http://example.com:5199'],
                        input='', capture_output=True, text=True, timeout=30)
    assert cp.returncode == 2
    assert 'loopback' in cp.stderr
