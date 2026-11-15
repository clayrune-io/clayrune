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
  For Claude that is the id being left AND every earlier id the session has
  run under (`_session_all_claude_session_ids`: live history plus its agent_log
  rows), so a thread silent across two rollovers is not stranded on a stale id.
  Other providers keep the exact left-id match.

`rebind_held_thread` is the one other entry: a human releasing a hold whose
reason is `conversation_identity_changed` (agent_mail_holds_routes) first moves
THAT thread to the session's current native id, else the release re-blocks.

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
from typing import Any, Iterable

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
           old_native: str | Iterable[str], new_native: str) -> int:
    """Move live continuation snapshots from `old_native` to `new_native`.

    `old_native` is one id or a collection of ids this session has run under.
    Only a snapshot whose project, Clayrune session and provider equal the
    arguments and whose native id is one of them is touched, and only its
    `native_id`. Events are not touched (a blocked event stays blocked until
    `release_hold`). Returns the number of snapshots rebound.
    """
    olds = {old_native} if isinstance(old_native, str) else set(old_native)
    olds.discard(new_native)
    olds.discard('')
    if not (project_id and session_id and new_native and olds):
        return 0
    changed = 0
    with store._tx() as db:
        rows = db.execute(
            'SELECT c.thread_hash,c.snapshot FROM mail_continuations c JOIN thread_bindings b '
            'ON b.thread_hash=c.thread_hash WHERE b.project_id=? AND b.session_id=? '
            'AND b.revoked_at IS NULL', (project_id, session_id)).fetchall()
        for row in rows:
            snap = json.loads(row['snapshot'])
            if (snap.get('project_id'), snap.get('session_id'), snap.get('provider')) != (
                    project_id, session_id, provider) or snap.get('native_id') not in olds:
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
             f'{len(olds)} prior id(s) -> {new_native[:12]} ({changed} thread(s))', flush=True)
    return changed


def rebind_thread(store: Any, thread_hash: str, project_id: str, session_id: str,
                  provider: str, new_native: str) -> bool:
    """Point ONE thread's snapshot at `new_native`; True when it ends up there.

    Same scope as `rebind` (live binding, same project, Clayrune session and
    provider) but for a single thread and any pinned id: the caller is a human
    who has just chosen to release that thread's held reply.
    """
    if not (thread_hash and project_id and session_id and new_native):
        return False
    with store._tx() as db:
        row = db.execute(
            'SELECT c.snapshot FROM mail_continuations c JOIN thread_bindings b '
            'ON b.thread_hash=c.thread_hash WHERE c.thread_hash=? AND b.project_id=? '
            'AND b.session_id=? AND b.revoked_at IS NULL', (thread_hash, project_id, session_id)).fetchone()
        if row is None:
            return False
        snap = json.loads(row['snapshot'])
        if (snap.get('project_id'), snap.get('session_id'), snap.get('provider')) != (
                project_id, session_id, provider):
            return False
        if snap.get('native_id') == new_native:
            return True
        snap['native_id'] = new_native
        changed = db.execute(
            'UPDATE mail_continuations SET snapshot=? WHERE thread_hash=? AND snapshot=?',
            (json.dumps(snap, sort_keys=True, separators=(',', ':')),
             thread_hash, row['snapshot'])).rowcount
    if changed:
        _log(f'[agent-mail] {REASON}: released hold, project {project_id} session {session_id} '
             f'{provider} -> {new_native[:12]} (1 thread)', flush=True)
    return bool(changed)


def rebind_held_thread(store: Any, event: dict[str, Any]) -> bool:
    """Rebind the thread of a held event to its Clayrune session's current native id.

    False (nothing changed) when the session cannot be found, has no native id,
    or the thread's snapshot is for a different provider. Never raises.
    """
    try:
        from mc.blueprints import agent_routes as ar
        pid, sid = event['project_id'], event['session_id']
        source = ar.agent_sessions.get(sid)
        if source is None or source.get('project_id') != pid:
            source = next((r for r in ar._load_agent_log(pid) if r.get('session_id') == sid), None)
        if source is None:
            return False
        return rebind_thread(store, event['thread_hash'], pid, sid,
                             source.get('provider') or 'claude', native(source))
    except Exception as exc:
        _log(f'[agent-mail] {REASON} for released hold failed: {type(exc).__name__}', flush=True)
        return False


def _prior_natives(project_id: str, session: dict[str, Any], provider: str, left: str) -> set[str]:
    """Every native id this Clayrune session has run under that a snapshot may still pin."""
    if provider != 'claude':
        return {left}
    from mc.blueprints import agent_routes as ar
    return {left, *ar._session_all_claude_session_ids(project_id, session)}


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
        if store is None:
            return 0
        return rebind(store, key[0], key[1], provider, _prior_natives(key[0], session, provider, held[1]), new)
    except Exception as exc:   # StoreHeld, sqlite, bad JSON: the stream reader must go on
        _log(f'[agent-mail] {REASON} failed, thread stays held: '
             f'{getattr(exc, "reason", type(exc).__name__)}', flush=True)
        return 0
