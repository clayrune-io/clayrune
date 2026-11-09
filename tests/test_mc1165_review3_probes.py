"""Review-only temporary-repository probes. No live provider processes."""
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
import mc.agent_worktree as w
import mc.merge_review_gate as gate
from mc import worktree_lifecycle as lifecycle
from mc.state import agent_sessions
from test_cold_review_wiring import project, routes, _session, _branch
from test_merge_review_gate import _agent, _store, repo
import test_worktree_lifecycle as supplied


def setup_session(project, monkeypatch, sid, **extra):
    tree, tip = _agent(project, sid)
    session = _session(sid)
    session.update(status='error', last_status_change_time=100.0, log_lines=[],
                   mode='A', proc=None, _agent_cwd=str(tree), **extra)
    monkeypatch.setitem(agent_sessions, sid, session)
    gate.record(project, _branch(sid), sha=tip, verdict='pass', cold=True)
    return tree, session


@pytest.mark.parametrize('timer', [False, True])
def test_stuck_queue_resume_keeps_isolation(routes, project, monkeypatch, timer):
    sid = 'queue-timer' if timer else 'queue-callback'
    tree, session = setup_session(project, monkeypatch, sid, pending_followups=['retry'])
    supplied._fake_spawn(routes, monkeypatch)
    cwds = []
    monkeypatch.setattr(routes, 'spawn_engine_proc', lambda *a, **k:
                        cwds.append(k['cwd']) or SimpleNamespace(pid=12345))
    if timer:
        assert w.merge_back(project, sid)[0] == 'clean'
        routes._worktree_retry_removal(session, 1)
    else:
        routes._worktree_cold_review_done(session, 0, 100.0)
    routes._guardian_check_session(sid, session, 200.0)
    assert session['status'] == 'running' and not session['pending_followups']
    assert cwds and cwds[0] != project['project_path'], (
        f'guardian spawned in MAIN: {cwds}; tree_exists={Path(tree).exists()}')


def test_queue_pop_gap_is_not_covered_by_pending_guard(routes, project, monkeypatch):
    sid = 'queue-pop-gap'
    tree, session = setup_session(project, monkeypatch, sid, pending_followups=['retry'])
    supplied._fake_spawn(routes, monkeypatch)
    cwds = []
    monkeypatch.setattr(routes, 'spawn_engine_proc', lambda *a, **k:
                        cwds.append(k['cwd']) or SimpleNamespace(pid=12345))
    entered, release = threading.Event(), threading.Event()
    real_auto = routes._auto_dispatch_followup
    def pause_after_queue_pop(s, msg):
        entered.set()
        assert release.wait(10)
        real_auto(s, msg)
    monkeypatch.setattr(routes, '_auto_dispatch_followup', pause_after_queue_pop)
    # Simulate the suggested extra queue guard without changing production code.
    real_pending = routes._worktree_resume_pending
    monkeypatch.setattr(routes, '_worktree_resume_pending', lambda s, live:
                        real_pending(s, live) or bool((live or s).get('pending_followups')))
    guardian = threading.Thread(target=routes._guardian_check_session,
                                args=(sid, session, 200.0))
    guardian.start()
    try:
        assert entered.wait(10) and not session['pending_followups']
        routes._worktree_cold_review_done(session, 0, 100.0)
    finally:
        release.set()
        guardian.join(15)
    assert not guardian.is_alive()
    assert cwds and cwds[0] != project['project_path'], 'queue-only guard still spawned in MAIN'


def test_tripped_breaker_holds_branch_without_latching_review(routes, project, monkeypatch):
    sid = 'breaker-hold'
    tree, session = setup_session(project, monkeypatch, sid,
                                 pending_recovery_message='retry', circuit_breaker_tripped=True)
    routes._guardian_attempt_recovery(session)
    routes._worktree_cold_review_done(session, 0, 100.0)
    routes._worktree_retry_removal(session, 1)
    assert Path(tree).exists() and session['pending_recovery_message'] == 'retry'
    assert gate.branch_tip(project, _branch(sid))
    assert session['status'] == 'error'


def test_fresh_resume_cannot_spawn_inside_removal_window(routes, project, monkeypatch):
    sid = 'fresh-retry'
    tree, session = setup_session(project, monkeypatch, sid, _resume_id='old-native')
    supplied._fake_spawn(routes, monkeypatch)
    monkeypatch.setattr(routes, '_fresh_context_for', lambda *a: '')
    monkeypatch.setattr(routes, '_sysprompt_file_args', lambda *a: ([], None))
    spawned = threading.Event()
    cwds = []
    def spawn(*a, **k):
        cwds.append(k['cwd'])
        spawned.set()
        return SimpleNamespace(pid=12345)
    monkeypatch.setattr(routes, 'spawn_engine_proc', spawn)
    t = threading.Thread(target=routes._auto_recover_failed_resume, args=(session,))
    with lifecycle.removal_window(routes.get_manager('testproj').lock, sid):
        t.start()
        spawned_inside = spawned.wait(1)
        status_inside = session['status']
    t.join(15)
    assert not t.is_alive()
    assert not spawned_inside, f'spawned while removal owns both locks; status={status_inside}; cwd={cwds}'


@contextmanager
def no_lock(*args):
    yield


@pytest.mark.parametrize('which', ['guardian', 'timer'])
def test_rewritten_race_probe_detects_missing_exclusion(routes, project, monkeypatch, which):
    if which == 'guardian':
        monkeypatch.setattr(lifecycle, 'resuming', no_lock)
        fn = supplied.test_guardian_resume_waits_for_the_cold_callback_window
        message = 'guardian resumed'
    else:
        monkeypatch.setattr(lifecycle, 'removal_window', no_lock)
        fn = supplied.test_timer_retry_holds_the_exclusion_from_status_check_through_removal
        message = 'timer removed'
    with pytest.raises(AssertionError, match=message):
        fn(routes, project, monkeypatch)


def test_context_refresh_does_not_invert_project_and_lifecycle_locks(routes, project, monkeypatch, tmp_path):
    import traceback
    from mc import state
    from mc.blueprints import coordination_routes as coord
    sid = 'context-lock'
    tree, session = setup_session(project, monkeypatch, sid)
    real_respawn = routes._respawn_sysprompt_args
    supplied._fake_spawn(routes, monkeypatch)
    monkeypatch.setattr(routes, '_respawn_sysprompt_args', real_respawn)
    monkeypatch.setattr(routes, '_context_fingerprint', lambda *a: 'changed')
    monkeypatch.setattr(routes, '_session_character_parts', lambda *a: ('', '', []))
    monkeypatch.setattr(routes, '_sysprompt_file_args', lambda *a: ([], None))
    monkeypatch.setattr(routes, '_get_memory_path', lambda *a: tmp_path / 'absent-memory')
    monkeypatch.setattr(routes, '_get_archive_path', lambda *a: tmp_path / 'absent-archive')
    monkeypatch.setattr(routes, 'SHARED_RULES_PATH', tmp_path / 'absent-rules')
    monkeypatch.setattr(routes, 'DATA_DIR', tmp_path / 'projects')
    monkeypatch.setattr(routes, '_skills_catalog_block', lambda *a, **k: '')
    monkeypatch.setattr(routes, '_memory_search', lambda *a, **k: [])
    monkeypatch.setattr(routes, '_load_agent_log', lambda *a: [])
    monkeypatch.setattr(routes, '_recent_claude_transcripts', lambda *a, **k: [])
    monkeypatch.setattr(routes, '_allowance_conflict_block', lambda *a: '')
    monkeypatch.setattr(state, 'CONFIG', {'coordination_enabled': True,
                                        'exploration_readback_enabled': False})
    session.update(source='agent', job_shape='task', _system_prompt='old', _system_prompt_fp='old')
    manager = routes.get_manager('testproj')
    monkeypatch.setattr(coord, 'get_manager', routes.get_manager)
    monkeypatch.setattr(coord, '_read_events', lambda *a, **k: [])
    context_ready, callback_owns_project = threading.Event(), threading.Event()
    timeouts = []
    # Same mutual exclusion as RLock, but bounded on a contended acquisition
    # so a demonstrated production deadlock cannot hang this test process.
    class BoundedProjectLock:
        def __init__(self):
            self.lock = threading.RLock()
        def __enter__(self):
            if not self.lock.acquire(timeout=1.5):
                timeouts.append(''.join(traceback.format_stack()))
                raise RuntimeError('test detected project/lifecycle lock cycle')
            if threading.current_thread().name == 'review-callback':
                callback_owns_project.set()
            return self
        def __exit__(self, *a):
            self.lock.release()
    monkeypatch.setattr(manager, 'lock', BoundedProjectLock())
    def pause_context(*a):
        context_ready.set()
        assert callback_owns_project.wait(10)
    monkeypatch.setattr(coord, 'reconcile_commits', pause_context)
    guardian = threading.Thread(target=routes._auto_dispatch_followup,
                                args=(session, 'retry'), name='review-guardian')
    guardian.start()
    assert context_ready.wait(10), session.get('log_lines')
    callback = threading.Thread(target=routes._worktree_cold_review_done,
                                args=(session, 0, 100.0), name='review-callback')
    callback.start()
    guardian.join(15)
    callback.join(15)
    assert not guardian.is_alive() and not callback.is_alive()
    assert not timeouts, 'project/lifecycle lock inversion:\n' + '\n'.join(timeouts)
