"""Durable evidence for delegated-report settlement, separate from transport.

Positive receipt remains MC-1106's whole-line user-message match. Absence is
stronger: an attempt-bound provider completion AND a complete owned native
transcript with a persisted terminal turn after that boundary. No clock/idle
heuristic, missing file, parse failure, rollover, or question pause is absence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from mc.core import _log

PARKED = ('uncertain', 'blocked', 'recovery_required')
CHECK_ERRORS = {
    'missing': 'chat log missing',
    'unreadable': 'chat log unreadable',
    'partial': 'chat log incomplete',
    'changed': 'chat log changed during the check',
    'unowned': 'chat log ownership could not be verified',
    'unsupported': 'this provider has no supported chat log',
    'no_boundary': 'handoff boundary was not recorded',
    'identity_changed': 'chat identity changed during handoff',
    'writer_pending': 'the original handoff has not finished',
    'queued': 'the original handoff is still queued',
    'turn_not_persisted': 'completed turn is not yet in the chat log',
    'turn_incomplete': 'parent turn did not complete',
    'handoff_unsettled': 'original handoff completion was not recorded',
    'ambiguous_receipt': 'matching report has no newer receipt timestamp',
}


def _encode(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def _decode(value: str) -> dict:
    try:
        result = json.loads(value or '{}')
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _native_id(session: dict) -> str:
    return str((session.get('claude_session_id') if session.get('provider', 'claude') == 'claude'
                else session.get('provider_session_id')) or '')


def _cwd(session: dict) -> str:
    return str(session.get('_agent_cwd') or session.get('cwd') or session.get('project_path') or '')


@dataclass(frozen=True)
class Snapshot:
    path: str
    raw: bytes
    records: list[dict]
    inode: int
    device: int

    def identity(self) -> dict:
        return {'path': self.path, 'size': len(self.raw),
                'digest': hashlib.sha256(self.raw).hexdigest(),
                'inode': self.inode, 'device': self.device}


def read_snapshot(path: str) -> tuple[Snapshot | None, str]:
    """A complete, consistent read, not two unchanged reads used as a timer."""
    try:
        p = Path(path)
        with p.open('rb') as stream:
            import os
            before = os.fstat(stream.fileno())
            raw = stream.read()
            after = os.fstat(stream.fileno())
        current = p.stat()
    except FileNotFoundError:
        return None, 'missing'
    except OSError:
        return None, 'unreadable'
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if signature(before) != signature(after) or signature(after) != signature(current):
        return None, 'changed'
    if not raw or not raw.endswith(b'\n'):
        return None, 'partial'
    try:
        records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    except (ValueError, UnicodeError):
        return None, 'partial'
    if not records or any(not isinstance(r, dict) for r in records):
        return None, 'partial'
    return Snapshot(str(p.resolve()), raw, records, before.st_ino, before.st_dev), ''


def _locate(session: dict) -> tuple[str, str]:
    provider = session.get('provider', 'claude')
    if provider not in ('claude', 'codex'):
        return '', 'unsupported'
    native, cwd = _native_id(session), _cwd(session)
    if not native or not cwd:
        return '', 'unowned'
    from mc.agent_runtime import get_runtime
    try:
        path = get_runtime(provider).transcript_path(cwd, native)
    except OSError:
        return '', 'unreadable'
    return (str(path.resolve()), '') if path else ('', 'missing')


def _timestamp(record: dict) -> float:
    try:
        stamp = datetime.fromisoformat(record.get('timestamp', '').replace('Z', '+00:00'))
        return stamp.timestamp() if stamp.tzinfo else 0.0
    except (ValueError, TypeError, AttributeError):
        return 0.0


def _texts(record: dict, provider: str) -> list[str]:
    if provider == 'codex':
        payload = record.get('payload') or {}
        if record.get('type') == 'response_item' and isinstance(payload, dict) and payload.get('role') == 'user':
            content = payload.get('content')
            return [b['text'] for b in content
                    if isinstance(b, dict) and b.get('type') == 'input_text'
                    and isinstance(b.get('text'), str)] if isinstance(content, list) else []
        return []
    message = record.get('message') or {}
    if record.get('type') != 'user' or not isinstance(message, dict):
        return []
    content = message.get('content')
    if isinstance(content, str):
        return [content]
    return [b['text'] for b in content if isinstance(b, dict)
            and b.get('type') == 'text' and isinstance(b.get('text'), str)] if isinstance(content, list) else []


def _owned(snapshot: Snapshot, provider: str, native: str, cwd: str) -> bool:
    def same_path(value) -> bool:
        try:
            return isinstance(value, str) and Path(value).resolve() == Path(cwd).resolve()
        except (OSError, ValueError):
            return False
    if provider == 'codex':
        return any(r.get('type') == 'session_meta'
                   and isinstance(r.get('payload'), dict)
                   and r['payload'].get('id') == native
                   and same_path(r['payload'].get('cwd'))
                   for r in snapshot.records)
    # The runtime located the exact saved provider id; verify the native file
    # itself as well. A shared directory or a filename alone is insufficient.
    return any(r.get('sessionId') == native and same_path(r.get('cwd')) for r in snapshot.records)


def _valid_records(snapshot: Snapshot, provider: str) -> bool:
    for record in snapshot.records:
        if provider == 'claude' and record.get('type') in ('user', 'assistant'):
            message = record.get('message')
            if not isinstance(message, dict) or not isinstance(message.get('content'), (str, list)):
                # Assistant terminal-only records are accepted by older CLIs.
                if not (record.get('type') == 'assistant' and isinstance(message, dict)
                        and message.get('stop_reason') in ('end_turn', 'stop_sequence')):
                    return False
            content = message.get('content') if isinstance(message, dict) else None
        elif provider == 'codex' and record.get('type') in ('event_msg', 'response_item', 'session_meta'):
            payload = record.get('payload')
            if not isinstance(payload, dict):
                return False
            content = payload.get('content')
            if payload.get('role') == 'user' and not isinstance(content, list):
                return False
        else:
            continue
        if isinstance(content, list) and any(not isinstance(b, dict) or
                (b.get('type') in ('text', 'input_text') and not isinstance(b.get('text'), str)) for b in content):
            return False
    return True


def evaluate(row: dict, parent: dict | None) -> tuple[str, dict | None]:
    boundary = _decode(row.get('handoff_boundary', ''))
    completion = _decode(row.get('turn_completion', ''))
    if not boundary:
        return 'no_boundary', None
    path = boundary.get('path') or boundary.get('snapshot', {}).get('path')
    if not path:
        return boundary.get('error') or 'missing', None
    snapshot, error = read_snapshot(path)
    if not snapshot:
        return error, None
    provider, native = boundary['provider'], boundary['native']
    if not _valid_records(snapshot, provider):
        return 'partial', None
    if not _owned(snapshot, provider, native, boundary['cwd']):
        return 'unowned', None
    payload = _decode(row['payload']).get('payload', {})
    header = str(payload.get('message', '')).split('\n', 1)[0]
    if not header.startswith('[dispatched agent finished] '):
        return 'unowned', None
    # Positive receipts can never be manufactured by a tool result/assistant
    # quote, nor by a callback predating the child's completion.
    if any(_timestamp(r) > row['created_at'] and header in text.splitlines()
           for r in snapshot.records for text in _texts(r, provider)):
        return 'delivered', None
    if any(header in text.splitlines() for r in snapshot.records for text in _texts(r, provider)):
        return 'ambiguous_receipt', None
    if boundary.get('error'):
        return boundary['error'], None
    if not completion or completion.get('token') != boundary.get('token'):
        if parent and parent.get('status') in ('error', 'stopped'):
            return 'turn_incomplete', None
        return 'confirming', None
    if parent and (parent.get('waiting_for_question') or parent.get('pending_questions')):
        return 'confirming', None
    if completion.get('error'):
        return completion['error'], None
    if parent and (parent.get('status') == 'running' or parent.get('_dispatching_followup')):
        return 'confirming', None
    if parent and parent.get('_stdin_write_uncertain'):
        return 'writer_pending', None
    if parent and _native_id(parent) != native:
        return 'identity_changed', None
    if 'in-memory queue' in row.get('last_error', ''):
        return 'queued', None
    if not boundary.get('submission_settled'):
        return 'handoff_unsettled', None
    if (completion.get('at', 0) <= boundary['at']
            or completion.get('native') != boundary['native']
            or completion.get('provider') != boundary['provider']):
        return 'identity_changed', None
    baseline = boundary['snapshot']
    if (snapshot.inode != baseline['inode'] or snapshot.device != baseline['device']
            or len(snapshot.raw) < baseline['size']
            or hashlib.sha256(snapshot.raw[:baseline['size']]).hexdigest() != baseline['digest']):
        return 'changed', None
    # A provider stdout result without its terminal native log record is not
    # transcript settlement. Side-band metadata after that record is allowed.
    terminal = False
    for r in snapshot.records:
        if not boundary['at'] < _timestamp(r) <= completion['at']:
            continue
        if provider == 'claude':
            message = r.get('message') or {}
            terminal |= (r.get('type') == 'assistant' and isinstance(message, dict)
                         and message.get('stop_reason') in ('end_turn', 'stop_sequence'))
        else:
            terminal |= (r.get('type') == 'event_msg' and isinstance(r.get('payload'), dict)
                         and r['payload'].get('type') == 'task_complete')
    if not terminal:
        return 'turn_not_persisted', None
    return 'absent', {'boundary': row['handoff_boundary'], 'completion': row['turn_completion'],
                      'snapshot': snapshot.identity(), 'fence_token': row['fence_token']}


class SettlementStore:
    """DeliveryStore mixin: all evidence/budget mutations are transactional."""
    clock: Callable[[], float]

    def _db(self) -> Any:
        raise NotImplementedError

    def record_handoff(self, row: dict, parent: dict) -> None:
        parent.pop('_report_attempt', None)
        message = _decode(row['payload']).get('payload', {}).get('message', '')
        if not isinstance(message, str) or not message.startswith('[dispatched agent finished] '):
            return  # background-job/other notifications retain their original contract
        path, error = _locate(parent)
        snapshot, error = read_snapshot(path) if path else (None, error)
        boundary = {'token': row['fence_token'], 'at': self.clock(),
                    'provider': parent.get('provider', 'claude'), 'native': _native_id(parent),
                    'cwd': _cwd(parent), 'path': path,
                    'snapshot': snapshot.identity() if snapshot else {}, 'error': error}
        if snapshot and not _owned(snapshot, boundary['provider'], boundary['native'], boundary['cwd']):
            boundary['error'] = 'unowned'
        if snapshot and not _valid_records(snapshot, boundary['provider']):
            boundary['error'] = 'partial'
        with self._db() as db:
            changed = db.execute("UPDATE inbox SET handoff_boundary=?,turn_completion='',check_status='confirming' "
                                 "WHERE event_id=? AND project_id=? AND state='dispatch_intent' AND fence_token=? AND lease_until>?",
                                 (_encode(boundary), row['event_id'], row['project_id'], row['fence_token'], self.clock())).rowcount
        if changed != 1:
            raise RuntimeError('handoff boundary lost its claim')
        parent['_report_attempt'] = (row['event_id'], row['project_id'], row['fence_token'])

    def record_handoff_response(self, row: dict, body: dict, *, needs_ack: bool) -> None:
        """Persist no-late-writer evidence, including across server restart.

        A provider turn can finish while a timed-out stdin writer is still
        alive. Transcript absence alone must never authorize its duplicate.
        """
        settled = (body.get('session_id') == row['parent_session_id'] and not body.get('queued')
                   and (not needs_ack or body.get('stdin_write_ack') == 'written'))
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT handoff_boundary FROM inbox WHERE event_id=? AND project_id=? '
                                 'AND fence_token=?', (row['event_id'], row['project_id'], row['fence_token'])).fetchone()
            if current and current['handoff_boundary']:
                boundary = _decode(current['handoff_boundary'])
                boundary['submission_settled'] = settled
                db.execute('UPDATE inbox SET handoff_boundary=? WHERE event_id=? AND project_id=? AND fence_token=?',
                           (_encode(boundary), row['event_id'], row['project_id'], row['fence_token']))
            db.execute('COMMIT')

    def record_turn_completion(self, session: dict) -> None:
        attempt = session.get('_report_attempt')
        if not attempt or session.get('waiting_for_question') or session.get('pending_questions'):
            return
        completion = {'token': attempt[2], 'at': self.clock(), 'native': _native_id(session),
                      'provider': session.get('provider', 'claude'),
                      'error': 'writer_pending' if session.get('_stdin_write_uncertain') else ''}
        with self._db() as db:
            db.execute("UPDATE inbox SET turn_completion=?,check_status='confirming' "
                       "WHERE event_id=? AND project_id=? AND fence_token=? AND handoff_boundary!='' "
                       "AND turn_completion='' AND state IN ('dispatch_intent','submitted','uncertain','blocked','recovery_required')",
                       (_encode(completion), *attempt))

    def request_report_check(self, event_id: str, project_id: str) -> bool:
        with self._db() as db:
            return db.execute("UPDATE inbox SET check_status='confirming',check_requested=check_requested+1 "
                              "WHERE event_id=? AND project_id=? AND state IN ('submitted','uncertain','blocked','recovery_required')",
                              (event_id, project_id)).rowcount == 1


def authorize_absence(db, row: dict, proof: dict) -> bool:
    """Called INSIDE DeliveryStore.retry's write transaction, never from UI.

    Re-read the exact artifact and re-evaluate stored evidence before consuming
    the allowance. Both the budget and transition to pending commit together.
    A crash before/after claim cannot mint another allowance on reopening.
    """
    if (row['state'] not in (*PARKED, 'submitted') or row['auto_resends'] != 0
            or proof.get('boundary') != row['handoff_boundary']
            or proof.get('completion') != row['turn_completion']
            or proof.get('fence_token') != row['fence_token']):
        return False
    verdict, verified = evaluate(row, None)
    if verdict != 'absent' or verified != proof:
        return False
    db.execute("UPDATE inbox SET auto_resends=auto_resends+1 WHERE event_id=? AND project_id=?",
               (row['event_id'], row['project_id']))
    return True


def note_claude_turn(session: dict, message: dict, store, original: Callable) -> None:
    original(session)
    if store is not None and not message.get('is_error'):
        try:
            store.record_turn_completion(session)
        except Exception as exc:
            _log(f'[delegation-settlement] turn boundary write failed: {exc}', flush=True)


def runtime_callbacks(callbacks: dict) -> dict:
    """Wire provider hooks without moving feature bodies into agent_routes."""
    def turn(event, session):
        session['_report_provider_ended'] = session.get('_report_attempt')
        if callbacks.get('on_turn_end'):
            callbacks['on_turn_end'](event, session)

    return {**callbacks, 'on_turn_end': turn}


def note_runtime_exit(session: dict, store) -> None:
    """Called after native MC question parsing, before the completion callback."""
    if store is not None and session.get('_report_provider_ended') == session.get('_report_attempt'):
        try:
            store.record_turn_completion(session)
        except Exception as exc:
            _log(f'[delegation-settlement] provider boundary write failed: {exc}', flush=True)


def settle(store, row: dict, sessions, parent_guard: Callable) -> int:
    """Evaluate/apply under the same parent manager lock as actual handoffs."""
    with parent_guard(row['project_id']):
        parent = sessions.get(row['parent_session_id'])
        verdict, proof = evaluate(row, parent)
        if verdict == 'delivered':
            return int(store.resolve_report(row['event_id'], row['project_id'], confirmed=True))
        if verdict == 'absent' and proof and row['auto_resends'] == 0:
            return int(store.retry('inbox', row['event_id'], row['project_id'], absence_proof=proof))
        status = 'not_delivered' if verdict == 'absent' else verdict
        with store._db() as db:
            db.execute("UPDATE inbox SET check_status=?,state=CASE WHEN ?='not_delivered' "
                       "THEN 'recovery_required' ELSE state END WHERE event_id=? AND project_id=? "
                       "AND state=? AND fence_token=? AND handoff_boundary=? AND turn_completion=?",
                       (status, status, row['event_id'], row['project_id'], row['state'], row['fence_token'],
                        row['handoff_boundary'], row['turn_completion']))
        return 0
