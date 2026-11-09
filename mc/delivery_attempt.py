"""Durable delivery ownership, independent of live sessions and callback turns."""
from dataclasses import dataclass
from typing import Any, TypeVar
from uuid import uuid4


_Error = TypeVar('_Error', bound=Exception)
_SESSION_KEY = '_delivery_attempt_id'
_ROW_KEY = 'delivery_attempt_id'


def pending_fields(session: dict[str, Any]) -> dict[str, str]:
    """Take a row, including ordinary continuations that keep turn 1.

    Called inside the manager -> log-leaf transaction. Identity-only backfills
    must not call this: they update metadata for the same delivery.
    """
    attempt_id = uuid4().hex
    session[_SESSION_KEY] = attempt_id
    return {_ROW_KEY: attempt_id}


def completion_fields(session: dict[str, Any]) -> dict[str, str]:
    """Keep the token when completion replaces the pending row wholesale."""
    attempt_id = session.get(_SESSION_KEY)
    return {_ROW_KEY: attempt_id} if attempt_id else {}


@dataclass(frozen=True)
class DeliveryAttempt:
    attempt_id: str

    @classmethod
    def capture(cls, session: dict[str, Any] | None) -> 'DeliveryAttempt':
        """Mint and persist before spawning/writing, never after an ack wait.

        No row is inserted here: initial dispatch already writes its pending
        row; revival takes its existing row without erasing conversation data.
        Storage failure propagates before launch, so no unowned writer starts.
        """
        attempt = cls(uuid4().hex)
        if session is None:
            return attempt
        project_id, session_id = session.get('project_id'), session.get('session_id')
        if not project_id or not session_id or session.get('incognito') or session.get('housekeeping'):
            attempt.stamp(session)
            return attempt
        from mc.blueprints.agent_routes import _update_agent_log, get_manager

        def claim(rows):
            row = next((r for r in rows if r.get('session_id') == session_id), None)
            if row is not None:
                row[_ROW_KEY] = attempt.attempt_id

        with get_manager(project_id).lock:
            _update_agent_log(project_id, claim)
            attempt.stamp(session)
        return attempt

    def stamp(self, session: dict[str, Any]) -> None:
        """Carry an already claimed token onto a newly constructed session."""
        session[_SESSION_KEY] = self.attempt_id

    def owns_row(self, row: dict[str, Any]) -> bool:
        # Legacy rows have no token. Registry absence and delegation_turn are
        # irrelevant; closing a tab must not erase ownership.
        return not row.get(_ROW_KEY) or row[_ROW_KEY] == self.attempt_id

    def bind(self, error: _Error) -> _Error:
        setattr(error, 'delivery_attempt', self)
        return error
