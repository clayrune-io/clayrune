"""MC-1165 round 3: one lifecycle exclusion between "resume a session" and "merge
back + remove its worktree" (Fenn review 2, findings 1-3). Temporary repos and
sessions; process creation is faked, no agent is launched, no server runs.

Each race test drives the real code with a second thread and asserts the one
invariant that matters: the worktree is never removed while the session reads
`running`. A resume that arrives mid-window must WAIT for it, and a resume that
arrives after finds the tree already gone (the follow-up path recreates it,
`mc/agent_worktree_followup.py`)."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import mc.agent_worktree as w
import mc.cold_review_runner as runner
import mc.merge_review_gate as gate
from mc import worktree_lifecycle as lifecycle
from mc.state import agent_sessions
from test_cold_review_wiring import (PASS_REPLY, CHANGES_REPLY, project, routes,  # noqa: F401
                                     _session, _branch)
from test_merge_review_gate import _agent, _commit, _git, _register, _store, repo  # noqa: F401

GRACE = 0.6   # how long a blocked resume is watched before it is called blocked


def _fake_spawn(routes, monkeypatch, *, history=None):
    """Real lifecycle locks, with process and history boundaries isolated."""
    # These tests import routes without server.wire(), leaving DATA_DIR=None.
    # Their mocked turn helper has no destination unless a composing revival
    # fixture supplies one. Keep that supplied row visible to claim checks.
    rows = [] if history is None else history
    monkeypatch.setattr(routes, '_load_agent_log', lambda pid: rows)
    monkeypatch.setattr(routes, '_respawn_sysprompt_args', lambda *a: ([], None))
    monkeypatch.setattr(routes, '_continuation_model', lambda *a: '')
    monkeypatch.setattr(routes, '_continuation_effort', lambda *a: '')
    monkeypatch.setattr(routes, '_session_skills', lambda *a: [])
    monkeypatch.setattr(routes, '_build_claude_flags', lambda *a, **k: [])
    monkeypatch.setattr(routes, '_resolve_claude', lambda: 'fake-claude')
    monkeypatch.setattr(routes, '_clock_wrap', lambda *a: a[2])
    monkeypatch.setattr(routes._launch_marker, 'launch_cmd', lambda cmd, *a: cmd)
    # Billing-aware continuations also pass the retained session by keyword.
    monkeypatch.setattr(routes._launch_marker, 'launch_env', lambda *a, **k: {})
    monkeypatch.setattr(routes, 'spawn_engine_proc', lambda *a, **k: SimpleNamespace(pid=12345))
    for name in ('_sysprompt_cleanup', '_hide_windows_delayed', '_register_process',
                 '_read_agent_stream', '_advance_delegation_turn',
                 '_write_usage_breakdown_turn_start_checkpoint'):
        monkeypatch.setattr(routes, name, lambda *a, **k: None)


def _record_status_at_removal(session, monkeypatch):
    seen = []
    real_remove = w.remove

    def remove(*a, **k):
        seen.append(session['status'])
        return real_remove(*a, **k)

    monkeypatch.setattr(w, 'remove', remove)
    return seen


# ── finding 1: guardian resume (_auto_dispatch_followup, no project lock) ───────

def test_guardian_resume_waits_for_the_cold_callback_window(routes, project, monkeypatch):
    tree, tip = _agent(project, 'guardianrace')
    session = _session('guardianrace')
    session.update(status='error', last_status_change_time=100.0, log_lines=[],
                   mode='A', pending_followups=['retry'], proc=None,   # the stuck-queue replay
                   _agent_cwd=str(tree))
    monkeypatch.setitem(agent_sessions, 'guardianrace', session)
    gate.record(project, _branch('guardianrace'), sha=tip, verdict='pass', cold=True)
    checked, release = threading.Event(), threading.Event()
    real_merge = w.merge_back

    def paused_merge(p, sid):
        checked.set()   # the callback has checked status and holds its exclusion
        assert release.wait(15)
        return real_merge(p, sid)

    monkeypatch.setattr(w, 'merge_back', paused_merge)
    monkeypatch.setattr(w, '_live_processes', lambda: [
        {'type': 'agent', 'pid': 12345, 'command_preview': str(tree)}])
    _fake_spawn(routes, monkeypatch)
    at_removal = _record_status_at_removal(session, monkeypatch)

    callback = threading.Thread(target=routes._worktree_cold_review_done,
                                args=(session, 0, 100.0))
    callback.start()
    assert checked.wait(15)
    guardian = threading.Thread(target=routes._auto_dispatch_followup,
                                args=(session, 'retry'))   # what the guardian calls, unlocked
    guardian.start()
    guardian.join(GRACE)
    blocked = guardian.is_alive() and session['status'] == 'error'
    release.set()   # always, so a failing run does not leave the lock held
    callback.join(30)
    assert blocked, 'guardian resumed while the callback was between its status check and removal'
    guardian.join(30)
    assert not callback.is_alive() and not guardian.is_alive()
    assert at_removal and 'running' not in at_removal   # removal ran while it was NOT running
    assert session['status'] == 'running'               # and the resume did go ahead after


def test_every_resume_entry_point_goes_through_auto_dispatch_followup(routes):
    """The guardian recovery and the stuck-followup guardian both call this one
    function, so the exclusion lives in it and not in each caller."""
    import inspect
    for fn in (routes._guardian_attempt_recovery, routes._guardian_check_session):
        assert '_auto_dispatch_followup(' in inspect.getsource(fn), fn.__name__


# ── finding 2: the removal-retry timer ────────────────────────────────────────

def test_timer_retry_holds_the_exclusion_from_status_check_through_removal(
        routes, project, monkeypatch):
    tree, tip = _agent(project, 'timerrace')
    session = _session('timerrace')
    session.update(status='completed', last_status_change_time=100.0)
    monkeypatch.setitem(agent_sessions, 'timerrace', session)
    gate.record(project, _branch('timerrace'), sha=tip, verdict='pass', cold=True)
    assert w.merge_back(project, 'timerrace')[0] == 'clean'   # merged; removal is what's left
    real_needs = runner.needs_review
    resumed = threading.Event()

    def resume_under_project_lock():
        with routes.get_manager('testproj').lock:   # how a follow-up resumes
            session.update(status='running', last_status_change_time=200.0)
        resumed.set()

    def needs_review_with_a_resume_arriving(p, b):
        # The timer is past its status check here. A follow-up arrives now.
        threading.Thread(target=resume_under_project_lock, daemon=True).start()
        resumed.wait(GRACE)
        return real_needs(p, b)

    monkeypatch.setattr(runner, 'needs_review', needs_review_with_a_resume_arriving)
    monkeypatch.setattr(w, '_live_processes', lambda: [
        {'type': 'agent', 'pid': 12345, 'command_preview': str(tree)}])
    at_removal = _record_status_at_removal(session, monkeypatch)
    routes._worktree_retry_removal(session, 1)
    assert resumed.wait(15)
    assert at_removal and 'running' not in at_removal, 'timer removed the tree of a running session'
    assert session['status'] == 'running'


def test_timer_retry_leaves_a_session_that_resumed_before_it_ran(routes, project, monkeypatch):
    tree, _ = _agent(project, 'timerskip')
    session = _session('timerskip')
    session.update(status='running', last_status_change_time=200.0)
    monkeypatch.setitem(agent_sessions, 'timerskip', session)
    at_removal = _record_status_at_removal(session, monkeypatch)
    routes._worktree_retry_removal(session, 1)
    assert at_removal == [] and Path(tree).exists()


# ── a queued guardian recovery owns the tree (Dave's call, round 3) ───────────

def test_callback_leaves_merge_and_tree_to_a_pending_recovery(routes, project, monkeypatch):
    tree, tip = _agent(project, 'pendrec')
    session = _session('pendrec')
    session.update(status='error', last_status_change_time=100.0,
                   pending_recovery_message='retry')
    monkeypatch.setitem(agent_sessions, 'pendrec', session)
    gate.record(project, _branch('pendrec'), sha=tip, verdict='pass', cold=True)
    merged = []
    real_merge = w.merge_back
    monkeypatch.setattr(w, 'merge_back', lambda *a, **k: merged.append(a) or real_merge(*a, **k))
    at_removal = _record_status_at_removal(session, monkeypatch)
    routes._worktree_cold_review_done(session, 0, 100.0)
    assert merged == [] and at_removal == []
    assert Path(tree).exists() and gate.branch_tip(project, _branch('pendrec')) == tip
    # the resumed turn ends with the message consumed: now it lands
    session.update(pending_recovery_message=None, last_status_change_time=300.0)
    routes._worktree_cold_review_done(session, 0, 300.0)
    assert len(merged) == 1 and not Path(tree).exists()


def test_timer_retry_leaves_a_pending_recovery_alone(routes, project, monkeypatch):
    tree, tip = _agent(project, 'pendtimer')
    session = _session('pendtimer')
    session.update(status='error', last_status_change_time=100.0,
                   pending_recovery_message='retry')
    monkeypatch.setitem(agent_sessions, 'pendtimer', session)
    gate.record(project, _branch('pendtimer'), sha=tip, verdict='pass', cold=True)
    assert w.merge_back(project, 'pendtimer')[0] == 'clean'
    at_removal = _record_status_at_removal(session, monkeypatch)
    routes._worktree_retry_removal(session, 1)
    assert at_removal == [] and Path(tree).exists()


# ── finding 3: a superseded review is reviewed against its own ask ────────────

def test_superseded_review_uses_pending_ask(routes, project, monkeypatch):
    tree, tip = _agent(project, 'newask')
    calls, outcomes, threads = [], [], []
    session = _session('newask')
    session.update(status='completed', last_status_change_time=100.0,
                   task='OLD-ASK: return SECOND')
    monkeypatch.setitem(agent_sessions, 'newask', session)
    real_start, real_merge = runner.start, w.merge_back

    def start(*a, **k):
        t = real_start(*a, transform=transform, model='', **k)
        if t is not None:
            threads.append(t)
        return t

    monkeypatch.setattr(runner, 'start', start)
    monkeypatch.setattr(w, 'merge_back', lambda *a, **k:
                        outcomes.append(real_merge(*a, **k)) or outcomes[-1])

    def transform(**kw):
        calls.append(kw['prompt'])
        if len(calls) == 1:
            _commit(tree, 'def one():\n    return "SECOND"\n')
            # A revived session reuses the id with a new dict and a NEW task; its
            # completion queues that ask on the same branch while review A runs.
            revived = _session('newask')
            revived.update(status='completed', last_status_change_time=200.0,
                           task='NEW-ASK: return THIRD')
            monkeypatch.setitem(agent_sessions, 'newask', revived)
            routes._worktree_merge_back_on_end(revived)
        return CHANGES_REPLY if 'NEW-ASK: return THIRD' in kw['prompt'] else PASS_REPLY

    routes._worktree_merge_back_on_end(session)
    threads[0].join(30)
    assert not threads[0].is_alive()
    assert len(calls) == 2
    assert 'NEW-ASK: return THIRD' in calls[1] and 'OLD-ASK' not in calls[1]
    assert outcomes and outcomes[0][0] == 'awaiting_review'   # the new tip's reviewer said no


def test_superseded_without_a_new_completion_keeps_the_current_ask(project, repo):
    """The tip moved but nobody queued a new ask: the running ask is still the
    right one for the new tip (an unchanged-ask regression guard)."""
    tree, _ = _agent(project, 'sameask')
    prompts = []

    def transform(**kw):
        prompts.append(kw['prompt'])
        if len(prompts) == 1:
            _commit(tree, 'def one():\n    return "SECOND"\n')
        return PASS_REPLY

    done = threading.Event()
    t = runner.start(project, _branch('sameask'), 'ONLY-ASK', done.set,
                     transform=transform, model='')
    t.join(30)
    assert done.is_set() and len(prompts) == 2
    assert all('ONLY-ASK' in p for p in prompts)


def test_stop_during_review_releases_worker(routes, project, monkeypatch):
    tree, tip = _agent(project, 'stopclean')
    session = _session('stopclean')
    session.update(status='completed', last_status_change_time=100.0)
    monkeypatch.setitem(agent_sessions, 'stopclean', session)
    threads = []
    real_start = runner.start

    def transform(**kw):
        session.update(status='stopped', last_status_change_time=200.0)
        return PASS_REPLY

    monkeypatch.setattr(runner, 'start', lambda *a, **k:
                        threads.append(real_start(*a, transform=transform, model='', **k))
                        or threads[-1])
    routes._worktree_merge_back_on_end(session)
    threads[0].join(30)
    assert not threads[0].is_alive()
    assert Path(tree).exists()
    key = 'testproj:' + _branch('stopclean')
    assert key not in runner._inflight and key not in runner._rerun
    assert gate.find('testproj', _branch('stopclean'), tip)['cold'] is True


# ── the shared exclusion itself ───────────────────────────────────────────────

def test_removal_window_excludes_a_resume_of_the_same_session_only():
    """A resume waits for a removal of ITS session. Since the lock order is
    project lock first (MC-1165 round 4) every resume of the same project also
    queues behind the window's project lock; only another project is untouched."""
    got = {}

    def resume(lock, sid, key):
        with lifecycle.resuming(lock, sid):
            got[key] = time.monotonic()

    project_lock = threading.RLock()
    with lifecycle.removal_window(project_lock, 's-a'):
        t = threading.Thread(target=resume, args=(project_lock, 's-a', 'same'))
        t.start()
        t.join(0.3)
        assert t.is_alive() and 'same' not in got          # same session waits
        sibling = threading.Thread(target=resume, args=(project_lock, 's-b', 'sibling'))
        sibling.start()
        sibling.join(0.3)
        assert sibling.is_alive() and 'sibling' not in got  # same project: the project lock
        other = threading.Thread(target=resume, args=(threading.RLock(), 's-c', 'other'))
        other.start()
        other.join(5)
        assert 'other' in got                              # another project does not
    t.join(5)
    sibling.join(5)
    assert 'same' in got and 'sibling' in got


def test_exclusion_is_reentrant_and_orders_project_lock_first():
    project_lock = threading.RLock()
    with lifecycle.removal_window(project_lock, 's-r'):
        with lifecycle.removal_window(project_lock, 's-r'):    # callback -> merge-back -> retry
            with lifecycle.resuming(project_lock, 's-r'):
                assert lifecycle.holds('s-r')
    assert not lifecycle.holds('s-r')
    # a resume already under the project lock (the reader thread's follow-up
    # drain) re-enters it and takes the session lock second, the order the window uses
    with project_lock:
        with lifecycle.resuming(project_lock, 's-r'):
            pass
