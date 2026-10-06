"""A sign-in held in server memory until the Save (Dave 2026-10-05, option B, after Ron:
"if step 3 is the login details, all options should be covered there").

The Connect flow's Details step now runs the browser sign-in itself. The Connect spec says
nothing is written before the one Save (docs/DESK_CONNECT_BY_URL_SPEC.md), so the token the
callback receives is not stored: it is HELD here, and the Save claims it and writes it with the
account and the app's Client ID in ONE passcode-gated commit (`provider_commit`, the existing
undo stack). Backing out leaves no vault entry and no Desk account.

Rules (Wren audits this file):

  * **Memory only.** The record lives in a dict in this process. It is never written to disk,
    a log line, a response body or the transcript; a restart forgets it and the person signs in
    again (the UI says so). Nothing here can read it back out except `peek`, which only
    `desk_oauth.commit_held` calls.
  * **Bound to who started it.** `start` (human-only, dashboard passcode) returns a `claim` secret
    in its own response, and only that response. A held sign-in is claimed or cancelled with its
    flow id AND that claim (constant-time compare). The flow id alone, which the poll route shows
    to anyone who knows it, opens nothing. (The server has no dashboard-session identity to bind
    to: the passcode is checked per request, so the claim is the binding.)
  * **Short.** `HOLD_TTL_S` from the moment the token is held (the caller passes
    `min(FLOW_TTL_S, 15 min)`). On expiry, cancel or a failed claim the token is dropped and the
    vendor token is revoked where the vendor offers a revocation endpoint: best effort, logged,
    never raised. After a restart nothing can revoke it (the process that held it is gone); the
    vendor's own expiry applies.
  * **Single use.** `consume` removes the entry the moment a Save has written it.

Window of exposure to say out loud: the record sits in this process's memory for up to the TTL,
beside the PKCE verifier the flow already holds there. A process memory dump or a debugger on the
server would see it. The same process receives the callback code and the token response in the
clear, so this adds time, not a new place.
"""
from __future__ import annotations

import hmac
import threading
import time
from typing import Any

from mc.core import _log


class HoldError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


_lock = threading.RLock()
_held: dict[str, dict[str, Any]] = {}      # flow_id -> {claim, service, account_id, rec, app, expires, timer}

GONE = 'The held sign-in is no longer there (it timed out, was cancelled, or the server restarted). Sign in again.'


def put(flow_id: str, claim: str, service: str, account_id: str | None, rec: dict, app: dict | None, ttl: float) -> None:
    """Hold `rec` (the full sign-in record, tokens included) for `ttl` seconds."""
    timer = threading.Timer(ttl, _expire, args=(flow_id,))
    timer.daemon = True
    with _lock:
        old = _held.pop(flow_id, None)
        _held[flow_id] = {'claim': claim, 'service': service, 'account_id': account_id, 'rec': rec,
                          'app': dict(app or {}), 'expires': time.time() + ttl, 'timer': timer}
    if old:
        old['timer'].cancel()
    timer.start()


def _sweep() -> None:
    now = time.time()
    with _lock:
        stale = [k for k, e in _held.items() if e['expires'] <= now]
    for k in stale:
        discard(k)


def alive(flow_id: str) -> int:
    """Seconds left on a held sign-in, 0 when there is none. Reveals nothing else. Never
    sweeps: callers hold other locks, and dropping a held sign-in is a network call."""
    with _lock:
        e = _held.get(flow_id)
        return max(0, int(e['expires'] - time.time())) if e else 0


def peek(flow_id: str, claim: str, service: str) -> dict[str, Any]:
    """The held entry for a Save to read. A wrong or missing claim, a different service or a
    gone entry is the same refusal: nothing about which part was wrong."""
    _sweep()
    with _lock:
        e = _held.get(flow_id) if isinstance(flow_id, str) else None
        ok = (e is not None and isinstance(claim, str) and hmac.compare_digest(e['claim'].encode(), claim.encode())
              and e['service'] == service)
        if not ok or e is None:
            raise HoldError('hold_missing', GONE)
        return e


def consume(flow_id: str) -> None:
    """A Save has written the sign-in: forget it (no revoke: it is now the saved one)."""
    with _lock:
        e = _held.pop(flow_id, None)
    if e:
        e['timer'].cancel()
        e['rec'] = None
        e['app'] = {}


def discard(flow_id: str, claim: str | None = None) -> bool:
    """Drop a held sign-in and revoke it at the vendor (best effort). With a `claim`, only that
    claim's holder may; without one (expiry, a failed claim) the server itself is acting."""
    with _lock:
        e = _held.get(flow_id)
        if e is None:
            return False
        if claim is not None and not (isinstance(claim, str) and hmac.compare_digest(e['claim'].encode(), claim.encode())):
            return False
        _held.pop(flow_id, None)
    e['timer'].cancel()
    rec, app, service = e['rec'], e['app'], e['service']
    e['rec'] = None
    e['app'] = {}
    try:
        from mc import desk_oauth as _oauth      # lazy: desk_oauth imports this module
        revoked = _oauth.revoke_record(service, rec, app=app)
    except Exception as ex:                      # best effort: a vendor refusing must not raise out of a timer
        revoked = None
        _log(f'[desk_oauth] revoking a held {service} sign-in raised {type(ex).__name__}', flush=True)
    _log(f'[desk_oauth] held {service} sign-in dropped (revoked={revoked})', flush=True)
    return True


def _expire(flow_id: str) -> None:
    discard(flow_id)


def _forget_all_for_tests() -> None:
    with _lock:
        entries = list(_held.values())
        _held.clear()
    for e in entries:
        e['timer'].cancel()
