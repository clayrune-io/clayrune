"""Containment tests for worktree-isolation DISPATCH wiring (b264200a).

test_agent_worktree.py proves the isolation engine. This file proves the
*decision* layer — `_maybe_isolate_worktree()` — which is what actually makes
the feature safe to enable on the live product:

  • flag OFF                → shared tree, byte-identical to today
  • flag ON, first agent    → shared tree (nothing to collide with)
  • flag ON, 2nd concurrent → isolated worktree
  • incognito / opt-out     → shared tree
  • any failure             → shared tree, never an exception

The load-bearing claim being verified is CONTAINMENT: a project running one
agent — the overwhelmingly common case — takes exactly the code path it always
has. That is why this can ship enabled without risking normal use.

Determinism: patches mc.blueprints.agent_routes.* ONLY (the Phase-0 test-port
rule). agent_sessions is snapshot/cleared/restored in place (the 1.8
cross-test-pollution lesson).
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import project_sync  # noqa: E402
import agent_worktree as _awt  # noqa: E402
from mc import state  # noqa: E402
from mc.blueprints import agent_routes as ar  # noqa: E402


def _git(cwd, *args):
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)}: {r.stderr.strip()}')
    return r.stdout.strip()


@pytest.fixture(autouse=True)
def _clean_state():
    """Snapshot/clear/restore the shared session registry in place."""
    project_sync.register(0, None, lambda p, m: None, lambda p: None,
                          lambda p, v: None, lambda: '',
                          Path(tempfile.gettempdir()))
    _awt.register(lambda p, m: None, lambda p: None, lambda m: None)
    snap = dict(state.agent_sessions)
    state.agent_sessions.clear()
    prev = state.CONFIG.get('worktree_isolation_enabled')
    yield
    state.agent_sessions.clear()
    state.agent_sessions.update(snap)
    if prev is None:
        state.CONFIG.pop('worktree_isolation_enabled', None)
    else:
        state.CONFIG['worktree_isolation_enabled'] = prev


@pytest.fixture
def project():
    d = Path(tempfile.mkdtemp(prefix='wt_disp_'))
    _git(d, 'init', '-q', '-b', 'master')
    _git(d, 'config', 'user.email', 't@t.local')
    _git(d, 'config', 'user.name', 'T')
    (d / 'f.txt').write_text('x', encoding='utf-8')
    _git(d, 'add', '.'); _git(d, 'commit', '-q', '-m', 'base')
    yield {'id': 'dispproj', 'project_path': str(d)}
    shutil.rmtree(d, ignore_errors=True)


def _add_live_agent(project_id, sid='existing1'):
    """Register a running sibling so the next dispatch is the 2nd concurrent."""
    state.agent_sessions[sid] = {
        'status': 'running', 'project_id': project_id,
        'task': 'sibling work', 'session_id': sid,
    }
    ar.get_manager(project_id).add_session(sid)


# ── Containment ─────────────────────────────────────────────────────────────

def test_flag_off_always_uses_shared_tree(project):
    """Default state. Must be byte-identical to pre-feature behavior even with
    a live sibling present."""
    state.CONFIG['worktree_isolation_enabled'] = False
    _add_live_agent(project['id'])
    cwd, isolated = ar._maybe_isolate_worktree(project, 'newsess1')
    assert cwd == project['project_path']
    assert isolated is False


def test_first_agent_uses_shared_tree(project):
    """Flag ON but no sibling — nothing to collide with, so no worktree. This
    is the common case and it must stay on the original path."""
    state.CONFIG['worktree_isolation_enabled'] = True
    cwd, isolated = ar._maybe_isolate_worktree(project, 'solo1')
    assert cwd == project['project_path']
    assert isolated is False
    assert not (Path(project['project_path']) / '.clayrune' / 'agents').exists()


def test_second_concurrent_agent_is_isolated(project):
    """The case the feature exists for."""
    state.CONFIG['worktree_isolation_enabled'] = True
    _add_live_agent(project['id'])
    cwd, isolated = ar._maybe_isolate_worktree(project, 'second1')
    try:
        assert isolated is True
        assert cwd != project['project_path']
        assert Path(cwd).is_dir()
        assert Path(cwd).name == 'second1'
    finally:
        _awt.remove(project, 'second1', delete_branch=True, force=True)


def test_incognito_never_isolated(project):
    """Incognito must leave no artifacts on disk."""
    state.CONFIG['worktree_isolation_enabled'] = True
    _add_live_agent(project['id'])
    cwd, isolated = ar._maybe_isolate_worktree(project, 'incog1', incognito=True)
    assert cwd == project['project_path'] and isolated is False


def test_per_project_opt_out_respected(project):
    state.CONFIG['worktree_isolation_enabled'] = True
    project['worktree_isolation'] = False
    _add_live_agent(project['id'])
    cwd, isolated = ar._maybe_isolate_worktree(project, 'optout1')
    assert cwd == project['project_path'] and isolated is False


# ── Fail-safe ───────────────────────────────────────────────────────────────

def test_non_git_project_falls_back_not_raises():
    """A non-git project must degrade to the shared tree, never break dispatch."""
    state.CONFIG['worktree_isolation_enabled'] = True
    d = Path(tempfile.mkdtemp(prefix='wt_nogit_'))
    try:
        proj = {'id': 'nogit', 'project_path': str(d)}
        _add_live_agent('nogit')
        cwd, isolated = ar._maybe_isolate_worktree(proj, 'x1')
        assert cwd == str(d) and isolated is False
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_worktree_creation_failure_falls_back(project, monkeypatch):
    """If create() raises for any reason, dispatch still proceeds on the shared
    tree. Isolation must never be able to break a dispatch."""
    state.CONFIG['worktree_isolation_enabled'] = True
    _add_live_agent(project['id'])

    def _boom(*a, **k):
        raise RuntimeError('simulated git failure')

    monkeypatch.setattr(ar._agent_worktree, 'create', _boom)
    cwd, isolated = ar._maybe_isolate_worktree(project, 'fail1')
    assert cwd == project['project_path'] and isolated is False


def test_finished_siblings_do_not_trigger_isolation(project):
    """Only RUNNING/IDLE siblings count — a completed session is not a collision
    risk and must not cause needless worktree churn."""
    state.CONFIG['worktree_isolation_enabled'] = True
    state.agent_sessions['done1'] = {
        'status': 'completed', 'project_id': project['id'],
        'task': 'old', 'session_id': 'done1',
    }
    ar.get_manager(project['id']).add_session('done1')
    cwd, isolated = ar._maybe_isolate_worktree(project, 'after1')
    assert cwd == project['project_path'] and isolated is False


def test_housekeeping_siblings_do_not_trigger_isolation(project):
    """Housekeeping sessions (scribe/condense) don't edit source."""
    state.CONFIG['worktree_isolation_enabled'] = True
    state.agent_sessions['hk1'] = {
        'status': 'running', 'project_id': project['id'], 'housekeeping': True,
        'task': 'scribe', 'session_id': 'hk1',
    }
    ar.get_manager(project['id']).add_session('hk1')
    cwd, isolated = ar._maybe_isolate_worktree(project, 'after2')
    assert cwd == project['project_path'] and isolated is False
