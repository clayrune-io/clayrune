"""Developer API discovery is unavailable: no documented account model-list API.

The official docs index points to the console's model pages, and explicitly says
its supplementary OpenAPI file is not an authoritative catalogue. Do not invent
GET /models or scrape the console using API credentials.
"""
from __future__ import annotations

from mc import desk_engines as engines


def discover_models(creds: engines._Creds, *, kind: str, query: str = '') -> list[dict]:
    raise engines.Refused('discovery_unavailable',
                         'Higgsfield API-key model discovery has no verified list endpoint; '
                         'the curated models remain available as a fallback.', 501)
