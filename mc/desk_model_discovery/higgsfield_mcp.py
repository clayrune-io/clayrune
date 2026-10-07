"""Higgsfield models_explore list/search only; no generation or media tools."""
from __future__ import annotations

from mc import desk_engines as engines
from mc.desk_higgsfield_mcp import HiggsfieldMcpAdapter
from mc.desk_model_discovery.common import MAX_MODELS, MAX_PAGES, cursor, fail, project, rows


def discover_models(creds: engines._Creds, *, kind: str, query: str = '') -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    after = ''
    adapter = HiggsfieldMcpAdapter()
    for _ in range(MAX_PAGES):
        args: dict = {'action': 'search' if query else 'list', 'type': kind, 'limit': 100}
        if query:
            args['query'] = query
        if after:
            args['after'] = after
        out = engines._mcp_data(adapter._call(creds, 'models_explore', args, free=True))
        for item in rows(out, 'items'):
            if item.get('output_type') not in ('image', 'video', 'audio', '3d'):
                fail('The service returned a model with no recognized output type')
            model = project(item.get('id'), item.get('output_type'), item.get('name'), raw=item)
            if item.get('output_type') == kind and model is None:
                fail('The service returned an invalid media model identifier')
            if model and model['kind'] == kind:
                found.append(model)
        if len(found) > MAX_MODELS:
            fail('The service model catalogue exceeds the size limit')
        after = cursor(out.get('next_page_token'), seen)
        if not after:
            if out.get('has_more') is True:
                fail('The service omitted its next model page')
            return found
    fail('The service model catalogue exceeds the page limit')
    return []
