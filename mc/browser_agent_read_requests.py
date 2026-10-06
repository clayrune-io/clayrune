"""Requests an agent's refused page read raises, and the single-use passes a human answers
them with (backlog b1e1b23c, MC-1059 piece A).

`POST /api/browser/read-digest` refuses a caller whose profile is off or whose site is not on
the list (mc/blueprints/browser_agent_read_routes.py). When the caller is a known agent
session, the refusal also opens ONE request here, and `browser_agent_read_ask` posts it into
that agent's chat as a card: "<agent> wants to read <site> through <profile>" with
Allow once / Always allow / Ignore. This module holds what the card points at.

What it deliberately cannot do:

  * An agent cannot create or widen a grant. `open_request` records a QUESTION; nothing in it
    changes what any read may do. A grant is made only by `grant_once` / the policy writer,
    both called from `browser_agent_read_request_routes`, which refuses an unattended caller
    and checks the dashboard passcode first.
  * A pass is not durable. In memory only: a server restart drops every pass, so a restart
    can only make the system stricter (fail closed). A pass lives `ONCE_TTL_S` and is spent by
    the first successful read it covers.
  * Cards cannot be spammed. One request per session+profile+domain per `CARD_RATE_S`
    (an Ignored request still counts), at most `MAX_PENDING_PER_SESSION` open at once, and
    the whole store is bounded.

Nothing here touches DATA_DIR or the repo; there is no sidecar to exclude.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from mc import browser_agent_read as policy

ONCE_TTL_S = 15 * 60
CARD_RATE_S = 10 * 60
MAX_PENDING_PER_SESSION = 5
MAX_RECORDS = 200
MAX_PASSES = 50

PENDING, ALLOWED_ONCE, ALWAYS, IGNORED = 'pending', 'allowed_once', 'always', 'ignored'

_lock = threading.Lock()
_requests: dict[str, dict[str, Any]] = {}        # request id -> record, oldest first
_last_card: dict[tuple[str, str, str], float] = {}
_passes: dict[str, dict[str, Any]] = {}          # pass id -> {profile, domain, session_id, expires}


def _now() -> float:
    return time.time()


def _public(rec: dict[str, Any]) -> dict[str, Any]:
    """What a card may show: no internals, no session handle beyond the display name."""
    return {k: rec[k] for k in ('id', 'agent_name', 'profile', 'domain', 'state', 'created_at')}


def open_request(*, session_id: str, project_id: str, agent_name: str, profile: str,
                 domain: str) -> tuple[dict[str, Any] | None, str]:
    """`(record, 'opened')`, or `(None, 'rate_limited' | 'too_many')`. Validation of the
    profile and domain is the caller's (`browser_agent_read_ask.ask`); this is bookkeeping."""
    now = _now()
    key = (session_id, profile, domain)
    with _lock:
        last = _last_card.get(key)
        if last is not None and now - last < CARD_RATE_S:
            return None, 'rate_limited'
        open_here = sum(1 for r in _requests.values()
                        if r['session_id'] == session_id and r['state'] == PENDING)
        if open_here >= MAX_PENDING_PER_SESSION:
            return None, 'too_many'
        rec = {'id': uuid.uuid4().hex[:12], 'session_id': session_id, 'project_id': project_id,
               'agent_name': agent_name, 'profile': profile, 'domain': domain,
               'state': PENDING, 'created_at': now}
        _requests[rec['id']] = rec
        _last_card[key] = now
        while len(_requests) > MAX_RECORDS:
            _requests.pop(next(iter(_requests)))
        for k in [k for k, t in _last_card.items() if now - t >= CARD_RATE_S]:
            del _last_card[k]
        return dict(rec), 'opened'


def get_request(rid: Any) -> dict[str, Any] | None:
    """The full record (server side only), or None for an unknown id."""
    with _lock:
        rec = _requests.get(rid) if isinstance(rid, str) else None
        return dict(rec) if rec else None


def public_request(rid: Any) -> dict[str, Any] | None:
    rec = get_request(rid)
    return _public(rec) if rec else None


def settle(rid: str, state: str) -> dict[str, Any] | None:
    """Move a still-pending request to `state`; None when it is unknown or already settled."""
    with _lock:
        rec = _requests.get(rid)
        if not rec or rec['state'] != PENDING:
            return None
        rec['state'] = state
        return dict(rec)


def grant_once(profile: str, domain: str, session_id: str) -> str:
    """A single-use pass for `profile` + `domain` + that session; returns its id. Called
    only by the passcode-gated decision route."""
    now = _now()
    pid = uuid.uuid4().hex
    with _lock:
        for k in [k for k, p in _passes.items() if p['expires'] <= now]:
            del _passes[k]
        while len(_passes) >= MAX_PASSES:
            _passes.pop(next(iter(_passes)))
        _passes[pid] = {'profile': profile, 'domain': domain, 'session_id': session_id,
                        'expires': now + ONCE_TTL_S}
    return pid


def find_once(profile: str, url: Any, session_id: str | None) -> dict[str, Any] | None:
    """The live pass covering this read, or None. A pass covers `url` when its host is the
    pass's domain or a subdomain of it (`policy.url_allowed`, the one definition), and only
    for the session it was granted to: a read that names no session never matches."""
    if not session_id:
        return None
    now = _now()
    with _lock:
        for pid, p in _passes.items():
            if (p['expires'] > now and p['profile'] == profile and p['session_id'] == session_id
                    and policy.url_allowed(url, [p['domain']])):
                return {'id': pid, 'profile': p['profile'], 'domain': p['domain'],
                        'session_id': p['session_id']}
    return None


def consume_once(pass_id: str) -> bool:
    """Spend a pass. True exactly once per pass, even under concurrent callers."""
    with _lock:
        return _passes.pop(pass_id, None) is not None


def reset_for_tests() -> None:
    with _lock:
        _requests.clear()
        _last_card.clear()
        _passes.clear()
