#!/usr/bin/env python3
"""Stdlib-only stdio MCP server for the live pass's `mcp` cell.

Exposes one tool, `fixture_token`, returning the token given as argv[1]. The
driver derives that token from its run id, so a reply containing it can only
have come from a real tool call. Newline-delimited JSON-RPC 2.0 (MCP stdio).
"""
import json
import sys

TOKEN = sys.argv[1] if len(sys.argv) > 1 else 'NOTOKEN'


def reply(msg_id, result=None, error=None):
    body = {'jsonrpc': '2.0', 'id': msg_id}
    body['error' if error else 'result'] = error or result
    sys.stdout.write(json.dumps(body) + '\n')
    sys.stdout.flush()


for raw in sys.stdin:
    try:
        msg = json.loads(raw)
    except ValueError:
        continue
    method, mid = msg.get('method'), msg.get('id')
    if mid is None:
        continue  # notification
    if method == 'initialize':
        reply(mid, {'protocolVersion': (msg.get('params') or {}).get('protocolVersion', '2024-11-05'),
                    'capabilities': {'tools': {}},
                    'serverInfo': {'name': 'clayrune_fixture', 'version': '1'}})
    elif method == 'tools/list':
        reply(mid, {'tools': [{'name': 'fixture_token', 'description': 'Returns the run token.',
                               'inputSchema': {'type': 'object', 'properties': {}}}]})
    elif method == 'tools/call':
        reply(mid, {'content': [{'type': 'text', 'text': TOKEN}]})
    else:
        reply(mid, error={'code': -32601, 'message': 'method not found'})
