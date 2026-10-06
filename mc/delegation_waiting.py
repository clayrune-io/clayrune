"""Which child reports are still waiting to reach their parent.

A finished child's report goes outbox -> inbox -> parent (`mc/delegation_delivery.py`).
While the parent is mid-turn the inbox row sits `pending` and is retried with
backoff, and a row that cannot be handed over is parked (`blocked`, `uncertain`,
`recovery_required`) until a person resolves it. In every one of those states
the parent's own chat showed nothing: 2026-10-06 a Sol_Tobin report waited
4m56s behind a busy Dave session and Ron had to say so, because the only view
of the queue was a status-list endpoint nobody has open.

This is a read-only projection of that queue per parent session, for
/agent/status (the chat marker) and /api/floor (the figure line). It never
writes, never touches a payload beyond the sender's display name, and never
raises: a status poll must not fail because the delivery database is busy.

A row stops being reported when it is no longer in one of the listed states --
`submitted` (handed to the parent) or `delivered`/`processed`. Parked rows stay
reported on purpose: the alternative is a marker that vanishes at the moment the
report is actually stuck.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from mc.core import _log

# inbox state -> what the reader needs to know. `dispatch_intent` is a claim in
# flight, still "waiting" from the parent's side. Outbox rows (child finished,
# receipt not yet recorded) are the same report one stage earlier.
STAGE_BY_STATE = {
    'pending': 'waiting',
    'dispatch_intent': 'waiting',
    'blocked': 'held',
    'uncertain': 'review',
    'recovery_required': 'review',
}
_WHO_CHARS = 48

_db_path: Optional[Path] = None
_last_error = ''


def configure(path) -> None:
    """Point at the delivery database (agent_routes.wire; tests)."""
    global _db_path, _last_error
    _db_path = Path(path) if path else None
    _last_error = ''


def _who(payload_json: str) -> tuple[str, str]:
    """(display name, child session id) from a stored envelope; never raises."""
    try:
        inner = json.loads(payload_json).get('payload', {})
        who = ' '.join(str(inner.get('who') or '').split())[:_WHO_CHARS]
        return who or 'an agent', str(inner.get('child_session_id') or '')
    except (TypeError, ValueError, AttributeError):
        return 'an agent', ''


_LABEL = {
    'waiting': 'report waiting from {who}',
    'held': 'report from {who} is held, needs recovery',
    'review': 'report from {who} may not have arrived, needs review',
}
_DETAIL = {
    'waiting': 'Queued. It is delivered into this chat when the current turn ends.',
    'held': 'This chat cannot take it right now (stopped, errored or quota-blocked). '
            'It is not retried automatically.',
    'review': 'Delivery outcome is unknown. It is not retried automatically; '
              'see the delegation status list.',
}


def waiting_by_parent(project_id: str = '') -> dict[str, list[dict[str, Any]]]:
    """Unresolved reports keyed by parent session id, oldest first.

    `project_id` narrows to one project; '' reads every project (the Floor).
    Returns {} when the database is unconfigured, absent or unreadable.
    """
    global _last_error
    path = _db_path
    if path is None or not path.exists():
        return {}
    states = tuple(STAGE_BY_STATE)
    marks = ','.join('?' for _ in states)
    scope = ' AND project_id=?' if project_id else ''
    args = [*states, *([project_id] if project_id else [])]
    sql = (f"SELECT event_id,parent_session_id,state,payload,created_at,1 AS stage_rank "
           f"FROM inbox WHERE state IN ({marks}){scope} UNION ALL "
           f"SELECT event_id,parent_session_id,state,payload,created_at,0 AS stage_rank "
           f"FROM outbox WHERE state IN ({marks}){scope} ORDER BY created_at")
    try:
        # A plain connection with query_only: a mode=ro URI cannot open a WAL
        # database whose -shm is gone (every connection closed), which is the
        # normal state between drains.
        db = sqlite3.connect(str(path), timeout=2)
        try:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute(sql, args + args).fetchall()
        finally:
            db.close()
        _last_error = ''
    except Exception as e:
        msg = f'{type(e).__name__}: {e}'
        if msg != _last_error:
            _last_error = msg
            _log(f'[delegation-waiting] queue read failed: {msg}', flush=True)
        return {}
    # An event present in both tables is one report; the inbox row is further along.
    best: dict[str, tuple] = {}
    for row in rows:
        if row[0] not in best or row[5] >= best[row[0]][5]:
            best[row[0]] = row
    out: dict[str, list[dict[str, Any]]] = {}
    for event_id, parent, state, payload, created_at, _rank in sorted(
            best.values(), key=lambda r: r[4]):
        who, child = _who(payload)
        stage = STAGE_BY_STATE[state]
        out.setdefault(parent, []).append({
            'event_id': event_id,
            'child_session_id': child or (event_id.rsplit(':turn:', 1)[0]
                                          if ':turn:' in event_id else ''),
            'who': who,
            'stage': stage,
            'label': _LABEL[stage].format(who=who),
            'detail': _DETAIL[stage],
            'since': created_at,
        })
    return out


def figure_label(reports: Optional[list[dict[str, Any]]]) -> str:
    """One line for a Floor figure; '' when nothing is waiting on this session."""
    if not reports:
        return ''
    waiting = [r for r in reports if r.get('stage') == 'waiting']
    if len(waiting) == 1:
        return waiting[0]['label']
    if waiting:
        names = ', '.join(r['who'] for r in waiting[:2])
        more = ', ...' if len(waiting) > 2 else ''
        return f'{len(waiting)} reports waiting ({names}{more})'
    n = len(reports)
    return f'{n} reports need review' if n != 1 else '1 report needs review'
