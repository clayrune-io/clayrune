"""The Connection step's data: what a service can be connected BY, as three types
(docs/desk_v1/CONNECT_FLOW_SIMPLIFY.md sections 3-7 and 10; MC-1062 ticket 01).

Read-only, offline and value-free: it reads a profile (`profile_schema`), asks the setup
providers what they support, asks `route_readiness` what the Desk can run, and opens no
vault, no network and no browser. It writes nothing and never returns a secret.

    Sign in   a browser route that declares a sign-in page            (transport `browser`)
    API       a provider-backed API route, or API details to save     (transport `api`)
    MCP       a reviewed package, a user-chosen npm package or remote
              server, or a detected PyPI package                      (transport `mcp`)

The projection is built from each ROUTE and each custom-connection kind directly. It does not
read the version 1 `connect_method` projection (`profile_compat.project_service`), which omits
a route without that field, so a browser sign-in route needs no `connect_method` to appear.

Two questions, answered separately on every variant:

  * `setup`   what the Connect flow can do for it. `mode` is `full` (a flow that exists and
              ends in a saved connection), `reference_only` (details can be saved, nothing
              connects) or `none`.
  * `runtime` what the Desk then runs through it. `purposes` maps a purpose to how
              `route_readiness` executes it today; `desk_executes` is true only when it is
              non-empty. A saved sign-in or a registered server proves neither.

Q1 (Dave, 2026-10-06): a variant whose setup `mode` is not `full` is HIDDEN from `picker` and
listed under `details.unavailable` with an `explanation` (`code` + `text`). Nothing is marked
available to make a button appear: a flag in a profile is read, never promoted.

Recognition is not support (`registry` module docstring): an unknown address gets the custom
MCP variants and the reference fallback, and no sign-in variant, because Clayrune types a
login only into a page a reviewed profile declares.
"""
from __future__ import annotations

from typing import Any, Callable

from mc.desk_connect import providers as _providers
from mc.desk_connect import registry as _registry
from mc.desk_connect import route_readiness as _ready

TYPE_ORDER = ('signin', 'api', 'mcp')
TYPE_LABELS = {'signin': 'Sign in (username/password)', 'api': 'API / developer app', 'mcp': 'MCP server'}
_TYPE_OF_TRANSPORT = {'browser': 'signin', 'api': 'api', 'mcp': 'mcp'}
MAX_PICKER = 4

# The existing endpoints each user-supplied variant is set up through (named, not called).
_CUSTOM_NPM_ENDPOINTS = {'review': '/api/desk/connect/custom/review', 'commit': '/api/desk/connect/custom/commit',
                         'detect': '/api/desk/connect/detect'}
_CUSTOM_REMOTE_ENDPOINTS = {'review': '/api/desk/connect/custom/remote/review', 'commit': '/api/desk/connect/custom/commit',
                            'check': '/api/desk/connect/custom/remote/check'}
_REFERENCE_ENDPOINT = '/api/desk/connect/commit'

ProviderFor = Callable[[str], Any]
Executor = Callable[[str, str, str], 'str | None']


def _reference() -> dict:
    """The fallback every address has: a saved note for agents. Not a connection type."""
    f = _registry.FALLBACK_OPTION
    return {'method': f['method'], 'title': f['title'], 'text': f['guidance'], 'endpoint': _REFERENCE_ENDPOINT}


def _explain(code: str, text: str) -> dict:
    return {'code': code, 'text': text}


def _evidence(profile: dict, route: dict) -> list[dict]:
    by_id = {e['id']: e for e in profile['evidence']}
    return [{'id': i, 'label': by_id[i]['label'], 'url': by_id[i]['url'], 'result': by_id[i]['result']} for i in route['evidence']]


def _route_variant(profile: dict, route: dict, provider_for: ProviderFor, executor: Executor) -> dict:
    """One profile route as a variant of its type. `setup.mode` is derived below, never copied
    from `route['support']` alone: `available` also needs a provider that supports the route's
    method (the rule `methods.is_connectable` applies), and a browser route needs a declared
    sign-in page."""
    sid = profile['service_id']
    transport = route['transport']
    method = route.get('connect_method')
    covered = [c for c in route['coverage'] if c['status'] in _ready.COVERED]
    purposes = {}
    for p in dict.fromkeys(c['purpose'] for c in covered):
        via = executor(sid, route['id'], p)
        if via:
            purposes[p] = via
    explanation = None
    setup: dict[str, Any] = {'mode': 'none', 'via': None}
    support = route['support']
    if support == 'manual':
        explanation = _explain('manual', 'A person finishes this in their own browser. Clayrune connects to nothing through it.')
    elif transport == 'browser':
        signin = route.get('signin')
        if signin and any(a['type'] == 'browser_signin' for a in route['auth']):
            setup = {'mode': 'full', 'via': 'browser_signin', 'signin': {'url': signin['url'], 'hosts': list(signin['hosts'])}}
        else:
            explanation = _explain('no_signin_declared', 'This route declares no sign-in page, so Clayrune will not type a login for it.')
    elif support == 'available':
        prov = provider_for(sid)
        if method and prov is not None and prov.supports(method):
            setup = {'mode': 'full', 'via': 'provider', 'method': method, 'provider': sid,
                     'signs_in': method in prov.signs_in}
        else:
            explanation = _explain('missing_adapter', 'This route is listed as available but no setup adapter supports it, so it cannot be connected here.')
    elif support == 'restricted':
        explanation = _explain('restricted', f'The provider limits this route and Clayrune has no flow for it. {route["guidance"]}')
    else:
        explanation = _explain('missing_adapter', f'Clayrune has no adapter that sets up or runs this route yet. {route["guidance"]}')
    runtime: dict[str, Any] = {'purposes': purposes, 'desk_executes': bool(purposes), 'adapter': route.get('adapter_id') or route.get('catalogue_id')}
    if setup['mode'] == 'full' and not purposes:
        runtime['note'] = 'Setup works. The Desk does not run this route for any purpose yet.'
    return {
        'id': route['id'], 'type': _TYPE_OF_TRANSPORT[transport], 'kind': 'profile_route',
        'route_id': route['id'], 'title': route['title'], 'transport': transport, 'support': support,
        'auth': [a['type'] for a in route['auth']],
        'account_kinds': sorted({c['account_kind'] for c in route['coverage']}),
        'coverage': [{k: c[k] for k in ('purpose', 'account_kind', 'capability', 'status')} for c in route['coverage']],
        'requirements': [{'kind': q['kind'], 'text': q['text']} for q in route['requirements']],
        'cost': {'basis': route['cost']['basis']}, 'evidence': _evidence(profile, route),
        'verification': dict(route.get('verification') or {}),
        'setup': setup, 'runtime': runtime, 'explanation': explanation,
    }


def _custom_variants() -> list[dict]:
    """The variants no profile or catalogue lists: any npm package or remote server a person
    names, and the detections that stop at a saved reference. Present for every address."""
    none_runtime = {'purposes': {}, 'desk_executes': False, 'adapter': None}
    base = {'route_id': None, 'support': 'user_supplied', 'auth': [], 'account_kinds': [], 'coverage': [], 'requirements': [],
            'cost': {'basis': 'unknown'}, 'evidence': [], 'verification': {}}
    return [
        {**base, 'id': 'custom-npm', 'type': 'mcp', 'kind': 'custom_npm', 'title': 'MCP server: npm package', 'transport': 'mcp',
         'setup': {'mode': 'full', 'via': 'custom_npm', 'endpoints': dict(_CUSTOM_NPM_ENDPOINTS)},
         'runtime': {**none_runtime, 'note': 'Approval registers the server. It does not prove the server can read this account or publish to it.'},
         'explanation': None},
        {**base, 'id': 'custom-remote', 'type': 'mcp', 'kind': 'custom_remote', 'title': 'MCP server: remote address', 'transport': 'mcp',
         'setup': {'mode': 'full', 'via': 'custom_remote', 'endpoints': dict(_CUSTOM_REMOTE_ENDPOINTS)},
         'runtime': {**none_runtime, 'note': 'Approval registers the server. It does not prove the server can read this account or publish to it.'},
         'explanation': None},
        {**base, 'id': 'detected-pypi', 'type': 'mcp', 'kind': 'detected_pypi', 'title': 'MCP server: PyPI package', 'transport': 'mcp',
         'setup': {'mode': 'none', 'via': None}, 'runtime': none_runtime,
         'explanation': _explain('detect_only', 'Clayrune can read a PyPI package to suggest settings, but it has no Python package installer, so it cannot run one.')},
        {**base, 'id': 'api-details', 'type': 'api', 'kind': 'detected_api', 'title': 'API details', 'transport': 'api',
         'setup': {'mode': 'reference_only', 'via': 'reference', 'endpoints': {'commit': _REFERENCE_ENDPOINT, 'detect': '/api/desk/connect/detect'}},
         'runtime': none_runtime,
         'explanation': _explain('reference_only', 'You can save API details, and Clayrune can detect their parameters, but it has no generic API executor, so nothing here connects or posts.')},
    ]


def _unreviewed_signin(recognised: bool) -> dict:
    text = ('This service has no reviewed sign-in page, so Clayrune will not type a login for it.' if recognised else
            'This address has no reviewed sign-in page, so Clayrune will not guess one or type a login for it.')
    return {'id': 'signin-unreviewed', 'type': 'signin', 'kind': 'unreviewed', 'route_id': None, 'title': 'Sign in', 'transport': 'browser',
            'support': 'none', 'auth': [], 'account_kinds': [], 'coverage': [], 'requirements': [], 'cost': {'basis': 'unknown'},
            'evidence': [], 'verification': {}, 'setup': {'mode': 'none', 'via': None},
            'runtime': {'purposes': {}, 'desk_executes': False, 'adapter': None}, 'explanation': _explain('no_reviewed_signin', text)}


def project(profile: dict | None, *, provider_for: ProviderFor | None = None, executor: Executor | None = None) -> dict:
    """`{service, picker, details, reference}` for one service profile, or for an address no profile
    names (`profile` None).

    `picker` lists the types that have at least one variant whose setup mode is `full`, in
    `TYPE_ORDER`, each with only those variants (at most `MAX_PICKER` types). `details.unavailable`
    holds every other variant with its `type` and `explanation`; `details.delivery` holds the
    manual routes (a person finishing a post themselves), which are not connections of any type.
    `provider_for` and `executor` default to the live provider table and `route_readiness`."""
    provider_for = provider_for or _providers.for_service
    executor = executor or _ready.executor
    variants: list[dict] = []
    delivery: list[dict] = []
    if profile is not None:
        for route in profile['routes']:
            v = _route_variant(profile, route, provider_for, executor)
            (delivery if v['explanation'] and v['explanation']['code'] == 'manual' else variants).append(v)
    variants += _custom_variants()
    if not any(v['type'] == 'signin' for v in variants):
        variants.append(_unreviewed_signin(profile is not None))
    picker = []
    for t in TYPE_ORDER:
        full = [v for v in variants if v['type'] == t and v['setup']['mode'] == 'full']
        if full:
            picker.append({'id': t, 'label': TYPE_LABELS[t], 'variants': full})
    unavailable = [v for v in variants if v['setup']['mode'] != 'full']
    unavailable.sort(key=lambda v: TYPE_ORDER.index(v['type']))
    service = ({'id': profile['service_id'], 'label': profile['label'], 'recognised': True, 'revision': profile['revision'],
                'account_kinds': [dict(k) for k in profile['account_kinds']]} if profile is not None else
               {'id': None, 'label': None, 'recognised': False, 'revision': None, 'account_kinds': []})
    return {'service': service, 'picker': picker[:MAX_PICKER],
            'details': {'unavailable': unavailable, 'delivery': [{'route_id': v['route_id'], 'title': v['title'], 'text': v['explanation']['text']}
                                                                 for v in delivery]},
            'reference': _reference()}


def project_service(service_id: object) -> dict:
    """`project` for a registry service id; an id no profile has is projected as an unknown address."""
    return project(_registry.profile(service_id))
