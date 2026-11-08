"""MC-1164 checks use throwaway Git repositories, never the live target.

Round 9: the gate's rule is exit codes only. A merged candidate passes when every
configured command exits 0 with no timeout, infrastructure error or oversize output;
anything else blocks. Nothing parses runner output and nothing is compared with the
target, so there is no failure-identity machinery left to test.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from mc import agent_worktree as worktrees, dev_harness_landing, landing_gate as gate
from mc.dev_harness_store import HarnessStore
from mc.landing_serialization import landing
from test_dev_harness_diff import repo, git


def command(*args: str) -> str:
    argv = [sys.executable, *args]
    return subprocess.list2cmdline(argv) if os.name == 'nt' else shlex.join(argv)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    from mc.blueprints import agent_routes
    monkeypatch.setattr(agent_routes, '_register_process', lambda *a: None)
    monkeypatch.setattr(agent_routes, '_unregister_process', lambda *a: None)
    monkeypatch.setattr(dev_harness_landing, 'store_for_landing', lambda: HarnessStore(tmp_path/'install/data'))
    yield


def setup(repo: Path, script: str):
    (repo/'check.py').write_text(script, encoding='utf-8')
    git(repo, 'add', 'check.py')
    git(repo, 'commit', '-qm', 'checker')
    project = {'id': 'one', 'project_path': str(repo), 'verify_command': command('check.py')}
    ok, path = worktrees.create(project, 'gate')
    assert ok, path
    wt = Path(path)
    (wt/'agent.txt').write_text('agent\n')
    git(wt, 'add', 'agent.txt')
    git(wt, 'commit', '-qm', 'agent')
    (repo/'target.txt').write_text('target\n')
    git(repo, 'add', 'target.txt')
    git(repo, 'commit', '-qm', 'target advanced')
    return project, wt


def refs(repo: Path):
    return git(repo, 'show-ref', '--heads')


def run(code=0, error=None, tail=''):
    return {'exit_code': code, 'error': error, 'output_tail': tail}


# ── Settings validation (unchanged) ───────────────────────────────────────────

@pytest.mark.parametrize('value', [None, '', []])
def test_unset_does_not_touch_git_or_reviewer(value, tmp_path):
    assert gate.verify({'verify_command': value}, tmp_path/'absent', 'missing', 'missing', None) is None


@pytest.mark.parametrize('value', [False, 0, {}, [''], ['good', 1], 'bad\0command'])
def test_invalid_commands_fail_closed(value):
    with pytest.raises(ValueError, match='invalid_verify_command'):
        gate.settings({'verify_command': value})


@pytest.mark.parametrize('value', [False, 0, -1, '20', float('inf'), float('nan')])
def test_invalid_timeout(value):
    with pytest.raises(ValueError, match='invalid_verify_timeout'):
        gate.settings({'verify_command': 'test', 'verify_timeout_seconds': value})


def test_defaults_and_order():
    assert gate.settings({'verify_command': 'one'}) == (['one'], 1200)
    assert gate.settings({'verify_command': ['one', 'two']}) == (['one', 'two'], 1200)


# ── The rule: every command exits 0, or the landing is blocked ────────────────

def test_evaluate_passes_only_when_every_command_exits_zero():
    assert gate.evaluate(['a', 'b'], [run(), run()])['status'] == 'pass'
    result = gate.evaluate(['a', 'b'], [run(), run(3, tail='boom')])
    assert result['status'] == 'blocked'
    assert result['failures'] == ['2:exit 3']
    assert 'Command 2 failed with exit 3: b' in result['message'] and 'boom' in result['message']
    assert 'Command 1' not in result['message']


def test_evaluate_names_every_failing_command_with_its_exit_code():
    result = gate.evaluate(['a', 'b', 'c'], [run(1, tail='one'), run(), run(7, tail='seven')])
    assert result['failures'] == ['1:exit 1', '3:exit 7']
    assert 'Command 1 failed with exit 1: a' in result['message']
    assert 'Command 3 failed with exit 7: c' in result['message']
    assert 'one' in result['message'] and 'seven' in result['message']


@pytest.mark.parametrize('error', ['timeout', 'output_limit'])
def test_evaluate_blocks_an_error_even_when_the_exit_code_is_zero(error):
    result = gate.evaluate(['a'], [run(0, error=error)])
    assert result['status'] == 'blocked'
    assert result['errors'] == [f'candidate:1:{error}']


def test_evaluate_blocks_a_command_that_did_not_run():
    unavailable = gate.evaluate(['a'], [run(None, error='command_unavailable:OSError')])
    assert unavailable['status'] == 'blocked' and 'did not run' in unavailable['failures'][0]
    assert gate.evaluate(['a', 'b'], [run()])['status'] == 'blocked'  # fewer runs than commands


def test_output_text_never_decides_the_verdict():
    # A green exit that prints failure-looking text passes; a red exit with no recognisable
    # output blocks. Nothing here reads the output.
    assert gate.evaluate(['a'], [run(0, tail='FAILED tests/a.py::test_x\n1 failed in 0.1s')])['status'] == 'pass'
    assert gate.evaluate(['a'], [run(1, tail='')])['status'] == 'blocked'


def test_actual_combined_result_blocks_without_moving_refs(repo):
    project, wt = setup(repo, "from pathlib import Path\nimport sys\n"
        "broken = Path('target.txt').exists() and Path('agent.txt').exists()\n"
        "print('FAILED tests/app.py::test_combined - regression' if broken else 'pass')\n"
        "sys.exit(1 if broken else 0)\n")
    # Agent alone is green. Only the combined tree exhibits the regression.
    assert gate.run_command(project['verify_command'], wt, 5, 'one')['exit_code'] == 0
    before = refs(repo)
    head = git(wt, 'rev-parse', 'HEAD')
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'blocked', detail
    result = detail['landing_gate']
    assert result['failures'] == ['1:exit 1']
    assert 'test_combined' in result['message'] and 'check.py' in result['message']
    assert result['checkout_restored']
    assert refs(repo) == before
    assert git(wt, 'symbolic-ref', '--short', 'HEAD') == 'clayrune/agent/gate'
    assert git(wt, 'rev-parse', 'HEAD') == head
    assert not (wt/'target.txt').exists()
    assert not (repo/'agent.txt').exists()
    assert git(wt, 'status', '--porcelain') == ''
    assert not worktrees.remove(project, 'gate', delete_branch=True)[0]


def test_a_red_candidate_blocks_even_when_the_target_is_equally_red(repo):
    # Round 9 (Ron): the gate no longer lands onto an already-red master.
    project, wt = setup(repo, "import sys\nprint('FAILED tests/old.py::test_old - known')\nsys.exit(1)\n")
    before = refs(repo)
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'blocked', detail
    result = detail['landing_gate']
    assert result['status'] == 'blocked' and result['failures'] == ['1:exit 1']
    assert 'known' in result['candidate'][0]['output_tail']
    assert refs(repo) == before
    assert not (repo/'agent.txt').exists()


def test_an_all_green_candidate_lands(repo):
    project, wt = setup(repo, 'print("pass")\n')
    tip = git(wt, 'rev-parse', 'HEAD')
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'clean', detail
    assert detail['landing_gate']['status'] == 'pass'
    assert detail['landing_gate']['failures'] == [] and detail['landing_gate']['message'] == ''
    assert git(repo, 'rev-parse', 'clayrune/agent/gate') == tip
    assert (repo/'agent.txt').exists() and (repo/'target.txt').exists()


def test_a_red_target_with_a_green_candidate_lands_and_the_target_is_never_run(repo, monkeypatch):
    # The checker fails on the target tip alone (no agent.txt) and passes on the merge.
    project, wt = setup(repo, "from pathlib import Path\nimport sys\n"
        "print('target alone is red')\nsys.exit(0 if Path('agent.txt').exists() else 1)\n")
    seen = []
    original = gate.run_command
    def observe(*args):
        seen.append((Path(args[1])/'agent.txt').exists())
        return original(*args)
    monkeypatch.setattr(gate, 'run_command', observe)
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'clean', detail
    assert seen == [True]  # one run, on the merged tree: no baseline phase
    assert 'baseline' not in detail['landing_gate']


def test_generic_error_regression_blocks(repo):
    project, _ = setup(repo, "from pathlib import Path\nimport sys\n"
        "print('TypeError: new' if Path('agent.txt').exists() else 'TypeError: old')\nsys.exit(2)\n")
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'blocked'
    assert detail['landing_gate']['failures'] == ['1:exit 2']
    assert 'TypeError: new' in detail['landing_gate']['message']


def test_unset_merge_result_is_historical_string(repo, monkeypatch):
    project, _ = setup(repo, 'raise SystemExit(1)\n')
    del project['verify_command']
    monkeypatch.setattr(gate, 'run_command', lambda *a: pytest.fail('unset spawned a check'))
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'clean'
    assert isinstance(detail, str)


def test_actual_timeout_is_blocked_and_restores_checkout(repo):
    project, wt = setup(repo, 'import time\ntime.sleep(5)\n')
    project['verify_timeout_seconds'] = 0.1
    before = refs(repo)
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'blocked'
    result = detail['landing_gate']
    assert result['errors'] == ['candidate:1:timeout']
    assert 'timeout' in result['failures'][0]
    assert result['checkout_restored']
    assert refs(repo) == before


def test_each_command_runs_once_on_the_merged_tree_only(repo, monkeypatch):
    project, wt = setup(repo, 'print("pass")\n')
    project['verify_command'] = [project['verify_command'], command('-c', 'print("two")')]
    calls = []
    def observe(*args):
        calls.append(git(wt, 'rev-parse', 'HEAD'))
        return {'exit_code': 0, 'error': None, 'output_tail': 'pass'}
    monkeypatch.setattr(gate, 'run_command', observe)
    with landing(repo) as owner:
        result = gate.verify(project, wt, 'master', 'clayrune/agent/gate', owner)
    assert result['status'] == 'pass'
    assert len(calls) == 2 and len(set(calls)) == 1  # two commands, one checkout
    assert calls[0] not in (git(repo, 'rev-parse', 'master'), git(repo, 'rev-parse', 'clayrune/agent/gate'))
    assert not hasattr(gate, '_cache')


def test_multiple_commands_all_run_and_one_red_blocks(repo):
    project, _ = setup(repo, "import sys\nprint('first is red')\nsys.exit(1)\n")
    project['verify_command'] = [project['verify_command'], command('-c', 'print("second")')]
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'blocked'
    result = detail['landing_gate']
    assert len(result['candidate']) == 2  # a red command does not stop the rest
    assert 'second' in result['candidate'][1]['output_tail']
    assert result['failures'] == ['1:exit 1']
    assert 'Command 2' not in result['message']


def test_multiple_green_commands_land(repo):
    project, _ = setup(repo, 'print("one")\n')
    project['verify_command'] = [project['verify_command'], command('-c', 'print("two")')]
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'clean', detail
    assert len(detail['landing_gate']['candidate']) == 2


def test_added_boot_smoke_failure_blocks_actual_merge(repo):
    project, _ = setup(repo, "from pathlib import Path\nimport sys\n"
        "print('FAIL old: known')\n"
        "if Path('agent.txt').exists(): print('SCHEDFAIL scheduler layout shifted')\n"
        "sys.exit(1)\n")
    before = refs(repo)
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'blocked'
    assert 'SCHEDFAIL scheduler layout shifted' in detail['landing_gate']['message']
    assert detail['landing_gate']['checkout_restored']
    assert refs(repo) == before


# ── Reviewer seam (unchanged contract) ────────────────────────────────────────

def test_future_reviewer_can_hold_without_disabling_existing_checks(repo):
    project, wt = setup(repo, 'print("pass")\n')
    before = refs(repo)
    with landing(repo) as owner:
        result = gate.verify(project, wt, 'master', 'clayrune/agent/gate', owner,
            reviewer=lambda pins: {'status': 'blocked', 'reason': 'cold review'})
    assert result['status'] == 'blocked'
    assert result['errors'] == ['reviewer_hold']
    assert result['candidate'][0]['exit_code'] == 0
    assert result['checkout_restored']
    assert refs(repo) == before


def test_reviewer_pass_cannot_waive_new_command_failures(repo):
    project, wt = setup(repo, "from pathlib import Path\nimport sys\n"
        "print('FAILED tests/new.py::test_new')\nsys.exit(int(Path('agent.txt').exists()))\n")
    with landing(repo) as owner:
        result = gate.verify(project, wt, 'master', 'clayrune/agent/gate', owner,
            reviewer=lambda pins: {'status': 'pass'})
    assert result['status'] == 'blocked'
    assert result['failures'] == ['1:exit 1']


@pytest.mark.parametrize('verdict', [None, {'status': 'unavailable'}])
def test_reviewer_missing_verdict_blocks(repo, verdict):
    project, wt = setup(repo, 'print("pass")\n')
    with landing(repo) as owner:
        result = gate.verify(project, wt, 'master', 'clayrune/agent/gate', owner,
            reviewer=lambda pins: verdict)
    assert result['status'] == 'blocked'
    assert result['checkout_restored']


def test_reviewer_exception_blocks_and_restores(repo):
    project, wt = setup(repo, 'print("pass")\n')
    def broken(pins):
        raise RuntimeError('review service unavailable')
    with landing(repo) as owner:
        result = gate.verify(project, wt, 'master', 'clayrune/agent/gate', owner, reviewer=broken)
    assert result['status'] == 'blocked'
    assert result['reviewer']['error'] == 'RuntimeError'
    assert result['checkout_restored']


# ── run_command plumbing (unchanged: registration, reaping, caps) ─────────────

def test_command_registration_and_unregister(repo, monkeypatch):
    from mc.blueprints import agent_routes
    events = []
    monkeypatch.setattr(agent_routes, '_register_process', lambda p, *a: events.append(('start', p.pid)))
    monkeypatch.setattr(agent_routes, '_unregister_process', lambda pid: events.append(('stop', pid)))
    result = gate.run_command(command('-c', 'print("pass")'), repo, 5, 'one')
    assert result['exit_code'] == 0
    assert events[0][0] == 'start' and events[1] == ('stop', events[0][1])


def test_registration_failure_blocks_and_reaps(repo, monkeypatch):
    from mc.blueprints import agent_routes
    processes = []
    def refuse(proc, *args):
        processes.append(proc)
        raise RuntimeError('registry unavailable')
    monkeypatch.setattr(agent_routes, '_register_process', refuse)
    result = gate.run_command(command('-c', 'import time; time.sleep(5)'), repo, 5, 'one')
    assert result['error'] == 'command_unavailable:RuntimeError'
    assert processes[0].poll() is not None
    assert gate.evaluate(['c'], [result])['status'] == 'blocked'


def test_a_red_exit_is_judged_on_the_exit_code_not_the_bounded_tail(repo):
    result = gate.run_command(command('-c',
        'import sys; print("FAILED tests/early.py::test_early - fail"); print("x" * 20000); sys.exit(1)'), repo, 5, 'one')
    assert result['exit_code'] == 1
    assert len(result['output_tail']) == gate.TAIL_CHARS
    assert 'test_early' not in result['output_tail']
    assert gate.evaluate(['c'], [result])['status'] == 'blocked'


def test_output_over_limit_is_infrastructure_block(repo, monkeypatch):
    monkeypatch.setattr(gate, 'MAX_OUTPUT_BYTES', 10)
    result = gate.run_command(command('-c', 'print("x" * 100)'), repo, 5, 'one')
    assert result['error'] == 'output_limit'
    assert result['exit_code'] == 0
    assert gate.evaluate(['c'], [result])['status'] == 'blocked'


# ── Checkout and ref safety (unchanged) ───────────────────────────────────────

def test_verifier_edits_are_preserved_and_block(repo):
    project, wt = setup(repo, "from pathlib import Path\nPath('app.py').write_text('verifier output\\n')\n")
    before = refs(repo)
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'blocked'
    assert 'verification_modified_tracked_files' in detail['landing_gate']['errors']
    assert (wt/'app.py').read_text() == 'verifier output\n'
    assert refs(repo) == before


def test_verifier_cannot_replace_checked_commit(repo, monkeypatch):
    project, wt = setup(repo, 'print("pass")\n')
    original = gate.run_command
    def commit_during_check(*args):
        result = original(*args)
        git(wt, 'commit', '--allow-empty', '-qm', 'different checkout')
        return result
    monkeypatch.setattr(gate, 'run_command', commit_during_check)
    before = refs(repo)
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'blocked'
    assert 'verification_checkout_changed' in detail['landing_gate']['errors']
    assert refs(repo) == before
    assert detail['landing_gate']['checkout_restored']


def test_conflicting_merge_blocks_without_checkout_or_ref_changes(repo):
    project, wt = setup(repo, 'print("pass")\n')
    (repo/'app.py').write_text('target = 1\n')
    git(repo, 'add', 'app.py')
    git(repo, 'commit', '-qm', 'target conflict')
    (wt/'app.py').write_text('agent = 2\n')
    git(wt, 'add', 'app.py')
    git(wt, 'commit', '-qm', 'agent conflict')
    before = refs(repo)
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'blocked'
    assert refs(repo) == before
    assert (wt/'app.py').read_text() == 'agent = 2\n'
    assert (repo/'app.py').read_text() == 'target = 1\n'


def test_check_cannot_silently_change_target_pin(repo, monkeypatch):
    project, wt = setup(repo, 'print("pass")\n')
    original = gate.run_command
    def moving(*args):
        result = original(*args)
        git(repo, 'commit', '--allow-empty', '-qm', 'external target change')
        return result
    monkeypatch.setattr(gate, 'run_command', moving)
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'blocked'
    assert 'landing_inputs_changed' in detail['landing_gate']['errors']
    assert not (repo/'agent.txt').exists()


def test_gc_preserves_blocked_work_without_caching_verdict(repo):
    project, _ = setup(repo, "from pathlib import Path\nimport sys\n"
        "print('FAILED tests/new.py::test_new')\nsys.exit(int(Path('agent.txt').exists()))\n")
    before = refs(repo)
    result = worktrees.gc_stale(project, [], use_cache=True)
    assert result['preserved'] == ['gate']
    assert result['merged'] == result['removed'] == 0
    assert refs(repo) == before
    assert worktrees._load_gc_cache(project) == {}
