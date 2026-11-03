"""Minimal stdio MCP transport for the adapter's one ephemeral dispatch tool."""
from __future__ import annotations

import json
import os
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

SCHEMA = {'name': 'dispatch_agent',
          'description': 'Start a hired project agent on a task. Returns a visible session; its result comes back to this campaign. Maximum two attempts this turn. Never retry an unknown outcome.',
          'inputSchema': {'type': 'object', 'properties': {
              'agent': {'type': 'string', 'description': 'Exact scope:name from the supplied hired roster'},
              'task': {'type': 'string', 'minLength': 1, 'maxLength': 12000}},
              'required': ['agent', 'task'], 'additionalProperties': False}}


def call(args: dict) -> dict:
    req = Request(os.environ['CLAYRUNE_DESK_TOOL_URL'], method='POST',
                  headers={'Content-Type': 'application/json', 'X-Desk-Turn': os.environ['CLAYRUNE_DESK_TURN']},
                  data=json.dumps(args).encode())
    try:
        with urlopen(req, timeout=90) as response:
            data = response.read().decode()
        return {'content': [{'type': 'text', 'text': data}]}
    except HTTPError as exc:
        return {'isError': True, 'content': [{'type': 'text', 'text': exc.read().decode()}]}
    except Exception as exc:
        return {'isError': True, 'content': [{'type': 'text', 'text':
                f'Dispatch outcome unknown ({type(exc).__name__}); do not retry. Check the Floor and campaign thread.'}]}


def respond(msg: dict) -> dict | None:
    if 'id' not in msg:
        return None
    method = msg.get('method')
    if method == 'initialize':
        result = {'protocolVersion': msg.get('params', {}).get('protocolVersion', '2024-11-05'),
                  'capabilities': {'tools': {}}, 'serverInfo': {'name': 'desk', 'version': '1'}}
    elif method == 'tools/list':
        result = {'tools': [SCHEMA]}
    elif method == 'tools/call' and msg.get('params', {}).get('name') == 'dispatch_agent':
        result = call(msg['params'].get('arguments') or {})
    elif method == 'ping':
        result = {}
    else:
        return {'jsonrpc': '2.0', 'id': msg['id'], 'error': {'code': -32601, 'message': 'Unknown method or tool'}}
    return {'jsonrpc': '2.0', 'id': msg['id'], 'result': result}


def main(argv: list[str] | None = None) -> int:
    for line in sys.stdin:
        try:
            out = respond(json.loads(line))
            if out is not None:
                print(json.dumps(out), flush=True)
        except Exception as exc:
            print(f'Desk MCP protocol failed: {type(exc).__name__}', file=sys.stderr, flush=True)
            return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
