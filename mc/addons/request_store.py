"""Add-on install requests: the cards waiting on the human (MC-1022, spec §4).

A request is a durable record that an agent (or a Desk render) asked for an
add-on. Creating one installs nothing. The store is `~/.clayrune/addon_requests.json`,
deliberately OUTSIDE `~/.clayrune/addons/` so filing a request leaves the
install tree and `installed.json` byte-identical (spec §7 done-when 1).

States: `pending` -> `installing` -> `installed` | `failed`; `pending` ->
`declined` | `expired`. A failed request can be approved again (Retry); nothing
is retried automatically. Decline is keyed on the base add-on id alone with a
30-day TTL, so rewording the reason, or asking for `system:ffmpeg` after
`ffmpeg` was declined, raises no new card. The user can still install from
Settings.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mc.addons.manifest import now_iso
from mc.atomic_json import write_json_atomic
from mc.core import _log
from mc.secrets_store import clayrune_home

DECLINE_TTL_DAYS = 30
PENDING_TTL_DAYS = 14
REASON_MAX = 300

_lock = threading.RLock()

LIVE_STATES = ('pending', 'installing', 'failed')


def store_path() -> Path:
    return clayrune_home() / 'addon_requests.json'


def base_id(addon_ref: str) -> str:
    """`system:ffmpeg` and `ffmpeg` are the same decline/dedupe key."""
    return addon_ref.split(':', 1)[1] if addon_ref.startswith('system:') else addon_ref


def clean_reason(text: object) -> str:
    """The agent's reason as short single-line plain text. It is untrusted: the
    card shows it quoted under "The agent says", never as markup."""
    s = re.sub(r'[\x00-\x1f\x7f]+', ' ', str(text or ''))
    s = re.sub(r'\s+', ' ', s).strip()
    return s[:REASON_MAX]


def _load() -> dict:
    try:
        raw = json.loads(store_path().read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'schema': 1, 'requests': {}}
    except (OSError, ValueError) as e:
        _log(f'[addons] request store unreadable, treating as empty: {e}', flush=True)
        return {'schema': 1, 'requests': {}}
    if not isinstance(raw, dict) or not isinstance(raw.get('requests'), dict):
        return {'schema': 1, 'requests': {}}
    return raw


def _save(d: dict) -> None:
    store_path().parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(store_path(), d, indent=2)


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.strptime(ts or '', '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso_in(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')


def get(request_id: str) -> dict | None:
    with _lock:
        r = _load()['requests'].get(request_id)
    return dict(r) if r else None


def list_requests(states: tuple[str, ...] | None = None) -> list[dict]:
    with _lock:
        rows = [dict(r) for r in _load()['requests'].values()]
    if states:
        rows = [r for r in rows if r.get('state') in states]
    return sorted(rows, key=lambda r: r.get('created_at', ''), reverse=True)


def update(request_id: str, **fields) -> dict | None:
    with _lock:
        d = _load()
        r = d['requests'].get(request_id)
        if r is None:
            return None
        r.update(fields, updated_at=now_iso())
        _save(d)
        return dict(r)


def find_live(addon_ref: str) -> dict | None:
    """The open request for this add-on (pending/installing/failed), if any."""
    want = base_id(addon_ref)
    for r in list_requests(LIVE_STATES):
        if base_id(r['addon_id']) == want:
            return r
    return None


def active_decline(addon_ref: str) -> dict | None:
    """A decline for this add-on that is still inside its 30-day TTL."""
    want = base_id(addon_ref)
    now = datetime.now(timezone.utc)
    for r in list_requests(('declined',)):
        until = _parse(r.get('declined_until'))
        if base_id(r['addon_id']) == want and until and until > now:
            return r
    return None


def create(addon_ref: str, kind: str, reason: str, requested_by: dict | None, *,
           adoption: dict | None = None) -> tuple[dict, str]:
    """File a request. Returns `(record, outcome)`:
    `created`, `existing` (same add-on already pending: touched, not
    duplicated) or `declined` (inside the 30-day decline TTL: no new card)."""
    with _lock:
        declined = active_decline(addon_ref)
        if declined is not None:
            return declined, 'declined'
        live = find_live(addon_ref)
        if live is not None:
            r = update(live['id'], last_touched_at=now_iso())
            return r or live, 'existing'
        d = _load()
        rid = uuid.uuid4().hex[:12]
        now = now_iso()
        rec = {
            'id': rid, 'addon_id': addon_ref, 'kind': kind, 'state': 'pending',
            'reason': clean_reason(reason), 'requested_by': requested_by or {},
            'created_at': now, 'updated_at': now, 'last_touched_at': now,
            'adoption': adoption, 'error': '', 'progress': None,
        }
        d['requests'][rid] = rec
        _save(d)
        return dict(rec), 'created'


def claim(request_id: str, from_states: tuple[str, ...] = ('pending', 'failed')) -> dict | None:
    """Move a request to `installing` if it is still in one of `from_states`.
    The first caller wins; a concurrent second one gets None (the route's 409)."""
    with _lock:
        d = _load()
        r = d['requests'].get(request_id)
        if r is None or r.get('state') not in from_states:
            return None
        r.update(state='installing', error='', approved_at=now_iso(), updated_at=now_iso())
        _save(d)
        return dict(r)


def decline(request_id: str) -> dict | None:
    with _lock:
        d = _load()
        r = d['requests'].get(request_id)
        if r is None or r.get('state') not in ('pending', 'failed'):
            return None
        r.update(state='declined', declined_at=now_iso(), declined_until=_iso_in(DECLINE_TTL_DAYS),
                 updated_at=now_iso())
        _save(d)
        return dict(r)


def expire_stale() -> int:
    """Pending requests nobody has re-touched for 14 days become `expired`,
    which clears their Inbox entry. A job still waiting re-files each poll and
    so keeps its request alive."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=PENDING_TTL_DAYS)
    n = 0
    with _lock:
        d = _load()
        for r in d['requests'].values():
            if r.get('state') == 'pending':
                touched = _parse(r.get('last_touched_at') or r.get('created_at'))
                if touched and touched < cutoff:
                    r.update(state='expired', updated_at=now_iso())
                    n += 1
        if n:
            _save(d)
    return n


def fail_interrupted() -> int:
    """Startup: an `installing` request has no worker any more."""
    n = 0
    with _lock:
        d = _load()
        for r in d['requests'].values():
            if r.get('state') == 'installing':
                r.update(state='failed', error='interrupted by a restart; approve again to retry',
                         updated_at=now_iso())
                n += 1
        if n:
            _save(d)
    return n
