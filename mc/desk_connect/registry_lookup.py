"""Bounded lookup in the official MCP registry (spec "Detection and trust").

The registry searches server NAMES by substring, not service hosts, so the query is a
brand term derived from the host ("app.plausible.io" -> "plausible"), at most three
pages and 100 results, inside a 10 second budget. A hit is a LISTING, not an
endorsement: each entry carries a `relation` computed here from structured metadata,
never by the model and never from the description text:

    own_domain   the entry's reverse-DNS namespace is the service's own domain
                 (`io.plausible/...` for plausible.io)
    claims_site  the entry says its website or a remote endpoint is on the service's
                 domain, which any publisher can write
    name_match   only the name matched

The fixed registry API is a separate source and never a page-reader substitute: this
module is the only HTTP client in discovery, it talks to ONE https host with no
redirects, caps each response, and parses JSON only. Nothing it returns is fetched or
executed afterwards: URLs are kept as display text.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from mc.desk_connect import net_guard

REGISTRY_HOST = 'registry.modelcontextprotocol.io'
BASE_URL = f'https://{REGISTRY_HOST}/v0.1/servers'
REGISTRY_S = 10.0
MAX_PAGES = 3
MAX_RESULTS = 100
PAGE_LIMIT = 50
MAX_BODY = 1_000_000
MIN_TERM = 3
_SLD = {'co', 'com', 'org', 'net', 'ac', 'gov', 'edu'}
_RANK = {'own_domain': 0, 'claims_site': 1, 'name_match': 2}


def domain_of(host: str) -> str:
    """The service's registrable-looking domain: the last two labels, three after a
    `co.uk`-style second level. A heuristic for matching, never for trust."""
    labels = [l for l in host.lower().rstrip('.').split('.') if l]
    if len(labels) >= 3 and labels[-2] in _SLD and len(labels[-1]) == 2:
        return '.'.join(labels[-3:])
    return '.'.join(labels[-2:])


def brand_term(host: str) -> str:
    """The search term for a host, '' when it is too short to search on."""
    dom = domain_of(host).split('.')
    term = dom[0] if dom else ''
    return term if len(term) >= MIN_TERM else ''


def _under(host: str, domain: str) -> bool:
    host = (host or '').lower().rstrip('.')
    return host == domain or host.endswith('.' + domain)


def _https_host(url) -> str:
    try:
        p = urllib.parse.urlsplit(url) if isinstance(url, str) else None
    except ValueError:
        return ''
    return (p.hostname or '').lower() if p and p.scheme == 'https' else ''


def _entry(item, domain: str) -> dict | None:
    srv = item.get('server') if isinstance(item, dict) else None
    if not isinstance(srv, dict) or not isinstance(srv.get('name'), str):
        return None
    name = srv['name'][:200]
    namespace = name.split('/', 1)[0]
    reverse = '.'.join(reversed(namespace.lower().split('.')))
    site = srv.get('websiteUrl') if isinstance(srv.get('websiteUrl'), str) else ''
    repo = (srv.get('repository') or {}).get('url') if isinstance(srv.get('repository'), dict) else ''
    remotes = [r.get('url') for r in (srv.get('remotes') or []) if isinstance(r, dict) and isinstance(r.get('url'), str)]
    if reverse == domain:
        relation = 'own_domain'
    elif _under(_https_host(site), domain) or any(_under(_https_host(u), domain) for u in remotes):
        relation = 'claims_site'
    else:
        relation = 'name_match'
    shown = next((u for u in [site, *remotes, repo] if _https_host(u)), '')
    return {'name': name, 'title': str(srv.get('title') or '')[:120], 'description': str(srv.get('description') or '')[:300],
            'relation': relation, 'url': shown[:300],
            'remote_hosts': sorted({_https_host(u) for u in remotes if _https_host(u)})[:3]}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_get(url: str, timeout: float) -> bytes:
    """One GET to the registry host: https only, no redirect, size-capped."""
    if urllib.parse.urlsplit(url).hostname != REGISTRY_HOST:
        raise ValueError('not the registry host')
    net_guard.resolve_public(REGISTRY_HOST)             # refuse a poisoned local resolver
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'Clayrune-desk-connect'})
    with opener.open(req, timeout=timeout) as r:        # nosec: fixed https host
        data = r.read(MAX_BODY + 1)
    if len(data) > MAX_BODY:
        raise ValueError('response too large')
    return data


def lookup(host: str, *, budget_s: float = REGISTRY_S, fetch: Callable[[str, float], bytes] | None = None,
           now: Callable[[], float] = time.monotonic) -> dict:
    """`{ok: True, entries, capped, term}` or `{ok: False, code, message}` with code
    `registry_timeout` | `registry_unavailable`. `capped` is true when more results
    existed than the bounds allowed (the answer is then incomplete, say so)."""
    term = brand_term(host)
    if not term:
        return {'ok': True, 'entries': [], 'capped': False, 'term': ''}
    domain = domain_of(host)
    get = fetch or _http_get
    t_end = now() + budget_s
    entries, cursor, capped, pages = [], '', False, 0
    while pages < MAX_PAGES and len(entries) < MAX_RESULTS:
        left = t_end - now()
        if left <= 0.2:
            return {'ok': False, 'code': 'registry_timeout', 'message': 'The MCP registry did not answer in time.'}
        q = {'search': term, 'limit': str(min(PAGE_LIMIT, MAX_RESULTS - len(entries)))}
        if cursor:
            q['cursor'] = cursor
        try:
            raw = get(f'{BASE_URL}?{urllib.parse.urlencode(q)}', left)
            doc = json.loads(raw)
        except (TimeoutError, urllib.error.URLError, OSError) as e:
            slow = isinstance(e, TimeoutError) or 'timed out' in str(e).lower()
            return {'ok': False, 'code': 'registry_timeout' if slow else 'registry_unavailable',
                    'message': 'The MCP registry did not answer in time.' if slow else 'The MCP registry could not be reached.'}
        except (ValueError, UnicodeDecodeError):
            return {'ok': False, 'code': 'registry_unavailable', 'message': 'The MCP registry gave an answer Clayrune could not read.'}
        pages += 1
        servers = doc.get('servers') if isinstance(doc, dict) else None
        if not isinstance(doc, dict) or not isinstance(servers, list):
            return {'ok': False, 'code': 'registry_unavailable', 'message': 'The MCP registry gave an answer Clayrune could not read.'}
        for item in servers:
            e = _entry(item, domain)
            if e and len(entries) < MAX_RESULTS:
                entries.append(e)
        meta = doc.get('metadata') if isinstance(doc.get('metadata'), dict) else {}
        cursor = meta.get('nextCursor') if isinstance(meta.get('nextCursor'), str) else ''
        if not cursor:
            break
    capped = bool(cursor)
    entries.sort(key=lambda e: _RANK[e['relation']])
    return {'ok': True, 'entries': entries, 'capped': capped, 'term': term}
