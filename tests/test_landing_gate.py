"""MC-1164 checks use throwaway Git repositories, never the live target."""
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
    gate._cache.clear()
    from mc.blueprints import agent_routes
    monkeypatch.setattr(agent_routes, '_register_process', lambda *a: None)
    monkeypatch.setattr(agent_routes, '_unregister_process', lambda *a: None)
    monkeypatch.setattr(dev_harness_landing, 'store_for_landing', lambda: HarnessStore(tmp_path/'install/data'))
    yield
    gate._cache.clear()


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


@pytest.mark.parametrize(('output', 'code', 'expected'), [
    ('FAILED tests/a.py::TestA::test_one - assert 1\nERROR tests/b.py - ImportError', 1,
     ['pytest:tests/a.py::TestA::test_one', 'pytest:tests/b.py']),
    ('\x1b[31mFAILED tests/a.py::test_x[one]\x1b[0m - x\r\n', 1,
     ['pytest:tests/a.py::test_x[one]']),
    ('  ✗ opens agent\n  ✗ saves task\n❌ FAIL — 2 check(s) failed.', 1,
     ['smoke:opens agent', 'smoke:saves task']),
    ('FAIL opens tabs\n', 1, ['smoke:opens tabs']),
    ('FAIL: closes tabs\n', 1, ['smoke:closes tabs']),
    ('FAILED tests/a.py::test_x[with spaces] - assert false', 1,
     ['pytest:tests/a.py::test_x[with spaces]']),
    ('log\nTypeError: broken\nfinished', 2, ['exit:2:TypeError: broken']),
    ('', 4, ['exit:4:<no output>']),
    ('FAILED tests/a.py::test_x - x', 0, []),
])
def test_signatures(output, code, expected):
    assert gate.failure_signatures(output, code) == expected


def test_generic_signature_normalizes_checkout_path(tmp_path):
    assert gate.failure_signatures(f'Error: {tmp_path}/bad', 1, tmp_path) == ['exit:1:Error: <worktree>/bad']


def run(signatures=(), error=None):
    return {'signatures': list(signatures), 'error': error}


def test_only_new_failures_block_and_command_positions_are_distinct():
    old = [run(['pytest:old']), run()]
    same = gate.compare(old, [run(['pytest:old']), run()])
    assert same['status'] == 'pass'
    assert same['baseline_failures'] == ['1:pytest:old']
    new = gate.compare(old, [run(), run(['pytest:old'])])
    assert new['status'] == 'blocked'
    assert new['new_failures'] == ['2:pytest:old']
    assert gate.compare(old, [run(), run()])['status'] == 'pass'
    assert gate.compare(old, [run(['pytest:old', 'pytest:new']), run()])['new_failures'] == ['1:pytest:new']


@pytest.mark.parametrize('phase', ['baseline', 'candidate'])
def test_timeout_blocks_even_if_present_in_both(phase):
    old, current = [run()], [run()]
    (old if phase == 'baseline' else current)[0]['error'] = 'timeout'
    assert gate.compare(old, current)['status'] == 'blocked'
    assert gate.compare([run(error='timeout')], [run(error='timeout')])['status'] == 'blocked'


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
    assert result['new_failures'] == ['1:pytest:tests/app.py::test_combined']
    assert result['baseline_failures'] == []
    assert result['checkout_restored']
    assert refs(repo) == before
    assert git(wt, 'symbolic-ref', '--short', 'HEAD') == 'clayrune/agent/gate'
    assert git(wt, 'rev-parse', 'HEAD') == head
    assert not (wt/'target.txt').exists()
    assert not (repo/'agent.txt').exists()
    assert git(wt, 'status', '--porcelain') == ''
    assert not worktrees.remove(project, 'gate', delete_branch=True)[0]


def test_existing_failures_reported_but_allow_landing(repo):
    project, wt = setup(repo, "import sys\nprint('FAILED tests/old.py::test_old - known')\nsys.exit(1)\n")
    tip = git(wt, 'rev-parse', 'HEAD')
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'clean', detail
    result = detail['landing_gate']
    assert result['status'] == 'pass'
    assert result['new_failures'] == []
    assert result['baseline_failures'] == ['1:pytest:tests/old.py::test_old']
    assert 'known' in result['candidate'][0]['output_tail']
    assert git(repo, 'rev-parse', 'clayrune/agent/gate') == tip
    assert (repo/'agent.txt').exists() and (repo/'target.txt').exists()


def test_generic_error_regression_blocks(repo):
    project, _ = setup(repo, "from pathlib import Path\nimport sys\n"
        "print('TypeError: new' if Path('agent.txt').exists() else 'TypeError: old')\nsys.exit(2)\n")
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'blocked'
    assert detail['landing_gate']['new_failures'] == ['1:exit:2:TypeError: new']


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
    assert 'baseline:1:timeout' in result['errors']
    assert 'candidate:1:timeout' in result['errors']
    assert result['checkout_restored']
    assert refs(repo) == before
    assert not gate._cache


def test_baseline_cache_pins_sha_commands_and_timeout(repo, monkeypatch):
    project, wt = setup(repo, 'print("pass")\n')
    calls = []
    def observe(*args):
        calls.append(git(wt, 'rev-parse', 'HEAD'))
        return {'exit_code': 0, 'signatures': [], 'error': None, 'output_tail': 'pass'}
    monkeypatch.setattr(gate, 'run_command', observe)
    def verify():
        with landing(repo) as owner:
            return gate.verify(project, wt, 'master', 'clayrune/agent/gate', owner)
    assert verify()['status'] == 'pass'
    assert len(calls) == 2
    assert verify()['baseline_cached'] is True
    assert len(calls) == 3
    project['verify_timeout_seconds'] = 5
    assert verify()['baseline_cached'] is False
    assert len(calls) == 5
    project['verify_command'] = command('-c', 'pass')
    assert verify()['baseline_cached'] is False
    assert len(calls) == 7
    (repo/'new-target.txt').write_text('new target')
    git(repo, 'add', 'new-target.txt')
    git(repo, 'commit', '-qm', 'new target')
    assert verify()['baseline_cached'] is False
    assert len(calls) == 9


def test_multiple_commands_continue_and_keep_failure_scope(repo):
    project, _ = setup(repo, "import sys\nprint('FAILED tests/old.py::test_old - known')\nsys.exit(1)\n")
    project['verify_command'] = [project['verify_command'], command('-c', 'print("second")')]
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'clean'
    assert len(detail['landing_gate']['candidate']) == 2
    assert 'second' in detail['landing_gate']['candidate'][1]['output_tail']


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
    assert result['new_failures'] == ['1:pytest:tests/new.py::test_new']


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


def test_bounded_tail_still_parses_early_failure(repo):
    result = gate.run_command(command('-c',
        'import sys; print("FAILED tests/early.py::test_early - fail"); print("x" * 20000); sys.exit(1)'), repo, 5, 'one')
    assert result['signatures'] == ['pytest:tests/early.py::test_early']
    assert len(result['output_tail']) == gate.TAIL_CHARS
    assert 'test_early' not in result['output_tail']


def test_output_over_limit_is_infrastructure_block(repo, monkeypatch):
    monkeypatch.setattr(gate, 'MAX_OUTPUT_BYTES', 10)
    result = gate.run_command(command('-c', 'print("x" * 100)'), repo, 5, 'one')
    assert result['error'] == 'output_limit'


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
