"""Re-arm a report parked `blocked` on a parent that has left memory.

Gap found 2026-11-13 (Dave session 09e12ac8, killed by the guardian four
times): `_process_inbox` parks a report `blocked` ("parent status error ...")
when the parent is in memory with status `error`. `drain_once` only claims
`pending` rows, so the only thing that can move a `blocked` row is
`delegation_wake.ParentWaker`, and that needs the parent to be a live dict in
`agent_sessions` whose status changes to idle/completed while the loop
watches. A parent that is later dropped from memory (stale-session cleanup,
server restart) is skipped by the waker -- `not isinstance(s, dict)` -- so
the row stayed `blocked` for hours (3 rows, 235-253 min old at diagnosis)
although `_process_inbox` already has the right answer for an absent parent:
`_revive_parent_for_delegation`.

This module moves such a row back to `pending` once, so the normal drain
reaches that revive branch. It acts only on a `blocked` row whose cause is the
parent's status (`delegation_wake.is_parent_status_block`) and whose parent is
absent from the session table. `uncertain` / `recovery_required` are never
touched (the parent may already have the message). One automatic retry per row
per loop lifetime, like `ParentWaker`: if the revive is refused the row is
parked again and stays for a person.

Never raises: the delivery loop must survive this.
"""
from __future__ import annotations

from typing import Any, Mapping

from mc.core import _log
from mc.delegation_wake import ParentWaker, is_parent_status_block

_RETRIED_CAP = 1000


class AbsentParentWaker:
    """Per-loop memory of rows already re-armed. Not thread-shared."""

    def __init__(self) -> None:
        self._retried: set[str] = set()
        self._last_error = ''

    def tick(self, store, sessions: Mapping[str, Any]) -> int:
        """Re-arm parent-status-blocked rows whose parent is gone; returns rows changed."""
        try:
            return self._tick(store, sessions)
        except Exception as e:
            msg = f'{type(e).__name__}: {e}'
            if msg != self._last_error:
                self._last_error = msg
                _log(f'[delegation-wake] absent-parent tick failed: {msg}', flush=True)
            return 0

    def _tick(self, store, sessions: Mapping[str, Any]) -> int:
        with store._db() as db:
            rows = db.execute(
                "SELECT event_id,parent_session_id,last_error FROM inbox "
                "WHERE state='blocked'").fetchall()
        live = {r['event_id'] for r in rows}
        self._retried &= live
        now = store.clock()
        changed = 0
        for r in rows:
            event_id = r['event_id']
            if event_id in self._retried or not is_parent_status_block(r['last_error']):
                continue
            if isinstance(sessions.get(r['parent_session_id']), dict):
                continue    # parent still in memory: ParentWaker owns it
            moved = ParentWaker._retry_blocked(store, event_id, now)
            if moved:
                self._retried.add(event_id)
                if len(self._retried) > _RETRIED_CAP:
                    self._retried.pop()
                _log(f'[delegation-wake] {event_id}: parent is no longer in memory, '
                     f're-armed a status-blocked report for revival', flush=True)
            changed += moved
        return changed
