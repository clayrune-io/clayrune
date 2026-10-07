"""Saved API-key /v1/models GET; no speculative endpoint for pagination or pricing."""
from __future__ import annotations

from mc import desk_engines as engines
from mc.desk_model_discovery.common import MAX_BYTES, MAX_MODELS, fail, project, rows


def discover_models(creds: engines._Creds, *, kind: str, query: str = '') -> list[dict]:
    out = engines._json_call('GET', engines._OPENAI_BASE + '/v1/models',
                             {'Authorization': f'Bearer {creds.secret}'}, free=True, max_bytes=MAX_BYTES)
    items = rows(out, 'data')
    if len(items) > MAX_MODELS or out.get('has_more') is True:
        fail('The service returned an incomplete model catalogue')
    found = []
    for item in items:
        mid = item.get('id')
        if not isinstance(mid, str):
            fail('The service returned an invalid model identifier')
        classified = 'image' if mid.startswith(('gpt-image-', 'dall-e-')) else (
            'video' if mid.startswith('sora-') else None)
        model = project(mid, classified, mid)
        if classified == kind and model is None:
            fail('The service returned an invalid media model identifier')
        if model and classified == kind and (not query or query.casefold() in mid.casefold()):
            model['kind_basis'] = 'model_id_family'
            found.append(model)
    return found
