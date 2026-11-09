"""MC-1165 round 4: an isolated session's process never starts in the project
checkout. `mc/isolated_launch.py` is the one cwd rule; these tests pin the rule
itself and the callers that used to bypass it (`_auto_recover_failed_resume`
spawned in the project path before taking any lock, `_auto_dispatch_followup`
fell back through `_session_cwd`). Temporary repos and sessions, process
creation faked, no server."""
from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import mc.agent_worktree as w
from mc import isolated_launch as launch
from mc import worktree_lifecycle as lifecycle
from mc.state import agent_sessions
from test_cold_review_wiring import project, routes, _session  # noqa: F401
from test_merge_review_gate import _agent, repo  # noqa: F401
import test_worktree_lifecycle as supplied


def _held(sid):
    return lifecycle.resuming(threading.RLock(), sid)


# ── the rule itself ──────────────────────────────────────────────────────────

def test_existing_worktree_is_returned_as_is(project):
    tree, _tip = _agent(project, 'keep1')
    sess = {'session_id': 'keep1', '_worktree_isolated': True, '_agent_cwd': str(tree)}
    with _held('keep1'):
        assert launch.launch_cwd(sess, project, project['project_path']) == (str(tree), '')


def test_removed_worktree_is_recreated_on_the_session_branch(project):
    tree, _tip = _agent(project, 'gone1')
    sess = {'session_id': 'gone1', '_worktree_isolated': True, '_agent_cwd': str(tree)}
    assert w.remove(project, 'gone1', delete_branch=False, force=True)[0] and not Path(tree).exists()
    with _held('gone1'):
        cwd, note = launch.launch_cwd(sess, project, project['project_path'])
    assert Path(cwd) == Path(tree) and (Path(cwd) / '.git').exists()
    assert 'recreated' in note and sess['_worktree_recreated'] == 1


@pytest.mark.parametrize('removed', [False, True])
def test_borrowed_tree_never_recreates_under_the_borrowers_id(project, removed):
    tree, _tip = _agent(project, 'lender')
    sess = {'session_id': 'borrower', '_worktree_isolated': True,
            '_worktree_borrowed': True, '_agent_cwd': str(tree)}
    if removed:
        assert w.remove(project, 'lender', delete_branch=False, force=True)[0]
    with _held('borrower'):
        if removed:
            with pytest.raises(launch.LaunchRefused, match='borrowed'):
                launch.launch_cwd(sess, project, project['project_path'],
                                  create=lambda *a: pytest.fail('borrower cannot recreate'))
        else:
            assert launch.launch_cwd(sess, project, project['project_path']) == (str(tree), '')
    assert not w.worktree_path(project, 'borrower').exists()


@pytest.mark.parametrize('recorded', ['', 'PROJECT', 'MISSING', 'BARE'])
def test_unusable_worktree_with_failing_recreate_refuses_never_the_project(project, recorded, tmp_path):
    pp = project['project_path']
    cwd = {'': '', 'PROJECT': pp, 'MISSING': str(tmp_path / 'nope'), 'BARE': str(tmp_path)}[recorded]
    sess = {'session_id': 'refuse1', '_worktree_isolated': True, '_agent_cwd': cwd}
    with _held('refuse1'):
        with pytest.raises(launch.LaunchRefused, match='disk full'):
            launch.launch_cwd(sess, project, pp, create=lambda p, s: (False, 'disk full'))
    assert sess['_worktree_isolated'] is True and sess['_agent_cwd'] == cwd


def test_a_create_that_returns_the_project_path_is_still_refused(project):
    pp = project['project_path']
    sess = {'session_id': 'refuse2', '_worktree_isolated': True, '_agent_cwd': ''}
    with _held('refuse2'):
        with pytest.raises(launch.LaunchRefused):
            launch.launch_cwd(sess, project, pp, create=lambda p, s: (True, pp))


def test_choosing_a_directory_outside_the_exclusion_is_refused(project):
    tree, _tip = _agent(project, 'out1')
    sess = {'session_id': 'out1', '_worktree_isolated': True, '_agent_cwd': str(tree)}
    with pytest.raises(launch.LaunchRefused, match='outside the lifecycle'):
        launch.launch_cwd(sess, project, project['project_path'])


def test_non_isolated_session_keeps_its_directory_or_the_project(project, tmp_path):
    pp = project['project_path']
    assert launch.launch_cwd({'session_id': 'a'}, project, pp) == (pp, '')
    assert launch.launch_cwd({'session_id': 'a', '_agent_cwd': str(tmp_path)}, project, pp) == (str(tmp_path), '')
    assert launch.launch_cwd({'session_id': 'a', '_agent_cwd': str(tmp_path / 'x')}, project, pp) == (pp, '')


def test_mark_refused_keeps_the_request_once():
    s = {'session_id': 'm1', 'mode': 'B', 'log_lines': [], 'status': 'running', 'process_alive': True}
    line = launch.mark_refused(s, 'do the thing', 'no tree', 5.0)
    launch.mark_refused(s, 'do the thing', 'no tree', 6.0)
    assert s['status'] == 'error' and s['process_alive'] is False
    assert s['pending_recovery_message'] == 'do the thing'
    assert s['_launch_refused'] == 'no tree' and 'NOT started in the project checkout' in line
    launch.mark_refused(s, 'second', 'no tree', 7.0)
    assert s['pending_recovery_message'] == 'do the thing\n\nsecond'


# ── the callers that used to bypass it ───────────────────────────────────────

def _refusing_setup(routes, project, monkeypatch, sid, **extra):
    tree, _tip = _agent(project, sid)
    session = _session(sid)
    session.update(status='error', last_status_change_time=100.0, log_lines=[], mode='A',
                   proc=None, _agent_cwd=str(tree), **extra)
    monkeypatch.setitem(agent_sessions, sid, session)
    supplied._fake_spawn(routes, monkeypatch)
    spawned = []
    monkeypatch.setattr(routes, 'spawn_engine_proc',
                        lambda *a, **k: spawned.append(k['cwd']) or SimpleNamespace(pid=1))
    monkeypatch.setattr(routes, '_fresh_context_for', lambda *a: '')
    monkeypatch.setattr(routes, '_sysprompt_file_args', lambda *a: ([], None))
    # the tree is gone and cannot be recreated
    assert w.remove(project, sid, delete_branch=False, force=True)[0] and not Path(tree).exists()
    monkeypatch.setattr(w, 'create', lambda p, s: (False, 'disk full'))
    return session, spawned


@pytest.mark.parametrize('mode', ['A', 'B'])
def test_failed_resume_recovery_refuses_instead_of_spawning_in_the_project(routes, project, monkeypatch, mode):
    session, spawned = _refusing_setup(routes, project, monkeypatch, 'rec-' + mode,
                                       _resume_id='old-native', task='finish the report')
    session['mode'] = mode
    routes._auto_recover_failed_resume(session)
    assert spawned == [], f'spawned in {spawned}'
    assert session['status'] == 'error' and 'disk full' in session['_launch_refused']
    assert session['pending_recovery_message'] == 'finish the report'
    assert any('Launch refused' in str(l) for l in session['log_lines'])


def test_queued_followup_drain_refuses_and_keeps_the_message(routes, project, monkeypatch):
    session, spawned = _refusing_setup(routes, project, monkeypatch, 'drain1')
    routes._auto_dispatch_followup(session, 'please continue')
    assert spawned == []
    assert session['status'] == 'error' and session['pending_recovery_message'] == 'please continue'


def test_recovery_recreates_and_spawns_in_the_worktree_when_it_can(routes, project, monkeypatch):
    tree, _tip = _agent(project, 'ok-rec')
    session = _session('ok-rec')
    session.update(status='error', log_lines=[], mode='A', proc=None, _agent_cwd=str(tree),
                   _resume_id='old-native', task='go')
    monkeypatch.setitem(agent_sessions, 'ok-rec', session)
    supplied._fake_spawn(routes, monkeypatch)
    cwds = []
    monkeypatch.setattr(routes, 'spawn_engine_proc',
                        lambda *a, **k: cwds.append(k['cwd']) or SimpleNamespace(pid=1))
    monkeypatch.setattr(routes, '_fresh_context_for', lambda *a: '')
    monkeypatch.setattr(routes, '_sysprompt_file_args', lambda *a: ([], None))
    assert w.remove(project, 'ok-rec', delete_branch=False, force=True)[0]
    routes._auto_recover_failed_resume(session)
    assert [Path(c) for c in cwds] == [Path(tree)] and session['status'] == 'running'
    assert (Path(tree) / '.git').exists()


def test_session_cwd_for_an_isolated_session_never_returns_the_project(routes, project, monkeypatch):
    session, _spawned = _refusing_setup(routes, project, monkeypatch, 'cwd1')
    pp = project['project_path']
    with pytest.raises(launch.LaunchRefused):
        routes._session_cwd(session, pp, 'msg')
    assert routes._session_cwd_for_reading(session, pp) != pp


def test_a_worktree_that_can_never_be_rebuilt_ends_in_needs_attention_not_a_loop(routes, project, monkeypatch):
    """Dave's decision (MC-1165 round 4): the refused request stays held for the
    guardian, whose own attempt counter bounds the retries."""
    session, spawned = _refusing_setup(routes, project, monkeypatch, 'bound1')
    session['pending_recovery_message'] = 'keep going'
    for n in (1, 2, 3):
        session['last_recovery_time'] = 0          # step past the backoff
        routes._guardian_attempt_recovery(session)
        assert session['recovery_attempts'] == n and spawned == []
    assert session['circuit_breaker_tripped'] is True
    assert session['guardian_state'] == 'needs_attention'
    assert session['pending_recovery_message'] == 'keep going'
    session['last_recovery_time'] = 0
    routes._guardian_attempt_recovery(session)     # a 4th launch must not happen
    assert session['recovery_attempts'] == 3 and spawned == []
    assert session['pending_recovery_message'] == 'keep going'
