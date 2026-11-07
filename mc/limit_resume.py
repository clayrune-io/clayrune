"""MC-1163: durable usage-reset waits, separate from project records.

No timer threads or provider calls here. The scheduler claims due waits; the
adapter submits them through the normal follow-up path. A persisted submitting
claim is never replayed after a crash: its outcome may be unknown.
"""
from __future__ import annotations

import json
import math
import os
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from mc.atomic_json import read_text_with_retry, write_json_atomic
from mc.core import _log

MAX_ATTEMPTS = 2
GRACE_SECONDS = 120
_finalizing = threading.local()


def finalizing(session: dict) -> bool:
    """Only the current completion call may bypass its own hold, never a snapshot."""
    return getattr(_finalizing, 'session', None) is session


CONTINUATION = ('[The provider usage-limit reset time has passed. Continue the '
                'same task from where you stopped; preserve all existing constraints.]')

# Runtime objects and cached system prompts are deliberately not persisted.
_SNAPSHOT_KEYS = (
    'session_id project_id started_at task provider mode source trigger_type trigger_id '
    'claude_session_id provider_session_id agent_model model model_source pinned_model '
    'requested_effort effort_support character character_body character_name character_skills '
    'project_generation _delivery_generation _delegation_turn _notify_session _spawned_by '
    '_notify_workflow _turn_owner _agent_cwd _launch_project_path _worktree_isolated '
    '_worktree_path _worktree_branch _worktree_base_branch _resume_id _resume_confirmed '
    'job_shape hivemind_id hivemind_ws_id hivemind_role log_lines _limit_resume_exhaustion '
    'character_ref assignment_attempt resolved_assignment effort pinned_prompt_path pinned_prompt_sha256 '
    'usage cost_usd num_turns human_joined housekeeping _limit_resume_attempt'
).split()


def reset_time(value: Any) -> datetime | None:
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, (float, int)) and math.isfinite(value):
            return datetime.fromtimestamp(value, timezone.utc)
        if not isinstance(value, str) or not value:
            return None
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def unattended(session: dict) -> bool:
    return (not session.get('incognito')
            and session.get('_turn_owner') != 'human'
            and not session.get('_limit_resume_cancelled')
            and (session.get('source') in ('agent', 'schedule')
                 or session.get('trigger_type') in ('schedule', 'steward', 'dispatch')))


def observe(session: dict, payload: dict) -> None:
    """Only a real exhaustion event for THIS session may arm a wait."""
    session['_limit_resume_exhaustion'] = dict(payload)
    session.pop('_limit_resume_completion', None)
    if _store:
        _store.discard_success(session.get('session_id', ''), session.get('_limit_resume_attempt'))


def observe_claude(session: dict, msg: dict) -> None:
    from mc.allowance_state import detect
    hit = detect('claude', msg)
    if hit:
        observe(session, hit)
    elif msg.get('type') == 'result' and not msg.get('is_error'):
        session.pop('_limit_resume_exhaustion', None)


class ResumeStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.rows = self._read()

    def _read(self) -> dict[str, dict]:
        if self.path.exists():
            data = json.loads(read_text_with_retry(self.path))
            if not isinstance(data, dict):
                raise ValueError('limit-resume state must be an object')
            for sid, row in data.items():
                if (not isinstance(row, dict) or row.get('session_id') != sid
                        or not row.get('project_id')
                        or row.get('phase') not in ('waiting', 'submitting', 'active', 'cancelled')
                        or type(row.get('attempts')) is not int
                        or not 0 <= row['attempts'] <= MAX_ATTEMPTS
                        or reset_time(row.get('resume_at')) is None
                        or not isinstance(row.get('snapshot'), dict)
                        or row['snapshot'].get('session_id') != sid
                        or row['snapshot'].get('project_id') != row['project_id']):
                    raise ValueError(f'invalid limit-resume record {sid!r}')
            return data
        return {}

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        # A restart briefly overlaps old/new servers. The OS releases this
        # lock on crash; reload under it so stale writers cannot double-claim
        # a reset or overwrite another process's cancellation/attempt count.
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.with_suffix('.lock').open('a+b') as stream:
                if stream.tell() == 0:
                    stream.write(b'0')
                    stream.flush()
                deadline = time.monotonic() + 1
                while True:
                    stream.seek(0)
                    try:
                        if os.name == 'nt':
                            import msvcrt
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.01)
                self.rows = self._read()
                yield

    def _commit(self, rows: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self.path, rows, indent=2)
        self.rows = rows  # publish only after durable write succeeds

    def get(self, sid: str) -> dict | None:
        with self._transaction():
            row = self.rows.get(sid)
            return dict(row) if row else None

    def records(self) -> list[dict]:
        with self._transaction():
            return list(self.rows.values())

    def park(self, session: dict, *, enabled: bool = True) -> bool:
        sid = session.get('session_id', '')
        hit = session.get('_limit_resume_exhaustion') or {}
        due = reset_time(hit.get('resets_at'))
        if (not enabled or not sid or not session.get('project_id') or not unattended(session)
                or not due or session.get('status') == 'stopped'
                or session.get('waiting_for_question') or session.get('waiting_for_plan_approval')):
            return False
        with self._transaction():
            prior = self.rows.get(sid) or {}
            if prior.get('phase') == 'waiting':
                session['status'] = 'waiting_for_reset'
                return True
            if (prior.get('phase') == 'submitting'
                    and session.get('_limit_resume_attempt') != prior['attempts']):
                return False  # recovered snapshot predates the uncertain claim
            if prior.get('phase') == 'cancelled' or prior.get('attempts', 0) >= MAX_ATTEMPTS:
                return False
            snapshot = {key: session[key] for key in _SNAPSHOT_KEYS if key in session}
            snapshot['log_lines'] = list(session.get('log_lines', []))
            row = {'session_id': sid, 'project_id': session['project_id'],
                   'resets_at': due.isoformat(),
                   'resume_at': (due + timedelta(seconds=GRACE_SECONDS)).isoformat(),
                   'attempts': prior.get('attempts', 0), 'phase': 'waiting',
                   'snapshot': snapshot}
            self._commit({**self.rows, sid: row})
            session['status'] = 'waiting_for_reset'
            session['log_lines'].append(f"[Waiting for usage reset until {row['resume_at']}]")
            return True

    def claim(self, sid: str, now: datetime) -> dict | None:
        with self._transaction():
            row = self.rows.get(sid)
            if not row or row['phase'] != 'waiting':
                return None
            due = reset_time(row['resume_at'])
            if due is None or due > now or row['attempts'] >= MAX_ATTEMPTS:
                return None
            claimed = {**row, 'phase': 'submitting', 'attempts': row['attempts'] + 1}
            self._commit({**self.rows, sid: claimed})
            return claimed

    def submitted(self, sid: str, *, attempt: int | None = None) -> bool:
        with self._transaction():
            row = self.rows.get(sid)
            # A fast new exhaustion may already have parked the next attempt.
            if (row and row['phase'] == 'submitting'
                    and not row.get('review_required')
                    and (not row.get('deferred_success') or row.get('completion_done'))
                    and (attempt is None or row['attempts'] == attempt)):
                self._commit({**self.rows, sid: {**row, 'phase': 'active'}})
                return True
            return False

    def defer_success(self, session: dict, kind: str) -> dict | None:
        """Commit success and its current output before publishing the hold."""
        sid = session['session_id']
        with self._transaction():
            row = self.rows.get(sid)
            if (not row or row['phase'] != 'submitting' or row.get('review_required')
                    or row['attempts'] != session.get('_limit_resume_attempt')):
                return None
            prior = row.get('deferred_success') or {}
            if kind == 'exit' and prior.get('attempt') != row['attempts']:
                return None  # an exit alone is not successful result evidence
            deferred = {'attempt': row['attempts'], 'kind': kind,
                        'status': 'completed' if kind == 'exit' else 'idle'}
            snapshot = {key: session[key] for key in _SNAPSHOT_KEYS if key in session}
            snapshot['log_lines'] = list(session.get('log_lines', []))
            self._commit({**self.rows, sid: {**row, 'deferred_success': deferred,
                                           'completion_key': row.get('completion_key')
                                           or f'limit-resume:{sid}:{uuid.uuid4().hex}',
                                           'snapshot': snapshot}})
            return deferred

    def completion_done(self, session: dict, attempt: int) -> bool:
        """Receipt written only after completion effects have committed."""
        sid = session['session_id']
        with self._transaction():
            row = self.rows.get(sid)
            if (not row or row['phase'] != 'submitting' or row.get('review_required')
                    or row['attempts'] != attempt):
                return False
            snapshot = {key: session[key] for key in _SNAPSHOT_KEYS if key in session}
            snapshot['log_lines'] = list(session.get('log_lines', []))
            self._commit({**self.rows, sid: {**row, 'completion_done': True,
                                           'snapshot': snapshot}})
            return True

    def discard_success(self, sid: str, attempt: int | None) -> None:
        with self._transaction():
            row = self.rows.get(sid)
            if row and row['attempts'] == attempt and row.get('deferred_success'):
                clean = {k: v for k, v in row.items()
                         if k not in ('deferred_success', 'completion_done', 'completion_key')}
                self._commit({**self.rows, sid: clean})

    def require_review(self, sid: str, attempt: int) -> bool:
        with self._transaction():
            row = self.rows.get(sid)
            if not row or row['phase'] != 'submitting' or row['attempts'] != attempt:
                return False
            reviewed = {k: v for k, v in row.items()
                        if k not in ('deferred_success', 'completion_done', 'completion_key')}
            self._commit({**self.rows, sid: {**reviewed, 'review_required': True}})
            return True

    def cancel(self, sid: str, project_id: str) -> bool:
        with self._transaction():
            row = self.rows.get(sid)
            if not row or row['project_id'] != project_id or row['phase'] not in ('waiting', 'submitting'):
                return False
            self._commit({**self.rows, sid: {**row, 'phase': 'cancelled'}})
            return True

    def clear(self, sid: str) -> None:
        with self._transaction():
            if sid in self.rows:
                rows = dict(self.rows)
                del rows[sid]
                self._commit(rows)

    def view(self, sid: str) -> dict | None:
        row = self.get(sid)
        if not row or row['phase'] not in ('waiting', 'submitting'):
            return None
        return {key: row[key] for key in ('resume_at', 'resets_at', 'attempts', 'phase')}


_store: ResumeStore | None = None


def wire(path: Path) -> None:
    global _store
    try:
        _store = ResumeStore(path)
    except Exception as exc:
        _store = None  # fail closed; never overwrite an unreadable state file
        _log(f'[limit-resume] state unavailable: {exc}', flush=True)


def store() -> ResumeStore | None:
    return _store


def confirm_submission(sid: str, attempt: int | None) -> bool:
    """Async launch acknowledgment, scoped to the claim that queued it."""
    if _store and attempt is not None:
        return _store.submitted(sid, attempt=attempt)
    return False


def submission_failed(session: dict, attempt: int | None) -> None:
    """Keep an unacknowledged launch visible and durably end its workflow step.

    The claim remains submitting: uncertain delivery must never be replayed.
    Call under the project lock so cancellation/human takeover wins.
    """
    if not _store or attempt is None or not unattended(session) or session.get('status') == 'stopped':
        return
    try:
        if not _store.require_review(session.get('session_id', ''), attempt):
            return
    except Exception as exc:
        _log(f'[limit-resume] could not persist submission review: {exc}', flush=True)
    session.pop('_limit_resume_completion', None)
    session['status'] = 'waiting_for_reset'
    session['last_status_change_time'] = time.time()
    session['log_lines'].append('[Reset continuation needs manual review; continue manually or cancel.]')


def view(session: dict) -> dict | None:
    return _store.view(session.get('session_id', '')) if _store else None


def hold_completion(session: dict, config: dict, *, completion_kind: str | None = None) -> bool:
    """Hold pending launches; remember actual success boundaries for their ack.

    Call boundary/ack hooks under the project lock. An idle status alone is not
    success evidence. Persisted success authorizes completion, never delivery.
    """
    row = None
    try:
        if finalizing(session):
            return False  # project-locked completion of the persisted result
        row = _store.get(session.get('session_id', '')) if _store else None
        if (row and row['phase'] == 'submitting' and unattended(session)
                and session.get('status') != 'stopped'
                and (row.get('review_required')
                     or session.get('_limit_resume_attempt') != row['attempts']
                     or not session.get('_limit_resume_exhaustion'))):
            if (not row.get('review_required')
                    and session.get('_limit_resume_attempt') == row['attempts']):
                if (completion_kind == 'turn' and session.get('status') == 'idle'
                        and not session.get('_limit_resume_exhaustion')):
                    assert _store is not None
                    session['_limit_resume_completion'] = _store.defer_success(session, 'turn')
                elif completion_kind == 'exit':
                    assert _store is not None
                    deferred = _store.defer_success(session, 'exit')
                    if deferred:
                        session['_limit_resume_completion'] = deferred
            session['status'] = 'waiting_for_reset'
            return True  # pending acknowledgment or manual review
        if (row and row['phase'] == 'waiting' and unattended(session)
                and session.get('status') != 'stopped'):
            session['status'] = 'waiting_for_reset'
            return True
        return bool(_store and _store.park(session, enabled=config.get('limit_resume_enabled', True)))
    except Exception as exc:
        _log(f'[limit-resume] could not persist wait: {exc}', flush=True)
        # Do not leak a success through normal completion after its deferral
        # write failed. Keep the pending claim visible for manual review.
        if row and row['phase'] == 'submitting':
            session['status'] = 'waiting_for_reset'
            return True
        return session.get('status') == 'waiting_for_reset'


def new_turn(session: dict, *, internal: bool = False) -> None:
    session.pop('_limit_resume_completion', None)
    session.pop('_limit_resume_exhaustion', None)
    session.pop('_allowance_exhausted', None)
    if internal and _store:
        _store.discard_success(session.get('session_id', ''), session.get('_limit_resume_attempt'))
    if not internal:
        session.pop('_limit_resume_attempt', None)
        session.pop('_limit_resume_cancelled', None)
        if _store:
            _store.clear(session.get('session_id', ''))
