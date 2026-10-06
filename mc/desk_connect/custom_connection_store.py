"""The record of approved user-chosen MCP servers (docs/DESK_SERVICE_PROFILES_SPEC.md,
section 6.1 and 6.5, slice U2a).

`~/.clayrune/desk_custom_connections.json`: operator state, outside the repo and outside
`data/projects/` (so `load_projects()` never reads it as a project). One record per server
a human approved with the passcode:

    {server_name, scope, project_id, project_path, fingerprint, operation, replaces?, approved_at,
     state, code}

It exists for three questions the MCP config alone cannot answer: WHAT was approved (the
operation the card showed, to compare with what is written now), WHO owns a server name (so a
legacy write path cannot overwrite an approved server without a new approval), and WHAT STATE
it is in (`registered`, or saved but `pending_runtime`, ...). It holds names only: an
operation carries vault entry names and variable names, never a value.

`replaces` is the earlier approved operation whose launch line may still be in the config while a
changed approval is being saved: it lets a failed Save of the change be retried (the config still
holds the earlier line) and is dropped when the new line is registered.

The key is `<scope>:<project_id>:<server_name>`. Writes are read-modify-write under one lock
and an atomic replace; a corrupt file reads as empty and is left in place, so a later write
cannot be mistaken for the first one without the log line saying so.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone

from mc import secrets_store as _vault
from mc.core import _log

FILE_NAME = 'desk_custom_connections.json'
_lock = threading.Lock()


def path():
    return _vault.clayrune_home() / FILE_NAME


def key(scope: str, project_id: str | None, server_name: str) -> str:
    return f'{scope}:{project_id or ""}:{server_name}'


def _read() -> dict:
    try:
        doc = json.loads(path().read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        _log(f'[desk_connect] custom connection record unreadable: {type(e).__name__}', flush=True)
        return {}
    conns = doc.get('connections') if isinstance(doc, dict) else None
    return conns if isinstance(conns, dict) else {}


def _write(conns: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f'.{p.name}.{uuid.uuid4().hex[:8]}.tmp')
    try:
        tmp.write_text(json.dumps({'version': 1, 'connections': conns}, indent=2, sort_keys=True), encoding='utf-8')
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def get(scope: str, project_id: str | None, server_name: str) -> dict | None:
    with _lock:
        rec = _read().get(key(scope, project_id, server_name))
    return rec if isinstance(rec, dict) else None


def all_records() -> list[dict]:
    with _lock:
        return [r for r in _read().values() if isinstance(r, dict)]


def is_managed(scope: str, project_id: str | None, server_name: str) -> bool:
    return get(scope, project_id, server_name) is not None


def find(scope: str, server_name: str, project_id: str | None = None, project_path: str | None = None) -> dict | None:
    """The record that owns `server_name` in `scope`, matched by project id OR project folder
    (callers of the MCP layer name one or the other). None when Desk approved no such server."""
    want = _norm_path(project_path)
    for rec in all_records():
        if rec.get('scope') != scope or rec.get('server_name') != server_name:
            continue
        if scope == 'global':
            return rec
        if (project_id and rec.get('project_id') == project_id) or (want and _norm_path(rec.get('project_path')) == want):
            return rec
    return None


def _norm_path(p) -> str:
    return os.path.normcase(os.path.normpath(str(p))) if isinstance(p, str) and p else ''


def put(op: dict, fingerprint: str, state: str, code: str = '', project_path: str | None = None) -> dict:
    """Record (or re-record) the approval of `op`. The approval time is kept when the same
    fingerprint is saved again, so a retry does not look like a new approval. `project_path`
    is the project's folder at approval (a project server's `.mcp.json` is found by it, so a
    write that names the folder and not the id is still recognised as this server's)."""
    scope, project_id = op['scope']['kind'], op['scope']['project_id']
    k = key(scope, project_id, op['server_name'])
    with _lock:
        conns = _read()
        old = conns.get(k) if isinstance(conns.get(k), dict) else None
        same = bool(old) and old.get('fingerprint') == fingerprint
        replaces = None
        if old and same:
            replaces = old.get('replaces')
        elif old:
            replaces = old['operation'] if old.get('state') == 'registered' else old.get('replaces')
        rec = {'server_name': op['server_name'], 'scope': scope, 'project_id': project_id,
               'project_path': project_path, 'fingerprint': fingerprint, 'operation': op,
               'approved_at': old['approved_at'] if same and old else datetime.now(timezone.utc).isoformat(),
               'state': state, 'code': code}
        if replaces:
            rec['replaces'] = replaces
        conns[k] = rec
        _write(conns)
    return rec


def set_state(scope: str, project_id: str | None, server_name: str, state: str, code: str = '') -> None:
    k = key(scope, project_id, server_name)
    with _lock:
        conns = _read()
        rec = conns.get(k)
        if isinstance(rec, dict):
            rec['state'], rec['code'] = state, code
            if state == 'registered':
                rec.pop('replaces', None)
            _write(conns)


def remove(scope: str, project_id: str | None, server_name: str) -> bool:
    k = key(scope, project_id, server_name)
    with _lock:
        conns = _read()
        if k not in conns:
            return False
        del conns[k]
        _write(conns)
    return True
