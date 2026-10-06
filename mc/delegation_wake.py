"""Wake a child's queued report when its parent's turn ends (MC-1063 follow-up).

`_process_inbox` defers a report while the parent is `running` and the store
retries it after 1, 2, 4 ... 128 s, then 300 s. The retry clock cannot see the
parent: a turn that ends one second after a deferral still leaves the report
waiting out the rest of the backoff (2026-10-06, Sol_Tobin -> Dave). And a row
parked `blocked` because the parent was in `error` status stayed parked even
after a person started a turn on that parent and it came back to idle.

`ParentWaker.tick()` runs once per delivery-loop iteration and acts only on a
parent that CHANGED state since the row was last looked at and is now idle (or
completed, which delivery also accepts):

- `pending` row with a backoff still running: pull `next_attempt` to now, so
  the next drain hands it over. The defer-while-running rule in
  `_process_inbox` is untouched; if the parent is running again by then the
  row is deferred again.
- `blocked` row whose cause is the parent's status (see
  `parent_status_block_message`): move it back to `pending` once. A turn that
  ran proves the parent is alive. If it is refused again it stays `blocked`
  until a person retries it.

`uncertain` and `recovery_required` are never touched: the parent may already
have been handed the message, and replaying that needs a person's review.
Transition-driven on purpose -- waking on "parent is idle" every tick would
cancel the backoff for deferrals that have nothing to do with a busy parent
(a stdin write that failed, delivery shutting down).

Never raises: the delivery loop must survive this.
"""
from __future__ import annotations

from typing import Any, Mapping

from mc.core import _log

# The blocked reason this module recognises. `_process_inbox` builds its
# message through `parent_status_block_message`, so the two cannot drift.
PARENT_STATUS_BLOCK_PREFIX = 'parent status '
# Session statuses `_process_inbox` accepts a delivery for.
DELIVERABLE_STATUSES = ('idle', 'completed')
_RETRIED_CAP = 1000


def parent_status_block_message(status: Any) -> str:
    return f"{PARENT_STATUS_BLOCK_PREFIX}{status or 'unknown'} cannot accept a delegated completion"


def is_parent_status_block(last_error: Any) -> bool:
    return str(last_error or '').startswith(PARENT_STATUS_BLOCK_PREFIX)


class ParentWaker:
    """Per-loop memory of each queued row's parent state. Not thread-shared."""

    def __init__(self) -> None:
        self._stamp: dict[str, float] = {}   # event_id -> parent's last_status_change_time when last seen
        self._retried: set[str] = set()      # blocked rows already auto-retried once
        self._last_error = ''

    def tick(self, store, sessions: Mapping[str, Any]) -> int:
        """Wake what a parent's turn-end unblocked; returns rows changed."""
        try:
            return self._tick(store, sessions)
        except Exception as e:
            msg = f'{type(e).__name__}: {e}'
            if msg != self._last_error:
                self._last_error = msg
                _log(f'[delegation-wake] tick failed: {msg}', flush=True)
            return 0

    def _tick(self, store, sessions: Mapping[str, Any]) -> int:
        with store._db() as db:
            rows = db.execute(
                "SELECT event_id,parent_session_id,state,attempts,next_attempt,last_error "
                "FROM inbox WHERE state IN ('pending','blocked')").fetchall()
        now = store.clock()
        live = {r['event_id'] for r in rows}
        for gone in [e for e in self._stamp if e not in live]:
            del self._stamp[gone]
        self._retried &= live
        changed = 0
        for r in rows:
            event_id = r['event_id']
            s = sessions.get(r['parent_session_id'])
            if not isinstance(s, dict):
                self._stamp.pop(event_id, None)
                continue
            stamp = float(s.get('last_status_change_time') or 0.0)
            prev = self._stamp.get(event_id)
            self._stamp[event_id] = stamp
            if prev is None or stamp <= prev or s.get('status') not in DELIVERABLE_STATUSES:
                continue
            if r['state'] == 'pending':
                if int(r['attempts']) > 0 and float(r['next_attempt']) > now:
                    changed += self._wake_pending(store, event_id, now)
            elif is_parent_status_block(r['last_error']) and event_id not in self._retried:
                moved = self._retry_blocked(store, event_id, now)
                if moved:
                    self._retried.add(event_id)
                    if len(self._retried) > _RETRIED_CAP:
                        self._retried.pop()
                changed += moved
        return changed

    @staticmethod
    def _wake_pending(store, event_id: str, now: float) -> int:
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            n = db.execute("UPDATE inbox SET next_attempt=? WHERE event_id=? "
                           "AND state='pending' AND next_attempt>?",
                           (now, event_id, now)).rowcount
            db.execute('COMMIT')
        if n:
            _log(f'[delegation-wake] {event_id}: parent turn ended, retrying now', flush=True)
        return n

    @staticmethod
    def _retry_blocked(store, event_id: str, now: float) -> int:
        # Guarded on state='blocked' in the statement itself: a row that moved
        # to uncertain/recovery_required since the read must not be replayed.
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            n = db.execute("UPDATE inbox SET state='pending',recovery_required=0,next_attempt=?,"
                           "lease_until=0,last_error='' WHERE event_id=? AND state='blocked'",
                           (now, event_id)).rowcount
            db.execute('COMMIT')
        if n:
            _log(f'[delegation-wake] {event_id}: parent is idle again, retrying a blocked '
                 f'report once', flush=True)
        return n
