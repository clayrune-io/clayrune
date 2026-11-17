"""What an agent-log row records about its turn's completion (MC-1196).

Two facts, both about keeping `DeliveryStore.recover_outbox` honest:

1. `delegation_completion` is the payload already committed for the event, not a
   rebuild of it. A Mode B child enqueues its wake at the turn boundary while the
   session status is still `idle`; the process-exit path then rewrites the row
   with `completed`. Rebuilt, the row disagreed with the stored event (same id,
   different `status` and `message`) and boot recovery raised a conflict.
2. A turn that finished with nothing committed for it AND the spawner latch
   already spent owed its spawner no wake (`_maybe_notify_spawner`: a human or an
   agent follow-up that did not name notify_session, a self-started wake). The row
   says so (`WAKE_NOT_OWED_KEY`) and recovery skips it. A turn whose wake was owed
   and lost (latch unset, nothing committed) carries no flag and is recovered.
"""
from __future__ import annotations

from typing import Any, Optional

from mc.core import _log
from mc.delegation_delivery import (WAKE_NOT_OWED_KEY, callback_payload,
                                    event_id_for_turn)


def delegation_row_fields(store: Any, project_id: str, session: dict[str, Any],
                          summary: str) -> dict[str, Any]:
    """The `delegation_completion` (and, when it applies, `WAKE_NOT_OWED_KEY`)
    entries for the agent-log row of `session`. A session nobody spawned gets
    `delegation_completion: None`. Never raises."""
    parent = (session.get('_notify_session') or '').strip()
    sid = session.get('session_id', '')
    if not parent or parent == sid:
        return {'delegation_completion': None}
    event_id = event_id_for_turn(sid, int(session.get('_delegation_turn', 1)))
    committed: Optional[dict[str, Any]] = None
    readable = store is not None
    if store is not None:
        try:
            committed = store.committed_payload(event_id, project_id, parent)
        except Exception as exc:
            readable = False
            _log(f'[delegation-delivery] committed completion unreadable for '
                 f'{event_id}: {exc}')
    if committed is not None:
        return {'delegation_completion': committed}
    fields: dict[str, Any] = {
        'delegation_completion': callback_payload(session, summary, event_id)}
    # Only when the store could be asked: an unreadable store must leave the row
    # recoverable, never mark a possibly-owed wake as not owed.
    if readable and session.get('_notify_session_sent'):
        fields[WAKE_NOT_OWED_KEY] = True
    return fields


def completion_for_row(store: Any, project_id: str, session: dict[str, Any],
                       summary: str) -> Optional[dict[str, Any]]:
    return delegation_row_fields(store, project_id, session, summary)['delegation_completion']
