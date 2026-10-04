"""The known-host registry for connect-by-URL: DATA, not code (`registry.json`).

It says what Clayrune can really do for a host TODAY, nothing else. Recognising a
host is not support: every option carries a `support` word, and only `available`
ones lead anywhere (into a flow that already exists in Connections, named by
`open`). `restricted` and `info_only` are guidance the screen shows and cannot be
acted on. Matching is by exact host alias, never a substring: `notx.com` and
`x.com.evil.example` are not X.

The file ships in the app and changes by commit and review. It is read from next
to this module, never from `data/` or `~/.clayrune`, so a user, an agent or a
page cannot add a host to it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).with_name('registry.json')

METHODS = ('mcp', 'api_key', 'oauth', 'browser_signin')
SUPPORT = ('available', 'restricted', 'info_only')
_ID_RE = re.compile(r'^[a-z0-9_]{1,40}$')
_HOST_RE = re.compile(r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$')
# The Add service panel's own pick keys (desk-v1-add-service.js): an `open` may only
# name a flow the panel already has.
_OPEN_RE = re.compile(r'^(account|engine):[a-z0-9_]{1,40}$')

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
        _need(o['support'] != 'available', where, 'an available option must say which flow it opens')
    return dict(o)


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
    services, by_host = [], {}
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
        rec = {'id': s['id'], 'label': s['label'].strip(), 'hosts': list(hosts or []), 'options': opts}
        for h in hosts or []:
            _need(h not in by_host, where, f'host {h} is claimed by two services')
            by_host[h] = rec
        services.append(rec)
    return {'version': 1, 'blocked_domains': list(blocked), 'common_options': common,
            'services': services, 'by_host': by_host}


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


def options_for(service: dict | None) -> list[dict]:
    """The Method step's rows for a host: its own options, the common ones (MCP is
    information only everywhere), then the fallback. Each carries `selectable`:
    only the fallback can be chosen and saved in this version. An `available`
    row with `open` leads into a flow Connections already has; the rest are
    guidance."""
    reg = registry()
    rows = (service['options'] if service else []) + reg['common_options'] + [FALLBACK_OPTION]
    out = []
    for o in rows:
        row = dict(o)
        row['selectable'] = o['method'] == 'save_for_agents'
        out.append(row)
    return out
