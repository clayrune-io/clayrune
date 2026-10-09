"""The "Allow once" / "Allow 10 min" pass record on a live session.

A human click in the chat header (passcode-gated, `attend_once_session` in
mc/blueprints/agent_routes.py) records a pass on the session dict; the
steward/fence.py PreToolUse hook asks the consume route whether one is open
for the blocked call. This module owns what a pass IS: its record shape,
expiry, mode and the transitions between modes. The routes stay thin and keep
what needs the web layer (the passcode gate, eligibility, locking).

Two modes (Ron, 2026-10-09: one passcode per blocked action meant re-typing
it every time the action failed and had to be retried):

  once    the original pass. The first consume spends it. Unchanged.
  window  every fence-blocked action in this session goes through until the
          pass expires (TTL_SECONDS). Consume does NOT spend it.

A window never extends and never stacks: granting while one is open reports
`already_open` with the ORIGINAL expiry. The only transition that rewrites an
open pass is once -> window (a fresh passcode, fresh TTL). Revoking closes
either mode and needs no passcode: closing the gate is the safe direction.

In memory only, like the pass always was: a server restart drops it, which
returns the session to fully fenced.

None of these functions lock. The caller holds the session's project manager
lock for anything that mutates (grant, evict, spend, revoke); `view` is a pure
read and is safe on the unlocked /agent/status path.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

PASS_KEY = '_attend_once_pass'
TTL_SECONDS = 600  # 10 minutes, both modes

MODE_ONCE = 'once'
MODE_WINDOW = 'window'
MODES = (MODE_ONCE, MODE_WINDOW)


def parse_mode(data: Any) -> Optional[str]:
    """The `mode` a grant body asks for. An absent key means `once` (every
    existing caller). A present value that is not a known mode is None, which
    the route turns into a 400 -- never silently coerced to `once`."""
    if not isinstance(data, dict) or 'mode' not in data:
        return MODE_ONCE
    mode = data.get('mode')
    return mode if mode in MODES else None


def _mode_of(info: dict) -> str:
    # Records written before modes existed (and by tests) carry none.
    return MODE_WINDOW if info.get('mode') == MODE_WINDOW else MODE_ONCE


def view(session: dict, now: Optional[datetime] = None) -> Optional[dict]:
    """Pure read: {'open': True, 'expires_at': iso, 'mode': 'once'|'window'}
    while an unexpired pass is recorded on `session`; else None. Never mutates:
    eviction happens only under a project lock, in `evict`. Status reads must
    stay pure -- the lazy pop this used to do raced a concurrent grant and
    silently discarded it."""
    info = session.get(PASS_KEY)
    if not info:
        return None
    try:
        expires_at = datetime.fromisoformat(info['expires_at'])
    except Exception:
        return None
    if (now or datetime.now(timezone.utc)) >= expires_at:
        return None
    return {'open': True, 'expires_at': info['expires_at'], 'mode': _mode_of(info)}


def evict(session: dict, now: Optional[datetime] = None) -> None:
    """Pop `session`'s pass if it is corrupt or past expiry. Caller MUST hold
    the session's project manager lock."""
    info = session.get(PASS_KEY)
    if not info:
        return
    try:
        if (now or datetime.now(timezone.utc)) < datetime.fromisoformat(info['expires_at']):
            return
    except Exception:
        pass
    session.pop(PASS_KEY, None)


def bound_session_id(session: dict) -> Optional[str]:
    """The MC session id the open pass was granted to (Fenn finding 5)."""
    return (session.get(PASS_KEY) or {}).get('mc_session_id')


def grant(session: dict, session_id: str, mode: str,
          now: Optional[datetime] = None) -> tuple[str, dict]:
    """Record a pass of `mode` on `session`. Returns (status, pass_info) where
    status is `granted`, `upgraded` or `already_open` and pass_info carries
    `expires_at` and `mode` of the pass now in force. Caller holds the lock
    and has already passed the passcode, eligibility and passcode-changed
    gates.

    An open pass is never extended or stacked. The one exception is an open
    `once` pass asked for a `window`: that is a different, wider grant, so it
    replaces the record with a fresh TTL (`upgraded`). A window asked for
    `once`, or anything asked for the mode already open, is `already_open`
    with the ORIGINAL expiry."""
    now = now or datetime.now(timezone.utc)
    evict(session, now)
    existing = view(session, now)
    if existing and not (mode == MODE_WINDOW and existing['mode'] == MODE_ONCE):
        return 'already_open', {'expires_at': existing['expires_at'], 'mode': existing['mode']}
    expires_iso = (now + timedelta(seconds=TTL_SECONDS)).isoformat()
    # mc_session_id pins WHICH session record the pass belongs to; consume
    # checks it, not just that some record shares the Claude conversation id.
    session[PASS_KEY] = {'expires_at': expires_iso, 'mc_session_id': session_id, 'mode': mode}
    return ('upgraded' if existing else 'granted'), {'expires_at': expires_iso, 'mode': mode}


def spend(session: dict) -> str:
    """Spend the open pass for one blocked call and return its mode. A `once`
    pass is popped; a `window` pass stays until it expires or is revoked.
    Caller holds the lock and has checked `view` and the session binding."""
    mode = _mode_of(session.get(PASS_KEY) or {})
    if mode != MODE_WINDOW:
        session.pop(PASS_KEY, None)
    return mode


def revoke(session: dict, now: Optional[datetime] = None) -> Optional[str]:
    """Drop any pass on `session`. Returns the mode that was open, or None if
    none was. Caller holds the lock."""
    existing = view(session, now)
    session.pop(PASS_KEY, None)
    return existing['mode'] if existing else None
