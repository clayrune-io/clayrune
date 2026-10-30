import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from mc import agent_worktree as worktrees, project_sync
from mc import dev_harness_landing as owner
from mc import dev_harness_manual as manual
from mc.dev_harness_store import HarnessStore
from mc.landing_serialization import landing, child_lease, LEASE_ENV
from test_dev_harness_diff import repo, git, branch
from test_dev_harness_native_plan import inputs, inspect, approve
from test_dev_harness_integration import executable, run
from test_dev_harness_activation_evidence import install_fixture

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def configured(executable, monkeypatch):
    executable = (HarnessStore(executable[0].root.parent/'data'), *executable[1:])
    monkeypatch.setattr(owner, 'store_for_landing', lambda: executable[0])
    from mc.blueprints import agent_routes as routes
    monkeypatch.setattr(routes, '_register_process', lambda *a: None)
    monkeypatch.setattr(routes, '_unregister_process', lambda *a: None)
    install_fixture(executable)
    proposal = inspect(executable)
    approve(executable, proposal)
    measured = run(executable, measurement=True)
    approve(executable, proposal, mode='enforce', revision=1, evidence_id=measured.record['measured_evidence_id'])
    return executable, proposal


@pytest.mark.parametrize('runtime_files', [False, True])
def test_managed_exact_tree_and_hook_lease_no_deadlock(configured, monkeypatch, runtime_files):
    inputs, _ = configured
    store, project, *_ = inputs
    root = Path(project['project_path'])
    if runtime_files:
        with (root/'runtime.sqlite3').open('wb') as stream:
            stream.truncate(17 * 1024 * 1024)
        runtime = root/'runtime.json'
        runtime.write_text('before evaluation')
        from mc.blueprints import agent_routes as routes
        monkeypatch.setattr(routes, '_register_process', lambda *a: runtime.write_text('during native execution'))
        real_prepare = owner.prepare
        def prepare(*args, **kwargs):
            candidate = real_prepare(*args, **kwargs)
            runtime.write_text('between prepare and merge')
            return candidate
        monkeypatch.setattr(owner, 'prepare', prepare)
    ok, path = worktrees.create(project, 'managed')
    assert ok
    (Path(path)/'new.py').write_text('new source\n')
    git(Path(path), 'add', 'new.py')
    git(Path(path), 'commit', '-qm', 'source')
    # Force a real merge hook, rather than a fast-forward which skips it.
    (root/'base.py').write_text('base\n')
    git(root, 'add', 'base.py')
    git(root, 'commit', '-qm', 'base')
    install_root = store.root.parent.parent
    project_file = install_root/'data/projects/one.json'
    project_file.parent.mkdir(parents=True)
    project_file.write_text(json.dumps(project))
    hook = root/'.git/hooks/pre-merge-commit'
    argv = [sys.executable, str(ROOT/'tools/merge-review-check.py'), '--merge-head', '--respect-setting',
            '--repo', str(root), '--data-root', str(install_root)]
    hook.write_text('#!/bin/sh\nexec '+' '.join(shlex.quote(v) for v in argv)+'\n', newline='\n')
    hook.chmod(0o755)
    result = worktrees.merge_back(project, 'managed')
    assert result[0] == 'clean', result
    assert (root/'new.py').read_text() == 'new source\n'
    assert not list((store.project_dir('one')/'landing_leases').glob('*.json'))
    if runtime_files:
        assert runtime.read_text() == 'between prepare and merge'
        assert (root/'runtime.sqlite3').stat().st_size > 16 * 1024 * 1024


@pytest.mark.parametrize('mode', [None, 'off', 'preview'])
@pytest.mark.parametrize('hooked', [False, True])
def test_inactive_landing_never_manifests_runtime_files(repo, tmp_path, monkeypatch, mode, hooked):
    from mc import dev_harness_diff as diff
    from mc.dev_harness_catalogue import load_catalogue
    install_root = tmp_path/'.clayrune/inactive-install'
    store = HarnessStore(install_root/'data')
    project = {'id': 'one', 'project_path': str(repo)}
    monkeypatch.setattr(owner, 'store_for_landing', lambda: store)
    if mode:
        from mc.dev_harness_policy import lost_protections, resolve_policy
        catalogue = load_catalogue()
        policy = resolve_policy({'profile': 'local-hobby'}, catalogue)
        store.activate('one', expected_revision=0, mode=mode,
                       draft={'profile': 'local-hobby'}, catalogue=catalogue,
                       acknowledged_losses=lost_protections(None, policy, mode, catalogue))
    ok, path = worktrees.create(project, 'inactive')
    assert ok
    (Path(path)/'new.py').write_text('landed\n')
    git(Path(path), 'add', 'new.py')
    git(Path(path), 'commit', '-qm', 'source')
    if hooked:
        (repo/'base.py').write_text('force a non-fast-forward merge\n')
        git(repo, 'add', 'base.py')
        git(repo, 'commit', '-qm', 'base')
        record = install_root/'data/projects/one.json'
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps(project))
        hook = repo/'.git/hooks/pre-merge-commit'
        # The separate hook process must also prove zero manifest calls, using
        # the same manual entry point as the installed production hook.
        probe = install_root/'hook.py'
        probe.write_text('import sys\n' + 'sys.path.insert(0, '+repr(str(ROOT))+')\n'
            'from mc import dev_harness_diff as diff, dev_harness_integration_inputs as inputs\n'
            'def forbidden(*a, **k): raise AssertionError("inactive hook hashed source")\n'
            'diff.manifest = inputs.manifest = forbidden\n'
            'import runpy\n' + 'sys.argv = '+repr([str(ROOT/'tools/merge-review-check.py'),
                '--merge-head','--respect-setting','--repo',str(repo),'--data-root',str(install_root)])+'\n'
            'runpy.run_path(sys.argv[0], run_name="__main__")\n')
        hook.write_text('#!/bin/sh\nexec '+' '.join(shlex.quote(v) for v in [sys.executable,str(probe)])+'\n', newline='\n')
        hook.chmod(0o755)
    with (repo/'runtime.sqlite3').open('wb') as stream:
        stream.truncate(17 * 1024 * 1024)
    runtime = repo/'runtime.json'
    runtime.write_text('before prepare')
    real_prepare = owner.prepare
    def prepare(*args, **kwargs):
        candidate = real_prepare(*args, **kwargs)
        runtime.write_text('between prepare and merge')
        return candidate
    monkeypatch.setattr(owner, 'prepare', prepare)
    monkeypatch.setattr(diff, 'manifest', lambda *a, **k: pytest.fail('inactive landing must not hash files'))
    from mc import dev_harness_integration_inputs as integration_inputs
    monkeypatch.setattr(integration_inputs, 'manifest', diff.manifest)
    monkeypatch.setattr(owner, 'freeze_integration', lambda *a: pytest.fail('inactive landing must not freeze source'))
    assert worktrees.merge_back(project, 'inactive')[0] == 'clean'
    assert (repo/'new.py').read_text() == 'landed\n'
    assert runtime.read_text() == 'between prepare and merge'
    assert (repo/'runtime.sqlite3').stat().st_size > 16 * 1024 * 1024


@pytest.mark.parametrize('dirty', ['collision', 'tracked', 'staged'])
def test_active_landing_refuses_real_merge_risk_and_preserves_work(configured, dirty):
    inputs, _ = configured
    project = inputs[1]
    root = Path(project['project_path'])
    ok, path = worktrees.create(project, 'risk')
    assert ok
    (Path(path)/'new.py').write_text('incoming\n')
    git(Path(path), 'add', 'new.py')
    git(Path(path), 'commit', '-qm', 'source')
    before = git(root, 'rev-parse', 'HEAD')
    local = root/('new.py' if dirty == 'collision' else 'app.py')
    local.write_text('local work\n')
    if dirty == 'staged':
        git(root, 'add', 'app.py')
    result = worktrees.merge_back(project, 'risk')
    assert result[0] == ('conflict' if dirty == 'collision' else 'awaiting_standards'), result
    assert git(root, 'rev-parse', 'HEAD') == before
    assert local.read_text() == 'local work\n'
    assert Path(path).exists()


@pytest.mark.parametrize('change', ['working', 'index', 'ref'])
def test_active_candidate_revalidation_refuses_tracked_or_ref_drift(configured, change):
    inputs, _ = configured
    project = inputs[1]
    root = Path(project['project_path'])
    branch(root)
    with landing(root) as lock:
        candidate = owner.prepare(project, lock, 'incoming')
        if change == 'ref':
            git(root, 'update-ref', 'refs/heads/incoming', git(root, 'rev-parse', 'HEAD'))
        else:
            (root/'app.py').write_text('local tracked work\n')
            if change == 'index':
                git(root, 'add', 'app.py')
                (root/'app.py').write_text('value = 1\n')
        with pytest.raises(owner.StandardsHold, match='landing_inputs_changed'):
            candidate.revalidate()


def test_required_failure_preserves_remove_gc_and_no_success(configured, monkeypatch):
    inputs, _ = configured
    store, project, *_ = inputs
    ok, path = worktrees.create(project, 'held')
    assert ok
    (Path(path)/'app.py').write_text('changed source\n')
    git(Path(path), 'add', 'app.py')
    git(Path(path), 'commit', '-qm', 'source')
    from mc import dev_harness_integration as integration
    real = integration.run_approved
    def fail(**kwargs):
        receipt = real(**kwargs)
        # Inject a trusted test finding at the receipt owner, preserving digest
        # so the coordinator exercises required severity rather than corruption.
        receipt['results'][0].update(status='fail', exit_code=1)
        receipt['status'] = 'fail'
        from test_dev_harness_activation_evidence import save_receipt
        save_receipt(store, receipt)
        return receipt
    monkeypatch.setattr(integration, 'run_approved', fail)
    assert worktrees.merge_back(project, 'held')[0] == 'awaiting_standards'
    assert not worktrees.remove(project, 'held', delete_branch=True)[0]
    result = worktrees.gc_stale(project, [], use_cache=True)
    assert result['merged'] == 0 and 'held' in result['preserved']
    assert Path(path).exists()
    assert not (Path(project['project_path'])/'app.py').read_text().startswith('changed')


def test_nonagent_review_off_manual_standards_still_run(configured):
    inputs, _ = configured
    store, project, *_ = inputs
    root = Path(project['project_path'])
    branch(root)
    reviewed = []
    with landing(root) as lock:
        candidate = owner.prepare(project, lock, 'incoming')
        assert candidate.evaluation.record['status'] == 'pass'
    code = manual.preflight(store=store, project=project, repo=root, branch='incoming',
        review=lambda h: (reviewed.append(h) or 0), register=lambda *a: None, unregister=lambda *a: None)
    assert code == 0 and reviewed == [git(root, 'rev-parse', 'incoming')]
    assert len(list((store.project_dir('one')/'integration_runs').glob('*.json'))) >= 3


def test_pending_forged_lease_cannot_bypass(configured, monkeypatch):
    from test_dev_harness_pending_merge import pending
    inputs, _ = configured
    root = Path(inputs[1]['project_path'])
    pending(root)
    monkeypatch.setenv(LEASE_ENV, 'a'*32)
    assert manual.preflight(store=inputs[0], project=inputs[1], repo=root, branch=None,
        review=lambda _: pytest.fail('bad lease must refuse'), register=lambda *a: None, unregister=lambda *a: None) == 2


def test_post_landing_mismatch_keeps_human_head_and_worker(configured, monkeypatch):
    inputs, _ = configured
    project = inputs[1]
    root = Path(project['project_path'])
    ok, path = worktrees.create(project, 'mismatch')
    assert ok
    (Path(path)/'new.py').write_text('kept work\n')
    git(Path(path), 'add', 'new.py')
    git(Path(path), 'commit', '-qm', 'work')
    old = git(root, 'rev-parse', 'HEAD')
    def mismatch(self):
        raise owner.StandardsHold('landed_tree_mismatch')
    monkeypatch.setattr(owner.Candidate, 'verify_landed', mismatch)
    result = worktrees.merge_back(project, 'mismatch')
    assert result[0] == 'awaiting_standards'
    assert git(root, 'rev-parse', 'HEAD') != old  # Never reset the human checkout.
    assert (root/'new.py').read_text() == 'kept work\n'
    assert not worktrees.remove(project, 'mismatch', delete_branch=True)[0]
    assert Path(path).exists()


def test_hivemind_standards_failure_stops_before_smokes_and_next_worker(configured, monkeypatch):
    from mc import dev_harness_integration as integration, hivemind_integration as hive
    inputs, _ = configured
    store, project, *_ = inputs
    root = Path(project['project_path'])
    baseline = git(root, 'rev-parse', 'HEAD')
    work = []
    for number in range(2):
        sid = f'hm_native_{number}'
        ok, path = worktrees.create(project, sid)
        assert ok
        (Path(path)/f'new{number}.py').write_text('work\n')
        git(Path(path), 'add', f'new{number}.py')
        git(Path(path), 'commit', '-qm', 'work')
        work.append({'id':str(number),'status':'completed','worktree_session_id':sid,
                     'worktree_branch':worktrees.branch_name(sid),'smokes':['boot.mjs']})
    real = integration.run_approved
    def fail(**kwargs):
        from test_dev_harness_activation_evidence import save_receipt
        receipt = real(**kwargs)
        receipt['status'] = receipt['results'][0]['status'] = 'fail'
        receipt['results'][0]['exit_code'] = 1
        save_receipt(store, receipt)
        return receipt
    monkeypatch.setattr(integration, 'run_approved', fail)
    monkeypatch.setattr(hive, '_run_smoke', lambda *a: pytest.fail('held standard must stop before smokes'))
    result = hive.integrate(project, 'native-hold', work, baseline)
    assert result['status'] == 'stopped' and result['merged'] == []
    assert result['failed']['kind'] == 'standards'
    assert result['failed']['code'] == 'awaiting_standards'
    assert len(result['failed']['run_id']) == 32
    assert result['unmerged'] == ['0','1']
    assert git(root, 'rev-parse', 'HEAD') == baseline
    assert all(worktrees.worktree_path(project, ws['worktree_session_id']).exists() for ws in work)


@pytest.mark.parametrize('tracked_dirty', [False, True])
def test_hivemind_registered_main_ignores_runtime_but_refuses_tracked_dirt(configured, monkeypatch, tracked_dirty):
    from mc import hivemind_integration as hive
    inputs, _ = configured
    project = inputs[1]
    root = Path(project['project_path'])
    baseline = git(root, 'rev-parse', 'HEAD')
    sid = 'hm_runtime'
    ok, path = worktrees.create(project, sid)
    assert ok
    (Path(path)/'new.py').write_text('work\n')
    git(Path(path), 'add', 'new.py')
    git(Path(path), 'commit', '-qm', 'work')
    with (root/'runtime.sqlite3').open('wb') as stream:
        stream.truncate(17 * 1024 * 1024)
    runtime = root/'runtime.json'
    runtime.write_text('before native')
    if tracked_dirty:
        (root/'app.py').write_text('local tracked work\n')
    from mc.blueprints import agent_routes as routes
    monkeypatch.setattr(routes, '_register_process', lambda *a: runtime.write_text('during native'))
    smokes = []
    monkeypatch.setattr(hive, '_run_smoke', lambda *a: (smokes.append(a) or (True, '')))
    result = hive.integrate(project, 'runtime-gate', [{'id':'one','status':'completed',
        'worktree_session_id':sid,'worktree_branch':worktrees.branch_name(sid),'smokes':['boot.mjs']}], baseline)
    if tracked_dirty:
        assert result['status'] == 'stopped' and result['failed']['kind'] == 'standards', result
        assert not result['merged'] and not smokes
        assert (root/'app.py').read_text() == 'local tracked work\n'
    else:
        assert result['status'] == 'completed', result
        assert len(result['merged']) == 1 and len(smokes) == 1
        assert runtime.read_text() == 'during native'
    assert git(root, 'rev-parse', 'HEAD') == baseline
    assert Path(path).exists()
