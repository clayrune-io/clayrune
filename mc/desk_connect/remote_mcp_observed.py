"""What a human-started check saw of an approved remote MCP server, and what a later check made of
it (docs/DESK_SERVICE_PROFILES_SPEC.md section 6.3, slice U2d): "record the ... observed capability
contract. A changed observable tool/auth schema suspends affected verification and asks for review
before adopting new capabilities."

`~/.clayrune/desk_remote_mcp_observed.json`: operator state, outside the repo and `data/projects/`.
One record per approved server (the key of `custom_connection_store`):

    {fingerprint, baseline, latest, status, checks, diff, checked_at}

`baseline` is the observation a human has seen and the card reports against. The first check records
it. After that a check compares what the server serves now with the baseline:

    baseline_recorded   first observation of this approval (or the first after a re-approval)
    unchanged           same server, protocol, capabilities and tool definitions
    changed             something differs. `checks` is CLEARED, `diff` names what moved, and the baseline
                        stays as it was until a human adopts the new observation (`adopt`)

`checks` is the evidence the server currently has. Today there is one kind, `handshake` (initialize
and tools/list answered); a change removes it. No purpose is ever recorded here: a remote server has no
purpose binding in this slice, and reaching a server is not evidence that it does what it is wanted for.

Server text is untrusted. Only bounded names and sha256 hashes of each tool's whole definition are
kept (a changed tool DESCRIPTION is a change, which is how a swapped-in instruction is caught); the
definitions themselves are not stored. A record is bound to the approval fingerprint: a re-approved
operation starts a new baseline. A damaged file is set aside and the next check starts a new baseline
and says so, never silently.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone

from mc import secrets_store as _vault
from mc.atomic_json import write_json_atomic
from mc.core import _log
from mc.desk_connect import custom_connection_store as _store

FILE_NAME = 'desk_remote_mcp_observed.json'
_lock = threading.Lock()


def path():
    return _vault.clayrune_home() / FILE_NAME


def digest_of(observed: dict) -> str:
    body = {k: observed.get(k) for k in ('protocol_version', 'server', 'capabilities', 'tools', 'truncated')}
    return 'sha256:' + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
                                      .encode('ascii')).hexdigest()


def _read() -> tuple[dict, bool]:
    """`(records, reset)`; `reset` is True when a damaged file was set aside."""
    p = path()
    try:
        doc = json.loads(p.read_text(encoding='utf-8'))
        recs = doc.get('records') if isinstance(doc, dict) else None
        if isinstance(recs, dict):
            return recs, False
        raise ValueError('not a record file')
    except FileNotFoundError:
        return {}, False
    except (OSError, ValueError) as e:
        aside = p.with_name(f'{p.name}.corrupt-{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}')
        try:
            os.replace(p, aside)
        except OSError as e2:
            _log(f'[desk_connect] remote MCP observation record unreadable and not movable: {type(e2).__name__}', flush=True)
            raise
        _log(f'[desk_connect] remote MCP observation record unreadable ({type(e).__name__}); set aside as {aside.name}',
             flush=True)
        return {}, True


def _write(recs: dict) -> None:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(p, {'version': 1, 'records': recs}, indent=2, sort_keys=True)


def get(scope: str, project_id: str | None, server_name: str, fingerprint: str | None = None) -> dict | None:
    """The record for this approval (None when there is none, or it belongs to another fingerprint)."""
    with _lock:
        recs, _ = _read()
    rec = recs.get(_store.key(scope, project_id, server_name))
    if not isinstance(rec, dict) or (fingerprint is not None and rec.get('fingerprint') != fingerprint):
        return None
    return rec


def _repeated(tools: list) -> set:
    """The tool names a list offers more than once."""
    seen: set = set()
    return {t['name'] for t in tools if t['name'] in seen or seen.add(t['name'])}


def diff(baseline: dict, now: dict) -> list[dict]:
    """What differs between two observations, as `{what, detail}` for a person to read."""
    out: list[dict] = []
    if baseline.get('server') != now.get('server'):
        out.append({'what': 'server identity', 'detail': 'the name or version the server reports changed'})
    if baseline.get('protocol_version') != now.get('protocol_version'):
        out.append({'what': 'protocol version', 'detail': f'{baseline.get("protocol_version")} to {now.get("protocol_version")}'})
    if baseline.get('capabilities') != now.get('capabilities'):
        out.append({'what': 'capabilities', 'detail': 'the kinds of thing the server offers changed'})
    old_tools, new_tools = baseline.get('tools') or [], now.get('tools') or []
    old = {t['name']: t['hash'] for t in old_tools}
    new = {t['name']: t['hash'] for t in new_tools}
    named = len(out)
    for n in sorted(new.keys() - old.keys()):
        out.append({'what': 'new tool', 'detail': n})
    for n in sorted(old.keys() - new.keys()):
        out.append({'what': 'tool removed', 'detail': n})
    shared = _repeated(old_tools) | _repeated(new_tools)
    for n in sorted(n for n in new.keys() & old.keys() if new[n] != old[n] and n not in shared):
        out.append({'what': 'tool changed', 'detail': f'{n}: its description or inputs are different'})
    # Keyed by name, tools that share a name (or whose name is not shown) overwrite each other above, so which
    # of them changed cannot be read from the dicts. The sorted (name, hash) lists still tell whether any did.
    if len(out) == named and sorted((t['name'], t['hash']) for t in old_tools) != sorted((t['name'], t['hash']) for t in new_tools):
        out.append({'what': 'tool changed', 'detail': 'the tools offered are different, and some share a name or have a name '
                                                      'that is not shown, so which one cannot be told'})
    if _repeated(old_tools) != _repeated(new_tools):
        out.append({'what': 'duplicate tool names', 'detail': 'the server now offers more than one tool under the same name, '
                                                              'or no longer does'})
    if bool(baseline.get('truncated')) != bool(now.get('truncated')):
        out.append({'what': 'tool list size', 'detail': 'the list became too long to read in full, or stopped being so'})
    return out


def _handshake(observed: dict) -> dict:
    return {'handshake': {'ok': True, 'at': observed['at'], 'covers': 'initialize and tools/list were answered',
                          'observed': digest_of(observed)}}


def record(scope: str, project_id: str | None, server_name: str, fingerprint: str, observed: dict) -> dict:
    """Apply a fresh observation to the approval's record and return it."""
    k = _store.key(scope, project_id, server_name)
    with _lock:
        recs, reset = _read()
        old = recs.get(k) if isinstance(recs.get(k), dict) else None
        if not old or old.get('fingerprint') != fingerprint or not old.get('baseline'):
            rec = {'fingerprint': fingerprint, 'baseline': observed, 'latest': observed, 'status': 'baseline_recorded',
                   'checks': _handshake(observed), 'diff': [], 'checked_at': observed['at']}
            if reset:
                rec['note'] = 'The earlier record could not be read, so this observation is a new baseline.'
        else:
            d = diff(old['baseline'], observed)
            rec = {**old, 'latest': observed, 'checked_at': observed['at'], 'diff': d}
            rec.pop('note', None)
            if d:
                rec['status'], rec['checks'] = 'changed', {}
            else:
                rec['status'], rec['checks'] = 'unchanged', _handshake(observed)
        recs[k] = rec
        _write(recs)
    return rec


def adopt(scope: str, project_id: str | None, server_name: str, fingerprint: str, observed_digest: str) -> dict | None:
    """A human accepts the latest observation as the new baseline. Only the observation the person
    was shown (`observed_digest`) is adopted; returns the record, or None when there is nothing to
    adopt (no record, not `changed`, or the latest observation is no longer the one shown)."""
    k = _store.key(scope, project_id, server_name)
    with _lock:
        recs, _ = _read()
        rec = recs.get(k)
        if not isinstance(rec, dict) or rec.get('fingerprint') != fingerprint or rec.get('status') != 'changed' \
                or digest_of(rec['latest']) != observed_digest:
            return None
        rec = {**rec, 'baseline': rec['latest'], 'status': 'adopted', 'diff': [], 'checks': _handshake(rec['latest'])}
        recs[k] = rec
        _write(recs)
    return rec


def forget(scope: str, project_id: str | None, server_name: str) -> None:
    """Drop the record of a server whose approval was removed. Never raises."""
    k = _store.key(scope, project_id, server_name)
    try:
        with _lock:
            recs, _ = _read()
            if k in recs:
                del recs[k]
                _write(recs)
    except Exception as e:
        _log(f'[desk_connect] remote MCP observation record could not be dropped: {type(e).__name__}', flush=True)


def summary(rec: dict | None) -> dict:
    """The part of a record a list row shows."""
    if not rec:
        return {'status': 'never_checked', 'checks': {}, 'diff': [], 'checked_at': None}
    return {'status': rec['status'], 'checks': rec.get('checks') or {}, 'diff': rec.get('diff') or [],
            'checked_at': rec.get('checked_at'), 'review_needed': rec['status'] == 'changed',
            'observed': digest_of(rec['latest']) if rec.get('latest') else None}
