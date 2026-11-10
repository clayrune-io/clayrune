"""Mail-only 10-second pipe acknowledgments, with durable late receipts.

There is no timeout-triggered process teardown. Context/payload preparation is
performed before starting the acknowledgment clock. Ordinary callers fall through
to their existing writers and watchdogs.
"""
from __future__ import annotations

import threading
from typing import Any

from mc import agent_mail_continuation as continuation
from mc.core import _log

ACK_SECONDS = 10.0


def session_scope(session: Any) -> Any:
    if session is None:
        return None
    scope = session.get('_mail_delivery')
    return scope if scope is not None and session.get('_delivery_attempt_id') == scope.attempt else None


def finish(scope: Any, delivery: Any) -> None:
    if scope is None:
        return
    try:
        if delivery.ok:
            scope.accept()
        else:
            scope.unknown()
    except Exception as exc:
        _log(f'[agent-mail] pipe receipt failed: {type(exc).__name__}', flush=True)
        scope.unknown()


def acknowledge(delivery: Any, session: Any, gate: Any = None) -> bool:
    """True means this was a mail call; receipt outcome remains separate."""
    scope = session_scope(session) or continuation.current()
    if scope is None:
        return False
    scope.session = session
    scope.gate = gate
    scope.waited = True
    if delivery is not None and delivery.done.wait(ACK_SECONDS) and delivery.ok:
        scope.accept()
    else:
        scope.unknown()
    return True


def write(proc: Any, data: Any, session: Any, lock: Any, close_after: bool) -> dict[str, str]:
    scope = continuation.current()
    if scope is None:
        raise RuntimeError('mail writer requires internal continuation scope')
    payload = data() if callable(data) else data  # preparation is not pipe waiting
    scope.begin()
    scope.waited = True
    done = threading.Event()
    result: dict[str, str] = {'ack': 'unknown'}

    def worker() -> None:
        try:
            if lock:
                lock.acquire()
            try:
                proc.stdin.write(payload)
                proc.stdin.flush()
                if close_after:
                    proc.stdin.close()
                result['ack'] = 'written'
            finally:
                if lock:
                    lock.release()
        except Exception as exc:
            result['ack'] = 'unknown'
            _log(f'[agent-mail] pipe write failed: {type(exc).__name__}', flush=True)
        finally:
            done.set()  # release a route holding manager.lock before gate work
        if result['ack'] == 'written':
            finish(scope, type('Receipt', (), {'ok': True})())
        else:
            scope.unknown()

    threading.Thread(target=worker, daemon=True, name='mail-followup-stdin').start()
    if not done.wait(ACK_SECONDS):
        scope.unknown()
    elif result['ack'] == 'written':
        scope.accept()
    return dict(result)


def native_write(stream: Any, prompt: str, session: Any, context: str,
                 on_error: Any) -> bool | None:
    """Return None for ordinary writes. close() includes shim delivery proof."""
    scope = session_scope(session)
    if scope is None:
        return None
    try:
        # The native spawn already admitted this exact attempt. A capture
        # stream's close blocks until the shim confirms the actual CLI write.
        if stream is None:
            raise BrokenPipeError('mail prompt stdin unavailable')
        stream.write(prompt)
        stream.close()
    except Exception as exc:
        if on_error is not None:
            on_error(exc)
        scope.unknown()
        return False
    scope.accept()
    if context.strip():
        from mc.resume_context import context_sha, resume_prompt
        if prompt.startswith(context) or prompt.startswith(resume_prompt(context, '')):
            session['_ctx_delivered_sha'] = context_sha(context)
    return True
