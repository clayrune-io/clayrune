"""Saved Gemini-key catalogue GETs; media kind is a conservative ID classification."""
from __future__ import annotations

import urllib.parse

from mc import desk_engines as engines
from mc.desk_model_discovery.common import MAX_BYTES, MAX_MODELS, MAX_PAGES, cursor, fail, project, rows


def discover_models(creds: engines._Creds, *, kind: str, query: str = '') -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()
    token = ''
    for _ in range(MAX_PAGES):
        params = {'pageSize': '1000'}
        if token:
            params['pageToken'] = token
        out = engines._json_call('GET', engines._GEMINI_BASE + '/v1beta/models?' +
                                 urllib.parse.urlencode(params), {'x-goog-api-key': creds.secret},
                                 free=True, max_bytes=MAX_BYTES)
        for item in rows(out, 'models'):
            mid = item.get('name')
            if not isinstance(mid, str) or not mid.startswith('models/'):
                fail('The service returned an invalid model resource name')
            mid = mid[7:]
            # models.list exposes methods, not an output-modality field. Do not
            # mistake ordinary generateContent chat models for image generation.
            classified = 'video' if mid.startswith('veo-') else (
                'image' if ('image' in mid and mid.startswith('gemini-')) or mid.startswith('imagen-') else None)
            model = project(mid, classified, item.get('displayName'))
            if classified == kind and model is None:
                fail('The service returned an invalid media model identifier')
            if model and classified == kind and (not query or query.casefold() in
                                                  (mid + ' ' + model['label']).casefold()):
                model['kind_basis'] = 'model_id_family'
                found.append(model)
        if len(found) > MAX_MODELS:
            fail('The service model catalogue exceeds the size limit')
        token = cursor(out.get('nextPageToken'), seen)
        if not token:
            return found
    fail('The service model catalogue exceeds the page limit')
    return []
