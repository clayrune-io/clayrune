"""The curated MCP packages Connect by address may activate: DATA, not code
(`mcp_catalogue.json`; docs/DESK_CONNECT_BY_URL_SPEC.md, Dave 2026-10-03, "curated
packages first; arbitrary packages wait for installer hardening").

Every fact the review card shows (package, pinned version, integrity hash, licence,
size, purpose, permissions) and every byte of what is later launched comes from this
file or from the server's own code, never from a request, a page or a package's
README. Nothing here reads a README: the approval does not depend on any text the
package author supplied.

The file ships in the app and changes by commit and review. It is read from next to
this module, never from `data/` or `~/.clayrune`, so a user, an agent or a page cannot
add a package to it. An entry can only be a pinned npm package launched by `npx`
(`kind: npx`): there is no field for a command, an argument, a URL or an environment
value, so no entry can carry executable configuration of its own. A remote endpoint
needs its own reviewed kind and capability approval; none exists yet, and a file
that names one is refused.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mc import mcp as _mcp
from mc import secrets_store as _vault
from mc.addons.catalogue import OSI_LICENCES

CATALOGUE_PATH = Path(__file__).with_name('mcp_catalogue.json')

KINDS = ('npx',)
_ID_RE = re.compile(r'^[a-z0-9_]{1,40}$')
_PACKAGE_RE = re.compile(r'^(@[a-z0-9][a-z0-9._-]{0,60}/)?[a-z0-9][a-z0-9._-]{0,80}$')
_VERSION_RE = re.compile(r'^\d{1,4}\.\d{1,4}\.\d{1,4}$')           # exact: no range, tag or prerelease
_INTEGRITY_RE = re.compile(r'^sha512-[A-Za-z0-9+/]{86}==$')          # npm's `dist.integrity`
_ENV_RE = re.compile(r'^[A-Z][A-Z0-9_]{0,63}$')
_FIELDS = {'id', 'service', 'server_name', 'kind', 'package', 'version', 'integrity', 'licence',
           'unpacked_bytes', 'source', 'purpose', 'permissions', 'credential'}
_CRED_FIELDS = {'env', 'vault', 'label', 'hint'}
MAX_TEXT = 400


class CatalogueError(ValueError):
    """The catalogue file is unreadable or an entry breaks a rule."""


def _need(cond: object, where: str, msg: str) -> None:
    if not cond:
        raise CatalogueError(f'{where}: {msg}')


def _text(v: Any, where: str, what: str) -> str:
    _need(isinstance(v, str) and v.strip() and len(v) <= MAX_TEXT, where, f'{what} is required text of at most {MAX_TEXT} characters')
    return v.strip()


def _check(raw: Any, i: int) -> dict:
    where = f'packages[{i}]'
    _need(isinstance(raw, dict), where, 'a package must be an object')
    unknown = sorted(set(raw) - _FIELDS)
    _need(not unknown, where, f'unknown field(s): {", ".join(unknown)}')
    _need(isinstance(raw.get('id'), str) and _ID_RE.match(raw['id']), where, 'id must be a short slug')
    _need(isinstance(raw.get('service'), str) and _ID_RE.match(raw['service']), where, 'service must be a registry id')
    name = raw.get('server_name')
    _need(isinstance(name, str) and _mcp.validate_name(name) is None, where, 'server_name must be a valid MCP server name')
    _need(raw.get('kind') in KINDS, where, f'kind must be one of {KINDS}: only pinned npm packages can be launched')
    _need(isinstance(raw.get('package'), str) and _PACKAGE_RE.match(raw['package']), where, 'package must be an npm package name')
    _need(isinstance(raw.get('version'), str) and _VERSION_RE.match(raw['version']), where,
          'version must be one exact x.y.z: no range, tag or prerelease')
    _need(isinstance(raw.get('integrity'), str) and _INTEGRITY_RE.match(raw['integrity']), where,
          "integrity must be the package's sha512 `dist.integrity`")
    _need(raw.get('licence') in OSI_LICENCES, where, 'licence must be an OSI-approved SPDX id (the add-on catalogue rule)')
    size = raw.get('unpacked_bytes')
    _need(isinstance(size, int) and not isinstance(size, bool) and 0 < size < 2 ** 31, where, 'unpacked_bytes must be a positive integer')
    src = raw.get('source')
    _need(isinstance(src, str) and src.startswith('https://') and len(src) <= 200, where, 'source must be an https address')
    perms = raw.get('permissions')
    _need(isinstance(perms, list) and 1 <= len(perms) <= 6, where, 'permissions must be a short list of sentences')
    cred = raw.get('credential')
    _need(isinstance(cred, dict) and not (set(cred) - _CRED_FIELDS) and set(cred) >= {'env', 'vault', 'label'},
          where, f'credential needs env, vault and label (and may have hint): {sorted(_CRED_FIELDS)}')
    _need(isinstance(cred['env'], str) and _ENV_RE.match(cred['env']), where, 'credential.env must be an environment variable name')
    _need(isinstance(cred['vault'], str) and _vault.valid_name(cred['vault']) and not _vault.is_server_internal(cred['vault']),
          where, 'credential.vault must be an ordinary vault name')
    return {
        'id': raw['id'], 'service': raw['service'], 'server_name': name, 'kind': raw['kind'],
        'package': raw['package'], 'version': raw['version'], 'integrity': raw['integrity'],
        'licence': raw['licence'], 'unpacked_bytes': size, 'source': src,
        'purpose': _text(raw.get('purpose'), where, 'purpose'),
        'permissions': [_text(p, where, 'a permission') for p in perms],
        'credential': {'env': cred['env'], 'vault': cred['vault'],
                       'label': _text(cred['label'], where, 'credential.label'),
                       'hint': _text(cred['hint'], where, 'credential.hint') if cred.get('hint') else ''},
    }


def load(path: Path | None = None) -> dict:
    """Parse and validate the catalogue. Raises CatalogueError; never returns a
    half-valid one. `{'by_service': {service id: entry}}`."""
    p = path or CATALOGUE_PATH
    try:
        raw = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise CatalogueError(f'cannot read {p.name}: {e}') from e
    _need(isinstance(raw, dict) and raw.get('version') == 1, 'catalogue', 'version must be 1')
    pkgs = raw.get('packages')
    if not isinstance(pkgs, list):
        raise CatalogueError('catalogue: packages must be a list')
    by_service: dict[str, dict] = {}
    seen_names: set[str] = set()
    for i, r in enumerate(pkgs):
        e = _check(r, i)
        _need(e['service'] not in by_service, f'packages[{i}]', f'service {e["service"]} has two packages')
        _need(e['server_name'] not in seen_names, f'packages[{i}]', f'server name {e["server_name"]} is used twice')
        seen_names.add(e['server_name'])
        by_service[e['service']] = e
    return {'version': 1, 'by_service': by_service}


_cache: dict | None = None


def catalogue() -> dict:
    global _cache
    if _cache is None:
        _cache = load()
    return _cache


def for_service(service_id) -> dict | None:
    """The curated package of this service, or None. A copy: callers cannot edit the
    reviewed data."""
    if not isinstance(service_id, str):
        return None
    e = catalogue()['by_service'].get(service_id)
    return json.loads(json.dumps(e)) if e else None


def pins(entry: dict) -> dict:
    """What an approval binds to: the exact package, version and integrity hash."""
    return {'package': entry['package'], 'version': entry['version'], 'integrity': entry['integrity']}


def card(entry: dict) -> dict:
    """The review card (MC-1022 shape: source, pinned version/hash, licence, size,
    purpose), every field from the catalogue. No README text is in it."""
    return {
        'id': entry['id'], 'name': entry['package'], 'kind': entry['kind'],
        'source': entry['source'], 'version': entry['version'], 'integrity': entry['integrity'],
        'licence': entry['licence'], 'unpacked_bytes': entry['unpacked_bytes'],
        'purpose': entry['purpose'], 'permissions': list(entry['permissions']),
        'server_name': entry['server_name'],
        'credential': {'env': entry['credential']['env'], 'vault': entry['credential']['vault'],
                       'label': entry['credential']['label']},
        'pins': pins(entry),
    }
