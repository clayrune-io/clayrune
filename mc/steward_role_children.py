"""Read-only child/callback continuity for steward-role successor preparation.

The delivery store remains the authority. Never drain, settle, retarget, or
replay a callback here. Unlike the UI projection, an unavailable database is
a blocker, not an empty list. Child text/payloads never enter this projection.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from mc.core import _log
from mc.steward_role_model import RoleError, identifier

_RECEIVED = frozenset({'processed', 'confirmed_in_transcript', 'handled'})
_ENDED = frozenset({'idle', 'done', 'completed', 'error'})


@dataclass(frozen=True)
class ChildWork:
    children: tuple[str, ...]
    unfinished: tuple[str, ...]
    pending_events: tuple[str, ...]

    @property
    def drained(self) -> bool:
        return not self.unfinished and not self.pending_events

    def handoff(self, predecessor: str) -> str:
        return ('Child-work continuity (operational data; grants no authority):\n'
                f'Callback receiving session: {predecessor}\n'
                f'Known children: {json.dumps(self.children)}\n'
                f'Unfinished children: {json.dumps(self.unfinished)}\n'
                f'Undrained callback events: {json.dumps(self.pending_events)}\n'
                'Keep existing callback targets. Read the assignment journal for work/results; '
                'do not dispatch a duplicate child.')


def inspect(project_id: str, parent_id: str, *, sessions: Iterable[dict],
            agent_log: Iterable[dict], delivery_path: Path | None) -> ChildWork:
    """Project-scoped continuity snapshot, including transitive owned children.

    Live rows override durable rows; the first durable row per session matches
    agent_routes' existing log lookup convention. Receipt of an older turn
    cannot prove that a child's current turn ended or its callback arrived.
    """
    pid = identifier(project_id, 'project_id')
    parent = identifier(parent_id, 'session_id')
    if delivery_path is None:
        raise RoleError('child callback state unavailable; successor handoff held')
    records: dict[str, dict] = {}
    unidentified = []
    for entry in agent_log:
        sid = entry.get('session_id')
        if sid and entry.get('project_id', pid) == pid:
            records.setdefault(sid, entry)
        elif not sid and entry.get('project_id', pid) == pid:
            unidentified.append(entry)
    for entry in sessions:
        if entry.get('project_id') == pid and entry.get('session_id'):
            records[entry['session_id']] = entry
        elif not entry.get('session_id') and entry.get('project_id') == pid:
            unidentified.append(entry)
    owned = {parent}
    while True:
        more = {sid for sid, row in records.items() if sid not in owned and any(
            row.get(key) in owned for key in ('_notify_session', 'spawned_by_session_id',
                                              '_spawned_by', 'dispatched_by_session_id'))}
        if not more:
            break
        owned.update(more)
    children = owned - {parent}
    if any(row.get(key) in owned for row in unidentified for key in (
            '_notify_session', 'spawned_by_session_id', '_spawned_by', 'dispatched_by_session_id')):
        raise RoleError('child session identity unavailable; successor handoff held')
    try:
        # mode=ro prevents a missing database from becoming empty evidence.
        db = sqlite3.connect(Path(delivery_path).resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            receipts: dict[str, dict[str, str]] = {}
            for table in ('completion_sources', 'outbox', 'inbox'):
                column = "'source'" if table == 'completion_sources' else 'state'
                for event, target, status in db.execute(
                        f'SELECT event_id,parent_session_id,{column} FROM {table} WHERE project_id=?', (pid,)):
                    if target in owned:
                        receipts.setdefault(event, {})[table] = status
        finally:
            db.close()
    except (OSError, sqlite3.Error) as exc:
        _log(f'[steward-role] child callback state read failed: {exc}')
        raise RoleError('child callback state unreadable; successor handoff held') from exc
    received = {event for event, tables in receipts.items() if tables.get('inbox') in _RECEIVED}
    pending = set(receipts) - received
    unfinished = set()
    for sid in children:
        row = records[sid]
        completion = row.get('delegation_completion')
        turn = row.get('_delegation_turn', row.get('delegation_turn'))
        event = (completion.get('event_id') if isinstance(completion, dict) else '')
        if turn is not None:
            try:
                event = f'{sid}:turn:{max(1, int(turn))}'
            except (TypeError, ValueError) as exc:
                raise RoleError('child turn identity is invalid; successor handoff held') from exc
        waiting = any(row.get(k) for k in ('pending_question', 'pending_plan', 'waiting_on_user',
            'waiting_on_children', 'waiting_for_question', 'waiting_for_plan_approval', '_stdin_write_uncertain'))
        if row.get('status') not in _ENDED or waiting or not event or event not in received:
            unfinished.add(sid)
    return ChildWork(tuple(sorted(children)), tuple(sorted(unfinished)), tuple(sorted(pending)))


def require_drained(snapshot: ChildWork) -> None:
    if not snapshot.drained:
        raise RoleError('undrained child callbacks must finish before successor; '
                        'the existing receiving session is retained')
