"""Tell the owner when an inbound reply is held, and again if it is dropped (MC-1213).

A `blocked` event is never re-selected: ``claim_for_submission`` takes only
ready/retryable rows, so nothing added to the delivery path later (the MC-1177
spawner fallback, the MC-1186 retries) can reach a hold that already exists.
Until this module the only signs of one were a line inside the bound chat and a
list in Settings. A dispatched chat is rarely reopened, and at retention the row
expired with a log line only. Incident: a reply held 2026-11-15, found 11-24.

LEVEL-TRIGGERED, from the store's current rows, so it also covers holds older
than this code and every path into `blocked` (ingest, extraction, wake, retry
exhaustion). It reads metadata only and NEVER moves an event: releasing a hold
stays passcode-gated (``agent_mail_holds_routes``), and no mail text is read,
shown or handed to an agent. The surface is the existing Inbox + push; no mail
is sent.

Delivery is at-least-once: the ledger is written only after the Inbox row is,
so a failed write is retried on the next pass.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from mc.agent_mail_store import (BLOCKED, EXPIRED, REVOKED, SUBMITTED, TURN_COMPLETED, REJECTED,
                                 MailStore)
from mc.atomic_json import write_json_atomic
from mc.core import _log

# Beside the mail store, outside DATA_DIR. Not a table: the pull schema is
# checked exactly, and an extra table would hold the store.
LEDGER_NAME = 'hold_alerts.json'
HELD_TITLE = 'Email reply not delivered'
DROPPED_TITLE = 'Email reply dropped, never delivered'
REVIEW = 'Release or reject it in Settings > Connectivity > Agent mail.'
_FIELDS = 'event_id,project_id,session_id,state,state_at,reason,received_at,expires_at'
_RETENTION = 'retention_expired'
_CODE = re.compile(r'^[a-z0-9_]{1,48}$')
_WHY = {'worktree_unavailable': "that chat's working copy is gone",
        'retry_exhausted': 'delivery kept failing',
        'rollover_required': 'open that chat once, then release it'}

Announce = Callable[[str, str, str], bool]


def _inbox(title: str, body: str, project_id: str) -> bool:
    """True once the Inbox row is on disk. No session_id: the Inbox keeps one row
    per chat, and that chat's next turn would otherwise replace this one."""
    from mc.blueprints import push_mobile
    return push_mobile._queue_push(title, body, url=f'/?project={project_id}',
                                   project_id=project_id, session_id='', kind='agent') is True


def _when(stamp: Any) -> str:
    return time.strftime('%b %d %H:%M', time.localtime(float(stamp or 0)))


def held_text(row: Any) -> str:
    reason = row['reason'] or ''
    why = _WHY.get(reason) or (reason.replace('_', ' ') if _CODE.match(reason) else 'unknown reason')
    return (f"Your reply of {_when(row['received_at'])} to chat {str(row['session_id'])[:12]} "
            f"is held: {why}. {REVIEW} Otherwise it is deleted undelivered "
            f"{_when(row['expires_at'])}.")


def dropped_text(row: Any) -> str:
    how = 'its time limit ran out' if row['state'] == EXPIRED else 'its chat or mail link was revoked'
    return (f"Your reply of {_when(row['received_at'])} to chat {str(row['session_id'])[:12]} "
            f"was never confirmed delivered and has been deleted: {how}. "
            "Send it again to a current chat if it still matters.")


def _load(path: Path) -> dict[str, str]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
        _log('[agent-mail] hold alert ledger is not an object; starting over', flush=True)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        _log(f'[agent-mail] hold alert ledger unreadable: {type(exc).__name__}', flush=True)
    return {}


def sweep(store: MailStore, *, announce: Announce | None = None) -> int:
    """Announce every hold and undelivered drop not yet told. Returns how many."""
    announce = announce or _inbox
    path = Path(store.path).with_name(LEDGER_NAME)
    ledger = _load(path)
    with store._read() as db:
        rows = {r['event_id']: r for r in db.execute(
            f'SELECT {_FIELDS} FROM events WHERE state=? OR (state=? AND reason=?)',
            (BLOCKED, EXPIRED, _RETENTION)).fetchall()}
        for event_id in ledger.keys() - rows.keys():
            row = db.execute(f'SELECT {_FIELDS} FROM events WHERE event_id=?', (event_id,)).fetchone()
            if row is not None:
                rows[event_id] = row
    changed = False
    for event_id in ledger.keys() - rows.keys():     # the tombstone was purged
        del ledger[event_id]
        changed = True
    told = 0
    for event_id, row in rows.items():
        state, seen = row['state'], ledger.get(event_id)
        if state == BLOCKED:
            # Keyed on state_at: released and held again is a new hold.
            mark, title, text = f"held:{row['state_at']!r}", HELD_TITLE, held_text
        elif ((state == EXPIRED and row['reason'] == _RETENTION)
              or (state == REVOKED and seen is not None)):
            mark, title, text = 'dropped', DROPPED_TITLE, dropped_text
        else:
            if state in (SUBMITTED, TURN_COMPLETED, REJECTED, EXPIRED) and seen is not None:
                del ledger[event_id]   # delivered, or the owner's own rejection
                changed = True
            continue
        if seen == mark:
            continue
        if not announce(title, text(row), row['project_id']):
            _log(f'[agent-mail] hold alert for {event_id[:12]} not recorded; will retry', flush=True)
            continue
        ledger[event_id] = mark
        write_json_atomic(path, ledger)
        changed = False
        told += 1
        _log(f'[agent-mail] owner told: event {event_id[:12]} {state} ({row["reason"]})', flush=True)
    if changed:
        write_json_atomic(path, ledger)
    return told
