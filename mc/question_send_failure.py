"""What the question channel does when mailing a question FAILS (MC-1179 OS16/MB13).

Before this, a question raised by an unattended run got one attempt at being
mailed. A failure wrote one server-log line, the qid stayed in
`question_channel._delivered`, the chat showed nothing and no push was raised,
so the run parked for good with nobody told.

A failed send now does four things (Dave's decisions, 2026-11-16):

1. removes the qid from `_delivered`, so a later turn boundary can select it again;
2. appends a chat line saying the question was NOT sent and why (closed phrases
   only: never a credential, an address or a remote error text);
3. raises the existing web push, on the first failure and again when it gives up;
4. re-arms the delivery timer with a backoff while the cause can clear on its
   own: MAX_ATTEMPTS attempts in all, waiting BACKOFF_SECONDS between them.

A send whose outcome is UNCERTAIN (a request byte may have left) is never sent
again automatically: it keeps the qid in `_delivered`, does not re-arm, and the
chat line says the question may or may not have arrived.

Pure policy plus three small side effects (chat line, push, timer); the send
itself stays in `question_channel`. A new concern gets its own file.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from mc import question_channel_retention as retention
from mc.core import _log

# One attempt, then three retries after these waits: 5 min, 15 min, 1 h. The
# cap bounds the mail an outage can cause and the timers it can leave behind.
BACKOFF_SECONDS = (300, 900, 3600)
MAX_ATTEMPTS = len(BACKOFF_SECONDS) + 1

# Agent-mail `blocked` reasons that can clear without anyone acting: the vault
# or manifest refresh failing, or the service being briefly unavailable.
# Everything else blocked (consent withdrawn, adapter off, session closed...)
# needs a person, so it is final.
_TRANSIENT_BLOCKED = frozenset({
    'outbound_unavailable', 'transport_unavailable', 'service_unavailable',
    'storage_unavailable', 'provider_unavailable', 'vault_locked'})

_mu = threading.Lock()
_attempts: Dict[str, int] = {}
_retry_timers: Dict[str, Any] = {}
_legacy = threading.local()


@dataclass(frozen=True)
class Failure:
    why: str                 # closed phrase for the chat line
    retryable: bool = False  # can clear on its own, and nothing was sent
    uncertain: bool = False  # a request byte may have left: never re-send


# ── classification ────────────────────────────────────────────────────────────

def note_legacy(code: str) -> None:
    """`question_channel._send_email` records how its attempt failed, so the
    bool it returns (a contract its callers and tests pin) can stay a bool."""
    _legacy.code = code


def from_legacy() -> Failure:
    code = getattr(_legacy, 'code', '')
    _legacy.code = ''
    if code == 'timeout':
        return Failure('the mailer timed out, so the mail may or may not have been sent', uncertain=True)
    if code == 'rc2':
        return Failure('mail is not set up on this install (no mail credentials)')
    if code in ('mailer_missing', 'error') or code.startswith('rc') and code != 'rc1':
        return Failure('the mailer could not run or reported an error')
    # rc1 (SMTP refused or unreachable) and an unrecorded failure: may clear.
    return Failure('the mail server refused the message or could not be reached', retryable=True)


def from_agent_mail(result: Optional[dict]) -> Optional[Failure]:
    """None when the result is a success or an in-flight send, else the failure."""
    result = result or {}
    state, reason = str(result.get('state') or ''), str(result.get('reason') or '')
    tail = f' ({reason})' if reason else ''
    if state in ('accepted', 'sending'):
        return None
    if state == 'uncertain':
        return Failure(f'agent mail reported the send as uncertain{tail}, so it may or may not have arrived',
                       uncertain=True)
    if state == 'retryable' or (state == 'blocked' and reason in _TRANSIENT_BLOCKED):
        return Failure(f'agent mail could not send it right now{tail}', retryable=True)
    return Failure(f'agent mail did not send it: {state or "unknown"}{tail}')


def internal_error() -> Failure:
    return Failure('delivery hit an internal error')


# ── effects ───────────────────────────────────────────────────────────────────

def succeeded(key: str) -> None:
    """A send went out: forget the failures, and keep the qid marked delivered
    again (a failed attempt before this removed it, see on_failure)."""
    from mc import question_channel as qc
    with _mu:
        _attempts.pop(key, None)
        _retry_timers.pop(key, None)
    with qc._lock:
        qc._delivered.add(key)


def cancel_retry(key: str) -> None:
    """A fresh selection of the same key owns delivery now; drop the pending retry."""
    with _mu:
        t = _retry_timers.pop(key, None)
    if t is None:
        return
    finished = getattr(t, 'finished', None)
    if finished is not None and finished.is_set():
        return  # already fired; its own finally releases the retention count
    cancel = getattr(t, 'cancel', None)
    if cancel is not None:
        cancel()
    retention.timer_done(key)


def _push(session: dict, text: str) -> None:
    """The existing web push, only from the process that owns it (the server)."""
    try:
        from mc.blueprints import push_mobile
        if push_mobile.PUSH_VAPID_PATH is None or session.get('housekeeping') or session.get('incognito'):
            return
        from mc.blueprints.project_routes import load_project
        pid, sid = session.get('project_id') or '', session.get('session_id') or ''
        project = load_project(pid) or {}
        if not project.get('notify_push_enabled', True):
            return
        push_mobile._notify_push((project.get('name') or 'Clayrune')[:60], text,
                                 url=f'/?project={pid}&session={sid}',
                                 project_id=pid, session_id=sid, kind='agent')
    except Exception as e:
        _log(f'[question-channel] failure push not raised: {e}', flush=True)


def on_failure(key: str, session: dict, grace: int, failure: Failure,
               redeliver: Callable[[str, dict, int], None]) -> None:
    """Apply decisions 1-4 for one failed attempt at mailing `key`."""
    from mc import question_channel as qc
    with _mu:
        n = _attempts[key] = _attempts.get(key, 0) + 1
    retry = failure.retryable and not failure.uncertain and n < MAX_ATTEMPTS
    if not failure.uncertain:
        with qc._lock:
            qc._delivered.discard(key)
    lines = session.setdefault('log_lines', [])
    if failure.uncertain:
        lines.append(f'[question NOT confirmed sent: {failure.why}. It will NOT be re-sent '
                     f'automatically. Check your mail, or answer it here in the chat.]')
    elif retry:
        wait = BACKOFF_SECONDS[n - 1]
        lines.append(f'[question NOT sent: {failure.why}. Attempt {n} of {MAX_ATTEMPTS}; '
                     f'trying again in {wait // 60} min. You can answer it here in the chat.]')
    elif failure.retryable:
        lines.append(f'[question NOT sent: {failure.why}. Gave up after {n} attempts. '
                     f'Answer it here in the chat.]')
    else:
        lines.append(f'[question NOT sent: {failure.why}. It will not be retried. '
                     f'Answer it here in the chat.]')
    _log(f'[question-channel] q:{key[:8]} not sent (attempt {n}): {failure.why}', flush=True)
    if n == 1 or not retry:
        _push(session, 'A question could not be sent to you and the run is waiting. '
                       'Open the chat to answer it.')
    if retry:
        t = threading.Timer(BACKOFF_SECONDS[n - 1], redeliver, args=(key, session, grace))
        t.daemon = True
        retention.timer_started(key)
        try:
            with _mu:
                _retry_timers[key] = t
            t.start()
        except BaseException:
            with _mu:
                _retry_timers.pop(key, None)
            retention.timer_done(key)
            raise
