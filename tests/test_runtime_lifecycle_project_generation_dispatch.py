"""Generation propagation tests for provider-neutral dispatch and revival."""

from pathlib import Path

import pytest

from mc.runtime_lifecycle_service import RuntimeLifecycleService
from tests.test_runtime_lifecycle_bridge import env  # noqa: F401


def _service(tmp_path: Path) -> RuntimeLifecycleService:
    return RuntimeLifecycleService(
        db_path=(tmp_path / 'lifecycle.sqlite').resolve(), enabled=True,
        owner_id='test-owner', authorize=lambda facts: None,
        source_format=lambda facts: 'codex-rollout-jsonl-0.153')


def test_runtime_dispatch_passes_canonical_generation_to_factory(env):
    ar, runtime, sessions, tmp = env
    seen = []

    def factory(facts, *, project_generation):
        seen.append((facts.mc_session_id, project_generation))
        return None

    sid = ar._dispatch_via_runtime(
        {'id': 'p', 'project_path': str(tmp)}, 'task', provider_name='codex',
        project_generation=7, lifecycle_bridge_factory=factory)

    assert seen == [(sid, 7)]
    assert sessions[sid]['project_generation'] == 7
    assert runtime.calls[0]['session_dict']['project_generation'] == 7


def test_dispatch_rejects_stale_generation_and_accepts_recreated_generation(env, monkeypatch):
    ar, runtime, sessions, tmp = env
    service = _service(tmp)
    monkeypatch.setattr(ar, '_runtime_lifecycle_service', service)

    # Opening the canonical service creates generation one; deletion and
    # recreation advances it to three without changing the project id.
    service.project_generation('p', include_deleted=False)
    assert service.revoke_project('p') == ()
    assert service.recreate_project('p') == 3

    with pytest.raises(Exception, match='generation'):
        ar._dispatch_agent_internal(
            'p', 'stale', provider_override='codex', project_generation=1)
    sid = ar._dispatch_agent_internal(
        'p', 'current', provider_override='codex', project_generation=3)
    assert sessions[sid]['project_generation'] == 3
    assert runtime.calls[-1]['session_dict']['project_generation'] == 3


def test_non_claude_revival_fences_old_log_and_forwards_current_generation(env, monkeypatch):
    ar, runtime, sessions, tmp = env
    service = _service(tmp)
    monkeypatch.setattr(ar, '_runtime_lifecycle_service', service)
    service.project_generation('p', include_deleted=False)
    assert service.revoke_project('p') == ()
    assert service.recreate_project('p') == 3

    common = {
        'session_id': 'mc-session', 'ts': '2026-01-01T00:00:00Z',
        'provider': 'codex', 'provider_session_id': 'native-thread',
        'status': 'completed', 'agent_model': '', 'pinned_model': '',
        'requested_effort': '', 'character': None, 'source': 'agent',
    }
    calls = []
    monkeypatch.setattr(ar, '_dispatch_agent_internal',
                        lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(ar, '_load_agent_log', lambda project_id: [
        {**common, 'project_generation': 1}])
    assert ar._revive_non_claude_from_agent_log(
        'p', 'mc-session', 'stale', {'project_path': str(tmp)}) is None
    assert calls == []

    monkeypatch.setattr(ar, '_load_agent_log', lambda project_id: [
        {**common, 'project_generation': 3}])
    assert ar._revive_non_claude_from_agent_log(
        'p', 'mc-session', 'current', {'project_path': str(tmp)}) == 'mc-session'
    assert calls[0][1]['project_generation'] == 3
    assert calls[0][1]['resume_id'] == 'native-thread'


def test_pending_log_preserves_canonical_generation(monkeypatch):
    import mc.blueprints.agent_routes as ar

    captured = []
    def update(project_id, mutate):
        log = []
        mutate(log)
        captured.extend(log)
    monkeypatch.setattr(ar, '_update_agent_log', update)
    ar._log_agent_dispatch_pending({
        'project_id': 'p', 'session_id': 'mc-session', 'task': 'task',
        'provider': 'codex', 'project_generation': 4,
    })
    assert captured and captured[0]['project_generation'] == 4


def test_completion_log_preserves_canonical_generation(monkeypatch):
    import mc.blueprints.agent_routes as ar

    captured = []
    def update(project_id, mutate):
        rows = []
        mutate(rows)
        captured.extend(rows)
    monkeypatch.setattr(ar, '_update_agent_log', update)
    monkeypatch.setattr(ar, 'load_project', lambda project_id: None)
    ar._log_agent_completion_body({
        'project_id': 'p', 'session_id': 'mc-session', 'task': 'task',
        'status': 'completed', 'provider': 'codex',
        'project_generation': 5, 'log_lines': ['answer'],
    })
    assert captured and captured[0]['project_generation'] == 5
