"""Loads the shipped service profiles as ONE snapshot (docs/DESK_SERVICE_PROFILES_SPEC.md
sections 3.1 and 9; slice P1).

`registry.json` version 2 is a data-only index: the blocked Clayrune discovery origins and
a list of `{id, file}` entries, one per service. Each `file` is `profiles/<id>.json` next
to the index and holds that service's whole profile (`profile_schema`). The loader reads
the index and EVERY listed profile, validates each, then checks the set as a whole, and only
then returns. Any failure raises `ProfileError`: a caller never receives a snapshot with
some profiles in it, so a bad release cannot half-load.

Checked across the set: an id or file listed twice, a file the index does not list (a
forgotten entry), a listed file that is missing, a path that is not exactly
`profiles/<id>.json` (no separator, no `..`, no symlink out of the directory), a profile whose
`service_id` is not its entry id, a host or a name two services claim, and a service host that
is, or sits under, a blocked Clayrune origin. Hosts are matched exactly (a dict lookup),
never as a substring or wildcard.

Read from next to this module, never from `data/` or `~/.clayrune`, so a user, an agent or a
page cannot add a host to it. The loader writes nothing and opens no network connection.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from mc.desk_connect import profile_schema as ps
from mc.desk_connect.profile_schema import ProfileError, need

INDEX_VERSION = 2
PROFILES_DIR = 'profiles'
_INDEX_FIELDS = {'version', 'blocked_domains', 'profiles'}


def _read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise ProfileError(f'cannot read {what} {path.name}: {e}') from e


def _entry_path(base: Path, entry: dict, where: str) -> Path:
    fid, fname = entry['id'], entry['file']
    need(fname == f'{PROFILES_DIR}/{fid}.json', where, f'file must be exactly {PROFILES_DIR}/{fid}.json')
    folder = (base / PROFILES_DIR)
    path = folder / f'{fid}.json'
    need(path.is_file(), where, f'{fname} does not exist')
    # A symlink (or junction) in the directory must not lead the loader out of it.
    need(path.resolve().parent == folder.resolve(), where, f'{fname} resolves outside {PROFILES_DIR}/')
    return path


def _blocked(host: str, blocked: list[str]) -> bool:
    return any(host == b or host.endswith('.' + b) for b in blocked)


def load_snapshot(index_path: Path) -> dict:
    """The validated snapshot, or `ProfileError`. Shape:
    `{version, blocked_domains, profiles: [profile], by_id, by_host, by_name, revisions}`.
    Each profile's routes carry a computed `verification`."""
    return snapshot_from_index(_read_json(index_path, 'index'), index_path.parent)


def snapshot_from_index(raw: Any, base: Path) -> dict:
    """`load_snapshot` for an index already parsed from `base`'s directory (one read of the
    index, not two, when the caller has looked at its version first)."""
    need(isinstance(raw, dict), 'index', 'must be an object')
    unknown = sorted(set(raw) - _INDEX_FIELDS)
    need(not unknown, 'index', f'unknown field(s): {", ".join(unknown)}')
    need(raw.get('version') == INDEX_VERSION, 'index', f'version must be {INDEX_VERSION}')
    blocked = raw.get('blocked_domains')
    need(isinstance(blocked, list) and all(ps.is_host(d) for d in blocked), 'blocked_domains', 'a list of domains')
    entries = raw.get('profiles')
    need(isinstance(entries, list) and entries, 'profiles', 'a non-empty list of {id, file}')

    items: list[tuple[str, dict]] = []
    ids: set[str] = set()
    listed: set[str] = set()
    for i, entry in enumerate(entries):
        where = f'profiles[{i}]'
        need(isinstance(entry, dict) and set(entry) == {'id', 'file'}, where, 'an entry is exactly {id, file}')
        need(isinstance(entry['id'], str) and ps._SLUG_RE.match(entry['id']), where, 'id must be a short slug')
        need(isinstance(entry['file'], str), where, 'file must be text')
        need(entry['id'] not in ids and entry['file'] not in listed, where, f'{entry["id"]} is listed twice')
        ids.add(entry['id'])
        listed.add(entry['file'])
        path = _entry_path(base, entry, where)
        prof = ps.check_profile(_read_json(path, 'profile'), entry['file'])
        need(prof['service_id'] == entry['id'], entry['file'], f'service_id "{prof["service_id"]}" is not the entry id "{entry["id"]}"')
        items.append((entry['file'], prof))
    folder = base / PROFILES_DIR
    on_disk = {f'{PROFILES_DIR}/{p.name}' for p in folder.glob('*.json')} if folder.is_dir() else set()
    need(on_disk <= listed, 'profiles', f'{", ".join(sorted(on_disk - listed))} is not listed in the index')
    return assemble(items, blocked, INDEX_VERSION)


def assemble(items: list[tuple[str, dict]], blocked: list[str], version: int) -> dict:
    """The set-level checks over already-validated profiles, shared by the v2 loader and the
    in-memory v1 conversion (`profile_compat`). `items` is `[(where, profile)]`; `where` names
    the file (or the v1 row) in an error."""
    profiles, by_id, by_host, by_name = [], {}, {}, {}
    for where, prof in items:
        need(prof['service_id'] not in by_id, where, f'service id {prof["service_id"]} is used twice')
        by_id[prof['service_id']] = prof
        for h in prof['hosts']:
            need(h not in by_host, where, f'host {h} is claimed by two services')
            need(not _blocked(h, blocked), where, f'host {h} is a blocked Clayrune origin')
            by_host[h] = prof
        for n in {ps.normalize(x) for x in [prof['label'], *prof['aliases']]}:
            need(n not in by_name, where, f'the name "{n}" is claimed by two services')
            by_name[n] = prof
        profiles.append(prof)
    return {'version': version, 'blocked_domains': list(blocked), 'profiles': profiles, 'by_id': by_id,
            'by_host': by_host, 'by_name': by_name, 'revisions': {p['service_id']: p['revision'] for p in profiles}}


def age_days(day: str | None, today: date | None = None) -> int | None:
    """Whole days since a profile date, or None when the date is unknown. Shown, never
    acted on: no freshness TTL is defined (spec section 9)."""
    if not day:
        return None
    return ((today or date.today()) - date.fromisoformat(day)).days
