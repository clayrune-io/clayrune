"""Project-generation and tombstone fencing for canonical lifecycle state."""
from pathlib import Path
import sqlite3

import pytest

from mc import execution_lifecycle as lifecycle
from mc.conversation_store import ConversationStore, ConversationUnavailable, SchemaError
from mc.runtime_attempt_owner import DispatchFacts
from mc.runtime_lifecycle_service import RuntimeLifecycleService


def _facts(tmp_path: Path, *, session: str, dispatch: str) -> DispatchFacts:
    return DispatchFacts(
        project_id='p', project_path=str(tmp_path), mc_session_id=session,
        provider='codex', model='', effort='', resume_id='', task='task',
        incognito=False, dispatch_id=dispatch,
        provenance={'trigger_type': 'manual', 'trigger_id': '', 'source': ''})


def _service(tmp_path: Path) -> RuntimeLifecycleService:
    return RuntimeLifecycleService(
        db_path=(tmp_path / 'state.sqlite').resolve(), enabled=True,
        owner_id='boot', authorize=lambda facts: None,
        source_format=lambda facts: 'codex-rollout-jsonl-0.153')


def test_delete_recreate_rejects_old_generation_and_allows_new_dispatch(tmp_path):
    service = _service(tmp_path)
    old_facts = _facts(tmp_path, session='old-session', dispatch='old-turn')
    old_bridge = service.bridge_factory(old_facts, project_generation=1)
    old_bridge.prepare(old_facts)
    assert service.project_generation('p') == 1

    assert service.revoke_project('p') == ('old-session',)
    assert service.project_generation('p') == 2
    with pytest.raises(ConversationUnavailable, match='deleted'):
        service.project_generation('p', include_deleted=False)

    assert service.recreate_project('p') == 3
    with pytest.raises(ConversationUnavailable, match='stale'):
        service.bridge_factory(old_facts, project_generation=1)

    new_facts = _facts(tmp_path, session='new-session', dispatch='new-turn')
    new_bridge = service.bridge_factory(new_facts, project_generation=3)
    new_bridge.prepare(new_facts)
    assert new_bridge.owner.store.lifecycle_state('p', 'new-session').deleted is False


def test_unseen_old_callback_requires_matching_generation_after_recreate(tmp_path):
    service = _service(tmp_path)
    assert service.revoke_project('p') == ()
    assert service.recreate_project('p') == 3

    old_facts = _facts(tmp_path, session='never-recorded', dispatch='old-turn')
    with pytest.raises(ConversationUnavailable, match='stale'):
        service.bridge_factory(old_facts, project_generation=1)

    current = _facts(tmp_path, session='new-session', dispatch='new-turn')
    bridge = service.bridge_factory(current, project_generation=3)
    bridge.prepare(current)


def test_generation_and_tombstone_survive_store_reopen(tmp_path):
    service = _service(tmp_path)
    facts = _facts(tmp_path, session='c', dispatch='turn')
    bridge = service.bridge_factory(facts, project_generation=1)
    bridge.prepare(facts)
    service.revoke_project('p')
    service.recreate_project('p')

    reopened = ConversationStore((tmp_path / 'state.sqlite').resolve())
    assert reopened.project_generation('p') == 3
    with pytest.raises(ConversationUnavailable, match='stale'):
        reopened.assert_project_generation('p', 1)
    reopened.assert_project_generation('p', 3)
    assert reopened.lifecycle_state('p', 'c').deleted is True


def test_old_v4_database_requires_explicit_project_generation_migration(tmp_path):
    store = ConversationStore((tmp_path / 'state.sqlite').resolve())
    store.create_lifecycle_conversation('p', 'c', engine={'provider': 'codex'}, event_id='create')
    with sqlite3.connect(store.db_path) as db:
        db.execute('DROP TABLE lifecycle_projects')

    with pytest.raises(SchemaError, match='project-generation'):
        store.project_generation('p')
    backup = tmp_path / 'v4-backup.sqlite'
    store.migrate_schema4(backup_path=backup)
    with sqlite3.connect(backup) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='lifecycle_projects'").fetchone()
        assert db.execute('PRAGMA user_version').fetchone()[0] == 4
    assert store.project_generation('p') == 1


def test_project_generation_migration_refuses_existing_backup_and_is_not_implicit(tmp_path):
    store = ConversationStore((tmp_path / 'state.sqlite').resolve())
    store.create_lifecycle_conversation('p', 'c', engine={'provider': 'codex'}, event_id='create')
    with sqlite3.connect(store.db_path) as db:
        db.execute('DROP TABLE lifecycle_projects')
    backup = tmp_path / 'existing.sqlite'
    backup.write_bytes(b'keep')
    with pytest.raises(ValueError):
        store.migrate_project_generation(backup_path=backup)
    assert backup.read_bytes() == b'keep'


def test_project_generation_migration_rejects_malformed_table_before_backup(tmp_path):
    store = ConversationStore((tmp_path / 'state.sqlite').resolve())
    store.create_lifecycle_conversation('p', 'c', engine={'provider': 'codex'}, event_id='create')
    with sqlite3.connect(store.db_path) as db:
        db.execute('DROP TABLE lifecycle_projects')
        db.execute('CREATE TABLE lifecycle_projects (project_id TEXT PRIMARY KEY, generation INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, extra TEXT)')
    with pytest.raises(SchemaError, match='project-generation shape'):
        store.ensure_project_generation_schema()
    assert not (tmp_path / 'backup.sqlite').exists()


def test_project_revoke_rolls_back_generation_and_rows_on_event_failure(tmp_path, monkeypatch):
    service = _service(tmp_path)
    facts = _facts(tmp_path, session='c', dispatch='turn')
    bridge = service.bridge_factory(facts, project_generation=1)
    bridge.prepare(facts)
    original = bridge.owner.store._lifecycle_event
    monkeypatch.setattr(bridge.owner.store, '_lifecycle_event',
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('event fault')))
    with pytest.raises(RuntimeError, match='event fault'):
        service.revoke_project('p')
    monkeypatch.setattr(bridge.owner.store, '_lifecycle_event', original)
    assert service.project_generation('p') == 1
    assert bridge.owner.store.lifecycle_state('p', 'c').deleted is False


def test_disabled_service_generation_apis_do_not_create_database(tmp_path):
    service = RuntimeLifecycleService(
        db_path=(tmp_path / 'disabled.sqlite').resolve(), enabled=False,
        owner_id='boot', authorize=lambda facts: None,
        source_format=lambda facts: 'unused')
    assert service.project_generation('p') == 1
    assert service.recreate_project('p') == 1
    assert service.revoke_project('p') == ()
    assert not (tmp_path / 'disabled.sqlite').exists()
