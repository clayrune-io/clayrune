"""Startup composition tests for the default-off lifecycle service."""
from pathlib import Path

import pytest

from mc import execution_lifecycle as lifecycle
from mc.runtime_attempt_owner import DispatchFacts
from mc.runtime_lifecycle_service import RuntimeLifecycleService
from tests.test_runtime_lifecycle_bridge import env  # noqa: F401


def _facts(tmp_path, *, session='mc1', dispatch='turn1', resume=''):
    return DispatchFacts(
        project_id='p', project_path=str(tmp_path), mc_session_id=session,
        provider='codex', model='', effort='', resume_id=resume, task='task',
        incognito=False, dispatch_id=dispatch,
        provenance={'trigger_type': 'manual', 'trigger_id': '', 'source': ''})


def test_disabled_service_is_inert_and_shutdown_fences_enabled(tmp_path):
    db = (tmp_path / 'state.sqlite').resolve()
    disabled = RuntimeLifecycleService(
        db_path=db, enabled=False, owner_id='boot', authorize=lambda facts: None,
        source_format=lambda facts: 'codex-rollout-jsonl-0.153')
    assert disabled.bridge_factory(_facts(tmp_path)) is None
    assert not db.exists()

    enabled = RuntimeLifecycleService(
        db_path=db, enabled=True, owner_id='boot', authorize=lambda facts: None,
        source_format=lambda facts: 'codex-rollout-jsonl-0.153')
    enabled.stop()
    with pytest.raises(RuntimeError, match='shutdown admission'):
        enabled.bridge_factory(_facts(tmp_path))
    assert not db.exists()


def test_service_uses_one_store_and_distinct_turn_incarnations(tmp_path):
    db = (tmp_path / 'state.sqlite').resolve()
    service = RuntimeLifecycleService(
        db_path=db, enabled=True, owner_id='boot', authorize=lambda facts: None,
        source_format=lambda facts: 'codex-rollout-jsonl-0.153')
    first = service.bridge_factory(_facts(tmp_path, dispatch='turn1'))
    second = service.bridge_factory(_facts(tmp_path, session='mc2', dispatch='turn2'))
    assert first.owner.store is second.owner.store
    assert first.launch_facts['source_incarnation'] == 'turn1'
    assert second.launch_facts['source_incarnation'] == 'turn2'
    assert first.launch_facts['source_id'] != second.launch_facts['source_id']


def test_agent_internal_uses_injected_service_but_default_off_stays_inert(env, monkeypatch):
    ar, runtime, sessions, tmp = env
    disabled_path = (tmp / 'disabled.sqlite').resolve()
    monkeypatch.setattr(ar, '_runtime_lifecycle_service', RuntimeLifecycleService(
        db_path=disabled_path, enabled=False, owner_id='boot',
        authorize=lambda facts: None,
        source_format=lambda facts: 'codex-rollout-jsonl-0.153'))
    sid = ar._dispatch_agent_internal('p', 'first', provider_override='codex')
    assert sid in sessions and not disabled_path.exists()

    enabled_path = (tmp / 'enabled.sqlite').resolve()
    monkeypatch.setattr(ar, '_runtime_lifecycle_service', RuntimeLifecycleService(
        db_path=enabled_path, enabled=True, owner_id='boot',
        authorize=lambda facts: None,
        source_format=lambda facts: 'codex-rollout-jsonl-0.153'))
    second = ar._dispatch_agent_internal(
        'p', 'continued', provider_override='codex', resume_id='native-prior')
    assert second in sessions and enabled_path.exists()
    assert runtime.calls[-1]['resume_id'] == 'native-prior'


def test_service_rejects_relative_path(tmp_path):
    with pytest.raises(ValueError, match='absolute'):
        RuntimeLifecycleService(
            db_path=Path('relative.sqlite'), enabled=False, owner_id='boot',
            authorize=lambda facts: None,
            source_format=lambda facts: 'codex-rollout-jsonl-0.153')


def test_revoke_conversations_fences_existing_aliases_without_creating_missing(tmp_path):
    db = (tmp_path / 'state.sqlite').resolve()
    service = RuntimeLifecycleService(
        db_path=db, enabled=True, owner_id='boot', authorize=lambda facts: None,
        source_format=lambda facts: 'codex-rollout-jsonl-0.153')
    first_facts = _facts(tmp_path, session='mc1')
    second_facts = _facts(tmp_path, session='mc2', dispatch='turn2')
    first = service.bridge_factory(first_facts)
    second = service.bridge_factory(second_facts)
    first.prepare(first_facts)
    second.prepare(second_facts)

    assert service.revoke_conversations('p', {'missing', 'mc1'}) == ('mc1',)
    assert first.owner.store.lifecycle_state('p', 'mc1').deleted is True
    assert first.owner.store.lifecycle_state('p', 'mc2').deleted is False
    assert service.revoke_conversations('p', {'mc1'}) == ('mc1',)


def test_revoke_project_fences_all_known_project_conversations_only(tmp_path):
    db = (tmp_path / 'state.sqlite').resolve()
    service = RuntimeLifecycleService(
        db_path=db, enabled=True, owner_id='boot', authorize=lambda facts: None,
        source_format=lambda facts: 'codex-rollout-jsonl-0.153')
    one_facts = _facts(tmp_path, session='mc1')
    two_facts = _facts(tmp_path, session='mc2', dispatch='turn2')
    one = service.bridge_factory(one_facts)
    two = service.bridge_factory(two_facts)
    other_facts = DispatchFacts(
        project_id='other', project_path=str(tmp_path), mc_session_id='mc3',
        provider='codex', model='', effort='', resume_id='', task='task',
        incognito=False, dispatch_id='turn3', provenance={})
    other = service.bridge_factory(other_facts)
    one.prepare(one_facts); two.prepare(two_facts); other.prepare(other_facts)

    assert service.revoke_project('p') == ('mc1', 'mc2')
    assert one.owner.store.lifecycle_state('p', 'mc1').deleted is True
    assert one.owner.store.lifecycle_state('p', 'mc2').deleted is True
    assert one.owner.store.lifecycle_state('other', 'mc3').deleted is False


def test_disabled_revocation_remains_inert(tmp_path):
    db = (tmp_path / 'state.sqlite').resolve()
    service = RuntimeLifecycleService(
        db_path=db, enabled=False, owner_id='boot', authorize=lambda facts: None,
        source_format=lambda facts: 'codex-rollout-jsonl-0.153')
    assert service.revoke_conversations('p', {'mc1'}) == ()
    assert service.revoke_project('p') == ()
    assert not db.exists()


def test_revocation_blocks_a_prepared_late_launch_before_spawn(tmp_path):
    db = (tmp_path / 'state.sqlite').resolve()
    service = RuntimeLifecycleService(
        db_path=db, enabled=True, owner_id='boot', authorize=lambda facts: None,
        source_format=lambda facts: 'codex-rollout-jsonl-0.153')
    facts = _facts(tmp_path, session='mc1')
    bridge = service.bridge_factory(facts)
    bridge.prepare(facts)
    service.revoke_conversations('p', {'mc1'})
    spawned = []

    with pytest.raises(lifecycle.LifecycleConflict, match='unavailable'):
        bridge.launch(lambda: spawned.append(True))
    assert spawned == []
