"""Retry policy for failed inbox handoffs in durable delegation delivery.

`drain_once` used to file every inbox failure that was not Blocked/Deferred
under `uncertain`, which `DeliveryStore.retry` refuses to replay without a
human's reviewed resolution. That is right when the parent may already have
been handed the message (a replay would duplicate it) and wrong when it
provably was not: `_process_inbox` raising on a lost claim BEFORE it reaches
the parent left the callback stuck forever, though nothing had been sent.
Bram's diagnosis, session 12c4c9228d07, 2026-10-05.

This module owns the decision; `drain_once` only asks it.
"""
from __future__ import annotations

from mc.core import _log
from mc.delegation_delivery import (DeliveryBlocked, DeliveryDeferred,
                                    DeliveryNotHandedOff)

# `DeliveryStore.finish_inbox(state=...)`. '' means "not forced": the store
# backs off, retries, and parks the row in recovery_required after
# MAX_ATTEMPTS, so a handoff that keeps failing cannot loop forever.
RETRY_BOUNDED = ''


def classify_inbox_failure(row, exc) -> str:
    """Pick the state a failed inbox handoff is filed under, and log why.

    - DeliveryBlocked: stays 'blocked' (parent stopped/error/changed).
    - DeliveryDeferred: stays 'pending' (parent busy; retried indefinitely).
    - DeliveryNotHandedOff: the parent was never handed the message, so it is
      retried automatically with bounded backoff.
    - anything else, including a plain DeliveryUncertain: the handoff may
      have happened, so 'uncertain' -- never replayed without review.
    """
    event_id = row.get('event_id', '') if isinstance(row, dict) else ''
    if isinstance(exc, DeliveryBlocked):
        state = 'blocked'
        verdict = 'blocked, not retried'
    elif isinstance(exc, DeliveryDeferred):
        state = 'pending'
        verdict = 'deferred, will retry'
    elif isinstance(exc, DeliveryNotHandedOff):
        state = RETRY_BOUNDED
        verdict = 'parent was never handed the message, retrying with backoff'
    else:
        state = 'uncertain'
        verdict = 'handoff may have happened, not retried without review'
    _log(f"[delegation-delivery] inbox {event_id} -> parent "
         f"{row.get('parent_session_id', '') if isinstance(row, dict) else ''}: "
         f"{verdict} ({type(exc).__name__}: {exc})", flush=True)
    return state
