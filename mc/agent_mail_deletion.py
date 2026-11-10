"""Session/project deletion hooks for agent mail (MC-1166 unit 7d-1).

Called by the destructive session and project routes BEFORE they remove anything.
Each hook, in ONE store transaction, revokes thread bindings, cancels queued
events, revokes unsent outbound intents and releases the compact question-channel
owner tombstones for exactly that session/project. This is the only path that
releases a tombstone (``Outbound.cleanup`` never ages one out).

It never touches the installation or its generation, so unrelated projects that
share one install are unaffected; a late callback keeps positive evidence but
every binding it could land on is already revoked.

If the store cannot take the write (held, locked, failed) the intent is first
persisted next to the store marker (outside the repo, never ``data/projects``)
and replayed by recovery; if even that cannot be persisted, ``DeletionRefused``
is raised and the route must refuse the destructive step. A store that was never
created has nothing to revoke and is not an error.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Iterable

from mc.agent_mail_store import MailStore
from mc.atomic_json import write_json_atomic
from mc.core import _log

INTENTS_NAME = 'deletion-intents.json'
_intent_lock = threading.Lock()
_OPEN_OUTBOUND = ('draft', 'ready', 'retryable')


class DeletionRefused(Exception):
    """Revocation could be neither applied nor durably recorded. ``reason`` is a
    closed code; the caller must not perform the destructive step."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def session_aliases(session_id: str, session: dict[str, Any] | None = None) -> list[str]:
    """Exact ids a session is known by, including when the runtime row is gone."""
    found = [session_id]
    if isinstance(session, dict):
        found += [session.get(k) for k in ('session_id', 'id', 'claude_session_id')]
    return sorted({v for v in found if isinstance(v, str) and v})


def _has(db: Any, table: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _revoke(store: MailStore, project_id: str, session_ids: list[str] | None) -> None:
    """One transaction. ``session_ids`` None means the whole project."""
    scope, args = ('project_id=?', (project_id,))
    if session_ids is not None:
        marks = ','.join('?' * len(session_ids))
        scope, args = (f'project_id=? AND session_id IN ({marks})', (project_id, *session_ids))
    with store._tx() as db:
        now = store.clock()
        db.execute(f'UPDATE thread_bindings SET revoked_at=? WHERE {scope} AND revoked_at IS NULL',
                   (now, *args))
        store._cancel(db, scope, args, 'session_revoked' if session_ids is not None else 'project_revoked')
        if _has(db, 'mail_outbound_intents'):
            marks = ','.join('?' * len(_OPEN_OUTBOUND))
            db.execute("UPDATE mail_outbound_intents SET payload=NULL,state='revoked',reason='consent_required',"
                       f'updated=? WHERE {scope} AND state IN ({marks})', (now, *args, *_OPEN_OUTBOUND))
        if _has(db, 'mail_outbound_question_channels'):
            db.execute(f'DELETE FROM mail_outbound_question_channels WHERE {scope}', args)


def _intents_path(factory: Any) -> Any:
    return factory.paths.root / INTENTS_NAME


def _read_intents(factory: Any) -> list[dict[str, Any]]:
    path = _intents_path(factory)
    if not path.exists():
        return []
    value = json.loads(path.read_text(encoding='utf-8'))
    if type(value) is not list:
        raise ValueError('deletion intents corrupt')
    return value


def _record(factory: Any, intent: dict[str, Any]) -> None:
    try:
        with _intent_lock:
            items = _read_intents(factory)
            if intent not in items:
                items.append(intent)
            factory.paths.root.mkdir(parents=True, exist_ok=True)
            write_json_atomic(_intents_path(factory), items)
    except Exception:
        _log('[agent-mail] deletion intent could not be persisted', flush=True)
        raise DeletionRefused('deletion_intent_unwritable') from None


def _apply(factory: Any, project_id: str, session_ids: list[str] | None) -> str:
    """'none' (never enrolled) | 'revoked'. Raises on anything unhealthy."""
    store = factory.open_store()
    if store is None:
        return 'none'
    _revoke(store, project_id, session_ids)
    return 'revoked'


def _run(factory: Any, project_id: str, session_ids: list[str] | None) -> str:
    if factory is None:
        from mc import agent_mail_factory
        factory = agent_mail_factory.get()
    try:
        return _apply(factory, project_id, session_ids)
    except Exception as exc:
        _log(f'[agent-mail] deletion revoke deferred: {getattr(exc, "reason", type(exc).__name__)}',
             flush=True)
        _record(factory, {'project_id': project_id, 'session_ids': session_ids})
        return 'deferred'


def revoke_session(project_id: str, session_ids: Iterable[str], *, factory: Any = None) -> str:
    """Before the session row/history disappears. 'none' | 'revoked' | 'deferred'."""
    ids = sorted({s for s in session_ids if isinstance(s, str) and s})
    if not ids:
        return 'none'
    return _run(factory, project_id, ids)


def revoke_project(project_id: str, *, factory: Any = None) -> str:
    """Dedicated project fence: every binding/queued event/unsent intent/tombstone
    of this project, nothing of the installation or other projects."""
    return _run(factory, project_id, None)


def apply_intents(factory: Any) -> dict[str, int]:
    """Replay persisted deletion intents once the store is healthy (recovery)."""
    with _intent_lock:
        items = _read_intents(factory)
    done = failed = 0
    keep: list[dict[str, Any]] = []
    for item in items:
        try:
            _apply(factory, item['project_id'], item['session_ids'])
            done += 1
        except Exception:
            failed += 1
            keep.append(item)
    if done:
        with _intent_lock:
            current = [i for i in _read_intents(factory) if i not in items or i in keep]
            write_json_atomic(_intents_path(factory), current)
    return {'applied': done, 'pending': failed}
