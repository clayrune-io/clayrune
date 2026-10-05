"""The shape of ONE service profile, and the rules a profile file must satisfy
(docs/DESK_SERVICE_PROFILES_SPEC.md section 3; slice P1).

A profile is inert JSON, shared data: what a service is called, which hosts are
exactly it, and for each PURPOSE the ROUTES to it (an operation path: API, browser or
MCP, each with its authorization alternatives), what each route covers, requires,
limits and costs, what Clayrune can execute today, and the EVIDENCE behind each
claim. It holds no secret, no executable body, no prompt and nothing about any one
user's account.

`check_profile` validates one parsed file and returns a normalised copy. It raises
`ProfileError` on the first problem and never returns a half-valid profile. It checks
shape and internal references only; whether a named adapter or catalogue entry exists
in code is the loader's neighbour's job (a test pins it), because this module must
stay free of any other Clayrune import.

Facts and dates: a fact that cites a CLAIM must have evidence in the same route that
carries that claim, and the dates come from the evidence alone (`retrieved`,
`attempted`). A route's verification is computed from them (`route_verification`): a
fact with no claim, or whose evidence was never retrieved, leaves `last_verified`
null. Nothing here treats conversion time as verification time.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = 1

# What a person asks a service for. Adding an id needs a schema/code review (spec 3.1).
PURPOSES: dict[str, tuple[str, ...]] = {
    'publish': ('post', 'reply', 'media', 'article'),
    'read_own': ('own_posts', 'mentions', 'replies', 'post_metrics'),
    'listen_broad': ('search',),
    'generate_image': ('generate',),
    'generate_video': ('generate',),
}
TRANSPORTS = ('api', 'browser', 'mcp')
MCP_PROTOCOLS = ('stdio', 'streamable_http', 'sse', 'unknown')
AUTH_TYPES = ('oauth', 'browser_signin', 'api_key', 'bearer', 'none', 'unknown')
PLACEMENTS = ('env', 'header', 'query', 'body', 'browser_session', 'none')
COVERAGE_STATUS = ('documented', 'claimed', 'unknown', 'not_supported', 'none_verified')
REQUIREMENT_KINDS = ('developer_app', 'provider_review', 'account_role', 'account_plan',
                     'permitted_use', 'adapter_prerequisite')
COST_BASIS = ('published', 'account_specific', 'unknown')
PAYERS = ('account_owner', 'clayrune', 'unknown')
LIMIT_SOURCES = ('provider', 'clayrune')
# What Clayrune can do with the route today. `available` leads into a flow that exists;
# `restricted` and `info_only` are explanations; `manual` is a person finishing it in
# their own browser (Clayrune only opens the link): none of the last three is a connection.
SUPPORT = ('available', 'restricted', 'info_only', 'manual')
# The Connect-by-address method token a provider recognises (registry.METHODS).
CONNECT_METHODS = ('mcp', 'api_key', 'oauth', 'browser_signin')
PUBLISHER_CLASSES = ('official_docs', 'official_registry', 'community', 'clayrune_builtin', 'clayrune_catalogue')
EVIDENCE_RESULTS = ('verified', 'incomplete', 'unavailable', 'conflicting', 'unverified')

MAX_ALIAS = 60
MAX_TEXT = 600
MAX_SHORT = 160
_SLUG_RE = re.compile(r'^[a-z0-9_]{1,40}$')
_LOCAL_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,60}$')
_CLAIM_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{0,80}$')
_HOST_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$')
# The Add service panel's own pick keys (desk-v1-add-service.js): an adapter may only name a flow it has.
_ADAPTER_RE = re.compile(r'^(account|engine):[a-z0-9_]{1,40}$')
_PARAM_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')
_AMOUNT_RE = re.compile(r'^\d{1,9}(\.\d{1,6})?$')
_DIGEST_RE = re.compile(r'^[0-9a-f]{64}$')
_DROP = {'Cc', 'Cf', 'Cs', 'Co', 'Cn', 'Zl', 'Zp'}


class ProfileError(ValueError):
    """A profile, the index or the set of profiles breaks a rule."""


def need(cond: object, where: str, msg: str) -> None:
    if not cond:
        raise ProfileError(f'{where}: {msg}')


def normalize(text: object) -> str:
    """A name as compared: NFKC, case-folded, every run of non letters/digits one
    space. "Google  AI-Studio" and "google ai studio" are the same name."""
    if not isinstance(text, str):
        return ''
    return re.sub(r'[\W_]+', ' ', unicodedata.normalize('NFKC', text).casefold()).strip()


def is_host(text: object) -> bool:
    return isinstance(text, str) and bool(_HOST_RE.match(text))


def _fields(obj: Any, where: str, required: set[str], optional: set[str] | None = None) -> dict:
    need(isinstance(obj, dict), where, 'must be an object')
    unknown = sorted(set(obj) - required - (optional or set()))
    need(not unknown, where, f'unknown field(s): {", ".join(unknown)}')
    missing = sorted(required - set(obj))
    need(not missing, where, f'missing field(s): {", ".join(missing)}')
    return obj


def _opt_text(v: Any, where: str, what: str, limit: int = MAX_TEXT) -> str | None:
    return None if v is None else _text(v, where, what, limit)


def _text(v: Any, where: str, what: str, limit: int = MAX_TEXT) -> str:
    need(isinstance(v, str) and v.strip() and len(v) <= limit, where, f'{what} must be text of at most {limit} characters')
    need(not any(unicodedata.category(c) in _DROP for c in v), where, f'{what} holds a control or format character')
    return v.strip()


def _enum(v: Any, allowed: tuple, where: str, what: str) -> str:
    need(isinstance(v, str) and v in allowed, where, f'{what} must be one of {allowed}')
    return v


def _date(v: Any, where: str, what: str) -> str | None:
    if v is None:
        return None
    need(isinstance(v, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', v), where, f'{what} must be a YYYY-MM-DD date or null')
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ProfileError(f'{where}: {what} is not a real date') from None
    return v


def _claim(v: Any, where: str) -> str | None:
    if v is None:
        return None
    need(isinstance(v, str) and _CLAIM_RE.match(v), where, 'claim must be a claim id or null')
    return v


def _list(v: Any, where: str, what: str, maximum: int = 40) -> list:
    need(isinstance(v, list) and len(v) <= maximum, where, f'{what} must be a list of at most {maximum}')
    return v


def _check_auth(a: Any, where: str, transport: str) -> dict:
    _fields(a, where, {'type', 'params', 'placement', 'scopes', 'issuer', 'registration', 'claim'})
    typ = _enum(a['type'], AUTH_TYPES, where, 'type')
    if transport == 'browser':
        need(typ in ('browser_signin', 'unknown'), where, 'a browser route is authorised by a browser sign-in')
    params = _list(a['params'], where, 'params', 8)
    # Names only: a value-shaped string here would be a secret typed into shared data.
    need(all(isinstance(p, str) and _PARAM_RE.match(p) for p in params), where, 'params must be credential parameter NAMES')
    placement = a['placement']
    need(placement is None or placement in PLACEMENTS, where, f'placement must be null or one of {PLACEMENTS}')
    scopes = [_text(s, where, 'a scope', 120) for s in _list(a['scopes'], where, 'scopes', 20)]
    if typ in ('none', 'browser_signin'):
        need(not params and not scopes, where, f'a {typ} alternative has no credential parameters or scopes')
    return {'type': typ, 'params': list(params), 'placement': placement, 'scopes': scopes,
            'issuer': _opt_text(a['issuer'], where, 'issuer', MAX_SHORT),
            'registration': _opt_text(a['registration'], where, 'registration', MAX_TEXT),
            'claim': _claim(a['claim'], where)}


def _check_route(r: Any, i: int) -> dict:
    where = f'routes[{i}]'
    _fields(r, where, {'id', 'title', 'guidance', 'transport', 'support', 'auth', 'coverage', 'requirements',
                       'limits', 'cost', 'evidence'},
            {'mcp_protocol', 'connect_method', 'adapter_id', 'catalogue_id', 'signin'})
    need(isinstance(r['id'], str) and _LOCAL_ID_RE.match(r['id']), where, 'id must be a short slug')
    where = f'routes[{i}] ({r["id"]})'
    transport = _enum(r['transport'], TRANSPORTS, where, 'transport')
    if transport == 'mcp':
        need('mcp_protocol' in r, where, f'an MCP route names its protocol (or "unknown"): {MCP_PROTOCOLS}')
        _enum(r['mcp_protocol'], MCP_PROTOCOLS, where, 'mcp_protocol')
    else:
        need('mcp_protocol' not in r, where, 'only an MCP route has a protocol')
    support = _enum(r['support'], SUPPORT, where, 'support')
    method = r.get('connect_method')
    need(method is None or method in CONNECT_METHODS, where, f'connect_method must be one of {CONNECT_METHODS}')
    need(support != 'manual' or method is None, where, 'a manual route is not a connection method')
    adapter, catalogue = r.get('adapter_id'), r.get('catalogue_id')
    need(adapter is None or (isinstance(adapter, str) and _ADAPTER_RE.match(adapter)), where,
         'adapter_id must be account:<platform> or engine:<id>')
    need(catalogue is None or (isinstance(catalogue, str) and _SLUG_RE.match(catalogue)), where, 'catalogue_id must be a short slug')
    # Only an `available` route leads anywhere; every `available` one must say where. (A reviewed
    # MCP package is set up by Connect by address itself, so it names its catalogue entry instead.)
    need((adapter is None and catalogue is None) or support == 'available', where, 'only an available route can name an adapter or a catalogue entry')
    need(support != 'available' or adapter is not None or (catalogue is not None and transport == 'mcp'), where,
         'an available route must name the adapter (or reviewed MCP package) that carries it')
    need(adapter is None or catalogue is None, where, 'a route names an adapter or a catalogue entry, not both')
    auths = [_check_auth(a, f'{where}.auth[{j}]', transport) for j, a in enumerate(_list(r['auth'], where, 'auth', 6))]
    need(auths, where, 'auth needs at least one alternative (type "unknown" is an explicit answer)')
    cov, seen = [], set()
    for j, c in enumerate(_list(r['coverage'], where, 'coverage', 40)):
        w = f'{where}.coverage[{j}]'
        _fields(c, w, {'purpose', 'account_kind', 'capability', 'status', 'claim'})
        _enum(c['purpose'], tuple(PURPOSES), w, 'purpose')
        _enum(c['capability'], PURPOSES[c['purpose']], w, 'capability')
        need(isinstance(c['account_kind'], str) and _SLUG_RE.match(c['account_kind']), w, 'account_kind must be a slug')
        status = _enum(c['status'], COVERAGE_STATUS, w, 'status')
        claim = _claim(c['claim'], w)
        # A negative finding records its search scope and date, so it is a claim with evidence too.
        need(status == 'unknown' or claim is not None, w, f'{status} coverage must cite a claim')
        key = (c['purpose'], c['account_kind'], c['capability'])
        need(key not in seen, w, f'{"/".join(key)} is listed twice')
        seen.add(key)
        cov.append({**c, 'claim': claim})
    reqs = []
    for j, q in enumerate(_list(r['requirements'], where, 'requirements', 20)):
        w = f'{where}.requirements[{j}]'
        _fields(q, w, {'kind', 'text', 'claim'})
        reqs.append({'kind': _enum(q['kind'], REQUIREMENT_KINDS, w, 'kind'), 'text': _text(q['text'], w, 'text'),
                     'claim': _claim(q['claim'], w)})
    lims = []
    for j, q in enumerate(_list(r['limits'], where, 'limits', 20)):
        w = f'{where}.limits[{j}]'
        _fields(q, w, {'name', 'source', 'value', 'unit', 'reset', 'claim'})
        lims.append({'name': _text(q['name'], w, 'name', MAX_SHORT), 'source': _enum(q['source'], LIMIT_SOURCES, w, 'source'),
                     'value': _opt_text(q['value'], w, 'value', MAX_SHORT),      # null: not known
                     'unit': _opt_text(q['unit'], w, 'unit', MAX_SHORT),
                     'reset': _opt_text(q['reset'], w, 'reset', MAX_SHORT), 'claim': _claim(q['claim'], w)})
    cost = _fields(r['cost'], f'{where}.cost', {'basis', 'items'})
    basis = _enum(cost['basis'], COST_BASIS, f'{where}.cost', 'basis')
    items = []
    for j, it in enumerate(_list(cost['items'], f'{where}.cost', 'items', 20)):
        w = f'{where}.cost.items[{j}]'
        _fields(it, w, {'currency', 'unit', 'amount', 'endpoint', 'payer', 'condition', 'claim'})
        need(isinstance(it['currency'], str) and re.match(r'^[A-Z]{3}$', it['currency']), w, 'currency must be a 3-letter code')
        need(isinstance(it['amount'], str) and _AMOUNT_RE.match(it['amount']), w, 'amount must be a decimal string')
        claim = _claim(it['claim'], w)
        need(claim is not None, w, 'a price (zero included) must cite a claim')
        items.append({'currency': it['currency'], 'unit': _text(it['unit'], w, 'unit', MAX_SHORT), 'amount': it['amount'],
                      'endpoint': _opt_text(it['endpoint'], w, 'endpoint', MAX_SHORT),
                      'payer': _enum(it['payer'], PAYERS, w, 'payer'),
                      'condition': _opt_text(it['condition'], w, 'condition', MAX_SHORT), 'claim': claim})
    need(bool(items) == (basis == 'published'), f'{where}.cost', 'published cost lists its prices; any other basis lists none')
    ev = _list(r['evidence'], where, 'evidence', 20)
    need(ev and all(isinstance(e, str) and _LOCAL_ID_RE.match(e) for e in ev) and len(set(ev)) == len(ev), where,
         'evidence must list at least one distinct evidence id')
    out = {'id': r['id'], 'title': _text(r['title'], where, 'title', MAX_SHORT), 'guidance': _text(r['guidance'], where, 'guidance'),
           'transport': transport, 'support': support, 'auth': auths, 'coverage': cov, 'requirements': reqs,
           'limits': lims, 'cost': {'basis': basis, 'items': items}, 'evidence': list(ev)}
    for k in ('mcp_protocol', 'connect_method', 'adapter_id', 'catalogue_id'):
        if r.get(k) is not None:
            out[k] = r[k]
    if 'signin' in r:
        out['signin'] = _check_signin(r['signin'], where, auths)
    return out


def _check_signin(s: Any, where: str, auths: list[dict]) -> dict:
    """A route's declared sign-in page: the ONE place a stored login may be typed for it
    (`signin_fill`). `hosts` are exact host names (https, default port, no wildcard); `url` is
    where the pane opens, or null when the provider's own flow supplies the address (OAuth)."""
    where = f'{where}.signin'
    _fields(s, where, {'url', 'hosts'})
    need(any(a['type'] in ('browser_signin', 'oauth') for a in auths), where,
         'only a route that signs in (browser sign-in or OAuth) declares a sign-in page')
    hosts = _list(s['hosts'], where, 'hosts', 6)
    need(hosts and all(is_host(h) for h in hosts) and len(set(hosts)) == len(hosts), where,
         'hosts must be a non-empty list of distinct lowercase host names')
    url = s['url']
    if url is not None:
        try:
            parts = urlsplit(url) if isinstance(url, str) else None
            ok = bool(parts) and parts.scheme == 'https' and parts.hostname in hosts and parts.port is None \
                and not parts.username and not parts.fragment and len(url) <= 300 \
                and not any(unicodedata.category(c) in _DROP or c.isspace() for c in url)
        except ValueError:
            ok = False
        need(ok, where, 'url must be a plain https address on one of the declared hosts, or null')
    return {'url': url, 'hosts': list(hosts)}


def _check_evidence(e: Any, i: int) -> dict:
    where = f'evidence[{i}]'
    _fields(e, where, {'id', 'label', 'url', 'publisher_class', 'published', 'retrieved', 'attempted', 'claim_ids', 'digest', 'result'})
    need(isinstance(e['id'], str) and _LOCAL_ID_RE.match(e['id']), where, 'id must be a short slug')
    where = f'evidence[{i}] ({e["id"]})'
    url = e['url']
    if url is not None:
        try:
            parts = urlsplit(url)
            ok = isinstance(url, str) and parts.scheme == 'https' and is_host(parts.hostname) and not parts.username and len(url) <= 300 \
                and not any(unicodedata.category(c) in _DROP or c.isspace() for c in url)
        except ValueError:
            ok = False
        need(ok, where, 'url must be a plain https address or null')
    published, retrieved, attempted = (_date(e[k], where, k) for k in ('published', 'retrieved', 'attempted'))
    result = _enum(e['result'], EVIDENCE_RESULTS, where, 'result')
    # A result is a statement about a retrieval: the dates must agree with it, so a legacy
    # import (no retrieval) can never read as a verification.
    need(result != 'verified' or (url is not None and retrieved is not None), where, 'verified evidence needs its url and the date it was retrieved')
    need(result != 'unverified' or (retrieved is None and published is None), where, 'unverified (imported) evidence has no retrieval or publication date')
    need(retrieved is None or attempted is None or attempted >= retrieved, where, 'attempted cannot be before retrieved')
    need(retrieved is None or result != 'unavailable', where, 'unavailable evidence was not retrieved')
    digest = e['digest']
    need(digest is None or (isinstance(digest, str) and _DIGEST_RE.match(digest)), where, 'digest must be a lowercase sha256 hex or null')
    claims = _list(e['claim_ids'], where, 'claim_ids', 60)
    need(all(isinstance(c, str) and _CLAIM_RE.match(c) for c in claims) and len(set(claims)) == len(claims), where, 'claim_ids must be distinct claim ids')
    return {'id': e['id'], 'label': _text(e['label'], where, 'label', MAX_SHORT), 'url': url,
            'publisher_class': _enum(e['publisher_class'], PUBLISHER_CLASSES, where, 'publisher_class'),
            'published': published, 'retrieved': retrieved, 'attempted': attempted,
            'claim_ids': list(claims), 'digest': digest, 'result': result}


def route_claims(route: dict) -> set[str]:
    """Every claim id a route's facets cite."""
    out = {a['claim'] for a in route['auth']} | {c['claim'] for c in route['coverage']} | {q['claim'] for q in route['requirements']}
    out |= {q['claim'] for q in route['limits']} | {i['claim'] for i in route['cost']['items']}
    out.discard(None)
    return out


def route_verification(route: dict, evidence: dict[str, dict]) -> dict:
    """`{status, last_verified, last_attempted}` of one route, from its evidence only.

    `last_verified` is the OLDEST retrieval among the verified evidence of the claims the
    route cites, so a newer price check cannot refresh an older permission claim; it is
    null when any cited claim has no verified evidence, or the route cites no claim.
    `last_attempted` is the newest attempt on any of its evidence and does not renew
    `last_verified`. Status: verified (every claim), partial (some), conflicting,
    unavailable (nothing retrievable), else unverified."""
    claims = route_claims(route)
    rows = [evidence[i] for i in route['evidence']]
    got: dict[str, list[str]] = {}
    for e in rows:
        if e['result'] == 'verified':
            for c in e['claim_ids']:
                got.setdefault(c, []).append(e['retrieved'])
    attempts = [d for e in rows for d in (e['attempted'], e['retrieved']) if d]
    covered = {c for c in claims if c in got}
    if any(e['result'] == 'conflicting' for e in rows):
        status = 'conflicting'
    elif claims and covered == claims:
        status = 'verified'
    elif covered:
        status = 'partial'
    elif rows and all(e['result'] == 'unavailable' for e in rows):
        status = 'unavailable'
    else:
        status = 'unverified'
    # The oldest of each claim's NEWEST verified retrieval.
    last = min((max(got[c]) for c in covered), default=None) if claims and covered == claims else None
    return {'status': status, 'last_verified': last, 'last_attempted': max(attempts) if attempts else None}


def check_profile(raw: Any, where: str = 'profile') -> dict:
    """Validate one parsed profile and return the normalised copy."""
    _fields(raw, where, {'schema_version', 'service_id', 'revision', 'label', 'aliases', 'home_url', 'hosts', 'account_kinds',
                         'purposes', 'routes', 'evidence', 'change_summary', 'supersedes'})
    need(raw['schema_version'] == SCHEMA_VERSION, where, f'schema_version must be {SCHEMA_VERSION}')
    sid = raw['service_id']
    need(isinstance(sid, str) and _SLUG_RE.match(sid), where, 'service_id must be a short slug')
    where = f'{where} ({sid})'
    rev = raw['revision']
    need(isinstance(rev, int) and not isinstance(rev, bool) and rev >= 1, where, 'revision must be an integer from 1')
    sup = raw['supersedes']
    need(sup is None or (isinstance(sup, int) and not isinstance(sup, bool) and 1 <= sup < rev), where, 'supersedes must be a lower revision number or null')
    label = _text(raw['label'], where, 'label', MAX_ALIAS)
    aliases = _list(raw['aliases'], where, 'aliases', 20)
    # Dot-free so an alias can never be read as an address (resolve.classify).
    need(all(isinstance(a, str) and 0 < len(a.strip()) <= MAX_ALIAS and '.' not in a for a in aliases), where,
         f'aliases must be a list of dot-free names of at most {MAX_ALIAS} characters')
    hosts = _list(raw['hosts'], where, 'hosts', 20)
    need(hosts and all(is_host(h) for h in hosts) and len(set(hosts)) == len(hosts), where,
         'hosts must be a non-empty list of distinct lowercase host names')
    home = raw['home_url']
    try:
        parts = urlsplit(home) if isinstance(home, str) else None
        home_ok = bool(parts) and parts.scheme == 'https' and parts.hostname in hosts and not parts.path.strip('/') and not parts.query
    except ValueError:
        home_ok = False
    need(home_ok, where, 'home_url must be https://<one of the hosts> with no path: the address a name resolves to')
    need(all(normalize(x) for x in [label, *aliases]), where, 'a name must contain a letter or a digit')

    kinds = []
    for i, k in enumerate(_list(raw['account_kinds'], where, 'account_kinds', 8)):
        w = f'{where}.account_kinds[{i}]'
        _fields(k, w, {'id', 'label'})
        need(isinstance(k['id'], str) and _SLUG_RE.match(k['id']), w, 'id must be a slug')
        kinds.append({'id': k['id'], 'label': _text(k['label'], w, 'label', MAX_SHORT)})
    kind_ids = [k['id'] for k in kinds]
    need(len(set(kind_ids)) == len(kind_ids), where, 'account kind ids must be distinct')

    evidence = [_check_evidence(e, i) for i, e in enumerate(_list(raw['evidence'], where, 'evidence', 60))]
    by_ev = {e['id']: e for e in evidence}
    need(len(by_ev) == len(evidence), where, 'evidence ids must be distinct')
    routes = [_check_route(r, i) for i, r in enumerate(_list(raw['routes'], where, 'routes', 40))]
    by_route = {r['id']: r for r in routes}
    need(len(by_route) == len(routes), where, 'route ids must be distinct')

    used_claims: set[str] = set()
    for r in routes:
        w = f'{where} route {r["id"]}'
        for h in (r.get('signin') or {}).get('hosts', []):
            need(any(h == own or h.endswith('.' + own) for own in hosts), w,
                 f'sign-in host {h} is not one of the service\'s own hosts or under one')
        for ev_id in r['evidence']:
            need(ev_id in by_ev, w, f'evidence "{ev_id}" does not exist')
        have = {c for ev_id in r['evidence'] for c in by_ev[ev_id]['claim_ids']}
        cites = route_claims(r)
        missing = sorted(cites - have)
        need(not missing, w, f'claim(s) {", ".join(missing)} have no evidence listed for this route')
        used_claims |= cites
    for e in evidence:
        stray = sorted(set(e['claim_ids']) - used_claims)
        need(not stray, f'{where} evidence {e["id"]}', f'claim(s) {", ".join(stray)} are cited by no route')

    purposes, seen_p = [], set()
    for i, p in enumerate(_list(raw['purposes'], where, 'purposes', 10)):
        w = f'{where}.purposes[{i}]'
        _fields(p, w, {'id', 'capabilities', 'account_kinds', 'routes', 'standing_policy'})
        pid = _enum(p['id'], tuple(PURPOSES), w, 'id')
        need(pid not in seen_p, w, f'purpose {pid} is listed twice')
        seen_p.add(pid)
        caps = _list(p['capabilities'], w, 'capabilities', 8)
        need(caps and all(c in PURPOSES[pid] for c in caps) and len(set(caps)) == len(caps), w, f'capabilities must be distinct values of {PURPOSES[pid]}')
        pk = _list(p['account_kinds'], w, 'account_kinds', 8)
        need(pk and all(k in kind_ids for k in pk) and len(set(pk)) == len(pk), w, 'account_kinds must be declared account kinds')
        pr = _list(p['routes'], w, 'routes', 40)
        need(pr and all(x in by_route for x in pr) and len(set(pr)) == len(pr), w, 'routes must be existing route ids')
        purposes.append({'id': pid, 'capabilities': list(caps), 'account_kinds': list(pk), 'routes': list(pr),
                         'standing_policy': [_text(s, w, 'a standing policy note') for s in _list(p['standing_policy'], w, 'standing_policy', 6)]})
    by_purpose = {p['id']: p for p in purposes}
    for r in routes:
        for c in r['coverage']:
            w = f'{where} route {r["id"]} coverage {c["purpose"]}/{c["capability"]}'
            p = by_purpose.get(c['purpose'])
            need(p is not None, w, 'the profile has no such purpose')
            need(r['id'] in p['routes'] and c['account_kind'] in p['account_kinds'] and c['capability'] in p['capabilities'], w,
                 'is not backed by the purpose: its routes, account kinds and capabilities must include this one')
    out = {'schema_version': SCHEMA_VERSION, 'service_id': sid, 'revision': rev, 'label': label,
           'aliases': [a.strip() for a in aliases], 'home_url': home, 'hosts': list(hosts), 'account_kinds': kinds,
           'purposes': purposes, 'routes': routes, 'evidence': evidence,
           'change_summary': _text(raw['change_summary'], where, 'change_summary'), 'supersedes': sup}
    for r in out['routes']:
        r['verification'] = route_verification(r, by_ev)
    return out
