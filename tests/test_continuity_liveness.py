"""MC-1146: historic continuity cannot assert dead work is still live."""
from types import SimpleNamespace
import subprocess

import pytest

from mc import continuity_liveness as live, memory as mem, session_provenance as provenance, state

SID = '938e81dbb116'
P = {'id': 'continuity-live'}
TEXT = f'Tobin fixing on branch ({SID})'


@pytest.fixture
def env(tmp_path, monkeypatch):
    import server  # noqa: F401
    monkeypatch.setattr(mem, '_get_memory_path', lambda p: tmp_path / 'MEMORY.md')
    monkeypatch.setattr(state, 'agent_sessions', {})
    monkeypatch.setattr(provenance, '_seen_projects', set())
    monkeypatch.setattr(provenance, '_origins', {})
    return tmp_path


def test_live_session_and_existing_branch_kept(env, monkeypatch):
    state.agent_sessions[SID] = dict(project_id=P['id'], status='running')
    monkeypatch.setattr(live, '_branch_exists', lambda p, b: True)
    mem.write_continuity(P, threads=[TEXT], owner='Dave')
    assert f'IN FLIGHT — {TEXT}' in mem.render_continuity(P, owner='Dave')


def test_dead_session_and_absent_branch_marked_lost(env, monkeypatch):
    monkeypatch.setattr(live, '_branch_exists', lambda p, b: False)
    mem.write_continuity(P, threads=[TEXT], owner='Dave')
    rendered = mem.render_continuity(P, owner='Dave')
    assert f'LOST IN RESTART: {TEXT} (session {SID} not running; branch absent' in rendered
    assert 'IN FLIGHT' not in rendered
    assert mem.read_continuity(P, owner='Dave')['threads'] == [TEXT]


@pytest.mark.parametrize('status', ['stopped', 'completed', 'error'])
def test_retained_terminal_registry_rows_are_not_live(env, status):
    state.agent_sessions[SID] = dict(project_id=P['id'], status=status)
    assert 'LOST IN RESTART' in live.render_thread(P, f'Tobin session {SID}', 'age')


def test_other_owners_threads_are_also_checked(env, monkeypatch):
    monkeypatch.setattr(live, '_branch_exists', lambda p, b: False)
    mem.write_continuity(P, threads=[TEXT], owner='Tobin')
    assert f'Tobin — LOST IN RESTART: {TEXT}' in mem.render_continuity(P, owner='Dave')


def test_dead_process_is_not_resurrected_by_running_status(env):
    state.agent_sessions[SID] = dict(project_id=P['id'], status='running',
                                    proc=SimpleNamespace(poll=lambda: 0))
    assert 'not running' in live.render_thread(P, f'session {SID}', 'age')


def test_wrong_project_session_does_not_validate_claim(env):
    state.agent_sessions[SID] = dict(project_id='other', status='running')
    assert 'LOST IN RESTART' in live.render_thread(P, f'session {SID}', 'age')


def test_missing_branch_with_live_session_does_not_render_inflight(env, monkeypatch):
    state.agent_sessions[SID] = dict(project_id=P['id'], status='running')
    monkeypatch.setattr(live, '_branch_exists', lambda p, b: False)
    rendered = live.render_thread(P, f'session {SID} on branch fix/ticket', 'age')
    assert rendered.startswith('STATE MISMATCH:')
    assert 'branch absent' in rendered


def test_failed_branch_probe_is_unknown_and_logged(env, monkeypatch):
    state.agent_sessions[SID] = dict(project_id=P['id'], status='running')
    def fail(*a, **kw):
        raise OSError('probe unavailable')
    monkeypatch.setattr(live.subprocess, 'run', fail)
    logs = []
    monkeypatch.setattr(live, '_log', logs.append)
    rendered = live.render_thread(dict(P, project_path=str(env)), TEXT, 'age')
    assert rendered.startswith('UNVERIFIED:')
    assert 'branch check unavailable' in rendered
    assert 'probe unavailable' in logs[0]


def test_real_git_local_branch_exists_and_absent(env):
    subprocess.run(['git', 'init', str(env)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(env), '-c', 'user.name=Test',
                    '-c', 'user.email=test@example.invalid', 'commit', '--allow-empty',
                    '-m', 'fixture'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(env), 'branch', f'clayrune/agent/{SID}'], check=True)
    project = dict(P, project_path=str(env))
    assert live._branch_exists(project, f'clayrune/agent/{SID}') is True
    assert live._branch_exists(project, 'missing') is False


def test_user_started_first_after_restart_then_later_new_chat(env):
    first = provenance.origin_line(P['id'], 'first', 'ui')
    second = provenance.origin_line(P['id'], 'second', 'ui')
    assert 'user-started new chat' in first
    assert 'first session for this project after server start/restart' in first
    assert 'user-started new chat' in second
    assert 'restart' not in second
    assert provenance.origin_line(P['id'], 'first', 'ui') == first


def test_rollover_origin_does_not_come_from_old_activity(env):
    provenance.origin_line(P['id'], 'first', 'ui')
    provenance.note_rollover(P['id'], 'first', 'native-predecessor')
    line = provenance.origin_line(P['id'], 'first')
    assert 'auto-fresh rollover from predecessor session native-predecessor' in line
    mem.write_continuity(P, threads=['finish tests'])
    assert line in mem.render_continuity(P, origin=line)


def test_activity_owners_are_explicit_and_legacy_is_unknown(env):
    block = provenance.recent_activity([
        {'session_id': 'current', 'msg': 'new turn'},
        {'session_id': 'old', 'msg': 'Agent interrupted: context rolled over mid-task'},
        {'msg': 'Auto-fresh: old event'},
    ], 'current')
    assert '[this session current]' in block
    assert '[other/predecessor session old; not this session]' in block
    assert '[project history; session unknown, may predate this chat]' in block


def test_resumed_chat_after_restart_is_not_called_user_started(env):
    provenance.note_resume(P['id'], 'current', 'saved-native')
    line = provenance.origin_line(P['id'], 'current')
    assert 'existing chat resumed (session saved-native)' in line
    assert 'first session for this project after server start/restart' in line
    assert 'user-started' not in line


def test_real_handoff_helper_sets_origin_before_context_rebuild(env, monkeypatch):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, '_build_handoff_context', lambda *a, **kw: ('handoff', {}))
    monkeypatch.setattr(ar, '_in_flight_children', lambda *a: [])
    ar._auto_fresh_handoff(str(env), 'claude', 'old-native', P['id'], 'current',
                           reason='tokens', detail=200000)
    line = provenance.origin_line(P['id'], 'current')
    assert 'auto-fresh rollover from predecessor session old-native' in line
    assert 'first session for this project after server start/restart' in line


def test_context_builder_injects_origin_and_labels_legacy_activity(env, monkeypatch):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, '_recent_claude_transcripts', lambda *a, **kw: [])
    monkeypatch.setattr(ar, '_load_agent_log', lambda *a: [])
    monkeypatch.setitem(state.CONFIG, 'continuity_enabled', True)
    monkeypatch.setitem(state.CONFIG, 'exploration_readback_enabled', False)
    monkeypatch.setitem(state.CONFIG, 'coordination_enabled', False)
    project = dict(P, project_path=str(env), activity_log=[{
        'msg': 'Agent interrupted: context rolled over mid-task'}])
    ctx = ar._build_agent_context(project, session_id='current', source='ui')
    assert 'Session origin: user-started new chat' in ctx
    assert '[project history; session unknown, may predate this chat]' in ctx
    assert 'Session origin: auto-fresh' not in ctx
    provenance.note_rollover(P['id'], 'current', 'prior-native')
    ctx = ar._build_agent_context(project, session_id='current', source='ui')
    assert 'Session origin: auto-fresh rollover from predecessor session prior-native' in ctx
