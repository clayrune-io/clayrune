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

    def bridge_factory(self, facts: DispatchFacts, *, project_generation: int | None = None):
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
        store.ensure_project_generation_schema()
        # A deleted project is not reopened by a late callback.  Callers that
        # carry the generation observed at dispatch must also match the
        # current generation; new callers may omit it for first-open projects.
        store.project_generation(facts.project_id, include_deleted=False)
        if project_generation is not None:
            store.assert_project_generation(facts.project_id, project_generation)
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

    def read_store(self) -> ConversationStore | None:
        """Return an already-configured store for injected read consumers.

        Disabled services stay inert, and an enabled service only opens an
        existing database.  Read-only consumers therefore cannot create a
        lifecycle database merely by rendering history.
        """
        return self._existing_store()

    def _mutation_store(self) -> ConversationStore | None:
        """Return the store for explicit project lifecycle mutations.

        A project deletion may arrive before this service has seen a
        conversation.  Enabled revocation therefore creates the canonical
        store so that an unseen callback is fenced durably; disabled services
        remain completely inert.
        """
        with self._lock:
            if not self.enabled:
                return None
            if self._shutdown:
                raise RuntimeError('runtime lifecycle shutdown admission is closed')
            if self._store is None:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                self._store = ConversationStore(self.db_path)
            self._store.ensure_project_generation_schema()
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
        store = self._mutation_store()
        if store is None:
            return ()
        with self._lock:
            return store.revoke_project(project_id)

    def project_generation(self, project_id: str, *, include_deleted: bool = True) -> int:
        """Return the canonical generation without opening a disabled store."""
        store = self._existing_store()
        if store is None:
            return 1
        return store.project_generation(project_id, include_deleted=include_deleted)

    def assert_project_generation(self, project_id: str, generation: int) -> int:
        """Authorize a caller against the canonical project generation.

        A missing store is the untouched generation-one state. It may accept
        generation one without creating or inspecting a database, but it must
        reject any higher generation because that identity cannot be proven.
        """
        if type(generation) is not int or generation < 1:
            raise ValueError('project_generation must be a positive integer')
        store = self._existing_store()
        if store is None:
            if generation != 1:
                raise ConversationUnavailable('project generation is unavailable')
            return 1
        store.assert_project_generation(project_id, generation)
        return generation

    def recreate_project(self, project_id: str) -> int:
        """Explicitly reopen a deleted project identity at a new generation."""
        store = self._mutation_store()
        if store is None:
            return 1
        with self._lock:
            return store.recreate_project(project_id)

    def stop(self) -> None:
        with self._lock:
            self._shutdown = True
