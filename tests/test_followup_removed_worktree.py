"""MC-1081: a follow-up into a session whose worktree was removed after a
no-commit run must run in a valid cwd instead of failing with WinError 267.

Incident 2026-10-07 (Sol_Tobin b4e0ad89dcc0): the run stopped to ask a scope
question with nothing committed, `_worktree_merge_back_on_end` saw
'clean'/'nothing' and deleted the worktree + branch, and the next follow-up
handed the deleted `_agent_cwd` to the runtime's Popen — `[codex followup
error: [WinError 267] The directory name is invalid]`, status `error`.

Everything here is real except the vendor runtime: a throwaway git repo, the
real `agent_worktree.create/merge_back/remove`, the real end-hook, and the real
`/agent/followup` route driven through the Flask test client. The fake runtime
does what the Codex runtime does with the handle — spawn a process with
`cwd=handle.project_path` — so a stale path fails exactly the way it did live.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mc.agent_runtime as agent_runtime_mod  # noqa: E402
import mc.agent_worktree as aw  # noqa: E402
import mc.agent_worktree_followup as fu  # noqa: E402
from mc import isolated_launch  # noqa: E402
from mc import project_sync  # noqa: E402
from mc import worktree_lifecycle as lifecycle  # noqa: E402


def _git(cwd, *args):
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)}: {r.stderr.strip()}')
    return r.stdout.strip()


@pytest.fixture
def repo():
    d = Path(tempfile.mkdtemp(prefix='wt_followup_'))
    _git(d, 'init', '-q', '-b', 'master')
    _git(d, 'config', 'user.email', 't@t.local')
    _git(d, 'config', 'user.name', 'T')
    (d / 'app.py').write_text('x = 1\n', encoding='utf-8')
    (d / '.gitignore').write_text('.clayrune/\n_scratch/\n', encoding='utf-8')
    _git(d, 'add', '.')
    _git(d, 'commit', '-q', '-m', 'baseline')
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(autouse=True)
def _register_worktree_engine():
    project_sync.register(0, None, lambda p, m: None, lambda p: None,
                          lambda p, v: None, lambda: '',
                          Path(tempfile.gettempdir()))
    aw.register(lambda p, m: None, lambda p: None, lambda m: None)


def _isolated_session(project, sid):
    ok, path = aw.create(project, sid)
    assert ok, path
    return {
        'session_id': sid, 'project_id': project['id'], 'provider': 'fakeprov',
        'mode': 'A', 'status': 'idle', 'log_lines': [], 'task': 'scope question',
        '_agent_cwd': path, '_worktree_isolated': True,
    }


# ── module-level policy ─────────────────────────────────────────────────────
# An isolated session's cwd is chosen inside the session's lifecycle exclusion
# (MC-1165 round 4), so these tests open it the way a resume path does.

def _resolve(sess, project, repo, **kw):
    with lifecycle.resuming(threading.RLock(), sess['session_id']):
        return fu.resolve_followup_cwd(sess, project, str(repo), **kw)


def test_existing_worktree_is_returned_untouched(repo):
    project = {'id': 'p1', 'project_path': str(repo)}
    sess = _isolated_session(project, 'aaaa1111bbbb')
    cwd, note = _resolve(sess, project, repo)
    assert (cwd, note) == (sess['_agent_cwd'], '')
    assert Path(cwd).is_dir()


def test_shared_tree_session_is_returned_untouched(repo):
    project = {'id': 'p1', 'project_path': str(repo)}
    sess = {'session_id': 'cccc2222dddd', '_agent_cwd': str(repo)}
    assert fu.resolve_followup_cwd(sess, project, str(repo)) == (str(repo), '')
    assert fu.resolve_followup_cwd({'session_id': 'x'}, project, str(repo)) == (str(repo), '')


def test_removed_worktree_is_recreated_at_the_same_path(repo):
    project = {'id': 'p1', 'project_path': str(repo)}
    sess = _isolated_session(project, 'eeee3333ffff')
    old = sess['_agent_cwd']
    ok, msg = aw.remove(project, 'eeee3333ffff', delete_branch=True)
    assert ok and not Path(old).exists(), msg

    cwd, note = _resolve(sess, project, repo)

    assert cwd == old and Path(cwd).is_dir()
    assert (Path(cwd) / '.git').exists()
    assert sess['_agent_cwd'] == old and sess['_worktree_isolated'] is True
    assert 'recreated' in note and old in note


def test_recreate_failure_refuses_and_keeps_the_session_isolated(repo):
    project = {'id': 'p1', 'project_path': str(repo)}
    sess = {'session_id': 'gggg4444hhhh', '_agent_cwd': str(repo / 'gone'),
            '_worktree_isolated': True}
    with pytest.raises(isolated_launch.LaunchRefused, match='disk full'):
        _resolve(sess, project, repo, create=lambda p, s: (False, 'disk full'))
    # No silent flip to the shared checkout: still isolated, cwd unchanged.
    assert sess['_worktree_isolated'] is True
    assert sess['_agent_cwd'] == str(repo / 'gone')
    assert '_cwd_moved_from' not in sess


def test_create_raising_is_contained_as_a_refusal(repo):
    project = {'id': 'p1', 'project_path': str(repo)}
    sess = {'session_id': 'iiii5555jjjj', '_agent_cwd': str(repo / 'gone'),
            '_worktree_isolated': True}

    def boom(p, s):
        raise OSError('nope')
    with pytest.raises(isolated_launch.LaunchRefused, match='nope'):
        _resolve(sess, project, repo, create=boom)
    assert sess['_worktree_isolated'] is True


def test_isolated_cwd_outside_the_exclusion_is_refused(repo):
    project = {'id': 'p1', 'project_path': str(repo)}
    sess = _isolated_session(project, 'mmmm7777nnnn')
    with pytest.raises(isolated_launch.LaunchRefused, match='outside the lifecycle'):
        fu.resolve_followup_cwd(sess, project, str(repo))


def test_non_isolated_session_with_a_gone_directory_still_falls_back(repo):
    project = {'id': 'p1', 'project_path': str(repo)}
    sess = {'session_id': 'oooo8888pppp', '_agent_cwd': str(repo / 'gone')}
    cwd, note = fu.resolve_followup_cwd(sess, project, str(repo))
    assert cwd == str(repo) and 'project tree' in note
    assert sess['_cwd_moved_from'] == str(repo / 'gone')


# ── the real route: end a no-commit run, then send a follow-up ──────────────

class _SpawningRuntime:
    """Stand-in for CodexRuntime.write_followup's one cwd-sensitive act:
    `subprocess.Popen(..., cwd=handle.project_path)`. A stale path raises the
    same NotADirectoryError/WinError 267 the live session logged."""
    name = 'fakeprov'
    display_name = 'FakeProv'
    spawned_in: list = []

    def model_supported(self, model):
        return False

    def capabilities(self):
        return type('Caps', (), {'context_file_name': 'FAKEPROV.md'})()

    def transcript_path(self, project_path, session_id):
        return None

    def write_followup(self, handle, message):
        proc = subprocess.Popen([sys.executable, '-c', 'pass'], cwd=handle.project_path)
        proc.wait()
        type(self).spawned_in.append(handle.project_path)
        return True


@pytest.fixture
def client(tmp_path, monkeypatch):
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state as mc_state
    from mc.blueprints import local_auth as la

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    snap = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()
    saved = dict(agent_runtime_mod._RUNTIMES)
    _SpawningRuntime.spawned_in = []
    agent_runtime_mod.register_runtime(_SpawningRuntime())
    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(snap)
        agent_runtime_mod._RUNTIMES.clear()
        agent_runtime_mod._RUNTIMES.update(saved)


def test_followup_after_no_commit_run_runs_in_a_valid_cwd(client, repo, monkeypatch):
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar

    project = {'id': 'p1', 'project_path': str(repo)}
    monkeypatch.setattr(ar, 'load_project', lambda pid: project if pid == 'p1' else None)
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_fresh_context_for', lambda *a, **k: 'ctx')
    monkeypatch.setattr(ar, '_mode_a_token_rollover', lambda cwd, pid, sid, sess, prov, msg: msg)
    monkeypatch.setattr(ar, '_bridge_images_for_blind_model', lambda msg, *a, **k: msg)

    sess = _isolated_session(project, 'kkkk6666llll')
    sid = sess['session_id']
    wt = Path(sess['_agent_cwd'])
    mc_state.agent_sessions[sid] = sess
    ar.get_manager('p1').add_session(sid)

    # The run ends having committed nothing — the exact incident precondition.
    ar._worktree_merge_back_on_end(sess)
    assert not wt.exists(), 'precondition: a no-commit end removes the worktree'

    resp = client.post('/api/project/p1/agent/followup',
                       json={'session_id': sid, 'message': 'go with option A'},
                       environ_base={'REMOTE_ADDR': '127.0.0.1'})

    assert resp.status_code == 200, resp.get_data(as_text=True)
    final = mc_state.agent_sessions[sid]
    log = ' '.join(str(l) for l in final['log_lines'])
    assert 'followup error' not in log, log
    assert final['status'] != 'error'
    assert _SpawningRuntime.spawned_in == [str(wt)]  # ran, in the recreated tree
    assert wt.is_dir()
    assert 'recreated' in log                        # and the agent log says so
