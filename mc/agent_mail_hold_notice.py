"""UI-only held-reply metadata. Never reads mail content or admits agent turns."""
from __future__ import annotations

from typing import Callable

from mc.agent_mail_store import BLOCKED, MailStore
from mc.core import _log

REVIEW = 'Release or reject it in Settings > Connectivity > Agent mail.'
HELD = 'Your email reply is held for review. ' + REVIEW


def by_session(project_id: str, *, store_factory: Callable[[], MailStore | None] | None = None
               ) -> dict[str, dict[str, str | int]] | None:
    """One metadata-only read per status poll, including sessions no longer live.

    None means unavailable: a client keeps its last notice instead of falsely
    clearing it. No store is created on an install that has never enabled mail.
    """
    try:
        if store_factory is None:
            from mc import agent_mail_factory
            store = agent_mail_factory.get().open_store(create=False)
        else:
            store = store_factory()
        if store is None:
            return {}
        with store._read() as db:
            rows = db.execute(
                'SELECT session_id, COUNT(*) AS n '
                'FROM events WHERE project_id=? AND state=? GROUP BY session_id',
                (project_id, BLOCKED)).fetchall()
        return {row['session_id']: {'count': row['n'],
                'text': HELD} for row in rows}
    except Exception as exc:
        # Status polling must stay usable even when the optional mail store fails.
        _log(f'[agent-mail] hold notice unavailable: {type(exc).__name__}', flush=True)
        return None
