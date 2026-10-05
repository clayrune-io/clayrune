"""Version 1 of the registry, in and out of the profile model (docs/DESK_SERVICE_PROFILES_SPEC.md
section 7; slice P1).

Two directions, both pure and offline:

* `snapshot_from_v1` converts a version 1 `registry.json` (flat `options[{method, support,
  title, evidence, guidance, open?}]`) into profiles IN MEMORY, validates them exactly as a
  shipped profile is validated, and returns the same snapshot the loader returns. Nothing is
  written. Each row becomes one route whose evidence is LEGACY: the row's old `evidence` text,
  result `unverified`, every date null. The time of conversion is never a verification time.
* `project_service` / `v1_view` render a profile back into the version 1 record the screens and
  `commit` read today, so the live flow keeps working on the new data. Only a route that
  carries a `connect_method` is projected, and only when that method names exactly ONE route of
  the service: once two routes share a method a version 1 selection is ambiguous, and the
  projection offers neither (`route_for_method` says so) instead of picking the first row.

`LEGACY_COMMON_OPTIONS` is the old global "MCP server: not available yet" row. The projection
keeps emitting it so the Method step is unchanged in this slice; the spec removes the blanket
row once each profile carries its own MCP evidence (slice P2 owns that screen change).
"""
from __future__ import annotations

from typing import Any

from mc.desk_connect import profile_loader as loader
from mc.desk_connect import profile_schema as ps
from mc.desk_connect.profile_schema import ProfileError, need

LEGACY_COMMON_OPTIONS: list[dict] = [{
    'method': 'mcp',
    'support': 'info_only',
    'title': 'MCP server',
    'evidence': "Clayrune's built-in list",
    'guidance': 'Connecting a service through an MCP server is not available yet.',
}]

_V1_SUPPORT = ('available', 'restricted', 'info_only')
_V1_OPTION_FIELDS = {'method', 'support', 'title', 'evidence', 'guidance', 'open'}
# How a version 1 method reads in the new vocabulary when nothing more is known about the row.
_TRANSPORT_AUTH = {'oauth': ('api', 'oauth'), 'api_key': ('api', 'api_key'),
                   'mcp': ('mcp', 'unknown'), 'browser_signin': ('browser', 'browser_signin')}
_LEGACY_NOTE = 'Imported from the version 1 registry; no fact in it was re-verified.'


class AmbiguousMethod(ValueError):
    """A version 1 method token names more than one route of the service."""


def check_v1_option(o: Any, where: str) -> dict:
    need(isinstance(o, dict), where, 'an option must be an object')
    unknown = sorted(set(o) - _V1_OPTION_FIELDS)
    need(not unknown, where, f'unknown field(s): {", ".join(unknown)}')
    need(o.get('method') in ps.CONNECT_METHODS, where, f'method must be one of {ps.CONNECT_METHODS}')
    need(o.get('support') in _V1_SUPPORT, where, f'support must be one of {_V1_SUPPORT}')
    for k in ('title', 'evidence', 'guidance'):
        need(isinstance(o.get(k), str) and o[k].strip(), where, f'{k} is required text')
    return dict(o)


def _legacy_profile(s: Any, i: int) -> dict:
    where = f'services[{i}]'
    need(isinstance(s, dict), where, 'a service must be an object')
    opts = [check_v1_option(o, f'{where}.options[{j}]') for j, o in enumerate(s.get('options') or [])]
    need(opts, where, 'a service needs at least one option')
    sid = s.get('id')
    routes, evidence, ev_ids, seen = [], [], {}, {}
    for o in opts:
        method = o['method']
        seen[method] = seen.get(method, 0) + 1
        transport, auth = _TRANSPORT_AUTH[method]
        route: dict[str, Any] = {
            'id': f'{sid}-{method}'.replace('_', '-') + (f'-{seen[method]}' if seen[method] > 1 else ''),
            'title': o['title'], 'guidance': o['guidance'], 'transport': transport, 'support': o['support'],
            'connect_method': method,
            'auth': [{'type': auth, 'params': [], 'placement': None, 'scopes': [], 'issuer': None, 'registration': None, 'claim': None}],
            'coverage': [], 'requirements': [], 'limits': [], 'cost': {'basis': 'unknown', 'items': []}}
        if transport == 'mcp':
            route['mcp_protocol'] = 'unknown'
        if o.get('open') is not None:
            route['adapter_id'] = o['open']
        elif o['support'] == 'available' and method == 'mcp':
            route['catalogue_id'] = sid          # the reviewed-package catalogue is keyed by service id
        cls = 'clayrune_catalogue' if route.get('catalogue_id') else 'clayrune_builtin'
        key = (o['evidence'], cls)
        if key not in ev_ids:
            ev_ids[key] = f'legacy-v1-{len(ev_ids) + 1}'
            evidence.append({'id': ev_ids[key], 'label': o['evidence'], 'url': None, 'publisher_class': cls, 'published': None,
                             'retrieved': None, 'attempted': None, 'claim_ids': [], 'digest': None, 'result': 'unverified'})
        route['evidence'] = [ev_ids[key]]
        routes.append(route)
    return {'schema_version': ps.SCHEMA_VERSION, 'service_id': sid, 'revision': 1, 'label': s.get('label'),
            'aliases': s.get('aliases') or [], 'home_url': s.get('url'), 'hosts': s.get('hosts'), 'account_kinds': [],
            'purposes': [], 'routes': routes, 'evidence': evidence, 'change_summary': _LEGACY_NOTE, 'supersedes': None}


def snapshot_from_v1(raw: Any) -> dict:
    """The snapshot of a version 1 registry file, converted in memory, plus its own
    `common_options`. Raises `ProfileError`; never returns a partial one."""
    need(isinstance(raw, dict) and raw.get('version') == 1, 'registry', 'version must be 1')
    blocked = raw.get('blocked_domains')
    need(isinstance(blocked, list) and all(ps.is_host(d) for d in blocked), 'blocked_domains', 'a list of domains')
    common = [check_v1_option(o, f'common_options[{i}]') for i, o in enumerate(raw.get('common_options') or [])]
    items = []
    for i, s in enumerate(raw.get('services') or []):
        items.append((f'services[{i}]', ps.check_profile(_legacy_profile(s, i), f'services[{i}]')))
    snap = loader.assemble(items, blocked, 1)
    snap['common_options'] = common
    return snap


def _method_routes(profile: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in profile['routes']:
        if r.get('connect_method'):
            out.setdefault(r['connect_method'], []).append(r)
    return out


def route_for_method(profile: dict, method: object) -> dict:
    """The one route a version 1 method token selects. `KeyError` if the service has none,
    `AmbiguousMethod` if several routes share the token (the caller must ask for a refreshed
    version 2 draft; it must not take the first)."""
    rows = _method_routes(profile).get(method) if isinstance(method, str) else None
    if not rows:
        raise KeyError(method)
    if len(rows) > 1:
        raise AmbiguousMethod(f'{profile["service_id"]}: {len(rows)} routes use the method "{method}"')
    return rows[0]


def project_service(profile: dict) -> dict:
    """The version 1 service record: identity plus `options` and `ambiguous_methods`."""
    evidence = {e['id']: e for e in profile['evidence']}
    options, ambiguous = [], []
    for method, rows in _method_routes(profile).items():
        if len(rows) > 1:
            ambiguous.append(method)
    for r in profile['routes']:
        m = r.get('connect_method')
        if not m or m in ambiguous:
            continue
        row = {'method': m, 'support': r['support'], 'title': r['title'], 'evidence': evidence[r['evidence'][0]]['label'],
               'guidance': r['guidance']}
        if r.get('adapter_id'):
            row['open'] = r['adapter_id']
        options.append(row)
    return {'id': profile['service_id'], 'label': profile['label'], 'aliases': list(profile['aliases']), 'url': profile['home_url'],
            'hosts': list(profile['hosts']), 'options': options, 'ambiguous_methods': ambiguous}


def v1_view(snapshot: dict) -> dict:
    """The snapshot as the version 1 file shape (what the screens and the smoke read)."""
    services = []
    for p in snapshot['profiles']:
        rec = project_service(p)
        rec.pop('ambiguous_methods')
        services.append(rec)
    return {'version': 1, 'blocked_domains': list(snapshot['blocked_domains']),
            'common_options': [dict(o) for o in snapshot.get('common_options', LEGACY_COMMON_OPTIONS)], 'services': services}
