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
    again (the UI says so). Nothing here can read it back out except `take`, which only
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
  * **Single use.** `take` removes the entry from memory in ONE step when a Save claims it (no
    peek-then-read, so an expiry cannot revoke it halfway through the write); `give_back` returns
    it when the write failed, `consume` forgets it once the Save landed. Nothing here sweeps or
    revokes inside a caller's lock: `take`/`account_of` refuse an expired entry and a late one is
    dropped on its own thread.
  * **The pane profile.** `put` takes an `on_drop` the flow uses to remove the browser profile it
    created (`desk_oauth_profile`); it runs on every DROP (expiry, cancel, replacement) and never
    on a Save.

Window of exposure to say out loud: the record sits in this process's memory for up to the TTL,
beside the PKCE verifier the flow already holds there. A process memory dump or a debugger on the
server would see it. The same process receives the callback code and the token response in the
clear, so this adds time, not a new place.
"""
from __future__ import annotations

import hmac
import threading
import time
from typing import Any, Callable

from mc.core import _log


class HoldError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code = code
        self.status = status


_lock = threading.RLock()
_held: dict[str, dict[str, Any]] = {}      # flow_id -> {claim, service, account_id, rec, app, expires, timer}

APP_MISMATCH = ('The app Client ID has changed since you signed in, so that sign-in does not belong to what '
                'you are saving. Sign in again.')
GONE = 'The held sign-in is no longer there (it timed out, was cancelled, or the server restarted). Sign in again.'


def put(flow_id: str, claim: str, service: str, account_id: str | None, rec: dict, app: dict | None, ttl: float,
        on_drop: Callable[[], None] | None = None) -> None:
    """Hold `rec` (the full sign-in record, tokens included) for `ttl` seconds. `on_drop` runs
    once when the held sign-in is DROPPED (expiry, cancel, a replacement), after the vendor
    revoke, never when a Save consumed it: the flow uses it to remove the pane profile it created."""
    timer = threading.Timer(ttl, _expire, args=(flow_id,))
    timer.daemon = True
    with _lock:
        old = _held.pop(flow_id, None)
        _held[flow_id] = {'claim': claim, 'service': service, 'account_id': account_id, 'rec': rec,
                          'app': dict(app or {}), 'expires': time.time() + ttl, 'timer': timer,
                          'on_drop': on_drop}
    if old:
        old['timer'].cancel()
    timer.start()


def alive(flow_id: str) -> int:
    """Seconds left on a held sign-in, 0 when there is none. Reveals nothing else. Never
    revokes: callers hold other locks, and dropping a held sign-in is a network call."""
    with _lock:
        e = _held.get(flow_id)
        return max(0, int(e['expires'] - time.time())) if e else 0


def _matches(e: dict | None, claim: Any, service: str) -> bool:
    return (e is not None and isinstance(claim, str) and hmac.compare_digest(e['claim'].encode(), claim.encode())
            and e['service'] == service)


def account_of(flow_id: str, claim: str, service: str, client_id: str | None = None) -> str | None:
    """The Desk account id the held sign-in was started for. Reads nothing else and drops
    nothing: an expired entry is refused here and left to its own timer (revoking is a network
    call, and the Save that asks holds the global commit lock). A wrong or missing claim, a
    different service or a gone entry is the same refusal. With a `client_id` (the app the Save
    stores) a sign-in minted for another app is refused too: the Save checks before it writes."""
    with _lock:
        e = _held.get(flow_id) if isinstance(flow_id, str) else None
        if not _matches(e, claim, service) or e is None or e['expires'] <= time.time():
            raise HoldError('hold_missing', GONE)
        if client_id is not None and not (isinstance(e['rec'].get('client_id'), str) and hmac.compare_digest(
                e['rec']['client_id'].encode(), client_id.encode())):
            raise HoldError('app_mismatch', APP_MISMATCH)
        return e['account_id']


def take(flow_id: str, claim: str, service: str) -> dict[str, Any]:
    """The Save claims the held sign-in: ONE step takes it out of memory (no peek-then-read, so
    an expiry cannot revoke it halfway through the write) and the caller owns it. Pair every
    take with `give_back` (the write failed and the person can press Save again) or let the
    entry go (the write landed). Never revokes: an entry already past its time is taken out and
    dropped on another thread, because the caller holds the global commit lock."""
    with _lock:
        e = _held.get(flow_id) if isinstance(flow_id, str) else None
        if not _matches(e, claim, service) or e is None:
            raise HoldError('hold_missing', GONE)
        _held.pop(flow_id, None)
        expired = e['expires'] <= time.time()
    e['timer'].cancel()
    if expired:
        _drop_off_thread(e)
        raise HoldError('hold_missing', GONE)
    return e


def give_back(flow_id: str, e: dict[str, Any]) -> None:
    """A Save that could not write: put the taken sign-in back for the time it has left."""
    left = e['expires'] - time.time()
    if left <= 0:
        _drop_off_thread(e)
        return
    timer = threading.Timer(left, _expire, args=(flow_id,))
    timer.daemon = True
    e['timer'] = timer
    with _lock:
        _held[flow_id] = e
    timer.start()


def consume(flow_id: str) -> None:
    """A Save has written the sign-in: forget it (no revoke: it is now the saved one)."""
    with _lock:
        e = _held.pop(flow_id, None)
    if e:
        e['timer'].cancel()
        e['rec'] = None
        e['app'] = {}
        e['on_drop'] = None


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
    _drop_entry(e)
    return True


def _drop_entry(e: dict[str, Any]) -> None:
    e['timer'].cancel()
    rec, app, service, on_drop = e['rec'], e['app'], e['service'], e.get('on_drop')
    e['rec'] = None
    e['app'] = {}
    e['on_drop'] = None
    try:
        from mc import desk_oauth as _oauth      # lazy: desk_oauth imports this module
        revoked = _oauth.revoke_record(service, rec, app=app)
    except Exception as ex:                      # best effort: a vendor refusing must not raise out of a timer
        revoked = None
        _log(f'[desk_oauth] revoking a held {service} sign-in raised {type(ex).__name__}', flush=True)
    _log(f'[desk_oauth] held {service} sign-in dropped (revoked={revoked})', flush=True)
    if on_drop:
        try:
            on_drop()
        except Exception as ex:
            _log(f'[desk_oauth] cleaning up after a dropped {service} sign-in raised {type(ex).__name__}', flush=True)


def _drop_off_thread(e: dict[str, Any]) -> None:
    threading.Thread(target=_drop_entry, args=(e,), name='desk-oauth-hold-drop', daemon=True).start()


def _expire(flow_id: str) -> None:
    discard(flow_id)


def _forget_all_for_tests() -> None:
    with _lock:
        entries = list(_held.values())
        _held.clear()
    for e in entries:
        e['timer'].cancel()
