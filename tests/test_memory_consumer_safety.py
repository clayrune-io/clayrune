"""MC-1122: exercise the real delivery callers against a mixed-origin vault."""
import pytest


@pytest.fixture()
def vault(tmp_data_dir, tmp_path, monkeypatch):
    import server  # noqa: F401 -- wire blueprints using isolated test paths
    from mc import memory as mem, memory_turn as turn, state
    from mc.blueprints import agent_routes as agents, scheduler_routes as scheduler

    project = {'id': 'railtest', 'project_path': str(tmp_path),
               'steward_objective': 'quartz calibration'}
    mp = tmp_path / 'MEMORY.md'
    mp.write_text(mem._mem_compose('# Index', [], []), encoding='utf-8')
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: mp)
    monkeypatch.setattr(mem, '_get_archive_path', lambda p: tmp_path / 'MEMORY_ARCHIVE.md')
    for origin in ('interactive', 'unattended'):
        (tmp_path / f'{origin}.md').write_text(
            f'---\nname: {origin}\norigin: {origin}\n---\n'
            f'quartz calibration {origin} evidence', encoding='utf-8')
    monkeypatch.setitem(state.CONFIG, 'read_floor_topk', 6)
    monkeypatch.setitem(state.CONFIG, 'read_floor_link_expand', 0)
    monkeypatch.setitem(state.CONFIG, 'memory_turn_refresh_enabled', True)
    monkeypatch.setitem(state.CONFIG, 'memory_turn_cold_probe_enabled', False)
    monkeypatch.setitem(state.CONFIG, 'negation_ledger_enabled', False)
    monkeypatch.setitem(state.CONFIG, 'exploration_readback_enabled', False)
    monkeypatch.setattr(agents, '_allowance_conflict_block', lambda *a: '')
    return project, mem, turn, agents, scheduler


@pytest.mark.parametrize('task,blocked', [
    ('[Steward cycle] quartz calibration', True),
    ('You run unattended: quartz calibration', True),
    ('quartz calibration', False),
])
def test_dispatch_and_seed_apply_identical_origin_gate(vault, task, blocked):
    project, mem, turn, agents, _ = vault
    context = agents._build_agent_context(project, task=task, job_shape='task')
    assert '[interactive.md]' in context
    assert ('[unattended.md]' not in context) == blocked
    session = {'task': task, 'trigger_type': 'schedule' if blocked else 'manual'}
    turn.seed_delivered(project, session, task)
    assert 'interactive.md' in session['_mem_turn_delivered']
    assert ('unattended.md' not in session['_mem_turn_delivered']) == blocked


@pytest.mark.parametrize('task,blocked', [
    ('[Steward cycle] quartz calibration', True),
    ('You run unattended: quartz calibration', True),
    ('quartz calibration', False),
])
def test_seed_does_not_mark_withheld_notes_delivered(vault, task, blocked):
    project, _, turn, _, _ = vault
    session = {'task': task, 'trigger_type': 'manual'}
    turn.seed_delivered(project, session, task)
    assert 'interactive.md' in session['_mem_turn_delivered']
    assert ('unattended.md' not in session['_mem_turn_delivered']) == blocked


@pytest.mark.parametrize('session,blocked', [
    ({'task': '[Steward cycle] unrelated objective', 'trigger_type': 'manual'}, True),
    ({'task': 'unrelated objective', 'trigger_type': 'schedule'}, True),
    ({'task': 'unrelated objective', 'trigger_type': 'dispatch'}, True),
    ({'task': 'unrelated objective', 'trigger_type': 'future-trigger'}, True),
    ({}, True),
    ({'task': 'unrelated objective', 'trigger_type': 'manual'}, False),
])
def test_followup_uses_session_provenance_not_markerless_message(vault, session, blocked):
    project, _, turn, _, _ = vault
    result = turn.refresh_for_turn(project, session, 'quartz calibration')
    assert '[interactive.md]' in result['block']
    assert ('[unattended.md]' not in result['block']) == blocked
    assert ('unattended.md' not in session['_mem_turn_delivered']) == blocked
    second = turn.refresh_for_turn(project, session, 'quartz calibration')
    assert second['block'] == ''
    assert 'interactive.md' in second['suppressed']


def test_steward_charter_withholds_unattended_memory(vault, monkeypatch):
    project, _, _, _, scheduler = vault
    monkeypatch.setattr(scheduler, 'load_project', lambda pid: project)
    monkeypatch.setattr(scheduler, 'save_project', lambda *a: None)
    monkeypatch.setattr(scheduler._steward_core, 'find_charter', lambda p: None)
    task, _ = scheduler._steward_cycle_task(project['id'])
    assert '[interactive.md]' in task
    assert '[unattended.md]' not in task
