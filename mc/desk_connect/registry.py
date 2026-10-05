"""The known-service registry for connect-by-URL: DATA, not code.

The data is one service profile per file (`profiles/<id>.json`, schema in `profile_schema`)
listed by the index `registry.json` (version 2, `profile_loader`). This module is the thin
reader the Connect screens and `commit` already use: it loads the snapshot, projects each
profile into the version 1 record they read (`profile_compat`), and answers by exact host or
by name. A version 1 `registry.json` is still accepted and converted in memory, so a file in
the old shape loads the same way.

It says what Clayrune can really do for a host TODAY, nothing else. Recognising a host is not
support: every option carries a `support` word, and only `available` ones lead anywhere (into
a flow that already exists in Connections, named by `open`). `restricted` and `info_only` are
guidance the screen shows and cannot be acted on. Matching is by exact host alias, never a
substring: `notx.com` and `x.com.evil.example` are not X.

A service is also found by NAME: its label and `aliases`, compared case-insensitively on
letters and digits only ("Higgsfield", "Google AI Studio", "linkedin"); `suggest` ranks them
for the as-you-type list and `url` is the address a name resolves to. Names are never matched
against hosts, and an unknown name never causes a lookup (that is a later slice).

The files ship in the app and change by commit and review. They are read from next to this
module, never from `data/` or `~/.clayrune`, so a user, an agent or a page cannot add a host.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from mc.desk_connect import profile_compat as _compat
from mc.desk_connect import profile_loader as _loader
from mc.desk_connect import profile_schema as _schema
from mc.desk_connect.profile_schema import ProfileError, normalize  # noqa: F401  (normalize: part of this module's API)

REGISTRY_PATH = Path(__file__).with_name('registry.json')

METHODS = _schema.CONNECT_METHODS
SUPPORT = ('available', 'restricted', 'info_only')
MAX_ALIAS = _schema.MAX_ALIAS
MAX_SUGGESTIONS = 6

# Offered for every address, recognised or not. The one thing the commit route can
# save in this version: a record agents can see, with an optional credential.
FALLBACK_OPTION: dict[str, Any] = {
    'method': 'save_for_agents',
    'support': 'available',
    'title': 'Save for agents',
    'evidence': 'Always available',
    'guidance': ('Saves a note for agents: the name and address, plus the name of a login if you add one. '
                 'Clayrune does not connect to it, sign in to it or post to it.'),
}


class RegistryError(ValueError):
    """The registry file is unreadable or breaks a rule."""


def load(path: Path | None = None) -> dict:
    """Parse and validate the registry (an index and every profile it lists, or a version 1
    file). Raises RegistryError; never returns a half-valid one."""
    p = path or REGISTRY_PATH
    try:
        raw = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise RegistryError(f'cannot read {p.name}: {e}') from e
    try:
        version = raw.get('version') if isinstance(raw, dict) else None
        if version == 1:
            snap = _compat.snapshot_from_v1(raw)
        elif version == _loader.INDEX_VERSION:
            snap = _loader.snapshot_from_index(raw, p.parent)
            snap['common_options'] = [dict(o) for o in _compat.LEGACY_COMMON_OPTIONS]
        else:
            raise ProfileError(f'registry: version must be 1 or {_loader.INDEX_VERSION}')
    except ProfileError as e:
        raise RegistryError(str(e)) from e
    services, by_id = [], {}
    for prof in snap['profiles']:
        rec = _compat.project_service(prof)
        rec['profile'] = prof
        by_id[rec['id']] = rec
        services.append(rec)
    return {'version': snap['version'], 'blocked_domains': snap['blocked_domains'], 'common_options': snap['common_options'],
            'services': services, 'snapshot': snap,
            'by_host': {h: by_id[p['service_id']] for h, p in snap['by_host'].items()},
            'by_name': {n: by_id[p['service_id']] for n, p in snap['by_name'].items()}}


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


def profile(service_id: object) -> dict | None:
    """The full profile (routes, purposes, evidence) of a service id, else None."""
    return registry()['snapshot']['by_id'].get(service_id) if isinstance(service_id, str) else None


def v1_projection() -> dict:
    """The loaded registry in the version 1 file shape, for tools that read the screens' data."""
    reg = registry()
    view = _compat.v1_view(reg['snapshot'])
    view['common_options'] = [dict(o) for o in reg['common_options']]
    return view


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
