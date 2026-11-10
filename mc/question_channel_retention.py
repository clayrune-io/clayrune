"""Joint, bounded retirement of the question channel's volatile caches (MC-1166 7d-1).

``question_channel`` keeps three process-lifetime caches per question id:
``_selection_locks`` (a non-reentrant per-qid Lock), ``_delivered`` (dedupe) and
``_outbox`` (legacy IMAP match + mail timers). They used to grow forever. This
module retires them TOGETHER, per qid group (the qid itself and every
``qid:<revision>`` key), and only when nothing can still reach them:

* no notification holds or awaits the selection lock (``acquire``/``release``
  bracket the WHOLE notification, so the window between "selection released" and
  "_outbox/timer published" is covered; a waiter's lock object is never replaced),
* no grace timer is armed for any key of the group,
* no reply handler is mid-flight,
* the session no longer has that question pending (a pending form is the
  deduplication obligation: retiring ``_delivered`` early would re-notify).

Durable ownership is NOT touched here. The channel receipt in SQLite is the
compact owner tombstone; it is released only by the destructive session/project
deletion path (``mc.agent_mail_deletion``). A retired cache is therefore
recoverable: ``mc.agent_mail_ownership.recover`` restores the owner before any
channel is chosen.

This module also owns the same-thread re-entry guard: a factory/lookup that calls
``on_question_raised`` for the qid already being selected on this thread would
self-deadlock on the non-reentrant lock. Such a call is deferred and replayed
after the selection lock has been released, never executed synchronously.

Lock order: ``question_channel._lock`` -> ``retention._mu``. ``release``,
``timer_*`` and ``reader`` take only ``_mu``. No I/O runs under either.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Callable, Generator

_mu = threading.Lock()
_refs: dict[str, int] = {}
_timers: dict[str, int] = {}
_sessions: dict[str, str] = {}
_readers = 0
_local = threading.local()

DEFAULT_LIMIT = 200
SessionSource = Callable[[str], 'dict[str, Any] | None']


def _selecting() -> set[str]:
    if not hasattr(_local, 'selecting'):
        _local.selecting = set()
        _local.deferred = []
    return _local.selecting


# --- selection users (called by question_channel.on_question_raised) -------
def reentrant(qid: str) -> bool:
    """True when this thread is already inside a notification for ``qid``."""
    return qid in _selecting()


def defer(session: dict) -> None:
    _selecting()
    _local.deferred.append(session)


def acquire(qid: str, session_id: str) -> None:
    """Register a user/waiter of ``qid``. Caller holds question_channel._lock."""
    with _mu:
        _refs[qid] = _refs.get(qid, 0) + 1
        if session_id:
            _sessions[qid] = session_id
    _selecting().add(qid)


def release(qid: str) -> None:
    with _mu:
        left = _refs.get(qid, 0) - 1
        if left > 0:
            _refs[qid] = left
        else:
            _refs.pop(qid, None)
    _selecting().discard(qid)


def drain(notify: Callable[[dict], None]) -> None:
    """Replay notifications deferred by the re-entry guard, now that no
    selection lock is held by this thread. Bounded: a replay that defers again
    only runs if it is no longer re-entrant, so this cannot loop."""
    selecting = _selecting()
    if selecting or not _local.deferred:
        return
    pending, _local.deferred = _local.deferred, []
    for session in pending:
        notify(session)


# --- timers and reply readers ----------------------------------------------
def timer_started(key: str) -> None:
    with _mu:
        _timers[key] = _timers.get(key, 0) + 1


def timer_done(key: str) -> None:
    with _mu:
        left = _timers.get(key, 0) - 1
        if left > 0:
            _timers[key] = left
        else:
            _timers.pop(key, None)


@contextmanager
def reader() -> Generator[None]:
    """Bracket an inbound-reply handler so retirement never runs under it."""
    global _readers
    with _mu:
        _readers += 1
    try:
        yield
    finally:
        with _mu:
            _readers -= 1


# --- retirement ---------------------------------------------------------------
def _group(key: str) -> str:
    return key.split(':', 1)[0]


def _default_source(session_id: str) -> dict[str, Any] | None:
    from mc.blueprints import agent_routes
    return agent_routes.agent_sessions.get(session_id)


def _pending(session: dict[str, Any] | None, qid: str) -> bool:
    if session is None:
        return False
    return any(isinstance(q, dict) and q.get('question_id') == qid
               for q in (session.get('pending_questions') or []))


def retire(*, limit: int = DEFAULT_LIMIT, source: SessionSource | None = None) -> dict[str, int]:
    """Retire up to ``limit`` fully idle qid groups from all three caches at once.

    Returns {'retired': n, 'kept': n}. Never raises for a session-source error:
    an unreadable session keeps its group (conservative)."""
    from mc import question_channel as qc
    lookup = source or _default_source
    retired = kept = 0
    with qc._lock:
        with _mu:
            if _readers:
                return {'retired': 0, 'kept': len(qc._selection_locks)}
            groups = ({_group(k) for k in qc._delivered} | {_group(k) for k in qc._outbox}
                      | set(qc._selection_locks))
            for qid in sorted(groups):
                if retired >= limit:
                    break
                keys = [k for k in (*qc._delivered, *qc._outbox) if _group(k) == qid]
                if _refs.get(qid) or any(_group(k) == qid for k in _timers):
                    kept += 1
                    continue
                session_id = _sessions.get(qid) or next(
                    (m.get('session_id') for k, m in qc._outbox.items() if _group(k) == qid
                     and m.get('session_id')), '')
                if not session_id and keys:
                    kept += 1  # no way to prove the form is gone
                    continue
                try:
                    live = bool(session_id) and _pending(lookup(session_id), qid)
                except Exception:
                    live = True
                if live:
                    kept += 1
                    continue
                for k in keys:
                    qc._delivered.discard(k)
                    qc._outbox.pop(k, None)
                qc._selection_locks.pop(qid, None)
                _sessions.pop(qid, None)
                retired += 1
    return {'retired': retired, 'kept': kept}


def snapshot() -> dict[str, int]:
    """Bounded metadata for status surfaces; counts only."""
    from mc import question_channel as qc
    with qc._lock:
        with _mu:
            return {'selection_locks': len(qc._selection_locks), 'delivered': len(qc._delivered),
                    'outbox': len(qc._outbox), 'users': sum(_refs.values()),
                    'timers': sum(_timers.values()), 'readers': _readers}
