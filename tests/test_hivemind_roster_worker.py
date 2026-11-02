"""HR-2: recording runtimes, real ordinary context and Claude bridge ownership.

No live roster, model call, or operator project is used. Real git/Claude bridge
tests reuse the existing isolated worktree harness; only process launch is fake.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from mc import agent_runtime, characters, state
from mc import hivemind_worker as worker
from mc.blueprints import agent_routes as ar, hivemind_routes as hm
from tests.test_hivemind_routes import client  # noqa: F401
from tests.test_hivemind_roster import agents_dir, PROVIDERS  # noqa: F401


class RecordingRuntime:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def model_supported(self, model):
        return True

    def model_choices(self):
        return []

    def latest_for(self, tier):
        return ''

    def capabilities(self):
        return agent_runtime.ProviderCapabilities(name=self.name, display_name=self.name)

    def dispatch(self, **kwargs):
        self.calls.append(kwargs)
        kwargs['session_dict']['process_alive'] = True


@pytest.fixture
def rig(client, agents_dir, monkeypatch, tmp_path):
    runtimes = {name: RecordingRuntime(name) for name in PROVIDERS}
    monkeypatch.setattr(hm, '_agent_runtime', agent_runtime)
    for name, runtime in runtimes.items():
        monkeypatch.setitem(agent_runtime._RUNTIMES, name, runtime)
        characters.write_character(
            'global', f'worker-{name}', 'fixture', f'PERSONA-{name}',
            agent_name=f'Worker_{name}', avatar='🛠', skills=['fixture-skill'],
            engine={'provider': name, 'effort': 'high'})
    (client.proj_path / 'AGENT_RULES.md').write_text('PROJECT-RULES', encoding='utf-8')
    shared = tmp_path / 'shared.md'
    shared.write_text('SHARED-RULES', encoding='utf-8')
    monkeypatch.setattr(ar, 'SHARED_RULES_PATH', shared)
    monkeypatch.setattr(ar, '_skills_catalog_block', lambda *a: 'SKILL-CATALOG')
    monkeypatch.setattr(ar, '_clayrune_universal_capabilities', lambda **k: ['UNIVERSAL-ONCE'])
    monkeypatch.setattr(ar, '_clayrune_api_pointer_card', lambda *a: 'POINTER-ONCE')
    monkeypatch.setattr(ar, '_get_memory_path', lambda p: None)
    monkeypatch.setattr(ar, '_get_archive_path', lambda p: tmp_path / 'archive.md')
    monkeypatch.setattr(ar, '_allowance_conflict_block', lambda *a: '')
    monkeypatch.setattr(ar, '_maybe_isolate_worktree', lambda p, sid, **kw: (p['project_path'], False))
    memory_calls, exploration_calls, rows = [], [], []
    monkeypatch.setattr(ar, '_memory_search', lambda *a, **kw: memory_calls.append(kw) or [])
    monkeypatch.setattr(ar._distiller, 'exploration_read_floor',
                        lambda *a, **kw: exploration_calls.append(kw) or [])
    monkeypatch.setitem(state.CONFIG, 'exploration_readback_enabled', True)
    monkeypatch.setattr(ar, '_update_agent_log', lambda pid, fn: fn(rows))
    manifest = {'id': 'roster1', 'project_id': 'thm', 'project_generation': 7,
                'title': 'Roster launch', 'goal': 'Goal',
                'created_by_session_id': 'attributed-parent',
                'config': {'roster_version': 1, 'worker_provider': 'claude',
                           'worker_model': 'sonnet', 'worker_max_turns': 13}}
    ws = {'id': 'ws1', 'title': 'Deliverable', 'description': 'Only assigned files',
          'character': 'global:worker-codex', 'status': 'pending',
          'assignment_attempt': 0}
    hm._hm_ensure_dirs('roster1')
    hm._hm_save_manifest('roster1', manifest)
    hm._hm_save_workstream('roster1', 'ws1', ws)
    hm._hm_write_handoff('roster1', 'ws1', 'PREVIOUS-HANDOFF')
    return SimpleNamespace(client=client, runtimes=runtimes, manifest=manifest,
                           ws=ws, project=client.projects['thm'], rows=rows,
                           memory_calls=memory_calls, exploration_calls=exploration_calls)


def launch(rig, provider='codex'):
    rig.ws['character'] = f'global:worker-{provider}'
    hm._hm_save_workstream('roster1', 'ws1', rig.ws)
    return hm._hm_spawn_worker_session(rig.manifest, rig.ws, rig.project, 'roster1', 'ws1')


@pytest.mark.parametrize('provider', PROVIDERS)
def test_every_provider_gets_ordinary_context_and_pinned_identity(rig, provider):
    sid = launch(rig, provider)
    call, = rig.runtimes[provider].calls
    session = state.agent_sessions[sid]
    prompt = call['system_prompt']
    for text in (f'PERSONA-{provider}', f'Worker_{provider}', 'fixture-skill',
                 'PROJECT-RULES', 'SHARED-RULES', 'SKILL-CATALOG',
                 'YOUR WORKSTREAM: Deliverable', 'Only assigned files', 'PREVIOUS-HANDOFF'):
        assert text in prompt
    assert prompt.count('UNIVERSAL-ONCE') == 1
    assert prompt.count('POINTER-ONCE') == 1
    assert call['mode'] == 'A' and call['housekeeping'] is True
    assert call['model'] == '' and call['effort'] == 'high'
    assert call['max_turns'] == 13
    assert sid.startswith('hm_') and call['mc_session_id'] == sid
    assert call['session_dict'] is session
    assert call['register_process'] is hm._register_process
    if provider == 'claude':
        assert call['callbacks'] == {}
    else:
        assert call['callbacks']['on_process_exit'] is ar._RUNTIME_CALLBACKS['on_process_exit']
    for key in ('character', 'character_ref', 'resolved_assignment', 'assignment_attempt'):
        assert session[key] == call['session_metadata'][key] == rig.rows[0][key]
    assert session['character']['avatar'] == '🛠'
    assert session['provider'] == provider
    assert session['_spawned_by'] == 'attributed-parent'
    assert rig.rows[0]['dispatched_by_session_id'] == 'attributed-parent'
    assert rig.memory_calls and rig.exploration_calls
    assert all(c['consumer_unattended'] is True for c in rig.memory_calls + rig.exploration_calls)
    saved = hm._hm_load_workstream('roster1', 'ws1')
    assert saved['resolved_assignment']['session_id'] == sid
    assert saved['character_snapshot'] == session['character']
    assert 'worker_context' not in saved
    assert worker.read_pinned_prompt(saved | {'hivemind_id': 'roster1'}) == prompt
    assert '_system_prompt' not in rig.rows[0]
    assert worker.read_pinned_prompt(rig.rows[0]) == prompt


def test_human_created_manifest_never_uses_notification_or_body_parent(rig):
    rig.manifest.pop('created_by_session_id')
    rig.manifest.update(notify_session='forged-parent', created_by='forged-parent')
    sid = launch(rig)
    assert state.agent_sessions[sid]['_spawned_by'] == ''
    assert rig.rows[0]['dispatched_by_session_id'] == ''


@pytest.mark.parametrize('provider', [p for p in PROVIDERS if p != 'claude'])
def test_runtime_completion_persists_pins_and_closes_the_pending_log_row(rig, monkeypatch, provider):
    sid = launch(rig, provider)
    session = state.agent_sessions[sid]
    session.update(status='completed', process_alive=False, provider_session_id='native-id')
    session['log_lines'].append('Completed the workstream.')
    monkeypatch.setattr(ar, 'get_manager', hm.get_manager)
    callback = rig.runtimes[provider].calls[-1]['callbacks']['on_process_exit']
    callback(None, session)
    row, = rig.rows
    assert row['status'] == 'completed'
    assert row['provider_session_id'] == 'native-id'
    assert row['project_generation'] == 7
    assert row['dispatched_by_session_id'] == 'attributed-parent'
    for key in worker.attempt_metadata(session):
        assert row[key] == session[key]


@pytest.mark.parametrize('failure', ['log', 'save', 'generation', 'missing_generation',
                                   'runtime', 'character', 'context', 'isolation', 'base',
                                   'prompt_write', 'prompt_sync'])
def test_prelaunch_failure_starts_nothing(rig, monkeypatch, agents_dir, failure):
    def refuse(*a, **k):
        raise RuntimeError('injected refusal')
    if failure == 'log':
        monkeypatch.setattr(ar, '_update_agent_log', refuse)
    elif failure == 'save':
        monkeypatch.setattr(hm, '_hm_save_workstream', refuse)
    elif failure == 'generation':
        monkeypatch.setattr(hm, '_assert_runtime_project_generation', refuse)
    elif failure == 'missing_generation':
        rig.manifest.pop('project_generation')
    elif failure == 'runtime':
        monkeypatch.setitem(agent_runtime._RUNTIMES, 'codex', None)
    elif failure == 'character':
        (agents_dir / 'worker-codex.md').unlink()
    elif failure == 'context':
        monkeypatch.setattr(ar, '_build_agent_context', refuse)
    elif failure == 'isolation':
        monkeypatch.setattr(worker._sync, 'is_git_repo', lambda p: True)
    elif failure == 'prompt_write':
        monkeypatch.setattr(worker.os, 'replace', refuse)
    elif failure == 'prompt_sync':
        monkeypatch.setattr(worker.os, 'fsync', refuse)
    else:
        monkeypatch.setattr(ar, '_maybe_isolate_worktree', lambda p, sid, **kw: (p['project_path'], True))
        monkeypatch.setattr(worker._sync, 'git_run', lambda *a, **k: (False, 'git failed'))
    # Save before installing the failing writer, then enter the actual adapter.
    if failure == 'character':
        rig.manifest['config']['worker_provider'] = 'codex'
        sid = hm._hm_spawn_worker_session(rig.manifest, rig.ws, rig.project, 'roster1', 'ws1')
        assignment = state.agent_sessions[sid]['resolved_assignment']
        assert assignment['substituted'] and assignment['requested_character'] == 'global:worker-codex'
        assert assignment['substitution_step'] == 'tier_fallback'
        assert 'PERSONA-' not in rig.runtimes['codex'].calls[0]['system_prompt']
        assert hm._hm_read_bus_messages('roster1')[0]['type'] == 'character_substitution'
        return
    with pytest.raises((ValueError, RuntimeError, KeyError)):
        hm._hm_spawn_worker_session(rig.manifest, rig.ws, rig.project, 'roster1', 'ws1')
    assert not any(rt.calls for rt in rig.runtimes.values())
    if failure.startswith('prompt_'):
        assert not state.agent_sessions and not rig.rows
        assert not list(hm._hm_dir('roster1').rglob('*.tmp'))
        assert not list(hm._hm_dir('roster1').rglob('*.prompt.txt'))


def test_active_attempt_and_uncertain_restart_cannot_spawn_a_second_worker(rig):
    sid = launch(rig)
    with pytest.raises(ValueError, match='live worker'):
        hm._hm_spawn_worker_session(rig.manifest, dict(rig.ws), rig.project, 'roster1', 'ws1')
    state.agent_sessions.pop(sid)  # Server restarted; durable owner has not reconciled.
    with pytest.raises(ValueError, match='reconciliation'):
        hm._hm_spawn_worker_session(rig.manifest, dict(rig.ws), rig.project, 'roster1', 'ws1')
    assert len(rig.runtimes['codex'].calls) == 1


def test_failed_git_probe_cannot_downgrade_code_to_shared_analysis(rig, monkeypatch):
    (rig.client.proj_path / '.git').mkdir()
    monkeypatch.setattr(worker._sync, 'is_git_repo', lambda p: False)
    with pytest.raises(RuntimeError, match='requires an isolated worktree'):
        launch(rig)
    assert not rig.runtimes['codex'].calls
    row = hm._hm_load_workstream('roster1', 'ws1')
    assert row['status'] == 'blocked' and row['failure_reason'] == 'isolation_required'


def test_retry_and_reconciled_restart_resolve_current_character_once_per_attempt(rig, agents_dir):
    first = launch(rig)
    pinned = state.agent_sessions[first]
    characters.write_character('global', 'worker-codex', 'changed', 'NEW-PERSONA',
                               engine={'provider': 'gemini'}, agent_name='Changed', overwrite=True)
    assert 'NEW-PERSONA' not in ar._fresh_context_for(rig.project, pinned, task='retry')
    pinned.update(status='completed', process_alive=False)
    second = hm._hm_spawn_worker_session(rig.manifest, rig.ws, rig.project, 'roster1', 'ws1')
    assert second != first
    assert state.agent_sessions[second]['provider'] == 'gemini'
    assert 'NEW-PERSONA' in rig.runtimes['gemini'].calls[-1]['system_prompt']
    assert rig.ws['assignment_attempt'] == 2
    rig.ws.update(status='pending', current_agent_session_id=None)
    hm._hm_save_workstream('roster1', 'ws1', rig.ws)
    state.agent_sessions.clear()  # Reconciled restart uses the same new-attempt entry point.
    third = hm._hm_spawn_worker_session(rig.manifest, rig.ws, rig.project, 'roster1', 'ws1')
    assert third != second and rig.ws['assignment_attempt'] == 3
    assert state.agent_sessions[third]['provider'] == 'gemini'


def test_respawn_uses_pinned_context_even_after_fingerprint_or_roster_change(rig, monkeypatch):
    sid = launch(rig)
    session = state.agent_sessions[sid]
    monkeypatch.setattr(ar, '_context_fingerprint', lambda p: 'changed')
    monkeypatch.setattr(ar, '_resolve_character', lambda *a, **k: pytest.fail('live persona lookup'))
    monkeypatch.setattr(ar, '_sysprompt_file_args', lambda text: (text, None))
    assert ar._respawn_sysprompt_args(session, rig.project, 'ordinary followup')[0] == session['_system_prompt']
    session.pop('_system_prompt')
    assert ar._fresh_context_for(rig.project, session) == rig.runtimes['codex'].calls[0]['system_prompt']
    (hm._hm_dir('roster1') / session['pinned_prompt_path']).unlink()
    with pytest.raises(worker.RosterRecoveryHeld, match='context file unavailable'):
        ar._fresh_context_for(rig.project, session)


@pytest.mark.parametrize('provider', PROVIDERS)
def test_cold_recovery_uses_pinned_attempt_after_roster_deletion(rig, agents_dir, provider):
    sid = launch(rig, provider)
    session = state.agent_sessions.pop(sid)
    entry = dict(rig.rows[0], status='completed', project_generation=7,
                 provider_session_id='native-id')
    (agents_dir / f'worker-{provider}.md').unlink()
    revived = worker.revive_roster_worker(entry, rig.project, 'Continue assigned work')
    call = rig.runtimes[provider].calls[-1]
    assert revived['session_id'] == sid
    assert revived['character'] == session['character']
    assert call['system_prompt'] == session['_system_prompt']
    assert call['resume_id'] == 'native-id'
    assert call['project_path'] == session['_agent_cwd']
    assert hm._hm_load_workstream('roster1', 'ws1')['assignment_attempt'] == 1


@pytest.mark.parametrize('mapped', [False, True])
def test_fallback_attempt_recovers_same_snapshot_and_engine_without_second_event(rig, agents_dir, mapped):
    (agents_dir / 'worker-codex.md').unlink()
    rig.manifest['config']['worker_provider'] = 'codex'
    if mapped:
        rig.manifest['config']['hivemind_role_fallbacks'] = {'other': 'global:worker-gemini'}
    sid = launch(rig)
    session = state.agent_sessions.pop(sid)
    entry = dict(rig.rows[0], status='completed', provider_session_id='native-id')
    # Current roster and fallback settings cannot rewrite a pinned attempt.
    rig.manifest['config']['hivemind_role_fallbacks'] = {'other': 'global:worker-claude'}
    hm._hm_save_manifest('roster1', rig.manifest)
    revived = worker.revive_roster_worker(entry, rig.project, 'Continue')
    assert revived['character'] == session['character']
    assert revived['resolved_assignment'] == session['resolved_assignment']
    assert revived['resolved_assignment']['character_ref'] == ('global:worker-gemini' if mapped else '')
    assert len(hm._hm_read_bus_messages('roster1')) == 1


def test_deletion_after_pre_hr9_worker_pin_is_missing_at_attempt(rig, agents_dir):
    rig.ws['resolved_assignment'] = {'character_ref': rig.ws['character']}
    (agents_dir / 'worker-codex.md').unlink()
    rig.manifest['config']['worker_provider'] = 'codex'
    sid = launch(rig)
    assert state.agent_sessions[sid]['resolved_assignment']['substitution_reason'] == 'missing_at_attempt'


def test_substitution_log_failure_never_dispatches(rig, agents_dir, monkeypatch):
    (agents_dir / 'worker-codex.md').unlink()
    def refuse(*args):
        raise OSError('run bus unavailable')
    monkeypatch.setattr(hm, '_hm_append_bus_message', refuse)
    with pytest.raises(OSError, match='run bus unavailable'):
        launch(rig)
    assert not any(rt.calls for rt in rig.runtimes.values())


HOLD_REASONS = ['pending', 'generation', 'attempt', 'context', 'owner', 'missing_file',
                'sha_mismatch', 'path_escape']


def held_entry(rig, damage):
    sid = launch(rig)
    state.agent_sessions.pop(sid)
    entry = dict(rig.rows[0], status='completed', project_generation=7)
    if damage == 'pending':
        entry['status'] = 'in_progress'
    elif damage == 'generation':
        entry['project_generation'] = 8
    elif damage == 'attempt':
        entry['assignment_attempt'] = 9
    elif damage == 'context':
        entry.pop('pinned_prompt_path')
    elif damage == 'owner':
        ws = hm._hm_load_workstream('roster1', 'ws1')
        ws['current_agent_session_id'] = 'replacement'
        hm._hm_save_workstream('roster1', 'ws1', ws)
    elif damage == 'missing_file':
        (hm._hm_dir('roster1') / entry['pinned_prompt_path']).unlink()
    elif damage == 'sha_mismatch':
        (hm._hm_dir('roster1') / entry['pinned_prompt_path']).write_text('TAMPERED', encoding='utf-8')
    elif damage == 'path_escape':
        entry['pinned_prompt_path'] = '../outside.txt'
    rig.rows[0].update(entry)
    if damage == 'context':
        rig.rows[0].pop('pinned_prompt_path')
    return sid, entry


@pytest.mark.parametrize('damage', HOLD_REASONS)
def test_cold_recovery_refuses_unknown_or_replaced_attempt(rig, damage):
    sid, entry = held_entry(rig, damage)
    with pytest.raises(worker.RosterRecoveryHeld):
        worker.revive_roster_worker(entry, rig.project, 'Continue')
    assert len(rig.runtimes['codex'].calls) == 1
    assert sid not in state.agent_sessions


@pytest.fixture
def route_rig(rig, monkeypatch):
    monkeypatch.setattr(ar, 'load_project', lambda pid: rig.project)
    monkeypatch.setattr(ar, 'get_manager', hm.get_manager)
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: rig.rows)
    monkeypatch.setattr(ar, '_mark_human_joined', lambda *a: None)
    monkeypatch.setattr(ar, '_observe_habit_cue', lambda *a: None)
    monkeypatch.setattr(ar, '_assert_runtime_project_generation', lambda *a, **kw: 7)
    monkeypatch.setattr(ar, '_dispatch_agent_internal', lambda *a, **kw: pytest.fail('ordinary dispatch'))
    monkeypatch.setattr(ar, '_find_transcript_file', lambda *a: pytest.fail('transcript fallback'))
    monkeypatch.setitem(state.CONFIG, 'agent_revive_from_log', True)
    return rig


@pytest.mark.parametrize('endpoint', ['send', 'followup'])
@pytest.mark.parametrize('damage', HOLD_REASONS)
def test_route_recovery_hold_is_409_and_starts_nothing(route_rig, endpoint, damage):
    rig = route_rig
    sid, entry = held_entry(rig, damage)
    rig.runtimes['codex'].calls.clear()
    before = dict(state.agent_sessions)
    with pytest.raises(worker.RosterRecoveryHeld) as hold:
        worker.revive_roster_worker(entry, rig.project, 'Continue')
    response = rig.client.post(f'/api/project/thm/agent/{endpoint}',
                               json={'session_id': sid, 'message': 'Continue'})
    assert response.status_code == 409, response.get_json()
    assert response.get_json() == {'ok': False, 'error': str(hold.value)}
    assert not any(rt.calls for rt in rig.runtimes.values())
    assert state.agent_sessions == before


@pytest.mark.parametrize('endpoint', ['send', 'followup'])
def test_second_revive_callsite_also_returns_409(route_rig, monkeypatch, endpoint):
    rig = route_rig
    sid, _ = held_entry(rig, 'context')
    monkeypatch.setitem(state.CONFIG, 'agent_revive_from_log', False)
    rig.runtimes['codex'].calls.clear()
    response = rig.client.post(f'/api/project/thm/agent/{endpoint}',
                               json={'session_id': sid, 'message': 'Continue'})
    assert response.status_code == 409, response.get_json()
    assert 'pinned context reference' in response.get_json()['error']
    assert not state.agent_sessions and not any(rt.calls for rt in rig.runtimes.values())


@pytest.mark.parametrize('endpoint', ['send', 'followup'])
@pytest.mark.parametrize('provider', ['claude', 'codex'])
def test_route_successful_roster_recovery_uses_original_adapter(route_rig, endpoint, provider):
    rig = route_rig
    sid = launch(rig, provider)
    original = state.agent_sessions.pop(sid)
    rig.rows[0].update(status='completed', provider_session_id='native-id')
    response = rig.client.post(f'/api/project/thm/agent/{endpoint}',
                               json={'session_id': sid, 'message': 'Continue'})
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['session_id'] == sid
    assert response.get_json()['revived'] is True
    assert list(state.agent_sessions) == [sid]
    assert len(rig.runtimes[provider].calls) == 2
    assert rig.runtimes[provider].calls[-1]['system_prompt'] == original['_system_prompt']


@pytest.mark.parametrize('provider', ['claude', 'codex'])
def test_parent_callback_roster_recovery_cannot_degrade(route_rig, provider):
    rig = route_rig
    sid = launch(rig, provider)
    original = state.agent_sessions.pop(sid)
    rig.rows[0].update(status='completed', provider_session_id='native-id')
    path = hm._hm_dir('roster1') / original['pinned_prompt_path']
    prompt = path.read_bytes()
    path.unlink()
    rig.runtimes[provider].calls.clear()
    with pytest.raises(ar.DeliveryBlocked, match='context file unavailable'):
        ar._revive_parent_for_delegation('thm', sid, 'Child completed', rig.project)
    assert not state.agent_sessions and not any(rt.calls for rt in rig.runtimes.values())
    path.write_bytes(prompt)
    result = ar._revive_parent_for_delegation('thm', sid, 'Child completed', rig.project)
    assert result['revived'] is True and result['parent_session_id'] == sid
    assert rig.runtimes[provider].calls[-1]['system_prompt'] == original['_system_prompt']


def test_log_row_size_with_49kb_project_context(route_rig, monkeypatch):
    rig = route_rig
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **kw: 'PROJECT CONTEXT\n' + 'p' * 49_000)
    sid = launch(rig)
    session = state.agent_sessions[sid]
    for status in ('in_progress', 'completed'):
        if status == 'completed':
            session.update(status=status, process_alive=False)
            rig.runtimes['codex'].calls[-1]['callbacks']['on_process_exit'](None, session)
        row, = rig.rows
        assert row['status'] == status and '_system_prompt' not in row
        assert worker.read_pinned_prompt(row) == session['_system_prompt']
        before = {k: v for k, v in row.items() if not k.startswith('pinned_prompt_')}
        before['_system_prompt'] = session['_system_prompt']
        size = lambda value: len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
        old, new = size(before), size(row)
        assert old - new > 48_000
        print(f'ROW_BYTES {status}: before={old} after={new} saved={old-new}')


def test_manual_and_automatic_entry_points_use_roster_adapter(rig):
    response = rig.client.post('/api/hivemind/roster1/workstreams/ws1/spawn')
    assert response.status_code == 200, response.get_json()
    assert len(rig.runtimes['codex'].calls) == 1
    # A separate ready workstream exercises automatic admission without touching HR-3+.
    ws2 = {'id': 'ws2', 'title': 'Second', 'status': 'pending', 'dependencies': [],
           'character': 'global:worker-gemini'}
    hm._hm_save_workstream('roster1', 'ws2', ws2)
    rig.manifest['status'] = 'active'
    rig.manifest['config']['max_concurrent_workers'] = 2
    hm._hm_save_manifest('roster1', rig.manifest)
    hm._hm_auto_spawn_workers('roster1')
    assert len(rig.runtimes['gemini'].calls) == 1


# The real git/Claude bridge harness owns all cleanup, fake Popen and threads.
from tests.test_hivemind_worktree_dispatch_hook import env, repo  # noqa: E402,F401


@pytest.mark.parametrize('model', ['', 'sonnet'])
def test_real_claude_bridge_keeps_one_tree_prompt_identity_and_launch_hooks(
        env, agents_dir, monkeypatch, model):
    import server
    from tests.test_hivemind_worktree_dispatch_hook import _agent_trees, _agent_branches

    characters.write_character('global', 'bridge-worker', 'fixture', 'BRIDGE-PERSONA',
                               agent_name='Bridge', skills=['fixture-skill'],
                               engine={'provider': 'claude', 'model': model, 'effort': 'high'})
    manifest = {'id': 'hm1', 'project_id': 'hmhook', 'project_generation': 1,
                'created_by_session_id': 'parent', 'config': {'roster_version': 1,
                                                            'worker_max_turns': 17}}
    ws = {'id': 'ws1', 'title': 'Bridge work', 'character': 'global:bridge-worker'}
    hm._hm_ensure_dirs('hm1')
    hm._hm_save_manifest('hm1', manifest)
    hm._hm_save_workstream('hm1', 'ws1', ws)
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **kw: '')
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a: '')
    monkeypatch.setattr(ar, '_update_agent_log', lambda pid, fn: fn([]))
    builds, flags, registrations, guards = [], [], [], []

    def context(project, **kwargs):
        builds.append(kwargs)
        return f"{kwargs['character_body']}\nUNIVERSAL-ONCE"

    monkeypatch.setattr(ar, '_build_agent_context', context)
    monkeypatch.setattr(ar, '_resolve_character', lambda *a, **k: pytest.fail('second persona lookup'))
    monkeypatch.setattr(ar, '_dispatch_with_routing_parallel', lambda *a, **k: pytest.fail('second router'))
    real_flags = ar._build_claude_flags
    monkeypatch.setattr(ar, '_build_claude_flags', lambda *a, **kw: flags.append(kw) or real_flags(*a, **kw))
    monkeypatch.setattr(ar, '_register_process', lambda *a, **kw: registrations.append(a))
    monkeypatch.setattr(ar._launch_marker, 'launch_cmd', lambda cmd, *a: guards.append(a) or cmd)
    prompts = []
    real_prompt = ar._sysprompt_file_args
    monkeypatch.setattr(ar, '_sysprompt_file_args', lambda text: prompts.append(text) or real_prompt(text))
    sid = hm._hm_spawn_worker_session(manifest, ws, env['project'], 'hm1', 'ws1')
    session = state.agent_sessions[sid]
    assert _agent_trees(env['repo']) == [sid]
    assert _agent_branches(env['repo']) == [f'clayrune/agent/{sid}']
    assert Path(env['spawned'][0].cwd) == Path(session['_agent_cwd'])
    assert session['session_id'] == sid and sid.startswith('hm_')
    assert session['character']['agent_name'] == 'Bridge'
    assert session['model'] == model and session['requested_effort'] == 'high'
    assert session['_spawned_by'] == 'parent'
    assert len(builds) == len(prompts) == len(flags) == len(registrations) == len(guards) == 1
    assert prompts[0].count('UNIVERSAL-ONCE') == 1
    assert prompts[0].count('BRIDGE-PERSONA') == 1
    assert flags[0]['max_turns_override'] == 17 and flags[0]['character_skills'] == ['fixture-skill']
    assert ws['base_commit'] and session['_worktree_isolated'] is True
