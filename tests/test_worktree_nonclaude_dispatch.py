"""Vendor-parity GAP 1 (docs/VENDOR_HARNESS_MATRIX.md): non-claude runtimes
never got a worktree.

`_dispatch_agent_internal`'s `if provider_name != 'claude': return
_dispatch_via_runtime(...)` branch used to return BEFORE the claude path's own
`_maybe_isolate_worktree` decision ever ran, so every Codex/Gemini/Qwen agent
always dispatched into the shared main checkout even with a concurrent
sibling isolated in its own tree. The fix makes the SAME decision (same
gates, same helper) in the non-claude branch and threads the result into
`_dispatch_via_runtime` via three new kwargs: `agent_cwd` (the subprocess
cwd), `isolated` (stamped on the session dict for merge-back), and
`planned_session_id` (forces `_dispatch_via_runtime`'s own id mint to match
whatever id the worktree was actually created under).

These tests pin the DECISION made at the `_dispatch_agent_internal` call
site. `_dispatch_via_runtime` is stubbed out (its own dispatch plumbing is
covered elsewhere, e.g. test_runtime_completion_log.py) so each test asserts
exactly the kwargs it was handed. `_agent_worktree.create` is stubbed too --
its real git engine is proven by test_agent_worktree.py / (the claude-path
decision layer) test_worktree_dispatch.py; this file is about the WIRING
that used to be entirely missing for non-claude providers, not the engine.
"""
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402
import mc.agent_worktree as _awt  # noqa: E402
from mc import project_sync  # noqa: E402
from mc import state  # noqa: E402
from mc.blueprints import agent_routes as ar  # noqa: E402


class _FakeNonClaudeRuntime:
    """Registered under a name outside the real vendor catalog so a test
    provider_override='fakeprov' resolves without touching qwen/gemini/codex
    setup at all -- only the worktree DECISION is under test here."""

    name = 'fakeprov'
    display_name = 'FakeProv'

    def model_supported(self, model):
        return False

    def capabilities(self):
        return type('Caps', (), {'context_file_name': 'FAKEPROV.md'})()

    def transcript_path(self, project_path, session_id):
        return None


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    project_sync.register(0, None, lambda p, m: None, lambda p: None,
                          lambda p, v: None, lambda: '',
                          Path(tempfile.gettempdir()))
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)
    _awt.register(lambda p, m: None, lambda p: None, lambda m: None)
    saved_runtimes = dict(agent_runtime_mod._RUNTIMES)
    agent_runtime_mod.register_runtime(_FakeNonClaudeRuntime())
    snap = dict(state.agent_sessions)
    state.agent_sessions.clear()
    prev = state.CONFIG.get('worktree_isolation_enabled')

    monkeypatch.setattr(ar, '_resolve_character', lambda *a, **k: (None, ''))
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_vendor_context_sync',
                        type('X', (), {'sync_vendor_context_file': staticmethod(
                            lambda *a, **k: None)}))

    # Stub the worktree ENGINE, not just its call site: this file proves the
    # decision wiring (agent_cwd/isolated/planned_session_id threaded through
    # to _dispatch_via_runtime), which is orthogonal to whether `git worktree
    # add` itself succeeds on any given host.
    def _fake_create(project, session_id):
        path = Path(project['project_path']) / '.clayrune' / 'agents' / session_id
        path.mkdir(parents=True, exist_ok=True)
        return True, str(path)
    monkeypatch.setattr(ar._agent_worktree, 'create', _fake_create)

    yield

    state.agent_sessions.clear()
    state.agent_sessions.update(snap)
    if prev is None:
        state.CONFIG.pop('worktree_isolation_enabled', None)
    else:
        state.CONFIG['worktree_isolation_enabled'] = prev
    agent_runtime_mod._RUNTIMES.clear()
    agent_runtime_mod._RUNTIMES.update(saved_runtimes)


@pytest.fixture
def project(tmp_path):
    d = tmp_path / 'proj'
    d.mkdir()
    return {'id': 'nc_proj', 'project_path': str(d)}


def _add_live_agent(project_id, sid='sibling1'):
    state.agent_sessions[sid] = {
        'status': 'running', 'project_id': project_id,
        'task': 'sibling work', 'session_id': sid,
    }
    ar.get_manager(project_id).add_session(sid)


def _dispatch_and_capture(project, monkeypatch, **dispatch_kwargs):
    calls = []
    monkeypatch.setattr(ar, 'load_project',
                        lambda pid: project if pid == project['id'] else None)
    monkeypatch.setattr(
        ar, '_dispatch_via_runtime',
        lambda p, task, **k: calls.append(k) or 'stub-sid')
    ar._dispatch_agent_internal(
        project['id'], 'do the thing', provider_override='fakeprov',
        **dispatch_kwargs)
    assert len(calls) == 1
    return calls[0]


class TestWorktreeGrantedOnDispatch:

    def test_nonclaude_dispatch_gets_isolated_worktree_when_enabled(
            self, project, monkeypatch):
        """The gap itself: a 2nd concurrent non-claude agent must land in its
        own worktree, not the shared checkout."""
        state.CONFIG['worktree_isolation_enabled'] = True
        _add_live_agent(project['id'])
        kwargs = _dispatch_and_capture(project, monkeypatch)
        assert kwargs['isolated'] is True
        assert kwargs['agent_cwd'] != project['project_path']
        assert Path(kwargs['agent_cwd']).is_dir()
        assert kwargs['planned_session_id']
        assert Path(kwargs['agent_cwd']).name == kwargs['planned_session_id']

    def test_nonclaude_dispatch_shared_tree_when_flag_disabled(
            self, project, monkeypatch):
        state.CONFIG['worktree_isolation_enabled'] = False
        _add_live_agent(project['id'])
        kwargs = _dispatch_and_capture(project, monkeypatch)
        assert kwargs['isolated'] is False
        assert kwargs['agent_cwd'] == project['project_path']

    def test_nonclaude_dispatch_shared_tree_when_project_opted_out(
            self, project, monkeypatch):
        state.CONFIG['worktree_isolation_enabled'] = True
        project['worktree_isolation'] = False
        _add_live_agent(project['id'])
        kwargs = _dispatch_and_capture(project, monkeypatch)
        assert kwargs['isolated'] is False
        assert kwargs['agent_cwd'] == project['project_path']

    def test_nonclaude_dispatch_shared_tree_as_first_agent(
            self, project, monkeypatch):
        """No sibling yet -- the common case stays on the identical path it
        always had, same containment guarantee as the claude decision."""
        state.CONFIG['worktree_isolation_enabled'] = True
        kwargs = _dispatch_and_capture(project, monkeypatch)
        assert kwargs['isolated'] is False
        assert kwargs['agent_cwd'] == project['project_path']


class TestResumeReturnsToItsOwnTree:

    def test_nonclaude_resume_reuses_its_recorded_worktree(
            self, project, monkeypatch):
        """A resume must go back to the tree its transcript lives in, not be
        treated as a fresh isolation decision (which could pick a DIFFERENT
        worktree, or the shared tree, mid-conversation)."""
        state.CONFIG['worktree_isolation_enabled'] = True
        recorded_tree = str(Path(project['project_path']) / '.clayrune' / 'agents' / 'old1')
        Path(recorded_tree).mkdir(parents=True)
        monkeypatch.setattr(ar, '_resume_cwd_for',
                            lambda pp, sid, provider: recorded_tree)

        kwargs = _dispatch_and_capture(project, monkeypatch, resume_id='native-xyz')

        assert kwargs['agent_cwd'] == recorded_tree
        assert kwargs['isolated'] is False

    def test_nonclaude_resume_with_no_recorded_tree_makes_a_fresh_decision(
            self, project, monkeypatch):
        """No transcript found in any tree (e.g. the runtime keeps none) --
        falls through to an ordinary fresh-dispatch isolation decision rather
        than silently defaulting to the shared tree."""
        state.CONFIG['worktree_isolation_enabled'] = True
        _add_live_agent(project['id'])
        monkeypatch.setattr(ar, '_resume_cwd_for', lambda pp, sid, provider: None)

        kwargs = _dispatch_and_capture(project, monkeypatch, resume_id='native-xyz')

        assert kwargs['isolated'] is True
        assert kwargs['agent_cwd'] != project['project_path']
