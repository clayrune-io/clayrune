"""Keep a mail thread bound to its chat across Clayrune's own auto-fresh rollover.

A thread's continuation snapshot (`mail_continuations`) pins the chat's native
conversation id, and `agent_mail_target.validate` holds every reply whose
session now runs under a different one (`conversation_identity_changed`).
Auto-fresh rollover keeps the Clayrune session and replaces its native id, so
without a rebind every reply to a long-lived chat was held after its first
rollover (live 2026-11-14, event 466690871419).

Two steps, because the new native id does not exist when the rollover is
decided:

* `expect` - `_auto_fresh_handoff`, the one place every rollover goes through,
  records (project, session) -> (provider, native id being left).
* `native_learned` - the stream readers that learn the new native id call it;
  if that session has a live expectation, the matching snapshots are rebound.

Server-authored only. Nothing here is reachable from a route or from an agent,
and `validate` is not loosened: a native id change with no rollover behind it
(no expectation, expired expectation, wrong provider, wrong session, wrong old
id) still holds. A restart between the two steps loses the expectation, so the
thread simply stays held - the safe failure.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from mc.agent_mail_target import native
from mc.core import _log

REASON = 'rollover_rebind'
# A rollover whose fresh process never reported an id must not authorise some
# later, unrelated id change; the new id arrives within seconds of the spawn.
PENDING_TTL_SECONDS = 900.0

_lock = threading.Lock()
_pending: dict[tuple[str, str], tuple[str, str, float]] = {}


def expect(project_id: str, session_id: str, provider: str, old_native: str) -> None:
    """Record that this session is leaving `old_native` by an auto-fresh rollover."""
    if not (project_id and session_id and old_native):
        return
    with _lock:
        _pending[project_id, session_id] = (
            provider or 'claude', old_native, time.monotonic() + PENDING_TTL_SECONDS)


def rebind(store: Any, project_id: str, session_id: str, provider: str,
           old_native: str, new_native: str) -> int:
    """Move live continuation snapshots from `old_native` to `new_native`.

    Only a snapshot whose project, Clayrune session, provider and native id all
    equal the arguments is touched, and only its `native_id`. Events are not
    touched (a blocked event stays blocked until `release_hold`). Returns the
    number of snapshots rebound.
    """
    if not (project_id and session_id and old_native and new_native) or old_native == new_native:
        return 0
    changed = 0
    with store._tx() as db:
        rows = db.execute(
            'SELECT c.thread_hash,c.snapshot FROM mail_continuations c JOIN thread_bindings b '
            'ON b.thread_hash=c.thread_hash WHERE b.project_id=? AND b.session_id=? '
            'AND b.revoked_at IS NULL', (project_id, session_id)).fetchall()
        for row in rows:
            snap = json.loads(row['snapshot'])
            if (snap.get('project_id'), snap.get('session_id'), snap.get('provider'),
                    snap.get('native_id')) != (project_id, session_id, provider, old_native):
                continue
            snap['native_id'] = new_native
            # Same encoding as MailWake.bind, so a later bind() of this thread
            # compares equal instead of raising continuation_binding_conflict.
            changed += db.execute(
                'UPDATE mail_continuations SET snapshot=? WHERE thread_hash=? AND snapshot=?',
                (json.dumps(snap, sort_keys=True, separators=(',', ':')),
                 row['thread_hash'], row['snapshot'])).rowcount
    if changed:
        _log(f'[agent-mail] {REASON}: project {project_id} session {session_id} {provider} '
             f'{old_native[:12]} -> {new_native[:12]} ({changed} thread(s))', flush=True)
    return changed


def _store() -> Any:
    from mc import agent_mail_factory
    return agent_mail_factory.get().open_store()


def native_learned(session: dict[str, Any]) -> int:
    """Called when a stream reader has recorded a session's native id. Never raises."""
    try:
        key = (session.get('project_id') or '', session.get('session_id') or '')
        provider = session.get('provider') or 'claude'
        new = native(session)
        with _lock:
            held = _pending.get(key)
            if held is None:
                return 0
            if held[2] <= time.monotonic():
                del _pending[key]
                return 0
            if held[0] != provider or not new or new == held[1]:
                return 0   # not the fresh process's id yet
            del _pending[key]
        store = _store()
        return 0 if store is None else rebind(store, key[0], key[1], provider, held[1], new)
    except Exception as exc:   # StoreHeld, sqlite, bad JSON: the stream reader must go on
        _log(f'[agent-mail] {REASON} failed, thread stays held: '
             f'{getattr(exc, "reason", type(exc).__name__)}', flush=True)
        return 0
