"""MC-1013 follow-up: a Hivemind claude worker runs in the worktree the hivemind
created — exactly one per worker — and never reaches master through any tree.

Live test hm_d9af73e7 (2026-09-30) showed each worker with TWO worktrees:
`_hm_spawn_worker_session` made `hm_<id>` (correct, base pinned), then the claude
dispatch hook (`server._claude_dispatch_hook`) dropped `project_path`, called
`_dispatch_agent_internal`, which planned a fresh id (the hm_ id was already in
`agent_sessions`) and ran `_maybe_isolate_worktree` again. The process ran in the
stray `clayrune/agent/<fresh>` tree; the `hm_*` branch the integrator merges had
zero commits. The earlier isolation tests stub `_hm_runtime_dispatch`, so none of
them crossed that hook. These do: real git repo, real `_hm_runtime_dispatch`,
real `ClaudeRuntime` -> `_claude_dispatch_hook` -> `_dispatch_agent_internal`;
only `subprocess.Popen` (and the threads around it) are faked.
"""
import io
import subprocess
import sys
import tempfile
import shutil
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import server  # noqa: E402  (wires blueprints)
from mc import project_sync, state  # noqa: E402
import mc.agent_worktree as _awt  # noqa: E402
from mc.blueprints import agent_routes as ar  # noqa: E402
from mc.blueprints import hivemind_routes as hm  # noqa: E402


def _git(cwd, *args):
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True,
                       stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)}: {r.stderr.strip()}')
    return r.stdout.strip()


class _Stdin:
    def write(self, s):
        pass

    def flush(self):
        pass

    def close(self):
        pass


class _FakeProc:
    def __init__(self, cwd):
        self.pid = 909091
        self.cwd = cwd
        self.stdin = _Stdin()
        self.stdout = io.StringIO('')

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


_RealThread = threading.Thread


def _is_subprocess_internal(target):
    return getattr(target, '__qualname__', '').startswith('Popen.')


class _InertThread(_RealThread):
    """Dispatch's reader/guardian threads never run (nothing touches test state
    after the assertions). `ar.threading` IS the threading module, so the patch
    is global: subprocess's own reader threads (used by the real `git` calls
    here) must keep working."""
    run_inline = False

    def __init__(self, group=None, target=None, name=None, args=(), kwargs=None, *,
                 daemon=None):
        super().__init__(group, target, name, args, kwargs or {}, daemon=daemon)
        self._mc_target, self._mc_args, self._mc_kwargs = target, args, kwargs or {}

    def start(self):
        if _is_subprocess_internal(self._mc_target):
            return super().start()
        if self.run_inline and self._mc_target:
            self._mc_target(*self._mc_args, **self._mc_kwargs)

    def is_alive(self):
        return True if not _is_subprocess_internal(self._mc_target) else super().is_alive()


class _SyncThread(_InertThread):
    """Runs its target inline, so the orchestrator's `_run` thread is observable."""
    run_inline = True


@pytest.fixture
def repo():
    d = Path(tempfile.mkdtemp(prefix='hm_hook_'))
    _git(d, 'init', '-q', '-b', 'master')
    _git(d, 'config', 'user.email', 't@t.local')
    _git(d, 'config', 'user.name', 'T')
    (d / 'a.txt').write_text('base\n', encoding='utf-8')
    (d / '.gitignore').write_text('.clayrune/\n', encoding='utf-8')
    _git(d, 'add', '.')
    _git(d, 'commit', '-q', '-m', 'base')
    yield d
    _git(d, 'worktree', 'prune')
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def env(repo, tmp_path, monkeypatch):
    from mc.delegation_delivery import DeliveryStore

    project = {'id': 'hmhook', 'project_path': str(repo), 'provider': 'claude'}
    project_sync.register(0, None, lambda p, m: None, lambda p: None,
                          lambda p, v: None, lambda: '', Path(tempfile.gettempdir()))
    _awt.register(lambda p, m: None, lambda p: None, lambda m: None)
    server._register_claude_runtime_hooks()

    monkeypatch.setitem(state.CONFIG, 'worktree_isolation_enabled', True)
    monkeypatch.setitem(state.CONFIG, 'default_provider', 'claude')
    monkeypatch.setitem(state.CONFIG, 'agent_model', '')
    monkeypatch.setitem(state.CONFIG, 'sticky_agent_settings', False)
    monkeypatch.setitem(state.CONFIG, 'auto_model_enabled', False)
    monkeypatch.setitem(state.CONFIG, 'use_streaming_agent', False)

    monkeypatch.setattr(hm, 'HIVEMIND_DIR', tmp_path / 'hiveminds')
    hm.HIVEMIND_DIR.mkdir()
    monkeypatch.setattr(hm, 'PORT', 5377)
    monkeypatch.setattr(hm, '_clayrune_universal_capabilities', lambda port=None: ['CAPS'])
    monkeypatch.setattr(hm, '_clayrune_api_reference', lambda: 'REF')
    monkeypatch.setattr(hm, '_clayrune_api_pointer_card', lambda port, pid: 'CARD')
    monkeypatch.setattr(hm, '_assert_runtime_project_generation', lambda pid, gen: gen)
    monkeypatch.setattr(hm, 'load_project', lambda pid: project)
    monkeypatch.setattr(server, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, 'load_project', lambda pid: project)
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    # A live sibling, as in the live test: it is what makes dispatch's own
    # `_maybe_isolate_worktree` create a tree instead of taking the shared one.
    monkeypatch.setattr(ar, '_live_agent_count', lambda pid: 1)
    monkeypatch.setattr(ar, '_delivery_store', DeliveryStore(tmp_path / 'delegation.db'))
    monkeypatch.setattr(ar, '_load_agent_log', lambda pid: [])
    monkeypatch.setattr(ar, '_pid_is_alive', lambda pid: True)
    monkeypatch.setattr(ar._memory_turn, 'refresh_for_turn', lambda *a, **k: {'block': ''})
    monkeypatch.setattr(ar._memory_turn, 'seed_delivered', lambda *a, **k: None)
    monkeypatch.setattr(ar._behavior_tail, 'render', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_prior_character', lambda *a, **k: None)
    monkeypatch.setattr(ar, '_resolve_character', lambda *a, **k: (None, ''))
    monkeypatch.setattr(ar, '_session_too_large', lambda *a, **k: (False, 0))
    monkeypatch.setattr(ar, '_check_context_budget', lambda *a, **k: '')
    monkeypatch.setattr(ar, '_build_agent_context', lambda *a, **k: 'context')
    monkeypatch.setattr(ar, '_dispatch_with_routing_parallel',
                        lambda p, task, context_builder=None, **k: (
                            'sonnet', 'auto', [], 'context', ''))

    spawned = []

    real_popen = subprocess.Popen

    def fake_popen(cmd, **kwargs):
        # `ar.subprocess` IS the subprocess module, so this patch is global;
        # git (the worktree code and this file's own helper) must stay real.
        if isinstance(cmd, (list, tuple)) and Path(str(cmd[0])).stem.lower() == 'git':
            return real_popen(cmd, **kwargs)
        proc = _FakeProc(kwargs.get('cwd'))
        spawned.append(proc)
        return proc

    monkeypatch.setattr(ar.subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(ar.threading, 'Thread', _InertThread)

    snapshot = dict(state.agent_sessions)
    state.agent_sessions.clear()
    try:
        yield {'project': project, 'repo': repo, 'spawned': spawned,
               'monkeypatch': monkeypatch}
    finally:
        state.agent_sessions.clear()
        state.agent_sessions.update(snapshot)


def _manifest(hivemind_id='hm1'):
    return {'id': hivemind_id, 'goal': 'g', 'title': 't', 'project_id': 'hmhook',
            'project_generation': 1,
            'config': {'worker_provider': 'claude', 'worker_model': 'sonnet',
                       'worker_effort': ''}}


def _spawn_worker(env, ws_id='ws1', hivemind_id='hm1'):
    manifest = _manifest(hivemind_id)
    ws = {'id': ws_id, 'provider': '', 'model': '', 'title': 't'}
    hm._hm_ensure_dirs(hivemind_id)
    hm._hm_save_manifest(hivemind_id, manifest)
    hm._hm_save_workstream(hivemind_id, ws_id, ws)
    sid = hm._hm_spawn_worker_session(manifest, ws, env['project'], hivemind_id, ws_id)
    return sid, ws


def _agent_trees(repo):
    root = Path(repo) / '.clayrune' / 'agents'
    return sorted(d.name for d in root.iterdir() if d.is_dir()) if root.exists() else []


def _agent_branches(repo):
    out = _git(repo, 'branch', '--list', 'clayrune/agent/*', '--format=%(refname:short)')
    return sorted(b for b in out.splitlines() if b)


def test_claude_worker_gets_exactly_one_worktree_and_runs_in_it(env):
    sid, ws = _spawn_worker(env)
    repo = env['repo']
    wt = _awt.worktree_path(env['project'], sid)

    assert sid.startswith('hm_')
    assert _agent_trees(repo) == [sid], 'a second (stray) worktree was created'
    assert _agent_branches(repo) == [f'clayrune/agent/{sid}']
    assert len(env['spawned']) == 1
    assert Path(env['spawned'][0].cwd) == wt, 'claude process did not run in the hm_ tree'

    session = state.agent_sessions[sid]
    assert Path(session['_agent_cwd']) == wt
    assert session['_worktree_isolated'] is True
    assert session['session_id'] == sid
    assert ws['worktree_session_id'] == sid


def test_worker_commits_land_on_the_hm_branch(env):
    sid, _ = _spawn_worker(env)
    repo = env['repo']
    cwd = env['spawned'][0].cwd
    base = _git(repo, 'rev-parse', 'master')

    Path(cwd, 'alpha.md').write_text('worker output\n', encoding='utf-8')
    _git(cwd, 'add', 'alpha.md')
    _git(cwd, '-c', 'user.email=t@t.local', '-c', 'user.name=T', 'commit', '-q', '-m', 'alpha')

    hm_tip = _git(repo, 'rev-parse', f'clayrune/agent/{sid}')
    assert hm_tip != base
    assert _git(repo, 'rev-list', '--count', f'{base}..{hm_tip}') == '1'
    assert _agent_branches(repo) == [f'clayrune/agent/{sid}']


def test_worker_never_merges_onto_master_via_session_end_or_gc(env):
    sid, _ = _spawn_worker(env)
    repo = env['repo']
    cwd = env['spawned'][0].cwd
    Path(cwd, 'alpha.md').write_text('worker output\n', encoding='utf-8')
    _git(cwd, 'add', 'alpha.md')
    _git(cwd, '-c', 'user.email=t@t.local', '-c', 'user.name=T', 'commit', '-q', '-m', 'alpha')
    master_before = _git(repo, 'rev-parse', 'master')

    ar._worktree_merge_back_on_end(state.agent_sessions[sid])
    _awt.gc_stale(env['project'], live_session_ids=set())

    assert _git(repo, 'rev-parse', 'master') == master_before
    assert not (Path(repo) / 'alpha.md').exists()
    # Work preserved, not deleted, for the integrator.
    assert _agent_trees(repo) == [sid]


def test_orchestrator_gets_no_isolated_tree_even_with_a_live_sibling(env):
    """The orchestrator writes only under the hivemind dir. With another agent
    live (`_live_agent_count` >= 1) dispatch used to give it its own worktree."""
    mp = env['monkeypatch']
    mp.setattr(ar.threading, 'Thread', _SyncThread)
    mp.setattr(hm.threading, 'Thread', _SyncThread)
    hm._hm_ensure_dirs('hm1')
    hm._hm_save_manifest('hm1', _manifest())

    sid = hm._hm_dispatch_orchestrator('hm1', 'synthesize')

    assert (sid or '').startswith('hm_orch_')
    assert _agent_trees(env['repo']) == []
    assert _agent_branches(env['repo']) == []
    assert len(env['spawned']) == 1
    assert Path(env['spawned'][0].cwd) == Path(env['repo'])
    assert state.agent_sessions[sid]['_worktree_isolated'] is False
