"""The agent-log row's `delegation_completion` must be the completion that was
committed, not a rebuild of it (MC-1196).

A Mode B child enqueues its wake at the turn boundary, while the session status
is still `idle`. The process-exit path then rewrites the agent-log row with the
status `completed` and rebuilds `delegation_completion` from the live session.
Same event id, different `status` and `message`, so at the next boot
`DeliveryStore.recover_outbox` found the stored event and the log row in
conflict. This returns the committed payload when one exists, so the row
records what was actually sent.
"""
from __future__ import annotations

from typing import Any, Optional

from mc.core import _log
from mc.delegation_delivery import callback_payload, event_id_for_turn


def completion_for_row(store: Any, project_id: str, session: dict[str, Any],
                       summary: str) -> Optional[dict[str, Any]]:
    """`delegation_completion` for the agent-log row of `session`, or None for a
    session nobody spawned. Falls back to a fresh payload when nothing is
    committed (no store, event absent, or committed for another parent)."""
    parent = (session.get('_notify_session') or '').strip()
    sid = session.get('session_id', '')
    if not parent or parent == sid:
        return None
    turn = int(session.get('_delegation_turn', 1))
    event_id = event_id_for_turn(sid, turn)
    if store is not None:
        try:
            committed = store.committed_payload(event_id, project_id, parent)
            if committed is not None:
                return committed
        except Exception as exc:
            _log(f'[delegation-delivery] committed completion unreadable for '
                 f'{event_id}: {exc}')
    return callback_payload(session, summary, event_id)
