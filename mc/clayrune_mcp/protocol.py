"""Minimal MCP stdio transport: newline-delimited JSON-RPC 2.0.

Stdlib only. stdout carries protocol messages and nothing else; diagnostics go
to stderr. Notifications (no `id`) are never answered. A malformed line gets a
-32700 reply rather than killing the loop, so one bad frame cannot take the
tool surface away from a live session.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional, Protocol, TextIO

SUPPORTED_PROTOCOLS = ('2025-06-18', '2025-03-26', '2024-11-05')

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# A tool catalogue is tiny; one frame over this is a misbehaving client.
MAX_LINE_BYTES = 1024 * 1024


class RpcError(Exception):
    """Raised by a tool lookup to produce a JSON-RPC error (not a tool error)."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ToolHost(Protocol):
    """What the protocol loop needs from the tool layer."""

    def list_tools(self) -> List[Dict[str, Any]]: ...

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]: ...


def _ok(rid: Any, result: Any) -> Dict[str, Any]:
    return {'jsonrpc': '2.0', 'id': rid, 'result': result}


def _err(rid: Any, code: int, message: str) -> Dict[str, Any]:
    return {'jsonrpc': '2.0', 'id': rid, 'error': {'code': code, 'message': message}}


def negotiate_version(requested: Any) -> str:
    return requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]


def handle_message(req: Any, host: ToolHost, server_info: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """One decoded frame -> one response dict, or None for notifications."""
    if not isinstance(req, dict) or req.get('jsonrpc') != '2.0' or not isinstance(req.get('method'), str):
        rid = req.get('id') if isinstance(req, dict) else None
        return _err(rid, INVALID_REQUEST, 'invalid JSON-RPC request')
    method = req['method']
    is_notification = 'id' not in req
    rid = req.get('id')
    params = req.get('params')
    if params is not None and not isinstance(params, dict):
        return None if is_notification else _err(rid, INVALID_PARAMS, 'params must be an object')
    params = params or {}

    if is_notification:
        return None  # notifications/initialized, notifications/cancelled, ...
    if method == 'initialize':
        return _ok(rid, {'protocolVersion': negotiate_version(params.get('protocolVersion')),
                         'capabilities': {'tools': {'listChanged': False}},
                         'serverInfo': dict(server_info)})
    if method == 'ping':
        return _ok(rid, {})
    if method == 'tools/list':
        return _ok(rid, {'tools': host.list_tools()})
    if method == 'tools/call':
        name = params.get('name')
        args = params.get('arguments')
        if not isinstance(name, str) or not name:
            return _err(rid, INVALID_PARAMS, 'tools/call requires a tool name')
        if args is not None and not isinstance(args, dict):
            return _err(rid, INVALID_PARAMS, 'arguments must be an object')
        try:
            return _ok(rid, host.call_tool(name, args or {}))
        except RpcError as e:
            return _err(rid, e.code, e.message)
        except Exception as e:  # a bug in a tool must not kill the loop
            print(f'[clayrune-mcp] tool {name} crashed: {e!r}', file=sys.stderr, flush=True)
            return _err(rid, INTERNAL_ERROR, 'internal error')
    return _err(rid, METHOD_NOT_FOUND, f'unknown method: {method}')


def serve(host: ToolHost, server_info: Dict[str, str],
          stdin: Optional[TextIO] = None, stdout: Optional[TextIO] = None) -> None:
    """Read frames until EOF. EOF is the shutdown signal: the CLI closing our
    stdin ends the process; there is nothing to flush."""
    src: TextIO = stdin if stdin is not None else sys.stdin
    out: TextIO = stdout if stdout is not None else sys.stdout

    def emit(msg: Dict[str, Any]) -> None:
        out.write(json.dumps(msg, ensure_ascii=False, separators=(',', ':')) + '\n')
        out.flush()

    for line in src:
        line = line.strip()
        if not line:
            continue
        if len(line.encode('utf-8', 'replace')) > MAX_LINE_BYTES:
            emit(_err(None, INVALID_REQUEST, 'frame too large'))
            continue
        try:
            req = json.loads(line)
        except ValueError:
            emit(_err(None, PARSE_ERROR, 'parse error'))
            continue
        if isinstance(req, list):  # 2025-03-26 batching
            replies = [r for r in (handle_message(m, host, server_info) for m in req) if r is not None]
            if replies:
                out.write(json.dumps(replies, ensure_ascii=False, separators=(',', ':')) + '\n')
                out.flush()
            continue
        resp = handle_message(req, host, server_info)
        if resp is not None:
            emit(resp)
