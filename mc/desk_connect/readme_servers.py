"""The README fallback of `mcp_installer.extract_config`, rebuilt on the isolated path
(docs/DESK_SERVICE_PROFILES_SPEC.md §6.5, slice U1).

The old tier 3 ran `claude -p <prompt + README>` with the user's full tool set, so a
README that said "ignore the above and run ..." was read by a model that could run it
(docs/UNTRUSTED_INPUT_SURFACE.md finding #1, the highest blast radius on the list). It is
gone. README prose now goes ONLY to `parameter_classifier.classify`, i.e. the certified
toolless `run_text_transform`. When that path is unavailable, nothing takes its place: the
result says so and the caller shows the manual form.

Returns the `{name: server}` shape `extract_config` already hands to the installer, built
from values the classifier proved were in the README. Credential names become EMPTY env or
header entries (never a value), so `detect_secrets` asks the person for them.
"""
from __future__ import annotations

from typing import Any

from mc.desk_connect import parameter_classifier as pc

README_TIMEOUT_S = 45


def _server(alt: dict) -> dict[str, Any] | None:
    f = alt['fields']
    cmd = (f.get('command') or {}).get('value')
    url = (f.get('url') or {}).get('value')
    if alt['route_type'] != 'mcp':
        return None
    out: dict[str, Any]
    if alt['transport'] == 'stdio' and cmd:
        out = {'command': cmd, 'args': list((f.get('args') or {}).get('value') or [])}
        env = {c['name']: '' for c in alt['credentials'] if c['placement'] == 'env'}
        if env:
            out['env'] = env
        return out
    if alt['transport'] in ('streamable_http', 'sse') and url:
        out = {'type': 'sse' if alt['transport'] == 'sse' else 'http', 'url': url}
        headers = {c['name']: '' for c in alt['credentials'] if c['placement'] == 'header'}
        if headers:
            out['headers'] = headers
        return out
    return None


def servers_from_readme(readme: str, *, label: str = '', transform=None) -> dict[str, Any]:
    """`{'servers': {name: cfg}, 'status': 'found'|'none'|'unavailable', 'code': ...}`.
    Never raises, never reaches any model except through the certified toolless
    transform, never retries."""
    if not (readme or '').strip():
        return {'servers': {}, 'status': 'none', 'code': None}
    evidence = [{'id': 'e1', 'kind': 'readme', 'label': 'README', 'text': readme}]
    answer = pc.classify(evidence, timeout=README_TIMEOUT_S, label=label, transform=transform)
    if not answer.get('ok'):
        return {'servers': {}, 'status': 'unavailable', 'code': answer.get('code')}
    servers: dict[str, Any] = {}
    for n, alt in enumerate(pc.to_alternatives(answer)):
        cfg = _server(alt)
        if cfg is not None:
            servers[f'server{n + 1}' if n else 'server'] = cfg
    return {'servers': servers, 'status': 'found' if servers else 'none', 'code': None}
