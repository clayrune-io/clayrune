"""The chat marker for an agent-read request card (backlog b1e1b23c, MC-1059 piece A).

The server posts ONE line into the asking agent's transcript:

    [agent-read-request:<request id>]

`static/js/agent-read-card.js` turns that line into the card, live and on a cold render, and
asks `GET /api/browser/agent-read/requests/<id>` for what to show (agent, profile, site, state).
The line carries only the id, so the card's words always come from the server's record, never
from whatever text reached the transcript. A fake marker an agent writes into its own reply
resolves to the same server record or to "expired"; it cannot say anything the server did not.

Same mechanism as `[stop-hook-redo:...]` and `[denied: ...]`: a bracketed line appended to the
session's `log_lines`, which every viewer already replays.
"""
from __future__ import annotations

import time
from typing import Any

from mc.core import TimestampedLines, _log

MARKER_PREFIX = '[agent-read-request:'


def marker_line(rid: str) -> str:
    return f'{MARKER_PREFIX}{rid}]'


def post(session: Any, rid: str) -> bool:
    """Append the marker to `session`'s transcript. True when it was posted; never raises."""
    try:
        lines = session.setdefault('log_lines', TimestampedLines())
        lines.append(marker_line(rid))
        session['last_output_time'] = time.time()
        return True
    except Exception as e:
        _log(f"[browser-agent-read] posting the request card failed: {e}", flush=True)
        return False
