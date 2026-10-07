"""Read-only Higgsfield tool discovery, with one deadline across all RPCs."""
from __future__ import annotations

import time


def collect(token: str, *, timeout: float = 15.0) -> list[dict]:
    from mc import desk_engines as engines

    deadline = time.monotonic() + timeout

    def post(body, rid):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise engines.EngineError('engine', 'Higgsfield schema capture timed out')
        result = engines._mcp_post(token, body, expect_id=rid, timeout=remaining)
        if time.monotonic() >= deadline:
            raise engines.EngineError('engine', 'Higgsfield schema capture timed out')
        return result

    post({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
        'protocolVersion': engines._MCP_PROTOCOL, 'capabilities': {},
        'clientInfo': {'name': 'Clayrune', 'version': '1'}}}, 1)
    post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, None)
    tools: list[dict] = []
    cursor = None
    seen: set[str] = set()
    for rid in range(2, 52):
        body: dict = {'jsonrpc': '2.0', 'id': rid, 'method': 'tools/list'}
        if cursor:
            body['params'] = {'cursor': cursor}
        page = post(body, rid)
        if not isinstance(page, dict) or not isinstance(page.get('tools'), list):
            raise engines.EngineError('engine', 'Higgsfield returned a malformed tool list')
        tools.extend(t for t in page['tools'] if isinstance(t, dict))
        cursor = page.get('nextCursor')
        if cursor is None or cursor == '':
            return tools
        if not isinstance(cursor, str) or cursor in seen:
            raise engines.EngineError('engine', 'Higgsfield returned an invalid or repeated tool-list cursor')
        seen.add(cursor)
    raise engines.EngineError('engine', 'Higgsfield tool listing exceeded 50 pages')


def refresh(*, token: str | None = None, project_id: str | None = None,
            unattended: bool = False) -> None:
    from mc import desk_oauth
    from mc.desk_connect import higgsfield_mcp_snapshot

    if token is None:
        token = desk_oauth.access_token('higgsfield', consumer='desk_engine_schemas',
                                        project_id=project_id, unattended=unattended)
    higgsfield_mcp_snapshot.capture(collect(token))
