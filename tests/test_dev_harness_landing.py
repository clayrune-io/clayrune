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


def test_managed_exact_tree_and_hook_lease_no_deadlock(configured):
    inputs, _ = configured
    store, project, *_ = inputs
    root = Path(project['project_path'])
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
