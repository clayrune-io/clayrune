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

A row with a recorded handoff boundary stays visible through `submitted` while
its receipt is checked. Legacy submitted notifications retain their existing
behavior. Check failures name the reason and offer a check-only Retry; only a
second proven absence is shown as non-delivery.

MC-1106: the delivery-loop reconciler can end a lost acknowledgment as
`confirmed_in_transcript`; a person's Mark handled action ends it as `handled`.
Both are terminal, without re-delivery. This projection remains read-only.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from mc.core import _log
from mc.delegation_settlement import CHECK_ERRORS

# inbox state -> what the reader needs to know. `dispatch_intent` is a claim in
# flight, still "waiting" from the parent's side. Outbox rows (child finished,
# receipt not yet recorded) are the same report one stage earlier.
STAGE_BY_STATE = {
    'pending': 'waiting',
    'dispatch_intent': 'delivering',
    'submitted': 'delivering',
    'blocked': 'delivering',
    'uncertain': 'delivering',
    'recovery_required': 'delivering',
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
    'delivering': 'Delivering report from {who}; confirming receipt',
    'failed': 'report from {who} was not delivered',
    'check_failed': 'Could not check report from {who}',
}
_DETAIL = {
    'waiting': 'Queued. It is delivered into this chat when the current turn ends.',
    'delivering': 'Checking the chat log to confirm receipt.',
    'failed': 'The completed chat log proves non-delivery after one automatic resend. Retry sends it again.',
    'check_failed': 'Retry checks the chat log again without resending the report.',
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
    sql = (f"SELECT event_id,parent_session_id,state,payload,created_at,1 AS stage_rank,check_status "
           f"FROM inbox WHERE state IN ({marks}){scope} "
           "AND (state!='submitted' OR handoff_boundary!='') UNION ALL "
           f"SELECT event_id,parent_session_id,state,payload,created_at,0 AS stage_rank,'' AS check_status "
           f"FROM outbox WHERE state IN ({marks}){scope} "
           "AND NOT EXISTS (SELECT 1 FROM inbox i WHERE i.event_id=outbox.event_id "
           "AND i.state IN ('confirmed_in_transcript','handled','submitted','processed')) ORDER BY created_at")
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
    for event_id, parent, state, payload, created_at, _rank, check_status in sorted(
            best.values(), key=lambda r: r[4]):
        who, child = _who(payload)
        stage = STAGE_BY_STATE[state]
        if state in ('pending', 'dispatch_intent'):
            check_status = ''
        reason = CHECK_ERRORS.get(check_status, '')
        if check_status == 'not_delivered':
            stage = 'failed'
        elif reason:
            stage = 'check_failed'
        elif not _rank and state in ('blocked', 'uncertain', 'recovery_required'):
            stage, reason = 'check_failed', 'delivery transport could not complete'
        out.setdefault(parent, []).append({
            'event_id': event_id,
            'table': 'inbox' if _rank else 'outbox',
            'child_session_id': child or (event_id.rsplit(':turn:', 1)[0]
                                          if ':turn:' in event_id else ''),
            'who': who,
            'stage': stage,
            'label': _LABEL[stage].format(who=who) + (f': {reason}' if reason else ''),
            'detail': ('Retry continues delivery of this queued report.'
                       if stage == 'check_failed' and not _rank else _DETAIL[stage]),
            'retry_action': 'check' if stage == 'check_failed' and _rank else 'retry',
            'since': created_at,
        })
    return out


def figure_label(reports: Optional[list[dict[str, Any]]]) -> str:
    """One line for a Floor figure; '' when nothing is waiting on this session."""
    if not reports:
        return ''
    if len(reports) == 1:
        return reports[0]['label']
    words = [('failed', 'not delivered'), ('check_failed', 'could not check'),
             ('delivering', 'delivering'), ('waiting', 'queued')]
    return ', '.join(f'{n} {word}' for stage, word in words
                     if (n := sum(r.get('stage') == stage for r in reports)))
