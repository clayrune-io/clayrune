"""Startup-owned composition for provider-neutral runtime lifecycle authority."""
from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Callable
from uuid import uuid4

from mc.conversation_store import ConversationStore, ConversationUnavailable
from mc.runtime_attempt_owner import (
    AuthorizedRuntimeLifecycleBridge, DispatchFacts, RuntimeAttemptOwner,
)


class RuntimeLifecycleService:
    """Own one explicitly located store and mint per-dispatch bridges.

    Disabled services are fully inert: they neither create nor inspect the DB.
    """

    def __init__(self, *, db_path: Path, enabled: bool, owner_id: str,
                 authorize: Callable[[DispatchFacts], None],
                 source_format: Callable[[DispatchFacts], str]) -> None:
        if not isinstance(db_path, Path) or not db_path.is_absolute():
            raise ValueError('absolute lifecycle database path is required')
        if (type(enabled) is not bool or not owner_id or not callable(authorize)
                or not callable(source_format)):
            raise ValueError('valid lifecycle service configuration is required')
        self.db_path = db_path
        self.enabled = enabled
        self.owner_id = owner_id
        self.authorize = authorize
        self.source_format = source_format
        self._lock = RLock()
        self._shutdown = False
        self._store: ConversationStore | None = None

    def bridge_factory(self, facts: DispatchFacts):
        with self._lock:
            if not self.enabled:
                return None
            if self._shutdown:
                raise RuntimeError('runtime lifecycle shutdown admission is closed')
            if facts.incognito:
                return None
            if self._store is None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                self._store = ConversationStore(self.db_path)
            store = self._store
        engine = {
            'provider': facts.provider, 'model': facts.model,
            'effort': facts.effort, 'resume_id': facts.resume_id,
            'settings': dict(facts.provenance),
        }
        import json
        engine_json = json.dumps(engine, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'), allow_nan=False)
        format_version = self.source_format(facts)
        if not isinstance(format_version, str) or not format_version:
            raise ValueError('authorized native source format is required')
        owner = RuntimeAttemptOwner(
            store=store, project_id=facts.project_id,
            conversation_id=facts.mc_session_id, owner_id=self.owner_id)
        return AuthorizedRuntimeLifecycleBridge(
            owner=owner, facts=facts,
            authorize=lambda: self.authorize(facts),
            launch_facts={
                'provider': facts.provider,
                'project_path': facts.project_path,
                'mc_session_id': facts.mc_session_id,
                'requested_engine_json': engine_json,
                'incognito': False,
                'source_id': f'{facts.project_id}:{facts.mc_session_id}',
                'source_incarnation': facts.dispatch_id,
                'format_version': format_version,
            })

    def _existing_store(self) -> ConversationStore | None:
        """Open the configured store only when lifecycle persistence exists."""
        with self._lock:
            if not self.enabled or not self.db_path.exists():
                return None
            if self._store is None:
                self._store = ConversationStore(self.db_path)
            return self._store

    def revoke_conversations(self, project_id: str,
                             conversation_ids: set[str]) -> tuple[str, ...]:
        """Durably privacy-fence existing aliases without creating new state."""
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError('project_id is required')
        if not isinstance(conversation_ids, set) or not all(
                isinstance(value, str) and value.strip() for value in conversation_ids):
            raise ValueError('conversation_ids must be a set of nonempty strings')
        store = self._existing_store()
        if store is None:
            return ()
        revoked = []
        with self._lock:
            for conversation_id in sorted(conversation_ids):
                try:
                    state = store.lifecycle_state(project_id, conversation_id)
                except ConversationUnavailable:
                    continue
                if not state.deleted:
                    state = store.set_lifecycle_deleted(
                        project_id, conversation_id, True,
                        expected_revision=state.revision,
                        event_id=f'privacy-delete:{uuid4().hex}')
                if state.deleted:
                    revoked.append(conversation_id)
        return tuple(revoked)

    def revoke_project(self, project_id: str) -> tuple[str, ...]:
        """Durably privacy-fence every known lifecycle conversation in a project."""
        store = self._existing_store()
        if store is None:
            return ()
        return self.revoke_conversations(
            project_id, set(store.list_lifecycle_conversations(project_id)))

    def stop(self) -> None:
        with self._lock:
            self._shutdown = True
