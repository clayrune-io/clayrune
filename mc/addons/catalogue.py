"""The add-on catalogue: what Clayrune may offer to download (MC-1022, spec §1-2).

The catalogue is CODE (`mc/addons/catalogue.json`, shipped in the app, changed
by commit and review). It is never read from `data/` or `~/.clayrune`, and the
steward fence blocks agent writes to it. An agent can only ask for an entry by
id; every fact the approval card shows comes from here or from the server's own
hashing, never from the request.

Licence filter (spec §1): only OSI-approved SPDX ids are accepted, copyleft
included. AGPL is accepted but flagged on the card. A non-OSI licence
(source-available, custom EULA) fails validation, which is what keeps
vendor-licensed tools out by rule.
"""
from __future__ import annotations

import json
import platform
import re
from pathlib import Path
from typing import Any

CATALOGUE_PATH = Path(__file__).with_name('catalogue.json')

# OSI-approved SPDX ids a catalogue entry may carry.
OSI_LICENCES = frozenset({
    '0BSD', 'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'ISC', 'MIT', 'MPL-2.0', 'Zlib', 'Unlicense',
    'EPL-2.0',
    'GPL-2.0-only', 'GPL-2.0-or-later', 'GPL-3.0-only', 'GPL-3.0-or-later',
    'LGPL-2.1-only', 'LGPL-2.1-or-later', 'LGPL-3.0-only', 'LGPL-3.0-or-later',
    'AGPL-3.0-only', 'AGPL-3.0-or-later',
})

_PERMISSIVE_LINE = 'Free to use, change and share; keep the licence notice with any copy you pass on.'
_GPL_LINE = 'Free to use; obligations apply only if you redistribute it.'
_LGPL_LINE = 'Free to use; obligations apply only if you redistribute it or change the library itself.'
_AGPL_LINE = ('This licence also binds anyone who offers a modified copy to users over a network. '
              'Using it unmodified on your own machine is not affected.')

_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,40}$')
_SHA_RE = re.compile(r'^[0-9a-f]{64}$')
_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,40}$')


class CatalogueError(ValueError):
    """The catalogue file is unreadable or an entry breaks a rule."""


def licence_line(spdx: str) -> str:
    """One plain sentence about what the licence asks of the user."""
    if spdx.startswith('AGPL'):
        return f'{spdx}: {_GPL_LINE}'
    if spdx.startswith('LGPL'):
        return f'{spdx}: {_LGPL_LINE}'
    if spdx.startswith('GPL'):
        return f'{spdx}: {_GPL_LINE}'
    return f'{spdx}: {_PERMISSIVE_LINE}'


def licence_flag(spdx: str) -> str:
    """The extra card line for a licence that needs one, else ''."""
    return _AGPL_LINE if spdx.startswith('AGPL') else ''


def platform_key() -> str:
    """`windows-x86_64`, `linux-aarch64`, `macos-arm64`, ... for this host."""
    system = platform.system().lower()
    system = {'darwin': 'macos'}.get(system, system)
    machine = platform.machine().lower()
    if machine in ('amd64', 'x86_64', 'x64'):
        machine = 'x86_64'
    elif machine in ('arm64', 'aarch64'):
        machine = 'arm64' if system == 'macos' else 'aarch64'
    return f'{system}-{machine}'


def os_name() -> str:
    return platform_key().split('-', 1)[0]


def _need(cond: bool, where: str, msg: str) -> None:
    if not cond:
        raise CatalogueError(f'{where}: {msg}')


def validate_entry(e: Any) -> None:
    """Raise `CatalogueError` unless `e` is a well-formed, licence-clean entry."""
    _need(isinstance(e, dict), 'entry', 'must be an object')
    aid = e.get('id')
    _need(isinstance(aid, str) and bool(_ID_RE.match(aid)), 'entry', f'bad id {aid!r}')
    where = f'entry {aid}'
    for k in ('name', 'description', 'homepage', 'licence', 'version', 'source', 'source_host', 'last_verified'):
        _need(isinstance(e.get(k), str) and e[k].strip() != '', where, f'missing {k}')
    _need(e['licence'] in OSI_LICENCES, where,
          f'licence {e["licence"]!r} is not an OSI-approved SPDX id; the catalogue refuses it')
    bins = e.get('binaries')
    _need(isinstance(bins, list) and bool(bins) and all(isinstance(b, str) and _NAME_RE.match(b) for b in bins),
          where, 'binaries must be a non-empty list of plain names')
    _need('ffplay' not in bins, where, 'ffplay may never be catalogued')
    probe = e.get('probe')
    _need(isinstance(probe, dict) and probe.get('binary') in bins and isinstance(probe.get('args'), list)
          and isinstance(probe.get('expect'), str) and probe['expect'] != '', where, 'bad probe')
    sysb = e.get('system_binaries', [])
    _need(isinstance(sysb, list) and all(b in bins for b in sysb), where, 'system_binaries must be catalogued binaries')
    plats = e.get('platforms')
    _need(isinstance(plats, dict), where, 'platforms must be an object')
    for key, p in plats.items():
        pw = f'{where} [{key}]'
        _need(isinstance(p, dict), pw, 'must be an object')
        _need(isinstance(p.get('url'), str) and p['url'].startswith('https://'), pw, 'url must be https')
        _need(isinstance(p.get('sha256'), str) and bool(_SHA_RE.match(p['sha256'])), pw, 'sha256 must be 64 hex chars')
        _need(p.get('archive') in ('zip', 'tar.xz'), pw, 'archive must be zip or tar.xz')
        _need(isinstance(p.get('download_bytes'), int) and p['download_bytes'] > 0, pw, 'download_bytes')
        _need(isinstance(p.get('installed_bytes'), int) and p['installed_bytes'] > 0, pw, 'installed_bytes')
        _need(isinstance(p.get('archive_root'), str), pw, 'archive_root')
        bp = p.get('binary_paths')
        _need(isinstance(bp, dict) and set(bp) == set(bins) and all(isinstance(v, str) and v for v in bp.values()),
              pw, 'binary_paths must name every catalogued binary')
        for path in list(bp.values()) + list(p.get('extra_files') or []):
            _need('..' not in path.replace('\\', '/').split('/') and not path.startswith(('/', '\\')),
                  pw, f'unsafe archive path {path!r}')
    cmds = e.get('commands', {})
    _need(isinstance(cmds, dict) and all(isinstance(v, str) for v in cmds.values()), where, 'commands')


def load(path: Path | None = None) -> dict[str, dict]:
    """The validated catalogue, keyed by add-on id. Re-read on every call (a
    few KB; tests swap `CATALOGUE_PATH`)."""
    p = path or CATALOGUE_PATH
    try:
        raw = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise CatalogueError(f'cannot read the add-on catalogue: {e}')
    _need(isinstance(raw, dict) and raw.get('schema') == 1 and isinstance(raw.get('addons'), list),
          'catalogue', 'schema 1 with an "addons" list is required')
    out: dict[str, dict] = {}
    for e in raw['addons']:
        validate_entry(e)
        _need(e['id'] not in out, 'catalogue', f'duplicate id {e["id"]}')
        out[e['id']] = e
    return out


def get(addon_id: str) -> dict | None:
    return load().get(addon_id)


def static_source(entry: dict, key: str | None = None) -> dict | None:
    """This host's download record, or None when the entry has no static build here."""
    return (entry.get('platforms') or {}).get(key or platform_key())


def command_for_user(entry: dict) -> str:
    return (entry.get('commands') or {}).get(os_name(), '')


def public_view(entry: dict) -> dict:
    """The catalogue-sourced facts the approval card shows. Everything here is
    ours; nothing is taken from a request."""
    src = static_source(entry)
    host = ''
    if src:
        m = re.match(r'https://([^/]+)/', src['url'])
        host = m.group(1) if m else ''
    return {
        'id': entry['id'], 'name': entry['name'], 'description': entry['description'],
        'homepage': entry['homepage'], 'version': entry['version'],
        'licence': entry['licence'], 'licence_line': licence_line(entry['licence']),
        'licence_flag': licence_flag(entry['licence']),
        'source': entry['source'], 'source_host': host or entry['source_host'],
        'trust_note': entry.get('trust_note', ''), 'last_verified': entry['last_verified'],
        'has_static_build': src is not None,
        'download_bytes': src['download_bytes'] if src else None,
        'installed_bytes': src['installed_bytes'] if src else None,
        'binaries': list(entry['binaries']),
        'command_for_user': command_for_user(entry),
        'platform_note': (entry.get('platform_notes') or {}).get(os_name(), ''),
        'can_adopt': bool(entry.get('system_binaries')),
    }
