"""HR-5: real temp git, real HR-3/HR-4 lifecycle, fake provider processes."""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from mc import agent_worktree as awt, project_sync, review_lineage, state
from mc import hivemind_integration as git, hivemind_integration_cycle as cycle
from mc import hivemind_reviewer as reviewer
from mc.blueprints import agent_routes as ar, hivemind_routes as hm
from mc.hivemind_review import ReviewStore
from mc.merge_review_gate import ReviewRefused
from tests.test_hivemind_routes import client  # noqa: F401
from tests.test_hivemind_roster import agents_dir  # noqa: F401
from tests.test_hivemind_roster_worker import rig  # noqa: F401
from tests.test_hivemind_reviewer import FakeProc, FakeTimer


def cmd(repo, *args):
    r = subprocess.run(['git', *args], cwd=repo, capture_output=True,
                       stdin=subprocess.DEVNULL, text=True, encoding='utf-8')
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def env(rig, monkeypatch, tmp_path):
    r = rig
    r.repo = Path(r.project['project_path'])
    monkeypatch.setattr(project_sync, '_data_root', tmp_path / 'install')
    r.repo.joinpath('.gitignore').write_text('.clayrune/\n')
    for name in ('a.txt', 'b.txt'):
        r.repo.joinpath(name).write_text('base\n')
    cmd(r.repo, 'init', '-q', '-b', 'master')
    cmd(r.repo, 'config', 'user.name', 'Fixture')
    cmd(r.repo, 'config', 'user.email', 'fixture@invalid.local')
    cmd(r.repo, 'add', '.gitignore', 'a.txt', 'b.txt', 'AGENT_RULES.md')
    cmd(r.repo, 'commit', '-qm', 'base')
    r.base = cmd(r.repo, 'rev-parse', 'HEAD')
    cmd(r.repo, 'update-ref', 'refs/remotes/origin/master', r.base)
    r.manifest.update(status='completed')
    r.manifest['config']['reviewer_character'] = 'global:worker-codex'
    hm._hm_save_manifest('roster1', r.manifest)
    hm._hm_pin_worktree_base('roster1', r.base)
    r.wss = []
    for index, letter in enumerate(('a', 'b')):
        sid = 'hm_worker_' + letter
        wt = awt.worktree_path(r.project, sid)
        branch = awt.branch_name(sid)
        wt.parent.mkdir(parents=True, exist_ok=True)
        cmd(r.repo, 'worktree', 'add', '-b', branch, str(wt), r.base)
        wt.joinpath(letter + '.txt').write_text(letter + '\n')
        cmd(wt, 'add', letter + '.txt')
        cmd(wt, 'commit', '-qm', letter)
        ws = {'id': 'ws' + str(index + 1), 'status': 'completed', 'completed_at': str(index),
              'worktree_session_id': sid, 'worktree_branch': branch,
              'assignment_attempt': 1, 'resolved_assignment': {'session_id': sid},
              'title': letter, 'description': letter + '.txt only', 'dependencies': [],
              'smokes': [letter + '.mjs']}
        r.wss.append(ws)
        hm._hm_save_workstream('roster1', ws['id'], ws)
    r.events, r.stops = [], []
    r.clock = [reviewer.time.time()]
    monkeypatch.setattr(reviewer, 'time', SimpleNamespace(time=lambda: r.clock[0]))
    monkeypatch.setattr(reviewer, 'threading', SimpleNamespace(Timer=FakeTimer))
    monkeypatch.setattr(review_lineage, '_production_spawner_of', lambda: lambda sid: [])
    monkeypatch.setattr(ar, '_allowance_refusal', lambda *a, **kw: '')
    monkeypatch.setattr(ar, '_model_quota_blocked', lambda *a, **kw: '')
    runtime = r.runtimes['codex']

    def dispatch(**kw):
        runtime.calls.append(kw)
        kw['session_dict'].update(proc=FakeProc(), process_alive=True)
        r.events.append('review ' + kw['session_metadata']['trigger_id'])

    def stop(handle):
        r.stops.append(handle.session_dict['session_id'])
        handle.session_dict['proc'].rc = 0
        handle.session_dict.update(status='stopped', process_alive=False)

    runtime.dispatch, runtime.stop = dispatch, stop
    merge = git.merge_candidate

    def logged_merge(*args):
        r.events.append('merge ' + args[2]['id'])
        return merge(*args)

    monkeypatch.setattr(git, 'merge_candidate', logged_merge)
    monkeypatch.setattr(git, '_run_smoke', lambda wt, name: r.events.append('smoke ' + name) or (True, ''))
    r.tick = lambda: cycle.advance(hm, 'roster1')
    return r


def candidate(st):
    return cycle._candidate(st)


def submit(r, verdict='pass', st=None):
    from unittest.mock import patch
    c = candidate(st or cycle.read(hm, 'roster1'))
    with reviewer.verdict_guard(hm, c) as status:
        with patch('mc.hivemind_lineage.creator_of_request', return_value=status['reviewer_session']):
            ReviewStore(hm._hm_dir('roster1')).record(c, object(), verdict=verdict,
                report_path='report.md', spawner_of=lambda sid: [])
    return c


def assert_outside_unchanged(r):
    assert cmd(r.repo, 'rev-parse', 'master') == r.base
    assert cmd(r.repo, 'rev-parse', 'origin/master') == r.base
    assert cmd(r.repo, 'symbolic-ref', '--short', 'HEAD') == 'master'
    assert cmd(r.repo, 'status', '--porcelain') == ''


def test_serial_exact_reviews_block_later_merges_and_resume(env):
    r = env
    st = r.tick()
    assert st['status'] == 'awaiting_review'
    a = candidate(st)
    assert cmd(st['worktree'], 'rev-parse', 'HEAD') == a.merged_sha
    assert cmd(st['worktree'], 'show', 'HEAD:b.txt') == 'base'
    assert st['merged'] == []  # provisional != accepted
    call = r.runtimes['codex'].calls[0]
    assert st['current']['brief_path'] in call['task']
    assert st['current']['brief_sha256'] in call['task']
    assert r.rows[0]['task'] == call['task']
    assert r.tick()['status'] == 'awaiting_review'
    assert len(r.runtimes['codex'].calls) == 1
    submit(r)
    st = r.tick()
    assert [m['ws_id'] for m in st['merged']] == ['ws1']
    b = candidate(st)
    assert b.pre_merge_sha == a.merged_sha and b.merged_sha != a.merged_sha
    assert b.contributing_sessions == ('hm_worker_a', 'hm_worker_b')
    assert ReviewStore(hm._hm_dir('roster1')).find(a)['consumed_at']
    assert ReviewStore(hm._hm_dir('roster1')).find(b)['result'] is None
    submit(r)
    st = r.tick()
    assert st['status'] == 'completed'
    assert [e.split()[0] for e in r.events] == ['merge', 'smoke', 'review', 'merge', 'smoke', 'review']
    assert st['merged'][0]['commit'] == a.merged_sha
    assert st['merged'][1]['commit'] == b.merged_sha
    brief = json.loads(Path(st['merged'][0]['review_brief']).read_text())
    assert brief['changed_files'] == ['a.txt'] and brief['smokes'] == [{'name': 'a.mjs', 'ok': True}]
    assert Path(st['worktree']).is_dir()
    assert r.tick()['cursor'] == 2 and len(r.runtimes['codex'].calls) == 2
    assert_outside_unchanged(r)


@pytest.mark.parametrize('kind', ['changes_requested', 'no_verdict', 'timeout', 'failed', 'cancelled', 'restart'])
def test_reviewer_holds_preserve_provisional_commit_and_never_merge_b(env, kind):
    r = env
    st = r.tick()
    c = candidate(st)
    sid = st['current']['reviewer']['reviewer_session']
    if kind == 'changes_requested':
        submit(r, kind)
    elif kind == 'no_verdict':
        state.agent_sessions[sid]['status'] = 'completed'
        state.agent_sessions[sid]['proc'].rc = 0
    elif kind == 'timeout':
        r.clock[0] += 2701
    elif kind == 'failed':
        state.agent_sessions[sid]['status'] = 'error'
    elif kind == 'cancelled':
        reviewer.cancel_reviewer(hm, c)
    else:
        state.agent_sessions.pop(sid)
    st = r.tick()
    assert st['status'] == 'held' and st['cursor'] == 0
    assert cmd(st['worktree'], 'rev-parse', 'HEAD') == c.merged_sha
    assert cmd(st['worktree'], 'show', 'HEAD:b.txt') == 'base'
    assert len(r.runtimes['codex'].calls) == 1
    assert r.tick()['status'] == 'held'
    assert_outside_unchanged(r)


@pytest.mark.parametrize('kind', ['head', 'dirty_candidate', 'tip', 'attempt', 'smokes', 'generation', 'paused', 'stopped', 'deleted'])
def test_pass_cannot_survive_current_pin_or_lifecycle_drift(env, monkeypatch, kind):
    r = env
    st = r.tick()
    c = submit(r)
    if kind == 'head':
        cmd(st['worktree'], 'reset', '--hard', r.base)
    elif kind == 'dirty_candidate':
        Path(st['worktree'], 'a.txt').write_text('unexpected edit')
    elif kind == 'tip':
        wt = awt.worktree_path(r.project, 'hm_worker_a')
        wt.joinpath('a.txt').write_text('new tip')
        cmd(wt, 'commit', '-am', 'drift')
    elif kind in ('attempt', 'smokes'):
        ws = hm._hm_load_workstream('roster1', 'ws1')
        ws.update(assignment_attempt=2) if kind == 'attempt' else ws.update(smokes=['new.mjs'])
        hm._hm_save_workstream('roster1', 'ws1', ws)
    elif kind in ('paused', 'stopped'):
        r.manifest['status'] = kind
        hm._hm_save_manifest('roster1', r.manifest)
    elif kind == 'generation':
        def deleted(*args):
            raise RuntimeError('generation deleted')
        monkeypatch.setattr(hm, '_assert_runtime_project_generation', deleted)
    else:
        r.client.projects.clear()
    st = r.tick()
    assert st['status'] == 'held' and st['cursor'] == 0
    assert not ReviewStore(hm._hm_dir('roster1')).find(c)['consumed_at']
    assert 'merge ws2' not in r.events


@pytest.mark.parametrize('kind', ['conflict', 'dirty', 'standards', 'smoke', 'native', 'dependent'])
def test_merge_smoke_standards_and_native_failures_hold(env, monkeypatch, kind):
    r = env
    if kind == 'conflict':
        wt = awt.worktree_path(r.project, 'hm_worker_b')
        wt.joinpath('a.txt').write_text('conflicting b\n')
        cmd(wt, 'add', 'a.txt')
        cmd(wt, 'commit', '-qm', 'conflict')
        submit(r, st=r.tick())
    elif kind == 'dirty':
        awt.worktree_path(r.project, 'hm_worker_a').joinpath('a.txt').write_text('dirty')
    elif kind == 'standards':
        from mc import dev_harness_landing
        def held(*args, **kwargs):
            raise dev_harness_landing.StandardsHold('candidate_unavailable')
        monkeypatch.setattr(dev_harness_landing, 'prepare', held)
    elif kind == 'smoke':
        monkeypatch.setattr(git, '_run_smoke', lambda *args: (False, 'broken test'))
    elif kind == 'native':
        state.agent_sessions['child'] = {'_spawned_by': 'hm_worker_a', 'status': 'running'}
    else:
        ws = r.wss[0] | {'dependencies': ['prerequisite']}
        hm._hm_save_workstream('roster1', 'ws1', ws)
    st = r.tick()
    assert st['status'] == 'held'
    assert len(r.runtimes['codex'].calls) == (1 if kind == 'conflict' else 0)
    if kind == 'smoke':
        assert cmd(st['worktree'], 'rev-parse', 'HEAD') == r.base
        assert st['failed']['rolled_back_to'] == r.base
    assert_outside_unchanged(r)


def test_empty_smokes_still_require_reviewer_and_noop_skips_review(env):
    r = env
    ws = r.wss[0] | {'smokes': []}
    hm._hm_save_workstream('roster1', 'ws1', ws)
    cmd(awt.worktree_path(r.project, 'hm_worker_b'), 'reset', '--hard', r.base)
    st = r.tick()
    assert st['status'] == 'awaiting_review'
    assert st['current']['merge']['smokes_run'] is False
    assert 'none run' in st['current']['merge']['note']
    submit(r)
    st = r.tick()
    assert st['status'] == 'completed'
    assert st['skipped'][0]['ws_id'] == 'ws2'
    assert len(r.runtimes['codex'].calls) == 1


def test_nongit_analysis_run_is_explicitly_skipped(rig):
    rig.manifest.update(status='completed')
    hm._hm_save_manifest('roster1', rig.manifest)
    hm._hm_save_workstream('roster1', 'ws1', rig.ws | {'status': 'completed'})
    result = cycle.advance(hm, 'roster1')
    assert result['status'] == 'skipped' and 'analysis-only' in result['reason']
    assert not git.integration_path(rig.project, 'roster1').exists()


def test_guard_vetoes_raw_pass_consumption(env, monkeypatch):
    r = env
    st = r.tick()
    c = submit(r)
    @contextmanager
    def veto(*args):
        raise ReviewRefused(409, 'lifecycle cancelled at consumption')
        yield
    monkeypatch.setattr(reviewer, 'verdict_guard', veto)
    assert r.tick()['status'] == 'held'
    assert not ReviewStore(hm._hm_dir('roster1')).find(c)['consumed_at']
    assert 'merge ws2' not in r.events


def test_restart_after_consumption_before_cursor_save_reuses_exact_pass(env, monkeypatch):
    r = env
    st = r.tick()
    c = submit(r)
    with reviewer.verdict_guard(hm, c):
        assert ReviewStore(hm._hm_dir('roster1')).consume_pass(c)
    state.agent_sessions.pop(st['current']['reviewer']['reviewer_session'])
    st = r.tick()
    assert st['cursor'] == 1 and st['status'] == 'awaiting_review'
    assert len(r.runtimes['codex'].calls) == 2


@pytest.mark.parametrize('phase', ['merging', 'launching_review'])
def test_ambiguous_intent_never_retries_or_duplicates_review(env, monkeypatch, phase):
    r = env
    def crash(*args, **kwargs):
        raise KeyboardInterrupt('simulated crash after intent')
    if phase == 'merging':
        monkeypatch.setattr(git, 'merge_candidate', crash)
    else:
        monkeypatch.setattr(reviewer, 'launch_reviewer', crash)
    with pytest.raises(KeyboardInterrupt):
        r.tick()
    st = cycle.read(hm, 'roster1')
    assert st['current']['phase'] == phase
    assert r.tick()['status'] == 'held'
    assert len(r.runtimes['codex'].calls) == 0
    assert 'merge ws2' not in r.events


def test_corrupt_cursor_is_not_absent_or_restartable(env):
    env.tick()
    path = hm._hm_dir('roster1') / 'integration.json'
    path.write_text('{"review_cycle_version":1,"cursor":2}')
    with pytest.raises(ReviewRefused):
        env.tick()
    with pytest.raises(ReviewRefused):
        cycle.start(hm, 'roster1')
    assert len(env.runtimes['codex'].calls) == 1


def test_tick_serialization_refuses_duplicate_consumer(env):
    with cycle._transaction(hm, 'roster1'):
        with pytest.raises(ReviewRefused, match='owned'):
            env.tick()
    assert env.tick()['status'] == 'awaiting_review'


def test_roster_start_and_startup_delegate_without_legacy_batch(env, monkeypatch):
    calls = []
    monkeypatch.setattr(cycle, 'start', lambda api, hid: calls.append(hid) or True)
    assert hm._hm_start_integration('roster1') is True
    assert calls == ['roster1']
    env.tick()
    hm._hm_reconcile_stale_on_startup()
    assert calls == ['roster1', 'roster1']
    assert cycle.read(hm, 'roster1')['status'] == 'awaiting_review'


def test_restart_from_smoking_checkpoint_reruns_checks_then_one_review(env, monkeypatch):
    r = env
    smoke = git.smoke_candidate
    def crash(*args):
        raise KeyboardInterrupt('simulated crash before smoke')
    monkeypatch.setattr(git, 'smoke_candidate', crash)
    with pytest.raises(KeyboardInterrupt):
        r.tick()
    assert cycle.read(hm, 'roster1')['current']['phase'] == 'smoking'
    monkeypatch.setattr(git, 'smoke_candidate', smoke)
    st = r.tick()
    assert st['status'] == 'awaiting_review'
    assert len(r.runtimes['codex'].calls) == 1
    assert r.events.count('merge ws1') == 1


def test_runtime_launch_failure_never_automatically_replaces_reviewer(env, monkeypatch):
    r = env
    def fail(**kwargs):
        raise RuntimeError('uncertain launch')
    r.runtimes['codex'].dispatch = fail
    st = r.tick()
    assert st['status'] == 'held' and 'uncertain launch' in st['reason']
    records = list((hm._hm_dir('roster1') / 'reviews').glob('*.runtime.json'))
    assert len(records) == 1
    assert r.tick()['status'] == 'held'
    assert len(list((hm._hm_dir('roster1') / 'reviews').glob('*.runtime.json'))) == 1


def test_pending_review_releases_landing_ownership(env):
    from mc.landing_serialization import landing
    st = env.tick()
    # A different thread can acquire it while review is pending.
    import concurrent.futures
    def acquire():
        with landing(Path(st['worktree']), timeout=1):
            return True
    with concurrent.futures.ThreadPoolExecutor() as pool:
        assert pool.submit(acquire).result(timeout=5)


def test_cursor_write_failure_after_pass_is_recoverable_without_another_review(env, monkeypatch):
    r = env
    st = r.tick()
    c = submit(r)
    write = hm._hm_write_integration
    def crash(hid, value):
        if value['cursor'] == 1:
            raise KeyboardInterrupt('simulated process crash')
        write(hid, value)
    monkeypatch.setattr(hm, '_hm_write_integration', crash)
    with pytest.raises(KeyboardInterrupt):
        r.tick()
    assert ReviewStore(hm._hm_dir('roster1')).find(c)['consumed_at']
    monkeypatch.setattr(hm, '_hm_write_integration', write)
    st = r.tick()
    assert st['cursor'] == 1 and len(r.runtimes['codex'].calls) == 2


def test_start_has_one_driver_and_releases_slot_after_failure(env, monkeypatch):
    from tests.test_hivemind_routes import FakeThread
    import threading
    monkeypatch.setattr(cycle, 'threading', SimpleNamespace(Thread=FakeThread, Lock=threading.Lock))
    FakeThread.started = []
    assert cycle.start(hm, 'roster1') is True
    assert cycle.start(hm, 'roster1') is False
    thread, = FakeThread.started
    # Exercise driver without running a daemon; one terminal tick.
    monkeypatch.setattr(cycle, 'advance', lambda *a: {'status': 'held', 'branch': 'hivemind/roster1'})
    thread.target(*thread.args)
    assert cycle.start(hm, 'roster1') is True
    cycle._threads.discard(str(hm._hm_dir('roster1').resolve()))


def test_native_descendant_appearing_during_review_cannot_consume_pass(env):
    r = env
    st = r.tick()
    c = submit(r)
    state.agent_sessions['child'] = {'_spawned_by': 'hm_worker_a', 'status': 'completed'}
    state.agent_sessions['grandchild'] = {'_spawned_by': 'child', 'status': 'running'}
    assert r.tick()['status'] == 'held'
    assert not ReviewStore(hm._hm_dir('roster1')).find(c)['consumed_at']


def test_pending_pause_cancels_only_owned_reviewer(env):
    r = env
    st = r.tick()
    sid = st['current']['reviewer']['reviewer_session']
    r.manifest['status'] = 'paused'
    hm._hm_save_manifest('roster1', r.manifest)
    st = r.tick()
    assert st['status'] == 'held' and r.stops == [sid]
    assert r.tick()['status'] == 'held' and r.stops == [sid]


def test_read_projection_holds_corrupt_state(env):
    env.tick()
    (hm._hm_dir('roster1') / 'integration.json').write_text('broken')
    assert hm._hm_read_integration('roster1')['status'] == 'held'


@pytest.mark.parametrize('status', ['failed', 'stopped', 'idle', 'blocked'])
def test_unreconciled_native_child_is_not_success(env, status):
    state.agent_sessions['native'] = {'_spawned_by': 'hm_worker_a', 'status': status}
    assert env.tick()['status'] == 'held'
    assert env.runtimes['codex'].calls == []


def test_review_dependencies_survive_wait_and_unlink_without_losing_source(env):
    r = env
    (r.repo / '.git' / 'info' / 'exclude').write_text('tools/smoke/node_modules/\n')
    source = r.repo / git._SMOKE_NODE_MODULES
    source.mkdir(parents=True)
    (source / 'sentinel').write_text('keep')
    st = r.tick()
    link = Path(st['worktree']) / git._SMOKE_NODE_MODULES
    assert awt._is_link(link) and (link / 'sentinel').read_text() == 'keep'
    submit(r)
    st = r.tick()
    assert awt._is_link(link)
    submit(r)
    assert r.tick()['status'] == 'completed'
    assert not awt._is_link(link) and not link.exists()
    assert (source / 'sentinel').read_text() == 'keep'
    assert_outside_unchanged(r)
