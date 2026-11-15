"""Journal lifecycle and decision communication for steward roles (SR-4).

Two jobs, both unattended-safe:

* ``append_journal`` / ``record_lifecycle`` write dated, append-only entries to
  the assignment's ``docs/_journal/<item>-<slug>.md``. Nothing here ever calls
  a backlog-note route (AGENT_RULES.md, CLAUDE.md "unattended agents never
  write backlog notes").
* ``raise_decision`` routes an ask that needs a human. Order is fixed:
  state -> open-ask reply check -> route -> dedupe -> daily cap -> send.
  A dispatched role reports to its spawner, an attended human is asked in
  chat, and only an unattended root with no spawner reaches the mailer.

Mail goes through the existing ``tools/night-review/send_mail.py``, handed the
role's project and session so it tries agent-mail first (the owner's reply then
wakes that session as a chat follow-up) and falls back to Gmail SMTP only when
agent-mail is unavailable. A Gmail-sent ask's reply is read ONLY through
``tools/mail-mcp/read_digest.py`` (laundered); an agent-mail ask is never read
from Gmail because its reply arrives in the session. There is no SMTP/IMAP code
here and no fallback to the raw mail MCP: a reader error blocks the send. A digest is untrusted data; ``ReplyCheck.grants_authority`` is
always False, so an ``approved`` still has to pass the action's own human gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from threading import Lock, RLock
from typing import Any, Callable

from mc.core import _log
from mc.steward_role_model import RoleError, identifier, journal_path
from mc.steward_role_state import (
    MAX_STR, OPEN_STATUS, RoleStateStore, StateError, _parse, _stamp,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SEND_MAIL = REPO_ROOT / 'tools' / 'night-review' / 'send_mail.py'
READ_DIGEST = REPO_ROOT / 'tools' / 'mail-mcp' / 'read_digest.py'

MAX_EMAILS_PER_DAY = 1
MAX_OPEN_DECISIONS = 5
MAX_BODY_WORDS = 200
MAX_FACTS = 5
MAX_JOURNAL_BODY = 20000

LIFECYCLE_EVENTS = frozenset({
    'started', 'completed', 'skipped_busy', 'skipped_disabled', 'blocked_charter',
    'blocked_guard', 'blocked_channel', 'dispatch_failed',
})
_TAG = re.compile(r'[A-Za-z0-9 _-]{1,40}\Z')
_JOURNAL_LOCKS: dict[str, RLock] = {}
_JOURNAL_GUARD = Lock()

ROUTE_SPAWNER, ROUTE_CHAT, ROUTE_EMAIL = 'spawner', 'chat', 'email'
# An ask that went out through agent-mail. Recorded in the same ``route`` field
# (no state-schema change) once the mailer reports that transport; its reply
# wakes the sending session, so no Gmail reply read applies to it.
ROUTE_AGENT_MAIL = 'agent_mail'
TRANSPORT_AGENT_MAIL, TRANSPORT_GMAIL = 'agent_mail', 'gmail'


class CommsError(RoleError):
    """Invalid communication input or an unusable channel."""


# ── journal ────────────────────────────────────────────────────────────────

def _journal_lock(path: Path) -> RLock:
    with _JOURNAL_GUARD:
        return _JOURNAL_LOCKS.setdefault(str(path), RLock())


def append_journal(project: dict, relative_path: str, event: str, body: str = '',
                   *, schedule_id: str = '', now: datetime | None = None) -> Path:
    """Append one dated entry to the assignment journal; return its path.

    ``relative_path`` is resolved by ``steward_role_model.journal_path`` against
    the registered project root (never the caller's cwd), so a journal written
    from a worktree session still lands in the canonical ``docs/_journal/``.
    Append-only, fsynced, no size cap; raises on failure (callers that must not
    raise use ``record_lifecycle``).
    """
    if not re.fullmatch(r'[a-z_]{1,40}', event or ''):
        raise CommsError('journal event must be a short snake_case word')
    text = (body or '').replace('\x00', '')[:MAX_JOURNAL_BODY]
    # A body line must not be able to forge a journal heading.
    text = re.sub(r'(?m)^#', r'\\#', text)
    target = journal_path(project, relative_path)
    head = f'## {_stamp(now)} | {event}' + (f' | schedule {schedule_id}' if schedule_id else '')
    entry = f'\n{head}\n{text}\n'
    with _journal_lock(target):
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, 'a', encoding='utf-8', newline='\n') as fh:
            fh.write(entry)
            fh.flush()
            os.fsync(fh.fileno())
    return target


def record_lifecycle(project: dict, schedule: dict, event: str, detail: str = '',
                     *, store: RoleStateStore, now: datetime | None = None) -> dict[str, Any]:
    """Notifier adapter for one lifecycle event. Never raises.

    Journals first (the durable narrative), then stamps ``last_attempt_at`` /
    ``last_completed_at`` / ``last_result`` in state. Each half's failure is
    logged and reported in the returned dict; one failing does not hide the
    other. There is no backlog-note path.
    """
    result: dict[str, Any] = {'ok': False, 'journal': False, 'state': False, 'error': ''}
    if event not in LIFECYCLE_EVENTS:
        result['error'] = f'unknown lifecycle event {event!r}'
        _log(f'[steward-role-comms] {result["error"]}')
        return result
    try:
        pid = identifier(project.get('id'), 'project_id')
        sid = identifier(schedule.get('id'), 'schedule_id')
        role = schedule['steward_role']
    except (RoleError, KeyError, TypeError) as exc:
        result['error'] = f'lifecycle target invalid: {exc}'
        _log(f'[steward-role-comms] {result["error"]}')
        return result
    errors = []
    try:
        append_journal(project, role['journal_path'], event, detail,
                       schedule_id=sid, now=now)
        result['journal'] = True
    except (RoleError, OSError, KeyError, TypeError) as exc:
        errors.append(f'journal: {exc}')
        _log(f'[steward-role-comms] journal append for {pid}/{sid} failed: {exc}')
    try:
        def mutate(state: dict[str, Any]) -> None:
            stamp = _stamp(now)
            if event in ('started', 'dispatch_failed'):
                state['last_attempt_at'] = stamp
            if event == 'completed':
                state['last_completed_at'] = stamp
            state['last_result'] = {'event': event, 'at': stamp, 'detail': detail[:MAX_STR]}
        store.update(pid, sid, mutate)
        result['state'] = True
    except StateError as exc:
        errors.append(f'state: {exc}')
    result['ok'] = result['journal'] and result['state']
    result['error'] = '; '.join(errors)
    return result


# ── default tool runners (tests inject fakes; these refuse under pytest) ────

def _under_pytest() -> bool:
    return bool(os.environ.get('PYTEST_CURRENT_TEST')) and not os.environ.get('MC_LIVE_MAIL_TESTS')


class SendResult(int):
    """A mailer exit code that also says which transport carried the mail.

    A plain ``int`` from a mailer means Gmail; only ``default_mailer`` (reading
    send_mail.py's own ``sent via agent-mail`` line) sets ``agent_mail``.
    """
    transport: str = TRANSPORT_GMAIL

    def __new__(cls, code: int, transport: str = TRANSPORT_GMAIL) -> 'SendResult':
        obj = super().__new__(cls, code)
        obj.transport = transport
        return obj


def default_mailer(subject: str, body: str, *, project_id: str = '', session_id: str = '',
                   run: Callable[..., Any] | None = None) -> int:
    """Run the existing send_mail.py and return its exit code (body via file).

    With ``project_id`` and ``session_id`` the mailer is told which session is
    asking (``--project`` / ``--session``), so it goes through agent-mail first
    and the owner's reply lands in that session. Without them (no session
    recorded yet) it is Gmail, as before. The returned ``SendResult`` carries the
    transport that actually accepted the mail.

    Exit codes: 0 sent+verified, 1 send failed, 2 not configured, 3 accepted but
    not found in the mailbox, 4 ``mail_paused`` kill switch, 5 pytest guard.
    A timeout or OSError propagates: the caller treats it as an unknown send.
    """
    if run is None:
        if _under_pytest():
            return 5
        run = subprocess.run
    with tempfile.TemporaryDirectory(prefix='mc-role-mail-') as tmp:
        body_file = Path(tmp) / 'body.txt'
        body_file.write_text(body, encoding='utf-8')
        cmd = [sys.executable, '-I', str(SEND_MAIL), '--subject', subject,
               '--body-file', str(body_file), '--verify']
        if project_id and session_id:
            cmd += ['--project', project_id, '--session', session_id]
        proc = run(cmd, capture_output=True, text=True, timeout=300)
    code = int(proc.returncode)
    via_agent_mail = code == 0 and '[send_mail] sent via agent-mail' in (proc.stdout or '')
    return SendResult(code, TRANSPORT_AGENT_MAIL if via_agent_mail else TRANSPORT_GMAIL)


def default_digest_reader(query: str, *, run: Callable[..., Any] | None = None) -> dict:
    """Run the existing laundering reader and return its parsed JSON.

    Raises CommsError on a non-zero exit or unparseable output. There is no
    other mail-reading path; the caller must not invent one on failure.
    """
    if run is None:
        if _under_pytest():
            raise CommsError('digest reader refused under pytest')
        run = subprocess.run
    try:
        proc = run([sys.executable, '-I', str(READ_DIGEST), '--query', query, '--count', '5'],
                   capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CommsError(f'digest reader did not run: {exc}') from exc
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise CommsError('digest reader output was not JSON') from exc
    if proc.returncode != 0 or not isinstance(data, dict) or data.get('ok') is not True:
        raise CommsError(f'digest reader failed (exit {proc.returncode})')
    return data


# ── reply check ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReplyCheck:
    """Result of reading one open ask's reply. Untrusted; grants nothing."""
    decision_id: str
    status: str                    # answered | no_reply | unclear | error
    reply_decision: str = ''       # approved | declined, only when status == answered
    reply_text: str = ''           # untrusted, in-memory only, never persisted
    detail: str = ''
    grants_authority: bool = field(default=False, init=False)


def interpret_digest(decision_id: str, payload: Any) -> ReplyCheck:
    """Map a read_digest payload to a ReplyCheck, failing closed on any surprise."""
    try:
        digest = payload['content']['digest']
        verdict = digest['decision']
        mixed = digest['ambiguous_or_mixed']
    except (KeyError, TypeError):
        return ReplyCheck(decision_id, 'error', detail='digest has an unexpected shape')
    if payload.get('ok') is not True or type(mixed) is not bool:
        return ReplyCheck(decision_id, 'error', detail='digest is not a clean ok envelope')
    if verdict == 'no_reply_found':
        return ReplyCheck(decision_id, 'no_reply')
    text = digest.get('reply_text')
    text = text[:MAX_STR] if isinstance(text, str) else ''
    if verdict in ('approved', 'declined') and not mixed:
        return ReplyCheck(decision_id, 'answered', verdict, text)
    return ReplyCheck(decision_id, 'unclear', reply_text=text,
                      detail='reply is ambiguous or mixed; treated as no decision')


def _ref(project_id: str, schedule_id: str, key: str) -> str:
    return 'D' + hashlib.sha256(f'{project_id}:{schedule_id}:{key}'.encode()).hexdigest()[:10]


def _tag(role_tag: str) -> str:
    if not _TAG.fullmatch(role_tag or ''):
        raise CommsError('role_tag must be short letters, digits, space, _ or -')
    return f'[Clayrune {role_tag}]'


def _awaits_gmail_reply(dec: dict[str, Any]) -> bool:
    """An open ask whose reply, if any, is in Gmail. An agent-mail ask is not:
    the owner's reply wakes the sending session as a chat follow-up, so there is
    no Gmail thread to read and no reader to fail closed on."""
    return dec['status'] in OPEN_STATUS and dec['route'] != ROUTE_AGENT_MAIL


def check_pending_replies(project_id: str, schedule_id: str, *, store: RoleStateStore,
                          digest_reader: Callable[[str], dict], role_tag: str = 'steward',
                          now: datetime | None = None) -> list[ReplyCheck]:
    """Read the reply for every open Gmail-sent ask, via the laundering reader only.

    Call at the start of a cycle and before raising any email. An ``answered``
    reply closes the ask with ``reply_decision``. Reader failures come back as
    ``error`` results; callers must treat any ``error`` as "do not send".
    Asks sent through agent-mail are skipped: their reply arrives in the
    session itself. Raises StateError if state is unreadable.
    """
    tag = _tag(role_tag)
    state = store.read(project_id, schedule_id)
    results: list[ReplyCheck] = []
    for dec in state['decisions']:
        if not _awaits_gmail_reply(dec):
            continue
        try:
            check = interpret_digest(dec['id'], digest_reader(f'{tag} {dec["id"]}'))
        except Exception as exc:  # reader contract: any failure closes the gate
            check = ReplyCheck(dec['id'], 'error', detail=f'reader failed: {exc}'[:MAX_STR])
            _log(f'[steward-role-comms] reply read for {dec["id"]} failed: {exc}')
        results.append(check)
        if check.status == 'answered':
            _close_answered(store, project_id, schedule_id, check, now)
    return results


def _close_answered(store: RoleStateStore, pid: str, sid: str, check: ReplyCheck,
                    now: datetime | None) -> None:
    def mutate(state: dict[str, Any]) -> None:
        for dec in state['decisions']:
            if dec['id'] == check.decision_id and dec['status'] in OPEN_STATUS:
                dec['status'] = 'answered'
                dec['reply_decision'] = check.reply_decision
                dec['reply_checked_at'] = _stamp(now)
    store.update(pid, sid, mutate)


# ── composition and routing ────────────────────────────────────────────────

@dataclass(frozen=True)
class DecisionRequest:
    """One ask. ``key`` names the EVENT: same key, same ask, never re-raised."""
    key: str
    summary: str
    ask: str
    facts: tuple[str, ...] = ()
    options: tuple[str, ...] = ()
    recommendation: str = ''
    detail_ref: str = ''


@dataclass(frozen=True)
class RouteContext:
    spawner_session_id: str = ''
    human_attended: bool = False
    # The role session raising the ask. Handed to the mailer so the mail goes
    # through agent-mail and the owner's reply wakes THIS session. Empty falls
    # back to the assignment's recorded ``current_session_id``.
    session_id: str = ''


@dataclass
class DecisionOutcome:
    status: str
    route: str = ''
    decision_id: str = ''
    message: str = ''
    report: str = ''
    replies: list[ReplyCheck] = field(default_factory=list)


def choose_route(ctx: RouteContext) -> str:
    """Spawner first (AGENT_RULES.md: a dispatched role does not email), then an
    attended chat, and only otherwise the decision email."""
    if (ctx.spawner_session_id or '').strip():
        return ROUTE_SPAWNER
    if ctx.human_attended:
        return ROUTE_CHAT
    return ROUTE_EMAIL


def compose_body(req: DecisionRequest) -> str:
    """Ask first, at most five one-line facts, options with the recommendation
    marked, about 200 words. Detail stays in the journal."""
    if len(req.facts) > MAX_FACTS:
        raise CommsError(f'at most {MAX_FACTS} facts')
    lines = [req.ask.strip()]
    lines += [f'- {f.strip()}' for f in req.facts]
    if req.options:
        lines.append('Options:')
        for opt in req.options:
            mark = ' (recommended)' if opt.strip() == req.recommendation.strip() else ''
            lines.append(f'* {opt.strip()}{mark}')
    if req.detail_ref:
        lines.append(f'Detail: {req.detail_ref.strip()}')
    body = '\n'.join(lines)
    if any('\n' in f for f in req.facts) or not req.ask.strip():
        raise CommsError('ask is required and each fact is one line')
    if len(body.split()) > MAX_BODY_WORDS:
        raise CommsError(f'decision body exceeds {MAX_BODY_WORDS} words')
    return body


def _validate(req: DecisionRequest) -> None:
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', req.key or ''):
        raise CommsError('decision key must be a short stable event id')
    one_line = req.summary.strip()
    if not one_line or '\n' in one_line or len(one_line) > 100:
        raise CommsError('summary must be one non-empty line of at most 100 chars')


def _counted_today(state: dict[str, Any], now: datetime) -> int:
    day = now.astimezone().date()
    return sum(1 for d in state['decisions'] if d['cap_counted_at']
               and _parse(d['cap_counted_at'], 'cap_counted_at').astimezone().date() == day)


_HELD_REASON = {1: 'send_failed', 2: 'channel_unavailable', 3: 'not_delivered',
                4: 'channel_paused', 5: 'channel_refused'}


def raise_decision(project: dict, schedule: dict, req: DecisionRequest, ctx: RouteContext,
                   *, store: RoleStateStore, mailer: Callable[..., int],
                   digest_reader: Callable[[str], dict], role_tag: str = 'steward',
                   max_per_day: int = MAX_EMAILS_PER_DAY,
                   now: datetime | None = None) -> DecisionOutcome:
    """Route one ask. Never sends mail unless every earlier gate passed.

    ``mailer(subject, body, *, project_id, session_id)`` returns an exit code;
    a ``SendResult`` whose ``transport`` is ``agent_mail`` marks the ask as
    answered in the session (route ``agent_mail``), so it is excluded from the
    Gmail reply read and the open-ask ceiling.

    Outcome ``status`` values: ``reported`` (spawner/chat; ``report`` holds the
    text to put in the final message), ``sent``, ``delivery_unknown``, ``held``
    (reason in ``message``), ``duplicate``, ``blocked_state``,
    ``blocked_reader``, ``blocked_open_asks``, ``invalid``. The journal gets an
    entry for every outcome except ``duplicate`` and ``invalid``.
    """
    stamp_now = (now or datetime.now().astimezone()).astimezone()
    try:
        _validate(req)
        tag = _tag(role_tag)
        body = compose_body(req)
        pid = identifier(project.get('id'), 'project_id')
        sid = identifier(schedule.get('id'), 'schedule_id')
        jpath = schedule['steward_role']['journal_path']
    except (RoleError, KeyError, TypeError) as exc:
        return DecisionOutcome('invalid', message=str(exc))
    ref = _ref(pid, sid, req.key)
    route = choose_route(ctx)
    subject = f'{tag} DECISION NEEDED: {req.summary.strip()} (ref {ref})'

    def journal(event: str, text: str) -> None:
        try:
            append_journal(project, jpath, event, text, schedule_id=sid, now=stamp_now)
        except (RoleError, OSError) as exc:
            _log(f'[steward-role-comms] journal for {pid}/{sid} failed: {exc}')

    with store.lock(pid, sid):
        # 1. State must be readable, and a crashed send becomes delivery_unknown.
        try:
            state = store.read(pid, sid)
            if any(d['status'] == 'sending' for d in state['decisions']):
                def reconcile(st: dict[str, Any]) -> None:
                    for d in st['decisions']:
                        if d['status'] == 'sending':
                            d['status'] = 'delivery_unknown'
                            d['reason'] = 'crashed_mid_send'
                            d['cap_counted_at'] = d['cap_counted_at'] or _stamp(stamp_now)
                store.update(pid, sid, reconcile)
        except StateError as exc:
            journal('decision_blocked', f'{req.key}: role state unusable ({exc}); nothing sent.')
            return DecisionOutcome('blocked_state', route, ref, str(exc))

        # 2. Reply-first: read every open email ask before any new email.
        replies: list[ReplyCheck] = []
        if route == ROUTE_EMAIL and any(_awaits_gmail_reply(d) for d in state['decisions']):
            try:
                replies = check_pending_replies(pid, sid, store=store, digest_reader=digest_reader,
                                                role_tag=role_tag, now=stamp_now)
                state = store.read(pid, sid)
            except StateError as exc:
                return DecisionOutcome('blocked_state', route, ref, str(exc))
            if any(r.status == 'error' for r in replies):
                journal('decision_blocked', f'{req.key}: pending-reply check failed; '
                        'not sending. No fallback mail path was used.')
                return DecisionOutcome('blocked_reader', route, ref,
                                       'pending-reply check failed; fail closed', replies=replies)

        # 3. Dedupe by event key. Held asks were never delivered, so they retry.
        existing = next((d for d in state['decisions'] if d['id'] == ref), None)
        if existing is not None and existing['status'] != 'held':
            return DecisionOutcome('duplicate', route, ref,
                                   f'already {existing["status"]}', replies=replies)

        # 4. Non-email routes: record once and hand the text to the caller.
        if route != ROUTE_EMAIL:
            _upsert(store, pid, sid, ref, req.key, route, subject, 'reported', '', stamp_now)
            text = f'DECISION NEEDED: {req.summary.strip()}\n{body}'
            journal('decision_reported', f'{req.key}: routed to {route}; {req.summary.strip()}')
            return DecisionOutcome('reported', route, ref, f'reported to {route}', text, replies)

        # 5. Email gates: open-ask ceiling, then the daily cap.
        if existing is None and sum(_awaits_gmail_reply(d) for d in state['decisions']) >= MAX_OPEN_DECISIONS:
            journal('decision_blocked', f'{req.key}: {MAX_OPEN_DECISIONS} asks are already unanswered.')
            return DecisionOutcome('blocked_open_asks', route, ref,
                                   'too many unanswered asks', replies=replies)
        if _counted_today(state, stamp_now) >= max_per_day:
            _upsert(store, pid, sid, ref, req.key, route, subject, 'held', 'daily_cap', stamp_now)
            journal('decision_held', f'{req.key}: daily email cap reached; will retry on a later cycle.')
            return DecisionOutcome('held', route, ref, 'daily_cap', replies=replies)

        # 6. Write-ahead, then send. A crash here leaves 'sending' => unknown.
        _upsert(store, pid, sid, ref, req.key, route, subject, 'sending', '', stamp_now,
                attempted=True, bump=True)
        try:
            code = mailer(subject, body, project_id=pid,
                          session_id=ctx.session_id or state['current_session_id'])
        except Exception as exc:  # timeout / OSError: the message may have left
            _log(f'[steward-role-comms] mailer raised for {ref}: {exc!r}')
            code = None
        if code == 0:
            if getattr(code, 'transport', TRANSPORT_GMAIL) == TRANSPORT_AGENT_MAIL:
                _finish(store, pid, sid, ref, 'sent', '', stamp_now, counted=True, sent=True,
                        route=ROUTE_AGENT_MAIL)
                journal('decision_sent', f'{req.key}: sent via agent-mail ({ref}); the reply '
                        'wakes this session as a chat follow-up.')
                return DecisionOutcome('sent', ROUTE_AGENT_MAIL, ref, 'sent via agent-mail',
                                       replies=replies)
            _finish(store, pid, sid, ref, 'sent', '', stamp_now, counted=True, sent=True)
            journal('decision_sent', f'{req.key}: emailed ({ref}); awaiting reply.')
            return DecisionOutcome('sent', route, ref, 'emailed', replies=replies)
        if code in _HELD_REASON:
            reason = _HELD_REASON[code]
            # not_delivered: SMTP accepted it but it never appeared, so it counts.
            _finish(store, pid, sid, ref, 'held', reason, stamp_now, counted=(code == 3))
            journal('decision_held', f'{req.key}: not delivered ({reason}); visible blocker, '
                    'will retry on a later cycle.')
            return DecisionOutcome('held', route, ref, reason, replies=replies)
        _finish(store, pid, sid, ref, 'delivery_unknown', 'send_result_unknown', stamp_now, counted=True)
        journal('decision_unknown', f'{req.key}: send result unknown; will NOT resend. Review the '
                f'mailbox for ref {ref}.')
        return DecisionOutcome('delivery_unknown', route, ref,
                               'send result unknown; review required', replies=replies)


def _upsert(store: RoleStateStore, pid: str, sid: str, ref: str, key: str, route: str,
            subject: str, status: str, reason: str, now: datetime, *,
            attempted: bool = False, bump: bool = False) -> None:
    def mutate(state: dict[str, Any]) -> None:
        dec = next((d for d in state['decisions'] if d['id'] == ref), None)
        if dec is None:
            dec = {'id': ref, 'key': key, 'route': route, 'status': status, 'reason': reason,
                   'subject': subject[:MAX_STR], 'created_at': _stamp(now),
                   'email_attempted_at': None, 'cap_counted_at': None, 'sent_at': None,
                   'attempts': 0, 'reply_decision': None, 'reply_checked_at': None,
                   'last_reported_status': status}
            state['decisions'].append(dec)
        dec['status'], dec['reason'], dec['last_reported_status'] = status, reason, status
        if attempted:
            dec['email_attempted_at'] = _stamp(now)
        if bump:
            dec['attempts'] += 1
    store.update(pid, sid, mutate)


def _finish(store: RoleStateStore, pid: str, sid: str, ref: str, status: str, reason: str,
            now: datetime, *, counted: bool, sent: bool = False, route: str = '') -> None:
    def mutate(state: dict[str, Any]) -> None:
        for dec in state['decisions']:
            if dec['id'] == ref:
                dec['status'], dec['reason'], dec['last_reported_status'] = status, reason, status
                if route:
                    dec['route'] = route
                if counted:
                    dec['cap_counted_at'] = _stamp(now)
                if sent:
                    dec['sent_at'] = _stamp(now)
    store.update(pid, sid, mutate)


def resolve_unknown(project_id: str, schedule_id: str, decision_id: str, *,
                    store: RoleStateStore, now: datetime | None = None) -> bool:
    """Close a ``delivery_unknown`` ask after someone reviewed the mailbox."""
    done: list[bool] = [False]

    def mutate(state: dict[str, Any]) -> None:
        for dec in state['decisions']:
            if dec['id'] == decision_id and dec['status'] == 'delivery_unknown':
                dec['status'], dec['reason'] = 'resolved', 'reviewed'
                dec['reply_checked_at'] = _stamp(now)
                done[0] = True
    store.update(project_id, schedule_id, mutate)
    return done[0]
