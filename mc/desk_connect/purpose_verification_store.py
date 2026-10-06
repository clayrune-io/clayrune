"""Where purpose-verification records are kept between server restarts (slice P2 follow-up).

`purpose_verification` decides what a record means; this module only reads and writes them.
The file is `desk_purpose_verification.json`, a sibling of the Desk store (`data/desk.json`),
so it sits outside DATA_DIR (`data/projects/`) for the reason CLAUDE.md gives: anything in
DATA_DIR that is not a project record becomes a malformed project and breaks both restart
endpoints. The path is derived from `desk.STORE_PATH` (wired by `server.py`), so there is one
place the Desk's data root is decided; with the Desk store unwired nothing is persisted.

A record carries what the status derivation needs (`fingerprint`, vault `stamps`) and what a
person reads (`identity`, what the check proved about the account, and `at`, when). A loaded
record is still only a CLAIM: `purpose_verification.capability_state` re-checks it against the
binding and the vault on every read, so a stale file can never make a changed route read
verified. Every failure here reads as "nothing remembered", the safe direction: a missing,
unreadable or corrupt file loads empty (a corrupt one is kept beside it as `.corrupt` so it
is not silently overwritten), a malformed entry is dropped alone, and a failed write is logged
and leaves the in-memory records in force.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from mc import desk as _desk
from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.core import _log

FILE_NAME = 'desk_purpose_verification.json'
VERSION = 1


def path() -> Path | None:
    """The sidecar's path, or None while the Desk store is unwired."""
    store = _desk.STORE_PATH
    return store.with_name(FILE_NAME) if store is not None else None


def _stamps_in(raw) -> dict | None:
    """JSON has no tuples: a stamp pair comes back as a list and must compare equal to the
    `(created_at, updated_at)` tuple `purpose_verification._stamps` builds. None = malformed."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for name, pair in raw.items():
        if pair is None:
            out[name] = None
        elif isinstance(pair, (list, tuple)) and len(pair) == 2:
            out[name] = tuple(pair)
        else:
            return None
    return out


def _entry_in(raw) -> tuple[tuple, dict] | None:
    if not isinstance(raw, dict):
        return None
    key = tuple(raw.get(k) for k in ('account_id', 'purpose', 'capability'))
    stamps = _stamps_in(raw.get('stamps'))
    at, identity = raw.get('at'), raw.get('identity')
    if (not all(isinstance(k, str) and k for k in key) or stamps is None
            or not isinstance(raw.get('fingerprint'), str) or not isinstance(at, str) or not at
            or not isinstance(identity, str)):
        return None
    return key, {'fingerprint': raw['fingerprint'], 'stamps': stamps, 'at': at, 'identity': identity}


def load(limit: int) -> 'OrderedDict[tuple, dict]':
    """The remembered records, oldest first, at most `limit` (the newest). Never raises."""
    out: 'OrderedDict[tuple, dict]' = OrderedDict()
    p = path()
    if p is None or not p.exists():
        return out
    try:
        data = json.loads(read_text_with_retry(p, encoding='utf-8'))
        rows = data['records']
        if not isinstance(rows, list):
            raise ValueError('records is not a list')
    except (OSError, ValueError, KeyError, TypeError) as e:
        _log(f'[desk_connect] purpose verification file {p.name} is unreadable ({type(e).__name__}); starting with none', flush=True)
        _set_aside(p)
        return out
    dropped = 0
    for raw in rows:
        got = _entry_in(raw)
        if got is None:
            dropped += 1
            continue
        out[got[0]] = got[1]
    if dropped:
        _log(f'[desk_connect] purpose verification file: dropped {dropped} malformed record(s)', flush=True)
    while len(out) > limit:
        out.popitem(last=False)
    return out


def _set_aside(p: Path) -> None:
    try:
        p.replace(p.with_name(p.name + '.corrupt'))
    except OSError as e:
        _log(f'[desk_connect] purpose verification: could not set the unreadable file aside: {e}', flush=True)


def save(records: 'OrderedDict[tuple, dict]') -> bool:
    """Write every record. True when written; False (logged) when not. Never raises."""
    p = path()
    if p is None:
        return False
    rows = [{'account_id': k[0], 'purpose': k[1], 'capability': k[2], 'fingerprint': r['fingerprint'],
             'stamps': {n: (list(v) if v is not None else None) for n, v in r['stamps'].items()},
             'at': r['at'], 'identity': r['identity']} for k, r in records.items()]
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(p, {'version': VERSION, 'records': rows}, indent=2, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as e:
        _log(f'[desk_connect] purpose verification could not be saved: {type(e).__name__}: {e}', flush=True)
        return False
    return True
