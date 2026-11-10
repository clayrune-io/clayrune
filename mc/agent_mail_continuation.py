"""Private exact-conversation scope around the existing follow-up lifecycle.

No HTTP fields can construct this scope. Provider actions check it before launch;
writer threads retain only the already-bound object on their session dictionary.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
import threading
from typing import Any, Iterator

from mc.agent_mail_store import SUBMITTING, UNCERTAIN
from mc.agent_mail_target import MailHeld, native, resolve, validate
from mc.core import _log


_scope: ContextVar[Any] = ContextVar('agent_mail_continuation', default=None)


def current() -> Any:
    return _scope.get()


@contextmanager
def activate(scope: Any) -> Iterator[None]:
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)


class Continuation:
    def __init__(self, wake: Any, event: dict[str, Any], bound: dict[str, Any],
                 project: dict[str, Any], sid: str, source: dict[str, Any]):
        self.wake, self.bound, self.project = wake, bound, project
        self.event = {key: event[key] for key in ('event_id', 'fence_token', 'thread_hash', 'laundered')}
        self.sid, self.source = sid, source
        self.session: dict[str, Any] | None = None
        self.attempt = ''
        self.started = False
        self.accepted_receipt = False
        self.gate: Any = None
        self.done = threading.Event()
        self.waited = False

    def preflight(self) -> None:
        sid, source = resolve(self.project, self.bound)
        if sid != self.sid:
            raise MailHeld('conversation_owner_changed')
        self.source = source

    def reserve(self, session: dict[str, Any], attempt: str) -> None:
        if session.get('project_id') != self.bound['project_id'] or session.get('session_id') != self.sid:
            raise MailHeld('conversation_owner_changed')
        with self.wake.store._tx() as db:
            previous = db.execute('SELECT * FROM mail_turn_receipts WHERE event_id=?',
                                  (self.event['event_id'],)).fetchone()
            if previous and previous['started']:
                if previous['attempt_id'] != attempt:
                    raise MailHeld('delivery_attempt_changed')
            else:
                self.check_claim(db)
                db.execute('INSERT INTO mail_turn_receipts(event_id,attempt_id,project_id,session_id,provider,native_id,project_generation) '
                           'VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET attempt_id=excluded.attempt_id '
                           'WHERE mail_turn_receipts.started=0',
                           (self.event['event_id'], attempt, self.bound['project_id'], self.sid,
                            self.bound['provider'], self.bound['native_id'], self.bound['project_generation']))
        self.attempt, self.session = attempt, session
        session['_mail_delivery'] = self

    def check_claim(self, db: Any) -> None:
        store = self.wake.store
        row = db.execute('SELECT * FROM events WHERE event_id=?', (self.event['event_id'],)).fetchone()
        if (row is None or row['state'] != SUBMITTING
                or row['fence_token'] != self.event['fence_token']
                or row['lease_until'] <= store.clock()):
            raise MailHeld('submission_claim_lost')
        reason = store._validate_target(db, row['registration_id'], row['thread_hash'], store.clock())
        if reason:
            raise MailHeld(reason)
        db.execute('UPDATE events SET lease_until=? WHERE event_id=?',
                   (store.clock() + store.config.lease_seconds, self.event['event_id']))

    @contextmanager
    def admission(self) -> Iterator[None]:
        """Serialize consent and process creation against store revocation."""
        self.begin()
        # The admission receipt is already durable. A post-child factory
        # exception may roll back this consent/lease window, never the receipt.
        with self.wake.store._tx() as db:
            self.check_claim(db)
            yield

    def begin(self) -> None:
        """Admit a pipe turn; its possibly blocking I/O never holds SQLite."""
        if not self.attempt:
            raise MailHeld('delivery_attempt_missing')
        with self.wake.store._tx() as db:
            self.check_claim(db)
            changed = db.execute('UPDATE mail_turn_receipts SET started=1 WHERE event_id=? AND attempt_id=?',
                                 (self.event['event_id'], self.attempt)).rowcount
            if changed != 1:
                raise MailHeld('delivery_attempt_changed')
            self.started = True

    def check_command(self, cmd: Any, engine: str, sid: str, cwd: Any) -> None:
        validate(self.project, self.source, self.bound, busy=False)
        if engine != self.bound['provider'] or sid != self.sid:
            raise MailHeld('conversation_owner_changed')
        if str(Path(cwd or '').resolve()) != self.bound['cwd']:
            raise MailHeld('worktree_changed')
        args = [str(a) for a in cmd]
        expected = self.bound['native_id']
        if engine == 'codex':
            resumes = [args[i + 2] for i in range(len(args) - 2)
                       if args[i:i + 2] == ['exec', 'resume']]
        else:
            resumes = [args[i + 1] for i in range(len(args) - 1)
                       if args[i] in ('-r', '--resume')]
        if resumes != [expected]:
            raise MailHeld('exact_resume_required')

    def accept(self) -> None:
        self.wake.accepted(self.event['event_id'], self.attempt, None)
        self.accepted_receipt = True
        if self.session and self.session.get('_delivery_attempt_id') == self.attempt:
            self.session.pop('_stdin_write_uncertain', None)
        self.done.set()
        if self.gate is not None:
            self.gate.accept()
        if self.session is not None:
            self.wake.consume_question(self.event['event_id'], self.attempt, self.session,
                                       bound=self.bound, text=self.event['laundered']['reply_text'])

    def unknown(self) -> None:
        if self.accepted_receipt:
            return
        store = self.wake.store
        with store._tx() as db:
            receipt = db.execute('SELECT accepted FROM mail_turn_receipts WHERE event_id=? AND attempt_id=?',
                                 (self.event['event_id'], self.attempt)).fetchone()
            if receipt is not None and receipt['accepted']:
                return
            row = db.execute('SELECT * FROM events WHERE event_id=?', (self.event['event_id'],)).fetchone()
            if row is not None and row['state'] == SUBMITTING:
                store._move(db, self.event['event_id'], SUBMITTING, UNCERTAIN,
                            reason='acknowledgment_missing', fence=self.event['fence_token'])
        if self.session and self.session.get('_delivery_attempt_id') == self.attempt:
            self.session['_stdin_write_uncertain'] = True
            self.session['pending_recovery_message'] = None
        self.done.set()


def reserved(session: dict[str, Any], attempt: str) -> None:
    scope = current()
    if scope is not None:
        if scope.attempt == attempt:
            if session.get('project_id') != scope.bound['project_id'] or session.get('session_id') != scope.sid:
                raise MailHeld('conversation_owner_changed')
            # Publication carries an admitted token; it must not introduce
            # a new database-failure point after the agent-log owner changed.
            scope.session = session
            session['_mail_delivery'] = scope
        else:
            scope.reserve(session, attempt)


def clear_gates(session: dict[str, Any]) -> None:
    if current() is not None:
        return
    session['waiting_for_plan_approval'] = False
    session['waiting_for_question'] = False
    session.pop('pending_questions', None)


def guard_session(session: dict[str, Any], project: dict[str, Any]) -> None:
    scope = current()
    if scope is not None:
        if session.get('session_id') != scope.sid:
            raise MailHeld('conversation_owner_changed')
        validate(project, session, scope.bound)


def reject_rollover() -> None:
    if current() is not None:
        raise MailHeld('rollover_required')


def check_persona(character: Any) -> None:
    scope = current()
    if scope is not None and character != scope.bound['character']:
        raise MailHeld('persona_unavailable')


def revival_metadata() -> dict[str, Any]:
    scope = current()
    return ({'trigger_type': scope.bound['trigger_type'], 'trigger_id': scope.bound['trigger_id']}
            if scope is not None else {})


def run_followup(fn: Any) -> None:
    if current() is not None:
        fn()
    else:
        threading.Thread(target=fn, daemon=True).start()


def followup_route(fn: Any) -> Any:
    @wraps(fn)
    def run(project_id: str) -> Any:
        scope = current()
        if scope is not None:
            from flask import request
            if (project_id != scope.bound['project_id']
                    or (request.get_json() or {}).get('session_id') != scope.sid):
                raise MailHeld('conversation_owner_changed')
            scope.preflight()
        return fn(project_id)
    return run


def spawn_boundary(fn: Any) -> Any:
    @wraps(fn)
    def run(cmd: Any, **kwargs: Any) -> Any:
        scope = current()
        if scope is None:
            return fn(cmd, **kwargs)
        scope.check_command(cmd, kwargs.get('engine', ''), kwargs.get('session_id', ''), kwargs.get('cwd'))
        with scope.admission():
            proc = fn(cmd, **kwargs)
        # Claude Mode A passes the prompt as argv. Interactive/piped spawns
        # require their actual writer's receipt, never just a process id.
        if kwargs.get('engine') == 'claude' and not kwargs.get('interactive') and kwargs.get('deliver') is None:
            scope.accept()
        return proc
    return run


def completed(session: dict[str, Any]) -> None:
    """A lifecycle callback supplies completion only, never acceptance."""
    scope = session.get('_mail_delivery')
    if (scope is None or session.get('_delivery_attempt_id') != scope.attempt
            or session.get('project_id') != scope.bound['project_id']
            or session.get('session_id') != scope.sid
            or session.get('project_generation', 1) != scope.bound['project_generation']
            or (session.get('provider') or 'claude') != scope.bound['provider']
            or native(session) != scope.bound['native_id']
            or session.get('status') not in ('completed', 'idle')):
        return
    try:
        with scope.wake.store._tx() as db:
            db.execute('UPDATE mail_turn_receipts SET completed=1 WHERE event_id=? AND attempt_id=?',
                       (scope.event['event_id'], scope.attempt))
        scope.wake.settle(scope.event['event_id'])
    except Exception as exc:
        _log(f'[agent-mail] completion receipt failed: {type(exc).__name__}', flush=True)
