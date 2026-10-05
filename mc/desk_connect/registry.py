"""The known-service registry for connect-by-URL: DATA, not code (`registry.json`).

It says what Clayrune can really do for a host TODAY, nothing else. Recognising a
host is not support: every option carries a `support` word, and only `available`
ones lead anywhere (into a flow that already exists in Connections, named by
`open`). `restricted` and `info_only` are guidance the screen shows and cannot be
acted on. Matching is by exact host alias, never a substring: `notx.com` and
`x.com.evil.example` are not X.

A service is also found by NAME: its label and `aliases`, compared
case-insensitively on letters and digits only ("Higgsfield", "Google AI Studio",
"linkedin"); `suggest` ranks them for the as-you-type list and `url` is the address
a name resolves to. Names are never matched against hosts, and an unknown name
never causes a lookup (that is a later slice).

The file ships in the app and changes by commit and review. It is read from next
to this module, never from `data/` or `~/.clayrune`, so a user, an agent or a
page cannot add a host to it.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REGISTRY_PATH = Path(__file__).with_name('registry.json')

METHODS = ('mcp', 'api_key', 'oauth', 'browser_signin')
SUPPORT = ('available', 'restricted', 'info_only')
_ID_RE = re.compile(r'^[a-z0-9_]{1,40}$')
_HOST_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$')
# The Add service panel's own pick keys (desk-v1-add-service.js): an `open` may only
# name a flow the panel already has.
_OPEN_RE = re.compile(r'^(account|engine):[a-z0-9_]{1,40}$')
MAX_ALIAS = 60
MAX_SUGGESTIONS = 6

# Offered for every address, recognised or not. The one thing the commit route can
# save in this version: a record agents can see, with an optional credential.
FALLBACK_OPTION: dict[str, Any] = {
    'method': 'save_for_agents',
    'support': 'available',
    'title': 'Save for agents',
    'evidence': 'Always available',
    'guidance': ('Clayrune remembers the service and, if you give one, where its credential is kept, '
                 'so agents can see it. Clayrune does not connect to it or post to it.'),
}


class RegistryError(ValueError):
    """The registry file is unreadable or breaks a rule."""


def _need(cond: object, where: str, msg: str) -> None:
    if not cond:
        raise RegistryError(f'{where}: {msg}')


def _check_option(o: Any, where: str) -> dict:
    _need(isinstance(o, dict), where, 'an option must be an object')
    unknown = sorted(set(o) - {'method', 'support', 'title', 'evidence', 'guidance', 'open'})
    _need(not unknown, where, f'unknown field(s): {", ".join(unknown)}')
    _need(o.get('method') in METHODS, where, f'method must be one of {METHODS}')
    _need(o.get('support') in SUPPORT, where, f'support must be one of {SUPPORT}')
    for k in ('title', 'evidence', 'guidance'):
        _need(isinstance(o.get(k), str) and o[k].strip(), where, f'{k} is required text')
    if 'open' in o:
        _need(o['support'] == 'available', where, 'only an available option can open a flow')
        _need(isinstance(o['open'], str) and _OPEN_RE.match(o['open']), where, 'open must be account:<platform> or engine:<id>')
    else:
        # A reviewed MCP package is set up by Connect by address itself, so it opens no other flow.
        _need(o['support'] != 'available' or o['method'] == 'mcp', where, 'an available option must say which flow it opens')
    return dict(o)


def normalize(text: object) -> str:
    """A name as compared: NFKC, case-folded, every run of non letters/digits one
    space. "Google  AI-Studio" and "google ai studio" are the same name."""
    if not isinstance(text, str):
        return ''
    return re.sub(r'[\W_]+', ' ', unicodedata.normalize('NFKC', text).casefold()).strip()


def load(path: Path | None = None) -> dict:
    """Parse and validate the registry. Raises RegistryError; never returns a
    half-valid one."""
    p = path or REGISTRY_PATH
    try:
        raw = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise RegistryError(f'cannot read {p.name}: {e}') from e
    _need(isinstance(raw, dict) and raw.get('version') == 1, 'registry', 'version must be 1')
    blocked = raw.get('blocked_domains')
    _need(isinstance(blocked, list) and all(isinstance(d, str) and _HOST_RE.match(d) for d in blocked),
          'blocked_domains', 'a list of domains')
    common = [_check_option(o, f'common_options[{i}]') for i, o in enumerate(raw.get('common_options') or [])]
    services, by_host, by_name = [], {}, {}
    for i, s in enumerate(raw.get('services') or []):
        where = f'services[{i}]'
        _need(isinstance(s, dict), where, 'a service must be an object')
        _need(isinstance(s.get('id'), str) and _ID_RE.match(s['id']), where, 'id must be a short slug')
        _need(isinstance(s.get('label'), str) and s['label'].strip(), where, 'label is required')
        hosts = s.get('hosts')
        _need(isinstance(hosts, list) and hosts and all(isinstance(h, str) and _HOST_RE.match(h) for h in hosts),
              where, 'hosts must be a non-empty list of lowercase host names')
        opts = [_check_option(o, f'{where}.options[{j}]') for j, o in enumerate(s.get('options') or [])]
        _need(opts, where, 'a service needs at least one option')
        aliases = s.get('aliases') or []
        _need(isinstance(aliases, list) and all(isinstance(a, str) and 0 < len(a.strip()) <= MAX_ALIAS and '.' not in a
                                       for a in aliases),
              where, f'aliases must be a list of dot-free names of at most {MAX_ALIAS} characters')
        home = s.get('url')
        _need(isinstance(home, str), where, 'url is required: the address a name resolves to')
        try:
            parts = urlsplit(home)
            home_ok = parts.scheme == 'https' and parts.hostname in hosts and not parts.path.strip('/')
        except ValueError:
            home_ok = False
        _need(home_ok, where, 'url must be https://<one of the hosts> with no path')
        rec = {'id': s['id'], 'label': s['label'].strip(), 'aliases': [a.strip() for a in aliases],
               'url': home, 'hosts': list(hosts or []), 'options': opts}
        for h in hosts or []:
            _need(h not in by_host, where, f'host {h} is claimed by two services')
            by_host[h] = rec
        for n in {normalize(x) for x in [rec['label'], *rec['aliases']]}:
            _need(n, where, 'a name must contain a letter or a digit')
            _need(n not in by_name, where, f'the name "{n}" is claimed by two services')
            by_name[n] = rec
        services.append(rec)
    return {'version': 1, 'blocked_domains': list(blocked), 'common_options': common,
            'services': services, 'by_host': by_host, 'by_name': by_name}


_cache: dict | None = None


def registry() -> dict:
    global _cache
    if _cache is None:
        _cache = load()
    return _cache


def blocked_domains() -> list[str]:
    return registry()['blocked_domains']


def lookup(host: str) -> dict | None:
    """The service whose alias list names exactly this host, else None."""
    return registry()['by_host'].get(host)


def lookup_name(text: object) -> dict | None:
    """The service whose label or alias is exactly this name (see `normalize`)."""
    return registry()['by_name'].get(normalize(text))


def brief(service: dict) -> dict:
    """What the Service step lists for a service: a name and where it lives."""
    return {'id': service['id'], 'label': service['label'], 'host': urlsplit(service['url']).hostname,
            'url': service['url']}


def suggest(text: object, limit: int = MAX_SUGGESTIONS) -> list[dict]:
    """Services whose name starts with what was typed, best first: an exact name,
    then a name that starts with it, then a name with a word that does (two
    characters or more). Pure data; the same registry the Method step reads."""
    q = normalize(text)
    if not q:
        return []
    ranked = []
    for svc in registry()['services']:
        best = None
        for n in {normalize(x) for x in [svc['label'], *svc['aliases']]}:
            if n == q:
                score = 0
            elif n.startswith(q):
                score = 1
            elif len(q) >= 2 and any(w.startswith(q) for w in n.split()):
                score = 2
            else:
                continue
            best = score if best is None else min(best, score)
        if best is not None:
            ranked.append((best, svc['label'].lower(), svc))
    ranked.sort(key=lambda r: (r[0], r[1]))
    return [brief(r[2]) for r in ranked[:limit]]


def options_for(service: dict | None) -> list[dict]:
    """The Method step's rows for a host: its own options, the common ones (MCP is
    information only unless the service has its own MCP option), then the fallback. Each carries `selectable`:
    only the fallback can be chosen and saved in this version. An `available`
    row with `open` leads into a flow Connections already has; the rest are
    guidance."""
    reg = registry()
    own = service['options'] if service else []
    # The common "MCP is information only" row is for services with no MCP option of their own.
    common = [o for o in reg['common_options'] if not any(x['method'] == o['method'] for x in own)]
    rows = own + common + [FALLBACK_OPTION]
    out = []
    for o in rows:
        row = dict(o)
        row['selectable'] = o['method'] == 'save_for_agents'
        out.append(row)
    return out
