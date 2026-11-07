"""MC-1164 checks use throwaway Git repositories, never the live target."""
from __future__ import annotations

import os
import shlex
import shutil
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


@pytest.mark.parametrize('line', [
    # Failure-print families audited across tools/smoke/*.mjs, including boot guards.
    'SCHEDFAIL scheduler layout guard: grid never rendered.',
    'CALFAIL calendar guard:',
    '✗ opens tab', '✘ opens tab', '× opens tab', '❌ opens tab',
    '❌ FAIL — opens tab', '❌ FAIL: opens tab', 'FAIL opens tab',
    'FAIL: opens tab', 'FAIL — opens tab',
    'HARNESS ERROR: page crashed', 'PAGE ERROR ReferenceError: missing',
    'capture failed: selector missing', 'bench failed: page crashed',
    'journey smoke error: the page RENDERER CRASHED',
    'TypeError: missing method', 'AssertionError [ERR_ASSERTION]: missing tab',
    'not ok 2 - opens tab',
    'NEWSTYLEFAIL opens tab', 'fatal: missing checkout',
    'CUSTOM error: 2 check(s) failed',
    'selector not found: #missing', 'could not find SW_VERSION in static/sw.js',
    'SPLIT_PROJECT is required (a project id on this server).',
    'usage: node capture-screenshot.mjs <url> <output-name.png> [selector]',
    'output name must be a bare filename (no path separators)',
    'process registration unreachable for pid 123: refused',
])
def test_each_smoke_failure_family_is_additive(line):
    baseline = gate.failure_signatures('FAIL old: known', 1)
    candidate = gate.failure_signatures('FAIL old: known\n' + line, 1)
    assert set(candidate) > set(baseline)
    assert gate.compare([run(baseline)], [run(candidate)])['status'] == 'blocked'


@pytest.mark.parametrize('line', [
    '❌ FAIL — 2 check(s) failed.', '❌ FAIL — 2/5 check(s) failed.',
    '2 check(s) FAILED', 'FAILED (2)', 'FAILED: 2', '2 FAILED',
    '❌ FAIL — 2 Desk v1 check(s) regressed.', '❌ FAIL: 2 problem(s).',
    '2 assertion(s) failed', 'RTL smoke: 2 failure(s).',
    'human-proof-busy: 2 failure(s)', 'RESULT: FAIL', 'Checks=5 failures=2',
    'MC-1070 wired controls: fails=2',
])
def test_smoke_aggregate_counts_do_not_create_new_check_failures(line):
    assert gate.failure_signatures('FAIL old: known\n' + line, 1) == ['smoke:old: known']
    assert gate.failure_signatures(line, 1)  # Summary-only exits still fail closed.


@pytest.mark.parametrize('header', ['CALFAIL calendar guard:', 'FAIL floor guard:',
                                   '❌ model picker guard:'])
@pytest.mark.parametrize('bullet', ['•', '*', '-'])
def test_boot_guard_detail_is_not_hidden_by_existing_heading(header, bullet):
    old = gate.failure_signatures(f'{header}\n       {bullet} old finding', 1)
    new = gate.failure_signatures(f'{header}\n       {bullet} old finding\n       {bullet} new finding', 1)
    assert len(set(new) - set(old)) == 1
    assert 'new finding' in next(iter(set(new) - set(old)))


def test_mixed_failure_formats_collect_every_error_line():
    output = ('FAILED tests/old.py::test_old - known\nFAIL old smoke: known\n'
              'SCHEDFAIL scheduler shifted\nCALFAIL calendar clipped\n'
              'TypeError: new method missing\nCUSTOM failure: another regression\n')
    assert gate.failure_signatures(output, 1) == [
        'exit:1:CUSTOM failure: another regression',
        'exit:1:TypeError: new method missing',
        'pytest:tests/old.py::test_old',
        'smoke:CALFAIL:calendar clipped', 'smoke:SCHEDFAIL:scheduler shifted',
        'smoke:old smoke: known',
    ]


def test_failure_noise_is_normalized_without_erasing_assertion_values(tmp_path):
    def output(root, duration, external, location):
        return (f'FAIL old: known\nCUSTOM error: {root}/check.mjs:{location} after {duration}\n'
                f'TypeError: {external}:{location} expected 2, got 3\n')
    old_root, new_root = tmp_path/'old', tmp_path/'new'
    old = gate.failure_signatures(output(old_root, '123.4ms', '/tmp/first/lib.mjs', '40:5'), 1, old_root)
    new = gate.failure_signatures(output(new_root, '2 seconds', r'C:\temp\second\lib.mjs', '80:9'), 1, new_root)
    assert old == new
    assert any('expected 2, got 3' in s for s in new)
    changed = gate.failure_signatures(output(new_root, '2 seconds', '/tmp/lib.mjs', '80:9')
                                      .replace('got 3', 'got 4'), 1, new_root)
    assert gate.compare([run(old)], [run(changed)])['status'] == 'blocked'


def test_generic_fallback_keeps_all_errors_without_known_formats():
    assert gate.failure_signatures('TypeError: first\nError: second\nfinished', 1) == [
        'exit:1:Error: second', 'exit:1:TypeError: first']


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


def test_failing_baseline_is_fresh_after_runtime_repair(repo):
    runtime_ready = repo.parent/'runtime-ready'
    project, wt = setup(repo, "from pathlib import Path\nimport sys\n"
        f"broken = not Path({str(runtime_ready)!r}).exists() or Path('agent.txt').exists()\n"
        "print('FAIL runtime: broken' if broken else 'pass')\nsys.exit(int(broken))\n")
    before = refs(repo)
    def verify():
        with landing(repo) as owner:
            return gate.verify(project, wt, 'master', 'clayrune/agent/gate', owner)
    first = verify()
    assert first['status'] == 'pass'  # Matching failures can be waived on this run.
    assert first['baseline_cached'] is False
    assert first['baseline_failures'] == ['1:smoke:runtime: broken']
    assert not gate._cache
    runtime_ready.write_text('dependencies/config repaired')  # No SHA/env/command change.
    second = verify()
    assert second['baseline_cached'] is False
    assert second['status'] == 'blocked'
    assert second['baseline_failures'] == []
    assert second['new_failures'] == ['1:smoke:runtime: broken']
    assert second['checkout_restored']
    assert refs(repo) == before


def test_non_green_cache_entry_cannot_exempt_candidate(repo, monkeypatch):
    project, wt = setup(repo, 'print("pass")\n')
    def verify():
        with landing(repo) as owner:
            return gate.verify(project, wt, 'master', 'clayrune/agent/gate', owner)
    assert verify()['status'] == 'pass'
    key = next(iter(gate._cache))
    gate._cache[key] = [{'exit_code': 1, 'signatures': ['smoke:regression'], 'error': None}]
    monkeypatch.setattr(gate, 'run_command', lambda *a: {
        'exit_code': int((wt/'agent.txt').exists()),
        'signatures': ['smoke:regression'] if (wt/'agent.txt').exists() else [],
        'error': None, 'output_tail': ''})
    result = verify()
    assert result['baseline_cached'] is False
    assert result['status'] == 'blocked'
    assert result['new_failures'] == ['1:smoke:regression']


def test_added_boot_smoke_failure_blocks_actual_merge(repo):
    project, _ = setup(repo, "from pathlib import Path\nimport sys\n"
        "print('FAIL old: known')\n"
        "if Path('agent.txt').exists(): print('SCHEDFAIL scheduler layout shifted')\n"
        "sys.exit(1)\n")
    before = refs(repo)
    status, detail = worktrees.merge_back(project, 'gate')
    assert status == 'blocked'
    assert detail['landing_gate']['new_failures'] == ['1:smoke:SCHEDFAIL:scheduler layout shifted']
    assert detail['landing_gate']['checkout_restored']
    assert refs(repo) == before


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


# ── Round two: failure identity must not carry runner noise (Fenn P1) ─────────
# Every output below is captured from the real runner on a tiny fixture, then
# fed through failure_signatures; nothing is hand-typed.

OLD_FAIL = 'def test_old():\n    x = 1\n    assert x == 2\n'
NEW_FAIL = 'def test_new():\n    assert "a" == "b"\n'
PASS_TEST = 'def test_fine():\n    assert True\n'
needs_node = pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
NODE_HEAD = "import test, { describe, it } from 'node:test';\nimport assert from 'node:assert';\n"
NODE_OLD = "test('old thing', () => { assert.strictEqual(1, 2); });\n"
NODE_NEW = "test('new thing', () => { assert.strictEqual(3, 4); });\n"
NODE_PASS = "test('fine', () => {});\n"
_STRIPPED_ENV = ('PYTEST_ADDOPTS', 'PYTEST_CURRENT_TEST', 'NODE_OPTIONS', 'PYTHONPATH')


class Captured(tuple):
    """(output, exit code) of a real runner, plus the cwd it ran in and the command line
    that ran it: run_command parses with that cwd as the root and that command, so the
    helpers below default to both."""
    root: Path
    command: str


def captured(argv, cwd: Path, extra_env=None):
    env = {k: v for k, v in os.environ.items() if k not in _STRIPPED_ENV}
    env.update(extra_env or {})
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=120)
    assert done.returncode != 0, done.stdout + done.stderr
    result = Captured((done.stdout + done.stderr, done.returncode))
    result.root = cwd
    result.command = subprocess.list2cmdline([str(a) for a in argv]) if os.name == 'nt' else shlex.join(
        [str(a) for a in argv])
    return result


def real_pytest(tmp_path: Path, label: str, source: str, extra_env=None):
    folder = tmp_path/label
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'test_fx.py').write_text(source, encoding='utf-8')
    return captured([sys.executable, '-m', 'pytest', 'test_fx.py', '-q', '-ra', '-p', 'no:cacheprovider',
                     '-o', 'addopts=', '--rootdir', str(folder)], folder, extra_env)


def real_tap(tmp_path: Path, label: str, source: str):
    folder = tmp_path/label
    folder.mkdir()
    (folder/'fx.test.mjs').write_text(source, encoding='utf-8')
    return captured(['node', '--test', '--test-reporter=tap', 'fx.test.mjs'], folder)


def sigs(captured_run, root=None):
    output, code = captured_run
    return gate.failure_signatures(output, code, root or captured_run.root,
                                   gate._direct_node(captured_run.command))


def verdict(old, new):
    return gate.compare([run(old)], [run(new)])


def test_real_runner_output_is_what_the_fixtures_assume(tmp_path):
    output, _ = real_pytest(tmp_path, 'p', PASS_TEST + OLD_FAIL)
    assert 'FAILED test_fx.py::test_old' in output and '1 failed, 1 passed' in output


def test_adding_a_passing_pytest_test_is_not_a_new_failure(tmp_path):
    old = sigs(real_pytest(tmp_path, 'old', OLD_FAIL))
    new = sigs(real_pytest(tmp_path, 'new', OLD_FAIL + PASS_TEST))
    assert verdict(old, new)['status'] == 'pass', verdict(old, new)['new_failures']


@needs_node
def test_adding_a_passing_tap_test_is_not_a_new_failure(tmp_path):
    old = sigs(real_tap(tmp_path, 'old', NODE_HEAD + NODE_OLD))
    new = sigs(real_tap(tmp_path, 'new', NODE_HEAD + NODE_OLD + NODE_PASS))
    assert verdict(old, new)['status'] == 'pass', verdict(old, new)['new_failures']


@needs_node
def test_tap_test_number_change_is_not_a_new_failure(tmp_path):
    old = sigs(real_tap(tmp_path, 'old', NODE_HEAD + NODE_OLD))
    new = sigs(real_tap(tmp_path, 'new', NODE_HEAD + NODE_PASS + NODE_OLD))
    assert verdict(old, new)['status'] == 'pass', verdict(old, new)['new_failures']
    assert 'tap:old thing @fx.test.mjs' in old and old == new


def test_moving_a_failing_pytest_assertion_is_not_a_new_failure(tmp_path):
    old_run = real_pytest(tmp_path, 'old', OLD_FAIL)
    new_run = real_pytest(tmp_path, 'new', '# moved\n\n\n' + OLD_FAIL)
    assert 'test_fx.py:3:' in old_run[0] and 'test_fx.py:6:' in new_run[0]  # relative, line moved
    assert verdict(sigs(old_run), sigs(new_run))['status'] == 'pass'


@needs_node
def test_moving_a_failing_tap_assertion_is_not_a_new_failure(tmp_path):
    old_run = real_tap(tmp_path, 'old', NODE_HEAD + NODE_OLD)
    new_run = real_tap(tmp_path, 'new', NODE_HEAD + '// moved\n\n\n' + NODE_OLD)
    assert 'fx.test.mjs:3:' in old_run[0] and 'fx.test.mjs:6:' in new_run[0]  # absolute location, line moved
    root_old, root_new = tmp_path/'old', tmp_path/'new'
    assert verdict(sigs(old_run, root_old), sigs(new_run, root_new))['status'] == 'pass'


@pytest.mark.skipif(shutil.which('pyright') is None, reason='pyright not installed')
def test_moving_a_failing_assertion_in_absolute_path_output_is_not_a_new_failure(tmp_path):
    def real(label, pad):
        folder = tmp_path/label
        folder.mkdir()
        (folder/'fx.py').write_text(pad + 'x: int = "a"\n', encoding='utf-8')
        return captured(['pyright', str(folder/'fx.py')], folder)
    old_run, new_run = real('old', ''), real('new', '\n\n\n')
    assert 'fx.py:1:' in old_run[0].replace('\\', '/') and 'fx.py:4:' in new_run[0].replace('\\', '/')
    assert str(tmp_path/'old').lower() in old_run[0].lower()  # genuinely absolute in the real output
    assert verdict(sigs(old_run), sigs(new_run))['status'] == 'pass'


def test_a_genuinely_new_failing_pytest_test_blocks(tmp_path):
    old = sigs(real_pytest(tmp_path, 'old', OLD_FAIL + PASS_TEST))
    new = sigs(real_pytest(tmp_path, 'new', OLD_FAIL + PASS_TEST + NEW_FAIL))
    result = verdict(old, new)
    assert result['status'] == 'blocked'
    assert any('pytest:test_fx.py::test_new' in s for s in result['new_failures'])


@needs_node
def test_a_genuinely_new_failing_tap_test_blocks(tmp_path):
    old = sigs(real_tap(tmp_path, 'old', NODE_HEAD + NODE_OLD))
    new = sigs(real_tap(tmp_path, 'new', NODE_HEAD + NODE_PASS + NODE_OLD + NODE_NEW))
    result = verdict(old, new)
    assert result['status'] == 'blocked'
    assert result['new_failures'] == ['1:tap:new thing @fx.test.mjs']


@needs_node
def test_same_named_tap_test_failing_in_a_second_suite_blocks(tmp_path):
    suite = "describe('%s', () => { it('works', () => { %s }); });\n"
    fail, ok = 'assert.strictEqual(1, 2);', ''
    old = sigs(real_tap(tmp_path, 'old', NODE_HEAD + suite % ('suiteA', fail) + suite % ('suiteB', ok)))
    new = sigs(real_tap(tmp_path, 'new', NODE_HEAD + suite % ('suiteA', fail) + suite % ('suiteB', fail)))
    assert verdict(old, new)['status'] == 'blocked'


def test_a_new_failure_in_an_unrecognised_format_still_blocks_beside_a_known_one(tmp_path):
    output, code = real_pytest(tmp_path, 'old', OLD_FAIL + PASS_TEST)
    old = gate.failure_signatures(output, code)
    new = gate.failure_signatures(output + 'widgetcheck: Fatal: widget exploded\n', code)
    result = verdict(old, new)
    assert result['status'] == 'blocked'
    assert any('widget exploded' in s for s in result['new_failures'])


@pytest.mark.parametrize('line', [
    '============================== 1 failed, 1 passed in 0.06s ==============================',
    '1 failed, 3 passed, 2 skipped, 1 warning in 61.00s (0:01:01)', '2 errors in 0.10s',
    '# tests 2', '# pass 1', '# fail 1', '# cancelled 0', '# duration_ms 57.3414',
    'ℹ tests 2', 'ℹ fail 1', 'ℹ duration_ms 57.3414'])
def test_runner_summary_count_lines_are_not_failure_identities(line):
    assert gate.failure_signatures('FAILED tests/a.py::test_x - boom\n' + line, 1) == ['pytest:tests/a.py::test_x']
    assert gate.failure_signatures(line, 1)  # a summary alone still fails closed


@pytest.mark.parametrize(('before', 'after'), [
    ('Error: bad at tests/x.py:42', 'Error: bad at tests/x.py:97'),
    (r'Error: bad at src\x.mjs:4:9', r'Error: bad at src\x.mjs:70:2'),
    ('Error: bad at ./x.ts:4', 'Error: bad at ./x.ts:5:5'),
])
def test_line_and_column_are_stripped_from_relative_paths(before, after):
    assert gate.failure_signatures(before, 1) == gate.failure_signatures(after, 1)


def test_ports_and_hosts_are_not_mistaken_for_line_numbers():
    assert gate.failure_signatures('Error: refused example.com:8080', 1) != \
        gate.failure_signatures('Error: refused example.com:9090', 1)


def test_pytest_without_a_short_summary_still_names_each_failure(tmp_path):
    def real(label, source):
        folder = tmp_path/label
        folder.mkdir()
        (folder/'test_fx.py').write_text(source, encoding='utf-8')
        return captured([sys.executable, '-m', 'pytest', 'test_fx.py', '-q', '-rN', '-p', 'no:cacheprovider',
                         '-o', 'addopts=', '--rootdir', str(folder)], folder)
    old_run = real('old', OLD_FAIL + PASS_TEST)
    new_run = real('new', OLD_FAIL + PASS_TEST + OLD_FAIL.replace('test_old', 'test_also'))
    assert 'FAILED' not in old_run[0]  # no short summary: only the report headers name the test
    assert verdict(sigs(old_run), sigs(real('moved', '#\n#\n' + OLD_FAIL + PASS_TEST)))['status'] == 'pass'
    result = verdict(sigs(old_run), sigs(new_run))
    assert result['status'] == 'blocked'
    assert any('test_also' in s for s in result['new_failures'])


# ── Round three: report sections and the node `spec` reporter (Fenn P1s) ─────
# Same rule: every output is captured from the real runner, none is hand-typed.

PASS_PRINTS = 'def test_ok():\n    print("hello")\n'
FAIL_PRINTS = 'def test_ok():\n    print("hello")\n    assert 1 == 2\n'


def real_pytest_flags(tmp_path: Path, label: str, source: str, flags):
    folder = tmp_path/label
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'test_fx.py').write_text(source, encoding='utf-8')
    return captured([sys.executable, '-m', 'pytest', 'test_fx.py', '-q', *flags, '-p', 'no:cacheprovider',
                     '-o', 'addopts=', '--rootdir', str(folder)], folder)


def real_spec(tmp_path: Path, label: str, source: str):
    """Node 24's default reporter for a non-TTY stdout, with no --test-reporter flag."""
    folder = tmp_path/label
    folder.mkdir(parents=True, exist_ok=True)
    (folder/'fx.test.mjs').write_text(source, encoding='utf-8')
    return captured(['node', '--test', 'fx.test.mjs'], folder)


def suite_source(*bodies):
    return NODE_HEAD + ''.join(
        "describe('%s', () => { it('works', () => { %s }); });\n" % (name, body) for name, body in bodies)


@pytest.mark.parametrize('flag', ['-rP', '-rA'])
def test_passing_test_with_captured_output_turning_red_blocks(tmp_path, flag):
    # Fenn P1-a, verbatim: one failure + one passing test whose output -rP prints
    # under `=== PASSES ===`; then the passing test fails too.
    old_run = real_pytest_flags(tmp_path, 'old', OLD_FAIL + PASS_PRINTS, [flag])
    new_run = real_pytest_flags(tmp_path, 'new', OLD_FAIL + FAIL_PRINTS, [flag])
    assert '= PASSES =' in old_run[0] and '_ test_ok _' in old_run[0]  # the heading really is there
    result = verdict(sigs(old_run), sigs(new_run))
    assert result['status'] == 'blocked'
    assert any('test_ok' in s for s in result['new_failures'])


@pytest.mark.parametrize('flag', ['-rP', '-rA'])
def test_adding_a_passing_captured_output_test_is_not_a_new_failure(tmp_path, flag):
    old_run = real_pytest_flags(tmp_path, 'old', OLD_FAIL, [flag])
    new_run = real_pytest_flags(tmp_path, 'new', OLD_FAIL + PASS_PRINTS, [flag])
    assert '_ test_ok _' in new_run[0]
    assert not any('test_ok' in s for s in sigs(new_run))
    assert verdict(sigs(old_run), sigs(new_run))['status'] == 'pass'


def test_error_section_headings_still_name_failures(tmp_path):
    # Setup errors are reported under `=== ERRORS ===` with `_ ERROR at setup of x _`.
    broken = 'import pytest\n@pytest.fixture\ndef boom():\n    raise RuntimeError("x")\ndef test_e(boom):\n    pass\n'
    run = real_pytest_flags(tmp_path, 'e', broken + PASS_PRINTS, ['-rN', '-rP'])
    assert '= ERRORS =' in run[0]
    assert any(s.startswith('pytest-report:') and 'test_e' in s for s in sigs(run))
    assert not any('test_ok' in s for s in sigs(run))


@needs_node
def test_real_spec_output_is_what_the_fixtures_assume(tmp_path):
    output, _ = real_spec(tmp_path, 's', NODE_HEAD + NODE_OLD + NODE_PASS)
    assert '✖ old thing (' in output and '✖ failing tests:' in output and '\ntest at ' in output


@needs_node
def test_spec_failure_with_the_same_assertion_as_an_existing_one_blocks(tmp_path):
    # Fenn P1-b, verbatim: `old thing` fails; `new thing` is added with the same assert.equal(1, 2).
    head = NODE_HEAD
    old_run = real_spec(tmp_path, 'old', head + "test('old thing', () => { assert.equal(1, 2); });\n")
    new_run = real_spec(tmp_path, 'new', head + "test('old thing', () => { assert.equal(1, 2); });\n"
                        "test('new thing', () => { assert.equal(1, 2); });\n")
    assert sigs(old_run) != sigs(new_run)
    result = verdict(sigs(old_run), sigs(new_run))
    assert result['status'] == 'blocked'
    assert result['new_failures'] == ['1:spec:new thing @fx.test.mjs']


@needs_node
def test_spec_names_are_suite_qualified_and_the_recap_adds_nothing(tmp_path):
    out, code = real_spec(tmp_path, 's', suite_source(('suiteA', 'assert.equal(1, 2);'), ('suiteB', 'assert.equal(1, 2);')))
    signatures = gate.failure_signatures(out, code)
    assert {'spec:suiteA > works @fx.test.mjs', 'spec:suiteB > works @fx.test.mjs'} <= set(signatures)
    assert not any(s.startswith('spec:works') for s in signatures)   # the recap's own copy adds nothing
    assert [s for s in signatures if s.startswith('spec:')] == [
        'spec:suiteA', 'spec:suiteA > works @fx.test.mjs', 'spec:suiteB', 'spec:suiteB > works @fx.test.mjs']


@needs_node
def test_same_named_spec_test_failing_in_a_second_suite_blocks(tmp_path):
    fail, ok = 'assert.equal(1, 2);', ''
    old = sigs(real_spec(tmp_path, 'old', suite_source(('suiteA', fail), ('suiteB', ok))))
    new = sigs(real_spec(tmp_path, 'new', suite_source(('suiteA', fail), ('suiteB', fail))))
    result = verdict(old, new)
    assert result['status'] == 'blocked'
    assert 'spec:suiteB > works @fx.test.mjs' in [s.split(':', 1)[1] for s in result['new_failures']]


@needs_node
def test_nested_spec_subtest_failure_is_named_under_its_parent(tmp_path):
    source = NODE_HEAD + ("test('parent', async (t) => {\n  await t.test('child ok', () => {});\n"
                          "  await t.test('child bad', () => { assert.equal(1, 2); });\n});\n")
    assert 'spec:parent > child bad @fx.test.mjs' in sigs(real_spec(tmp_path, 's', source))


@needs_node
def test_spec_stability_adding_a_passing_test_or_moving_the_assertion_is_not_new(tmp_path):
    old_run = real_spec(tmp_path, 'old', NODE_HEAD + NODE_OLD)
    added = real_spec(tmp_path, 'added', NODE_HEAD + NODE_PASS + NODE_OLD + NODE_PASS)
    moved = real_spec(tmp_path, 'moved', NODE_HEAD + '// moved\n\n\n' + NODE_OLD)
    assert 'fx.test.mjs:3:' in old_run[0] and 'fx.test.mjs:6:' in moved[0]  # the recap and stack really moved
    for label, new_run in (('added', added), ('moved', moved)):
        assert verdict(sigs(old_run, tmp_path/'old'), sigs(new_run, tmp_path/label))['status'] == 'pass'


def test_two_node_runs_in_one_output_are_both_parsed_past_the_first_recap():
    # Two node invocations in one command output: the second starts after the first's recap.
    first = "✖ one (0.5ms)\nℹ tests 1\nℹ fail 1\n\n✖ failing tests:\n\ntest at a.test.mjs:3:1\n✖ one (0.5ms)\n  Error: x\n"
    second = "✖ two (0.4ms)\nℹ tests 1\nℹ fail 1\n"
    assert gate.failure_signatures(first + second, 1) == sorted(
        {'spec:one @a.test.mjs', 'spec:two', 'exit:1:Error: x'})


# ── Round four: provenance of a banner, and a count backstop (Fenn, 2026-11-07) ─
# Same rule: every output is captured from the real runner (pytest, node --test).

BANNER_OLD = 'def test_old():\n    print("=== PASSES ===")\n    assert 1 == 2\n'
BANNER_NEW = 'def test_new():\n    assert 1 == 2\n'
OLD_FAIL_PRINTS = 'def test_old():\n    print("hello")\n    assert 1 == 2\n'
PASS_PRINTS_TOO = 'def test_ok_too():\n    print("again")\n'
FILE_FAIL = "test('works', () => { assert.equal(1, 2); });\n"
FILE_OK = "test('works', () => {});\n"


def counted(captured_run, root=None):
    """What run_command stores per command: identities plus the runner's own totals."""
    output, code = captured_run
    root = root or captured_run.root
    return {'exit_code': code, 'signatures': sigs(captured_run, root),
            'failure_totals': gate.failure_totals(output, code, root),
            'proof': gate.prove(output, code, root, captured_run.command), 'error': None}


def full_verdict(old_run, new_run, old_root=None, new_root=None):
    return gate.compare([counted(old_run, old_root)], [counted(new_run, new_root)])


def real_two_files(tmp_path: Path, label: str, first: str, second: str, reporter: str):
    folder = tmp_path/label
    folder.mkdir(parents=True, exist_ok=True)
    for name, body in (('a.test.mjs', first), ('b.test.mjs', second)):
        (folder/name).write_text(NODE_HEAD + body, encoding='utf-8')
    return captured(['node', '--test', f'--test-reporter={reporter}', 'a.test.mjs', 'b.test.mjs'], folder)


@pytest.mark.parametrize('flag', ['-rP', '-rA'])
def test_a_banner_printed_by_a_test_does_not_hide_the_next_failure_heading(tmp_path, flag):
    # Fenn P1 (banner_old / banner_new): test_old prints `=== PASSES ===`, then a second
    # failing test with the same assertion is added. The printed banner used to move the
    # parser into PASSES, so the second real heading was ignored and the gate said pass.
    old_run = real_pytest_flags(tmp_path, 'old', BANNER_OLD, [flag])
    new_run = real_pytest_flags(tmp_path, 'new', BANNER_OLD + BANNER_NEW, [flag])
    assert 'Captured stdout call' in old_run[0] and '\n=== PASSES ===' in old_run[0].replace('\r', '')
    by_identity = verdict(sigs(old_run), sigs(new_run))  # no totals: the heading alone must block
    assert by_identity['status'] == 'blocked'
    assert any('test_new' in s for s in by_identity['new_failures'])
    assert full_verdict(old_run, new_run)['status'] == 'blocked'


def test_a_real_passes_banner_after_captured_output_still_ends_the_failures_section(tmp_path):
    # The failing test has captured output, so its block is open when pytest's own
    # `=== PASSES ===` arrives. That banner is the right width and must still count.
    old_run = real_pytest_flags(tmp_path, 'old', OLD_FAIL_PRINTS + PASS_PRINTS, ['-rP'])
    new_run = real_pytest_flags(tmp_path, 'new', OLD_FAIL_PRINTS + PASS_PRINTS + PASS_PRINTS_TOO, ['-rP'])
    assert '= PASSES =' in new_run[0] and 'hello' in new_run[0] and '_ test_ok_too _' in new_run[0]
    assert not any('test_ok' in s for s in sigs(new_run))
    assert full_verdict(old_run, new_run)['status'] == 'pass'


def test_banner_width_follows_the_terminal_not_a_constant(tmp_path):
    def real(label, source):
        folder = tmp_path/label
        folder.mkdir()
        (folder/'test_fx.py').write_text(source, encoding='utf-8')
        return captured([sys.executable, '-m', 'pytest', 'test_fx.py', '-q', '-rP', '-p', 'no:cacheprovider',
                         '-o', 'addopts=', '--rootdir', str(folder)], folder, {'COLUMNS': '120'})
    old_run = real('old', OLD_FAIL_PRINTS + PASS_PRINTS)
    new_run = real('new', OLD_FAIL_PRINTS + PASS_PRINTS + PASS_PRINTS_TOO)
    assert max(len(line) for line in old_run[0].splitlines()) >= 110  # not the 80-column default
    assert not any('test_ok' in s for s in sigs(new_run))
    assert full_verdict(old_run, new_run)['status'] == 'pass'


def test_runner_failure_totals_from_real_output(tmp_path):
    assert gate.failure_totals(*real_pytest(tmp_path, 'p', OLD_FAIL + PASS_TEST)) == [1]
    two = real_pytest(tmp_path, 'two', OLD_FAIL + NEW_FAIL + PASS_TEST)
    assert '2 failed, 1 passed' in two[0] and gate.failure_totals(*two) == [2]
    broken = 'import pytest\n@pytest.fixture\ndef boom():\n    raise RuntimeError("x")\ndef test_e(boom):\n    pass\n'
    mixed = real_pytest(tmp_path, 'mixed', OLD_FAIL + broken)
    assert '1 failed, 1 error' in mixed[0] and gate.failure_totals(*mixed) == [2]   # failures + errors
    assert gate.failure_totals('anything at all', 0) == []
    assert gate.failure_totals('boom: something unrecognised', 3) == []   # no total: identities only


@needs_node
def test_node_failure_totals_from_real_spec_and_tap_output(tmp_path):
    spec = real_spec(tmp_path, 's', NODE_HEAD + NODE_OLD + NODE_NEW + NODE_PASS)
    tap = real_tap(tmp_path, 't', NODE_HEAD + NODE_OLD + NODE_NEW + NODE_PASS)
    assert 'ℹ fail 2' in spec[0] and '# fail 2' in tap[0]
    assert gate.failure_totals(*spec) == gate.failure_totals(*tap) == [2, 0]   # fail, cancelled


def test_run_command_records_the_runner_total(tmp_path):
    folder = tmp_path/'rc'
    folder.mkdir()
    (folder/'test_fx.py').write_text(OLD_FAIL + NEW_FAIL + PASS_TEST, encoding='utf-8')
    done = gate.run_command(command('-m', 'pytest', 'test_fx.py', '-q', '-ra', '-p', 'no:cacheprovider',
                                    '-o', 'addopts=', '--rootdir', str(folder)), folder, 120, 'one')
    assert done['exit_code'] == 1 and done['failure_totals'] == [2]
    assert 'pytest:test_fx.py::test_old' in done['signatures']


@needs_node
@pytest.mark.parametrize('reporter', ['spec', 'tap'])
def test_same_test_name_failing_in_a_second_file_blocks(tmp_path, reporter):
    # Fenn P1 (files_old_* / files_new_*): `works` fails in a.test.mjs; b.test.mjs also
    # has a `works` that starts failing. Before: one identity, count line ignored, pass.
    old_run = real_two_files(tmp_path, 'old', FILE_FAIL, FILE_OK, reporter)
    new_run = real_two_files(tmp_path, 'new', FILE_FAIL, FILE_FAIL, reporter)
    result = full_verdict(old_run, new_run)
    assert result['status'] == 'blocked'
    assert result['count_increases'] == ['1:runner failure count #1 1 -> 2']
    assert result['new_failures']  # the identities see it too (spec: the file, tap: the multiset)


@needs_node
def test_spec_failure_moving_to_another_file_blocks_even_at_equal_count(tmp_path):
    # a fixed, b broken: one failure either way, so only the file in the identity can tell.
    old_run = real_two_files(tmp_path, 'old', FILE_FAIL, FILE_OK, 'spec')
    new_run = real_two_files(tmp_path, 'new', FILE_OK, FILE_FAIL, 'spec')
    result = full_verdict(old_run, new_run)
    assert result['count_increases'] == []
    assert result['status'] == 'blocked'
    assert result['new_failures'] == ['1:spec:works @b.test.mjs']
    assert 'spec:works @a.test.mjs' in counted(old_run)['signatures']


@needs_node
def test_spec_stability_with_file_qualified_names(tmp_path):
    old_run = real_two_files(tmp_path, 'old', FILE_FAIL, FILE_OK, 'spec')
    added = real_two_files(tmp_path, 'added', FILE_FAIL, FILE_OK + NODE_PASS, 'spec')
    moved = real_two_files(tmp_path, 'moved', '// moved\n\n\n' + FILE_FAIL, FILE_OK, 'spec')
    assert 'a.test.mjs:3:' in old_run[0] and 'a.test.mjs:6:' in moved[0]
    for label, new_run in (('added', added), ('moved', moved)):
        result = full_verdict(old_run, new_run, tmp_path/'old', tmp_path/label)
        assert result['status'] == 'pass', result['new_failures']


def test_passing_count_changes_never_block_but_failure_count_increases_do(tmp_path):
    old_run = real_pytest(tmp_path, 'old', OLD_FAIL)
    more_passing = real_pytest(tmp_path, 'passing', OLD_FAIL + PASS_TEST + PASS_PRINTS)
    assert full_verdict(old_run, more_passing)['status'] == 'pass'
    assert counted(old_run)['failure_totals'] == counted(more_passing)['failure_totals'] == [1]
    two = real_pytest(tmp_path, 'two', OLD_FAIL + NEW_FAIL)
    assert full_verdict(two, old_run)['status'] == 'pass'       # fewer failures than before
    result = full_verdict(old_run, two)
    assert result['status'] == 'blocked' and result['count_increases'] == ['1:runner failure count 1 -> 2']


def test_two_files_with_one_failing_test_name_are_both_listed_without_a_short_summary(tmp_path):
    def real(label, with_second):
        folder = tmp_path/label
        folder.mkdir()
        (folder/'test_a.py').write_text(OLD_FAIL, encoding='utf-8')
        if with_second:
            (folder/'test_b.py').write_text(OLD_FAIL, encoding='utf-8')
        return captured([sys.executable, '-m', 'pytest', '-q', '-rN', '-p', 'no:cacheprovider',
                         '-o', 'addopts=', '--rootdir', str(folder)], folder)
    old_run, new_run = real('old', False), real('new', True)
    assert 'FAILED' not in new_run[0]
    assert sigs(old_run).count('pytest-report:test_old') == 1 and sigs(new_run).count('pytest-report:test_old') == 2
    result = verdict(sigs(old_run), sigs(new_run))  # identities only: the multiset is what blocks
    assert result['status'] == 'blocked' and '1:pytest-report:test_old' in result['new_failures']


def test_compare_counts_a_repeated_signature_and_ignores_missing_totals():
    twice = gate.compare([run(['pytest:a'])], [run(['pytest:a', 'pytest:a'])])
    assert twice['status'] == 'blocked' and twice['new_failures'] == ['1:pytest:a']
    assert gate.compare([run(['pytest:a', 'pytest:a'])], [run(['pytest:a'])])['status'] == 'pass'
    assert gate.compare([run(['pytest:a', 'pytest:a'])], [run(['pytest:a', 'pytest:a'])])['baseline_failures'] == [
        '1:pytest:a', '1:pytest:a']

    def with_totals(totals, signatures=('pytest:a',) * 3):  # named >= every total (round 7)
        return {**run(signatures), 'failure_totals': totals, 'exit_code': 1}
    only_count = gate.compare([with_totals([1])], [with_totals([2])])  # same identities, one more failure
    assert only_count['status'] == 'blocked' and only_count['new_failures'] == []
    assert only_count['count_increases'] == ['1:runner failure count 1 -> 2']
    assert gate.compare([with_totals([2])], [with_totals([2])])['status'] == 'pass'
    assert gate.compare([with_totals([3])], [with_totals([1])])['status'] == 'pass'
    # No totals on either side (a smoke script): identities only.
    assert gate.compare([with_totals([])], [with_totals([])])['status'] == 'pass'
    assert gate.compare([with_totals([1]), with_totals([0], ())], [with_totals([1]), with_totals([2], ())])[
        'count_increases'] == ['2:runner failure count 0 -> 2']          # scoped by command position


def test_compare_blocks_when_the_candidate_totals_cannot_be_proved_no_worse():
    # Round 6, "unprovable means block": totals are an ordered list, never summed.
    def with_totals(totals, exit_code=1):
        return {**run(['pytest:a'] * 4), 'failure_totals': totals, 'exit_code': exit_code}  # named >= totals
    # A different number of total lines blocks even though the SUMS tie at 2.
    fewer = gate.compare([with_totals([1, 1])], [with_totals([2])])
    assert fewer['status'] == 'blocked' and fewer['count_increases'] == []
    assert fewer['unprovable'] == ['1:runner total lines differ (baseline 2, candidate 1)']
    more = gate.compare([with_totals([2])], [with_totals([1, 1])])
    assert more['unprovable'] == ['1:runner total lines differ (baseline 1, candidate 2)']
    # No total at all on one side is a length difference too.
    assert gate.compare([with_totals([])], [with_totals([1])])['status'] == 'blocked'
    assert gate.compare([with_totals([1])], [with_totals([])])['status'] == 'blocked'
    # Same length: element by element, any increase blocks, a drop elsewhere does not offset it.
    swap = gate.compare([with_totals([1, 3])], [with_totals([2, 0])])
    assert swap['status'] == 'blocked' and swap['count_increases'] == ['1:runner failure count #1 1 -> 2']
    assert gate.compare([with_totals([2, 0])], [with_totals([1, 0])])['status'] == 'pass'
    # AMBIGUOUS on either side blocks while the candidate fails...
    for was, now in (([gate.AMBIGUOUS], [1]), ([1], [gate.AMBIGUOUS]), ([gate.AMBIGUOUS], [gate.AMBIGUOUS])):
        result = gate.compare([with_totals(was)], [with_totals(now)])
        assert result['status'] == 'blocked' and len(result['unprovable']) == 1, (was, now)
        assert 'ambiguous' in result['unprovable'][0]
    # ...and a candidate that exits 0 has nothing to prove, whatever the baseline printed.
    green = gate.compare([with_totals([gate.AMBIGUOUS])], [with_totals([], exit_code=0)])
    assert green['status'] == 'pass' and green['unprovable'] == []


# ── Round five: TAP file identity, and only the runner's own summary is a count ─
# Same rule: every output is captured from the real runner (pytest, node --test).

PRINTS_FAILED = 'def test_chatty():\n    print("1 failed")\n'
PRINTS_FAILED_IN = 'def test_chatty():\n    print("1 failed in 0.01s")\n'
NODE_PRINTS_FAILED = "test('chatty', () => { console.log('1 failed'); });\n"


@needs_node
def test_tap_failure_moving_to_another_file_blocks_even_at_equal_count(tmp_path):
    # Fenn r5 P1: a.test.mjs is repaired and b.test.mjs breaks; both name the test `works`.
    # One failure either way, so only the `location:` node prints in the TAP block can tell.
    old_run = real_two_files(tmp_path, 'old', FILE_FAIL, FILE_OK, 'tap')
    new_run = real_two_files(tmp_path, 'new', FILE_OK, FILE_FAIL, 'tap')
    assert gate.failure_totals(*old_run) == gate.failure_totals(*new_run) == [1, 0]
    result = full_verdict(old_run, new_run, tmp_path/'old', tmp_path/'new')
    assert result['count_increases'] == []
    assert result['status'] == 'blocked'
    assert len(result['new_failures']) == 1 and result['new_failures'][0].startswith('1:tap:works @')
    assert result['new_failures'][0].endswith('b.test.mjs')
    assert any(s.startswith('tap:works @') and s.endswith('a.test.mjs') for s in counted(old_run)['signatures'])


@needs_node
def test_tap_stability_with_file_qualified_names(tmp_path):
    # The file qualifier must not turn a harmless edit into a new failure.
    old_run = real_two_files(tmp_path, 'old', FILE_FAIL, FILE_OK, 'tap')
    added = real_two_files(tmp_path, 'added', FILE_FAIL, FILE_OK + NODE_PASS, 'tap')
    moved = real_two_files(tmp_path, 'moved', '// moved\n\n\n' + FILE_FAIL, FILE_OK, 'tap')
    renumbered = real_two_files(tmp_path, 'renum', NODE_PASS + FILE_FAIL, FILE_OK, 'tap')
    assert 'a.test.mjs:3:' in old_run[0].replace('\\', '/') and 'a.test.mjs:6:' in moved[0].replace('\\', '/')
    for label, new_run in (('added', added), ('moved', moved), ('renum', renumbered)):
        result = full_verdict(old_run, new_run, tmp_path/'old', tmp_path/label)
        assert result['status'] == 'pass', result['new_failures']


@pytest.mark.parametrize('flag', ['-rP', '-rA'])  # the flags that show a PASSING test's output
def test_a_passing_test_printing_a_bare_failure_count_is_not_a_failure(tmp_path, flag):
    # Fenn r5 P2: a bare `1 failed` (no ` in <time>`) is never pytest's line.
    old_run = real_pytest_flags(tmp_path, 'old', OLD_FAIL, [flag])
    new_run = real_pytest_flags(tmp_path, 'new', OLD_FAIL + PRINTS_FAILED, [flag])
    assert '\n1 failed\n' in new_run[0].replace('\r', '')  # really in the output
    assert gate.failure_totals(*old_run) == gate.failure_totals(*new_run) == [1]
    assert full_verdict(old_run, new_run)['status'] == 'pass'


@pytest.mark.parametrize('flag', ['-rP', '-rA'])
def test_a_passing_test_printing_a_full_pytest_total_line_is_an_accepted_false_block(tmp_path, flag):
    # A test's print() spelled exactly like pytest's closing line cannot be told from it.
    # Round 5 guessed (and a wrapper footer then made the guess wrong, Fenn r6 P1); round 6
    # blocks instead: the held total is AMBIGUOUS. This is a harmless change that blocks.
    old_run = real_pytest_flags(tmp_path, 'old', OLD_FAIL, [flag])
    new_run = real_pytest_flags(tmp_path, 'new', OLD_FAIL + PRINTS_FAILED_IN, [flag])
    assert '\n1 failed in 0.01s\n' in new_run[0].replace('\r', '')  # really in the output
    assert gate.failure_totals(*old_run) == [1]
    assert gate.failure_totals(*new_run) == [gate.AMBIGUOUS, 1]
    result = full_verdict(old_run, new_run)
    assert result['status'] == 'blocked' and result['new_failures'] == []
    # Round 8 adds the one-total-line reason beside the round-6 ambiguity reason; still blocked.
    assert result['unprovable'] == ['1:runner total ambiguous on candidate (baseline 1 total lines, candidate 2)',
                                    '1:candidate printed 2 runner total lines, only exactly 1 is provable']


@needs_node
def test_a_passing_node_test_printing_a_failure_count_is_not_a_failure(tmp_path):
    old_run = real_spec(tmp_path, 'old', NODE_HEAD + NODE_OLD)
    new_run = real_spec(tmp_path, 'new', NODE_HEAD + NODE_OLD + NODE_PRINTS_FAILED)
    assert '1 failed' in new_run[0] and 'ℹ fail 1' in new_run[0]
    assert gate.failure_totals(*old_run) == gate.failure_totals(*new_run) == [1, 0]  # fail, cancelled
    assert full_verdict(old_run, new_run)['status'] == 'pass'


def test_the_real_summary_after_a_failures_captured_output_still_counts(tmp_path):
    # `-rN` prints no short summary, so pytest's bare closing line directly follows the last
    # captured block. Skipping captured text must not lose it, or the backstop goes quiet.
    one = real_pytest_flags(tmp_path, 'one', OLD_FAIL_PRINTS, ['-rN'])
    two = real_pytest_flags(tmp_path, 'two', OLD_FAIL_PRINTS + NEW_FAIL, ['-rN'])
    assert gate.failure_totals(*one) == [1] and gate.failure_totals(*two) == [2]
    assert full_verdict(one, two)['count_increases'] == ['1:runner failure count 1 -> 2']


def test_a_failing_test_printing_a_pytest_total_line_is_an_accepted_false_block(tmp_path):
    # The same layout, but the failing test's own print is spelled like pytest's closing line
    # and sits right above the real one. The two cannot be told apart, so the first is AMBIGUOUS
    # (round 5 summed them away; round 6 blocks). A harmless change, blocked by design.
    chatty_fail = 'def test_old():\n    print("1 failed in 0.01s")\n    assert 1 == 2\n'
    one = real_pytest_flags(tmp_path, 'one', chatty_fail, ['-rN'])
    same = real_pytest_flags(tmp_path, 'same', chatty_fail + PASS_TEST, ['-rN'])
    assert gate.failure_totals(*one) == gate.failure_totals(*same) == [gate.AMBIGUOUS, 1]
    result = full_verdict(one, same)
    assert result['status'] == 'blocked' and result['new_failures'] == []
    assert 'ambiguous on baseline and candidate' in result['unprovable'][0]


# ── Round six: "unprovable means block" (Fenn, 2026-11-07) ────────────────────
# Same rule: every output is captured from the real runner (pytest, node --test).
# Each test is one of Fenn's three P1 repros, rebuilt from his fixtures.

WRAPPER = '''import subprocess, sys
p = subprocess.run([sys.executable, '-m', 'pytest', 'test_fx.py', '-q', '-rN', '-p', 'no:cacheprovider',
                    '-o', 'addopts=', '--rootdir=.'])
print('verification finished', flush=True)
sys.exit(p.returncode)
'''
NESTED_OUTER = '''import subprocess, sys
def test_old():
    subprocess.run([sys.executable, '-m', 'pytest', 'inner.py', '-q', '-rP', '-p', 'no:cacheprovider',
                    '-o', 'addopts=', '--rootdir=.'])
    assert 1 == 2
'''
NESTED_NEW = 'def test_new():\n    print("hello")\n    assert 1 == 2\n'


def real_wrapped_nested(tmp_path: Path, label: str, extra: str):
    folder = tmp_path/label
    folder.mkdir()
    for name, body in (('run.py', WRAPPER), ('inner.py', 'def test_inner():\n    print("hello")\n'),
                       ('test_fx.py', NESTED_OUTER + extra)):
        (folder/name).write_text(body, encoding='utf-8')
    return captured([sys.executable, 'run.py'], folder)


def test_nested_pytest_total_followed_by_a_wrapper_footer_blocks_a_new_failure(tmp_path):
    # Fenn r6 P1 (inner_old_True / inner_new_True): a wrapper prints a footer after pytest's
    # closing line, so the held total could not be told from a test's print. Round 5 dropped
    # it silently and 1 -> 2 failures passed. Now the dropped total is AMBIGUOUS and blocks.
    old_run = real_wrapped_nested(tmp_path, 'old', '')
    new_run = real_wrapped_nested(tmp_path, 'new', NESTED_NEW)
    assert '1 failed in' in old_run[0] and '2 failed in' in new_run[0]
    assert old_run[0].rstrip().endswith('verification finished')
    assert gate.AMBIGUOUS in gate.failure_totals(*old_run) and gate.AMBIGUOUS in gate.failure_totals(*new_run)
    result = full_verdict(old_run, new_run)
    assert result['status'] == 'blocked'
    # Round 8 adds the 2-total-lines / pytest-twice reasons beside the ambiguity reason.
    assert 'ambiguous on baseline and candidate' in result['unprovable'][0]
    assert any('ran pytest 2 times in one command' in reason for reason in result['unprovable'])


def real_same_basename(tmp_path: Path, label: str, in_a: str, in_b: str, reporter: str):
    folder = tmp_path/label
    for sub, body in (('a', in_a), ('b', in_b)):
        (folder/sub).mkdir(parents=True)
        (folder/sub/'same.test.mjs').write_text(NODE_HEAD + body, encoding='utf-8')
    return captured(['node', '--test', f'--test-reporter={reporter}', 'a/same.test.mjs', 'b/same.test.mjs'], folder)


@needs_node
@pytest.mark.parametrize('reporter', ['tap', 'spec'])
def test_same_basename_in_two_directories_is_two_files(tmp_path, reporter):
    # Fenn r6 P1 (basename_old_tap / basename_new_tap): `works` fails in a/same.test.mjs and is
    # repaired there while b/same.test.mjs breaks. One failure either way; the qualifier was the
    # basename, so both runs had the identity `tap:works @same.test.mjs` and the gate said pass.
    old_run = real_same_basename(tmp_path, 'old', FILE_FAIL, FILE_OK, reporter)
    new_run = real_same_basename(tmp_path, 'new', FILE_OK, FILE_FAIL, reporter)
    old_sigs = sigs(old_run)
    assert f'{reporter}:works @a/same.test.mjs' in old_sigs
    assert not any(s.endswith('@same.test.mjs') for s in old_sigs)
    result = full_verdict(old_run, new_run)
    assert result['count_increases'] == [] and result['unprovable'] == []   # the totals tie: identity decides
    assert result['status'] == 'blocked'
    assert result['new_failures'] == [f'1:{reporter}:works @b/same.test.mjs']


@needs_node
@pytest.mark.parametrize('reporter', ['tap', 'spec'])
def test_same_basename_qualifier_is_stable_when_nothing_moved(tmp_path, reporter):
    # The path is relative to the run cwd, so the same failure in two checkouts is one identity.
    old_run = real_same_basename(tmp_path, 'old', FILE_FAIL, FILE_OK, reporter)
    added = real_same_basename(tmp_path, 'added', '// moved\n\n\n' + FILE_FAIL, FILE_OK + NODE_PASS, reporter)
    mine = lambda run_: [s for s in sigs(run_) if s.startswith(reporter + ':')]  # noqa: E731
    assert mine(old_run) == mine(added) == [f'{reporter}:works @a/same.test.mjs']
    assert full_verdict(old_run, added)['status'] == 'pass'


def real_s_flag(tmp_path: Path, label: str, source: str):
    folder = tmp_path/label
    folder.mkdir()
    (folder/'test_fx.py').write_text(source, encoding='utf-8')
    return captured([sys.executable, '-m', 'pytest', 'test_fx.py', '-p', 'no:cacheprovider', '-o', 'addopts=',
                     '--rootdir', str(folder), '-q', '-s', '-rN', '--tb=line'], folder)


def test_a_printed_total_cannot_offset_a_new_failure_under_dash_s(tmp_path):
    # Fenn r6 P1 (s_line_replacement_old / _new): under -s a passing test's print of
    # `1 failed in 0.01s` is a plain count line. The old SUM (printed 1 + real 1 = 2) tied the
    # candidate's real 2, and --tb=line leaves no per-test heading. The totals are now a list:
    # [1, 1] against [2] is a different number of total lines, so the gate blocks.
    chatty = '\ndef test_chatty():\n    print("\\n1 failed in 0.01s")\n'
    old_run = real_s_flag(tmp_path, 'old', 'def test_old():\n    assert 1 == 2\n' + chatty + '\ndef test_new():\n    pass\n')
    new_run = real_s_flag(tmp_path, 'new', 'def test_old():\n    assert 1 == 2\n'
                          '\ndef test_chatty():\n    pass\n\ndef test_new():\n    assert 1 == 2\n')
    assert '1 failed, 2 passed' in old_run[0] and '2 failed, 1 passed' in new_run[0]
    assert gate.failure_totals(*old_run) == [1, 1] and gate.failure_totals(*new_run) == [2]
    result = full_verdict(old_run, new_run)
    assert result['status'] == 'blocked' and result['new_failures'] == []   # identities cannot see it
    # The identity rule (round 7) also fires: --tb=line names no test, so neither run is provable.
    # Round 8: the baseline also prints two total lines, which is unprovable by itself.
    assert result['unprovable'][0] == '1:runner total lines differ (baseline 2, candidate 1)'
    assert '1:baseline printed 2 runner total lines, only exactly 1 is provable' in result['unprovable']
    assert '1:candidate names 0 of 2 failures the runner counted' in result['unprovable']


# ── Round seven: a failure is "pre-existing" only if it is NAMED (Fenn, 2026-11-07) ──
# Dave's decision (b): when the candidate fails, each run must name at least as many failures
# as its runner counted, and a relative file qualifier must resolve from the verification cwd.
# Same rule: every output is captured from the real runner (pytest, node --test).

SWAP_BEFORE = 'def test_old():\n    assert False\n\ndef test_new():\n    pass\n'
SWAP_AFTER = 'def test_old():\n    pass\n\ndef test_new():\n    assert False\n'


def pytest_swap(tmp_path: Path, flags):
    old_run = real_pytest_flags(tmp_path, 'old', SWAP_BEFORE, flags)
    new_run = real_pytest_flags(tmp_path, 'new', SWAP_AFTER, flags)
    assert gate.failure_totals(*old_run) == gate.failure_totals(*new_run) == [1]
    return old_run, new_run


@pytest.mark.parametrize('flags', [['-rN', '--tb=no'], ['-rN', '--tb=line']])
def test_a_swapped_pytest_failure_with_no_per_test_name_blocks(tmp_path, flags):
    # Fenn r7 P1 (pytest_replaced_none / _line): `-rN --tb=no|line` prints no test name, so
    # old-test-repaired / new-test-broken is `1 failed` -> `1 failed` and passed at [1] -> [1].
    old_run, new_run = pytest_swap(tmp_path, flags)
    result = full_verdict(old_run, new_run)
    assert result['status'] == 'blocked' and result['new_failures'] == [] and result['count_increases'] == []
    assert result['unprovable'] == ['1:baseline names 0 of 1 failures the runner counted',
                                    '1:candidate names 0 of 1 failures the runner counted']


@pytest.mark.parametrize('flags', [['-rN', '--tb=no'], ['-rN', '--tb=line']])
def test_an_unchanged_red_pytest_with_no_per_test_name_blocks_by_design(tmp_path, flags):
    # The accepted cost of the rule: an unchanged red suite that hides its test names cannot be
    # proved unchanged, so a person merges it by hand (or fixes the red test).
    old_run, _ = pytest_swap(tmp_path, flags)
    same = real_pytest_flags(tmp_path, 'same', SWAP_BEFORE, flags)
    result = full_verdict(old_run, same)
    assert result['status'] == 'blocked' and len(result['unprovable']) == 2


@pytest.mark.parametrize('flags', [[], ['-rN'], ['-ra']])
def test_a_swapped_pytest_failure_with_names_blocks_on_identity_and_unchanged_passes(tmp_path, flags):
    # Controls: with full tracebacks (and/or -ra) the failure is named, so the old behaviour holds.
    old_run, new_run = pytest_swap(tmp_path, flags)
    swapped = full_verdict(old_run, new_run)
    assert swapped['status'] == 'blocked' and swapped['unprovable'] == []
    assert any('test_new' in n for n in swapped['new_failures'])
    same = real_pytest_flags(tmp_path, 'same', SWAP_BEFORE, flags)
    unchanged = full_verdict(old_run, same)
    assert unchanged['status'] == 'pass' and unchanged['unprovable'] == []


def test_named_identities_counts_a_pytest_failure_once_across_summary_and_report():
    # `-ra` with tracebacks lists a failure twice (short summary + report heading).
    both = ['pytest:test_fx.py::test_a', 'pytest-report:test_a', 'pytest:test_fx.py::test_b', 'pytest-report:test_b']
    assert gate.named_identities(both) == 2
    assert gate.named_identities(both[1::2]) == 2 and gate.named_identities(both[::2]) == 2
    # A generic banner, an exit signature and a smoke line name no test.
    assert gate.named_identities(['exit:1:================== FAILURES ===================', 'smoke:x']) == 0
    assert gate.named_identities(['tap:a @x.mjs', 'spec:b @y.mjs']) == 2
    # An unproven file qualifier is not a name.
    assert gate.named_identities(['spec:works @?same.test.mjs', 'spec:other @a/same.test.mjs']) == 1


SUBPROJECT_RUNNER = """import subprocess, sys
from pathlib import Path
codes = []
for where in Path('selected.txt').read_text().split():
    p = subprocess.run(['node', '--test', '--test-reporter=%s', 'same.test.mjs'], cwd=where)
    codes.append(p.returncode)
sys.exit(max(codes))
"""


def real_subprojects(tmp_path: Path, label: str, selected: str, in_a: str, in_b: str, reporter: str):
    """A wrapper that runs node in child directories: the spec reporter prints paths relative
    to the CHILD cwd, which the gate (knowing only the wrapper's cwd) cannot interpret."""
    folder = tmp_path/label
    for sub, body in (('a', in_a), ('b', in_b)):
        (folder/sub).mkdir(parents=True)
        (folder/sub/'same.test.mjs').write_text(NODE_HEAD + body, encoding='utf-8')
    (folder/'run.py').write_text(SUBPROJECT_RUNNER % reporter, encoding='utf-8')
    (folder/'selected.txt').write_text(selected, encoding='utf-8')
    return captured([sys.executable, 'run.py'], folder)


@needs_node
def test_a_child_cwd_spec_wrapper_cannot_swap_failures_between_subprojects(tmp_path):
    # Fenn r7 P1 (child_cwd_old_spec / _new_spec): selected.txt reordered `a b` -> `b a`, a is
    # repaired, b now fails the same way. Both runs parsed to `spec:works @same.test.mjs` with
    # totals [1,0,0,0], so it passed. `same.test.mjs` is not a file in the wrapper's cwd.
    old_run = real_subprojects(tmp_path, 'old', 'a b', FILE_FAIL, FILE_OK, 'spec')
    new_run = real_subprojects(tmp_path, 'new', 'b a', FILE_OK, FILE_FAIL, 'spec')
    assert sigs(old_run).count('spec:works @?same.test.mjs') == 1 and sigs(new_run).count('spec:works @?same.test.mjs') == 1
    result = full_verdict(old_run, new_run)
    assert result['status'] == 'blocked' and result['new_failures'] == []
    assert result['unprovable'][0].startswith('1:test file path does not resolve from the verification cwd on baseline')
    assert any('on candidate' in reason for reason in result['unprovable'])
    # Unchanged is the accepted harmless block: the child-relative path is still unproven.
    unchanged = full_verdict(old_run, real_subprojects(tmp_path, 'same', 'a b', FILE_FAIL, FILE_OK, 'spec'))
    assert unchanged['status'] == 'blocked' and len(unchanged['unprovable']) >= 2


@needs_node
def test_a_child_cwd_tap_wrapper_still_blocks_a_swap_on_the_absolute_location(tmp_path):
    # TAP prints an absolute `location:`, relativized against the wrapper's cwd: still proven.
    old_run = real_subprojects(tmp_path, 'old', 'a b', FILE_FAIL, FILE_OK, 'tap')
    new_run = real_subprojects(tmp_path, 'new', 'b a', FILE_OK, FILE_FAIL, 'tap')
    assert 'tap:works @a/same.test.mjs' in sigs(old_run)
    result = full_verdict(old_run, new_run)
    # Round 8: this wrapper runs node twice (two total lines), so it is unprovable; the TAP
    # identity itself is still resolved and still reported as the new failure.
    assert result['status'] == 'blocked'
    assert all('printed 2 runner total lines' in reason for reason in result['unprovable']), result['unprovable']
    assert result['new_failures'] == ['1:tap:works @b/same.test.mjs']
    assert full_verdict(old_run, real_subprojects(tmp_path, 'same', 'a b', FILE_FAIL, FILE_OK, 'tap'))['status'] == 'blocked'


@needs_node
def test_a_one_child_tap_wrapper_keeps_its_absolute_location_identity_and_passes_unchanged(tmp_path):
    # Round 8 rule 5: a TAP absolute location relativized against the cwd stays valid under a
    # wrapper, when the wrapper runs exactly one node invocation (one total line).
    old_run = real_subprojects(tmp_path, 'old', 'a', FILE_FAIL, FILE_OK, 'tap')
    new_run = real_subprojects(tmp_path, 'new', 'b', FILE_OK, FILE_FAIL, 'tap')
    assert 'tap:works @a/same.test.mjs' in sigs(old_run) and not gate._direct_node(old_run.command)
    result = full_verdict(old_run, new_run)
    assert result['status'] == 'blocked' and result['unprovable'] == []
    assert result['new_failures'] == ['1:tap:works @b/same.test.mjs']
    assert full_verdict(old_run, real_subprojects(tmp_path, 'same', 'a', FILE_FAIL, FILE_OK, 'tap'))['status'] == 'pass'


@needs_node
@pytest.mark.parametrize('reporter', ['tap', 'spec'])
def test_unchanged_red_node_suite_run_from_the_verification_cwd_still_passes(tmp_path, reporter):
    old_run = real_same_basename(tmp_path, 'old', FILE_FAIL, FILE_OK, reporter)
    same = real_same_basename(tmp_path, 'same', FILE_FAIL, FILE_OK, reporter)
    assert not any(gate._UNRESOLVED in s for s in sigs(old_run))
    result = full_verdict(old_run, same)
    assert result['status'] == 'pass' and result['unprovable'] == []


@needs_node
@pytest.mark.parametrize('reporter', ['tap', 'spec'])
def test_node_timeout_cancellation_is_unchanged_by_the_identity_rule(tmp_path, reporter):
    # A test that times out is counted as `cancelled` ([0, 1]). Unchanged it passes; a new
    # ordinary failure beside it still blocks.
    cancel = "test('pending', {timeout: 20}, () => new Promise(resolve => setTimeout(resolve, 100)));\n"

    def real(label, body):
        folder = tmp_path/label
        folder.mkdir()
        (folder/'fx.test.mjs').write_text(NODE_HEAD + body, encoding='utf-8')
        return captured(['node', '--test', f'--test-reporter={reporter}', 'fx.test.mjs'], folder)
    old_run, same = real('old', cancel), real('same', cancel)
    assert gate.failure_totals(*old_run) == [0, 1]
    unchanged = full_verdict(old_run, same)
    assert unchanged['status'] == 'pass' and unchanged['unprovable'] == [], unchanged['unprovable']
    added = full_verdict(old_run, real('added', cancel + "test('boom', () => { assert.equal(1, 2); });\n"))
    assert added['status'] == 'blocked'


def test_identity_file_resolves_relative_paths_against_the_cwd_only(tmp_path):
    (tmp_path/'a').mkdir()
    (tmp_path/'a'/'t.test.mjs').write_text('', encoding='utf-8')
    assert gate._identity_file('a/t.test.mjs:3:1', tmp_path) == 'a/t.test.mjs'
    assert gate._identity_file('t.test.mjs:3:1', tmp_path) == '?t.test.mjs'            # child-relative
    assert gate._identity_file('a/missing.test.mjs', tmp_path) == '?a/missing.test.mjs'
    assert gate._identity_file(str(tmp_path/'a'/'t.test.mjs') + ':3:1', tmp_path) == 'a/t.test.mjs'
    assert gate._identity_file('t.test.mjs', None) == 't.test.mjs'                      # no cwd: nothing to resolve


# ── Round eight: a count is provable only from ONE runner's own report (Fenn, 2026-11-07) ──
# Dave's decision (b). Same rule: every output is captured from the real runner. Repros A, B
# and C are Fenn's `suite_*`, `duplicate_*` and `printed_*_uncaptured`, rebuilt from his fixtures.

PY_FLAGS = ['-p', 'no:cacheprovider', '-o', 'addopts=', '--rootdir=.']
R8_SWAP_BEFORE = 'def test_old():\n    assert False\n\ndef test_new():\n    pass\n'
R8_SWAP_AFTER = 'def test_old():\n    pass\n\ndef test_new():\n    assert False\n'
R8_SUITE = ("import {describe, it} from 'node:test';\nimport assert from 'node:assert/strict';\n"
            "describe('suite', () => { it('known', () => {assert.equal(1,2);}); });\n")
R8_NODE_THEN_PYTEST = f'''import subprocess, sys
a = subprocess.run(['node', '--test', '--test-reporter=spec', 'known.test.mjs'])
b = subprocess.run([sys.executable, '-m', 'pytest', 'test_fx.py', '-q', '-rN', '--tb=no'] + {PY_FLAGS!r})
sys.exit(max(a.returncode, b.returncode))
'''
R8_PYTEST_TWICE = f'''import subprocess, sys
common = [sys.executable, '-m', 'pytest', '-q'] + {PY_FLAGS!r}
a = subprocess.run(common + ['test_visible.py', '-ra'])
b = subprocess.run(common + ['test_hidden.py', '-rN', '--tb=no'])
sys.exit(max(a.returncode, b.returncode))
'''
R8_VISIBLE = 'def test_known():\n    print("FAILED test_visible.py::test_known")\n    assert False\n'
R8_CHATTY = '\ndef test_chatty():\n    print("\\nFAILED test_fx.py::test_chatty")\n'


def real_wrapper(tmp_path: Path, label: str, files: dict):
    folder = tmp_path/label
    folder.mkdir()
    for name, body in files.items():
        (folder/name).write_text(body, encoding='utf-8')
    return captured([sys.executable, 'run.py'], folder)


def assert_unprovable(result, *needles):
    assert result['status'] == 'blocked' and result['new_failures'] == [], result
    for needle in needles:
        assert any(needle in reason for reason in result['unprovable']), (needle, result['unprovable'])


@needs_node
def test_repro_a_node_suite_plus_unnamed_pytest_swap_blocks(tmp_path):
    # Fenn r8 P1 A: the failing describe() supplied a spare identity, so the `--tb=no` pytest
    # swap was "named" by node's container line. Two runner families in one command: unprovable.
    def real(label, test):
        return real_wrapper(tmp_path, label, {'run.py': R8_NODE_THEN_PYTEST, 'known.test.mjs': R8_SUITE,
                                              'test_fx.py': test})
    old_run, new_run = real('old', R8_SWAP_BEFORE), real('new', R8_SWAP_AFTER)
    assert_unprovable(full_verdict(old_run, new_run), 'more than one runner family (pytest, node)',
                      'runner total lines')
    assert full_verdict(old_run, real('same', R8_SWAP_BEFORE))['status'] == 'blocked'  # accepted false block


def test_repro_b_a_captured_duplicate_name_plus_second_pytest_blocks(tmp_path):
    # Fenn r8 P1 B: test_known prints its own `FAILED ...` (captured), so pytest's report names it
    # twice; the duplicate filled the count of a second, unnamed `-rN --tb=no` pytest run.
    def real(label, hidden):
        return real_wrapper(tmp_path, label, {'run.py': R8_PYTEST_TWICE, 'test_visible.py': R8_VISIBLE,
                                              'test_hidden.py': hidden})
    result = full_verdict(real('old', R8_SWAP_BEFORE), real('new', R8_SWAP_AFTER))
    assert_unprovable(result, '2 runner total lines', 'ran pytest 2 times in one command')


def test_repro_b2_without_the_duplicate_two_pytest_runs_still_block(tmp_path):
    def real(label, hidden):
        return real_wrapper(tmp_path, label, {'run.py': R8_PYTEST_TWICE,
                                              'test_visible.py': 'def test_known():\n    assert False\n',
                                              'test_hidden.py': hidden})
    assert_unprovable(full_verdict(real('old', R8_SWAP_BEFORE), real('new', R8_SWAP_AFTER)), '2 runner total lines')


def test_repro_c_a_passing_test_printing_failed_under_dash_s_is_not_an_identity(tmp_path):
    # Fenn r8 P1 C: `-s` lets a passing test write `FAILED test_fx.py::test_chatty` straight into
    # the stream, where it looked like a short-summary line and filled the count of the real,
    # unnamed (`-rN --tb=no`) failure that swapped under it. Only the LAST short-summary section
    # before the total is a name source, and `-rN` has none.
    argv = [sys.executable, '-m', 'pytest', 'test_fx.py', '-q', '--tb=no', '-s', '-rN', *PY_FLAGS]

    def real(label, source):
        folder = tmp_path/label
        folder.mkdir()
        (folder/'test_fx.py').write_text(source, encoding='utf-8')
        return captured(argv, folder)
    old_run, new_run = real('old', R8_SWAP_BEFORE + R8_CHATTY), real('new', R8_SWAP_AFTER + R8_CHATTY)
    assert 'FAILED test_fx.py::test_chatty' in old_run[0]
    assert gate.prove(*old_run, old_run.root, old_run.command)['identities'] == []
    assert_unprovable(full_verdict(old_run, new_run), 'baseline names 0 of 1 failures',
                      'candidate names 0 of 1 failures')


@needs_node
def test_a_same_named_file_at_the_wrapper_cwd_does_not_prove_a_child_cwd_path(tmp_path):
    # Fenn r8 P1 (child_old_True / child_new_True): a decoy `same.test.mjs` at the wrapper's own
    # cwd made `exists()` true for the child-relative spec path, so the swap `a b` -> `b a` passed.
    def real(label, selected, in_a, in_b):
        folder = tmp_path/label
        for sub, body in (('a', in_a), ('b', in_b)):
            (folder/sub).mkdir(parents=True)
            (folder/sub/'same.test.mjs').write_text(NODE_HEAD + body, encoding='utf-8')
        (folder/'same.test.mjs').write_text(NODE_HEAD + FILE_OK, encoding='utf-8')  # the decoy
        (folder/'run.py').write_text(SUBPROJECT_RUNNER % 'spec', encoding='utf-8')
        (folder/'selected.txt').write_text(selected, encoding='utf-8')
        return captured([sys.executable, 'run.py'], folder)
    old_run, new_run = real('old', 'a b', FILE_FAIL, FILE_OK), real('new', 'b a', FILE_OK, FILE_FAIL)
    assert (tmp_path/'old'/'same.test.mjs').is_file()
    assert 'spec:works @same.test.mjs' not in sigs(old_run)  # the wrapper is not a direct node run
    assert_unprovable(full_verdict(old_run, new_run), 'test file path does not resolve from the verification cwd')


@needs_node
def test_a_decoy_file_also_fails_a_single_child_cwd_spec_wrapper(tmp_path):
    # Same decoy with ONE node invocation: only the wrapper rule (not the two-totals rule) can block it.
    def real(label, selected, in_a, in_b):
        folder = tmp_path/label
        for sub, body in (('a', in_a), ('b', in_b)):
            (folder/sub).mkdir(parents=True)
            (folder/sub/'same.test.mjs').write_text(NODE_HEAD + body, encoding='utf-8')
        (folder/'same.test.mjs').write_text(NODE_HEAD + FILE_OK, encoding='utf-8')
        (folder/'run.py').write_text(SUBPROJECT_RUNNER % 'spec', encoding='utf-8')
        (folder/'selected.txt').write_text(selected, encoding='utf-8')
        return captured([sys.executable, 'run.py'], folder)
    old_run, new_run = real('old', 'a', FILE_FAIL, FILE_OK), real('new', 'b', FILE_OK, FILE_FAIL)
    assert_unprovable(full_verdict(old_run, new_run), 'test file path does not resolve from the verification cwd')


@needs_node
def test_a_direct_node_spec_run_still_resolves_relative_paths_against_the_cwd(tmp_path):
    old_run = real_same_basename(tmp_path, 'old', FILE_FAIL, FILE_OK, 'spec')
    assert gate._direct_node(old_run.command)
    assert 'spec:works @a/same.test.mjs' in sigs(old_run)
    new_run = real_same_basename(tmp_path, 'new', FILE_OK, FILE_FAIL, 'spec')
    swapped = full_verdict(old_run, new_run)
    assert swapped['status'] == 'blocked' and swapped['new_failures'] == ['1:spec:works @b/same.test.mjs']


@needs_node
@pytest.mark.parametrize('reporter', ['spec', 'tap'])
def test_a_failing_describe_container_is_not_a_test_identity(tmp_path, reporter):
    folder = tmp_path/reporter
    folder.mkdir()
    (folder/'known.test.mjs').write_text(R8_SUITE, encoding='utf-8')
    run_ = captured(['node', '--test', f'--test-reporter={reporter}', 'known.test.mjs'], folder)
    proof = gate.prove(*run_, run_.root, run_.command)
    assert proof['reasons'] == [], proof
    assert proof['total'] == len(proof['identities']) == 1, proof
    assert proof['identities'][0].endswith(' @known.test.mjs') and 'known' in proof['identities'][0]
    assert len(sigs(run_)) > len(proof['identities']) or reporter == 'tap'  # the container is still a signature
    assert full_verdict(run_, run_)['status'] == 'pass'


def test_prove_requires_the_name_count_to_equal_the_total_and_deduplicates():
    short = ('=========== short test summary info ===========\n'
             'FAILED test_fx.py::test_a - assert False\nFAILED test_fx.py::test_a - assert False\n'
             '============== 2 failed in 0.01s ==============\n')
    proof = gate.prove(short, 1, None, 'pytest -q')
    assert proof['identities'] == ['pytest:test_fx.py::test_a'] and proof['total'] == 2
    assert proof['reasons'] == ['names 1 of 2 failures the runner counted']
    over = ('=========== short test summary info ===========\n'
            'FAILED test_fx.py::test_a - assert False\nFAILED test_fx.py::test_b - assert False\n'
            '============== 1 failed in 0.01s ==============\n')
    assert gate.prove(over, 1, None, 'pytest -q')['reasons'] == ['names 2 failures but the runner counted only 1']


def test_prove_takes_pytest_names_only_from_the_summary_directly_before_the_total():
    text = ('FAILED test_fx.py::test_printed\n'
            '=========== short test summary info ===========\n'
            'FAILED test_fx.py::test_real - assert False\n'
            'a stray line a wrapper printed\n'
            '============== 1 failed in 0.01s ==============\n')
    reasons = gate.prove(text, 1, None, 'pytest -q')['reasons']
    assert reasons and 'not immediately followed by the runner total line' in reasons[0]


def test_direct_node_accepts_only_a_bare_node_test_invocation():
    for yes in ('node --test', 'node --test --test-reporter=spec a.test.mjs', 'node.exe --test x',
                'node --no-warnings --test x'):
        assert gate._direct_node(yes), yes
    for no in ('npm test', 'python run.py', 'cd a && node --test', 'node --test | tee x', 'FOO=1 node --test',
               'node a.test.mjs', 'node', '', 'node --test "unterminated', 'node --test; echo done'):
        assert not gate._direct_node(no), no
