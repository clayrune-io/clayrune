"""Hivemind worker worktree isolation + serial integration (backlog e3c0824e).

PART 1: `_hm_spawn_worker_session` routes through the same
`_maybe_isolate_worktree` dispatched agents use, so a worker in a git project
runs in its own worktree on its own branch; a non-git project (or isolation
off) keeps the shared tree.

PART 2: `mc.hivemind_integration.integrate` merges finished workstreams one at
a time into `hivemind/<id>` off the pinned base commit, runs each workstream's
named smokes, stops at the first conflict/failed smoke, and never touches
master.

Real git repos in temp dirs; only the runtime dispatch (and, where noted, the
smoke runner / thread) is faked.
"""
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from mc import project_sync, state  # noqa: E402
import mc.agent_worktree as _awt  # noqa: E402
import mc.hivemind_integration as hi  # noqa: E402
from mc.blueprints import hivemind_routes as hm  # noqa: E402


def _git(cwd, *args):
    r = subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f'git {" ".join(args)}: {r.stderr.strip()}')
    return r.stdout.strip()


@pytest.fixture(autouse=True)
def _register():
    project_sync.register(0, None, lambda p, m: None, lambda p: None,
                          lambda p, v: None, lambda: '',
                          Path(tempfile.gettempdir()))
    _awt.register(lambda p, m: None, lambda p: None, lambda m: None)


@pytest.fixture
def repo():
    d = Path(tempfile.mkdtemp(prefix='hm_iso_'))
    _git(d, 'init', '-q', '-b', 'master')
    _git(d, 'config', 'user.email', 't@t.local')
    _git(d, 'config', 'user.name', 'T')
    for n in ('a.txt', 'b.txt', 'c.txt', 'd.txt'):
        (d / n).write_text('base\n', encoding='utf-8')
    (d / '.gitignore').write_text('.clayrune/\n', encoding='utf-8')
    _git(d, 'add', '.')
    _git(d, 'commit', '-q', '-m', 'base')
    yield d
    # Worktrees first (junction-free here), then the repo.
    _git(d, 'worktree', 'prune')
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def project(repo):
    return {'id': 'hmproj', 'project_path': str(repo)}


@pytest.fixture
def isolation_on(monkeypatch):
    from mc.blueprints import agent_routes as ar
    monkeypatch.setattr(ar, '_log_agent_activity', lambda *a, **k: None)
    prev = state.CONFIG.get('worktree_isolation_enabled')
    state.CONFIG['worktree_isolation_enabled'] = True
    yield
    if prev is None:
        state.CONFIG.pop('worktree_isolation_enabled', None)
    else:
        state.CONFIG['worktree_isolation_enabled'] = prev


@pytest.fixture
def hm_env(monkeypatch, tmp_path):
    """Sandboxed hivemind dir + stubs; `_hm_runtime_dispatch` records."""
    from mc import agent_runtime
    monkeypatch.setattr(hm, 'HIVEMIND_DIR', tmp_path / 'hiveminds')
    hm.HIVEMIND_DIR.mkdir()
    monkeypatch.setattr(hm, 'PORT', 5377)
    monkeypatch.setattr(hm, '_clayrune_universal_capabilities',
                        lambda port=None: ['CAPS'])
    monkeypatch.setattr(hm, '_clayrune_api_reference', lambda: 'REF')
    monkeypatch.setattr(hm, '_clayrune_api_pointer_card', lambda port, pid: 'CARD')
    monkeypatch.setitem(state.CONFIG, 'default_provider', 'claude')
    monkeypatch.setitem(state.CONFIG, 'agent_model', '')
    calls = []
    monkeypatch.setattr(hm, '_hm_runtime_dispatch',
                        lambda **kw: calls.append(kw) or kw['session_id'])
    return calls


def _spawn(project, ws_id='ws1', hivemind_id='hm1'):
    manifest = {'id': hivemind_id, 'goal': 'g', 'title': 't', 'project_generation': 1,
                'config': {'worker_provider': 'claude', 'worker_model': 'sonnet',
                           'worker_effort': ''}}
    ws = {'id': ws_id, 'provider': '', 'model': '', 'title': 't'}
    hm._hm_ensure_dirs(hivemind_id)
    hm._hm_save_manifest(hivemind_id, manifest)
    hm._hm_save_workstream(hivemind_id, ws_id, ws)
    sid = hm._hm_spawn_worker_session(manifest, ws, project, hivemind_id, ws_id)
    return sid, ws


# ── PART 1: spawn ───────────────────────────────────────────────────────────

def test_worker_in_git_project_gets_worktree_cwd_and_branch(hm_env, project, isolation_on):
    sid, ws = _spawn(project)
    call = hm_env[0]
    wt = _awt.worktree_path(project, sid)
    assert sid.startswith('hm_')
    assert wt is not None and wt.is_dir()
    assert call['project_path'] == str(wt)              # runtime cwd = the worktree
    assert call['project_path'] != project['project_path']
    assert call['session_dict']['_agent_cwd'] == str(wt)
    assert call['session_dict']['_worktree_isolated'] is True
    assert _git(wt, 'rev-parse', '--abbrev-ref', 'HEAD') == f'clayrune/agent/{sid}'
    assert ws['worktree_session_id'] == sid
    assert ws['worktree_branch'] == f'clayrune/agent/{sid}'
    assert ws['base_commit'] == _git(project['project_path'], 'rev-parse', 'master')
    assert 'Commit your changes' in call['task']


def test_first_worker_is_isolated_even_with_no_live_agents(hm_env, project, isolation_on):
    """Housekeeping sessions aren't counted by `_live_agent_count`, so the
    'first agent' gate must not apply to workers."""
    sid, _ = _spawn(project)
    assert _awt.worktree_path(project, sid).is_dir()


def test_all_workers_branch_from_the_pinned_base(hm_env, project, isolation_on):
    _sid1, ws1 = _spawn(project, 'ws1')
    base = _git(project['project_path'], 'rev-parse', 'master')
    # master moves while the hivemind runs
    repo = Path(project['project_path'])
    (repo / 'moved.txt').write_text('x', encoding='utf-8')
    _git(repo, 'add', 'moved.txt')
    _git(repo, 'commit', '-q', '-m', 'master moved')
    _sid2, ws2 = _spawn(project, 'ws2')
    assert ws1['base_commit'] == base
    assert ws2['base_commit'] == base                   # not the moved HEAD
    assert hm._hm_worktree_base('hm1') == base


def test_non_git_project_keeps_project_path(hm_env, isolation_on, tmp_path):
    plain = tmp_path / 'plain'
    plain.mkdir()
    project = {'id': 'plainproj', 'project_path': str(plain)}
    sid, ws = _spawn(project)
    call = hm_env[0]
    assert call['project_path'] == str(plain)
    assert call['session_dict']['_worktree_isolated'] is False
    assert 'worktree_session_id' not in ws
    assert 'Commit your changes' not in call['task']
    assert not (plain / '.clayrune').exists()


def test_isolation_flag_off_keeps_project_path(hm_env, project):
    prev = state.CONFIG.get('worktree_isolation_enabled')
    state.CONFIG['worktree_isolation_enabled'] = False
    try:
        _sid, ws = _spawn(project)
    finally:
        if prev is None:
            state.CONFIG.pop('worktree_isolation_enabled', None)
        else:
            state.CONFIG['worktree_isolation_enabled'] = prev
    assert hm_env[0]['project_path'] == project['project_path']
    assert 'worktree_session_id' not in ws


def test_per_project_opt_out_keeps_project_path(hm_env, project, isolation_on):
    project['worktree_isolation'] = False
    _sid, ws = _spawn(project)
    assert hm_env[0]['project_path'] == project['project_path']
    assert 'worktree_session_id' not in ws


# ── worker branches never reach master through the existing cleanup ─────────

def _commit_in(wt, name, text, msg='work'):
    Path(wt, name).write_text(text, encoding='utf-8')
    _git(wt, 'add', name)
    _git(wt, 'commit', '-q', '-m', msg)


def test_merge_back_and_gc_never_land_a_hivemind_worker_on_master(project):
    ok, path = _awt.create(project, 'hm_deadbeef')
    assert ok
    _commit_in(path, 'a.txt', 'worker\n')
    master_before = _git(project['project_path'], 'rev-parse', 'master')
    status, detail = _awt.merge_back(project, 'hm_deadbeef')
    assert status == 'skipped' and 'integration' in detail
    out = _awt.gc_stale(project, live_session_ids=[])
    assert out['merged'] == 0
    assert 'hm_deadbeef' in out['preserved']            # work kept, not deleted
    assert _git(project['project_path'], 'rev-parse', 'master') == master_before
    assert Path(path).is_dir()


def test_dispatched_agent_merge_back_is_unchanged(project):
    """Negative control: a bare-hex dispatched agent still merges back."""
    ok, path = _awt.create(project, 'abcdef123456')
    assert ok
    _commit_in(path, 'a.txt', 'agent\n')
    status, _ = _awt.merge_back(project, 'abcdef123456')
    assert status == 'clean'


# ── PART 2: integration ─────────────────────────────────────────────────────

def _worker(project, hivemind_id, ws_id, sid, files, smokes=None, completed_at='1'):
    """A finished workstream whose worker committed `files` on its branch."""
    ok, path = _awt.create(project, sid)
    assert ok
    for name, text in files.items():
        _commit_in(path, name, text, f'{ws_id} work')
    return {'id': ws_id, 'status': 'completed', 'completed_at': completed_at,
            'worktree_session_id': sid, 'worktree_branch': f'clayrune/agent/{sid}',
            'smokes': smokes or []}


def test_two_merge_serially_and_a_conflict_stops_the_run(project):
    repo = project['project_path']
    base = _git(repo, 'rev-parse', 'master')
    wss = [
        _worker(project, 'hmX', 'ws1', 'hm_00000001', {'a.txt': 'from ws1\n'}, completed_at='1'),
        _worker(project, 'hmX', 'ws2', 'hm_00000002', {'b.txt': 'from ws2\n'}, completed_at='2'),
        _worker(project, 'hmX', 'ws3', 'hm_00000003', {'a.txt': 'from ws3\n'}, completed_at='3'),
        _worker(project, 'hmX', 'ws4', 'hm_00000004', {'d.txt': 'from ws4\n'}, completed_at='4'),
    ]
    updates = []
    st = hi.integrate(project, 'hmX', wss, base, on_update=lambda s: updates.append(s['status']))

    assert st['status'] == 'stopped'
    assert st['branch'] == 'hivemind/hmX'
    assert [m['ws_id'] for m in st['merged']] == ['ws1', 'ws2']
    assert st['failed']['ws_id'] == 'ws3' and st['failed']['kind'] == 'conflict'
    assert st['unmerged'] == ['ws3', 'ws4']             # ws4 left alone
    assert all(m['smokes_run'] is False and 'no smokes' in m['note'] for m in st['merged'])
    assert updates[-1] == 'stopped'
    # integration branch: base + two merges, conflicting + later work absent
    assert _git(repo, 'rev-parse', 'hivemind/hmX~0^1') != ''
    assert _git(repo, 'show', 'hivemind/hmX:a.txt') == 'from ws1'
    assert _git(repo, 'show', 'hivemind/hmX:b.txt') == 'from ws2'
    assert _git(repo, 'show', 'hivemind/hmX:d.txt') == 'base'
    assert _git(repo, 'merge-base', 'hivemind/hmX', 'master') == base
    # master untouched, user's checkout untouched
    assert _git(repo, 'rev-parse', 'master') == base
    assert _git(repo, 'rev-parse', '--abbrev-ref', 'HEAD') == 'master'
    assert _git(repo, 'status', '--porcelain') == ''
    # the dedicated integration worktree is gone; the branch stays
    assert not hi.integration_path(project, 'hmX').exists()


def test_all_clean_completes(project):
    repo = project['project_path']
    base = _git(repo, 'rev-parse', 'master')
    wss = [_worker(project, 'hmY', 'ws1', 'hm_00000011', {'a.txt': '1\n'}, completed_at='1'),
           _worker(project, 'hmY', 'ws2', 'hm_00000012', {'b.txt': '2\n'}, completed_at='2')]
    st = hi.integrate(project, 'hmY', wss, base)
    assert st['status'] == 'completed' and st['failed'] is None and st['unmerged'] == []
    assert _git(repo, 'rev-parse', 'master') == base


def test_failing_smoke_rolls_back_and_stops(project, monkeypatch):
    repo = project['project_path']
    base = _git(repo, 'rev-parse', 'master')
    ran = []

    def fake_smoke(wt, name):
        ran.append(name)
        return (name != 'bad.mjs', 'boom' if name == 'bad.mjs' else '')

    monkeypatch.setattr(hi, '_run_smoke', fake_smoke)
    wss = [_worker(project, 'hmZ', 'ws1', 'hm_00000021', {'a.txt': '1\n'},
                   smokes=['good.mjs'], completed_at='1'),
           _worker(project, 'hmZ', 'ws2', 'hm_00000022', {'b.txt': '2\n'},
                   smokes=['good.mjs', 'bad.mjs'], completed_at='2'),
           _worker(project, 'hmZ', 'ws3', 'hm_00000023', {'c.txt': '3\n'},
                   smokes=['good.mjs'], completed_at='3')]
    st = hi.integrate(project, 'hmZ', wss, base)
    assert st['status'] == 'stopped'
    assert [m['ws_id'] for m in st['merged']] == ['ws1']
    assert st['merged'][0]['smokes'] == [{'name': 'good.mjs', 'ok': True}]
    assert st['failed']['ws_id'] == 'ws2' and st['failed']['kind'] == 'smoke'
    assert st['failed']['smoke'] == 'bad.mjs' and 'boom' in st['failed']['detail']
    assert st['unmerged'] == ['ws2', 'ws3']
    assert ran == ['good.mjs', 'good.mjs', 'bad.mjs']   # ws3's smoke never ran
    # rolled back: the bad merge is not on the integration branch
    assert _git(repo, 'show', 'hivemind/hmZ:b.txt') == 'base'
    assert _git(repo, 'show', 'hivemind/hmZ:a.txt') == '1'
    assert _git(repo, 'rev-parse', 'master') == base


def test_dirty_worker_tree_stops_rather_than_dropping_work(project):
    repo = project['project_path']
    base = _git(repo, 'rev-parse', 'master')
    ws = _worker(project, 'hmD', 'ws1', 'hm_00000031', {'a.txt': '1\n'})
    wt = _awt.worktree_path(project, 'hm_00000031')
    (wt / 'a.txt').write_text('uncommitted\n', encoding='utf-8')
    st = hi.integrate(project, 'hmD', [ws], base)
    assert st['status'] == 'stopped' and st['failed']['kind'] == 'dirty'
    assert st['merged'] == []


def test_workstream_with_no_commits_is_skipped_not_failed(project):
    base = _git(project['project_path'], 'rev-parse', 'master')
    ok, _ = _awt.create(project, 'hm_00000041')
    ws = {'id': 'ws1', 'status': 'completed', 'completed_at': '1',
          'worktree_session_id': 'hm_00000041'}
    st = hi.integrate(project, 'hmN', [ws], base)
    assert st['status'] == 'completed' and st['merged'] == []
    assert st['skipped'] == [{'ws_id': 'ws1', 'reason': 'no commits beyond the base'}]


def test_no_isolated_workstreams_creates_no_branch(project):
    repo = project['project_path']
    base = _git(repo, 'rev-parse', 'master')
    st = hi.integrate(project, 'hmS', [{'id': 'ws1', 'status': 'completed'}], base)
    assert st['status'] == 'skipped'
    assert _git(repo, 'branch', '--list', 'hivemind/hmS') == ''


def test_clean_smokes_keeps_only_bare_mjs_names():
    assert hi.clean_smokes(['boot-smoke.mjs', '../x.mjs', 'a/b.mjs', 'x.sh', 7,
                            'boot-smoke.mjs', 'ok_1.mjs']) == ['boot-smoke.mjs', 'ok_1.mjs']
    assert hi.clean_smokes('boot-smoke.mjs') == [] and hi.clean_smokes(None) == []


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_real_node_smoke_pass_and_fail(project):
    repo = Path(project['project_path'])
    (repo / 'tools' / 'smoke').mkdir(parents=True)
    (repo / 'tools' / 'smoke' / 'pass.mjs').write_text('process.exit(0)\n', encoding='utf-8')
    (repo / 'tools' / 'smoke' / 'fail.mjs').write_text('console.error("nope");process.exit(3)\n',
                                                        encoding='utf-8')
    _git(repo, 'add', 'tools')
    _git(repo, 'commit', '-q', '-m', 'smokes')
    base = _git(repo, 'rev-parse', 'master')
    wss = [_worker(project, 'hmR', 'ws1', 'hm_00000051', {'a.txt': '1\n'},
                   smokes=['pass.mjs'], completed_at='1'),
           _worker(project, 'hmR', 'ws2', 'hm_00000052', {'b.txt': '2\n'},
                   smokes=['fail.mjs'], completed_at='2')]
    st = hi.integrate(project, 'hmR', wss, base)
    assert [m['ws_id'] for m in st['merged']] == ['ws1']
    assert st['merged'][0]['smokes'] == [{'name': 'pass.mjs', 'ok': True}]
    assert st['failed']['ws_id'] == 'ws2' and 'exit 3' in st['failed']['detail']
    assert 'nope' in st['failed']['detail']


# ── wiring: trigger, idempotence, status API ────────────────────────────────

class _Threads:
    started = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self.target, self.args = target, args

    def start(self):
        _Threads.started.append(self)


def test_start_integration_marks_running_once(hm_env, monkeypatch):
    _Threads.started = []
    monkeypatch.setattr(hm, 'threading', types.SimpleNamespace(Thread=_Threads))
    hm._hm_ensure_dirs('hm1')
    assert hm._hm_start_integration('hm1') is True
    assert hm._hm_read_integration('hm1')['status'] == 'running'
    assert hm._hm_read_integration('hm1')['branch'] == 'hivemind/hm1'
    assert hm._hm_start_integration('hm1') is False     # second call: nothing
    assert len(_Threads.started) == 1


def test_orchestrator_starts_integration_only_when_all_completed(hm_env, monkeypatch):
    started = []
    monkeypatch.setattr(hm, '_hm_start_integration', lambda hid: started.append(hid) or True)
    monkeypatch.setattr(hm, '_hm_dispatch_orchestrator', lambda *a, **k: None)
    monkeypatch.setattr(hm, '_hm_auto_spawn_workers', lambda hid: None)
    monkeypatch.setattr(hm, '_hm_push_sse', lambda *a, **k: None)
    monkeypatch.setattr(hm, 'load_project', lambda pid: None)

    class _Stop:
        n = 0

        def is_set(self):
            _Stop.n += 1
            return _Stop.n > 1

        def wait(self, t):
            return None

    def run_once(hid, statuses):
        _Stop.n = 0
        hm._hm_ensure_dirs(hid)
        hm._hm_save_manifest(hid, {'id': hid, 'status': 'active', 'project_id': 'p',
                                   'goal': 'g', 'config': {}})
        for i, stt in enumerate(statuses):
            hm._hm_save_workstream(hid, f'ws{i}', {'id': f'ws{i}', 'status': stt})
        monkeypatch.setattr(hm, '_hivemind_orchestrator_stop', _Stop())
        hm._hivemind_orchestrator_loop()

    run_once('hmA', ['completed', 'completed'])
    assert started == ['hmA']
    assert hm._hm_load_manifest('hmA')['status'] == 'completed'
    run_once('hmB', ['completed', 'failed'])
    assert started == ['hmA']                           # failed outcome: no integration


def test_status_api_surfaces_integration(hm_env):
    import server
    hm._hm_ensure_dirs('hmApi')
    hm._hm_save_manifest('hmApi', {'id': 'hmApi', 'status': 'completed', 'project_id': 'p'})
    c = server.app.test_client()
    assert c.get('/api/hivemind/hmApi').get_json()['integration'] is None
    hm._hm_write_integration('hmApi', {'status': 'stopped', 'branch': 'hivemind/hmApi',
                                       'merged': [{'ws_id': 'ws1'}],
                                       'failed': {'ws_id': 'ws2', 'kind': 'conflict'}})
    body = c.get('/api/hivemind/hmApi').get_json()
    assert body['integration']['branch'] == 'hivemind/hmApi'
    assert body['integration']['failed']['ws_id'] == 'ws2'
    row = [h for h in c.get('/api/hivemind/list').get_json() if h['id'] == 'hmApi'][0]
    assert row['integration_status'] == 'stopped'


def test_workstream_smokes_are_sanitised_on_create_and_update(hm_env):
    import server
    hm._hm_ensure_dirs('hmW')
    hm._hm_save_manifest('hmW', {'id': 'hmW', 'status': 'active', 'project_id': 'p'})
    c = server.app.test_client()
    r = c.post('/api/hivemind/hmW/workstreams/create',
               json={'id': 'w1', 'title': 't', 'smokes': ['boot-smoke.mjs', '../evil.mjs']})
    assert r.get_json()['workstream']['smokes'] == ['boot-smoke.mjs']
    r = c.put('/api/hivemind/hmW/workstreams/w1', json={'smokes': ['desk.mjs', 'x.sh']})
    assert r.get_json()['workstream']['smokes'] == ['desk.mjs']


def test_restart_marks_running_integration_interrupted(hm_env):
    hm._hm_ensure_dirs('hmR2')
    hm._hm_write_integration('hmR2', {'status': 'running', 'merged': []})
    hm._hm_reconcile_stale_on_startup()
    got = hm._hm_read_integration('hmR2')
    assert got['status'] == 'interrupted' and 'restarted' in got['reason']
